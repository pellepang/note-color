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
        bpm = 60.0 / (best_lag * self.hop_seconds)
        return float(min(max(bpm, self.min_bpm), self.max_bpm))
