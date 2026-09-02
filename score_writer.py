"""MusicXML score writer (issue #65, batch-only, v1).

One of two modules in this codebase permitted to import `music21` (the
other is `score_editor_state.py`, issue #98's second permitted importer --
see that module's docstring) -- mirrors `batch_transcribe.py`'s
sole-`librosa`-importer convention (see that module's docstring for the
rationale): `music21`'s import cost is real and one-time, and has no
business landing on the live/Pi-constrained path, so it's isolated to
these two modules and invoked only when a caller explicitly wants a
written/editable score.

Public API: `write_score(result, path, time_signature=...)` and
`guess_key_signature(chroma_histogram)`. Both take/build on
`batch_transcribe.TranscriptionResult` -- nothing here touches live audio,
`SessionState`, or any live-path module. `QUARTER_LENGTHS` (the
duration_class -> music21 quarterLength lookup), `note_hex_color()`, and
`pitch_for()` are also public -- promoted from private names for issue #98
so `score_editor_state.py`'s own MusicXML save/load can reuse this
module's per-note color and pitch-spelling logic and duration lookup table
instead of duplicating them (see docs/DECISIONS.md for the rationale).
"""

import numpy as np
from music21 import chord, clef, key as m21key, layout, meter, note, pitch, stream

import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from config_store import store
from duration_tracker import duration_class_for_beats
from staff_map import staff_row

# quarterLength (music21's per-quarter-note duration unit) is numerically
# identical to duration_tracker.py's "beats" unit everywhere in this
# codebase -- a "beat" is always a quarter note (see TempoTracker/
# DurationTracker) -- so this table is exactly duration_tracker.py's
# _DURATION_CLASSES beat values, just keyed by name instead of looked up
# by nearest-beat distance. No tuplet handling needed here (issue #62):
# every duration_class name is already a plain, possibly-dotted,
# power-of-two note value.
QUARTER_LENGTHS = {
    "whole": 4.0,
    "dotted-half": 3.0,
    "half": 2.0,
    "dotted-quarter": 1.5,
    "quarter": 1.0,
    "dotted-eighth": 0.75,
    "eighth": 0.5,
    "dotted-sixteenth": 0.375,
    "sixteenth": 0.25,
    "thirtysecond": 0.125,
}

# A fallback tempo used ONLY to convert each note's onset_time (seconds)
# into a beat offset for positioning within the score when
# result.bpm is None (e.g. an empty/near-silent recording -- see
# batch_transcribe._estimate_bpm()). Per-note duration_class already
# falls back to duration_tracker.DEFAULT_DURATION_CLASS in that case
# independently of this constant; this only affects where notes land in
# the bar, not how long each one is drawn.
_FALLBACK_BPM_FOR_OFFSETS = 120.0

# Krumhansl-Kessler key-profile weights (Krumhansl & Kessler 1982) --
# standard 12-value major/minor tonal-hierarchy templates, index 0 =
# tonic's own weight. guess_key_signature() correlates a chroma histogram
# against every rotation of both.
_KK_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_KK_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


def _pearson_correlation(a, b):
    """Pearson correlation coefficient between two same-length 1D arrays.
    Returns 0.0 for a constant (zero-variance) input rather than dividing
    by zero -- a flat/uniform chroma histogram (no tonal center) or
    silence correlates with nothing, which is exactly "no confident key",
    not an error."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a @ a) * (b @ b))
    if denom == 0:
        return 0.0
    return float((a @ b) / denom)


def guess_key_signature(chroma_histogram):
    """Krumhansl-Schmuckler-style key guess from a whole-recording summed
    chroma histogram (12-element, `batch_transcribe.TranscriptionResult.
    chroma_histogram`). Correlates the histogram against all 24 rotations
    (12 major + 12 minor) of the Krumhansl-Kessler profiles and returns
    the best-correlating `music21.key.Key`, or None if the best
    correlation falls below `config.KEY_GUESS_CONFIDENCE_THRESHOLD` --
    same "blank rather than a guess" posture `chord_templates.match()`
    already uses below its own threshold."""
    histogram = np.asarray(chroma_histogram, dtype=np.float64)

    best_score = -2.0  # below any real Pearson correlation (-1..1)
    best_root = 0
    best_mode = "major"
    for mode_name, profile in (("major", _KK_MAJOR_PROFILE), ("minor", _KK_MINOR_PROFILE)):
        for root in range(12):
            # rolled[i] == profile[(i - root) % 12] -- profile index 0 is
            # always the tonic's own weight, so rotating by `root` moves
            # that peak to pitch-class `root`.
            rotated = np.roll(profile, root)
            score = _pearson_correlation(histogram, rotated)
            if score > best_score:
                best_score, best_root, best_mode = score, root, mode_name

    if best_score < config.KEY_GUESS_CONFIDENCE_THRESHOLD:
        return None
    tonic_name = NOTE_NAMES_FIFTHS[best_root]
    return m21key.Key(tonic_name, best_mode)


def note_hex_color(pitch_class):
    """A note's score color -- same fixed-lightness fifths-hue mapping
    `main.py`'s `_tab_note_rgb()` uses for the `tab` view, so a note reads
    as the same color in an exported score as it does live. Returns
    `#RRGGBB`, the format `music21`'s `Note.style.color`/`Chord`-member
    `.style.color` accepts."""
    hue, sat, _light = note_to_hsl(
        pitch_class, config.MAX_OCTAVE, scheme="fifths", hue_override=store.note_hue_override(pitch_class)
    )
    r, g, b = hsl_to_rgb255(hue, sat, config.TAB_NOTE_LIGHTNESS)
    return f"#{r:02X}{g:02X}{b:02X}"


def pitch_for(pitch_class, octave):
    """`music21.pitch.Pitch` for a (pitch_class, octave) pair, spelled via
    NOTE_NAMES_FIFTHS -- this project's flat-biased root-spelling
    convention (see chord_templates.py's docstring / CLAUDE.md's
    chord-mode section), same spelling `tab`'s *name* notehead style and
    `_tab_note_label()` already use."""
    return pitch.Pitch(f"{NOTE_NAMES_FIFTHS[pitch_class]}{octave}")


def _staff_for(pitch_class, octave):
    """Which grand-staff part a note belongs on -- reuses
    `staff_map.staff_row()`, the exact function `tab`'s own rendering
    places noteheads with, rather than inventing a new threshold. Row 10
    is middle C (the one ledger-line row *between* the two staves, per
    `staff_map.py`'s docstring); everything from middle C up goes to the
    treble part, everything below it to the bass part -- a simple,
    documented tie-break for the one row genuinely ambiguous between the
    two clefs."""
    return "treble" if staff_row(pitch_class, octave) >= 10 else "bass"


def _duration_quarter_length(note_event, result):
    """This note's music21 quarterLength, via the same duration_class
    computation `main.run_batch_transcribe()` already performs (see that
    function, ~line 994) -- no tuplet handling needed (issue #62 deferred
    it), so the duration_class name maps straight to a quarterLength."""
    note_beats = (
        (note_event.duration_hops * result.hop_seconds * result.bpm / 60.0) if result.bpm else None
    )
    duration_class = duration_class_for_beats(note_beats)
    return QUARTER_LENGTHS[duration_class]


def write_score(result, path, time_signature=config.DEFAULT_TIME_SIGNATURE):
    """Writes `result` (a `batch_transcribe.TranscriptionResult`) to a
    MusicXML file at `path` via `music21`: two-staff grand staff (treble +
    bass `music21.stream.Part`s, see `_staff_for()`), one column per
    `onset_hop` in `result.notes` (the polyphonic list -- required to
    represent chord-mode's simultaneous notes as `<chord/>` groups, see
    #30), each note colored via `note_hex_color()`, time signature from
    `time_signature` (a `(numerator, denominator)` tuple, same shape as
    `config.DEFAULT_TIME_SIGNATURE`), and a key signature guessed by
    `guess_key_signature(result.chroma_histogram)` -- left at music21's
    default (C major / no accidentals) when that guess returns None.

    Simultaneous notes that land on the *same* staff become one
    `music21.chord.Chord`, sharing that chord's one quarterLength --
    the longest of the group's own individually-computed durations, same
    "longest of the simultaneous notes" convention
    `main.run_batch_transcribe()` already uses for its barline
    beat-accumulator. Simultaneous notes split across the two staves (e.g.
    a bass note under a treble chord) become independent notes/chords at
    the same beat offset in each part -- no `<chord/>` tag needed there,
    since MusicXML chords are a same-part construct."""
    numerator, denominator = time_signature
    time_sig = meter.TimeSignature(f"{numerator}/{denominator}")
    guessed_key = guess_key_signature(result.chroma_histogram)

    treble = stream.Part(id="treble")
    bass = stream.Part(id="bass")
    treble.insert(0, clef.TrebleClef())
    bass.insert(0, clef.BassClef())
    treble.insert(0, meter.TimeSignature(f"{numerator}/{denominator}"))
    bass.insert(0, meter.TimeSignature(f"{numerator}/{denominator}"))
    if guessed_key is not None:
        treble.insert(0, m21key.Key(guessed_key.tonic.name, guessed_key.mode))
        bass.insert(0, m21key.Key(guessed_key.tonic.name, guessed_key.mode))

    by_hop = {}
    for note_event in result.notes:
        by_hop.setdefault(note_event.onset_hop, []).append(note_event)

    bpm_for_offsets = result.bpm if result.bpm else _FALLBACK_BPM_FOR_OFFSETS

    for onset_hop in sorted(by_hop):
        notes_here = by_hop[onset_hop]
        onset_time = onset_hop * result.hop_seconds
        offset_beats = onset_time * bpm_for_offsets / 60.0

        staff_groups = {"treble": [], "bass": []}
        for note_event in notes_here:
            staff_groups[_staff_for(note_event.pitch_class, note_event.octave)].append(note_event)

        for staff_name, group in staff_groups.items():
            if not group:
                continue
            part = treble if staff_name == "treble" else bass
            quarter_length = max(_duration_quarter_length(n, result) for n in group)

            if len(group) == 1:
                note_event = group[0]
                m21_note = note.Note(pitch_for(note_event.pitch_class, note_event.octave))
                m21_note.duration.quarterLength = quarter_length
                m21_note.style.color = note_hex_color(note_event.pitch_class)
                part.insert(offset_beats, m21_note)
            else:
                pitches = [pitch_for(n.pitch_class, n.octave) for n in group]
                m21_chord = chord.Chord(pitches)
                m21_chord.duration.quarterLength = quarter_length
                for chord_note, note_event in zip(m21_chord.notes, group):
                    chord_note.style.color = note_hex_color(note_event.pitch_class)
                part.insert(offset_beats, m21_chord)

    # Quantize each part's note *offsets* (not durations -- those already
    # come from duration_class_for_beats(), so they're already snapped to
    # one of QUARTER_LENGTHS' clean, MusicXML-expressible values) to the
    # nearest 32nd-note grid. Without this, a note's offset_beats (derived
    # straight from real, non-quantized onset_time/bpm above) lands on an
    # arbitrary fraction of a beat on real (non-synthetic) audio -- unlike
    # this module's test fixtures, whose onset times are constructed to
    # already fall on clean beat boundaries. music21's own measure-making
    # then has to insert a rest to fill the gap up to that arbitrary
    # offset, and a rest whose length isn't expressible as a
    # dotted-power-of-two note value makes `score.write()` raise
    # `MusicXMLExportException: Cannot convert inexpressible durations to
    # MusicXML` -- reproduced via a real (non-synthetic) `virtualnote
    # transcribe --write-score` run during issue #65's CLI-wiring
    # integration test. (8,) matches QUARTER_LENGTHS' finest grain
    # (thirtysecond = 0.125 quarterLength = 1/8) so no real duration class
    # gets coarsened by the snap.
    treble.quantize(quarterLengthDivisors=(8,), processOffsets=True, processDurations=False,
                     inPlace=True, recurse=True)
    bass.quantize(quarterLengthDivisors=(8,), processOffsets=True, processDurations=False,
                   inPlace=True, recurse=True)

    score = stream.Score()
    score.insert(0, treble)
    score.insert(0, bass)
    score.insert(0, layout.StaffGroup([treble, bass], symbol="brace"))

    score.write("musicxml", fp=path)
