"""Scrolling note-history view for the terminal: notes enter on the right
and scroll left over time, each placed at its correct vertical position on
a grand staff (bass + treble, see staff_map.py) and colored per the active
color scheme. Two callers decide *when* a new column is pushed (see
main.py's run_terminal_tab: 'fix' on a timer, 'onset' on a new note-attack)
-- this module only owns layout/rendering and the session history used for
the on-quit ANSI dump.

Two live-togglable notehead render styles (issue #21's finalized decision,
`N` keybind in main.py): *symbol* (an open notehead glyph, U+1D157, plus a
real Unicode flat/sharp marker if needed -- no letter or octave text at
all) and *name* (bare letter + ASCII accidental, e.g. "A", "Bb", "F#" --
no octave digit, since the staff row already conveys octave). Both use
`color_map.NOTE_NAMES_FIFTHS` for spelling, matching every other flat-
biased label in this app. `TabEntry.notes` still stores each note's
pitch_class/octave (for `_cell_text()` to render fresh against whichever
style is currently selected -- so toggling `N` live restyles history
that's still on screen, not just future pushes) alongside its precomputed
letter+octave `label`, which exists purely for `dump_ansi()`'s on-quit
text dump (unaffected by the `N`/`L` toggles, per the map's standing
decision) and is never used by `render()`.

Two further live-togglable behaviors (issues #22/#23, `main.py`'s render-
thread-local state, no `TabDisplay`-side toggle methods -- same pattern as
`notehead_style`/`legend_on`): per-column age-based dimming, and a `Space`
freeze-frame. Dimming (#22): the newest visible column renders at
`config.TAB_NOTE_LIGHTNESS` (tab's normal, always-used note lightness);
every older column's lightness fades linearly down to `config.
DIM_LIGHTNESS` (the same floor `terminal_wheel_display.py` uses for its
own inactive wedges -- promoted to `config.py` so the two can't drift)
over `config.FADE_COLUMNS` columns of age, held at that floor beyond it.
Age is each column's distance from the newest *visible* column (0 =
newest), recomputed fresh every `render()` call from `pitch_class` (see
`_column_note_rgb()`) rather than baked in at push time -- a column's age
changes every frame as newer columns scroll in, so its color can't be
fixed when pushed. `TabEntry.notes`' precomputed `rgb` field is therefore
only ever read by `dump_ansi()` now, same as `label`. Freeze-frame (#23,
`render(..., frozen=True)`): forces every visible column's age to 0,
overriding the fade entirely, while `main.py` stops pushing new columns --
`render()` itself doesn't know or care that no new columns are arriving,
it just keeps redrawing the same history with age pinned at 0.
"""

import shutil
import sys
import time
from collections import deque, namedtuple

import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
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

NOTEHEAD_GLYPH = "\U0001D157"  # MUSICAL SYMBOL VOID NOTEHEAD -- #15's winner
# Real Unicode accidental signs for *symbol* style, keyed by the ASCII
# suffix NOTE_NAMES_FIFTHS already uses -- #21's finalized decision (an
# earlier ASCII "b"/"#" stand-in was replaced with these after live
# reaction; the East_Asian_Width=Ambiguous risk #14 flagged for both is
# expected/accepted, not a reason to avoid them).
SYMBOL_ACCIDENTALS = {"b": "♭", "#": "♯"}


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

    def render(self, status, chord_mode=False, notehead_style="symbol", legend_on=True, frozen=False):
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
        # `L` (legend_on) reclaims the legend column's width for note
        # columns entirely when off, rather than just blanking its
        # content -- issue #19's stated intent for the toggle.
        legend_width = config.TAB_LEGEND_WIDTH if legend_on else 0
        visible_cols = max((cols - legend_width) // width, 1)
        visible_entries = list(self.entries)[-visible_cols:]
        pad = visible_cols - len(visible_entries)

        columns = [({}, frozenset(), None)] * pad
        last_index = len(visible_entries) - 1
        for index, e in enumerate(visible_entries):
            # Age is distance from the newest *visible* column (0 = newest);
            # #23's freeze-frame pins every column's age to 0, overriding
            # #22's fade entirely, without this loop needing to know why.
            age = 0 if frozen else last_index - index
            row_map = {}
            ledgers = set()
            for pitch_class, octave, _rgb, _label in e.notes:
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
                # Store the raw pitch_class, not the precomputed `label`
                # (that's dump_ansi()'s letter+octave text, untouched by
                # notehead_style) -- _cell_text() below renders fresh
                # against whichever style is current, so a live `N` toggle
                # restyles already-on-screen columns too. Color is also
                # recomputed fresh here (_column_note_rgb), not the
                # precomputed `_rgb` this note tuple carries -- that
                # precomputed value is always TAB_NOTE_LIGHTNESS and is
                # only ever read by dump_ansi(); a column's age (and thus
                # its dimming) changes every render as newer columns scroll
                # in, so it can't be baked in at push time.
                row_map[row] = (_column_note_rgb(pitch_class, age), pitch_class)
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
            if not legend_on:
                legend = ""
            elif screen_row == BASS_CLEF_ROW:
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
                    rgb, pitch_class = row_map[screen_row]
                    cells.append(_note_cell(rgb, _cell_text(pitch_class, notehead_style), width))
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


def _aged_lightness(age):
    """Issue #22's dimming curve: linear fade from `config.TAB_NOTE_LIGHTNESS`
    at `age` 0 (the newest visible column) down to `config.DIM_LIGHTNESS`
    by `config.FADE_COLUMNS` columns of age, held at that floor beyond it.
    Freeze-frame (#23) passes `age=0` for every visible column, which this
    formula already resolves to full `TAB_NOTE_LIGHTNESS` -- no separate
    freeze branch needed here, the caller just lies about age."""
    if config.FADE_COLUMNS <= 1:
        return config.DIM_LIGHTNESS
    fraction = min(age / (config.FADE_COLUMNS - 1), 1.0)
    return config.TAB_NOTE_LIGHTNESS - (config.TAB_NOTE_LIGHTNESS - config.DIM_LIGHTNESS) * fraction


def _column_note_rgb(pitch_class, age):
    """A note cell's on-screen color, recomputed fresh every render: the
    same fixed fifths hue/saturation `main.py`'s `_tab_note_rgb()` computes
    when a note is first pushed, but with lightness taken from
    `_aged_lightness()` instead of the fixed `config.TAB_NOTE_LIGHTNESS`
    that precomputed value always uses. `note_to_hsl(..., scheme="fifths")`
    is used (not `--color-scheme`) for the same reason `_tab_note_rgb()`
    hardcodes it: `tab` reads as the same color as `wheel`, independent of
    the active color scheme."""
    hue, sat, _light = note_to_hsl(pitch_class, config.MAX_OCTAVE, scheme="fifths")
    return hsl_to_rgb255(hue, sat, _aged_lightness(age))


def _accidental_suffix(pitch_class):
    """'b'/'#' suffix from NOTE_NAMES_FIFTHS, or '' for a natural note."""
    name = NOTE_NAMES_FIFTHS[pitch_class]
    return name[1:] if len(name) > 1 else ""


def _cell_text(pitch_class, style):
    """The notehead's on-screen text for the given render style -- see the
    module docstring for what each style shows. `style` is whatever the
    caller's live `N` toggle currently selects; unrecognized values fall
    back to *name* rather than raising, so a stale/typo'd style string
    degrades gracefully instead of crashing the render loop."""
    if style == "symbol":
        marker = SYMBOL_ACCIDENTALS.get(_accidental_suffix(pitch_class), "")
        return NOTEHEAD_GLYPH + marker
    return NOTE_NAMES_FIFTHS[pitch_class]
