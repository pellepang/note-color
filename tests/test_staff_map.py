from staff_map import (
    staff_row, ledger_rows, line_note_name, STAFF_LINE_ROWS, TOP_ROW, BOTTOM_ROW,
    BASS_CLEF_ROW, TREBLE_CLEF_ROW,
)


def test_treble_bottom_line_is_e4():
    assert staff_row(4, 4) == 12
    assert 12 in STAFF_LINE_ROWS


def test_bass_bottom_line_is_g2():
    assert staff_row(7, 2) == 0
    assert 0 in STAFF_LINE_ROWS


def test_middle_c_sits_between_the_staves():
    assert staff_row(0, 4) == 10
    assert ledger_rows(10) == [10]


def test_range_extremes_match_top_and_bottom_row():
    assert staff_row(0, 2) == BOTTOM_ROW  # C2
    assert staff_row(11, 5) == TOP_ROW    # B5


def test_ledger_lines_below_staff():
    assert ledger_rows(BOTTOM_ROW) == [-2, -4]  # C2: 2 ledger lines
    assert ledger_rows(-1) == []                 # space just below the staff, no ledger


def test_ledger_lines_above_staff():
    assert ledger_rows(TOP_ROW) == [22]  # B5: 1 ledger line
    assert ledger_rows(21) == []          # space just above the staff, no ledger


def test_accidentals_share_their_displayed_letters_row():
    # Flat-spelled accidentals (NOTE_NAMES_FIFTHS: Db, Eb, Ab, Bb) sit on the
    # row of the natural letter *above* them -- matching the letter the app
    # actually displays (main.py's _tab_note_label() via NOTE_NAMES_FIFTHS).
    assert staff_row(1, 4) == staff_row(2, 4)   # Db4 sits on D4's row
    assert staff_row(3, 4) == staff_row(4, 4)   # Eb4 sits on E4's row
    assert staff_row(8, 4) == staff_row(9, 4)   # Ab4 sits on A4's row
    assert staff_row(10, 4) == staff_row(11, 4)  # Bb4 sits on B4's row
    # F# is the one accidental spelled with a sharp, and keeps sitting on
    # the row of the natural letter *below* it.
    assert staff_row(6, 4) == staff_row(5, 4)   # F#4 sits on F4's row


def test_rows_inside_staff_blocks_need_no_ledger():
    for row in range(0, 9):
        assert ledger_rows(row) == []
    for row in range(12, 21):
        assert ledger_rows(row) == []


def test_line_note_names_match_standard_staff_mnemonics():
    bass = [line_note_name(r) for r in (0, 2, 4, 6, 8)]
    assert bass == ["G2", "B2", "D3", "F3", "A3"]  # Good Boys Do Fine Always
    treble = [line_note_name(r) for r in (12, 14, 16, 18, 20)]
    assert treble == ["E4", "G4", "B4", "D5", "F5"]  # Every Good Boy Does Fine


def test_clef_anchor_rows_are_staff_lines():
    assert BASS_CLEF_ROW in STAFF_LINE_ROWS
    assert TREBLE_CLEF_ROW in STAFF_LINE_ROWS
    assert line_note_name(BASS_CLEF_ROW) == "F3"
    assert line_note_name(TREBLE_CLEF_ROW) == "G4"
