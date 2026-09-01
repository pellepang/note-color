"""Tests for main.py's pure, non-threaded helper logic. Mirrors this
repo's existing convention (see test_shell.py) of importing and directly
testing small pure functions extracted from main.py's render loops,
rather than the loops themselves (smoke-tested manually)."""

from main import _hop_beats


def test_hop_beats_takes_max_not_sum_of_simultaneous_finalizations():
    # Issue #76: an ordinary single note finalized independently by both
    # the mono DurationTracker and multipitch's one-note "chord" must
    # credit its duration once toward the bar boundary, not twice.
    assert _hop_beats([2.0, 2.0]) == 2.0


def test_hop_beats_takes_max_across_differing_values():
    assert _hop_beats([1.5, 3.0, 0.5]) == 3.0


def test_hop_beats_treats_none_entries_as_zero():
    # A `beats` value is None when bpm_estimate was unknown at
    # finalization time -- shouldn't crash max()/comparisons.
    assert _hop_beats([None, 1.0]) == 1.0
    assert _hop_beats([None, None]) == 0.0


def test_hop_beats_empty_list_is_zero():
    # No note finalized this hop at all.
    assert _hop_beats([]) == 0.0


def test_hop_beats_single_mono_only_value_unchanged():
    # No chord/multipitch finalization this hop -- behaves like before
    # the fix for the common single-note case.
    assert _hop_beats([1.25]) == 1.25
