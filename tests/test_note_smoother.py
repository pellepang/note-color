import numpy as np

from note_smoother import NoteSmoother
from pitch_detect import compute_spectrum
import config

DUMMY_SPECTRUM = np.zeros(5)


def freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def feed(smoother, pitch_class, octave, count, confidence=0.9, rms=0.1, spectrum=DUMMY_SPECTRUM):
    result = None
    for _ in range(count):
        result = smoother.update(freq_for(pitch_class, octave), confidence, rms, spectrum)
    return result


def test_stable_note_locks_in():
    s = NoteSmoother(config)
    pitch_class, octave, is_onset = feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    assert (pitch_class, octave) == (9, 4)


def test_single_frame_octave_blip_does_not_flicker():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    # single-frame octave error blip
    pitch_class, octave, _ = s.update(freq_for(9, 5), 0.9, 0.1, DUMMY_SPECTRUM)
    assert (pitch_class, octave) == (9, 4)
    # keep playing the real note, still stable
    pitch_class, octave, _ = feed(s, 9, 4, 3)
    assert (pitch_class, octave) == (9, 4)


def test_brief_silence_gap_does_not_reset_note():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    # gap shorter than SILENCE_HOPS
    for _ in range(config.SILENCE_HOPS - 1):
        pitch_class, octave, _ = s.update(None, 0.0, 0.0, DUMMY_SPECTRUM)
    assert (pitch_class, octave) == (9, 4)


def test_sustained_silence_goes_idle():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    pitch_class, octave, _ = None, None, None
    for _ in range(config.SILENCE_HOPS + 1):
        pitch_class, octave, _ = s.update(None, 0.0, 0.0, DUMMY_SPECTRUM)
    assert pitch_class is None and octave is None


def test_higher_sensitivity_registers_a_quieter_note():
    quiet_rms = config.RMS_SILENCE_THRESHOLD * 1.5  # passes the default gate, but not a stricter one

    default = NoteSmoother(config)
    pitch_class, octave, _ = feed(default, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW, rms=quiet_rms)
    assert (pitch_class, octave) == (9, 4)

    less_sensitive = NoteSmoother(config, sensitivity=0.1)
    pitch_class, octave, _ = feed(less_sensitive, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW, rms=quiet_rms)
    assert (pitch_class, octave) == (None, None)


def test_genuine_note_change_registers_with_onset():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)

    onset_seen = False
    pitch_class = octave = None
    for _ in range(config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW):
        pitch_class, octave, is_onset = s.update(freq_for(0, 5), 0.9, 0.1, DUMMY_SPECTRUM)
        onset_seen = onset_seen or is_onset

    assert (pitch_class, octave) == (0, 5)
    assert onset_seen


def test_steady_real_size_tone_only_onsets_on_the_initial_attack():
    # Issue #66: with the unnormalized raw spectral_flux() sum, a
    # perfectly sustained real-size tone misfired is_onset on nearly
    # every hop (100% in the reported live repro) because the raw flux
    # between two hops -- differing only by the natural phase advance a
    # sliding ring buffer sees -- sat orders of magnitude over
    # config.ONSET_FLUX_THRESHOLD.
    #
    # Feed a totally fresh NoteSmoother a long run of real
    # config.WINDOW_SIZE spectra for one sustained tone -- same
    # amplitude, same note, same rms throughout, only phase advancing
    # hop to hop (exactly the live scenario the bug report described).
    # The only *legitimate* onsets in a run like this are right at the
    # start: the very first hop (silence -> sound) and the debounce
    # lock-in a few hops later once the candidate note is confirmed --
    # both real, separate mechanisms from spectral_flux, not part of
    # this bug. Once the note is locked in and sustained, nothing about
    # a steady tone should ever re-trigger is_onset again -- that
    # "misfires on nearly every hop" was exactly the reported bug.
    sr, w, hop_size = config.SAMPLE_RATE, config.WINDOW_SIZE, config.BLOCK_SIZE
    freq = freq_for(9, 4)  # A4
    t = np.arange(w) / sr
    rms = 0.1

    s = NoteSmoother(config)
    onset_flags = []
    lock_in_hop = config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW
    n_hops = lock_in_hop + 60
    for i in range(n_hops):
        phase = 2 * np.pi * freq * (i * hop_size) / sr
        spectrum = compute_spectrum(0.2 * np.sin(2 * np.pi * freq * t + phase))
        pitch_class, octave, is_onset = s.update(freq, 0.9, rms, spectrum)
        onset_flags.append(is_onset)

    assert (pitch_class, octave) == (9, 4)
    assert onset_flags[0] is True  # the initial attack out of silence
    # No onset anywhere in the long sustained tail well past lock-in --
    # this is the actual regression check.
    assert onset_flags[lock_in_hop:] == [False] * (len(onset_flags) - lock_in_hop)


# --- issue #70: onset_backdate_hops ---------------------------------------

def test_onset_backdate_hops_set_on_genuine_note_change():
    """A note-change promotion always lands on the exact hop where
    candidate_count first reaches debounce_hops -- so the true attack
    precedes it by exactly debounce_hops - 1 hops. This is what
    duration_tracker.py's onset_backdate mechanism corrects for.

    Uses a fresh smoother's very first note (empty history, was_silent) --
    i.e. a note attack out of real silence, exactly issue #70's actual
    scenario (scripts/acoustic_pipeline_test.py's rhythm suite always
    separates notes with real silence gaps). A same-key legato transition
    straight from one already-locked-in note to another without any
    silence in between pays extra MEDIAN_WINDOW-driven delay on top of
    debounce (the median history isn't cleared until a hop actually goes
    silent) -- out of scope here; onset_backdate only ever claims to
    correct the debounce portion."""
    s = NoteSmoother(config)
    for _ in range(config.DEBOUNCE_HOPS - 1):
        pitch_class, octave, is_onset = s.update(freq_for(0, 5), 0.9, 0.1, DUMMY_SPECTRUM)
        # was_silent alone can still fire is_onset=True here (the "coming
        # out of silence" signal, independent of note-change promotion),
        # but pitch_class stays None until debounce actually promotes a
        # candidate -- main.py only ever feeds DurationTracker a note once
        # pitch_class is non-None, so this early is_onset is never acted on.
        assert pitch_class is None
        assert s.onset_backdate_hops == 0  # candidate hasn't been promoted yet
    pitch_class, octave, is_onset = s.update(freq_for(0, 5), 0.9, 0.1, DUMMY_SPECTRUM)
    assert (pitch_class, octave) == (0, 5)
    assert is_onset is True
    assert s.onset_backdate_hops == config.DEBOUNCE_HOPS - 1  # the promotion hop itself

    pitch_class, octave, is_onset = s.update(freq_for(0, 5), 0.9, 0.1, DUMMY_SPECTRUM)
    assert is_onset is False
    assert s.onset_backdate_hops == 0  # back to 0 on the very next hop -- no longer a change


def test_onset_backdate_hops_zero_for_rms_jump_reattack():
    # A same-pitch RMS-jump re-attack never has to rebuild candidate_count
    # (the candidate never changed), so there's no debounce buildup delay
    # to backdate for.
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW, rms=0.05)
    loud_rms = 0.05 * (10 ** (config.ONSET_RMS_JUMP_DB / 20.0)) * 1.5
    pitch_class, octave, is_onset = s.update(freq_for(9, 4), 0.9, loud_rms, DUMMY_SPECTRUM)
    assert (pitch_class, octave) == (9, 4)
    assert is_onset is True
    assert s.onset_backdate_hops == 0


def test_spectral_flux_triggers_onset_without_note_change_or_rms_jump():
    s = NoteSmoother(config)
    # Lock in a stable note first, with a flat (zero) spectrum every hop so
    # no flux has fired yet.
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW, rms=0.1, spectrum=np.zeros(64))

    # Same rms (no jump), same note (no change), no silence -- but a big
    # spectral shift between two consecutive hops should still fire the
    # flux-triggered onset condition on its own.
    quiet_spectrum = np.zeros(64)
    loud_spectrum = np.full(64, 10.0)
    s.update(freq_for(9, 4), 0.9, 0.1, quiet_spectrum)

    from onset_detect import spectral_flux
    assert spectral_flux(loud_spectrum, quiet_spectrum) >= config.ONSET_FLUX_THRESHOLD

    pitch_class, octave, is_onset = s.update(freq_for(9, 4), 0.9, 0.1, loud_spectrum)
    assert (pitch_class, octave) == (9, 4)
    assert is_onset is True
    assert s.onset_backdate_hops == 0  # no candidate change, so no buildup delay to correct for
