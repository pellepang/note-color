"""Tests for the scoring harness (map #123, issue #132's metric triple)."""

import numpy as np
import pytest

from notecolor.convert import evaluate as ev

pytest.importorskip("mir_eval", reason="mir_eval arrives with the [convert] extra")


def N(onset, pitch, duration=0.5):
    return (onset, onset + duration, pitch)


# --- F0.5, and why it is not F1 -------------------------------------------


def test_f_half_weights_precision_twice_as_heavily_as_recall():
    """The stated editorial choice #132 made: a ghost note costs about
    twice a miss. Asserted as an actual asymmetry, not just a formula."""
    precise = ev.f_beta(precision=0.9, recall=0.5, beta=0.5)
    complete = ev.f_beta(precision=0.5, recall=0.9, beta=0.5)
    assert precise > complete
    # F1 would call these identical, which is exactly the blindness F0.5
    # exists to remove.
    assert ev.f_beta(0.9, 0.5, beta=1.0) == pytest.approx(ev.f_beta(0.5, 0.9, beta=1.0))


def test_f_beta_degenerate_cases():
    assert ev.f_beta(0.0, 0.0) == 0.0
    assert ev.f_beta(1.0, 1.0) == pytest.approx(1.0)


def test_perfect_transcription_scores_one():
    notes = [N(0.0, 60), N(1.0, 64), N(2.0, 67)]
    s = ev.score_notes(notes, notes, bars=1)
    assert s.f_half == pytest.approx(1.0)
    assert s.ghosts == 0 and s.misses == 0


def test_empty_estimate_scores_zero_and_misses_everything():
    ref = [N(0.0, 60), N(1.0, 64)]
    s = ev.score_notes(ref, [], bars=1)
    assert s.f_half == 0.0
    assert s.misses == 2 and s.ghosts == 0


def test_ghost_and_miss_rates_are_per_bar_not_per_note():
    """#132: a 2-note-per-bar ballad and a 16-note-per-bar riff at equal
    F1 are not equal work."""
    ref = [N(0.0, 60)]
    est = [N(0.0, 60), N(0.5, 61), N(0.7, 62), N(0.9, 63)]
    sparse = ev.score_notes(ref, est, bars=1)
    spread = ev.score_notes(ref, est, bars=4)
    assert sparse.ghost_rate_per_bar == pytest.approx(3.0)
    assert spread.ghost_rate_per_bar == pytest.approx(0.75)


def test_octave_error_counts_as_both_a_ghost_and_a_miss():
    """Under note-matching it is two errors, which is exactly the case the
    editor metric below prices differently."""
    s = ev.score_notes([N(0.0, 60)], [N(0.0, 72)], bars=1)
    assert s.matched == 0 and s.ghosts == 1 and s.misses == 1


# --- The editor-operations metric -----------------------------------------


def test_right_time_wrong_pitch_is_one_keystroke_not_two():
    """The whole reason this metric exists. F1 counts a wrong pitch as a
    ghost AND a miss; in the editor it is a single transpose."""
    ops = ev.editor_operations([N(0.0, 60)], [N(0.0, 62)])
    assert ops["operations"] == 1
    assert ops["transposed"] == 1 and ops["removed"] == 0


def test_a_missing_note_costs_one_placement():
    ops = ev.editor_operations([N(0.0, 60), N(1.0, 64)], [N(0.0, 60)])
    assert ops["operations"] == 1


def test_a_spurious_note_costs_one_removal():
    ops = ev.editor_operations([N(0.0, 60)], [N(0.0, 60), N(1.0, 64)])
    assert ops["operations"] == 1
    assert ops["removed"] == 1


def test_a_perfect_transcription_costs_nothing():
    notes = [N(0.0, 60), N(1.0, 64)]
    ops = ev.editor_operations(notes, notes)
    assert ops["operations"] == 0 and ops["ratio"] == 0.0


def test_an_empty_transcription_costs_exactly_typing_it_out():
    """The baseline the product bar is measured against: correcting must
    cost less than half of this."""
    ref = [N(0.0, 60), N(1.0, 64), N(2.0, 67)]
    ops = ev.editor_operations(ref, [])
    assert ops["operations"] == 3
    assert ops["ratio"] == pytest.approx(1.0)


def test_a_worse_than_useless_transcription_scores_above_one():
    """Which is a real outcome, not a hypothetical: measured 1.09x on
    dense synthetic jazz. The metric has to be able to say so."""
    ref = [N(0.0, 60)]
    est = [N(0.0, 61), N(0.5, 62), N(0.9, 63)]
    ops = ev.editor_operations(ref, est)
    assert ops["ratio"] > 1.0


def test_truth_notes_converts_beats_to_seconds_and_midi():
    from notecolor.convert import synth_corpus as sc

    track = sc.make_track(parts=("bass",), with_drums=False, repeats=1, seed=0)
    notes = ev.truth_notes(track)
    assert notes == sorted(notes)
    seconds_per_beat = 60.0 / track.tempo_bpm
    assert notes[0][0] == pytest.approx(0.0)
    assert notes[1][0] == pytest.approx(seconds_per_beat)
    assert all(0 <= pitch <= 127 for _o, _f, pitch in notes)
