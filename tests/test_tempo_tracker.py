import types

import numpy as np
import pytest

from tempo_tracker import TempoTracker


def _cfg(min_bpm=40, max_bpm=240, update_interval_hops=5, history_seconds=2.0, min_confidence=0.3):
    return types.SimpleNamespace(
        TEMPO_MIN_BPM=min_bpm,
        TEMPO_MAX_BPM=max_bpm,
        TEMPO_UPDATE_INTERVAL_HOPS=update_interval_hops,
        TEMPO_HISTORY_SECONDS=history_seconds,
        TEMPO_MIN_CONFIDENCE=min_confidence,
    )


def _feed_impulse_train(tracker, period_hops, n_hops):
    estimate = None
    for i in range(n_hops):
        estimate = tracker.update(1.0 if i % period_hops == 0 else 0.0)
    return estimate


def test_returns_none_before_history_fills():
    hop_seconds = 0.02  # history_len = 2.0 / 0.02 = 100 hops
    tracker = TempoTracker(_cfg(), hop_seconds)
    estimate = _feed_impulse_train(tracker, period_hops=25, n_hops=99)
    assert estimate is None


def test_converges_to_a_known_periodic_bpm():
    # A perfectly periodic novelty impulse every 25 hops at 50 hops/sec
    # (hop_seconds=0.02) is a beat every 0.5s -- exactly 120 BPM.
    hop_seconds = 0.02
    tracker = TempoTracker(_cfg(), hop_seconds)
    estimate = _feed_impulse_train(tracker, period_hops=25, n_hops=150)
    assert estimate == pytest.approx(120.0, rel=0.05)


def test_estimate_always_stays_within_configured_bounds():
    hop_seconds = 0.02
    cfg = _cfg(min_bpm=40, max_bpm=240)
    tracker = TempoTracker(cfg, hop_seconds)
    rng = np.random.default_rng(0)
    estimate = None
    for _ in range(150):
        estimate = tracker.update(float(rng.random()))
    assert estimate is None or cfg.TEMPO_MIN_BPM <= estimate <= cfg.TEMPO_MAX_BPM


# --- issue #70: confidence-gated re-estimation -----------------------------

def test_low_confidence_reestimate_holds_last_estimate():
    """Regression for issue #70's third mechanism: once a periodic
    novelty history scrolls out of the rolling window and is replaced by
    genuinely non-periodic content (no consistent beat at all -- verified
    on real recorded audio via a sequence of isolated single notes at
    irregular intervals), the autocorrelation's best lag stops meaning
    anything -- confidence collapses and the estimate swings wildly
    (measured live: 99bpm -> 41bpm -> 76bpm -> 49bpm across consecutive
    re-estimates). Below TEMPO_MIN_CONFIDENCE, the tracker should hold its
    last real estimate instead of re-locking onto that noise."""
    hop_seconds = 0.02
    cfg = _cfg(update_interval_hops=5, history_seconds=1.0)  # history_len = 50 hops
    tracker = TempoTracker(cfg, hop_seconds)

    # Converge on a clean, confident periodic impulse train first.
    estimate = _feed_impulse_train(tracker, period_hops=25, n_hops=100)
    assert estimate == pytest.approx(120.0, rel=0.05)

    # Now feed low-amplitude incoherent noise (no consistent period) for
    # several more re-estimation intervals -- confidence should collapse
    # and the tracker should keep reporting the earlier, still-valid
    # estimate rather than chasing noise.
    rng = np.random.default_rng(1)
    for _ in range(5 * 4):
        estimate = tracker.update(float(rng.random()) * 0.01)
    assert estimate == pytest.approx(120.0, rel=0.05)


def test_high_confidence_reestimate_still_updates():
    # Sanity check the gate isn't just permanently latching -- a genuine
    # tempo CHANGE (still cleanly periodic, still high confidence) must
    # still be picked up.
    hop_seconds = 0.02
    cfg = _cfg(update_interval_hops=5, history_seconds=1.0)
    tracker = TempoTracker(cfg, hop_seconds)
    _feed_impulse_train(tracker, period_hops=25, n_hops=100)  # 120bpm

    estimate = _feed_impulse_train(tracker, period_hops=17, n_hops=100)  # ~176bpm
    assert estimate == pytest.approx(60.0 / (17 * hop_seconds), rel=0.05)


def test_reestimation_is_amortized_across_update_interval_hops():
    hop_seconds = 0.02  # history_len = 1.0 / 0.02 = 50 hops
    cfg = _cfg(update_interval_hops=5, history_seconds=1.0)
    tracker = TempoTracker(cfg, hop_seconds)

    calls = {"n": 0}
    original_estimate = tracker._estimate

    def counting_estimate():
        calls["n"] += 1
        return original_estimate()

    tracker._estimate = counting_estimate

    for i in range(50 + 5 * 3):  # fill history, then three more re-estimation intervals
        tracker.update(1.0 if i % 10 == 0 else 0.0)

    assert calls["n"] == 4  # once on fill, then once per subsequent interval
