"""Score editor (issue #98, terminal UI layer): the main editor view --
`score_editor_state.EditorScore` rendered as a fixed, cursor-addressable
grand staff, extending `terminal_tab_display.py`'s rendering approach
(same notehead glyph, accidental markers, `staff_map.py` row math,
fifths-order coloring) into a *loaded-once, random-access, editable*
buffer instead of a live-scrolling, append-only one -- see
`CONTEXT.md`'s Score editor glossary section for the "Column (editor
sense)" distinction this module's `EditorColumn` addressing follows.

Cursor = `(column_index, staff_row)`, addressed in the same staff-row
space `staff_map.py` already defines (`staff_row()`/`row_note_name()`/
`ledger_rows()`) -- reused rather than inventing a second numbering
scheme, same as `terminal_tab_display.py` already does. `pitch_at_row()`
below is this module's one genuinely new piece of staff-row math: the
*inverse* of `staff_map.staff_row()` (a staff row -> the natural note
that sits there), needed because the editor's cursor can sit on any row,
occupied or not, and `note_toggle`/`transpose_*` need to go from "the row
the cursor is on" back to a real pitch.

Every function up to `render()` is pure (an `EditorColumn`/`EditorScore`
plus plain ints in, a mutated column or a plain value out) and unit-
tested (tests/test_score_editor_display.py); `render()`'s actual screen
layout (column widths, viewport scrolling, cursor highlight) is smoke-
tested manually only, same convention as every other run_terminal_*
loop's own render method in this codebase.
"""

import shutil
import sys
import unicodedata

import chord_templates
import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from duration_tracker import DEFAULT_DURATION_CLASS, DURATION_CLASS_ORDER
from score_editor_state import EditorColumn, EditorNote
from staff_map import (
    BASS_CLEF_ROW, BOTTOM_ROW, GRAND_STAFF_REF_STEP, LETTER_INDEX, STAFF_LINE_ROWS,
    TOP_ROW, TREBLE_CLEF_ROW, key_signature_accidental, ledger_rows, row_note_name, staff_row,
)

NOTEHEAD_GLYPH = "\U0001D157"  # MUSICAL SYMBOL VOID NOTEHEAD -- same as terminal_tab_display.py
SYMBOL_ACCIDENTALS = {"b": "♭", "#": "♯"}
LEDGER_CHAR = "─"
BASS_CLEF_GLYPH = "𝄢"
TREBLE_CLEF_GLYPH = "𝄞"
CURSOR_MARKER = "+"  # freeview-style placeholder for an empty cursor cell (mirrors the prototype)

# Duration suffix for zoom level 3 ("duration") -- same short-text
# convention terminal_tab_display.py's *name* notehead style uses for its
# own duration suffixes, duplicated here (rather than imported) since
# that dict is module-private there and this module's cell text always
# includes the octave digit too, a different composed shape.
_DURATION_SUFFIXES = {
    "whole": "whole",
    "dotted-half": "half.",
    "half": "half",
    "dotted-quarter": "4th.",
    "quarter": "4th",
    "dotted-eighth": "8th.",
    "eighth": "8th",
    "dotted-sixteenth": "16th.",
    "sixteenth": "16th",
    "thirtysecond": "32nd",
}

# Render-only zoom/column-width levels (z / zoom_cycle) -- no EditorScore
# mutation, mirrors tab's N/L toggles being pure render-thread-local
# state. Each level adds more text to a note's cell; the column width
# grows to fit. Not decided by #98's spec ("implementer's call") --
# these four are the natural "how much do I want to see" progression:
# bare identity, then letter, then octave, then full duration.
ZOOM_LEVELS = [
    ("notehead", 3),
    ("letter", 4),
    ("octave", 5),
    ("duration", 9),
]

_NATURAL_PC_BY_LETTER_IDX = {letter_idx: pc for pc, letter_idx in LETTER_INDEX.items()}


def pitch_at_row(row, key_fifths=0):
    """Inverse of `staff_map.staff_row()`: the note that sits at a given
    staff row, by default the *natural* (no accidental) -- `note_toggle`
    places whatever this returns, matching `staff_map`'s own "an
    accidental shares its natural neighbor's row" convention (there's no
    separate row to place a sharp/flat on directly; Shift+Up/Down are how
    a placed note becomes a *different* accidental afterward).

    `key_fifths` (issue #98 follow-up, direct user feedback after
    hands-on use) makes the *default* spelling key-aware instead of
    always-natural: in G major (key_fifths=1), the F-row's default is
    F-sharp, not F natural -- `staff_map.key_signature_accidental()`
    looks up whether this row's letter is sharped/flatted in the active
    key, and the semitone shift is folded into the same
    `octave*12+pitch_class` arithmetic `transpose_note_at_cursor()` uses,
    so it wraps octaves correctly at the rare edge case of a sharped B or
    flatted C. Only affects the *default* a fresh placement gets --
    Shift+Up/Down still freely retunes any placed note afterward."""
    step = row + GRAND_STAFF_REF_STEP
    letter_idx = step % 7
    octave = step // 7
    natural_pc = _NATURAL_PC_BY_LETTER_IDX[letter_idx]
    delta = {"sharp": 1, "flat": -1, "natural": 0}[key_signature_accidental(key_fifths, letter_idx)]
    combined = octave * 12 + natural_pc + delta
    return combined % 12, combined // 12


def clamp_row(row):
    return max(BOTTOM_ROW, min(TOP_ROW, row))


def clamp_column(index, num_columns):
    return max(0, min(num_columns - 1, index))


def note_index_at_row(column, row):
    """Index into `column.notes` of whichever note (if any) sits at
    `row`, or None."""
    for i, note in enumerate(column.notes):
        if staff_row(note.pitch_class, note.octave) == row:
            return i
    return None


def toggle_note_at_cursor(column, row, key_fifths=0):
    """`note_toggle` (Space): places a note at `row` if nothing's there
    (spelled per the active key signature -- see `pitch_at_row()`),
    removes the one that is there otherwise -- including a column's very
    last note, emptying it to a Rest. Space used to refuse that last-note
    case (forcing a separate `clear_to_rest` press), but direct user
    feedback after hands-on use called the two-step flow unwanted
    friction: the same key that places a note should be able to remove
    it all the way down to zero, with no special case (see
    docs/DECISIONS.md). `clear_to_rest` still has its own independent
    value for a multi-note chord column -- clearing everything in one
    press instead of one note at a time -- so it stays. Returns True if
    the column was actually mutated (always True here; the return value
    is kept for symmetry with this module's other mutation functions,
    some of which do refuse)."""
    idx = note_index_at_row(column, row)
    if idx is not None:
        column.notes.pop(idx)
        return True
    pitch_class, octave = pitch_at_row(row, key_fifths)
    column.notes.append(EditorNote(pitch_class=pitch_class, octave=octave))
    column.notes.sort(key=lambda n: n.octave * 12 + n.pitch_class)
    return True


def transpose_note_at_cursor(column, row, direction):
    """`transpose_up`/`transpose_down`: shifts the note at `row` a
    semitone (direction=+1/-1). A no-op (returns None) if there's no note
    at `row`. Returns the note's new staff row on success, so the caller
    can move the cursor to follow the note it just transposed."""
    idx = note_index_at_row(column, row)
    if idx is None:
        return None
    note = column.notes[idx]
    combined = note.octave * 12 + note.pitch_class + direction
    new_pitch_class, new_octave = combined % 12, combined // 12
    column.notes[idx] = EditorNote(pitch_class=new_pitch_class, octave=new_octave)
    return staff_row(new_pitch_class, new_octave)


def cycle_duration(column, delta):
    """`duration_shorten`/`duration_lengthen`: steps `column.duration_class`
    through `DURATION_CLASS_ORDER` (longest-to-shortest) by `delta` --
    positive shortens, negative lengthens. Clamped at either end rather
    than wrapping, same "a bounded real-world quantity doesn't wrap"
    convention `settings_display.parse_numeric_input()` already follows
    -- a whole note doesn't become a thirtysecond by lengthening past the
    top."""
    try:
        idx = DURATION_CLASS_ORDER.index(column.duration_class)
    except ValueError:
        idx = DURATION_CLASS_ORDER.index(DEFAULT_DURATION_CLASS)
    idx = max(0, min(len(DURATION_CLASS_ORDER) - 1, idx + delta))
    column.duration_class = DURATION_CLASS_ORDER[idx]


def clear_to_rest(column):
    """`clear_to_rest` (r): empties the column's notes outright -- the
    one dedicated action that may produce a Rest (see CONTEXT.md's
    glossary entry)."""
    column.notes = []


def insert_column_at(score, index):
    """`insert_column` (i): inserts a new empty (Rest) column before
    `index` -- the caller (main.run_score_editor) moves the cursor onto
    the new column, per #98's spec's chosen "cursor follows" convention."""
    score.columns.insert(index, EditorColumn(notes=[], duration_class=DEFAULT_DURATION_CLASS))


def delete_column_at(score, index):
    """`delete_column` (x): deletes `score.columns[index]`; refuses
    (returns False, no mutation) if it's the only remaining column -- an
    editor can never have zero columns, the same invariant
    `new_blank_score()` establishes. Returns True on success."""
    if len(score.columns) <= 1:
        return False
    del score.columns[index]
    return True


def cycle_zoom(zoom_level):
    """`zoom_cycle` (z): wraps through ZOOM_LEVELS -- render-only, no
    EditorScore mutation."""
    return (zoom_level + 1) % len(ZOOM_LEVELS)


def visible_column_range(cursor_col, num_columns, visible_count):
    """The [start, end) window of column indices `render()` should draw,
    keeping `cursor_col` inside it and never reaching outside
    [0, num_columns) -- centers the window on the cursor where there's
    room to, but clamps at either edge of the score instead of leaving
    dead space past the first/last column."""
    if visible_count >= num_columns:
        return 0, num_columns
    start = cursor_col - visible_count // 2
    start = max(0, min(start, num_columns - visible_count))
    return start, start + visible_count


def chord_name_for_column(column):
    """Lead-sheet chord name for `chords_only_toggle` (c) -- per #98's
    spec, recognized "using chroma.fold() + chord_templates.match()
    against each column's notes." There's no live audio spectrum to run
    chroma.fold() against here (a loaded score's columns are exact
    pitch-class sets, not detected-from-audio energy), so this builds the
    equivalent synthetic chroma vector directly -- a 1.0 at each pitch
    class actually present in the column, 0 elsewhere -- and feeds that
    straight to chord_templates.match(), the same recognizer chord mode
    uses live. Bass chroma is approximated the same way, gated on the
    column's lowest note actually sitting below chroma.
    DEFAULT_BASS_CUTOFF_HZ (~250Hz, roughly B3) rather than a real
    measured ratio -- see docs/DECISIONS.md for why this deviates from a
    literal chroma.fold() call. Blank (None) for fewer than 2 distinct
    pitch classes or no confident match, same "no guess" convention chord
    mode already follows for live audio."""
    pitch_classes = sorted({note.pitch_class for note in column.notes})
    if len(pitch_classes) < 2:
        return None
    chroma_vector = [1.0 if pc in pitch_classes else 0.0 for pc in range(12)]
    lowest = min(column.notes, key=lambda n: n.octave * 12 + n.pitch_class)
    bass_vector = None
    if lowest.octave <= 3:  # approximates a note below ~250Hz -- see docstring
        bass_vector = [1.0 if pc == lowest.pitch_class else 0.0 for pc in range(12)]
    match = chord_templates.match(chroma_vector, bass_chroma=bass_vector, lowest_pc=lowest.pitch_class)
    return match.name if match else None


def _editor_note_rgb(pitch_class):
    """Same fixed-lightness fifths hue mapping tab's own `_tab_note_rgb()`
    uses (CLAUDE.md: "tab's note color ignores octave, fixed
    lightness") -- a note should read as the same color in the editor as
    it does everywhere else in this app."""
    hue, sat, _light = note_to_hsl(pitch_class, config.MAX_OCTAVE, scheme="fifths")
    return hsl_to_rgb255(hue, sat, config.TAB_NOTE_LIGHTNESS)


def _note_text(note, zoom_name, duration_class):
    letter = NOTE_NAMES_FIFTHS[note.pitch_class]
    if zoom_name == "notehead":
        marker = SYMBOL_ACCIDENTALS.get(letter[1:], "")
        return NOTEHEAD_GLYPH + marker
    if zoom_name == "letter":
        return letter
    if zoom_name == "octave":
        return f"{letter}{note.octave}"
    suffix = _DURATION_SUFFIXES.get(duration_class, duration_class)
    return f"{letter}{note.octave}·{suffix}"


def _pad_center(text, width):
    """Center `text` into `width` characters -- this module's cell text
    is either a bare notehead glyph (occupying one real terminal column
    despite str.center()'s naive per-codepoint count treating it as two
    -- issue #82's same combining-mark subtlety terminal_tab_display.py
    already documents) or plain ASCII, so a light per-codepoint zero-
    advance rule (rather than importing that module's full wcwidth-aware
    machinery) is enough here."""
    display_len = sum(0 if unicodedata.combining(ch) else 1 for ch in text)
    pad = max(width - display_len, 0)
    left = pad // 2
    return " " * left + text + " " * (pad - left)


def _cell_swatch(rgb, text, width):
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    fg = (20, 20, 20) if lum > 140 else (230, 230, 230)
    return f"\033[48;2;{r};{g};{b}m\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{_pad_center(text, width)}\033[0m"


def render(score, cursor_col, cursor_row, zoom_level, chords_only, status, help_legend=""):
    """Draws the fixed grand-staff editor. Smoke-tested manually only,
    per this module's docstring -- the layout below (viewport scrolling,
    column widths, cursor highlight) mirrors terminal_tab_display.py's
    render() shape closely, minus its age-fade/barline/freeze machinery,
    none of which applies to a fixed, loaded-once buffer."""
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols, rows = size

    header_rows = 1 if chords_only else 0
    text_rows = 2 if help_legend else 1
    usable_rows = max(rows - text_rows - header_rows, 1)

    top, bottom = TOP_ROW, BOTTOM_ROW
    shrink = (top - bottom + 1) - usable_rows
    while shrink > 0 and (top > 20 or bottom < 0):
        if bottom < 0:
            bottom += 1
            shrink -= 1
        if shrink > 0 and top > 20:
            top -= 1
            shrink -= 1
    cursor_row = max(bottom, min(top, cursor_row))

    zoom_name, width = ZOOM_LEVELS[zoom_level]
    legend_width = config.TAB_LEGEND_WIDTH
    available_width = max(cols - legend_width, 0)
    visible_count = max(available_width // width, 1)
    start, end = visible_column_range(cursor_col, len(score.columns), visible_count)

    lines = []
    if chords_only:
        header_cells = [" " * legend_width]
        for i in range(start, end):
            name = chord_name_for_column(score.columns[i]) or ""
            header_cells.append(_pad_center(name[:width], width))
        lines.append("".join(header_cells))

    for screen_row in range(top, bottom - 1, -1):
        if screen_row == BASS_CLEF_ROW:
            clef_cell = BASS_CLEF_GLYPH.center(config.TAB_CLEF_WIDTH)
        elif screen_row == TREBLE_CLEF_ROW:
            clef_cell = TREBLE_CLEF_GLYPH.center(config.TAB_CLEF_WIDTH)
        else:
            clef_cell = " " * config.TAB_CLEF_WIDTH
        letter_cell = _legend_letter(screen_row, score.key_fifths).center(config.TAB_LETTER_WIDTH)
        cells = [clef_cell + letter_cell]

        for i in range(start, end):
            column = score.columns[i]
            is_cursor_cell = (i == cursor_col and screen_row == cursor_row)
            if chords_only:
                cell = LEDGER_CHAR * width if screen_row in STAFF_LINE_ROWS else " " * width
                if is_cursor_cell:
                    cell = f"\033[7m{_pad_center('', width)}\033[0m"
                cells.append(cell)
                continue

            idx = note_index_at_row(column, screen_row)
            if idx is not None:
                note = column.notes[idx]
                text = _note_text(note, zoom_name, column.duration_class)
                # A cursor sitting on a real note reverse-videos the
                # whole cell instead of its usual color swatch -- the
                # cursor always visibly wins over a note's own color.
                cell = (f"\033[7m{_pad_center(text, width)}\033[0m" if is_cursor_cell
                        else _cell_swatch(_editor_note_rgb(note.pitch_class), text, width))
            elif is_cursor_cell:
                cell = f"\033[7m{_pad_center(CURSOR_MARKER, width)}\033[0m"
            elif screen_row in ledger_rows_for(column, bottom, top) or screen_row in STAFF_LINE_ROWS:
                cell = LEDGER_CHAR * width
            else:
                cell = " " * width
            cells.append(cell)
        lines.append("".join(cells))

    out = []
    for i, line in enumerate(lines, start=1):
        out.append(f"\033[{i};1H\033[K{line}")
    out.append(f"\033[{len(lines) + 1};1H\033[K{status[:cols]}")
    if help_legend:
        out.append(f"\033[{len(lines) + 2};1H\033[K{help_legend[:cols]}")
    sys.stdout.write("\033[2J" + "".join(out))
    sys.stdout.flush()


def _legend_letter(row, key_fifths):
    """Legend letter for a staff row, spelled as it appears in the active
    key signature (issue #98 follow-up, direct user feedback) rather than
    always bare-natural -- e.g. the F-row's legend reads 'F♯' in G major,
    not 'F'. Reuses `staff_map.row_note_name()`'s own letter and the same
    `(row + GRAND_STAFF_REF_STEP) % 7` letter-index math
    `key_signature_accidental()` expects, rather than a second lookup
    table."""
    letter = row_note_name(row)
    letter_idx = (row + GRAND_STAFF_REF_STEP) % 7
    marker = {"sharp": "♯", "flat": "♭", "natural": ""}[key_signature_accidental(key_fifths, letter_idx)]
    return letter + marker


def ledger_rows_for(column, bottom, top):
    """Every ledger-line row this column's own notes need, clipped to
    [bottom, top] -- same per-column ledger accumulation
    terminal_tab_display.py's render() does, just recomputed per cell
    here instead of accumulated once per column (this module has no
    per-column loop separate from the per-row loop the way that one
    does)."""
    rows = set()
    for note in column.notes:
        row = staff_row(note.pitch_class, note.octave)
        rows.update(r for r in ledger_rows(row) if bottom <= r <= top)
    return rows
