from note_smoother import NoteSmoother
import config


def freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def feed(smoother, pitch_class, octave, count, confidence=0.9, rms=0.1):
    result = None
    for _ in range(count):
        result = smoother.update(freq_for(pitch_class, octave), confidence, rms)
    return result


def test_stable_note_locks_in():
    s = NoteSmoother(config)
    pitch_class, octave, is_onset = feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    assert (pitch_class, octave) == (9, 4)


def test_single_frame_octave_blip_does_not_flicker():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    # single-frame octave error blip
    pitch_class, octave, _ = s.update(freq_for(9, 5), 0.9, 0.1)
    assert (pitch_class, octave) == (9, 4)
    # keep playing the real note, still stable
    pitch_class, octave, _ = feed(s, 9, 4, 3)
    assert (pitch_class, octave) == (9, 4)


def test_brief_silence_gap_does_not_reset_note():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    # gap shorter than SILENCE_HOPS
    for _ in range(config.SILENCE_HOPS - 1):
        pitch_class, octave, _ = s.update(None, 0.0, 0.0)
    assert (pitch_class, octave) == (9, 4)


def test_sustained_silence_goes_idle():
    s = NoteSmoother(config)
    feed(s, 9, 4, config.DEBOUNCE_HOPS + config.MEDIAN_WINDOW)
    pitch_class, octave, _ = None, None, None
    for _ in range(config.SILENCE_HOPS + 1):
        pitch_class, octave, _ = s.update(None, 0.0, 0.0)
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
        pitch_class, octave, is_onset = s.update(freq_for(0, 5), 0.9, 0.1)
        onset_seen = onset_seen or is_onset

    assert (pitch_class, octave) == (0, 5)
    assert onset_seen
