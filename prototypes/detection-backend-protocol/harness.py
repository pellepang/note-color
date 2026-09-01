"""Harness: run YinBackend and PyinLiteBackend (backends.py) side by side
against synthesized tones, through the exact same MonoPitchBackend.detect()
call shape main.analysis_loop() would use post-DetectionBackend-adoption.

Run: .venv/bin/python prototypes/detection-backend-protocol/harness.py
"""

import os
import sys

import numpy as np

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import config  # noqa: E402
from pitch_detect import compute_spectrum  # noqa: E402

from backends import PyinLiteBackend, YinBackend  # noqa: E402

SAMPLE_RATE = config.SAMPLE_RATE


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=config.WINDOW_SIZE / SAMPLE_RATE, harmonics=(1.0,)):
    """Same synthesis convention as tests/test_pitch_detect.py's make_tone()
    -- duration defaults to exactly config.WINDOW_SIZE samples so `ring`
    below matches the app's real analysis window size."""
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


# Three cases, chosen to exercise different registers/harmonic profiles --
# the middle two are exactly tests/test_pitch_detect.py's own issue #69
# octave-2 low-register profiles (fundamental-dominant, and the adversarial
# fundamental-silent/3rd-harmonic-dominant case), since that's this app's
# one documented, real octave-doubling failure history.
CASES = [
    ("A4, plain sine (440Hz)", 440.0, (1.0,)),
    ("G#2, harmonic-rich fundamental-dominant (92.5Hz, issue #69 profile)", 92.5, (1.0, 0.5, 1.0 / 3, 0.25)),
    ("C2, adversarial weak-fundamental/3rd-harmonic-dominant (65.41Hz, issue #69 regression profile)",
     65.41, (0.0, 0.1, 1.0, 0.2)),
]


def cents_off(detected, true_freq):
    if detected is None:
        return None
    return 1200 * np.log2(detected / true_freq)


def main():
    yin = YinBackend()
    pyin_lite = PyinLiteBackend()

    print(f"{'case':70s} {'backend':10s} {'freq_hz':>10s} {'cents_off':>10s} {'conf/voice_p':>13s}")
    print("-" * 118)

    for label, freq, harmonics in CASES:
        tone = make_tone(freq, harmonics=harmonics)
        spectrum = compute_spectrum(tone)

        for name, backend in [("YIN", yin), ("pYIN-lite", pyin_lite)]:
            det_freq, conf = backend.detect(tone, spectrum, SAMPLE_RATE)
            off = cents_off(det_freq, freq)
            off_str = f"{off:+.0f}" if off is not None else "n/a"
            freq_str = f"{det_freq:.2f}" if det_freq is not None else "None"
            print(f"{label:70s} {name:10s} {freq_str:>10s} {off_str:>10s} {conf:>13.3f}")

        # Extra diagnostic: pYIN-lite's full candidate distribution, to show
        # the multi-candidate structure a single confidence scalar can't
        # express -- this is the concrete thing a probabilistic backend
        # can expose that YIN's interface structurally can't.
        dist = pyin_lite.candidate_distribution(tone, spectrum, SAMPLE_RATE)
        top = list(dist.items())[:3]
        top_str = ", ".join(f"{f:.1f}Hz={p:.2f}" for f, p in top)
        print(f"{'':70s} {'(pYIN-lite candidates)':10s} {top_str}")
        print()

    print("=" * 118)
    print("Ground truth frequencies:", ", ".join(f"{label.split(',')[0]}={freq}Hz" for label, freq, _ in CASES))


if __name__ == "__main__":
    main()
