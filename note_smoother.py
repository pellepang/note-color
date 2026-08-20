"""Turns noisy per-hop pitch estimates into a stable displayed note.

Handles: silence/low-confidence gating, octave-error outlier rejection
(median filter in semitone space), and debounce/hysteresis before the
displayed note is allowed to change.
"""

import math
from collections import deque

from onset_detect import spectral_flux


class NoteSmoother:
    def __init__(self, cfg, sensitivity=1.0):
        self.median_window = cfg.MEDIAN_WINDOW
        self.debounce_hops = cfg.DEBOUNCE_HOPS
        self.silence_hops = cfg.SILENCE_HOPS
        self.base_rms_silence_threshold = cfg.RMS_SILENCE_THRESHOLD
        self.base_confidence_threshold = cfg.CONFIDENCE_THRESHOLD
        self.onset_rms_ratio = 10 ** (cfg.ONSET_RMS_JUMP_DB / 20.0)
        self.onset_flux_threshold = cfg.ONSET_FLUX_THRESHOLD
        self.set_sensitivity(sensitivity)

        self.history = deque(maxlen=self.median_window)
        self.candidate_note = None
        self.candidate_count = 0
        self.current_note = None  # (pitch_class, octave) or None
        self.silence_count = 0
        self.prev_rms = 0.0
        self.was_silent = True
        self.prev_spectrum = None

    def set_sensitivity(self, sensitivity):
        """Higher sensitivity lowers both gates, so quieter/softer playing
        is more likely to register. 1.0 reproduces the config.py defaults."""
        self.sensitivity = max(sensitivity, 0.01)
        self.rms_silence_threshold = self.base_rms_silence_threshold / self.sensitivity
        self.confidence_threshold = self.base_confidence_threshold / self.sensitivity

    def update(self, freq_hz, confidence, rms, spectrum):
        """Returns (pitch_class, octave, is_onset). pitch_class/octave are
        None when idle (silence or unpitched input). `spectrum` is this
        hop's pitch_detect.compute_spectrum() output, used only for the
        spectral-flux onset condition below -- prev_spectrum is tracked
        (and updated on every call, including this silence-gated early
        return) so flux history stays valid across a silence gap, the same
        way prev_rms already is."""
        if freq_hz is None or rms < self.rms_silence_threshold or confidence < self.confidence_threshold:
            self.history.clear()
            self.candidate_note = None
            self.candidate_count = 0
            self.silence_count += 1
            self.prev_rms = rms
            self.prev_spectrum = spectrum
            if self.silence_count >= self.silence_hops:
                self.current_note = None
                self.was_silent = True
            if self.current_note is None:
                return None, None, False
            return self.current_note[0], self.current_note[1], False

        self.silence_count = 0
        midi = 69 + 12 * math.log2(freq_hz / 440.0)
        self.history.append(midi)
        median_midi = sorted(self.history)[len(self.history) // 2]
        rounded = round(median_midi)
        pitch_class = rounded % 12
        octave = rounded // 12 - 1

        candidate = (pitch_class, octave)
        if candidate == self.candidate_note:
            self.candidate_count += 1
        else:
            self.candidate_note = candidate
            self.candidate_count = 1

        note_changed = False
        if self.candidate_count >= self.debounce_hops and candidate != self.current_note:
            self.current_note = candidate
            note_changed = True

        is_onset = False
        if note_changed or self.was_silent:
            is_onset = True
        elif self.prev_rms > 0 and rms / self.prev_rms >= self.onset_rms_ratio:
            is_onset = True
        elif spectral_flux(spectrum, self.prev_spectrum) >= self.onset_flux_threshold:
            is_onset = True

        self.was_silent = False
        self.prev_rms = rms
        self.prev_spectrum = spectrum

        if self.current_note is None:
            return None, None, is_onset
        return self.current_note[0], self.current_note[1], is_onset
