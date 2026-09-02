# Prototype: MIDI export feasibility

Throwaway prototype answering one question: is a `--export-midi` flag
(mirroring `--write-score`/`--export-abc`) cheap, given `score_writer.py`
already builds the right `music21.stream.Score` object graph for
MusicXML? Feature idea 2 in `docs/research/notation-and-feature-ideas.md`
("Export to ABC / MusicXML / standard MIDI file") — ABC and MusicXML
export both shipped; MIDI never got built.

## What this demonstrates

- `midi_writer.py`'s `build_score()` is a deliberate ~30-line duplication
  of `score_writer.write_score()`'s body — it calls that module's own
  private helpers (`_pitch_for`, `_staff_for`, `_duration_quarter_length`,
  `_note_hex_color`, `guess_key_signature`) directly rather than
  reimplementing any of their logic, so every pitch/staff/duration/color/
  key computation here is exactly `score_writer.py`'s own code. The
  duplication exists only because `write_score()` doesn't hand its
  `Score` object back to the caller before writing — see the module's
  own docstring for why a real implementation should instead refactor
  `score_writer.py` to share a `_build_score()` helper between
  `write_score()` and a new `write_midi()`, not ship this duplication.
- `run_demo.py` builds one `Score` from a synthesized `TranscriptionResult`
  (`sample_result.py`, same "synthesize the signal, no binary fixtures"
  convention this repo's tests already use), writes it to both
  `.musicxml` and `.mid`, then re-parses both back and reports concretely
  what survived: note/pitch content (identical), key/time signature
  (identical), tempo (survives via a `SET_TEMPO` meta-event), staff/part
  structure (identical), and **color** (survives in MusicXML, unconditionally
  dropped in MIDI — a real Standard MIDI File format limitation, not a
  `music21` writer gap; MIDI has no per-note color concept at all).

See `docs/research/midi-export-feasibility.md` for the full write-up and
recommendation.

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/midi-export/run_demo.py
```

Writes `prototypes/midi-export/output/demo.musicxml` and `demo.mid` (both
gitignored-by-convention run artifacts, not checked in) and prints a
side-by-side survival report to the terminal.
