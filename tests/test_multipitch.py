import numpy as np

import chroma
import config
from multipitch import detect, select_window
from pitch_detect import compute_spectrum

SAMPLE_RATE = 22050


def freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=0.4, harmonics=(1.0,)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


def as_note_set(notes):
    return {(n.pitch_class, n.octave) for n in notes}


def test_single_pure_tone_detected_as_one_note():
    tone = make_tone(freq_for(9, 4))  # A4
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(9, 4)}


def test_major_triad_notes_all_detected():
    c = make_tone(freq_for(0, 4))
    e = make_tone(freq_for(4, 4))
    g = make_tone(freq_for(7, 4))
    mixed = c + e + g
    notes = detect(mixed, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 4), (4, 4), (7, 4)}


def test_own_harmonics_not_double_counted_as_separate_notes():
    # A single instrument's overtones (2nd = octave up, 3rd = octave+fifth
    # up) must collapse into its one fundamental, not read as a 3-note chord.
    tone = make_tone(freq_for(0, 3), harmonics=(1.0, 0.6, 0.4))  # C3 + overtones
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 3)}


def test_detected_notes_are_capped_at_max_notes():
    pitch_classes = [0, 2, 4, 5, 7, 9, 11, 1]  # 8 distinct notes, one octave
    tones = sum(make_tone(freq_for(pc, 4)) for pc in pitch_classes)
    notes = detect(tones, SAMPLE_RATE, max_notes=6)
    assert len(notes) <= 6


def test_each_note_carries_a_confidence_between_zero_and_one():
    tone = make_tone(freq_for(0, 4))
    notes = detect(tone, SAMPLE_RATE)
    assert len(notes) == 1
    assert 0.0 < notes[0].confidence <= 1.0


def test_silence_detects_no_notes():
    silence = np.zeros(2048)
    notes = detect(silence, SAMPLE_RATE)
    assert notes == []


def test_low_register_triad_garbled_at_real_live_window_size():
    # issue #63: C2+E2+G2's fundamentals (~65/82/98Hz) are only ~15-17Hz
    # apart -- closer together than the app's real live window
    # (config.WINDOW_SIZE, ~93ms) can resolve. Their Hann-window mainlobes
    # physically overlap and merge into one wrong-frequency peak, unlike
    # the same chord at test_major_triad_notes_all_detected()'s midrange
    # octave. This documents the underlying limitation select_window()
    # works around, not a regression to fix in detect() itself.
    duration = config.WINDOW_SIZE / SAMPLE_RATE
    harmonics = (1.0, 0.5, 0.3, 0.15)
    c = make_tone(freq_for(0, 2), duration=duration, harmonics=harmonics)
    e = make_tone(freq_for(4, 2), duration=duration, harmonics=harmonics)
    g = make_tone(freq_for(7, 2), duration=duration, harmonics=harmonics)
    notes = detect(c + e + g, SAMPLE_RATE)
    assert as_note_set(notes) != {(0, 2), (4, 2), (7, 2)}


def test_select_window_resolves_low_triad_the_short_live_window_cannot():
    # The same C2+E2+G2 chord, this time run through the real fix
    # (select_window() swapping to a longer window once bass_chroma shows
    # real low-frequency content) -- the longer window has enough
    # resolution to separate the fundamentals correctly.
    short_duration = config.WINDOW_SIZE / SAMPLE_RATE
    long_duration = config.MULTIPITCH_LOW_WINDOW_SIZE / SAMPLE_RATE
    harmonics = (1.0, 0.5, 0.3, 0.15)
    chord = [(0, 2), (4, 2), (7, 2)]

    short_mix = sum(make_tone(freq_for(pc, oc), duration=short_duration, harmonics=harmonics) for pc, oc in chord)
    long_mix = sum(make_tone(freq_for(pc, oc), duration=long_duration, harmonics=harmonics) for pc, oc in chord)

    spectrum = compute_spectrum(short_mix)
    main_chroma = chroma.fold(spectrum, SAMPLE_RATE)
    bass_chroma = chroma.fold_bass(spectrum, SAMPLE_RATE)

    window = select_window(short_mix, long_mix, main_chroma, bass_chroma, gate_ratio=config.MULTIPITCH_BASS_GATE_RATIO)
    assert window is long_mix

    notes = detect(window, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 2), (4, 2), (7, 2)}


def test_select_window_keeps_short_window_when_no_bass_content():
    # An ordinary midrange chord has no real energy in fold_bass()'s
    # <~250Hz band, so select_window() must not pay the extra-latency long
    # window's cost for it.
    short_duration = config.WINDOW_SIZE / SAMPLE_RATE
    c = make_tone(freq_for(0, 4), duration=short_duration)
    e = make_tone(freq_for(4, 4), duration=short_duration)
    short_mix = c + e
    long_mix = np.zeros(config.MULTIPITCH_LOW_WINDOW_SIZE)  # sentinel: must not be picked

    spectrum = compute_spectrum(short_mix)
    main_chroma = chroma.fold(spectrum, SAMPLE_RATE)
    bass_chroma = chroma.fold_bass(spectrum, SAMPLE_RATE)

    window = select_window(short_mix, long_mix, main_chroma, bass_chroma, gate_ratio=config.MULTIPITCH_BASS_GATE_RATIO)
    assert window is short_mix


def test_select_window_falls_back_to_short_window_on_silence():
    short_mix = np.zeros(config.WINDOW_SIZE)
    long_mix = np.zeros(config.MULTIPITCH_LOW_WINDOW_SIZE)
    main_chroma = np.zeros(12)
    bass_chroma = np.zeros(12)
    window = select_window(short_mix, long_mix, main_chroma, bass_chroma)
    assert window is short_mix


def test_louder_harmonic_still_pruned_regardless_of_peak_order():
    # issue #67: under real acoustic capture (mic/speaker frequency
    # response, room-reflection comb filtering), a note's own harmonic
    # overtone can carry MORE raw FFT magnitude than its own fundamental
    # -- confirmed against a real speaker->mic round trip and reproduced
    # here with a synthesized E4 whose 3rd harmonic partial (amplitude
    # 1.4) is louder than its own fundamental (amplitude 1.0). The old
    # pruning loop walked candidates in magnitude order, so that louder
    # harmonic got *accepted* before the fundamental was ever considered
    # -- and _is_harmonic_of() has no reverse-direction check ("is this
    # already-accepted candidate itself a harmonic of a lower note not
    # yet accepted"), so the true fundamental, arriving later and not
    # itself a harmonic of anything higher, got accepted too, producing
    # a phantom second note at the harmonic's pitch class (B5). Walking
    # candidates ascending by frequency instead (this fix) means the
    # real, lower fundamental always gets first claim on a slot, so its
    # louder-but-still-a-harmonic overtone reliably prunes against it
    # afterward regardless of which partial the FFT happened to weight
    # louder that hop.
    tone = make_tone(freq_for(4, 4), harmonics=(1.0, 0.3, 1.4, 0.2))  # E4, loud 3rd harmonic
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(4, 4)}


def test_moderate_frequency_jitter_on_a_harmonic_does_not_defeat_pruning():
    # issue #67 floated a hypothesis (explicitly unverified in the issue
    # itself): that a few cents of real-world frequency-estimate jitter
    # on the accepted fundamental could push a higher harmonic's
    # predicted frequency outside harmonic_tolerance_cents once projected
    # up by the harmonic number. Checked directly here: detuning a note's
    # own 3rd harmonic by 30 cents (already a generous simulated-jitter
    # budget -- more than plausible FFT-interpolation noise at this app's
    # bin resolution, ~5.4Hz at config.WINDOW_SIZE) still gets pruned
    # under the existing 35-cent tolerance. The tolerance itself was not
    # the bug; see test_louder_harmonic_still_pruned_regardless_of_peak_order
    # above for the actual confirmed root cause (evaluation order, not
    # tolerance width) -- cents are already a relative/logarithmic unit,
    # so a fixed cents tolerance does not need to scale with harmonic
    # number the way a fixed-Hz tolerance would.
    def make_detuned_tone(base_freq, cents_off, sample_rate=SAMPLE_RATE, duration=0.4):
        n = int(sample_rate * duration)
        t = np.arange(n) / sample_rate
        signal = np.sin(2 * np.pi * base_freq * t)
        signal += 0.5 * np.sin(2 * np.pi * base_freq * 2 * t)
        detuned_3rd = base_freq * 3 * (2 ** (cents_off / 1200))
        signal += (1 / 3) * np.sin(2 * np.pi * detuned_3rd * t)
        signal += 0.25 * np.sin(2 * np.pi * base_freq * 4 * t)
        return signal

    tone = make_detuned_tone(freq_for(4, 4), cents_off=30)
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(4, 4)}


def test_high_order_harmonic_near_miss_does_not_prune_a_real_independent_note():
    # issue #68 residual: _is_harmonic_of() used to check ANY integer
    # harmonic_number = round(freq / accepted_freq) with no upper bound.
    # A2 (110Hz) and B5 (987.77Hz) aren't in any small-integer relationship
    # a real single instrument's overtone series would plausibly produce
    # (harmonics 1-4, this app's own established convention -- see
    # chroma.HARMONIC_WEIGHTS/YIN_SUBHARMONIC_MAX_MULTIPLE) -- B5 just
    # happens to sit close to A2's 9th harmonic (110*9=990Hz, ~4 cents
    # away, well inside the 35-cent tolerance). Before capping
    # harmonic_max_number, that accidental high-order near-miss pruned B5
    # as "just A2's 9th harmonic" even though it's a real, independently
    # sounding note -- exactly the false-collision-risk-grows-with-density
    # pattern #68 reported. Reproduced empirically via
    # scripts/acoustic_pipeline_test.py's density suite (a 3-note voicing
    # of A2+E4+C5 additionally lost B5-ish content at high harmonic
    # numbers before this fix).
    a2 = make_tone(freq_for(9, 2), harmonics=(1.0, 0.5, 1 / 3, 0.25))
    b5 = make_tone(freq_for(11, 5))
    notes = detect(a2 + b5, SAMPLE_RATE)
    assert as_note_set(notes) == {(9, 2), (11, 5)}


def test_own_low_order_harmonics_still_pruned_after_capping_harmonic_number():
    # Companion to the test above: capping harmonic_max_number must not
    # weaken pruning for the harmonics that actually matter (1-4) -- a
    # note's own real overtones there still collapse into one detection,
    # same as test_own_harmonics_not_double_counted_as_separate_notes.
    tone = make_tone(freq_for(0, 3), harmonics=(1.0, 0.6, 0.4, 0.2))  # C3 + harmonics 2-4
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 3)}


def test_out_of_range_high_frequency_noise_produces_no_phantom_notes():
    # issue #74: a hi-hat's high-passed broadband noise (6-11kHz in the
    # acoustic test's synthesis, ~3-4 octaves above config.FMAX/DEFAULT_MAX_FREQ_HZ)
    # has real spectral peaks like any other signal -- peak-picking alone
    # can't tell a noise band's local maxima apart from a real note's
    # fundamental. Before this fix, detect() had no frequency-range bound
    # at all and reported these peaks as phantom notes at octave 8-9.
    n = 2048
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 1, n)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
    spectrum[freqs < 4000] = 0  # keep only content well above DEFAULT_MAX_FREQ_HZ
    signal = np.fft.irfft(spectrum, n)
    notes = detect(signal, SAMPLE_RATE)
    assert notes == []


def test_out_of_range_low_frequency_rumble_produces_no_phantom_notes():
    # Companion low-end case: a kick drum's sub-bass thump can sit below
    # config.FMIN/DEFAULT_MIN_FREQ_HZ entirely (this app's real low bound is
    # ~C2, not true sub-bass) -- must be filtered the same way as the
    # high-frequency case above, not just capped on one side.
    tone = make_tone(30.0)  # well below DEFAULT_MIN_FREQ_HZ (65Hz)
    notes = detect(tone, SAMPLE_RATE)
    assert notes == []


def test_frequency_range_bound_does_not_affect_in_range_chords():
    # The fix must not narrow the app's real target range -- every note in
    # this suite's existing chord tests (C2 through B5) must keep detecting
    # exactly as before. Spot-checked here with a chord spanning close to
    # both edges of the default range.
    low = make_tone(freq_for(0, 2))  # C2, ~65.4Hz -- just above DEFAULT_MIN_FREQ_HZ
    high = make_tone(freq_for(11, 5))  # B5, ~987.8Hz -- just below DEFAULT_MAX_FREQ_HZ
    notes = detect(low + high, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 2), (11, 5)}


def test_dense_six_note_chord_all_survive_when_not_harmonically_colliding():
    # issue #68: raw peak-picking/pruning must not silently drop real
    # notes as note density increases, for an ordinary, musically
    # plausible chord voicing -- as opposed to a voicing deliberately
    # chosen (or, per the acoustic density test in
    # scripts/acoustic_pipeline_test.py, accidentally landed on) so that
    # one note's frequency nearly coincides with another note's own
    # harmonic (e.g. a root and a fifth an octave+fifth above it, a 3:1
    # ratio -- see docs/DECISIONS.md for why that specific ambiguity is
    # not fixed by this issue: a single-hop integer-frequency-ratio test
    # genuinely cannot tell "this peak is note X's own 3rd harmonic"
    # apart from "this peak is a real, independent note that happens to
    # sit near note X's 3rd harmonic," and no combination of ordering or
    # magnitude reasoning tried while fixing #67 could resolve that
    # without reopening #67 itself -- confirmed empirically, not assumed).
    # C3 D3 F3 G#3 B3 C#4 -- 6 notes, no pair within 60 cents of any
    # small-integer frequency ratio, so no harmonic-collision ambiguity
    # is in play; this isolates the density/candidate-limit behavior
    # this issue is actually about.
    voicing = [(0, 3), (2, 3), (5, 3), (8, 3), (11, 3), (1, 4)]
    harmonics = (1.0, 0.5, 1 / 3, 0.25)
    mix = sum(make_tone(freq_for(pc, oc), harmonics=harmonics) for pc, oc in voicing)
    notes = detect(mix, SAMPLE_RATE)
    assert as_note_set(notes) == set(voicing)
