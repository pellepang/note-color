"""Scrolling note-history view for the terminal: notes enter on the right
and scroll left over time, each placed at its correct vertical position on
a grand staff (bass + treble, see staff_map.py) and colored per the active
color scheme. Two callers decide *when* a new column is pushed (see
main.py's run_terminal_tab: 'fix' on a timer, 'onset' on a new note-attack)
-- this module only owns layout/rendering and the session history used for
the on-quit ANSI dump.
"""

import shutil
import sys
import time
from collections import deque, namedtuple

import config
from staff_map import staff_row, ledger_rows, STAFF_LINE_ROWS, TOP_ROW, BOTTOM_ROW

TabEntry = namedtuple("TabEntry", "pitch_class octave rgb label t")

LEDGER_CHAR = "─"


class TabDisplay:
    def __init__(self, fps=20):
        self.fps = fps
        self.entries = deque(maxlen=config.TAB_VISIBLE_MAXLEN)
        self.session_history = []
        self._t0 = time.monotonic()
        self._last_size = None
        sys.stdout.write("\033[?25l\033[2J")
        sys.stdout.flush()

    def push(self, pitch_class, octave, rgb, label):
        entry = TabEntry(pitch_class, octave, rgb, label, time.monotonic() - self._t0)
        self.entries.append(entry)
        if len(self.session_history) < config.TAB_SESSION_HISTORY_MAX:
            self.session_history.append(entry)

    def render(self, status):
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size

        # Only the current frame's row/column count is redrawn each time;
        # on a resize (frequent in a tiling WM) that region shrinks or the
        # note columns re-align, so the previous frame's content outside
        # it would otherwise never get overwritten and linger as ghosts.
        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

        usable_rows = max(rows - 1, 1)  # reserve the last row for status text

        top, bottom = TOP_ROW, BOTTOM_ROW
        shrink = (top - bottom + 1) - usable_rows
        while shrink > 0 and (top > 20 or bottom < 0):
            if bottom < 0:
                bottom += 1
                shrink -= 1
            if shrink > 0 and top > 20:
                top -= 1
                shrink -= 1

        width = config.TAB_COLUMN_WIDTH
        visible_cols = max(cols // width, 1)
        visible_entries = list(self.entries)[-visible_cols:]
        pad = visible_cols - len(visible_entries)

        columns = [(None, frozenset(), None, None)] * pad
        for e in visible_entries:
            # Notes still outside [bottom, top] after the shrink above (only
            # possible once the staff has hit its 21-row floor, on a terminal
            # too short even for that) are dropped rather than clamped onto
            # the boundary row -- clamping would silently draw the note at
            # the wrong staff position instead of just not showing it.
            if e.pitch_class is None or not (bottom <= staff_row(e.pitch_class, e.octave) <= top):
                columns.append((None, frozenset(), None, None))
                continue
            row = staff_row(e.pitch_class, e.octave)
            ledgers = frozenset(r for r in ledger_rows(row) if bottom <= r <= top)
            columns.append((row, ledgers, e.rgb, e.label))

        # The staff itself is never shrunk below 21 rows (top=20..bottom=0),
        # even on a terminal shorter than that -- cap what we actually emit
        # to usable_rows so cursor addressing never targets a row beyond the
        # real terminal height, which would scroll/corrupt the fixed-position
        # rendering instead of just cropping the staff.
        lines = []
        for screen_row in range(top, bottom - 1, -1):
            if len(lines) >= usable_rows:
                break
            cells = []
            for note_row, ledgers, rgb, label in columns:
                if note_row == screen_row and rgb is not None:
                    cells.append(_note_cell(rgb, label, width))
                elif screen_row in ledgers or screen_row in STAFF_LINE_ROWS:
                    cells.append(LEDGER_CHAR * width)
                else:
                    cells.append(" " * width)
            lines.append("".join(cells))

        out = []
        for i, line in enumerate(lines, start=1):
            out.append(f"\033[{i};1H\033[K{line}")
        out.append(f"\033[{len(lines) + 1};1H\033[K{status}")
        sys.stdout.write(clear + "".join(out))
        sys.stdout.flush()

    def dump_ansi(self, path):
        lines = []
        for i, e in enumerate(self.session_history):
            if e.pitch_class is None:
                lines.append(f"{e.t:8.2f}s  {i:5d}  --")
                continue
            r, g, b = e.rgb
            swatch = f"\033[48;2;{r};{g};{b}m  \033[0m"
            lines.append(f"{e.t:8.2f}s  {i:5d}  {swatch}  {e.label}")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


def _note_cell(rgb, label, width):
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    fg = (20, 20, 20) if lum > 140 else (230, 230, 230)
    text = (label or "")[:width].center(width)
    return f"\033[48;2;{r};{g};{b}m\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{text}\033[0m"
