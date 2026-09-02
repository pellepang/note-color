# MIDI export feasibility (feature idea 2, standard MIDI file)

Research task: is a `--export-midi` flag (mirroring `--write-score`/
`--export-abc`) cheap to add, given `music21` (already a `score_writer.py`
dependency) has a built-in MIDI writer? Prototype and evidence:
`prototypes/midi-export/`.

## Method

`prototypes/midi-export/midi_writer.py`'s `build_score()` reuses
`score_writer.py`'s own private helpers to build the identical
`music21.stream.Score` object graph `write_score()` builds internally,
without writing it to disk — so the same in-memory `Score` can be handed
to more than one `music21` writer for a direct, apples-to-apples
comparison. `run_demo.py` builds one `Score` from a synthesized
`TranscriptionResult`, writes it to both `.musicxml` and `.mid`, and
re-parses both back with `music21.converter.parse()` to inspect what
actually survived.

## Findings

- **`Score.write("midi", fp=path)` succeeds with no exception** against
  this app's real two-staff/chord/color object graph — not just a toy
  example. This is not a hidden/unverified claim; it was run and the
  output file inspected directly.
- **Pitch content survives exactly**: MusicXML and MIDI round-trips
  produced identical sorted pitch-class lists (7 note/chord elements each).
- **Time signature and key signature survive exactly** (verified via
  re-parse: `(4, 4)` on both, key signature `0` sharps/flats on both for
  this sample).
- **Tempo survives**, but only because this prototype adds one line
  `write_score()` itself doesn't have: `include_tempo_mark=True` inserts
  a `music21.tempo.MetronomeMark(number=result.bpm)` into both parts.
  Confirmed via raw MIDI event inspection: a real `SET_TEMPO` meta-event
  at 454545 usec/quarter (= 132.0bpm, matching the sample's input bpm)
  landed in the output file. **Adjacent, pre-existing gap found while
  building this**: `write_score()` today uses `bpm` only to compute each
  note's beat *offset* — it never inserts a tempo indication into the
  MusicXML output either, so an exported MusicXML file also silently
  defaults to 120bpm on playback in any program that opens it. Not a
  MIDI-specific problem; worth its own small fix to `write_score()`
  regardless of whether MIDI export ships.
- **Staff/part structure survives**: 2 parts in, 2 tracks with note
  content out (plus one tempo/meta track — 3 tracks total, as expected).
- **Color does NOT survive** — `Note.style.color` reads back as `None`
  on every note after the MIDI round-trip, while the same notes' colors
  survive perfectly through MusicXML. This is confirmed as a real,
  unavoidable Standard MIDI File format limitation, not a `music21`
  writer gap — the SMF spec has no per-note color meta-event or
  controller of any kind. Nothing implementable would fix this; it's a
  property of the target format itself.

## What this means for a real `--export-midi` flag

**Cheap, with one real design question to resolve first, not an
engineering blocker**: the object-graph reuse works cleanly. The one
real decision is architectural, not technical — `score_writer.py`'s
`write_score()` builds its `Score` and writes it in one function with no
seam to reuse the object graph for a second writer. A real implementation
should refactor `write_score()` into a `_build_score()` helper (returning
the `Score`, not writing it) plus two thin callers — `write_score()`
(MusicXML) and a new `write_midi()` — rather than duplicate the ~30-line
build loop the way this prototype does for comparison purposes only. That
refactor is small and low-risk (`write_score()`'s existing test suite
would need no behavior changes, just a seam extracted).

**Color loss needs an explicit product decision, not a workaround**:
since this app's whole MusicXML-format rationale (per issue #26/#30's
research) was specifically color's presence, a MIDI export inherently
ships a lesser artifact on that one dimension. This should be surfaced to
the user clearly (e.g. a one-line CLI warning on `--export-midi`, or
just documented prominently) rather than silently produced — this is a
UX/expectations call, not a technical one, and needs the repo owner's
sign-off same as every other feature's scope.

**Recommended invocation shape** (not decided, just a natural default
given precedent): `virtualnote transcribe song.wav --export-midi
[PATH]`, same bare-flag/explicit-path/omit convention `--write-score`/
`--export-abc` already use.

## What's NOT decided here

Whether to actually build this, the exact CLI flag name, whether the
tempo-mark gap above gets fixed as part of this work or filed separately,
and whether `--export-midi` also makes sense on `virtualnote replay`
(mirroring how `--write-score` landed on both `transcribe` and `replay`)
— all real scope questions for the repo owner, same as every other
feature in this project's history (`--write-score` went through
#26→#30→#60→#65 before a line of implementation code existed). This
document and its prototype exist to make that future decision
well-informed, not to make it.

## Sources

- `prototypes/midi-export/` (this task's own prototype, run directly —
  see its README for how to reproduce).
- `score_writer.py` (read in full; `build_score()` reuses its private
  helpers verbatim).
- `music21`'s installed `midi` module (its MIDI writer, exercised
  directly via `Score.write("midi", ...)`, not read line-by-line the way
  `swiftf0-source-verification.md` read `swift_f0/core.py` — this task's
  scope was behavioral verification via round-trip testing, which is
  sufficient to answer the feasibility question asked).
