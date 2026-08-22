import numpy as np
import pytest

from pitch_detect import compute_spectrum, detect_pitch

SAMPLE_RATE = 22050


def test_compute_spectrum_matches_manual_rfft_at_given_size():
    window = np.arange(2048, dtype=np.float64)
    spectrum = compute_spectrum(window, size=4096)
    expected = np.fft.rfft(window, 4096)
    assert np.allclose(spectrum, expected)


def test_compute_spectrum_defaults_to_next_pow2_of_double_length():
    window = np.arange(2048, dtype=np.float64)
    spectrum = compute_spectrum(window)
    assert len(spectrum) == 4096 // 2 + 1


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=0.1, harmonics=(1.0,)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


@pytest.mark.parametrize("freq", [110.0, 220.0, 440.0, 880.0])
def test_pure_tone_detected_within_10_cents(freq):
    tone = make_tone(freq)
    detected, confidence = detect_pitch(tone, SAMPLE_RATE, compute_spectrum(tone))
    assert detected is not None
    cents_off = 1200 * np.log2(detected / freq)
    assert abs(cents_off) < 10
    assert confidence > 0.5


def test_tone_with_harmonics_detected():
    tone = make_tone(220.0, harmonics=(1.0, 0.5, 0.25))
    detected, confidence = detect_pitch(tone, SAMPLE_RATE, compute_spectrum(tone))
    assert detected is not None
    cents_off = 1200 * np.log2(detected / 220.0)
    assert abs(cents_off) < 10


@pytest.mark.parametrize("freq", [110.0, 65.4, 69.3, 73.4, 92.5, 103.8])
def test_octave2_harmonic_rich_tone_not_octave_doubled(freq):
    """Issue #69: A2/C2/C#2/D2/F#2/G#2, synthesized with real harmonic
    content (harmonics 1-4, weighted like chroma.HARMONIC_WEIGHTS), used
    to lock onto the note's own 2nd or 4th harmonic instead of the true
    fundamental -- a strong-harmonic-relative-to-fundamental low tone lets
    YIN's ascending threshold scan find a confident sub-threshold dip at
    an exact submultiple of the true period before ever reaching the true
    (longer) fundamental lag. Confirm detect_pitch() lands on the true
    fundamental, not a harmonic multiple of it."""
    tone = make_tone(freq, duration=2048 / SAMPLE_RATE, harmonics=(1.0, 0.5, 1.0 / 3, 0.25))
    detected, confidence = detect_pitch(tone, SAMPLE_RATE, compute_spectrum(tone))
    assert detected is not None
    cents_off = 1200 * np.log2(detected / freq)
    assert abs(cents_off) < 100, f"expected ~{freq}Hz, got {detected}Hz ({cents_off:.0f} cents off)"


@pytest.mark.parametrize("freq", [65.41, 69.30, 73.42, 77.78, 82.41, 87.31,
                                   92.50, 98.00, 103.83, 110.00, 116.54, 123.47])
def test_octave2_silent_fundamental_dominant_3rd_harmonic_not_octave_doubled(freq):
    """Issue #69, adversarial regression case: a MUCH more extreme
    harmonic-rich profile than test_octave2_harmonic_rich_tone_not_octave_
    doubled above -- fundamental entirely absent, 3rd harmonic dominant
    (harmonics=(0.0, 0.1, 1.0, 0.2)). The existing test's profile
    (fundamental-dominant, chroma.HARMONIC_WEIGHTS-shaped) turns out to
    already detect correctly even with the subharmonic check fully
    disabled (subharmonic_max_multiple=0) -- it doesn't actually exercise
    the fix. This profile does: with the check disabled it reliably
    octave-doubles (or worse) across all 12 octave-2 pitch classes; with
    it enabled (default margin) it must land on the true fundamental."""
    tone = make_tone(freq, harmonics=(0.0, 0.1, 1.0, 0.2))
    spectrum = compute_spectrum(tone)

    detected_without_check, _ = detect_pitch(tone, SAMPLE_RATE, spectrum, subharmonic_max_multiple=0)
    without_off = 1200 * np.log2(detected_without_check / freq) if detected_without_check else None
    assert without_off is not None and abs(without_off) > 100, (
        f"test profile stopped reproducing the original bug at {freq}Hz "
        f"(off={without_off}) -- this adversarial profile needs updating"
    )

    detected, confidence = detect_pitch(tone, SAMPLE_RATE, spectrum)
    assert detected is not None
    cents_off = 1200 * np.log2(detected / freq)
    assert abs(cents_off) < 100, f"expected ~{freq}Hz, got {detected}Hz ({cents_off:.0f} cents off)"


def _make_hum_noise_tone(freq, sample_rate=SAMPLE_RATE, duration=2048 / SAMPLE_RATE,
                          harmonics=(1.0, 0.5, 1.0 / 3, 0.25), noise_amp=0.05,
                          hum_amp=0.3, hum_freq=60.0, seed=0):
    """A dominant-fundamental tone (chroma.HARMONIC_WEIGHTS-shaped) plus
    additive white noise and a low-frequency hum component (60Hz + its
    2nd harmonic, standing in for mains hum/room rumble/mic self-noise) --
    the plausible real-mic-coloration mechanism behind issue #69's second
    (regression) round: this hum content sits near tau_max (the fmin
    edge) and can produce a coincidentally deep CMND dip there,
    independent of the note actually being played."""
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    signal += hum_amp * np.sin(2 * np.pi * hum_freq * t)
    signal += hum_amp * 0.5 * np.sin(2 * np.pi * hum_freq * 2 * t)
    rng = np.random.default_rng(seed)
    signal = signal + rng.normal(0, noise_amp, n)
    return signal.astype(np.float64)


@pytest.mark.parametrize("freq", [130.81, 138.59, 146.83, 155.56, 164.81, 174.61,
                                   185.00, 196.00, 207.65, 220.00, 233.08, 246.94])
def test_octave3_hum_and_noise_does_not_flip_already_correct_detection(freq):
    """Issue #69's reopened regression: a real-mic re-verification found
    the original fix (subharmonic_margin=0.5) actively broke previously-
    correct detections rather than just failing to fix new ones. Root
    cause (see docs/DECISIONS.md): ordinary broadband low-frequency
    content in a real recording (mains hum, room rumble, mic self-noise)
    can produce its own coincidentally-deep CMND dip near tau_max, and a
    margin of 0.5 (needing only a 2x-deeper dip to switch) was nowhere
    near strict enough to reject it -- misreading an already-correct
    octave-3 detection down an octave (or more).

    Reproduces the mechanism directly: octave-3 was chosen because that's
    where 2x/3x/4x of an octave-3 note's own true tau still lands inside
    tau_max (~339 samples for the default fmin=65Hz) -- octave-2 notes'
    own tau is already close to tau_max, so their multiples always exceed
    it and the subharmonic check is structurally a no-op for them
    regardless of margin, which is also why the original real-world
    regression report's affected notes cluster there. Across 20 noise
    seeds per note, every detection must stay within 50 cents of the true
    note -- the pre-regression-fix margin (0.5) fails this consistently
    for several of these notes; the margin recalibration (0.1) must not."""
    ok = 0
    n_trials = 20
    for seed in range(n_trials):
        tone = _make_hum_noise_tone(freq, seed=seed)
        spectrum = compute_spectrum(tone)
        detected, _ = detect_pitch(tone, SAMPLE_RATE, spectrum)
        if detected is not None:
            cents_off = 1200 * np.log2(detected / freq)
            if abs(cents_off) < 50:
                ok += 1
    # A single failure out of 20 is within this base algorithm's own noise
    # robustness limits (confirmed unrelated to the subharmonic check: it
    # reproduces identically with the check fully disabled) -- the bar
    # here is "the subharmonic check doesn't make a noisy-but-correct
    # detection systematically worse," not perfect noise immunity.
    assert ok >= n_trials - 1, f"{freq}Hz: only {ok}/{n_trials} stayed within 50 cents"


def test_silence_returns_none():
    silence = np.zeros(2048)
    detected, confidence = detect_pitch(silence, SAMPLE_RATE, compute_spectrum(silence))
    assert detected is None


def test_white_noise_low_confidence_or_none():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 1, 2048)
    detected, confidence = detect_pitch(noise, SAMPLE_RATE, compute_spectrum(noise))
    assert detected is None or confidence < 0.5
