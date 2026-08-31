# Finding: tab view's status/help_legend lines overflow on almost every
# real terminal, corrupting the whole staff via an unwanted scroll (fixed)

Caught while extending this tool to render additional scenes (chord mode,
different notehead styles) with more realistic status text than the first
finding's minimal repro used.

## Reproduction

Building a *realistic* status string via `main._status_text()` +
`main._legend_line()` — the exact functions `main.py`'s `run_terminal_tab()`
calls every frame — for perfectly ordinary conditions (not even an extreme
case): a plain note letter, a tempo estimate, a time signature, and the
default-on help-legend hints:

```python
status_len = len(main._status_text(...) + "  tempo=120  time=4/4  " + mode_hint + "  [onset] (Ctrl+C to quit)")
# -> 203 characters, not-frozen/no-scrollback/no-reanalysis "everyday" case
# -> 226 characters, frozen + scrollback active (an entirely ordinary,
#    documented workflow: freeze, then Left/Right through history)

help_legend_len = len(main._legend_line([...]))  # the real hint list run_terminal_tab() builds
# -> 145 characters, help-legend is on by default (`H` toggle)
```

`TabDisplay.render()` writes both directly with no width clipping:

```python
out.append(f"\033[{len(lines) + 1};1H\033[K{status}")
if help_legend:
    out.append(f"\033[{len(lines) + 2};1H\033[K{help_legend}")
```

Any terminal narrower than the status/help_legend text's own length (i.e.
almost every real terminal — 203-226 and 145 characters are both wider
than the vast majority of real terminal windows) hits the terminal's own
auto-wrap: the overflow spills onto an extra row neither `text_rows` nor
`usable_rows` accounted for. Since that extra row lands past the terminal's
last line, writing it triggers a real scroll-up of the *entire screen* —
confirmed via `pyte`: feeding the exact ANSI `render()` produces with a
realistic long status shows the whole staff, including the chord-mode
header row, shifted up by one row and the true top row lost off-screen,
reproducing exactly on every single frame in ordinary use (not an edge
case) on any terminal narrower than ~226 columns.

## Why this had gone unnoticed

Commit `87be8b3` ("terminal_display: fix status/legend line overflowing
past terminal height") fixed a *differently-shaped* bug in this same
family, but only in `terminal_display.py` (the `fill` view): a
`"\033[H"`-joined-into-`"\n".join()` off-by-one, not a "the text itself is
too long" issue. `terminal_tab_display.py` was never touched by that fix
and has no width clipping of its own — a distinct instance of the same bug
*class* (unaccounted-for extra row forcing a scroll), not a regression of
the already-fixed one.

## Fix applied

Added `_clip_to_width()` (wcwidth-aware, same clip-loop convention
`_pad_center()` already uses) and applied it to both `status` and
`help_legend` immediately before they're written, so an oversized string
is truncated to the real terminal width instead of being handed to the
terminal's own auto-wrap. Verified by reproducing the exact scenario above
before and after: before, the chord header row and every staff row after
it shifted up by one and the true top row was lost; after, every row lands
at its intended position with the status/help_legend lines silently
truncated to fit. `tests/test_terminal_tab_display.py`'s existing 44 tests
still pass unchanged.

## Residual, lower-priority risk (not fixed here)

`terminal_display.py` (`fill`) and `terminal_wheel_display.py` (`wheel`)
have the same "no width clipping on status/legend text" gap in principle,
but their status strings are much shorter (no mode/reanalysis/scrollback
hints) and this wasn't reproduced actually overflowing for either — noted
as a residual risk worth a quick look, not blind-fixed without evidence.

---

# Finding: the d7d2ea0 barline fix may not hold on every real terminal

Caught on this tool's first real run, by comparing `TabDisplay.render()`'s
actual ANSI output (fed through `pyte`) against what `terminal_tab_display.
py`'s own `_pad_center()` assumes.

## Reproduction

```
.venv/bin/python research/terminal-capture/capture.py
```

Pushes four synthetic notes with different duration classes (`whole`,
`quarter`, `eighth`, `dotted-sixteenth` — 0, 1, 2, and 3 combining marks
respectively) followed by a barline, then renders one frame. Checking the
barline glyph's (U+1D100) x-position per row in the resulting `pyte.Screen`
buffer:

```
row  8 (legend "A")  -> barline at x=76   <- A4, dotted-sixteenth (3 combining marks)
row  9 (legend "G")  -> barline at x=76   <- G4, quarter (1 combining mark)
row 12 (legend "D")  -> barline at x=76   <- D4, eighth (2 combining marks)
every other row       -> barline at x=77   <- includes C4 "whole" (0 combining marks)
```

Every row carrying a note with at least one duration-glyph combining mark
drifts the barline left by exactly 1 column, regardless of how many
combining marks that note has (1, 2, or 3 all drift by the same 1 column).
The `whole`-note row (no stem at all) does not drift.

## Root cause

`_pad_center()` measures cell text with `wcwidth.wcswidth()`, which has a
deliberate heuristic (see `wcwidth/_wcswidth.py`'s `_CATEGORY_MC_TABLE`
check) for General_Category "Mc" (spacing combining mark) codepoints:
when one follows a measured base character, it forces that grapheme
cluster's width to exactly 2, regardless of how many further Mc marks
follow. That's what `d7d2ea0` relies on: a notehead + any combination of
`STEM_GLYPH`/`FLAG_GLYPHS`/`DOT_GLYPH` (this module's duration glyphs, all
Mc-category, confirmed by `unicodedata.category()`) is padded assuming it
occupies exactly 2 real columns.

`pyte`'s own cursor-advance model (`Screen.draw()`) is different: it calls
single-character `wcwidth()` per codepoint, and merges any character where
`wcwidth() == 0` and `unicodedata.combining() != 0` into the *previous*
cell's data, advancing the cursor by 0 for it. All three duration-glyph
codepoints get `wcwidth() == 0` and a nonzero `unicodedata.combining()`
class (216 for stem/flags, 226 for the dot — confirmed directly). So pyte
treats a notehead + any number of these combining marks as consuming
exactly 1 column, not 2 — merging them all into the notehead's own cell.

Concretely, for a cell padded to `config.TAB_COLUMN_WIDTH` real columns per
`_pad_center()`'s math: 2 leading spaces + a 2-column-per-wcswidth
notehead+stem cluster + 2 trailing spaces `== 6` python characters, but
pyte only advances the cursor 2 (spaces) + 1 (notehead+merged stem) + 2
(spaces) `== 5` real columns for that same string — one short of the
column's declared width. Every column drawn after it on that row lands one
real terminal column to the left of where it lands on a row without such a
note.

## Why this matters

`pyte`'s combining-mark handling is not an implementation quirk — it's the
standard, spec-following behavior (`unicodedata.combining()`-driven
zero-advance for a combining character), the same model most real
terminal emulators are built on. `wcwidth.wcswidth()`'s Mc-table heuristic
exists specifically because Mc marks are *not* guaranteed zero-width by
Unicode's own definition (unlike Mn/Me marks) — but that heuristic is a
guess about how terminals render them, not a guarantee. It's entirely
possible that:

- Some real terminals actually follow wcswidth's assumption (genuinely
  render/advance by 2), in which case `d7d2ea0`'s fix is correct on those.
- Some real terminals follow pyte's standards-conformant model (advance
  by 1, i.e. zero-advance for the combining marks), in which case the
  barline still drifts by exactly 1 column on any row with a
  duration-glyph note — the same bug class as before the fix, just
  smaller (1 column instead of up to 3).
- Some terminals do something else entirely (docs/research/
  terminal-rendering-performance.md already found no terminal emulator
  passes every Unicode-width test category, and specifically flagged that
  several — Windows Terminal, cmd.exe, ConsoleZ — measure *this exact
  category* of combining mark as narrow (width 1) rather than zero-width,
  which would produce yet another drift amount).

This is the same fundamentally terminal-dependent residual risk that
research already flagged as unfixable purely app-side — but this tool
turned it from an abstract "no terminal is 100% compliant" caveat into a
concrete, reproducible, exact reproduction case worth a real multi-terminal
visual check.

## Suggested next step

Filed as issue #82 rather than fixed here — fixing it would mean picking
one cursor-advance model to target without knowing which one this
project's actual users' terminals follow, which needs a real
multi-emulator visual check first (exactly what docs/research/
terminal-rendering-performance.md's own next-steps list already
recommended, now with a concrete repro instead of a hypothetical one).

### Attempted: real-terminal cross-check (inconclusive, environment-limited)

Tried to get a second real data point beyond pyte's interpretation by
running `kitty` headlessly under `Xvfb` (both available in this dev
environment) and feeding it the identical ANSI payload `capture.py`
produces, then reading back kitty's own idea of the rendered grid. Two
approaches, both blocked by this specific sandbox, not by anything about
the actual question:

- `kitty @ get-text --extent screen` (kitty's remote-control text-dump
  command) turned out to be the wrong tool: it's built to extract
  "readable output" (e.g. for scripting copy-paste), not to dump an exact
  per-cell grid, and it silently joined all 30 rows into one line with a
  single trailing newline — no way to recover which row a given character
  belongs to, so no per-row barline-position comparison was possible from
  it.
- A literal pixel screenshot (`PIL.ImageGrab.grab()` against the Xvfb
  display) came back all-black at both default and forced
  `background_opacity=1.0` — ruled out alpha/compositing as the cause
  first. Most likely cause: kitty's GPU-accelerated rendering path
  doesn't get composited into Xvfb's visible framebuffer without a
  window manager/compositor present, a known class of issue for
  GL-accelerated terminal emulators under a bare Xvfb, unrelated to the
  width-model question itself.

Not chased further — burning more time on this specific sandbox's
GL/Xvfb interaction wasn't worth it once the actual question (does a
standards-conformant cursor model disagree with `wcwidth.wcswidth()`
here) was already answered cleanly via direct source inspection. A real
physical machine with an actual display (or a software-rendering-only
terminal like a plain TTY/`xterm`, if available) would sidestep this
entirely — worth trying first before another GPU-terminal-under-Xvfb
attempt.
