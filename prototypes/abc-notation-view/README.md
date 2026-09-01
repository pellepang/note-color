# ABC notation view -- prototype

Research demo for `docs/research/notation-and-feature-ideas.md`'s
**Concept A** ("ABC notation as the live data model, current staff as one
*renderer* of it") and its Recommendation section ("pursue A as an
*additional* export/import path first, not an immediate rewrite of
`TabDisplay`'s live rendering"). This prototype builds that export/import
path standalone, outside the real app, to find out whether it actually
works before scoping a real ticket.

Does **not** modify any file outside `prototypes/abc-notation-view/` --
it only *imports* (read-only) from the real app's modules
(`color_map.py`, `staff_map.py`, `duration_tracker.py`,
`batch_transcribe.py`) to reuse their tables/functions rather than
duplicating them.

## What it demonstrates

1. **A converter** (`abc_convert.py`): note events (pitch_class/octave/
   duration_class, in this app's own `NOTE_NAMES_FIFTHS` spelling and
   `duration_tracker._DURATION_CLASSES` duration vocabulary) <-> ABC
   notation text.
2. **A minimal terminal renderer** (`abc_terminal_preview.py`): raw-ANSI,
   no Rich/Textual, reusing `staff_map.py`'s row-placement math
   (`staff_row()`, `ledger_rows()`, `row_note_name()`) and
   `color_map.py`'s note coloring (`note_to_hsl(..., scheme="fifths")`) --
   but *not* `terminal_tab_display.py`'s combining-mark notehead glyphs or
   its `_display_width()`/`wcwidth` machinery, since every cell here is
   plain fixed-width ASCII/short text.
3. **A genuine edit round-trip** (`edit_demo.py`): write a short piece to
   a `.abc` file, edit it as *plain text* (`str.replace()`, no
   dict/index bookkeeping), re-parse, re-render.
4. **An end-to-end run** (`run_demo.py`) tying all three together against
   one multi-note synthesized melody spanning octaves 3-5 with six
   different duration classes, an accidental of each kind (Bb3 flat, F#4
   sharp), and a rest -- plus a cross-check that a real
   `batch_transcribe.NoteEvent` list converts to the identical note-event
   list the hand-built melody uses.

All four were actually run against this repo's real modules (not just
written) -- see "Confirmed working" below.

## How to run each piece

From the repo root, using the project's own venv:

```bash
# Everything, in order (converter -> validate -> preview -> edit round-trip):
.venv/bin/python prototypes/abc-notation-view/run_demo.py

# Just the edit round-trip demo on its own, with a custom edit:
.venv/bin/python prototypes/abc-notation-view/edit_demo.py --find "G/2 ^F" --replace "A/2 ^F"

# Pipe arbitrary ABC text into the terminal preview:
echo 'X:1
T:test
M:4/4
L:1/4
K:C
C D E F |' | .venv/bin/python prototypes/abc-notation-view/abc_terminal_preview.py
```

`run_demo.py` writes `output/melody.abc` and `output/melody_edited.abc`
(gitignored-worthy scratch output, same convention as the real app's
`note_history_*.txt` dumps -- not committed as fixtures).

## Confirmed working

Ran `run_demo.py` end to end against this repo's real venv (`music21`
10.5.0, already a `score_writer.py` dependency). All four steps completed
without error:

- **Step 1**: `note_events_to_abc()` produced this ABC text for the
  13-event sample melody (4 bars, 4/4, C major, spanning C3-C5):

  ```
  X:1
  T:ABC prototype demo
  M:4/4
  L:1/4
  K:C
  C D E/2 F/2 G | A3/2 G/2 ^F z | c2 _B, D, | E4 |
  ```

  `validate=True` (the default) round-tripped this straight back through
  `music21.converter.parse(..., format="abc")` and it parsed cleanly --
  correct pitches (including the flat-spelled `_B,` = Bb3 and
  sharp-spelled `^F` = F#4), correct durations (`E/2`=eighth,
  `A3/2`=dotted-quarter, `c2`=half, `E4`=whole -- all exactly matching
  `duration_tracker._DURATION_CLASSES`' beat values), correct octaves
  (ABC's own "bare uppercase = octave 4, bare lowercase = octave 5, `,`/`'`
  shift further" convention lines up with this app's own octave numbering
  with zero translation needed -- confirmed by direct round-trip, not
  assumed).
- **Step 2**: `from_note_events()` given the equivalent real
  `batch_transcribe.NoteEvent` list (`note_events.SAMPLE_NOTE_EVENTS`,
  built with real `duration_hops`/`hop_seconds`/`bpm` values) reproduced
  the *exact* `ProtoNote` list the hand-built melody uses --
  `expected == adapted` is `True`. This is the concrete evidence that this
  prototype's conversion path is the same arithmetic
  `score_writer.py`'s `_duration_quarter_length()` already uses on real
  batch-transcription output, not a lookalike.
- **Step 3**: the terminal preview correctly placed every note at its
  real grand-staff row (via the actual `staff_map.staff_row()`), colored
  each by its actual fifths hue, and rendered ledger lines/legend/duration
  suffixes/barlines/rest -- visually close to the mockup in
  `notation-and-feature-ideas.md`'s Concept A section.
- **Step 4**: `edit_demo.py` wrote `melody.abc`, replaced the substring
  `"G/2 ^F"` with `"A/2 ^F"` (turning bar 2's second note from G4 to A4,
  same eighth-note duration), wrote `melody_edited.abc`, and the
  re-rendered preview showed exactly that one note changed -- everything
  else on screen, pixel-for-pixel, identical.

## Friction / real surprises

- **music21 cannot write ABC.** This was the single biggest surprise.
  `music21.converter.subConverters.ConverterABC` registers zero
  `registerOutputExtensions` -- calling `stream.write("abc")` on any
  `music21` object raises `SubConverterException: This subConverter
  cannot show or write: no output extensions are registered for it`
  (reproduced directly against this venv). Reading ABC works great
  (`converter.parse(text, format="abc")`, used by `abc_to_note_events()`)
  -- writing does not exist at all. So "use music21 to do the actual ABC
  serialization... where practical" turned out to mean "for the read
  direction only": `note_events_to_abc()` hand-rolls the ABC body/header
  text itself (a genuinely small job -- pitch spelling +
  accidental-prefix + octave-marks + a `Fraction`-based length-ratio
  computation, about 40 lines total in `abc_convert.py`), and only calls
  back into `music21` afterward to *validate* what it wrote by parsing it
  right back. This actually strengthens Concept A's own "small,
  documented token vocabulary, easy to extend by hand" pitch from the
  research doc -- there's no possibility of relying on some `music21`
  writer's idiosyncrasies for the live app's own emitted text, because no
  such writer exists to depend on.
- **ABC has no structural check that a bar's tokens sum to the meter.**
  A duration-preserving edit (pitch-only, `"G/2 ^F"` -> `"A/2 ^F"`) round
  trips perfectly. But trying a duration-*changing* edit in the same spot
  (`"G/2 ^F"` -> `"A2 ^F"`, turning an eighth note into a half note without
  removing anything else from the bar) still parses without error -- ABC
  the *format* has no opinion about bar-duration validity -- but
  `music21`'s reader silently re-bars the now-5.5-beat measure, splitting
  the following `F#4` quarter note into two `F#4` eighth-note fragments
  across an inserted barline rather than rejecting the input or leaving it
  alone. Reproduced directly (`edit_demo.py --replace "A2 ^F"`): the
  edited body becomes `A3/2 A2 ^F z |` and the terminal preview shows
  `F#4·8` appearing *twice*, once on each side of a new mid-bar barline
  that wasn't there before. This is a real, concrete downside worth
  weighing (see below) -- plain-text editability doesn't come with any
  free correctness guarantee; a naive hand-edit that doesn't also rebalance
  the rest of its bar produces musically-nonsensical output rather than an
  error message a hobbyist could act on.
- **Octave/pitch-class numbering needed zero translation.** A pleasant
  surprise, not friction: `music21`'s `Pitch.pitchClass` (0=C..11=B) and
  its ABC octave convention (bare uppercase = octave 4) line up exactly
  with this app's own `pitch_class`/`octave` numbering. No offset-by-one
  or C4-vs-C5-as-"middle C" ambiguity to resolve, which is not guaranteed
  in general (different tools disagree about which octave counts as
  "middle C" -- e.g. some MIDI conventions call it octave 5) and was worth
  confirming empirically (see `abc_convert.py`'s `_abc_pitch()` docstring)
  rather than assuming.
- **Column-width simplicity was real, not just claimed.** Writing
  `abc_terminal_preview.py` took no `wcwidth`/`unicodedata.combining()`
  reasoning at all -- every cell is `str.center(COLUMN_WIDTH)` on plain
  ASCII/short text (`"C4·q"`, `"|"`, `"rest"`), because there are no
  Unicode combining marks in this renderer's vocabulary the way
  `terminal_tab_display.py`'s `STEM_GLYPH`/`FLAG_GLYPHS`/`DOT_GLYPH`
  duration-glyph composition has. Issue #82's whole barline-drift class of
  bug (a base+combining-mark cluster's *real* terminal-cursor-advance
  width disagreeing with `wcwidth.wcswidth()`'s cluster-forced-to-2
  heuristic) has no analog here, structurally, not by careful measurement
  -- there's nothing zero-advance to miscount.

## System architecture reasoning

### How this would eventually plug in

The research doc's Recommendation is explicit: keep
`terminal_tab_display.py`'s live combining-mark staff renderer exactly as
it is (it works, the user likes it, issue #82 already hardened it against
a real bug class) and add ABC as a **parallel export/import path**, not a
replacement. Concretely, against the real call sites:

- **Export from batch transcription** (`batch_transcribe.py` +
  `main.run_batch_transcribe()`, `main.py:1240`). `transcribe()` already
  returns a `TranscriptionResult` whose `.notes` field is exactly the
  `NoteEvent` list this prototype's `abc_convert.from_note_events()`
  already consumes (proven working, see Step 2 above) -- a real
  `--export-abc out.abc` CLI flag would be: call
  `from_note_events(result.notes, result.hop_seconds, result.bpm)`, feed
  the output through a beat-per-bar barline pass like this prototype's
  `with_barlines()` (main.py already computes bar placement for the `tab`
  dump via its own beat-accumulator around `main.py:811`'s `_hop_beats()`
  and `main.py:1319`'s `display.push_barline(t=onset_time)` -- the same
  beat math, just needing to also emit a `Bar()` token into the ABC event
  stream instead of only calling `TabDisplay.push_barline()`), then
  `note_events_to_abc()`. This is close to a genuinely small addition --
  `score_writer.py` already establishes the "one more batch-only output
  module, gated behind an explicit CLI flag" pattern this would follow
  exactly (an `abc_writer.py` sibling to `score_writer.py`, same "batch
  only, never imported by `main.py`'s live path" convention `librosa`/
  `music21` already both follow).
- **Export from a live session.** Requires Concept E (the JSONL session
  log) first, per the research doc's own sequencing -- `TabDisplay`
  doesn't currently retain enough structured history in a form this
  prototype's converter could consume directly (`self.session_history`'s
  `TabEntry`/`BarlineEntry` dicts are close, but keyed for rendering, not
  for ABC-oriented beat math). Out of this prototype's scope; the research
  doc already flags this as "adopt E first."
- **Import into `TabDisplay`'s rendering**, e.g. a hypothetical
  `virtualnote view song.abc` that plays an ABC file through the *real*
  scrolling staff. This is the harder direction and the one the research
  doc explicitly recommends *not* attempting yet: `abc_to_note_events()`
  gives a flat `ProtoNote`/`Bar`/rest list, and `TabDisplay.push()`/
  `push_notes()`/`push_barline()` (`terminal_tab_display.py:247-268`)
  already accept exactly that shape (pitch_class, octave, rgb, label,
  optional `t=` timestamp override -- the same `t=` override
  `main.run_batch_transcribe()` already uses for its own non-wall-clock
  batch pushes, per that module's docstring). So *technically* the glue
  code is small (walk the ABC event list, compute rgb/label the same way
  `main.py`'s `_tab_note_rgb()`/`_tab_note_label()` already do, call
  `push()`/`push_barline(t=...)` in a loop) -- the risk isn't the glue,
  it's that this reintroduces exactly the "does this drive
  `TabDisplay`'s live render path correctly" question issue #82 already
  had to fight through once. Doing this only after the export direction
  has lived for a while (as the research doc suggests) means the ABC
  token vocabulary and conversion logic are already tested against real
  transcription output before anything touches the live renderer.
- **Where `score_writer.py`'s existing pattern is the clearest precedent.**
  This prototype's `abc_convert.py` mirrors `score_writer.py` almost
  exactly in shape: both take a `TranscriptionResult`-shaped input, both
  reuse `duration_tracker.duration_class_for_beats()`/
  `_QUARTER_LENGTHS`-equivalent tables, both are the *only* module
  permitted to import their respective heavy dependency
  (`music21`/`music21`'s ABC half specifically), both are batch-only.
  A real `abc_writer.py` would essentially be `score_writer.py`'s
  MusicXML-writing half swapped for this prototype's hand-rolled ABC
  body/header serialization -- same `_staff_for()`-style bar/beat-offset
  math, same `guess_key_signature()` reuse for the `K:` header field
  (this prototype hardcodes `K:C`; a real version would call
  `score_writer.guess_key_signature(result.chroma_histogram)` and feed
  its `.tonic.name`/`.mode` into the `K:` line).

### What would change vs. stay the same

**Stays the same:** `terminal_tab_display.py`'s live render path,
combining-mark glyphs, `_display_width()`/`wcwidth` machinery, `TabEntry`/
`BarlineEntry` shapes, the three-thread live pipeline, every existing
test. Nothing here touches any of it.

**Would change (a real, scoped addition, not a rewrite):** a new
`abc_writer.py` (batch-only, `music21`-importing only for validation, same
isolation convention as `score_writer.py`/`batch_transcribe.py`/
`rhythm_reanalysis.py`), a `--export-abc` CLI flag on
`virtualnote transcribe` (`virtualnote.py`'s `build_parser()`, alongside
the existing `--dump-file`/`--time-signature`/(soon)`--write-score`
flags), and a small amount of beat-accumulator-to-`Bar()`-token glue in
`main.run_batch_transcribe()`, reusing math that already exists there for
`push_barline()` placement rather than inventing new bar-boundary logic.

### Effort/risk estimate

- **Export-only path (batch transcription -> `.abc` file):** small,
  roughly a day -- most of the hard parts (pitch spelling, octave
  numbering, duration-to-ABC-length math, validation-by-round-trip) are
  already built and confirmed working in this prototype; what's left is
  almost entirely wiring (`abc_writer.py`'s public API,
  `virtualnote.py`'s flag, `main.run_batch_transcribe()`'s barline-pass
  glue) plus tests mirroring `tests/test_score_writer.py`'s existing
  shape (if that file exists) or a fresh `tests/test_abc_writer.py`
  following this codebase's "synthesize the signal, no binary fixtures"
  convention.
- **Live-session export (needs Concept E first):** medium -- blocked on
  the JSONL session log existing at all; once it does, converting a JSONL
  log to ABC is a similarly small pass over already-structured data.
- **Import into the live `TabDisplay` renderer:** medium-to-large, and
  explicitly *not recommended yet* by the research doc -- the glue itself
  is small (see above), but it's new code driving the one render path
  this project has already had to debug a real, hard-won bug class in
  (issue #82). Worth doing only after the export direction has proven
  itself in real use.

### Is this genuinely "more true to real music notation" and "simpler to edit"?

**Truer to real notation: yes, concretely, in the specific sense the
research doc claims** -- the underlying representation (`.abc` text) is a
real, 30+-year-old standard other software already understands, not an
approximation invented for this app. That's not a vague claim here: this
prototype's output round-trips through `music21`'s own independent ABC
reader without modification, meaning any other ABC-consuming tool
(`abcm2ps`, `abcjs`, any of the folk-tune-archive tooling this format was
built for) could open the exact same file. The on-screen rendering
*quality* doesn't improve (both this prototype's preview and the real
`tab` view show the same grand-staff information) -- what changes is that
the data underneath now speaks a language other tools already understand,
exactly the research doc's own framing.

**Simpler to edit/tweak: yes, and now demonstrated rather than argued.**
The whole edit in `edit_demo.py` is "open a file, do a string replace,
save it" -- no walking `self._open_notes`/`self.session_history` to find
the right mutable dict, no timestamp-based disambiguation the way
`TabDisplay.correct_duration()` already has to do (see that method's own
docstring: it searches `session_history` and picks whichever repeated-key
occurrence's timestamp is *closest* to the target, since there's no
per-note id). An ABC-text edit needs none of that -- the token you want to
change is right there as literal, greppable text.

**But engaging honestly with the downsides:**

- **This is a real, if bounded, rewrite for the *live* path specifically**
  -- exactly as the research doc says. The export-only scope above avoids
  that cost; a live-ABC-token-driven `TabDisplay` would not.
- **ABC's duration vocabulary is a clean fit for this app's, but its bar
  model is stricter than this app's own beat-accumulator barline
  placement.** `duration_tracker._DURATION_CLASSES`' ten values (including
  dotted variants) map 1:1 onto ABC length ratios with no loss or
  approximation (confirmed: every one of the ten produces a clean ABC
  token, verified via `_abc_length()`'s `Fraction`-based computation over
  all ten in testing). But ABC (via `music21`'s reader, at least) expects
  a bar's contents to actually sum to the declared meter -- this app's own
  barline placement is explicitly approximate (`CLAUDE.md`: "barline
  placement is an approximation tied to that live tempo estimate, not
  exact bar-for-bar accuracy — expected drift, not a bug"), so a real
  batch export needs to either accept that an approximate/drifted barline
  can produce a musically-odd-looking bar in the exported ABC (harmless --
  it's still valid, parseable ABC, just a bar that doesn't look like a
  human transcriber would have barred it) or not bother with barlines at
  all in a first version and let the exported tune be one continuous
  unbarred phrase (also valid ABC -- barlines are not mandatory).
- **ABC has no representation for this app's chord-mode chord *names*** at
  all -- ABC has chord *symbols* as an annotation feature (`"C"` before a
  note, a guitar-chord-style overlay), which could carry
  `chord_templates.match()`'s jazz-symbol names (`Δ7`, `-7`, etc.) as an
  annotation layer, but ABC's actual *pitch* content for a chord is
  written as bracketed simultaneous notes (`[CEG]`), which
  `abc_to_note_events()` above already handles on the read side (picks the
  chord's top note as a simplification) but `note_events_to_abc()` does
  *not* yet emit on the write side -- this prototype is scoped to the
  monophonic case only, per the task's own instructions. A real export
  writer would need to add `[...]`-chord emission for `chord_mode`-on
  transcriptions; not hard, but not built here.
- **No dynamics/articulation/tie handling** -- this app doesn't track
  those anyway, so this is a non-issue for *this* app's data, but worth
  naming: ABC supports far more than this converter uses (grace notes,
  slurs, repeat signs, multi-voice) and none of that surface is
  exercised here.

**Net assessment, matching the research doc's own conclusion:** worth
doing as an additive export/import path, not worth it yet as a live
rendering rewrite. This prototype's main new contribution beyond the
research doc's analysis is the concrete finding that `music21` cannot
write ABC (only read it) -- which doesn't change the recommendation, but
does mean a real implementation's ABC *serialization* logic is squarely
this project's own code to own and test, not a thin wrapper delegating to
a library.
