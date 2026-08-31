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

Filed as issue (see repo issue tracker) rather than fixed here — fixing
it would mean picking one cursor-advance model to target without knowing
which one this project's actual users' terminals follow, which needs a
real multi-emulator visual check first (exactly what docs/research/
terminal-rendering-performance.md's own next-steps list already
recommended, now with a concrete repro instead of a hypothetical one).
