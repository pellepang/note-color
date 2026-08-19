from staff_map import (
    staff_row, ledger_rows, row_note_name, STAFF_LINE_ROWS, TOP_ROW, BOTTOM_ROW,
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
    # No octave digit -- the staff row position already conveys octave.
    bass = [row_note_name(r) for r in (0, 2, 4, 6, 8)]
    assert bass == ["G", "B", "D", "F", "A"]  # Good Boys Do Fine Always
    treble = [row_note_name(r) for r in (12, 14, 16, 18, 20)]
    assert treble == ["E", "G", "B", "D", "F"]  # Every Good Boy Does Fine


def test_clef_anchor_rows_are_staff_lines():
    assert BASS_CLEF_ROW in STAFF_LINE_ROWS
    assert TREBLE_CLEF_ROW in STAFF_LINE_ROWS
    assert row_note_name(BASS_CLEF_ROW) == "F"
    assert row_note_name(TREBLE_CLEF_ROW) == "G"


def test_space_row_note_names_match_standard_staff_mnemonics():
    # Issue #36: the space rows between staff lines must resolve too, not
    # just STAFF_LINE_ROWS -- row_note_name() is general over every row.
    bass_spaces = [row_note_name(r) for r in (1, 3, 5, 7)]
    assert bass_spaces == ["A", "C", "E", "G"]  # All Cows Eat Grass
    treble_spaces = [row_note_name(r) for r in (13, 15, 17, 19)]
    assert treble_spaces == ["F", "A", "C", "E"]  # spells FACE


def test_row_note_name_covers_ledger_line_rows_too():
    # Middle C (row 10, between the staves) and rows further out into
    # ledger-line territory must still resolve to a letter, not just rows
    # inside the two 5-line staff blocks.
    assert row_note_name(10) == "C"          # middle C
    assert row_note_name(BOTTOM_ROW) == "C"  # C2, 2 ledger lines below bass staff
    assert row_note_name(TOP_ROW) == "B"     # B5, 1 ledger line above treble staff


def test_row_note_name_matches_staff_row_for_every_pitch_class_octave():
    # Cross-check against staff_row()/diatonic_step() directly: whatever
    # natural or accidental note lands on a row, that row's letter must
    # match the natural letter the note is displayed/spelled as sharing.
    naturals = {0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B"}
    for pitch_class, letter in naturals.items():
        for octave in range(2, 6):
            row = staff_row(pitch_class, octave)
            if BOTTOM_ROW <= row <= TOP_ROW:
                assert row_note_name(row) == letter
