"""Minimal raw-ANSI terminal preview for ABC text (prototype, research demo
for docs/research/notation-and-feature-ideas.md's Concept A). Reuses this
project's own `staff_map.py` row-placement math (`staff_row()`,
`ledger_rows()`, `row_note_name()`) and `color_map.py` note coloring
(`note_to_hsl(..., scheme="fifths")`, matching `tab`'s own fixed-fifths-hue
convention -- see `terminal_tab_display._column_note_rgb()`) -- but
deliberately skips `terminal_tab_display.py`'s combining-mark notehead
glyphs (STEM_GLYPH/FLAG_GLYPHS/DOT_GLYPH) and its `_display_width()`/
`wcwidth` machinery entirely: every cell here is plain ASCII/short text of
a known fixed width, one `str.center()` away from correct, no terminal-
cursor-advance reverse-engineering needed (issue #82's whole bug class,
per CLAUDE.md, existed only because of that combining-mark approach). This
is the concrete "simpler to render" claim demonstrated, not just argued.

Not a live scrolling view (no `render()` loop, no queues) -- this is a
one-shot print of a whole finished ABC string, appropriate for a prototype
proving the *representation*, not the live rendering architecture.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from staff_map import ledger_rows, row_note_name, staff_row, STAFF_LINE_ROWS

from abc_convert import Bar, abc_to_note_events

COLUMN_WIDTH = 6
LEGEND_WIDTH = 4


def _swatch(rgb, text):
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    fg = (20, 20, 20) if lum > 140 else (230, 230, 230)
    return f"\033[48;2;{r};{g};{b}m\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{text}\033[0m"


def render_preview(abc_text, out=None):
    """Print a simplified grand-staff preview of `abc_text` (one column
    per note/rest/barline token) plus a plain-text note-name/duration
    legend row underneath -- close to the mockup in
    docs/research/notation-and-feature-ideas.md's Concept A section."""
    out = out if out is not None else sys.stdout
    events = abc_to_note_events(abc_text)

    note_rows = [
        staff_row(e.pitch_class, e.octave) for e in events if e is not None and not isinstance(e, Bar)
    ]
    if not note_rows:
        print("(no notes to preview)", file=out)
        return
    top = max(note_rows) + 1
    bottom = min(note_rows) - 1

    for row in range(top, bottom - 1, -1):
        legend = row_note_name(row).rjust(LEGEND_WIDTH - 1) + " "
        cells = []
        for e in events:
            if isinstance(e, Bar):
                cells.append("|".center(COLUMN_WIDTH))
                continue
            if e is None:
                cells.append((" " * COLUMN_WIDTH))
                continue
            note_row = staff_row(e.pitch_class, e.octave)
            if note_row == row:
                hue, sat, _light = note_to_hsl(e.pitch_class, config.MAX_OCTAVE, scheme="fifths")
                rgb = hsl_to_rgb255(hue, sat, config.TAB_NOTE_LIGHTNESS)
                cells.append(_swatch(rgb, "*".center(COLUMN_WIDTH)))
            elif row in ledger_rows(note_row):
                cells.append("-".center(COLUMN_WIDTH))
            elif row in STAFF_LINE_ROWS:
                cells.append("-" * COLUMN_WIDTH)
            else:
                cells.append(" " * COLUMN_WIDTH)
        print(legend + "".join(cells), file=out)

    label_cells = []
    for e in events:
        if isinstance(e, Bar):
            label_cells.append("|".center(COLUMN_WIDTH))
        elif e is None:
            label_cells.append("rest".center(COLUMN_WIDTH))
        else:
            label = f"{NOTE_NAMES_FIFTHS[e.pitch_class]}{e.octave}·{_short_dur(e.duration_class)}"
            label_cells.append(label.center(COLUMN_WIDTH))
    print(" " * LEGEND_WIDTH + "".join(label_cells), file=out)

    print(file=out)
    print(f"abc-so-far: {_first_line(abc_text)}", file=out)


_SHORT_DUR = {
    "whole": "w", "dotted-half": "h.", "half": "h", "dotted-quarter": "q.",
    "quarter": "q", "dotted-eighth": "8.", "eighth": "8", "dotted-sixteenth": "16.",
    "sixteenth": "16", "thirtysecond": "32",
}


def _short_dur(duration_class):
    return _SHORT_DUR.get(duration_class, "?")


def _first_line(abc_text):
    body_lines = [ln for ln in abc_text.splitlines() if ln and not ln[1:2] == ":"]
    return body_lines[0] if body_lines else abc_text.splitlines()[-1]


if __name__ == "__main__":
    text = sys.stdin.read() if not sys.stdin.isatty() else None
    if text is None:
        print("usage: pipe ABC text on stdin, e.g.: python edit_demo.py | python abc_terminal_preview.py")
        sys.exit(1)
    render_preview(text)
