# Research: `tab` view barline "not straight" complaint

## Question

A user reports the `tab` terminal view's barlines (vertical divider
columns marking estimated bar boundaries in the scrolling note-history
display) "are not straight." Is this (a) a pure tempo/accuracy problem
(the live BPM estimate feeding the beat-accumulator is wrong/unstable),
(b) a genuine rendering/alignment defect independent of tempo accuracy,
(c) both, or (d) inconclusive from static code reading alone?

Findings below are cited to the actual source as of this writing
(`main.py`, `terminal_tab_display.py`, `duration_tracker.py`, `config.py`,
`tests/test_terminal_tab_display.py`), read directly for this diagnosis.

## Verdict: (c) both — but the dominant, fully-verified defect is a
beat-accumulator double-counting bug, not a glyph/ANSI rendering glitch

The strongest, concretely-confirmed finding is a genuine logic bug in how
`main.py`'s `run_terminal_tab()` accumulates beats toward the next
barline — independent of whether the live tempo estimate is accurate at
all. A perfect BPM estimate would not fix it. Alongside that, there is a
second, plausible but *unconfirmed* rendering risk in the barline glyph's
column-width budget, which would need a live-terminal repro to settle.

## 1. Primary finding: beats are double-counted every hop a note finalizes

`run_terminal_tab()`'s beat-accumulator (`main.py:723-757`) runs this
logic every hop a new item arrives (`got_new`):

```python
# main.py:726-732 (mono contribution)
if duration_hops is not None and prev_pitch_class is not None:
    beats = (duration_hops * hop_seconds * bpm_estimate / 60.0) if bpm_estimate else None
    dclass = duration_class_for_beats(beats)
    display.finalize_duration(prev_pitch_class, prev_octave, dclass)
    beats_accumulated += (
        duration_hops * hop_seconds * bpm_estimate / 60.0 if bpm_estimate else 0.0
    )

# main.py:738-748 (chord/note_stack contribution — unconditional, not
# gated on chord_mode, the display toggle)
for entry in note_stack:
    if entry["duration_hops"] is None:
        continue
    beats = (
        entry["duration_hops"] * hop_seconds * bpm_estimate / 60.0
    ) if bpm_estimate else None
    dclass = duration_class_for_beats(beats)
    display.finalize_duration(entry["pitch_class"], entry["octave"], dclass)
    beats_accumulated += (
        entry["duration_hops"] * hop_seconds * bpm_estimate / 60.0 if bpm_estimate else 0.0
    )

# main.py:754-757
while beats_accumulated >= beats_per_bar:
    display.push_barline()
    beats_accumulated -= beats_per_bar
```

Both blocks write into the **same** `beats_accumulated` variable, and
both run **unconditionally** every hop, regardless of the view's current
`chord_mode` display toggle (`P` key). This matches the project's
documented "always-on pipeline" convention — the chord/multipitch/duration
pipeline runs every hop whether or not any view has `P` toggled on
(CLAUDE.md's Architecture section; `main.py`'s own comment at
`main.py:734-737`, "Chord-mode duration tracking runs every hop
regardless of the current chord_mode display toggle").

The consequence: `mono_duration_tracker` (fed from `NoteSmoother`, keyed
by `(pitch_class, octave)`, constructed at `main.py:247`) and
`chord_duration_tracker` (fed from `multipitch.detect()`'s `raw_stack` via
`chord_smoother`, constructed at `main.py:248`) are **two fully
independent `DurationTracker` instances** —
`duration_tracker.py:46-129`'s `DurationTracker.update()` confirms each
owns its own `self.states` dict (`duration_tracker.py:66`) with no shared
state or dedup mechanism between them. Because `multipitch.detect()` runs
every hop regardless of display mode (same always-on convention), playing
an ordinary single monophonic note causes it to plausibly be picked up
*both* by `NoteSmoother`'s mono path *and* as a one-note entry in
multipitch's `raw_stack` — i.e. the same physical acoustic event gets
tracked, and independently finalized, by two separate trackers. When both
finalize (at their own, not-necessarily-synchronized moments), **both**
contributions get summed into `beats_accumulated`, effectively crediting
that one note's duration roughly twice toward the next bar boundary.

The net effect is barlines placed roughly twice as often as the true
time signature/tempo would dictate — landing in visibly/musically wrong,
uneven positions relative to the actual notes on screen. This is a
distinct defect from tempo-estimate inaccuracy: it would persist and
produce wrong-looking barlines even under a perfectly accurate BPM
estimate, because the bug is in how many beats get counted per note
event, not in the beats-per-minute conversion factor itself.

### Contrast with the batch path (proof this is a fixable asymmetry, not
an inherent requirement)

`run_batch_transcribe()` (`main.py:1033-1062`, the offline
`virtualnote transcribe` path) does **not** have this bug. It groups
`result.notes` — already a single, unified polyphonic list, not
separately-tracked mono-vs-chord streams — `by_hop` (`main.py:1029-1030`),
and for every group of simultaneous notes sharing one onset hop, takes
```python
column_beats = max(column_beats, note_beats or 0.0)   # main.py:1054
```
i.e. the *longest* of the simultaneous notes' durations, not a sum across
tracker "modes." This confirms the double-counting in the live path is a
real, avoidable asymmetry against the already-correct pattern this same
codebase uses elsewhere — not something inherent to tracking mono and
chord data in parallel.

## 2. Secondary finding: barline column has zero width margin for its glyph (plausible, unconfirmed without a live-terminal repro)

`config.py:253` sets:

```python
TAB_BARLINE_WIDTH = 1  # terminal characters per barline column -- narrower than a note
                        # column (TAB_COLUMN_WIDTH), so it reads as a divider, not data
```

compared to note columns' `TAB_COLUMN_WIDTH = 3` (`config.py:135`) or
`TAB_COLUMN_WIDTH_CHORD = 9` (`config.py:136`). `terminal_tab_display.py`'s
`_barline_cell()` (`terminal_tab_display.py:434-440`) renders:

```python
def _barline_cell(rgb, width):
    r, g, b = rgb
    text = BARLINE_GLYPH.center(width)
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"
```

with `BARLINE_GLYPH = "\U0001D100"` (MUSICAL SYMBOL SINGLE BARLINE,
`terminal_tab_display.py:108`) and `width = config.TAB_BARLINE_WIDTH = 1`.
This is a Supplementary Multilingual Plane (astral) Unicode codepoint from
the same Musical Symbols block as `NOTEHEAD_GLYPH` (U+1D157) and the
duration stem/flag/dot glyphs, for which this same module's docstring
already documents an accepted risk: "the East_Asian_Width=Ambiguous risk
#14 flagged for both is expected/accepted, not a reason to avoid them"
(`terminal_tab_display.py:103-105`, referring to the accidental markers).
Note columns absorb that ambiguous-width risk with real margin (3 or 9
characters of budget); the barline column has **none** — if a user's
terminal/font renders U+1D100 as display-width 2 (plausible; real
terminal wcwidth/font-fallback behavior for rare astral codepoints varies
and is not guaranteed to match the Unicode-spec "Neutral" classification
in every terminal emulator), the barline would silently overflow its
1-column budget and shift every character to its right by 1 terminal
column.

Because the barline spans every visible staff row uniformly — the
per-row loop in `render()` unconditionally emits `_barline_cell()` for
every `screen_row` whenever `col.kind == "barline"`, with no per-row
branching (`terminal_tab_display.py:376-382`) — this particular overflow,
if it occurs, would be internally consistent top-to-bottom within that
one column (i.e. it would not itself look "jagged" row-by-row); rather it
would shift that whole barline, and every column scrolled to its right,
out of alignment with the note columns and legend to its left. Depending
on how many barlines are on screen and how terminal-width accounting
compounds, this could plausibly read as "not straight" in the sense of
inconsistent spacing/alignment between bars and the surrounding note
columns.

This cannot be confirmed or ruled out from static code reading — it
depends entirely on the affected user's actual terminal emulator and font
stack. The existing test coverage does not (and structurally cannot)
catch this: `tests/test_terminal_tab_display.py`'s
`test_push_barline_renders_glyph_spanning_staff_at_barline_width`
(`tests/test_terminal_tab_display.py:252-263`) asserts only
`len(cell) == config.TAB_BARLINE_WIDTH` — a Python string length
(codepoint count) check, not a real terminal display-width measurement.
**A live-terminal repro is needed**: reproduce in the user's actual
terminal/font combination and check whether the barline column visibly
overflows into neighboring columns, the same way this project's own
`docs/DECISIONS.md` already investigated (and confirmed as a real,
terminal/font-stack-dependent, app-unfixable property) for the treble
clef glyph's clipped-descent issue.

## 3. Rendering code checked and ruled out as sources of misalignment

- Column-width accounting is otherwise self-consistent: every column
  (note or barline) contributes exactly its own `col.width` characters to
  every row it appears in (`terminal_tab_display.py:376-389`), matching
  what the header row (`terminal_tab_display.py:344-351`) and the
  visible-width budget walk (`terminal_tab_display.py:266-286`) both
  assume.
- The beat-accumulator's bar-crossing loop itself has no off-by-one:
  `while beats_accumulated >= beats_per_bar: push_barline();
  beats_accumulated -= beats_per_bar` (`main.py:754-757`) is a `while`,
  not an `if`, specifically so a hop that crosses more than one bar
  boundary at once doesn't lose barlines, and it subtracts the exact
  `beats_per_bar` remainder rather than zeroing — no drift introduced by
  this loop itself.
- The legend column's width (`TAB_CLEF_WIDTH + TAB_LETTER_WIDTH`) is
  constant per row (`terminal_tab_display.py:367-374`) — not a source of
  per-row variation.
- `_note_cell()` (`terminal_tab_display.py:426-431`) truncates its text to
  `width` before centering (`(label or "")[:width].center(width)`),
  whereas `_barline_cell()` does not truncate, only centers — an
  asymmetry, but not itself exploitable into a bug, since barline text is
  always exactly one codepoint.

## 4. Tempo-estimate accuracy: a separate, smaller, already-acknowledged contributor

Independent of finding #1's double-counting, the beats-per-note
conversion (`duration_hops * hop_seconds * bpm_estimate / 60.0`,
`main.py:727` and `:731`, `:742` and `:747`) uses whatever `bpm_estimate`
(`tempo_tracker.TempoTracker`'s live, causal estimate) happens to be *at
the moment a note finalizes* — not a value integrated over that note's
actual sounding span. An unstable tempo estimate therefore directly
distorts the number of "beats" credited per note, on top of and
independent of the double-counting bug above.

`batch_transcribe.py`'s offline path uses `librosa.beat.beat_track()`
instead, producing a single stable whole-file `bpm` value
(`result.bpm`, consumed in `run_batch_transcribe()`). This is a
fundamentally different, one-shot value, not something that could simply
be "fed into" the live per-hop accumulator after the fact for
already-rendered columns — the live view has already committed to
placing barlines using its own running estimate as each column was
pushed. A more accurate live tempo estimate would reduce, but not
eliminate, this specific source of drift; this matches the project's own
already-documented, accepted limitation (CLAUDE.md: "Barline placement...
is an approximation ... expected drift, not a bug"). It would **not**,
however, fix finding #1's double-counting, which is a distinct logic bug
that persists even under a perfect tempo estimate.

## Summary for a fix

- **Finding #1 (double-counting) is fully verified from the code, not
  speculative** — it should be treated as a real, fixable bug: the fix
  needs to make the beat-accumulator credit each acoustic note event's
  duration exactly once, e.g. by accumulating beats from only the path
  that corresponds to the currently-relevant note identity (mirroring
  `run_batch_transcribe()`'s `max()`-over-simultaneous-notes-per-onset
  pattern instead of summing across `mono_duration_tracker` and
  `chord_duration_tracker` unconditionally).
- **Finding #2 (barline glyph width margin) is a real, open risk but
  unconfirmed** — recommend a live-terminal repro across a couple of
  terminal emulators/fonts before deciding whether `TAB_BARLINE_WIDTH`
  needs headroom (e.g. bumping to 2) or whether `_barline_cell()` needs
  to defensively truncate/pad the same way `_note_cell()` already does.
- **Tempo-estimate accuracy** is a real but smaller, already-documented
  contributor to overall drift — not the primary explanation for "not
  straight," and not something a better estimate alone would fully
  resolve given finding #1.
