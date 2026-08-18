from staff_map import staff_row, ledger_rows, STAFF_LINE_ROWS, TOP_ROW, BOTTOM_ROW


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


def test_sharp_shares_natural_letters_row():
    assert staff_row(1, 4) == staff_row(0, 4)  # C#4 sits on C4's row
    assert staff_row(6, 4) == staff_row(5, 4)  # F#4 sits on F4's row


def test_rows_inside_staff_blocks_need_no_ledger():
    for row in range(0, 9):
        assert ledger_rows(row) == []
    for row in range(12, 21):
        assert ledger_rows(row) == []
