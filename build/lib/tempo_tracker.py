"""Live-only causal tempo (BPM) estimation (issue #55).

FFT-based autocorrelation over a rolling window of recent onset-novelty
values (spectral_flux()/chroma_flux() output, accumulated by the caller
each hop) -- the same autocorrelation approach pitch_detect.py's YIN
already uses for pitch, applied here to the novelty-history signal
instead of a raw audio window, rather than a second, different technique.

Batch tempo tracking does not use this module at all -- librosa.beat.
beat_track() covers that need directly (batch_transcribe.py).
"""

from collections import deque

import numpy as np


class TempoTracker:
    def __init__(self, cfg, hop_seconds):
        """`hop_seconds`: real seconds per analysis hop (config.BLOCK_SIZE
        / config.SAMPLE_RATE) -- converts an autocorrelation lag (in hops)
        into a tempo in beats per minute."""
        self.hop_seconds = hop_seconds
        self.min_bpm = cfg.TEMPO_MIN_BPM
        self.max_bpm = cfg.TEMPO_MAX_BPM
        self.update_interval_hops = cfg.TEMPO_UPDATE_INTERVAL_HOPS
        self.min_confidence = cfg.TEMPO_MIN_CONFIDENCE
        self.octave_lock_margin = cfg.TEMPO_OCTAVE_LOCK_MARGIN
        history_len = max(1, int(cfg.TEMPO_HISTORY_SECONDS / hop_seconds))
        self.history = deque(maxlen=history_len)
        self._hops_since_update = 0
        self._last_estimate = None

    def update(self, novelty_value):
        """Call once per hop with this hop's novelty value. Returns a bpm
        estimate (float, clamped to [TEMPO_MIN_BPM, TEMPO_MAX_BPM]) once
        TEMPO_HISTORY_SECONDS worth of history has accumulated, else None
        -- mirrors chord mode's own "blank until confident" convention.
        Re-runs the autocorrelation estimate only every
        TEMPO_UPDATE_INTERVAL_HOPS calls, returning the previous estimate
        on the hops between (tempo shouldn't and can't meaningfully change
        every ~23ms, and this amortizes the FFT cost)."""
        self.history.append(novelty_value)
        self._hops_since_update += 1
        if len(self.history) < self.history.maxlen:
            return None
        if self._last_estimate is not None and self._hops_since_update < self.update_interval_hops:
            return self._last_estimate
        self._hops_since_update = 0
        self._last_estimate = self._estimate()
        return self._last_estimate

    def _estimate(self):
        signal = np.asarray(self.history, dtype=np.float64)
        signal = signal - np.mean(signal)
        if not np.any(signal):
            return self._last_estimate  # no novelty at all -- hold the previous estimate

        n = len(signal)
        size = 1
        while size < 2 * n:
            size *= 2
        spectrum = np.fft.rfft(signal, size)
        acf = np.fft.irfft(spectrum * np.conjugate(spectrum))[:n]

        lag_min = max(1, int(round(60.0 / self.max_bpm / self.hop_seconds)))
        lag_max = min(n - 1, int(round(60.0 / self.min_bpm / self.hop_seconds)))
        if lag_max <= lag_min:
            return self._last_estimate

        window = acf[lag_min:lag_max + 1]
        best_lag = lag_min + int(np.argmax(window))

        # Issue #70: how much of the novelty history's total energy
        # (acf[0], the zero-lag autocorrelation) the best candidate lag
        # actually explains -- a real periodic passage concentrates energy
        # at its true period's lag (and integer multiples), while
        # non-periodic content (no consistent beat in this window at all)
        # spreads it thinly across every lag, so no single candidate ever
        # stands out. Below min_confidence, re-locking onto whichever lag
        # happens to be marginally tallest is re-locking onto noise --
        # hold the last real estimate instead of chasing it. See
        # config.TEMPO_MIN_CONFIDENCE's comment for the empirical
        # calibration.
        peak = float(window[best_lag - lag_min])
        confidence = peak / acf[0] if acf[0] > 0 else 0.0
        if confidence < self.min_confidence:
            return self._last_estimate

        best_lag = self._resolve_octave_lock(acf, best_lag, peak, confidence, lag_max)

        bpm = 60.0 / (best_lag * self.hop_seconds)
        return float(min(max(bpm, self.min_bpm), self.max_bpm))

    def _resolve_octave_lock(self, acf, best_lag, peak, confidence, lag_max):
        """Issue #79: `acf` (the full autocorrelation array) is already in
        memory once `best_lag` has been picked by argmax -- checking
        `acf[2*best_lag]` mirrors pitch_detect.py's YIN sub-harmonic check
        (applied to tempo instead of pitch) and catches the single most
        common causal-beat-tracker failure mode named in the literature
        (docs/research/oss-landscape-rhythm-tempo.md): locking onto a
        strong subdivision (e.g. eighth notes) instead of the true, slower
        beat -- argmax alone has no way to prefer the musically-plausible
        slower reading over a subdivision that happens to autocorrelate
        marginally taller.

        A genuinely non-alternating periodic novelty signal's acf decays
        *linearly* with lag multiple -- `acf[k*L]/acf[0] == (periods - k) /
        periods` for a delta train, confirmed empirically to hold exactly
        regardless of tempo or window length (see docs/DECISIONS.md) -- so
        `2*acf[best_lag] - acf[0]` is what a plain, non-alternating signal's
        acf at `2*best_lag` *should* measure. Real alternating structure (a
        stronger beat every other subdivision, i.e. best_lag is actually a
        subdivision of a genuine slower beat) shows up as `acf[2*best_lag]`
        measuring meaningfully *higher* than that linear prediction --
        that gap, normalized by acf[0], is `excess` below. Only correcting
        towards the *slower* (2x) reading, never the faster (0.5x) one, is
        deliberate: argmax already returns the single tallest candidate in
        the whole search window, so a same-window faster candidate can
        never be more confident than what argmax already picked -- the only
        exploitable asymmetry is a slower candidate whose own autocorrelation
        support isn't fully captured by "is it the single tallest peak."

        Raw additive noise inflates `acf[0]` (its own zero-lag energy)
        without inflating acf at any nonzero lag, which biases `excess`
        upward even for a plain, non-alternating signal -- subtracting
        `(1 - confidence)` cancels that bias empirically (see
        config.TEMPO_OCTAVE_LOCK_MARGIN's comment for the calibration data).
        """
        double_lag = 2 * best_lag
        if double_lag > lag_max or acf[0] <= 0:
            return best_lag
        double_peak = float(acf[double_lag])
        double_confidence = double_peak / acf[0]
        if double_confidence < self.min_confidence:
            return best_lag
        expected_double_peak = 2.0 * peak - float(acf[0])
        excess = (double_peak - expected_double_peak) / float(acf[0])
        adjusted = excess - (1.0 - confidence)
        if adjusted >= self.octave_lock_margin:
            return double_lag
        return best_lag
