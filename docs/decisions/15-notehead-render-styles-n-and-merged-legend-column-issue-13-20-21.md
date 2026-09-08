# Notehead render styles (`N`) and merged legend column (issue #13/#20/#21)

`terminal_tab_display.py`'s `TabDisplay.render()` now takes `notehead_style`
("symbol" or "name") and `legend_on` (bool), both owned as render-thread-
local state in `main.py`'s `run_terminal_tab` (same pattern as `chord_mode`
for `P` — no shared state, no `TabDisplay`-side toggle method). `TabEntry`
still carries each note's raw `pitch_class`/`octave` (not just a
precomputed label), so `render()` recomputes on-screen text fresh every
frame via `_cell_text()` — a live `N` press restyles columns already on
screen, not just future ones. `dump_ansi()` is untouched: it reads the
`label` field of `TabEntry.notes` directly (letter+octave), which
`render()` no longer uses at all.

**G-clef bottom-clipping investigation (issue #20, fix 1).** #19's
prototype (`prototype/clef-and-legend-toggle`) theorized the clipping was
a horizontal-overhang problem — an astral-plane glyph rendering wider than
Python's `.center()` assumes — and tried isolating the clef in its own
buffered, bold column. #20 reported that didn't fix it, and asked for
real investigation into whether it's actually a font-coverage or
cell-height problem instead. Checked with Pillow (`ImageFont.getbbox()`)
against the fonts this machine's fontconfig resolves for these codepoints
(`NotoSansSymbols2-Regular.ttf` has no real glyphs for any of them — its
`getbbox()` returns the same box as an uncovered `.notdef`; the actual
covering font is `NotoMusic-Regular.ttf`). Measured ink bounding boxes at
a 1000-unit em, font's own ascent/descent alongside:

| glyph | above baseline | below baseline | font's own descent |
|---|---|---|---|
| G-clef `\U0001D11E` | 1334 | **398** | 398 |
| F-clef `\U0001D122` | 900 | 0 | 398 |
| notehead `\U0001D157` | 269 | 1 | 398 |

The G-clef's design uses its font's *entire* descent allocation (398 of
398) — no other glyph checked comes close. That's real evidence for the
cell-height theory #20 asked about, not the horizontal one #19 assumed:
when a terminal falls back to a rarely-used symbol font for one glyph but
sizes/positions it inside the *primary* (monospace) font's cell box, any
glyph whose design needs more descent than that primary font's own
metrics allocate gets its bottom cut off by the cell's fixed pixel
height — independent of any horizontal buffering, and it explains, glyph
by glyph, exactly which one clips (only the G-clef needs its font's full
descent; the F-clef needs none at all, matching that only the G-clef was
ever reported clipped). No ANSI escape sequence gives an application
control over a fallback glyph's vertical placement/scale inside a
terminal's cell grid, so this isn't fixable from the app layer the way
#19's horizontal buffer was attempted — it's a terminal/font-stack
property. Shipped mitigation: kept the real glyph (the user preferred it live over
#19's variant C plain-text fallback), rendered plain rather than carrying
over the bold styling #19's variant B prototype added — bold synthesis
for a glyph the primary font doesn't cover is another plausible source of
extra vertical overhang, so not adding it is a legitimate, low-risk
choice, not a proven fix. Documenting
this as a known, terminal-dependent limitation rather than a fully
resolved bug — same treatment as the other environment-dependent
limitations already listed in this section — since there's no further
lever available inside the app to pull.

**Legend merge (issue #20, fix 2) — superseded by issue #36.** The
dedicated-clef-column prototype (variant B, two side-by-side regions: a
clef-only sub-column plus a letter-only sub-column, mostly empty on any
given row) was *not* carried into the shipped code at this point. The
single shared `legend_width`-wide region already in
`terminal_tab_display.py` before this change — clef glyph on its anchor
row, letter name on every other staff-line row, blank otherwise —
already was the "merge into one column" outcome #20 asked for; it needed
no restructuring, just the octave-digit drop (`staff_map.
line_note_name()`) and the `L` toggle wired through. **This call was
reversed by #36** (below) after a live user reaction to the shipped
branch said the merged layout wasn't actually what was wanted — variant
B's two-column split is now what's shipped.
