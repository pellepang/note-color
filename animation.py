"""Smooths the displayed color: exponential crossfade toward the current
target note color, plus a decaying brightness pulse on note onsets."""

import math


class ColorAnimator:
    def __init__(self, tau_ms=100, pulse_decay_ms=200, pulse_boost=0.15):
        self.tau = max(tau_ms, 1) / 1000.0
        self.pulse_decay = max(pulse_decay_ms, 1) / 1000.0
        self.pulse_boost = pulse_boost
        self.current_rgb = [0.0, 0.0, 0.0]
        self.pulse_env = 0.0

    def update(self, dt, target_rgb, is_onset):
        alpha = 1.0 - math.exp(-dt / self.tau)
        for i in range(3):
            self.current_rgb[i] += (target_rgb[i] - self.current_rgb[i]) * alpha

        if is_onset:
            self.pulse_env = 1.0
        else:
            self.pulse_env *= math.exp(-dt / self.pulse_decay)

        boost = 1.0 + self.pulse_boost * self.pulse_env
        return tuple(int(max(0, min(255, c * boost))) for c in self.current_rgb)
