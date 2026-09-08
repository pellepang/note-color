# Two-column legend split and every-row labeling (issue #36)

Live reaction to the shipped `feature/tab-sheet-notation` branch: two
concrete, fully-specified fixes to the legend, on top of everything else
on that branch (confirmed good otherwise).

**Fix 1: label every staff row, not just line rows.** `staff_map.
line_note_name()` — despite its name and docstring, which claimed the
input "must be in `STAFF_LINE_ROWS`" — was already pure `(row +
GRAND_STAFF_REF_STEP) % 7` diatonic-step math with no branch on line-vs-
space; it was already correct for every row, line or space, and simply
undocumented/underused as such. Renamed to `row_note_name()` and its
docstring corrected to state it's general over every row (line, space, or
ledger-line territory beyond the staff) — no logic change, since none was
needed. `terminal_tab_display.py`'s legend-building loop now calls it
unconditionally for every `screen_row` in the render loop's visible range
(which is always exactly the rows actually drawn, so no extra bounds
check is needed), instead of only inside a `screen_row in
STAFF_LINE_ROWS` branch. `tests/test_staff_map.py` gained direct coverage
of space rows (bass clef space mnemonic "All Cows Eat Grass", treble
"FACE"), ledger-line-territory rows (middle C, the range extremes), and a
cross-check against `staff_row()`/`diatonic_step()` for every natural
pitch class/octave in range.

**Fix 2: clef and letter in separate columns.** `config.py` splits the
old single `TAB_LEGEND_WIDTH` into `TAB_CLEF_WIDTH` (3) and
`TAB_LETTER_WIDTH` (2), with `TAB_LEGEND_WIDTH` now derived as their sum
— so the total width the `L` toggle reserves from/returns to the note
columns is unchanged, only how that width is split internally.
`terminal_tab_display.py`'s per-row legend cell is now built as two
concatenated sub-cells: a `TAB_CLEF_WIDTH`-wide clef cell (blank except
on `BASS_CLEF_ROW`/`TREBLE_CLEF_ROW`) followed by a `TAB_LETTER_WIDTH`-
wide letter cell (always populated, per fix 1). The `L` keybind still
toggles the whole `legend_width` region as one unit (`legend_width =
config.TAB_LEGEND_WIDTH if legend_on else 0`, unchanged) — not
fragmented into two independently-toggleable halves, per the ticket's
explicit instruction.

**G-clef clipping, revisited.** #36 asked to retry the G-clef (𝄞)
bottom-clipping investigation now that the clef has genuine dedicated
column space rather than shared cells, in case that happened to help.
It doesn't, and per #20's original investigation (above) there was never
reason to expect it would: the clipping is a *vertical* cell-height
problem (the glyph's covering font, `NotoMusic-Regular.ttf`, draws it
using that font's entire descent allocation, and no ANSI-level control
exists over a fallback glyph's vertical placement inside a terminal's
cell grid) — giving the clef more *horizontal* room via its own column
doesn't touch that axis at all. Not re-verified pixel-for-pixel against a
real terminal/font stack as part of this fix (this environment's smoke
test only inspects the raw ANSI text stream, which can't show font
rendering) — left documented as a known, terminal-dependent limitation in
`CLAUDE.md`, per #36's own instruction to note it again rather than block
on it or re-run the full #20 investigation from scratch.
