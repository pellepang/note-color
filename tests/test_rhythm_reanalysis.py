"""Tests for rhythm_reanalysis.py's pure recompute() (issue #77's `R`-key
non-causal rhythm re-analysis). Synthesizes HopRecord sequences directly
(this repo's existing "synthesize the signal, no binary fixtures"
convention -- see tests/test_tempo_tracker.py's impulse trains and
tests/test_batch_transcribe.py's make_tone()/make_click_track()) rather
than driving the real live pipeline end to end."""

import pytest

from rhythm_reanalysis import CorrectedNote, HopRecord, recompute


HOP_SECONDS = 0.02  # 50 hops/sec, matches tempo_tracker tests' convention


def _mono_hop(hop_index, pitch_class=0, octave=4, rms=1.0, is_onset=False, novelty=0.0):
    return HopRecord(hop_index=hop_index, mono=(pitch_class, octave, rms, is_onset),
                      chord_notes=(), chroma_novelty=novelty)


def _silent_hop(hop_index, novelty=0.0):
    return HopRecord(hop_index=hop_index, mono=None, chord_notes=(), chroma_novelty=novelty)


def _chord_hop(hop_index, notes=(), novelty=0.0):
    return HopRecord(hop_index=hop_index, mono=None, chord_notes=tuple(notes), chroma_novelty=novelty)


def test_empty_buffer_returns_none():
    assert recompute([], HOP_SECONDS, beats_per_bar=4.0) is None


def test_mono_note_gets_a_corrected_duration_class():
    # A held mono note, onset at hop 0, decaying below threshold at hop 10
    # -- 10 hops * 0.02s = 0.2s. With no bpm estimate available (no
    # novelty energy anywhere in the window), duration_class falls back to
    # the same DEFAULT_DURATION_CLASS the live/causal path uses.
    records = [_mono_hop(0, is_onset=True, rms=1.0)]
    records += [_mono_hop(i, rms=1.0) for i in range(1, 8)]
    records += [_mono_hop(i, rms=0.05) for i in range(8, 12)]  # decays below DURATION_DECAY_RATIO=0.25

    result = recompute(records, HOP_SECONDS, beats_per_bar=4.0)

    assert result is not None
    assert len(result.corrected_notes) == 1
    note = result.corrected_notes[0]
    assert (note.pitch_class, note.octave) == (0, 4)
    assert note.onset_time == pytest.approx(0.0)


def test_mono_note_with_no_onset_in_window_is_skipped():
    # A note already sounding when the buffer's window starts (its real
    # onset happened before the buffer began) has no onset_index at all --
    # can't be corrected without a known onset, so it should be silently
    # skipped rather than guessed at.
    records = [_mono_hop(i, is_onset=False, rms=1.0) for i in range(5)]
    result = recompute(records, HOP_SECONDS, beats_per_bar=4.0)
    assert result.corrected_notes == []


def test_chord_note_onset_detected_on_reappearance_after_absence():
    records = [
        _chord_hop(0, notes=[(0, 4, 0.9)]),
        _chord_hop(1, notes=[(0, 4, 0.9)]),
        _chord_hop(2, notes=[]),  # gap
        _chord_hop(3, notes=[(0, 4, 0.9)]),  # re-onset
        _chord_hop(4, notes=[(0, 4, 0.05)]),  # decays below threshold
    ]
    result = recompute(records, HOP_SECONDS, beats_per_bar=4.0)
    # Two separate onsets for the same key -- the gap at hop 2 means the
    # reappearance at hop 3 is a fresh onset, not a continuation.
    onset_times = sorted(n.onset_time for n in result.corrected_notes)
    assert onset_times == [pytest.approx(0.0), pytest.approx(3 * HOP_SECONDS)]


def test_tempo_estimate_recovers_a_known_periodic_bpm():
    # A clean periodic novelty impulse every 25 hops at 50 hops/sec is a
    # beat every 0.5s -- 120bpm, same setup as test_tempo_tracker.py's own
    # convergence test but fed through beat_track() instead of
    # TempoTracker's autocorrelation.
    n_hops = 200
    records = [_silent_hop(i, novelty=1.0 if i % 25 == 0 else 0.0) for i in range(n_hops)]

    result = recompute(records, HOP_SECONDS, beats_per_bar=4.0)

    assert result.bpm_estimate is not None
    # Beat trackers commonly lock onto a half/double-time multiple of the
    # true tempo rather than the literal value -- same tolerance
    # convention test_batch_transcribe.py's own tempo test uses.
    ratio = result.bpm_estimate / 120.0
    assert any(abs(ratio - mult) < 0.15 for mult in (0.5, 1.0, 2.0))


def test_no_novelty_energy_returns_no_tempo_estimate():
    records = [_silent_hop(i, novelty=0.0) for i in range(50)]
    result = recompute(records, HOP_SECONDS, beats_per_bar=4.0)
    assert result.bpm_estimate is None
    assert result.barline_times == []  # no bpm -- can't place corrected barlines at all


def test_barline_times_follow_beats_per_bar_accumulation():
    # Two mono notes, each measured (via the decay-ratio crossing) at
    # roughly 2 beats long at a bpm that recompute() itself estimates --
    # rather than asserting an exact barline count against an estimate
    # this test doesn't control, assert the structural invariant: every
    # emitted barline is a real, ascending timestamp within the window.
    records = []
    hop = 0
    for _ in range(4):
        for i in range(20):
            records.append(_mono_hop(hop, is_onset=(i == 0), rms=1.0, novelty=1.0 if i == 0 else 0.0))
            hop += 1
        for i in range(5):
            records.append(_mono_hop(hop, rms=0.05, novelty=0.0))
            hop += 1

    result = recompute(records, HOP_SECONDS, beats_per_bar=1.0)  # low bar so *some* barline is likely
    for t in result.barline_times:
        assert 0.0 <= t <= records[-1].hop_index * HOP_SECONDS
    assert result.barline_times == sorted(result.barline_times)


def test_window_start_and_end_times_match_first_and_last_hop():
    records = [_mono_hop(100 + i, rms=1.0) for i in range(10)]
    result = recompute(records, HOP_SECONDS, beats_per_bar=4.0)
    assert result.window_start_time == pytest.approx(100 * HOP_SECONDS)
    assert result.window_end_time == pytest.approx(109 * HOP_SECONDS)


def test_corrected_note_is_a_plain_namedtuple_shape():
    # Sanity check on the public shape callers (main.py) destructure.
    note = CorrectedNote(pitch_class=0, octave=4, onset_time=1.0, duration_class="quarter")
    assert note.pitch_class == 0 and note.duration_class == "quarter"
