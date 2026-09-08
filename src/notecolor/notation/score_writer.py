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

from fractions import Fraction

from notecolor.settings import config
from notecolor.analysis.color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from notecolor.settings.config_store import store
from notecolor.analysis.duration_tracker import duration_class_for_beats
from notecolor.analysis.staff_map import staff_row

# quarterLength (music21's per-quarter-note duration unit) is numerically
# identical to duration_tracker.py's "beats" unit everywhere in this
# codebase -- a "beat" is always a quarter note (see TempoTracker/
# DurationTracker) -- so this table is exactly duration_tracker.py's
# _DURATION_CLASSES beat values, just keyed by name instead of looked up
# by nearest-beat distance. No tuplet handling needed here (issue #62):
# every duration_class name is already a plain, possibly-dotted,
# power-of-two note value.
QUARTER_LENGTHS = {
    # Triplets (map #123, issue #130) are exact Fractions, not floats:
    # music21 normalises a quarterLength through `opFrac` and builds the
    # matching `duration.Tuplet` automatically from it (verified: 1/3 ->
    # type "eighth" + Tuplet 3/2/eighth), so a triplet needs no explicit
    # Tuplet construction here -- but it does need a value that is exactly
    # a third, which a float is not.
    "triplet-half": Fraction(4, 3),
    "triplet-quarter": Fraction(2, 3),
    "triplet-eighth": Fraction(1, 3),
    "triplet-sixteenth": Fraction(1, 6),
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

# Divisors for the offset-quantization guard both writers apply. 8 is a
# 32nd-note grid, matching QUARTER_LENGTHS' finest dyadic grain; 12 is
# what a triplet needs, since a triplet-eighth onset falls at 1/3 of a
# quarter and an 8-only grid would shift it to 3/8. music21 picks the
# divisor that fits each element best, so a dyadic offset still lands
# exactly on the 8 grid.
OFFSET_QUANTIZE_DIVISORS = (8, 12)

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
    function, ~line 994).

    Deliberately does NOT pass `allow_tuplets=True`, even though
    QUARTER_LENGTHS can now express a triplet (issue #130). This path
    derives beats from `result.bpm`, a single tempo scalar for the whole
    recording -- exactly the input #130 found a triplet cannot be
    distinguished from ordinary timing jitter against. Tuplets are for the
    offline converter, which has a real per-beat grid from Beat This! to
    quantize against."""
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
    treble.quantize(quarterLengthDivisors=OFFSET_QUANTIZE_DIVISORS, processOffsets=True, processDurations=False,
                     inPlace=True, recurse=True)
    bass.quantize(quarterLengthDivisors=OFFSET_QUANTIZE_DIVISORS, processOffsets=True, processDurations=False,
                   inPlace=True, recurse=True)

    score = stream.Score()
    score.insert(0, treble)
    score.insert(0, bass)
    score.insert(0, layout.StaffGroup([treble, bass], symbol="brace"))

    score.write("musicxml", fp=path)


# --- Multi-track score projects (map #123, issues #129/#131) --------------

#: Key of the JSON track manifest inside the score-level `<miscellaneous>`
#: block. #128 measured that per-part `<identification>` is silently
#: dropped by music21 and that `Part.id` does not survive a round trip, so
#: `<part-name>` is the only stable track identity and this manifest is
#: keyed by it.
PROJECT_MANIFEST_KEY = "note-color.project"
#: Bumped to 2 by ticket #150, which renamed the manifest's `tracks` key to
#: `parts`. Under map #145's glossary a **Track** is a DAW timeline lane and a
#: **Part** is one instrument's notated music -- MusicXML's own word for it
#: (`<score-part>`, `<part-name>`), which is what this key always held.
#: `read_project_manifest()` still accepts a version 1 file.
PROJECT_MANIFEST_VERSION = 2


def _beat_positions(times, beat_seconds):
    """Seconds -> position in beats, against a real beat grid.

    This is #130's "quantize against the beat grid, not a global tempo" in
    its most direct form: linear interpolation between detected beat
    times, so a beat is one quarter-note of score time no matter how the
    tempo drifts underneath it. Outside the grid's range it extrapolates
    from the nearest beat interval rather than clamping, which keeps a
    pickup before the first detected beat, or a tail after the last, at a
    sane position instead of piling everything onto one offset."""
    times = np.asarray(times, dtype=float)
    beats = np.asarray(beat_seconds, dtype=float)
    if beats.size < 2:
        return None
    indices = np.arange(beats.size, dtype=float)
    positions = np.interp(times, beats, indices)
    # np.interp clamps; redo the out-of-range ends by extrapolation.
    first_gap = beats[1] - beats[0]
    last_gap = beats[-1] - beats[-2]
    if first_gap > 0:
        before = times < beats[0]
        positions[before] = (times[before] - beats[0]) / first_gap
    if last_gap > 0:
        after = times > beats[-1]
        positions[after] = (beats.size - 1) + (times[after] - beats[-1]) / last_gap
    return positions


def _tempo_from_beats(beat_seconds, default_bpm=_FALLBACK_BPM_FOR_OFFSETS):
    """Median BPM implied by a beat grid.

    Median, not mean, so one dropped or doubled beat does not drag the
    whole tempo marking. This is a single number written for legibility --
    #131 settled that tempo is stored as a *curve*, and this is not it;
    the curve is a later step, and the grid itself is the real record."""
    beats = np.asarray(beat_seconds, dtype=float)
    if beats.size < 2:
        return default_bpm
    gaps = np.diff(beats)
    gaps = gaps[gaps > 1e-6]
    if gaps.size == 0:
        return default_bpm
    return float(60.0 / np.median(gaps))


#: Subdivisions of a beat that a converted onset may land on.
#:
#: 12 is the reference grid #127 found the field uses, and it is the
#: smallest number covering both binary and ternary subdivision: a
#: sixteenth is 3/12 of a beat, a triplet-eighth 4/12, a triplet-sixteenth
#: 2/12. Without it, raw interpolated beat positions are arbitrary floats
#: and music21 raises "Cannot convert inexpressible durations to
#: MusicXML" -- it cannot express the rest it would need between two
#: notes at unquantized offsets. That is the same constraint
#: `score_writer.write_score()`'s existing 32nd-note offset guard exists
#: for, met here by quantizing musically rather than by rounding after the
#: fact.
#:
#: This constrains *onsets*, not durations. A note's duration is the
#: difference between two grid positions, and one grid unit (1/12 beat,
#: 0.083) still snaps to a thirtysecond (0.125) as its nearest class --
#: measured, not assumed. So 32nd notes can appear in output even though
#: no onset sits on a 32nd-note position off the beat grid. Clamping that
#: away was considered and rejected: a 1/12-beat span is a real grid unit,
#: and rewriting it as a sixteenth would misstate a duration the grid can
#: actually resolve.
BEAT_SUBDIVISIONS = 12


def snap_to_grid(positions, subdivisions=BEAT_SUBDIVISIONS):
    """Quantize beat positions to the nearest 1/`subdivisions` of a beat."""
    return np.round(np.asarray(positions, dtype=float) * subdivisions) / float(subdivisions)


def _confidence_triples(part, to_beats):
    """[[beat, midi, confidence], ...] for the notes of `part` that have
    one. Empty when the model reported none, rather than fabricating a
    default -- a missing confidence and a confidence of zero are not the
    same claim."""
    scored = [n for n in part.notes if n.confidence is not None]
    if not scored:
        return []
    positions = to_beats([n.onset_seconds for n in scored])
    return [
        [round(float(beat), 4), int(n.pitch_midi), round(float(n.confidence), 3)]
        for n, beat in zip(scored, positions)
    ]


def _pad_to(part, result, to_beats):
    """Extend `part` with a trailing rest so it spans the whole
    conversion.

    Without this, music21 creates measures only as far as the last
    *note*, and a `ChordSymbol` positioned beyond that is **silently
    dropped** on write (measured: a symbol at beat 4 in a part whose last
    note ends at beat 2 does not survive the round trip). A lead sheet
    routinely ends on a chord held past the final melody note, so this is
    the common case rather than an edge one."""
    from music21 import note as m21note

    end_beats = float(to_beats([result.duration_seconds])[0]) if result.duration_seconds else 0.0
    if result.chords:
        end_beats = max(end_beats, float(to_beats([result.chords[-1].end_seconds])[0]))
    current = float(part.highestTime)
    if end_beats > current + 1e-6:
        filler = m21note.Rest()
        filler.duration.quarterLength = max(end_beats - current, 1.0 / BEAT_SUBDIVISIONS)
        part.insert(current, filler)


def write_project(result, path, key_fifths=0, melody_part=None, title=None):
    """Write a `convert.ConversionResult` as a **multi-track MusicXML
    project** (issue #131).

    One track per `<score-part>`, identified by a unique `<part-name>`;
    per-track provenance in a versioned JSON manifest in the *score-level*
    `<miscellaneous>` block; tempo, time signature and key score-global on
    the first part. Chord spans, if any, are written as MusicXML
    `<harmony>` elements on the melody part -- verified here to round-trip
    through `music21.converter.parse()` with figure and offset intact,
    which #131 recorded as unverified.

    Note onsets and durations are placed against `result.beats` when a
    grid exists (#130), falling back to a constant tempo otherwise, and
    durations snap through `duration_class_for_beats(allow_tuplets=True)`
    so a triplet can be written at all (#130's tuplet decision).
    """
    import json

    from music21 import harmony, metadata, tempo as m21tempo

    from notecolor.analysis.duration_tracker import beats_for_duration_class, duration_class_for_beats

    beat_seconds = list(result.beats.beat_seconds)
    bpm = _tempo_from_beats(beat_seconds)
    beats_per_bar = result.beats_per_bar or config.DEFAULT_TIME_SIGNATURE[0]
    # #130: the denominator is a convention, never an inference. 4, or 8
    # when the numerator is a compound-meter value.
    denominator = 8 if beats_per_bar in (6, 9, 12) else 4
    time_sig_str = f"{beats_per_bar}/{denominator}"

    def to_beats(times):
        positions = _beat_positions(times, beat_seconds)
        if positions is None:
            positions = np.asarray(times, dtype=float) * (bpm / 60.0)
        return snap_to_grid(positions)

    score = stream.Score()
    md = metadata.Metadata()
    md.title = title or "Converted score"
    manifest = {
        "version": PROJECT_MANIFEST_VERSION,
        "parts": [
            {
                "name": part.name,
                "source_stem": part.source_stem,
                "model": part.model,
                "low_confidence": bool(part.low_confidence),
                "note_count": len(part.notes),
                # Per-note confidence lives HERE, not on the notes
                # themselves: MusicXML has no per-note certainty field and
                # music21's `editorial` dict is **not exported** --
                # measured, a note carrying editorial.confidence writes a
                # file with no trace of it. Stored as [beat, midi,
                # confidence] triples so a reader matches them back by
                # position and pitch. #143 established the signal is free
                # and useful; dropping it because the format has no slot
                # would be the wrong trade.
                "note_confidence": _confidence_triples(part, to_beats),
            }
            for part in result.parts
        ],
        "meter_inferred": result.beats_per_bar is not None,
        "beat_count": len(beat_seconds),
    }
    md.setCustom(PROJECT_MANIFEST_KEY, json.dumps(manifest))
    score.insert(0, md)

    melody_name = melody_part or (result.parts[0].name if result.parts else None)
    used_names = set()

    for index, source in enumerate(result.parts):
        # `source` is this project's own Part (the data); `part` is
        # music21's stream.Part (the notation object being built). Keeping
        # the two names apart matters -- they are both "a part" and a single
        # shared name silently reads the wrong one.
        part = stream.Part()
        # Unique part names are load-bearing: #128 found this is the only
        # part identity that survives, so a duplicate would silently
        # merge two parts' provenance on read.
        name = source.name
        suffix = 2
        while name in used_names:
            name = f"{source.name} {suffix}"
            suffix += 1
        used_names.add(name)
        part.partName = name
        part.insert(0, meter.TimeSignature(time_sig_str))
        part.insert(0, m21key.KeySignature(key_fifths))
        if index == 0:
            # A MetronomeMark inserted on the Score itself is silently
            # discarded by music21 (#128), so it goes on the first part.
            part.insert(0, m21tempo.MetronomeMark(number=round(bpm, 2)))

        if source.notes:
            onsets = to_beats([n.onset_seconds for n in source.notes])
            offsets = to_beats([n.offset_seconds for n in source.notes])
            for transcribed, start, end in zip(source.notes, onsets, offsets):
                # A note quantized to zero length would be dropped by
                # music21 rather than written; give it the shortest value
                # the grid can express instead of silently losing it.
                span = max(float(end - start), 1.0 / BEAT_SUBDIVISIONS)
                duration_class = duration_class_for_beats(span, allow_tuplets=True)
                element = note.Note(transcribed.pitch_midi)
                element.duration.quarterLength = QUARTER_LENGTHS[duration_class]
                element.style.color = note_hex_color(transcribed.pitch_midi % 12)
                part.insert(float(max(start, 0.0)), element)

        if result.chords and name == melody_name:
            chord_starts = to_beats([c.start_seconds for c in result.chords])
            for span, start in zip(result.chords, chord_starts):
                try:
                    symbol = harmony.ChordSymbol(span.name)
                except Exception:
                    # This repo's jazz spelling (Δ7, ø7) is not always a
                    # figure music21 parses. A chord we cannot express is
                    # skipped rather than crashing the whole write --
                    # the same blank-rather-than-a-guess posture
                    # chord_templates.match() already takes.
                    continue
                part.insert(float(max(start, 0.0)), symbol)

        _pad_to(part, result, to_beats)
        score.insert(0, part)

    if not result.parts:
        # A chords-only conversion still has to produce a readable file.
        part = stream.Part()
        part.partName = "chords"
        part.insert(0, meter.TimeSignature(time_sig_str))
        part.insert(0, m21tempo.MetronomeMark(number=round(bpm, 2)))
        for span, start in zip(result.chords, to_beats([c.start_seconds for c in result.chords])):
            try:
                part.insert(float(max(start, 0.0)), harmony.ChordSymbol(span.name))
            except Exception:
                continue
        score.insert(0, part)

    score.write("musicxml", fp=str(path))
    return str(path)


def read_project_manifest(path):
    """The JSON part manifest from a project file, or `None`.

    `music21.metadata.Metadata.getCustom()` returns a **tuple** of `Text`
    objects rather than a string (measured, not assumed), which is the
    kind of detail that silently produces `"(<music21...Text ...>,)"` in a
    manifest field if a caller stringifies it directly."""
    import json

    from music21 import converter

    parsed = converter.parse(str(path))
    if parsed.metadata is None:
        return None
    custom = parsed.metadata.getCustom(PROJECT_MANIFEST_KEY)
    if not custom:
        return None
    raw = custom[0] if isinstance(custom, (tuple, list)) else custom
    try:
        manifest = json.loads(str(raw))
    except (ValueError, TypeError):
        return None
    # Version 1 spelled this key `tracks`, before map #145 reserved that word
    # for a DAW timeline lane. Normalise on read so a project written by an
    # earlier build still opens -- the same additive, degrade-rather-than-fail
    # posture `config_store` and `patch_format` take.
    if isinstance(manifest, dict) and "parts" not in manifest and "tracks" in manifest:
        manifest["parts"] = manifest.pop("tracks")
    return manifest
