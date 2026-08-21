import numpy as np

from onset_detect import chroma_flux, spectral_flux
from pitch_detect import compute_spectrum
import config


# --- spectral_flux ----------------------------------------------------

def test_spectral_flux_zero_with_no_previous_spectrum():
    spectrum = np.array([1 + 1j, 2 + 2j, 3 + 3j])
    assert spectral_flux(spectrum, None) == 0.0


def test_spectral_flux_zero_between_identical_frames():
    spectrum = np.array([1 + 1j, 2 + 2j, 3 + 3j])
    assert spectral_flux(spectrum.copy(), spectrum.copy()) == 0.0


def test_spectral_flux_positive_on_a_new_transient():
    prev = np.array([0.1, 0.1, 0.1], dtype=np.complex128)
    now = np.array([0.1, 5.0, 0.1], dtype=np.complex128)  # one bin jumps up
    assert spectral_flux(now, prev) > 0.0


def test_spectral_flux_ignores_pure_decay():
    # Every bin's magnitude decreased -- half-wave rectification means a
    # note simply dying away registers no novelty at all.
    prev = np.array([5.0, 5.0, 5.0], dtype=np.complex128)
    now = np.array([1.0, 1.0, 1.0], dtype=np.complex128)
    assert spectral_flux(now, prev) == 0.0


def test_spectral_flux_zero_on_shape_mismatch():
    prev = np.array([1.0, 1.0], dtype=np.complex128)
    now = np.array([1.0, 1.0, 1.0], dtype=np.complex128)
    assert spectral_flux(now, prev) == 0.0


def test_spectral_flux_stays_below_threshold_for_a_real_size_steady_tone():
    # Issue #66 repro: a real config.WINDOW_SIZE spectrum pair representing
    # a genuinely SUSTAINED tone -- same amplitude, only the natural
    # hop-to-hop phase advance a real ring buffer sees as it slides forward
    # by one config.BLOCK_SIZE hop. Nothing about this is a new note
    # attack, so the raw flux measure between them must stay comfortably
    # under config.ONSET_FLUX_THRESHOLD -- before the fix, the unnormalized
    # raw sum sat ~600x over threshold for this exact case.
    sr, w, hop = config.SAMPLE_RATE, config.WINDOW_SIZE, config.BLOCK_SIZE
    t = np.arange(w) / sr

    def tone(amp, freq=220.0, phase=0.0):
        return amp * np.sin(2 * np.pi * freq * t + phase)

    phase_shift = 2 * np.pi * 220.0 * hop / sr
    s1 = compute_spectrum(tone(0.2, phase=0.0))
    s2 = compute_spectrum(tone(0.2, phase=phase_shift))

    flux = spectral_flux(s2, s1)
    assert flux < config.ONSET_FLUX_THRESHOLD / 2  # comfortably below, not just barely


def test_spectral_flux_clears_threshold_for_a_real_size_genuine_attack():
    # Same real WINDOW_SIZE scale, but this time a genuine attack (silence
    # -> a fresh tone) -- must still clear the threshold easily, so the
    # normalization fix doesn't just suppress flux into uselessness.
    sr, w = config.SAMPLE_RATE, config.WINDOW_SIZE
    t = np.arange(w) / sr

    silence = compute_spectrum(np.zeros(w))
    attack = compute_spectrum(0.2 * np.sin(2 * np.pi * 440.0 * t))

    flux = spectral_flux(attack, silence)
    assert flux >= config.ONSET_FLUX_THRESHOLD


# --- chroma_flux --------------------------------------------------------

def test_chroma_flux_zero_with_no_previous_chroma():
    chroma = np.ones(12)
    assert chroma_flux(chroma, None) == 0.0


def test_chroma_flux_zero_between_identical_frames():
    chroma = np.arange(12, dtype=np.float64)
    assert chroma_flux(chroma.copy(), chroma.copy()) == 0.0


def test_chroma_flux_positive_when_a_new_pitch_class_appears():
    prev = np.zeros(12)
    now = np.zeros(12)
    now[3] = 1.0
    assert chroma_flux(now, prev) == 1.0


def test_chroma_flux_ignores_pure_decay():
    prev = np.full(12, 2.0)
    now = np.full(12, 1.0)
    assert chroma_flux(now, prev) == 0.0
