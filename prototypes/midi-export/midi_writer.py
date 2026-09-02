"""Throwaway prototype (research question: is `--export-midi` cheap given
`score_writer.py` already exists?). NOT a real app module -- lives only
under `prototypes/midi-export/`, never imported by anything outside this
directory.

`score_writer.write_score(result, path, ...)` (issue #65) already builds a
two-staff `music21.stream.Score` from a `batch_transcribe.TranscriptionResult`
and calls `score.write("musicxml", fp=path)` at the very end -- but it never
hands the `Score` object back to its caller, only a written file. To test
whether the *same* object graph also serializes correctly as a Standard MIDI
File via `music21`'s own MIDI writer (`Score.write("midi", ...)`), this
module needs the `Score` *before* that final `.write()` call.

`build_score()` below is a deliberate, minimal duplication of
`write_score()`'s body (~30 lines) -- it calls score_writer.py's own private
helpers (`_pitch_for`, `_staff_for`, `_duration_quarter_length`,
`_note_hex_color`, `guess_key_signature`) directly rather than
reimplementing any of their logic, so every pitch/staff/duration/color/key
computation this prototype performs is *exactly* score_writer.py's own,
unmodified code -- only the orchestration loop (which needs to run once,
not twice, to build one Score both writers can consume) is duplicated here.
See docs/research/midi-export-feasibility.md for why a real implementation
should instead refactor score_writer.py itself to share a `_build_score()`
helper between `write_score()` and a new `write_midi()`, rather than ship
this duplication.

One deliberate addition beyond write_score()'s current behavior:
`include_tempo_mark=True` inserts a `music21.tempo.MetronomeMark(number=
result.bpm)` at the top of both parts when `result.bpm` is known --
write_score() itself does NOT do this today (confirmed by reading it: bpm
is used only to compute each note's beat *offset*, never inserted as a
tempo indication), so neither its MusicXML output nor a byte-identical MIDI
built from the same object graph would carry the real detected tempo --
every such file would silently default to 120bpm on playback. This
prototype adds the one-line fix to demonstrate the MIDI writer *can* carry
real tempo (`SET_TEMPO` meta-event, confirmed in this doc's own testing),
and flags the same gap as pre-existing in write_score() itself, not
MIDI-specific -- see the research doc's "Adjacent, pre-existing gap" note.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for config/score_writer

import music21
from music21 import chord, clef, key as m21key, layout, meter, stream, tempo

import config
import score_writer


def build_score(result, time_signature=config.DEFAULT_TIME_SIGNATURE, include_tempo_mark=True):
    """Builds and returns the same two-staff grand-staff `music21.stream.Score`
    `score_writer.write_score()` builds internally, without writing it to
    disk -- so a caller can hand the *same* object graph to more than one
    `music21` writer (`"musicxml"`, `"midi"`) and compare what each format
    actually retains. Mirrors `write_score()`'s body line-for-line via its
    own private helpers; see this module's docstring for why the
    duplication exists and `include_tempo_mark`'s docstring note above for
    the one deliberate behavior addition."""
    numerator, denominator = time_signature
    guessed_key = score_writer.guess_key_signature(result.chroma_histogram)

    treble = stream.Part(id="treble")
    bass = stream.Part(id="bass")
    treble.insert(0, clef.TrebleClef())
    bass.insert(0, clef.BassClef())
    treble.insert(0, meter.TimeSignature(f"{numerator}/{denominator}"))
    bass.insert(0, meter.TimeSignature(f"{numerator}/{denominator}"))
    if guessed_key is not None:
        treble.insert(0, m21key.Key(guessed_key.tonic.name, guessed_key.mode))
        bass.insert(0, m21key.Key(guessed_key.tonic.name, guessed_key.mode))
    if include_tempo_mark and result.bpm:
        treble.insert(0, tempo.MetronomeMark(number=result.bpm))
        bass.insert(0, tempo.MetronomeMark(number=result.bpm))

    by_hop = {}
    for note_event in result.notes:
        by_hop.setdefault(note_event.onset_hop, []).append(note_event)

    bpm_for_offsets = result.bpm if result.bpm else score_writer._FALLBACK_BPM_FOR_OFFSETS

    for onset_hop in sorted(by_hop):
        notes_here = by_hop[onset_hop]
        onset_time = onset_hop * result.hop_seconds
        offset_beats = onset_time * bpm_for_offsets / 60.0

        staff_groups = {"treble": [], "bass": []}
        for note_event in notes_here:
            staff_groups[score_writer._staff_for(note_event.pitch_class, note_event.octave)].append(
                note_event
            )

        for staff_name, group in staff_groups.items():
            if not group:
                continue
            part = treble if staff_name == "treble" else bass
            quarter_length = max(score_writer._duration_quarter_length(n, result) for n in group)

            if len(group) == 1:
                note_event = group[0]
                m21_note = music21.note.Note(
                    score_writer._pitch_for(note_event.pitch_class, note_event.octave)
                )
                m21_note.duration.quarterLength = quarter_length
                m21_note.style.color = score_writer._note_hex_color(note_event.pitch_class)
                part.insert(offset_beats, m21_note)
            else:
                pitches = [score_writer._pitch_for(n.pitch_class, n.octave) for n in group]
                m21_chord = chord.Chord(pitches)
                m21_chord.duration.quarterLength = quarter_length
                for chord_note, note_event in zip(m21_chord.notes, group):
                    chord_note.style.color = score_writer._note_hex_color(note_event.pitch_class)
                part.insert(offset_beats, m21_chord)

    # Same 32nd-note offset quantization write_score() applies, for the
    # same reason documented there: this sample's onset times (computed
    # from onset_hop * hop_seconds, then converted to beats) don't
    # generally land on a clean fraction of a beat, and MusicXML's writer
    # raises MusicXMLExportException("Cannot convert inexpressible
    # durations to MusicXML") on an unquantized offset that leaves a
    # non-power-of-two-length rest -- reproduced directly while building
    # this prototype (see docs/research/midi-export-feasibility.md).
    # Needed here because this Score is written to MusicXML *and* MIDI
    # from one object graph; a MIDI-only writer would not need this step
    # at all (confirmed separately, see the research doc's "MIDI doesn't
    # need offset quantization" finding) but keeping it keeps both output
    # files' note *positions* identical for a fair side-by-side.
    treble.quantize(quarterLengthDivisors=(8,), processOffsets=True, processDurations=False,
                     inPlace=True, recurse=True)
    bass.quantize(quarterLengthDivisors=(8,), processOffsets=True, processDurations=False,
                   inPlace=True, recurse=True)

    score = stream.Score()
    score.insert(0, treble)
    score.insert(0, bass)
    score.insert(0, layout.StaffGroup([treble, bass], symbol="brace"))
    return score


def write_midi(result, path, time_signature=config.DEFAULT_TIME_SIGNATURE, include_tempo_mark=True):
    """Prototype sibling to `score_writer.write_score()`: builds the same
    object graph via `build_score()` and writes it as a Standard MIDI File
    instead of MusicXML."""
    score = build_score(result, time_signature=time_signature, include_tempo_mark=include_tempo_mark)
    score.write("midi", fp=path)
