import pytest

from duration_tracker import DEFAULT_DURATION_CLASS, DurationTracker, duration_class_for_beats


class _Cfg:
    DURATION_DECAY_RATIO = 0.25


# --- duration_class_for_beats -------------------------------------------

@pytest.mark.parametrize("beats,expected", [
    (4.0, "whole"),
    (3.0, "dotted-half"),
    (2.0, "half"),
    (1.5, "dotted-quarter"),
    (1.0, "quarter"),
    (0.9, "quarter"),  # closer to 1.0 than to 0.75
    (0.75, "dotted-eighth"),
    (0.5, "eighth"),
    (0.375, "dotted-sixteenth"),
    (0.25, "sixteenth"),
    (0.125, "thirtysecond"),
])
def test_duration_class_snaps_to_nearest_standard_value(beats, expected):
    assert duration_class_for_beats(beats) == expected


@pytest.mark.parametrize("beats", [None, 0, -1.0])
def test_duration_class_falls_back_when_beats_unknown(beats):
    assert duration_class_for_beats(beats) == DEFAULT_DURATION_CLASS


# --- DurationTracker.update ----------------------------------------------

def test_update_finalizes_on_disappearance_with_correct_span():
    tracker = DurationTracker(_Cfg())
    tracker.update([(0, 4, 1.0, True)], hop_index=0)
    tracker.update([(0, 4, 1.0, False)], hop_index=1)
    tracker.update([(0, 4, 1.0, False)], hop_index=2)
    finalized = tracker.update([], hop_index=3)
    assert finalized == [(0, 4, 3)]


def test_update_finalizes_on_decay_below_ratio():
    tracker = DurationTracker(_Cfg())
    tracker.update([(0, 4, 1.0, True)], hop_index=0)
    tracker.update([(0, 4, 0.5, False)], hop_index=1)  # ratio 0.5, still active
    finalized = tracker.update([(0, 4, 0.2, False)], hop_index=2)  # ratio 0.2 <= 0.25
    assert finalized == [(0, 4, 2)]


def test_update_never_finalizes_a_brand_new_slot_the_same_hop():
    tracker = DurationTracker(_Cfg())
    finalized = tracker.update([(0, 4, 1.0, True)], hop_index=0)
    assert finalized == []


def test_update_reonset_finalizes_old_slot_before_starting_a_new_one():
    # Two quick repeated attacks on the same pitch/octave should read as
    # two short notes, not one held note (#55 story 3).
    tracker = DurationTracker(_Cfg())
    tracker.update([(0, 4, 1.0, True)], hop_index=0)
    finalized_1 = tracker.update([(0, 4, 1.0, True)], hop_index=1)
    finalized_2 = tracker.update([], hop_index=2)
    assert finalized_1 == [(0, 4, 1)]
    assert finalized_2 == [(0, 4, 1)]


def test_update_tracks_independent_slots_by_key():
    tracker = DurationTracker(_Cfg())
    tracker.update([(0, 4, 1.0, True), (4, 4, 1.0, True)], hop_index=0)
    # pitch_class 0 drops out here -- finalizes on this same call, distinct
    # from pitch_class 4, which keeps sounding.
    finalized_at_drop = tracker.update([(4, 4, 1.0, False)], hop_index=1)
    finalized_at_end = tracker.update([], hop_index=2)
    assert finalized_at_drop == [(0, 4, 1)]
    assert finalized_at_end == [(4, 4, 2)]


# --- DurationTracker.finalize_noncausal -----------------------------------

def test_finalize_noncausal_basic_decay_span():
    envelope = [1.0, 1.0, 1.0, 0.5, 0.2, 0.1, 0.05]
    result = DurationTracker.finalize_noncausal(envelope, [0], decay_ratio=0.25, smooth_window=1)
    assert result == [(0, 4)]


def test_finalize_noncausal_splits_multiple_onsets_into_segments():
    envelope = [1, 1, 0.5, 0.1, 1, 1, 0.5, 0.1]
    result = DurationTracker.finalize_noncausal(envelope, [0, 4], decay_ratio=0.25, smooth_window=1)
    assert result == [(0, 3), (4, 3)]


def test_finalize_noncausal_smooths_a_single_noisy_dip():
    # A single-hop dip mid-note should not read as the note ending early
    # once centered smoothing sees the recovery on either side of it --
    # the whole point of the non-causal refinement over the causal
    # update() path, which can't see into the future.
    envelope = [1.0, 1.0, 0.05, 1.0, 1.0, 0.5, 0.1, 0.05, 0.05]
    unsmoothed = DurationTracker.finalize_noncausal(envelope, [0], decay_ratio=0.25, smooth_window=1)
    smoothed = DurationTracker.finalize_noncausal(envelope, [0], decay_ratio=0.25, smooth_window=5)
    assert smoothed[0][1] > unsmoothed[0][1]
