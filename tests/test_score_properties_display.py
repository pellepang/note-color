"""Tests for issue #98's Score properties screen -- reel-stepping pure
logic. Per this repo's test convention, `render()`'s actual screen
layout is smoke-tested manually, not here."""

import score_properties_display as spd


def test_move_slot_wraps_both_directions():
    n = len(spd.PROPERTY_SLOTS)
    assert spd.move_slot(0, -1) == n - 1
    assert spd.move_slot(n - 1, 1) == 0


def test_spin_time_signature_steps_through_the_fixed_set():
    idx0 = spd.TIME_SIGNATURE_OPTIONS.index((4, 4))
    assert spd.spin_time_signature((4, 4), 1) == spd.TIME_SIGNATURE_OPTIONS[idx0 + 1]


def test_spin_time_signature_wraps_around():
    last = spd.TIME_SIGNATURE_OPTIONS[-1]
    assert spd.spin_time_signature(last, 1) == spd.TIME_SIGNATURE_OPTIONS[0]


def test_spin_time_signature_snaps_an_unrecognized_signature_to_the_start():
    # (9, 16) isn't in the fixed set at all -- snaps to index 0 first.
    assert spd.spin_time_signature((9, 16), 0) == spd.TIME_SIGNATURE_OPTIONS[0]


def test_spin_key_fifths_steps_by_one():
    assert spd.spin_key_fifths(0, 1) == 1
    assert spd.spin_key_fifths(0, -1) == -1


def test_spin_key_fifths_clamps_not_wraps():
    assert spd.spin_key_fifths(spd.KEY_FIFTHS_MAX, 1) == spd.KEY_FIFTHS_MAX
    assert spd.spin_key_fifths(spd.KEY_FIFTHS_MIN, -1) == spd.KEY_FIFTHS_MIN


def test_spin_tempo_steps_by_the_fixed_increment():
    assert spd.spin_tempo(100.0, 1) == 100.0 + spd.TEMPO_STEP_BPM
    assert spd.spin_tempo(100.0, -1) == 100.0 - spd.TEMPO_STEP_BPM


def test_spin_tempo_clamps_at_the_configured_range():
    assert spd.spin_tempo(spd.TEMPO_MAX_BPM, 1) == spd.TEMPO_MAX_BPM
    assert spd.spin_tempo(spd.TEMPO_MIN_BPM, -1) == spd.TEMPO_MIN_BPM


def test_key_fifths_label_zero_reads_as_no_accidentals():
    assert spd.key_fifths_label(0) == "no sharps/flats"


def test_key_fifths_label_pluralizes_correctly():
    assert spd.key_fifths_label(1) == "1 sharp"
    assert spd.key_fifths_label(2) == "2 sharps"
    assert spd.key_fifths_label(-1) == "1 flat"
    assert spd.key_fifths_label(-3) == "3 flats"
