import numpy as np

from onset_detect import chroma_flux, spectral_flux


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
