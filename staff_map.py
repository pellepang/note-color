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


def row_note_name(row):
    """Bare letter for ANY staff row -- a line row, a space row between
    lines, or a ledger-line row/space beyond the staff. Every row (line or
    space) sits on a distinct diatonic step, so this is just that step's
    letter, reusing the same `LETTER_NAMES`/`GRAND_STAFF_REF_STEP` math
    `diatonic_step()`/`staff_row()` already use to place noteheads --
    lines are always natural notes, never sharps, and so is every space
    (issue #36: the legend labels every row, not just `STAFF_LINE_ROWS`).
    No octave digit -- the row position on the staff already conveys
    octave, same reasoning as the notehead *name* render style's label
    (see terminal_tab_display.py) and tab's octave-independent note color
    (CLAUDE.md: "tab's note color ignores octave, fixed lightness")."""
    step = row + GRAND_STAFF_REF_STEP
    return LETTER_NAMES[step % 7]


# Standard order-of-sharps/order-of-flats, expressed as letter indices
# (0=C..6=B, LETTER_NAMES' own order) rather than pitch classes -- a key
# signature accidental applies to a *letter name* across every octave, not
# one specific pitch class. key_fifths sharps adds the first `key_fifths`
# entries of _SHARP_ORDER_LETTER_IDX; key_fifths flats (negative) adds the
# first `abs(key_fifths)` entries of _FLAT_ORDER_LETTER_IDX. Issue #98
# follow-up (direct user feedback after hands-on use): the editor's
# legend/default note placement should reflect the active key signature
# instead of always showing/placing a bare natural -- see docs/DECISIONS.md.
_SHARP_ORDER_LETTER_IDX = [LETTER_NAMES.index(letter) for letter in "FCGDAEB"]
_FLAT_ORDER_LETTER_IDX = [LETTER_NAMES.index(letter) for letter in "BEADGCF"]


def key_signature_accidental(key_fifths, letter_idx):
    """Whether `letter_idx` (0=C..6=B) is sharped, flatted, or left natural
    under a key signature of `key_fifths` sharps (negative = flats) -- e.g.
    key_fifths=1 (G major) sharps only F, the order-of-sharps' first entry.
    Returns "sharp"/"flat"/"natural". Used by score_editor_display.py's
    legend (a row's letter should read as spelled in the active key) and
    `pitch_at_row()`'s key-aware default placement."""
    if key_fifths > 0 and letter_idx in _SHARP_ORDER_LETTER_IDX[:key_fifths]:
        return "sharp"
    if key_fifths < 0 and letter_idx in _FLAT_ORDER_LETTER_IDX[:-key_fifths]:
        return "flat"
    return "natural"


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
