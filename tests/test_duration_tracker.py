import numpy as np
import pytest

import config
from chord_smoother import ChordSmoother
from duration_tracker import DEFAULT_DURATION_CLASS, DurationTracker, duration_class_for_beats
from multipitch import NoteCandidate


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


# --- issue #64: live chord-mode wiring must not desync from the displayed
# note stack --------------------------------------------------------------

def test_chord_duration_tracker_survives_single_hop_raw_dropout_when_fed_debounced_stack():
    """Regression for issue #64. This mirrors the issue's own repro (a
    single-hop dropout in multipitch.detect()'s raw per-hop output, mid-
    sustain, on an otherwise continuously-held note) but drives
    DurationTracker.update() from ChordSmoother's already-debounced
    raw_stack -- the fixed main.py analysis_loop() order (chord_smoother.
    update() first, then chord_duration_tracker fed from its stack output)
    -- instead of raw multipitch.detect() output directly. Before the fix,
    the same dropout fragmented one continuously-sustained/displayed note
    into two separate finalize events; after it, ChordSmoother's
    NOTE_STACK_RELEASE_HOPS hysteresis absorbs the dropout before
    DurationTracker ever sees an absence, so the note stays one
    continuous duration event start to finish."""
    dt = DurationTracker(config)
    cs = ChordSmoother(config)
    silent_chroma = np.zeros(12)

    finalized_all = []
    saw_dropout_but_stack_still_active = False
    for i in range(30):
        hop = i + 1
        present = i != 15  # single-hop raw-detection dropout at the 16th hop
        raw_notes = [NoteCandidate(0, 4, 261.6, 0.9)] if present else []

        chord_name, raw_stack = cs.update(silent_chroma, silent_chroma, raw_notes)
        # Mirrors main.py's fixed wiring: chord_duration_tracker is fed
        # from the debounced raw_stack, not raw_notes directly.
        chord_notes = [
            (entry["pitch_class"], entry["octave"], entry["confidence"], False) for entry in raw_stack
        ]
        finalized = dt.update(chord_notes, hop)
        finalized_all.extend(finalized)

        stack_active = any(e["pitch_class"] == 0 and e["octave"] == 4 for e in raw_stack)
        if not present and stack_active:
            saw_dropout_but_stack_still_active = True

    # The note plays continuously for the full 30 hops (the one dropout
    # hop is absorbed by ChordSmoother's release hysteresis, same as the
    # issue's own repro demonstrated for the displayed note_stack) -- no
    # finalize event should have fired yet, and in particular none should
    # have fired fragmenting the sustain into a spurious early segment at
    # the dropout hop.
    assert saw_dropout_but_stack_still_active
    assert finalized_all == []

    # Now let the note actually stop for good and run out ChordSmoother's
    # release hysteresis -- only then should DurationTracker finalize,
    # and it should do so as exactly ONE event covering the note's whole
    # real lifetime, not two fragments.
    for extra in range(config.NOTE_STACK_RELEASE_HOPS + 1):
        hop = 30 + extra + 1
        chord_name, raw_stack = cs.update(silent_chroma, silent_chroma, [])
        chord_notes = [
            (entry["pitch_class"], entry["octave"], entry["confidence"], False) for entry in raw_stack
        ]
        finalized_all.extend(dt.update(chord_notes, hop))

    assert len(finalized_all) == 1
    pitch_class, octave, duration_hops = finalized_all[0]
    assert (pitch_class, octave) == (0, 4)
    # Onset was debounced in by NOTE_STACK_ATTACK_HOPS, so the tracked
    # span is close to but not exactly the full 30-hop sustain -- the
    # important assertion is "one event, roughly the whole sustain", not
    # an exact hop count tied to hysteresis constants.
    assert duration_hops >= 25
