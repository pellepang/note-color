"""The Project: the document VisualNote Studio opens, edits and saves.

Format and rationale: `docs/adr/0001-ncproj-project-bundle.md` (map #145,
ticket #149). This module is the in-memory model only -- `bundle.py` handles
the `.ncproj` directory and its JSON. Nothing here imports music21, Qt, or an
audio library: a Project is plain data, and `tests/test_package_boundary.py`
keeps it that way.

Vocabulary is `CONTEXT.md`'s, and is worth restating because two of the words
changed meaning recently (ticket #150):

- **Track** -- one lane of the timeline, holding **Clips** in sequence.
- **Part** -- one instrument's *notated* music. What Track used to mean here.
  Parts are a notation-side concept; they arrive as Tracks on import.
- **Clip** -- a bounded piece of content at a musical position. Two kinds.
- **Session** -- deliberately *not* used for a document. That word is the live
  microphone-capture bundle (`audio.session.SessionState`).

**Positions are musical, everywhere.** A beat is the unit; seconds are derived
through `TempoMap`. That is what makes changing the tempo move the music
rather than rewrite it, and it is why an Audio clip is anchored by beat while
keeping its own length in samples -- the audio does not stretch just because
the grid moved.
"""

import bisect
from dataclasses import dataclass, field, replace
from typing import List, Optional

#: Bumped when the on-disk shape changes in a way `bundle.migrate()` has to
#: know about. Deliberately unrelated to `score_writer.PROJECT_MANIFEST_VERSION`
#: -- that versions the JSON manifest embedded in *MusicXML* by #123's
#: converter, which is a different artifact with a different lifetime. Merging
#: the two counters would tie a notation sidecar's history to the DAW
#: document's for no benefit.
PROJECT_VERSION = 1

NOTE_TRACK = "note"
AUDIO_TRACK = "audio"

CONSTANT = "constant"
LINEAR = "linear"  # accepted on read, not yet honoured -- see TempoMap


class ProjectError(Exception):
    """Something about a project is wrong in a way worth stopping for."""


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TempoAnchor:
    """A tempo in force from `beat` until the next anchor."""

    beat: float
    bpm: float
    interpolation: str = CONSTANT


@dataclass
class TempoMap:
    """Musical position <-> seconds.

    A list of anchors rather than a single BPM scalar: #131 already
    established that tempo is something *read out of* beat times, never a
    number imposed on them, and a scalar cannot express a piece that changes
    tempo at all.

    Only `CONSTANT` interpolation is implemented -- tempo steps at each anchor
    and holds. `LINEAR` (a ramp) is accepted and stored so that adding it later
    is additive rather than a format change, but it is currently *treated as
    constant*, and `has_unsupported_interpolation` says so rather than letting
    a file silently play back at the wrong speed.

    This is the piece most worth getting exactly right: a wrong conversion here
    is wrong in the ruler, the playhead, every clip position and every exported
    file at once.
    """

    anchors: List[TempoAnchor] = field(default_factory=lambda: [TempoAnchor(0.0, 120.0)])

    def __post_init__(self):
        self.normalise()

    def normalise(self):
        """Sort anchors, and guarantee one at beat 0 so every lookup lands."""
        anchors = sorted(self.anchors, key=lambda a: a.beat)
        anchors = [a for a in anchors if a.bpm > 0]
        if not anchors:
            anchors = [TempoAnchor(0.0, 120.0)]
        if anchors[0].beat > 0:
            # Extend the first tempo backwards rather than inventing one.
            anchors.insert(0, replace(anchors[0], beat=0.0))
        self.anchors = anchors
        self._cumulative = [0.0]
        for previous, current in zip(anchors, anchors[1:]):
            span = current.beat - previous.beat
            self._cumulative.append(self._cumulative[-1] + span * 60.0 / previous.bpm)
        self._beats = [a.beat for a in anchors]

    @property
    def has_unsupported_interpolation(self):
        return any(a.interpolation != CONSTANT for a in self.anchors)

    def bpm_at(self, beat):
        index = max(0, bisect.bisect_right(self._beats, float(beat)) - 1)
        return self.anchors[index].bpm

    def beats_to_seconds(self, beat):
        beat = float(beat)
        index = max(0, bisect.bisect_right(self._beats, beat) - 1)
        anchor = self.anchors[index]
        return self._cumulative[index] + (beat - anchor.beat) * 60.0 / anchor.bpm

    def seconds_to_beats(self, seconds):
        seconds = float(seconds)
        index = max(0, bisect.bisect_right(self._cumulative, seconds) - 1)
        anchor = self.anchors[index]
        return anchor.beat + (seconds - self._cumulative[index]) * anchor.bpm / 60.0

    def to_dict(self):
        return [{"beat": a.beat, "bpm": a.bpm, "interpolation": a.interpolation}
                for a in self.anchors]

    @classmethod
    def from_dict(cls, data):
        anchors = []
        for entry in data or []:
            try:
                anchors.append(TempoAnchor(
                    float(entry["beat"]), float(entry["bpm"]),
                    str(entry.get("interpolation", CONSTANT))))
            except (KeyError, TypeError, ValueError):
                continue  # one bad anchor is not worth losing the project over
        return cls(anchors or [TempoAnchor(0.0, 120.0)])


@dataclass(frozen=True)
class TimeSignature:
    numerator: int = 4
    denominator: int = 4

    @property
    def beats_per_bar(self):
        """In quarter-note beats, which is the unit positions are stored in --
        so 6/8 is three quarter-note beats per bar, not six."""
        return self.numerator * 4.0 / self.denominator


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


@dataclass
class Note:
    start_beat: float
    duration_beats: float
    pitch: int                      # MIDI note number
    velocity: float = 1.0           # 0..1, `sound_engine.NoteOn`'s convention
    #: What the model thought, where a model produced it (#143). Kept because
    #: it is free and it is what lets an editor highlight notes worth checking;
    #: `None` means nobody reported one, which is not the same as zero.
    confidence: Optional[float] = None

    @property
    def pitch_class(self):
        return self.pitch % 12


@dataclass
class NoteClip:
    name: str = ""
    start_beat: float = 0.0
    length_beats: float = 4.0
    notes: List[Note] = field(default_factory=list)
    kind = NOTE_TRACK


@dataclass
class AudioClip:
    """A region of a recorded or imported file.

    Anchored by beat like everything else, but its `source_length_samples` is
    in samples and does not move when the tempo does -- audio does not stretch
    because the grid changed. `source` is a **bare filename** inside the
    bundle's `audio/`, the rule `patch_format` already applies to samples so a
    project stays portable.
    """

    name: str = ""
    start_beat: float = 0.0
    length_beats: float = 4.0
    source: str = ""
    source_offset_samples: int = 0
    source_length_samples: int = 0
    gain_db: float = 0.0
    fade_in_beats: float = 0.0
    fade_out_beats: float = 0.0
    kind = AUDIO_TRACK


@dataclass
class Track:
    name: str = "Track"
    kind: str = NOTE_TRACK
    #: Pitch class this track is tinted by, or None to let the content decide.
    #: A drum track has no pitch to be honest about, exactly as the synth
    #: tool's pads have none.
    color_pitch_class: Optional[int] = None
    clips: List[object] = field(default_factory=list)
    muted: bool = False
    soloed: bool = False
    gain_db: float = 0.0
    pan: float = 0.0                # -1 left .. +1 right

    @property
    def end_beat(self):
        return max((c.start_beat + c.length_beats for c in self.clips), default=0.0)


@dataclass
class ChordSpan:
    """One chord on the chord track.

    `derived` marks a span analysis produced; a span the user corrected is not
    derived and **survives re-analysis**. That is the whole contract of the
    chord track (#149/#145): analysis proposes, a person corrects, and the
    correction is not thrown away the next time the notes change.
    """

    start_beat: float
    end_beat: float
    name: str
    derived: bool = True


@dataclass
class Project:
    name: str = "Untitled"
    tempo_map: TempoMap = field(default_factory=TempoMap)
    time_signature: TimeSignature = field(default_factory=TimeSignature)
    key_fifths: int = 0
    sample_rate: int = 48000
    tracks: List[Track] = field(default_factory=list)
    chords: List[ChordSpan] = field(default_factory=list)

    @property
    def end_beat(self):
        return max((t.end_beat for t in self.tracks), default=0.0)

    @property
    def duration_seconds(self):
        return self.tempo_map.beats_to_seconds(self.end_beat)

    def track_named(self, name):
        for track in self.tracks:
            if track.name == name:
                return track
        return None

    def unique_track_name(self, name):
        """A name no existing track holds. Duplicate track names are the one
        thing #128 measured as *not* surviving a notation round trip, so they
        are prevented at the source rather than repaired on export."""
        existing = {t.name for t in self.tracks}
        if name not in existing:
            return name
        suffix = 2
        while f"{name} {suffix}" in existing:
            suffix += 1
        return f"{name} {suffix}"
