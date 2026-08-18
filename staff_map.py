"""Pure functions mapping a (pitch_class, octave) note to its position on a
grand staff (bass + treble, connected by the middle-C ledger line) -- the
same layout piano music is written in. Row 0 is the bass staff's bottom
line (G2); row 12 is the treble staff's bottom line (E4); rows increase
upward. Sharp notes share their natural letter's row (matches the
sharps-only note spelling used elsewhere), with the accidental shown as a
"#" marker rather than a row shift.
"""

LETTER_INDEX = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}  # natural semitone -> C..B

GRAND_STAFF_REF_STEP = 18  # diatonic step of G2 (bass clef bottom line) -> row 0

STAFF_LINE_ROWS = frozenset({0, 2, 4, 6, 8, 12, 14, 16, 18, 20})  # bass 5 + treble 5

TOP_ROW, BOTTOM_ROW = 23, -4  # B5 .. C2, the app's usable pitch range


def diatonic_step(pitch_class, octave):
    base = pitch_class if pitch_class in LETTER_INDEX else pitch_class - 1
    return LETTER_INDEX[base] + 7 * octave


def staff_row(pitch_class, octave):
    return diatonic_step(pitch_class, octave) - GRAND_STAFF_REF_STEP


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
