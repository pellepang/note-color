"""Pure functions mapping a (pitch_class, octave) note to its position on a
grand staff (bass + treble, connected by the middle-C ledger line) -- the
same layout piano music is written in. Row 0 is the bass staff's bottom
line (G2); row 12 is the treble staff's bottom line (E4); rows increase
upward. Accidental notes share their *displayed* natural letter's row --
matching the flat-biased spelling `color_map.NOTE_NAMES_FIFTHS` already
uses elsewhere in the app (e.g. Db shares D's row, not C's; F# is the one
accidental spelled with a sharp and shares F's row) -- with the accidental
shown as a marker rather than a row shift.
"""

from color_map import NOTE_NAMES_FIFTHS

LETTER_INDEX = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}  # natural semitone -> C..B
LETTER_NAMES = "CDEFGAB"  # index -> letter, inverse of LETTER_INDEX's values

# Pitch classes spelled with a flat (Db/Eb/Ab/Bb) resolve to the natural
# letter above; everything else accidental (just F#) resolves to the
# natural letter below. Derived from NOTE_NAMES_FIFTHS itself, not a
# hardcoded pitch-class list, so the two spellings can never drift apart.
_FLAT_SPELLED_PITCH_CLASSES = {
    pc for pc, name in enumerate(NOTE_NAMES_FIFTHS) if "b" in name
}

GRAND_STAFF_REF_STEP = 18  # diatonic step of G2 (bass clef bottom line) -> row 0

STAFF_LINE_ROWS = frozenset({0, 2, 4, 6, 8, 12, 14, 16, 18, 20})  # bass 5 + treble 5

TOP_ROW, BOTTOM_ROW = 23, -4  # B5 .. C2, the app's usable pitch range

# Anchor lines a clef's spiral/dots wrap around: F3 for bass, G4 for treble.
BASS_CLEF_ROW = 6
TREBLE_CLEF_ROW = 14


def diatonic_step(pitch_class, octave):
    if pitch_class in LETTER_INDEX:
        base = pitch_class
    elif pitch_class in _FLAT_SPELLED_PITCH_CLASSES:
        base = pitch_class + 1
    else:
        base = pitch_class - 1
    return LETTER_INDEX[base] + 7 * octave


def staff_row(pitch_class, octave):
    return diatonic_step(pitch_class, octave) - GRAND_STAFF_REF_STEP


def line_note_name(row):
    """Bare letter for a staff line row (row must be in STAFF_LINE_ROWS --
    lines are always natural notes, never sharps). No octave digit -- the
    row position on the staff already conveys octave, same reasoning as
    the notehead *name* render style's label (see terminal_tab_display.py)
    and tab's octave-independent note color (CLAUDE.md: "tab's note color
    ignores octave, fixed lightness")."""
    step = row + GRAND_STAFF_REF_STEP
    return LETTER_NAMES[step % 7]


def ledger_rows(row):
    """Rows where a ledger line must be drawn for a note at `row`."""
    if 0 <= row <= 8 or 12 <= row <= 20:
        return []
    if 9 <= row <= 11:
        return [10] if row == 10 else []  # middle C, between the staves
    if row < 0:
        n = (-row) // 2
        return [-2 * i for i in range(1, n + 1)]
    off = row - 20
    n = off // 2
    return [20 + 2 * i for i in range(1, n + 1)]
