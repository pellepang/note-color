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
from staff_map import (
    staff_row, ledger_rows, line_note_name, STAFF_LINE_ROWS, TOP_ROW, BOTTOM_ROW,
    BASS_CLEF_ROW, TREBLE_CLEF_ROW,
)

TabEntry = namedtuple("TabEntry", "notes chord_name t")
# `notes` is a list of (pitch_class, octave, rgb, label) tuples -- one
# entry in monophonic mode, up to CHORD_MAX_NOTES in chord mode.

LEDGER_CHAR = "─"
BASS_CLEF_GLYPH = "𝄢"
TREBLE_CLEF_GLYPH = "𝄞"


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
        """Monophonic mode: push one note as a new scrolling column."""
        self._push_entry([(pitch_class, octave, rgb, label)], chord_name=None)

    def push_notes(self, notes, chord_name):
        """Chord mode: push up to CHORD_MAX_NOTES (pitch_class, octave,
        rgb, label) tuples as one stacked scrolling column, plus the
        recognized chord name shown in the header row above it."""
        self._push_entry(list(notes), chord_name)

    def _push_entry(self, notes, chord_name):
        entry = TabEntry(notes, chord_name, time.monotonic() - self._t0)
        self.entries.append(entry)
        if len(self.session_history) < config.TAB_SESSION_HISTORY_MAX:
            self.session_history.append(entry)

    def render(self, status, chord_mode=False):
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size

        # Only the current frame's row/column count is redrawn each time;
        # on a resize (frequent in a tiling WM) that region shrinks or the
        # note columns re-align, so the previous frame's content outside
        # it would otherwise never get overwritten and linger as ghosts.
        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

        header_rows = 1 if chord_mode else 0
        usable_rows = max(rows - 1 - header_rows, 1)  # reserve the last row for status text

        top, bottom = TOP_ROW, BOTTOM_ROW
        shrink = (top - bottom + 1) - usable_rows
        while shrink > 0 and (top > 20 or bottom < 0):
            if bottom < 0:
                bottom += 1
                shrink -= 1
            if shrink > 0 and top > 20:
                top -= 1
                shrink -= 1

        width = config.TAB_COLUMN_WIDTH_CHORD if chord_mode else config.TAB_COLUMN_WIDTH
        legend_width = config.TAB_LEGEND_WIDTH
        visible_cols = max((cols - legend_width) // width, 1)
        visible_entries = list(self.entries)[-visible_cols:]
        pad = visible_cols - len(visible_entries)

        columns = [({}, frozenset(), None)] * pad
        for e in visible_entries:
            row_map = {}
            ledgers = set()
            for pitch_class, octave, rgb, label in e.notes:
                # Notes still outside [bottom, top] after the shrink above
                # (only possible once the staff has hit its 21-row floor,
                # on a terminal too short even for that) are dropped rather
                # than clamped onto the boundary row -- clamping would
                # silently draw the note at the wrong staff position
                # instead of just not showing it.
                if pitch_class is None:
                    continue
                row = staff_row(pitch_class, octave)
                if not (bottom <= row <= top):
                    continue
                row_map[row] = (rgb, label)
                ledgers.update(r for r in ledger_rows(row) if bottom <= r <= top)
            columns.append((row_map, frozenset(ledgers), e.chord_name))

        # The staff itself is never shrunk below 21 rows (top=20..bottom=0),
        # even on a terminal shorter than that -- cap what we actually emit
        # to usable_rows so cursor addressing never targets a row beyond the
        # real terminal height, which would scroll/corrupt the fixed-position
        # rendering instead of just cropping the staff.
        lines = []
        if chord_mode:
            header_cells = [" " * legend_width]
            for _row_map, _ledgers, chord_name in columns:
                header_cells.append((chord_name or "")[:width].ljust(width))
            lines.append("".join(header_cells))

        for screen_row in range(top, bottom - 1, -1):
            if len(lines) >= usable_rows + header_rows:
                break
            if screen_row == BASS_CLEF_ROW:
                legend = BASS_CLEF_GLYPH.center(legend_width)
            elif screen_row == TREBLE_CLEF_ROW:
                legend = TREBLE_CLEF_GLYPH.center(legend_width)
            elif screen_row in STAFF_LINE_ROWS:
                legend = line_note_name(screen_row).center(legend_width)
            else:
                legend = " " * legend_width
            cells = [legend]
            for row_map, ledgers, _chord_name in columns:
                if screen_row in row_map:
                    rgb, label = row_map[screen_row]
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
            sounding = [n for n in e.notes if n[0] is not None]
            if not sounding:
                lines.append(f"{e.t:8.2f}s  {i:5d}  --")
                continue
            note_strs = []
            for _pitch_class, _octave, rgb, label in sounding:
                r, g, b = rgb
                swatch = f"\033[48;2;{r};{g};{b}m  \033[0m"
                note_strs.append(f"{swatch}  {label}")
            chord_part = f"  [{e.chord_name}]" if e.chord_name else ""
            lines.append(f"{e.t:8.2f}s  {i:5d}  " + "  ".join(note_strs) + chord_part)
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
