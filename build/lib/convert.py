"""Map #123's offline audio-to-score converter -- the pipeline driver
(issue #129).

Owns the *order* of the passes and nothing about how any of them work:
every stage is reached through `transcribe_backends.py`'s Protocols, so
which model runs is a construction argument rather than something wired
into this file. That is the whole point of the seam (#129) -- the models
chosen today are defaults to be replaced by whichever ones #132's harness
measures as better.

    audio -> Separator      -> stems          (optional; see H1 below)
          -> BeatTracker    -> beat/downbeat grid, drum stem as oracle
          -> NoteTranscriber-> notes, per stem
          -> ChordEstimator -> chord spans     (the Real Book half, #141)

**This module deliberately asserts nothing about whether the multi-pass
shape helps.** #129 recorded three hypotheses rather than architecture:
H1 (separation improves note accuracy -- #126 found *no published study*
measures it, and #142's one real datapoint is +0.20 points), H2 (a locked
beat grid beats independent snapping) and H3 (ensembling earns its cost).
The stages are ordered so each can be switched off and measured, which is
what makes them testable rather than assumed. Separation survives H1's
rejection regardless: #127's drum-stem downbeat gain is measured, and a
multi-track deliverable structurally needs per-instrument audio.

Refuses rather than degrades (#129): a missing stage raises
`ConversionUnavailable` carrying its install line, never a silent
substitution of the live DSP pipeline.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from transcribe_backends import (
    BeatGrid,
    ConversionUnavailable,
    TemplateChordEstimator,
)

#: Install lines quoted verbatim in refusals. Kept here rather than inside
#: each backend so the message a user sees is one this module controls,
#: and so `pyproject.toml`'s extra names have exactly one source in code.
CONVERT_EXTRA = "pip install -e .[convert]"
PIANO_EXTRA = "pip install -e .[piano]"

#: Stem names, in Demucs' own vocabulary (#129 picked it, and every
#: routing rule in the map is written in these terms). "other" is the
#: polyphonic residue -- everything that is not drums, bass or vocals.
STEM_DRUMS = "drums"
STEM_BASS = "bass"
STEM_VOCALS = "vocals"
STEM_OTHER = "other"

#: Which stem is the melody, absent better information. #141 has to
#: settle this properly (vocals in a pop mix, but not always, and the
#: user must be able to override); until then this is the default a lead
#: sheet is built from, and naming it here beats hiding the assumption.
DEFAULT_MELODY_STEM = STEM_VOCALS


@dataclass
class Track:
    """One track of the multi-track project (#131). `name` becomes the
    MusicXML `<part-name>`, which #128 measured is the only stable track
    identity across a music21 round trip."""

    name: str
    notes: list = field(default_factory=list)
    source_stem: Optional[str] = None
    model: Optional[str] = None
    #: #129: a stem no model covers well is still transcribed and its
    #: track marked, never dropped -- the output is editable, so an
    #: approximate track a human corrects beats a missing one.
    low_confidence: bool = False


@dataclass
class ConversionResult:
    """What a conversion produced. Deliberately *not* a music21 object or
    a file path: writing is a separate concern (#131's format), and
    #132's harness scores this shape directly without a round trip
    through disk."""

    tracks: list = field(default_factory=list)
    chords: list = field(default_factory=list)
    beats: BeatGrid = field(default_factory=BeatGrid)
    #: Meter numerator, inferred; None when the confidence gate did not
    #: clear. The denominator is never inferred anywhere (#130) -- it is a
    #: convention applied at write time.
    beats_per_bar: Optional[int] = None
    duration_seconds: float = 0.0

    @property
    def note_count(self) -> int:
        return sum(len(track.notes) for track in self.tracks)


#: #130's meter candidate set. Small on purpose: this is how the field
#: itself works (madmom defaults to [3,4], PM2S's vocabulary is
#: {0,2,3,4,6}), not a shortcut.
METER_CANDIDATES = (2, 3, 4, 6)

#: #130's confidence gate. Below this share of observed bars agreeing,
#: the guess is not committed and 4/4 is used instead -- meter accuracy
#: is bounded above by downbeat F1 (~0.78) and #127 is blunt that
#: anything other than 4/4-vs-3/4 on Western pop-rock should be assumed
#: wrong.
METER_MIN_AGREEMENT = 0.6
METER_MIN_BARS = 4
DEFAULT_BEATS_PER_BAR = 4


def infer_beats_per_bar(beats: BeatGrid):
    """#130's meter-numerator inference, gate included.

    Returns `(numerator, committed)`. `committed` is False when the
    evidence did not clear the gate, in which case the numerator is
    `DEFAULT_BEATS_PER_BAR` -- written to the file anyway, because a
    downbeat grid is worth having even when the meter label is wrong, but
    recorded as *not* inferred so the manifest can say so (#130's
    "correctable guess" made concrete)."""
    if len(beats.downbeat_seconds) < METER_MIN_BARS + 1 or not beats.beat_seconds:
        return DEFAULT_BEATS_PER_BAR, False

    beat_times = np.asarray(beats.beat_seconds, dtype=float)
    counts = []
    for start, end in zip(beats.downbeat_seconds, beats.downbeat_seconds[1:]):
        count = int(np.count_nonzero((beat_times >= start - 1e-6) & (beat_times < end - 1e-6)))
        if count > 0:
            counts.append(count)
    if len(counts) < METER_MIN_BARS:
        return DEFAULT_BEATS_PER_BAR, False

    modal = max(set(counts), key=counts.count)
    agreement = counts.count(modal) / len(counts)
    if modal not in METER_CANDIDATES or agreement < METER_MIN_AGREEMENT:
        return DEFAULT_BEATS_PER_BAR, False
    return modal, True


def convert(
    audio,
    sample_rate,
    separator=None,
    note_transcriber=None,
    beat_tracker=None,
    chord_estimator=None,
    want_notes=True,
    want_chords=True,
):
    """Run the pipeline over `audio`, using whichever backends are given.

    Every backend is optional *as an argument* but not optional *as a
    requirement*: asking for notes with no `note_transcriber` raises
    `ConversionUnavailable` rather than quietly returning an empty score.
    That is #129's refuse-don't-degrade rule at the one place it can
    actually be enforced.

    With no `separator`, the mix is transcribed directly as a single
    track. That is not a fallback -- it is H1's control arm, the "without
    separation" half of the A/B #126 says nobody has ever run."""
    audio = np.asarray(audio, dtype=np.float64)
    duration_seconds = audio.size / float(sample_rate) if sample_rate else 0.0

    if want_notes and note_transcriber is None:
        raise ConversionUnavailable(
            "Note transcription needs a transcription model, and none is installed.",
            CONVERT_EXTRA,
        )
    if want_chords and chord_estimator is None:
        # Unlike notes, this one has a tier that needs nothing installed,
        # so defaulting to it is not a silent substitution of a worse
        # model -- it is the only model, until an extra brings a better one.
        chord_estimator = TemplateChordEstimator()

    stems = {}
    if separator is not None:
        stems = separator.separate(audio, sample_rate) or {}

    beats = BeatGrid()
    if beat_tracker is not None:
        # #130's timing oracle: the separated drum stem as an extra input
        # channel, measured worth downbeat F1 0.699 -> 0.775 on drum-heavy
        # material. A tracker that cannot use it ignores it.
        beats = beat_tracker.track(audio, sample_rate, drum_stem=stems.get(STEM_DRUMS))

    tracks = []
    if want_notes:
        model_name = type(note_transcriber).__name__
        if stems:
            for stem_name, stem_audio in sorted(stems.items()):
                # v1 writes no notated drum part (#130): drums are a
                # timing oracle here, and a notated drum part needs an ADT
                # model whose only licence-clean route is training on STAR
                # Drums -- which graduates to its own effort.
                if stem_name == STEM_DRUMS:
                    continue
                tracks.append(
                    Track(
                        name=stem_name,
                        notes=note_transcriber.transcribe(stem_audio, sample_rate),
                        source_stem=stem_name,
                        model=model_name,
                    )
                )
        else:
            tracks.append(
                Track(
                    name="mix",
                    notes=note_transcriber.transcribe(audio, sample_rate),
                    source_stem=None,
                    model=model_name,
                )
            )

    chords = []
    if want_chords:
        # Chords come from the harmonic content, so a separated mix is
        # reassembled without drums rather than analysed per stem -- a
        # chord is a property of the whole harmony, not of one instrument.
        harmonic = audio
        if stems:
            parts = [a for name, a in stems.items() if name != STEM_DRUMS]
            if parts:
                harmonic = np.sum([np.asarray(p, dtype=np.float64) for p in parts], axis=0)
        chords = chord_estimator.estimate(harmonic, sample_rate, beats=beats)

    beats_per_bar, committed = infer_beats_per_bar(beats)
    return ConversionResult(
        tracks=tracks,
        chords=chords,
        beats=beats,
        beats_per_bar=beats_per_bar if committed else None,
        duration_seconds=duration_seconds,
    )
