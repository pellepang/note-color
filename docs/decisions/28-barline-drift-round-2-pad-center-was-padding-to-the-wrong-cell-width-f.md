# Barline drift, round 2: `_pad_center()` was padding to the wrong cell width for duration-glyph notes (issue #82)

`d7d2ea0`'s fix (`_pad_center()` measuring cell text with `wcwidth.
wcswidth()` instead of naive code-point counting) was believed to fully
close the barline-column-misalignment bug class. `research/terminal-
capture/`'s new `pyte`+Pillow capture tool (built for unrelated visual
bug-catching, see its FINDINGS.md) immediately found a real residual case
by comparing `TabDisplay.render()`'s actual ANSI output, fed through
`pyte`, against what `_pad_center()` assumed: every screen row carrying a
note with at least one duration-glyph combining mark (`STEM_GLYPH`/
`FLAG_GLYPHS`/`DOT_GLYPH`, all Unicode General_Category "Mc") drifted a
following barline column 1 real column left, regardless of whether the
note had 1, 2, or 3 such marks — only the `whole`-note row (no marks at
all) landed correctly.

**Root cause:** `wcwidth.wcswidth()` has a deliberate heuristic that
forces a base+Mc-mark grapheme cluster to measure exactly 2 columns
regardless of how many further Mc marks follow — `d7d2ea0` built
`_pad_center()`'s padding math on that number. But `pyte`'s own
cursor-advance model (`Screen.draw()`) — the standards-conformant one,
per `unicodedata.combining()`-driven zero-advance — treats any codepoint
where `wcwidth(ch) == 0` and `unicodedata.combining(ch) != 0` as truly
consuming 0 columns, merging it into the previous cell. All three
duration-glyph codepoints qualify (confirmed directly: `wcwidth() == 0`,
`combining()` class 216 for stem/flags, 226 for the dot). So a real
terminal advances its cursor only 1 column for a notehead + any number of
these marks, not the 2 `_pad_center()` assumed — a real, reproducible
1-column desync on exactly the rows carrying duration-glyph notes. Prior
research (`docs/research/terminal-rendering-performance.md`, its own
`ucs-detect`-survey citations) had already flagged, in the abstract, that
several real terminals (Windows Terminal, cmd.exe, ConsoleZ, ...) measure
this exact Mc category as narrow (width 1) rather than zero-width — a
third possible drift amount, and independent evidence that `wcswidth()`'s
cluster-forced-to-2 answer is not a safe default to build on for this
codepoint class.

**Fix:** `_pad_center()`/`_clip_to_width()` now measure with a new
`_display_width()` (`terminal_tab_display.py`) — a per-codepoint sum using
the same zero-advance rule `pyte`'s `Screen.draw()` applies (`wcwidth(ch)
== 0 and unicodedata.combining(ch)` → contributes 0), rather than
`wcwidth.wcswidth()`'s grapheme-cluster heuristic. A notehead + any
number of duration-glyph marks now measures as 1 column, matching what
`pyte` (and, per the `ucs-detect` survey, most real terminal emulators)
actually advance the cursor by. Verified via `research/terminal-capture/
capture.py`'s exact repro: before the fix, the barline glyph landed at
x=76 on rows with 1-3 combining marks and x=77 on the `whole`-note row;
after, every row (0/1/2/3 marks) lands at the same x=98 in a fresh
100x30-terminal render, fed through `pyte`. `tests/
test_terminal_tab_display.py` gained two direct regression tests
(`_display_width()` returning the same value for a bare notehead as for
notehead+1/2/3 combining marks; `_pad_center()`'s padded output measuring
the same total width across all four real duration classes) plus updated
its existing barline-alignment/width tests to measure via the new
`_display_width()` instead of `wcwidth.wcswidth()` (which would now
disagree with the app's own corrected padding by construction, since
that's exactly the heuristic being moved away from). Full suite: 372
passed.

**Caveat, stated explicitly per this repo's own track record on this
exact question:** this fix is validated against `pyte`'s standards-
conformant model and the prior `ucs-detect` research survey, not a live
sweep of real terminal emulators — no physical/GUI terminal was available
in this environment (a `kitty`-under-`Xvfb` attempt in the prior research
round was inconclusive for unrelated environment reasons, see FINDINGS.md).
The zero-advance model chosen here matches `pyte` and is the
standards-conformant default most terminals build on, but the `ucs-detect`
survey already found a real minority (Windows Terminal, cmd.exe, ConsoleZ,
ExtraTermQt, zoc) that measure this exact Mc category as width-1 instead
of width-0 — on those, this fix would leave a smaller residual drift than
before (1 column instead of up to 3, since multiple marks no longer
compound), not necessarily zero. Same posture as issue #69's history:
treat this as provisionally fixed and evidence-based, not field-confirmed,
until a live multi-terminal visual check happens.
