"""Score editor data model + MusicXML persistence (issue #98's synthesized
spec, data-layer half; synthesized from wayfinder map #85 and its four
resolved children #86/#87/#88/#90).

One of two modules in this codebase permitted to import `music21` (the
other is `score_writer.py`) -- mirrors how `rhythm_reanalysis.py` became a
second permitted `librosa` importer alongside `batch_transcribe.py` (issue
#77's precedent, see that module's docstring): `music21` stays confined to
`load_score()`/`save_score()` here, never touching the live/Pi-constrained
analysis path, and this module reuses `score_writer.py`'s already-public
`QUARTER_LENGTHS`/`note_hex_color()`/`pitch_for()` rather than duplicating
them (see docs/DECISIONS.md for why sharing beats a second copy here).

Per #86: `EditorScore`/`EditorColumn`/`EditorNote` are a plain, simple
mutable intermediate structure -- no music21 objects escape `load_score()`,
and nothing outside `save_score()` builds a music21 graph. This keeps the
(not-yet-built) interactive terminal editor layer free of music21 entirely,
same "confine the heavy/slow import to one clearly-bounded module" posture
`batch_transcribe.py`/`score_writer.py` already established.

Public API: `EditorNote`, `EditorColumn`, `EditorScore` (dataclasses),
`new_blank_score()`, `load_score(path)`, `save_score(score, path)`,
`EditHistory` (bounded multi-level undo/redo over plain `EditorScore`
snapshots).
"""

import copy
from dataclasses import dataclass, field

from music21 import chord as m21chord
from music21 import clef, converter
from music21 import key as m21key
from music21 import layout, meter
from music21 import note as m21note
from music21 import stream
from music21 import tempo as m21tempo

import config
from duration_tracker import DEFAULT_DURATION_CLASS, duration_class_for_beats
from score_writer import QUARTER_LENGTHS, note_hex_color, pitch_for
from staff_map import staff_row

# Fallback tempo for a parsed file with no MetronomeMark at all -- true of
# every file score_writer.write_score() has produced so far (it never
# wrote one), so this is the normal case for a round-tripped batch-export
# file, not a rare edge case. Matches new_blank_score()'s own default.
DEFAULT_TEMPO_BPM = 90.0


@dataclass
class EditorNote:
    pitch_class: int  # 0-11
    octave: int


@dataclass
class EditorColumn:
    notes: list  # list[EditorNote]; empty list == Rest (see #90/CONTEXT.md's Rest entry)
    duration_class: str  # one of duration_tracker.DURATION_CLASS_ORDER's names


@dataclass
class EditorScore:
    time_signature: tuple  # (numerator, denominator)
    key_fifths: int  # circle-of-fifths position, -7..7 (matches music21 Key.sharps)
    tempo_bpm: float
    columns: list  # list[EditorColumn]


def new_blank_score() -> EditorScore:
    """A brand-new, empty score -- `virtualnote edit <path>`'s starting
    point when `<path>` doesn't exist yet. One starting column (Rest, the
    default duration class) so the cursor always has somewhere to land --
    an editor can never have zero columns, mirroring `x`/`delete_column`'s
    refusal to delete the last remaining column."""
    return EditorScore(
        time_signature=config.DEFAULT_TIME_SIGNATURE,
        key_fifths=0,
        tempo_bpm=DEFAULT_TEMPO_BPM,
        columns=[EditorColumn(notes=[], duration_class=DEFAULT_DURATION_CLASS)],
    )


def _staff_for(pitch_class, octave):
    """Which grand-staff part a note belongs on when writing -- deliberately
    re-derived here rather than importing `score_writer._staff_for()`
    (that helper stayed private; issue #98's spec only named
    `QUARTER_LENGTHS`/`note_hex_color()`/`pitch_for()` for promotion). Same
    one-line threshold `score_writer.py`'s own `_staff_for()` uses: row 10
    is middle C, the one row genuinely ambiguous between the two clefs --
    everything from middle C up goes to the treble part, everything below
    to the bass part."""
    return "treble" if staff_row(pitch_class, octave) >= 10 else "bass"


def _build_element(column: "EditorColumn"):
    """One music21 Note/Chord/Rest for a single staff's slice of a column
    (the caller decides which notes belong to which staff via
    `_staff_for()`) -- shared by both staves' construction in
    `save_score()`."""
    if not column.notes:
        element = m21note.Rest()
    elif len(column.notes) == 1:
        enote = column.notes[0]
        element = m21note.Note(pitch_for(enote.pitch_class, enote.octave))
        element.style.color = note_hex_color(enote.pitch_class)
    else:
        pitches = [pitch_for(n.pitch_class, n.octave) for n in column.notes]
        element = m21chord.Chord(pitches)
        for chord_note, enote in zip(element.notes, column.notes):
            chord_note.style.color = note_hex_color(enote.pitch_class)
    element.duration.quarterLength = QUARTER_LENGTHS[column.duration_class]
    return element


def save_score(score: EditorScore, path) -> None:
    """Writes `score` to a MusicXML file at `path`, mirroring
    `score_writer.write_score()`'s two-staff grand-staff / `<chord/>`-
    grouping / per-note-color structure -- but driven by `score.columns`'
    own fixed sequence of columns (each with an explicit duration_class,
    including Rest columns) rather than a sparse `onset_hop` mapping,
    since every editor column -- rest or not -- is real, addressable
    content that must round-trip back through `load_score()` unchanged.
    A Rest column is written as an explicit `music21.note.Rest` on *both*
    staves (not just skipped, unlike `write_score()`'s batch-conversion
    gaps) so the two parts' offsets stay in lockstep column-for-column --
    `load_score()` relies on that lockstep to merge them back into one
    flat column list.

    Also writes a `music21.tempo.MetronomeMark(number=score.tempo_bpm)`
    and a real `music21.key.KeySignature(score.key_fifths)` -- both new
    relative to `write_score()`, which never wrote either (it only ever
    guessed a key, and only when confident; never wrote tempo at all)."""
    numerator, denominator = score.time_signature
    time_sig_str = f"{numerator}/{denominator}"

    treble = stream.Part(id="treble")
    bass = stream.Part(id="bass")
    treble.insert(0, clef.TrebleClef())
    bass.insert(0, clef.BassClef())
    treble.insert(0, meter.TimeSignature(time_sig_str))
    bass.insert(0, meter.TimeSignature(time_sig_str))
    treble.insert(0, m21key.KeySignature(score.key_fifths))
    bass.insert(0, m21key.KeySignature(score.key_fifths))
    treble.insert(0, m21tempo.MetronomeMark(number=score.tempo_bpm))

    offset = 0.0
    for column in score.columns:
        quarter_length = QUARTER_LENGTHS[column.duration_class]
        staff_groups = {"treble": [], "bass": []}
        for enote in column.notes:
            staff_groups[_staff_for(enote.pitch_class, enote.octave)].append(enote)

        for staff_name, part in (("treble", treble), ("bass", bass)):
            group_column = EditorColumn(notes=staff_groups[staff_name], duration_class=column.duration_class)
            part.insert(offset, _build_element(group_column))

        offset += quarter_length

    # Same inexpressible-duration guard write_score() applies (see that
    # function's own comment) -- offsets here are already exact sums of
    # QUARTER_LENGTHS' dyadic values, so this is cheap insurance, not a
    # correction of any expected drift.
    treble.quantize(quarterLengthDivisors=(8,), processOffsets=True, processDurations=False,
                     inPlace=True, recurse=True)
    bass.quantize(quarterLengthDivisors=(8,), processOffsets=True, processDurations=False,
                   inPlace=True, recurse=True)

    m21_score = stream.Score()
    m21_score.insert(0, treble)
    m21_score.insert(0, bass)
    m21_score.insert(0, layout.StaffGroup([treble, bass], symbol="brace"))

    m21_score.write("musicxml", fp=str(path))


def load_score(path) -> EditorScore:
    """Parses a MusicXML file at `path` via `music21.converter.parse()`
    and reconstructs it into an `EditorScore` -- the reverse of
    `save_score()`'s staff-split-by-`staff_map.staff_row()`: both parts'
    notes/chords/rests are merged back into one flat `columns` list keyed
    by shared offset. A file with no tempo marking (every file
    `score_writer.write_score()` has produced so far, since it never wrote
    one) defaults `tempo_bpm` to `DEFAULT_TEMPO_BPM` (90.0) rather than
    crashing or guessing; no time signature or key signature present
    similarly falls back to `config.DEFAULT_TIME_SIGNATURE`/0 sharps.

    Known limitation: MusicXML is measure-based, so `save_score()`'s
    `stream.write()` call can pad an incomplete final measure with an
    extra rest to fill it out when a score's total duration doesn't land
    on a whole number of measures -- that padding rest would reappear here
    as one extra trailing Rest column not present in the original
    `EditorScore`. Not worked around here (see docs/DECISIONS.md) --
    affects only scores whose total duration doesn't align to a measure
    boundary."""
    parsed = converter.parse(str(path))

    time_signature = config.DEFAULT_TIME_SIGNATURE
    ts_list = list(parsed.recurse().getElementsByClass(meter.TimeSignature))
    if ts_list:
        time_signature = (ts_list[0].numerator, ts_list[0].denominator)

    key_fifths = 0
    key_list = list(parsed.recurse().getElementsByClass(m21key.KeySignature))
    if key_list:
        key_fifths = key_list[0].sharps

    tempo_bpm = DEFAULT_TEMPO_BPM
    tempo_list = list(parsed.recurse().getElementsByClass(m21tempo.MetronomeMark))
    if tempo_list and tempo_list[0].number is not None:
        tempo_bpm = float(tempo_list[0].number)

    by_offset = {}  # rounded offset -> {"notes": [EditorNote, ...], "quarter_length": float}
    for part in parsed.parts:
        for element in part.flatten().notesAndRests:
            offset_key = round(float(element.offset), 6)
            entry = by_offset.setdefault(offset_key, {"notes": [], "quarter_length": 0.0})
            entry["quarter_length"] = max(entry["quarter_length"], float(element.quarterLength))
            if isinstance(element, m21chord.Chord):
                for p in element.pitches:
                    entry["notes"].append(EditorNote(pitch_class=p.pitchClass, octave=p.octave))
            elif isinstance(element, m21note.Note):
                entry["notes"].append(
                    EditorNote(pitch_class=element.pitch.pitchClass, octave=element.pitch.octave)
                )
            # A Rest contributes no notes -- an empty column stays empty.

    if not by_offset:
        columns = [EditorColumn(notes=[], duration_class=DEFAULT_DURATION_CLASS)]
    else:
        columns = []
        for offset_key in sorted(by_offset):
            entry = by_offset[offset_key]
            duration_class = duration_class_for_beats(entry["quarter_length"])
            columns.append(EditorColumn(notes=entry["notes"], duration_class=duration_class))

    return EditorScore(
        time_signature=time_signature,
        key_fifths=key_fifths,
        tempo_bpm=tempo_bpm,
        columns=columns,
    )


@dataclass
class EditHistory:
    """Bounded multi-level undo/redo (per #88) over plain `EditorScore`
    snapshots -- cheap since `EditorScore`/`EditorColumn`/`EditorNote` are
    plain dataclasses with no music21 graph attached. `.record(score)` is
    called with the state *before* a mutation: it pushes that previous
    snapshot and clears the redo stack (a fresh edit invalidates whatever
    was previously undone). `.undo(current)`/`.redo(current)` take the
    live score being displayed/edited and return the previous/next
    snapshot, or `None` at either bound (nothing left to undo/redo) --
    `None` means "no-op," never an exception, since hitting either bound
    during ordinary editing is a normal occurrence, not an error."""

    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    def record(self, score: EditorScore) -> None:
        self.undo_stack.append(copy.deepcopy(score))
        if len(self.undo_stack) > config.EDITOR_UNDO_MAX_DEPTH:
            del self.undo_stack[0]
        self.redo_stack.clear()

    def undo(self, current: EditorScore):
        if not self.undo_stack:
            return None
        previous = self.undo_stack.pop()
        self.redo_stack.append(copy.deepcopy(current))
        return previous

    def redo(self, current: EditorScore):
        if not self.redo_stack:
            return None
        next_score = self.redo_stack.pop()
        self.undo_stack.append(copy.deepcopy(current))
        return next_score
