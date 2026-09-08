"""MusicXML -> Project (map #145, milestone 1).

The third module permitted to import `music21`, alongside `score_writer.py`
and `score_editor_state.py`, and for the same reason they are: the import is
real and one-time, and it has no business on the live per-hop path. It is done
lazily inside the function so importing this module costs nothing.

**One Part per Track.** MusicXML's `<score-part>` is exactly this project's
notion of a **Part** (ticket #150), and a Part becomes a **Track** on import --
a lane you can then edit, mute and record alongside.

This deliberately does *not* go through `score_editor_state.load_score()`.
That function refuses a multi-part file outright (`MultiTrackScoreError`),
which was correct for a single-grand-staff terminal editor and is precisely the
limitation this milestone exists to lift. It also merges both staves of a grand
staff into one flat column list keyed by offset, which is the right shape for a
cursor and the wrong one for a timeline.

What is deliberately not imported: dynamics, articulations, lyrics, slurs,
repeats. A Project has nowhere to put them yet, and inventing storage for
things nothing reads would be guessing at a format.
"""

import os

from notecolor.project.model import (
    Note,
    NoteClip,
    Project,
    TempoAnchor,
    TempoMap,
    TimeSignature,
    Track,
)

#: A grand staff arrives as two `PartStaff` objects sharing one `<score-part>`.
#: They are one instrument and become one Track, not two.
GRAND_STAFF_JOIN = True


def import_musicxml(path, name=None):
    """Read a MusicXML file into a `Project`."""
    from music21 import converter, note as m21note, tempo as m21tempo

    parsed = converter.parse(str(path))
    project = Project(name=name or os.path.splitext(os.path.basename(str(path)))[0])

    flat = parsed.flatten()
    marks = list(flat.getElementsByClass(m21tempo.MetronomeMark))
    anchors = []
    for mark in marks:
        bpm = mark.getQuarterBPM()
        if bpm:
            anchors.append(TempoAnchor(float(mark.offset), float(bpm)))
    project.tempo_map = TempoMap(anchors) if anchors else TempoMap()

    signature = flat.timeSignature
    if signature is not None:
        project.time_signature = TimeSignature(signature.numerator, signature.denominator)
    key = flat.keySignature
    if key is not None:
        project.key_fifths = int(key.sharps or 0)

    for part in _instrument_parts(parsed):
        track = _track_from_part(part, project)
        if track is not None:
            project.tracks.append(track)
    return project


def _instrument_parts(parsed):
    """One entry per instrument.

    music21 exposes each staff of a grand staff as its own `PartStaff` inside a
    `StaffGroup`. Those are one instrument played by two hands, so they are
    merged -- otherwise a piano arrives as two Tracks that can be muted
    independently, which is not a thing a piano can do.
    """
    from music21 import layout, stream

    parts = list(parsed.getElementsByClass(stream.Part))
    if not parts:
        return [parsed]
    if not GRAND_STAFF_JOIN:
        return parts

    grouped, claimed = [], set()
    for group in parsed.getElementsByClass(layout.StaffGroup):
        members = [p for p in group.getSpannedElements() if p in parts]
        if len(members) > 1:
            grouped.append(members)
            claimed.update(id(m) for m in members)
    merged = [[p] for p in parts if id(p) not in claimed]
    return [_merge(members) for members in grouped] + [m[0] for m in merged]


def _merge(members):
    """Two staves of one instrument, as a single stream."""
    from music21 import stream

    combined = stream.Part()
    combined.partName = members[0].partName or members[0].id
    for member in members:
        for element in member.flatten().notes:
            combined.insert(element.offset, element)
    return combined


def _track_from_part(part, project):
    from music21 import chord as m21chord

    notes = []
    for element in part.flatten().notes:
        pitches = element.pitches if isinstance(element, m21chord.Chord) else (element.pitch,)
        length = float(element.quarterLength)
        if length <= 0:
            # A grace note has zero length. Giving it a real one would be
            # inventing rhythm the file does not contain, so it is skipped --
            # visibly absent beats silently mistimed.
            continue
        for pitch in pitches:
            notes.append(Note(start_beat=float(element.offset),
                              duration_beats=length,
                              pitch=int(pitch.midi)))
    if not notes:
        return None
    notes.sort(key=lambda n: (n.start_beat, n.pitch))
    end = max(n.start_beat + n.duration_beats for n in notes)
    name = project.unique_track_name(_part_name(part))
    return Track(name=name,
                 color_pitch_class=notes[0].pitch_class,
                 clips=[NoteClip(name=name.lower(), start_beat=0.0,
                                 length_beats=end, notes=notes)])


def _part_name(part):
    """A human name for a part, or a plain fallback.

    music21 fills `id` with the object's memory address as a decimal string
    when a file names no part, which is how "140237127173520" ends up as a
    track name. Anything all-digits is that, not a name.
    """
    for candidate in (getattr(part, "partName", None), getattr(part, "id", None)):
        text = str(candidate).strip() if candidate is not None else ""
        if text and not text.isdigit() and not text.startswith("0x"):
            return text
    return "Part"
