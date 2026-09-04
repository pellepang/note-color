"""Tests for the subtractive synth engine (map #99, ticket #113,
implementing research #103 and decision #111).

**The machine is muted; nothing here listens to anything.** Every claim
this engine makes is a numerical one and is checked numerically:
oscillator cleanliness by FFT against an alias floor, the filter against
the analytic response of its own coefficients through `scipy.signal.
freqz`, envelope stages sample by sample, and -- the test that actually
justifies the whole resumable-voice design -- rendering one block of
1024 versus two blocks of 512 and requiring the samples to match, which
no voice with a reset phase, `zi` or envelope stage can pass.

Two measurement notes, both learned the hard way while writing this:

- The alias-floor tests must exclude a *generous* band around each
  harmonic (`HARMONIC_GUARD_HZ`), not a few bins. A Hann window's
  leakage skirt around a strong low-order harmonic is tens of dB above
  the true alias floor, so a narrow guard measures the window rather
  than the oscillator -- it reports ~-37 dBc for a table that is really
  at -113 dBc.
- `resonance_to_damping()`'s peak-gain identity (peak = 1/k) is a
  high-Q approximation. At k = sqrt(2) (resonance 0, Butterworth) there
  is no peak at all, so that end is asserted as flatness instead.

Per this repo's "pure logic unit-tested, real I/O smoke-tested"
convention, `SynthVoice.render()` is exercised here as pure array math;
nothing opens an audio device.
"""

import math

import numpy as np
import pytest

import config
import patch_format
import synth_engine
from sound_engine import NoteOn, frequency_for
from synth_engine import (
    ATTACK, DECAY, DELAY, DONE, HOLD, RELEASE, SUSTAIN,
    DahdsrEnvelope, Lfo, PINK_A, PINK_B, SynthEngine, SynthUnavailable, SynthVoice,
    band_partials, band_top_hz, build_tables, default_patch, lfo_shape, mip_level_for,
    modulated_cutoff, oscillator_frequency, phase_array, pulse_from_saw, read_table,
    resonance_to_damping, svf_coefficients, svf_damping_coefficients, table_lookup,
    table_waveform, tables_for,
)

SR = 44100
HARMONIC_GUARD_HZ = 20.0

scipy_signal = pytest.importorskip("scipy.signal")


# --------------------------------------------------------------------------
# SciPy isolation (#111): refuse to open, never open filterless
# --------------------------------------------------------------------------

def test_scipy_is_not_imported_at_module_scope():
    """The librosa/music21 idiom: importing this module must cost nothing
    on an install that never opens a synth, so the import lives inside
    `_signal()` and not at the top of the file."""
    source = open(synth_engine.__file__).read()
    header = source.split("# ---", 1)[0]
    assert "import scipy" not in header
    assert "from scipy" not in header


def test_require_scipy_raises_with_the_install_line(monkeypatch):
    """Missing SciPy => `SynthUnavailable` carrying the install hint, so
    a refusing surface can print it verbatim."""
    monkeypatch.setattr(synth_engine, "_SIGNAL", None)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def no_scipy(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError("no scipy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(__import__("builtins").__dict__, "__import__", no_scipy)
    try:
        assert synth_engine.scipy_available() is False
        with pytest.raises(SynthUnavailable) as excinfo:
            synth_engine.require_scipy()
        assert synth_engine.INSTALL_HINT in str(excinfo.value)
        with pytest.raises(SynthUnavailable):
            SynthEngine()
    finally:
        monkeypatch.undo()
        synth_engine._SIGNAL = None


def test_scipy_available_is_true_here_and_never_raises():
    assert synth_engine.scipy_available() is True
    assert synth_engine.require_scipy() is True


# --------------------------------------------------------------------------
# Mip-mapped wavetables (#103 recommendation 2)
# --------------------------------------------------------------------------

def test_square_reads_the_saw_table():
    assert table_waveform("square") == "saw"
    for waveform in ("saw", "triangle", "sine"):
        assert table_waveform(waveform) == waveform


def test_band_partials_never_exceed_nyquist_at_the_top_of_the_band():
    """The whole point of a mip band: the top note of the band must still
    be alias-free, so its highest partial has to stay under Nyquist
    there.

    Only checked for the bands a real note can actually reach. The
    topmost bands exist as headroom for a pitch LFO past MIDI 127 and
    have their *own fundamental* above Nyquist, where "alias-free" has no
    meaning left -- there the table degrades to a single partial (a
    sine), which is the correct degenerate answer rather than a
    violation."""
    nyquist = SR * 0.5
    for band in range(config.SYNTH_MIP_BANDS):
        partials = band_partials(band, SR, config.SYNTH_TABLE_SIZE)
        assert partials >= 1
        assert partials <= config.SYNTH_TABLE_SIZE // 4
        if band_top_hz(band) < nyquist:
            assert partials * band_top_hz(band) <= nyquist + 1e-9
        else:
            assert partials == 1


def test_band_partials_halve_as_the_bands_climb():
    counts = [band_partials(b, SR, config.SYNTH_TABLE_SIZE) for b in range(6)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > counts[-1]


def test_mip_level_covers_the_whole_midi_range():
    """Every MIDI note must land on a band whose top note is at or above
    it -- otherwise that note reads a table holding partials it cannot
    afford."""
    for pitch in range(128):
        freq = frequency_for(pitch)
        level = mip_level_for(freq)
        assert 0 <= level < config.SYNTH_MIP_BANDS
        assert band_top_hz(level) >= freq - 1e-6


def test_mip_level_steps_at_the_octave_boundaries():
    base = synth_engine.MIP_BASE_HZ
    assert mip_level_for(base) == 0
    assert mip_level_for(base * 1.99) == 0
    assert mip_level_for(base * 2.01) == 1
    assert mip_level_for(base * 4.01) == 2
    assert mip_level_for(base / 100.0) == 0            # clamped below
    assert mip_level_for(base * 2.0 ** 40) == config.SYNTH_MIP_BANDS - 1  # clamped above


def test_build_tables_shape_and_bandlimiting():
    tables = build_tables("saw", SR, size=1024, bands=4)
    assert tables.shape == (4, 1024)
    for band in range(4):
        spectrum = np.abs(np.fft.rfft(tables[band]))
        partials = band_partials(band, SR, 1024)
        # nothing above the band's own partial count
        assert spectrum[partials + 1:].max() < 1e-9
        assert spectrum[1:partials + 1].max() > 1e-3


@pytest.mark.parametrize("waveform,expect", [
    ("sine", lambda k: 1.0 if k == 1 else 0.0),
    ("saw", lambda k: 2.0 / (math.pi * k)),
    ("triangle", lambda k: (8.0 / math.pi ** 2) / k ** 2 if k % 2 else 0.0),
])
def test_table_partial_amplitudes_match_the_ideal_series(waveform, expect):
    """The table is an inverse FFT of an exact harmonic series, so its
    own forward FFT must return that series."""
    size = 4096
    table = build_tables(waveform, SR, size=size, bands=1)[0]
    spectrum = np.abs(np.fft.rfft(table)) * 2.0 / size
    for k in range(1, 12):
        assert spectrum[k] == pytest.approx(expect(k), abs=1e-9)


def test_sine_table_is_a_sine():
    table = build_tables("sine", SR, size=1024, bands=1)[0]
    expected = np.sin(2 * np.pi * np.arange(1024) / 1024)
    assert np.allclose(table, expected, atol=1e-9)


def test_tables_are_cached_per_waveform_and_rate():
    """A voice must never build a table at note-on."""
    first = tables_for("saw", SR)
    assert tables_for("saw", SR) is first
    assert tables_for("square", SR) is first          # square reads the saw table
    assert tables_for("triangle", SR) is not first


# --- the cleanliness claim (#103: 60-90dB cleaner than PolyBLEP) ----------

def alias_floor_dbc(samples, fundamental, sample_rate=SR):
    """Worst inharmonic component, in dB relative to the strongest
    harmonic. Excludes a generous guard band around every harmonic (and
    everything below the fundamental) so the figure measures the
    oscillator rather than the analysis window's own leakage skirts."""
    windowed = samples * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / sample_rate)
    harmonic = freqs < fundamental * 0.5
    for k in range(1, int(sample_rate / 2 / fundamental) + 1):
        harmonic |= np.abs(freqs - k * fundamental) < HARMONIC_GUARD_HZ
    return 20.0 * np.log10(spectrum[~harmonic].max() / spectrum.max())


@pytest.mark.parametrize("freq", [55.0, 110.0, 220.0, 440.0, 1760.0])
def test_wavetable_alias_floor_beats_polyblep(freq):
    """#103 measured PolyBLEP at -25..-53 dBc and mip-mapped wavetables at
    -86..-118. This is the measurement that justifies choosing the slower
    option (#100: ~10% slower per block once vectorized across voices)."""
    tables = tables_for("saw", SR)
    band = tables[mip_level_for(freq)]
    samples, _ = read_table(band, 0.0, freq / SR, 1 << 16)
    assert alias_floor_dbc(samples, freq) < -85.0


def test_a_naive_saw_is_the_control_and_is_far_dirtier():
    """Guard against the alias measurement being vacuous: the same metric
    on an unbandlimited saw at the same frequency must look terrible."""
    freq = 1760.0
    n = 1 << 16
    phase = (np.arange(n) * freq / SR) % 1.0
    naive = 2.0 * phase - 1.0
    assert alias_floor_dbc(naive, freq) > -40.0


# --- table reads ----------------------------------------------------------

def test_phase_array_is_a_cumulative_sum_starting_at_the_given_phase():
    dts = np.array([0.1, 0.2, 0.3])
    phases, next_phase = phase_array(0.5, dts)
    assert phases == pytest.approx([0.5, 0.6, 0.8])
    assert next_phase == pytest.approx(0.1)           # 1.1 wrapped


def test_phase_array_wraps_rather_than_growing_without_bound():
    phases, next_phase = phase_array(0.9, np.full(1000, 0.37))
    assert np.all((phases >= 0.0) & (phases < 1.0))
    assert 0.0 <= next_phase < 1.0


def test_table_lookup_interpolates_and_wraps():
    table = np.array([0.0, 1.0, 2.0, 3.0])
    got = table_lookup(table, np.array([0.0, 0.125, 0.25, 0.9375]))
    assert got == pytest.approx([0.0, 0.5, 1.0, 0.75])  # last wraps 3 -> 0


def test_read_table_is_phase_continuous_across_calls():
    table = build_tables("sine", SR, size=1024, bands=1)[0]
    dt = 440.0 / SR
    whole, _ = read_table(table, 0.0, dt, 600)
    first, phase = read_table(table, 0.0, dt, 300)
    second, _ = read_table(table, phase, dt, 300)
    assert np.allclose(whole, np.concatenate([first, second]), atol=1e-12)


# --- pulse from saw -------------------------------------------------------

@pytest.mark.parametrize("width", [0.1, 0.25, 0.5, 0.75, 0.9])
def test_pulse_from_saw_has_the_requested_duty_cycle(width):
    """A band-limited pulse built from two saw reads: the fraction of the
    cycle spent positive must be `width`, which is what lets pulse_width
    be continuous with no table per width."""
    table = build_tables("saw", SR, size=4096, bands=1)[0]
    phases = np.linspace(0.0, 1.0, 8192, endpoint=False)
    pulse = pulse_from_saw(table, phases, width)
    assert np.mean(pulse > 0.0) == pytest.approx(width, abs=0.01)


def test_pulse_at_half_width_is_a_square_and_is_dc_free():
    table = build_tables("saw", SR, size=4096, bands=1)[0]
    phases = np.linspace(0.0, 1.0, 8192, endpoint=False)
    pulse = pulse_from_saw(table, phases, 0.5)
    assert np.mean(pulse) == pytest.approx(0.0, abs=1e-3)
    # Gibbs overshoot, roughly doubled: a pulse is a *difference* of two
    # band-limited saws, so it inherits an overshoot from each.
    assert 1.0 < np.abs(pulse).max() < 1.25


def test_pulse_dc_offset_tracks_width():
    """Narrow pulse => mostly -1 => negative mean; the (2d-1) term is what
    keeps the two saw ramps cancelling to flat levels."""
    table = build_tables("saw", SR, size=4096, bands=1)[0]
    phases = np.linspace(0.0, 1.0, 8192, endpoint=False)
    assert np.mean(pulse_from_saw(table, phases, 0.1)) < -0.7
    assert np.mean(pulse_from_saw(table, phases, 0.9)) > 0.7


# --------------------------------------------------------------------------
# The 2-pole state-variable filter (#103 recommendation 3)
# --------------------------------------------------------------------------

def response_db(b, a, freqs):
    _, h = scipy_signal.freqz(b, a, worN=np.asarray(freqs, dtype=float), fs=SR)
    return 20.0 * np.log10(np.abs(h) + 1e-30)


def test_resonance_maps_linearly_onto_damping():
    assert resonance_to_damping(0.0) == pytest.approx(config.SYNTH_DAMPING_MAX)
    assert resonance_to_damping(1.0) == pytest.approx(config.SYNTH_DAMPING_MIN)
    mid = resonance_to_damping(0.5)
    assert mid == pytest.approx((config.SYNTH_DAMPING_MAX + config.SYNTH_DAMPING_MIN) / 2)
    assert resonance_to_damping(-5.0) == pytest.approx(config.SYNTH_DAMPING_MAX)   # clamped
    assert resonance_to_damping(5.0) == pytest.approx(config.SYNTH_DAMPING_MIN)


@pytest.mark.parametrize("filter_type,expected", [
    ("lp", (0.0, -3.01, -79.0)),
    ("hp", (-80.0, -3.01, 0.0)),
    ("bp", (-40.0, -3.01, -39.6)),
])
def test_all_three_taps_share_a_denominator_and_differ_only_in_shape(filter_type, expected):
    """One structure, one extra output tap -- the property that makes
    `filter.type` a free parameter (#103 s5). Checked at a decade below
    cutoff, at cutoff, and near Nyquist."""
    b, a = svf_coefficients(1000.0, 0.0, SR, filter_type)
    got = response_db(b, a, [10.0, 1000.0, 20000.0])
    assert got == pytest.approx(expected, abs=1.0)


def test_every_filter_type_shares_the_same_poles():
    """Same denominator, literally."""
    _, a_lp = svf_coefficients(1200.0, 0.4, SR, "lp")
    _, a_hp = svf_coefficients(1200.0, 0.4, SR, "hp")
    _, a_bp = svf_coefficients(1200.0, 0.4, SR, "bp")
    assert a_lp == a_hp == a_bp


@pytest.mark.parametrize("cutoff", [100.0, 440.0, 1000.0, 5000.0])
def test_prewarp_puts_the_cutoff_exactly_where_the_patch_asked(cutoff):
    """The point of the `g = tan(pi*fc/sr)` prewarp: at Butterworth
    damping the -3.01dB point must land on `cutoff` itself, not on a
    bilinear-warped approximation of it."""
    b, a = svf_coefficients(cutoff, 0.0, SR, "lp")
    assert response_db(b, a, [cutoff])[0] == pytest.approx(-3.01, abs=0.05)


def test_lowpass_rolls_off_at_twelve_db_per_octave():
    b, a = svf_coefficients(500.0, 0.0, SR, "lp")
    two_oct, three_oct = response_db(b, a, [2000.0, 4000.0])
    assert (two_oct - three_oct) == pytest.approx(12.0, abs=0.6)


def test_highpass_rolls_off_at_twelve_db_per_octave_downwards():
    b, a = svf_coefficients(4000.0, 0.0, SR, "hp")
    low, lower = response_db(b, a, [1000.0, 500.0])
    assert (low - lower) == pytest.approx(12.0, abs=0.6)


def test_zero_resonance_is_butterworth_flat_with_no_peak():
    """"No resonance" should mean the flattest passband, not a small
    bump -- which is why SYNTH_DAMPING_MAX is sqrt(2)."""
    b, a = svf_coefficients(1000.0, 0.0, SR, "lp")
    _, h = scipy_signal.freqz(b, a, worN=8192, fs=SR)
    assert 20.0 * np.log10(np.abs(h).max()) == pytest.approx(0.0, abs=0.02)


@pytest.mark.parametrize("resonance", [0.6, 0.8, 1.0])
def test_resonance_peaks_by_one_over_damping_at_the_cutoff(resonance):
    """High-Q identity: the SVF's peak gain is 1/k and sits at cutoff."""
    cutoff = 1000.0
    b, a = svf_coefficients(cutoff, resonance, SR, "lp")
    w, h = scipy_signal.freqz(b, a, worN=1 << 15, fs=SR)
    magnitude = np.abs(h)
    peak_db = 20.0 * np.log10(magnitude.max())
    expected = 20.0 * np.log10(1.0 / resonance_to_damping(resonance))
    assert peak_db == pytest.approx(expected, abs=0.8)
    assert w[magnitude.argmax()] == pytest.approx(cutoff, rel=0.2)


def test_maximum_resonance_stops_short_of_self_oscillation():
    """k -> 0 is a marginally-stable oscillator with unbounded output, so
    it is deliberately unreachable (SYNTH_DAMPING_MIN > 0)."""
    assert config.SYNTH_DAMPING_MIN > 0.0
    b, a = svf_coefficients(1000.0, 1.0, SR, "lp")
    assert np.abs(np.roots(a)).max() < 1.0


@pytest.mark.parametrize("cutoff", [1.0, 20.0, 100.0, 1000.0, 10000.0, 19000.0, 25000.0, 1e6])
@pytest.mark.parametrize("resonance", [0.0, 0.5, 1.0])
def test_the_filter_is_stable_at_every_cutoff_a_modulation_can_reach(cutoff, resonance):
    """A modulated cutoff routinely tries to run past Nyquist or below
    audibility; the clamp must keep every pole inside the unit circle."""
    b, a = svf_coefficients(cutoff, resonance, SR, "lp")
    assert np.abs(np.roots(a)).max() < 1.0
    assert np.all(np.isfinite(b)) and np.all(np.isfinite(a))


def test_cutoff_is_clamped_into_the_documented_range():
    low = svf_coefficients(0.001, 0.0, SR, "lp")
    assert low == svf_coefficients(config.SYNTH_CUTOFF_MIN_HZ, 0.0, SR, "lp")
    high = svf_coefficients(1e9, 0.0, SR, "lp")
    assert high == svf_coefficients(SR * 0.45, 0.0, SR, "lp")


def test_damping_coefficients_take_k_directly():
    b, a = svf_damping_coefficients(1000.0, resonance_to_damping(0.7), SR, "lp")
    assert (b, a) == svf_coefficients(1000.0, 0.7, SR, "lp")


def test_filter_normalizes_a_zero_to_one():
    for filter_type in ("lp", "hp", "bp"):
        _, a = svf_coefficients(800.0, 0.3, SR, filter_type)
        assert a[0] == 1.0


# --- pink noise -----------------------------------------------------------

def test_pink_filter_rolls_off_three_db_per_octave():
    """The 1/f claim, measured across the decade the constant was fitted
    over."""
    response = response_db(PINK_B, PINK_A, [100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0, 12800.0])
    slopes = np.diff(response)
    assert np.all(slopes < 0.0)
    assert slopes.mean() == pytest.approx(-3.0, abs=0.5)


def test_pink_make_up_gain_matches_white_noise_rms():
    """The `colour` knob changes spectrum, not loudness."""
    synth_engine.seed_noise(7)
    white = np.random.default_rng(7).uniform(-1.0, 1.0, 1 << 16)
    pink, _ = scipy_signal.lfilter(PINK_B, PINK_A, white, zi=np.zeros(len(PINK_A) - 1))
    ratio = np.sqrt(np.mean(white ** 2)) / np.sqrt(np.mean(pink ** 2))
    assert config.SYNTH_PINK_GAIN == pytest.approx(ratio, rel=0.15)


# --------------------------------------------------------------------------
# DAHDSR envelope (#103 s5, SF2's delay/hold ahead of ADSR)
# --------------------------------------------------------------------------

def envelope(sample_rate=1000, **kwargs):
    """One envelope at 1000Hz, so one sample is exactly one millisecond
    and every stage boundary below is readable as an index."""
    spec = patch_format.Envelope(**kwargs)
    return DahdsrEnvelope(spec, sample_rate)


def test_dahdsr_stage_boundaries_are_sample_exact():
    env = envelope(delay=0.010, hold=0.020, attack=0.010, decay=0.020, sustain=0.5, release=0.040)
    out = env.block(200)
    assert np.all(out[:10] == 0.0)                      # delay
    assert out[19] == pytest.approx(1.0)                # attack completes
    assert np.allclose(out[20:40], 1.0)                 # hold
    assert out[40] < 1.0 and out[59] == pytest.approx(0.5, abs=1e-9)   # decay
    assert np.allclose(out[60:], 0.5)                   # sustain
    assert env.stage == SUSTAIN


def test_attack_is_a_linear_ramp():
    env = envelope(delay=0.0, attack=0.010, decay=1.0, sustain=1.0)
    out = env.block(10)
    assert out == pytest.approx(np.arange(1, 11) / 10.0)


def test_zero_delay_starts_in_attack_and_zero_hold_skips_hold():
    env = envelope(delay=0.0, hold=0.0, attack=0.005, decay=0.010, sustain=0.5)
    assert env.stage == ATTACK
    env.block(5)
    assert env.stage == DECAY


def test_delay_holds_at_silence_then_attacks():
    env = envelope(delay=0.030, attack=0.005, decay=1.0, sustain=1.0)
    assert env.stage == DELAY
    out = env.block(30)
    assert np.all(out == 0.0) and env.stage == ATTACK


def test_release_takes_exactly_the_release_time_from_wherever_it_starts():
    """The note-off can interrupt any stage; the fade must still take
    `release` seconds, which is why the rate is computed at note-off."""
    for run_first in (5, 15, 40, 80):
        env = envelope(delay=0.0, hold=0.0, attack=0.010, decay=0.020, sustain=0.5, release=0.040)
        env.block(run_first)
        level_at_off = env.level
        env.note_off()
        assert env.stage == RELEASE
        out = env.block(60)
        assert out[0] == pytest.approx(level_at_off * 39 / 40, abs=1e-9)
        assert out[39] == pytest.approx(0.0, abs=1e-12)
        assert np.all(out[40:] == 0.0)
        assert env.finished


def test_note_off_is_idempotent():
    """A second note-off must not restart the fade -- otherwise repeated
    note-offs ring a note on indefinitely."""
    env = envelope(delay=0.0, hold=0.0, attack=0.005, decay=0.010, sustain=0.8, release=0.040)
    env.block(30)
    env.note_off()
    env.block(20)
    partway = env.level
    env.note_off()
    assert env.level == partway
    assert env.stage == RELEASE
    env.block(20)
    assert env.finished


def test_zero_sustain_finishes_at_the_end_of_decay():
    """A voice that can never be heard again should give its polyphony
    slot back."""
    env = envelope(delay=0.0, hold=0.0, attack=0.005, decay=0.010, sustain=0.0)
    out = env.block(40)
    assert env.stage == DONE
    assert env.finished
    assert np.all(out[16:] == 0.0)


def test_released_property_tracks_the_stage():
    env = envelope(delay=0.0, attack=0.005, decay=0.010, sustain=0.5, release=0.010)
    assert not env.released
    env.note_off()
    assert env.released
    env.block(50)
    assert env.stage == DONE and env.released


def test_advance_walks_identically_to_block():
    """The control-rate filter envelope and the audio-rate amp envelope
    go through the same segment walk, so they can never drift apart."""
    spec = dict(delay=0.005, hold=0.005, attack=0.010, decay=0.020, sustain=0.4, release=0.030)
    a, b = envelope(**spec), envelope(**spec)
    values = []
    for _ in range(12):
        values.append(a.advance(8))
    rendered = b.block(96)
    assert values == pytest.approx(rendered[::8] - np.diff(rendered, prepend=0.0)[::8], abs=1e-9)
    assert a.stage == b.stage
    assert a.level == pytest.approx(b.level)


def test_envelope_survives_being_split_across_blocks():
    spec = dict(delay=0.004, hold=0.004, attack=0.008, decay=0.016, sustain=0.35, release=0.020)
    whole = envelope(**spec).block(120)
    split_env = envelope(**spec)
    split = np.concatenate([split_env.block(37), split_env.block(11), split_env.block(72)])
    assert np.allclose(whole, split, atol=1e-12)


def test_stage_names_cover_every_stage():
    assert set(synth_engine.STAGE_NAMES) == {DELAY, ATTACK, HOLD, DECAY, SUSTAIN, RELEASE, DONE}


# --------------------------------------------------------------------------
# LFO (#103 s5, SF2's GEN_MODLFO*)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("waveform", ["sine", "triangle"])
def test_lfo_shapes_with_a_zero_start_from_no_modulation(waveform):
    """Sine and triangle fade in from no modulation rather than jumping
    to an extreme at note-on."""
    assert lfo_shape(waveform, 0.0) == pytest.approx(0.0)


@pytest.mark.parametrize("waveform,start", [("saw", -1.0), ("square", 1.0)])
def test_saw_and_square_have_no_zero_to_start_at(waveform, start):
    """Neither shape *has* a zero at phase 0 -- a square that started at
    0 would not be a square -- so both begin at an extreme, and `delay`
    rather than the waveform is what holds their modulation off."""
    assert lfo_shape(waveform, 0.0) == pytest.approx(start)
    assert lfo_shape("saw", 0.5) == pytest.approx(0.0)


@pytest.mark.parametrize("waveform", ["sine", "triangle", "square", "saw"])
def test_lfo_waveforms_are_bipolar_and_bounded(waveform):
    """Every waveform spans [-1, 1] and never leaves it. The saw only
    *approaches* +1 (it jumps back to -1 at the wrap), so its maximum is
    checked to the tolerance of the phase grid rather than exactly."""
    values = [lfo_shape(waveform, p) for p in np.linspace(0, 1, 4097)]
    assert all(-1.0 - 1e-12 <= v <= 1.0 + 1e-12 for v in values)
    assert min(values) == pytest.approx(-1.0, abs=1e-3)
    assert max(values) == pytest.approx(1.0, abs=1e-3)


def test_triangle_and_square_shapes():
    assert lfo_shape("triangle", 0.25) == pytest.approx(1.0)
    assert lfo_shape("triangle", 0.5) == pytest.approx(0.0)
    assert lfo_shape("triangle", 0.75) == pytest.approx(-1.0)
    assert lfo_shape("square", 0.25) == 1.0
    assert lfo_shape("square", 0.75) == -1.0


def test_lfo_shape_wraps_phase():
    assert lfo_shape("sine", 2.25) == pytest.approx(lfo_shape("sine", 0.25))


def lfo(**kwargs):
    return Lfo(patch_format.Lfo(**kwargs), 1000)


def test_lfo_delay_gates_modulation_and_is_counted_per_voice():
    osc = lfo(rate=1.0, depth=1.0, delay=0.050, waveform="saw")
    assert not osc.active
    assert osc.value() == 0.0
    assert osc.tremolo() == 1.0
    osc.step(50)
    assert osc.active
    assert osc.value() != 0.0


def test_lfo_phase_persists_across_blocks():
    """Restarting the phase at a block boundary produces a buzz at the
    block rate that is easy to mistake for a filter problem (#103)."""
    whole = lfo(rate=5.0, depth=1.0)
    for _ in range(8):
        whole.step(16)
    split = lfo(rate=5.0, depth=1.0)
    split.step(64)
    split.step(64)
    assert whole.phase == pytest.approx(split.phase)
    assert whole.phase == pytest.approx((5.0 * 128 / 1000.0) % 1.0)


def test_lfo_step_reports_the_value_at_the_start_of_the_sub_block():
    """Value first, then advance -- a sub-block's coefficients come from
    the modulation state at its own start, not one sub-block into its
    future."""
    osc = lfo(rate=1.0, depth=1.0, waveform="saw")
    first, _ = osc.step(100)
    assert first == pytest.approx(-1.0)
    second, _ = osc.step(100)
    assert second == pytest.approx(lfo_shape("saw", 0.1))


def test_zero_depth_is_no_modulation_but_still_advances():
    osc = lfo(rate=5.0, depth=0.0)
    value, gain = osc.step(64)
    assert value == 0.0 and gain == 1.0
    assert osc.phase > 0.0


def test_tremolo_attenuates_and_never_boosts_past_unity():
    """An amp LFO must not be able to push a voice into the master
    soft-clip on its own."""
    osc = lfo(rate=3.0, depth=1.0)
    gains = []
    for _ in range(400):                 # > one full cycle at 3Hz / 1000Hz
        _, gain = osc.step(1)
        gains.append(gain)
    assert max(gains) == pytest.approx(1.0, abs=1e-4)
    assert min(gains) == pytest.approx(0.0, abs=1e-4)
    assert all(0.0 <= g <= 1.0 + 1e-12 for g in gains)


def test_tremolo_depth_bounds_the_attenuation():
    osc = lfo(rate=3.0, depth=0.25)
    gains = [osc.step(1)[1] for _ in range(400)]
    assert min(gains) == pytest.approx(0.75, abs=1e-6)


# --------------------------------------------------------------------------
# Modulation routing
# --------------------------------------------------------------------------

def test_oscillator_frequency_applies_octave_semitones_and_fine():
    base = 440.0
    assert oscillator_frequency(base, patch_format.Oscillator()) == pytest.approx(base)
    assert oscillator_frequency(base, patch_format.Oscillator(octave=1)) == pytest.approx(880.0)
    assert oscillator_frequency(base, patch_format.Oscillator(octave=-1)) == pytest.approx(220.0)
    assert oscillator_frequency(base, patch_format.Oscillator(semitones=12)) == pytest.approx(880.0)
    assert oscillator_frequency(base, patch_format.Oscillator(fine=1200.0)) == pytest.approx(880.0)
    detuned = oscillator_frequency(base, patch_format.Oscillator(fine=-7.0))
    assert detuned == pytest.approx(base * 2.0 ** (-7.0 / 1200.0))


def test_modulated_cutoff_is_the_patch_cutoff_with_nothing_modulating():
    patch = patch_format.new_patch()
    patch.filter.cutoff = 1000.0
    patch.filter.env_amount = 0.0
    patch.filter.key_tracking = 0.0
    patch.voice.velocity_to_filter = 0.0
    assert modulated_cutoff(patch, 60, 1.0, 1.0, 0.0) == pytest.approx(1000.0)


def test_filter_envelope_opens_and_closes_in_octaves():
    """Everything modulates cutoff in octaves, because a fixed Hz offset
    means something completely different at C1 and at C7."""
    patch = patch_format.new_patch()
    patch.filter.cutoff = 1000.0
    patch.filter.key_tracking = 0.0
    patch.voice.velocity_to_filter = 0.0
    patch.filter.env_amount = 1.0
    up = modulated_cutoff(patch, 60, 1.0, 1.0, 0.0)
    assert up == pytest.approx(1000.0 * 2.0 ** config.SYNTH_FILTER_ENV_OCTAVES)
    patch.filter.env_amount = -1.0
    down = modulated_cutoff(patch, 60, 1.0, 1.0, 0.0)
    assert down == pytest.approx(1000.0 / 2.0 ** config.SYNTH_FILTER_ENV_OCTAVES)


def test_key_tracking_follows_the_note_exactly_at_full_depth():
    """Without it high notes sound muffled relative to low ones (#103)."""
    patch = patch_format.new_patch()
    patch.filter.cutoff = 1000.0
    patch.filter.env_amount = 0.0
    patch.voice.velocity_to_filter = 0.0
    patch.filter.key_tracking = 1.0
    assert modulated_cutoff(patch, 60, 1.0, 0.0, 0.0) == pytest.approx(1000.0)
    assert modulated_cutoff(patch, 72, 1.0, 0.0, 0.0) == pytest.approx(2000.0)
    assert modulated_cutoff(patch, 48, 1.0, 0.0, 0.0) == pytest.approx(500.0)


def test_velocity_closes_the_filter_rather_than_opening_it():
    """Velocity 1.0 is always the patch's stated cutoff, never an
    over-bright surprise."""
    patch = patch_format.new_patch()
    patch.filter.cutoff = 4000.0
    patch.filter.env_amount = 0.0
    patch.filter.key_tracking = 0.0
    patch.voice.velocity_to_filter = 1.0
    assert modulated_cutoff(patch, 60, 1.0, 0.0, 0.0) == pytest.approx(4000.0)
    quiet = modulated_cutoff(patch, 60, 0.0, 0.0, 0.0)
    assert quiet == pytest.approx(4000.0 / 2.0 ** config.SYNTH_VELOCITY_FILTER_OCTAVES)
    assert quiet < 4000.0


def test_filter_lfo_swings_the_cutoff_by_its_configured_octaves():
    patch = patch_format.new_patch()
    patch.filter.cutoff = 1000.0
    patch.filter.env_amount = 0.0
    patch.filter.key_tracking = 0.0
    patch.voice.velocity_to_filter = 0.0
    assert modulated_cutoff(patch, 60, 1.0, 0.0, 1.0) == pytest.approx(
        1000.0 * 2.0 ** config.SYNTH_LFO_FILTER_OCTAVES)


# --------------------------------------------------------------------------
# SynthVoice -- the resumable-voice contract
# --------------------------------------------------------------------------

def simple_patch(**overrides):
    """A deliberately plain patch: one sine oscillator, filter wide open,
    envelope a rectangle. Anything asserting on exact samples uses this
    so the assertion is about the mechanism under test rather than about
    the init patch's own voicing."""
    patch = patch_format.new_patch()
    patch.osc1.waveform = "sine"
    patch.osc2.level = 0.0
    patch.noise.level = 0.0
    patch.filter.cutoff = 18000.0
    patch.filter.resonance = 0.0
    patch.filter.env_amount = 0.0
    patch.filter.key_tracking = 0.0
    patch.amp_env.delay = 0.0
    patch.amp_env.hold = 0.0
    patch.amp_env.attack = 0.0005
    patch.amp_env.decay = 0.001
    patch.amp_env.sustain = 1.0
    patch.amp_env.release = 0.02
    patch.voice.volume = 1.0
    patch.voice.velocity_to_amp = 1.0
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        setattr(getattr(patch, section), field, value)
    return patch


def voice_for(pitch=60, velocity=1.0, patch=None, **kwargs):
    return SynthVoice(NoteOn(pitch, velocity), SR, patch=patch or simple_patch(), **kwargs)


def render(voice, frames):
    out = np.zeros(frames, dtype=np.float64)
    voice.render(out, frames)
    return out


def test_voice_state_survives_a_block_boundary():
    """*The* test for the resumable-voice design: one block of 1024 must
    equal two blocks of 512. No voice with a reset oscillator phase,
    filter `zi`, envelope stage or LFO phase can pass it."""
    patch = simple_patch()
    patch.osc1.waveform = "saw"
    patch.osc2.level = 0.7
    patch.osc2.fine = -7.0
    patch.filter.cutoff = 1200.0
    patch.filter.resonance = 0.8
    patch.filter.env_amount = 0.6
    patch.lfo.depth = 0.9
    patch.lfo.rate = 6.0
    patch.lfo.destination = "filter"

    whole = render(voice_for(patch=patch), 1024)
    split_voice = voice_for(patch=patch)
    split = np.concatenate([render(split_voice, 512), render(split_voice, 512)])
    assert np.allclose(whole, split, atol=1e-9)


def test_sub_block_aligned_splits_are_bit_exact():
    """Any split on the control-sub-block grid reproduces the whole
    render exactly -- oscillator phase, filter `zi` and both envelopes
    all resume where they stopped."""
    patch = simple_patch()
    patch.osc1.waveform = "saw"
    patch.filter.env_amount = 0.5
    whole = render(voice_for(patch=patch), 1024)
    aligned = voice_for(patch=patch)
    split = np.concatenate([render(aligned, 64), render(aligned, 256), render(aligned, 704)])
    assert np.allclose(whole, split, atol=1e-9)


def test_ragged_splits_resync_the_control_grid_rather_than_drifting():
    """A block whose length is not a multiple of `sub_block` must cut its
    final control sub-block short -- that much is unavoidable -- but the
    *next* block has to resume the grid where the note left it rather
    than restarting it at the block boundary.

    Without that carry-over the control updates re-time themselves
    against every block boundary and the error compounds. Measured on
    this patch and this ragged split: 37.7% of peak without the
    carry-over, 8.9% with it -- a 4.2x reduction, and the residual is
    the unavoidable short sub-block at each block's end. So this asserts
    a bound comfortably under the no-carry figure rather than
    exactness, while the companion test above pins the aligned case to
    bit-exactness."""
    patch = simple_patch()
    patch.osc1.waveform = "saw"
    patch.filter.cutoff = 1800.0
    patch.filter.env_amount = 0.5
    whole = render(voice_for(patch=patch), 1000)
    ragged = voice_for(patch=patch)
    split = np.concatenate([render(ragged, 37), render(ragged, 291), render(ragged, 672)])
    assert np.abs(whole - split).max() < 0.15 * np.abs(whole).max()


def test_sub_block_offset_is_carried_between_render_calls():
    voice = voice_for(sub_block=64)
    assert voice._sub_offset == 0
    render(voice, 100)
    assert voice._sub_offset == 36
    render(voice, 28)
    assert voice._sub_offset == 0
    assert voice._sub_block_lengths(200) == [64, 64, 64, 8]


def test_sub_block_lengths_always_cover_the_block():
    voice = voice_for(sub_block=64)
    for frames in (1, 7, 63, 64, 65, 128, 291, 512, 1000):
        lengths = voice._sub_block_lengths(frames)
        assert sum(lengths) == frames
        assert all(0 < n <= 64 for n in lengths)


def test_render_is_additive_and_leaves_the_rest_of_the_buffer_alone():
    """`Voice.render()` adds into the caller's block, so `VoiceManager`
    mixes N voices with no per-voice allocation."""
    voice = voice_for()
    out = np.ones(600, dtype=np.float32) * 0.25
    voice.render(out, 400)
    assert np.all(out[400:] == 0.25)
    assert not np.allclose(out[:400], 0.25)


def test_two_voices_sum_into_one_buffer():
    a, b = voice_for(60), voice_for(67)
    solo_a, solo_b = render(a, 256), render(b, 256)
    mixed = np.zeros(256)
    voice_for(60).render(mixed, 256)
    voice_for(67).render(mixed, 256)
    assert np.allclose(mixed, solo_a + solo_b, atol=1e-12)


def test_render_of_zero_frames_is_a_no_op():
    voice = voice_for()
    out = np.zeros(64)
    voice.render(out, 0)
    assert np.all(out == 0.0)


def test_voice_reaches_the_note_pitch():
    """The oscillator really runs at the note's own frequency."""
    patch = simple_patch()
    voice = voice_for(69, patch=patch)          # A4 = 440Hz
    samples = np.concatenate([render(voice, 4096) for _ in range(4)])
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    peak = np.fft.rfftfreq(len(samples), 1.0 / SR)[spectrum.argmax()]
    assert peak == pytest.approx(440.0, rel=0.01)


def test_note_off_releases_then_finishes_and_is_idempotent():
    voice = voice_for()
    assert not voice.released and not voice.finished
    render(voice, 512)
    voice.note_off()
    assert voice.released
    voice.note_off()                             # must not restart the release
    blocks = 0
    while not voice.finished and blocks < 200:
        render(voice, 512)
        blocks += 1
    assert voice.finished
    assert blocks <= math.ceil(0.02 * SR / 512) + 2


def test_a_finished_voice_renders_nothing_more():
    voice = voice_for()
    voice.note_off()
    while not voice.finished:
        render(voice, 512)
    out = np.ones(128) * 0.5
    voice.render(out, 128)
    assert np.all(out == 0.5)


def test_amplitude_ranks_voices_for_the_stealing_policy():
    voice = voice_for(velocity=1.0)
    render(voice, 512)
    loud = voice.amplitude()
    voice.note_off()
    render(voice, 256)
    assert 0.0 <= voice.amplitude() < loud


def test_velocity_scales_the_output_and_zero_velocity_is_silent():
    """velocity_to_amp 1.0 => velocity 0 is silence; 0.0 => an organ that
    ignores velocity entirely."""
    loud = render(voice_for(velocity=1.0), 512)
    soft = render(voice_for(velocity=0.4), 512)
    assert np.abs(soft).max() < np.abs(loud).max()
    silent = render(voice_for(velocity=0.0), 512)
    assert np.abs(silent).max() == pytest.approx(0.0, abs=1e-12)

    organ_patch = simple_patch(voice__velocity_to_amp=0.0)
    a = render(voice_for(velocity=1.0, patch=organ_patch), 512)
    b = render(voice_for(velocity=0.2, patch=organ_patch), 512)
    assert np.allclose(a, b, atol=1e-12)


def test_sources_are_level_balanced_so_a_fat_patch_does_not_clip():
    """Gain staging in the voice rather than leaving it to the master
    tanh, which would otherwise distort a fat patch and not a thin one."""
    thin = simple_patch()
    fat = simple_patch()
    fat.osc2.level = 1.0
    fat.osc2.waveform = "sine"
    fat.noise.level = 1.0
    fat_peak = np.abs(render(voice_for(patch=fat), 2048)).max()
    thin_peak = np.abs(render(voice_for(patch=thin), 2048)).max()
    # Three sources at full level must not sum to 3x one source: the
    # point is that the master tanh is never asked to rescue a fat patch
    # that a thin one would not have needed rescuing from.
    assert fat_peak < thin_peak * 1.6
    assert fat_peak < 1.25


def test_the_filter_actually_filters():
    """A closed lowpass on a saw must remove the upper harmonics that an
    open one keeps -- the direct check that `lfilter` is in the path."""
    bright = simple_patch()
    bright.osc1.waveform = "saw"
    dull = simple_patch()
    dull.osc1.waveform = "saw"
    dull.filter.cutoff = 200.0

    def harmonic_energy(patch):
        voice = voice_for(45, patch=patch)       # A2 = 110Hz
        samples = np.concatenate([render(voice, 4096) for _ in range(4)])
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / SR)
        return spectrum[freqs > 2000.0].sum()

    assert harmonic_energy(dull) < harmonic_energy(bright) * 0.05


def test_highpass_and_bandpass_patches_render_without_blowing_up():
    for filter_type in ("lp", "hp", "bp"):
        patch = simple_patch()
        patch.osc1.waveform = "saw"
        patch.filter.type = filter_type
        patch.filter.cutoff = 800.0
        patch.filter.resonance = 0.9
        out = np.concatenate([render(voice_for(patch=patch), 512) for _ in range(8)])
        assert np.all(np.isfinite(out))
        assert np.abs(out).max() < 10.0


def test_noise_only_patch_makes_broadband_noise():
    patch = simple_patch()
    patch.osc1.level = 0.0
    patch.noise.level = 1.0
    synth_engine.seed_noise(3)
    out = render(voice_for(patch=patch), 4096)
    assert np.abs(out).max() > 0.0
    spectrum = np.abs(np.fft.rfft(out))
    assert spectrum[len(spectrum) // 2:].mean() > 0.0


def test_pink_noise_is_darker_than_white():
    def spectrum_for(colour):
        patch = simple_patch()
        patch.osc1.level = 0.0
        patch.noise.level = 1.0
        patch.noise.colour = colour
        synth_engine.seed_noise(11)
        out = np.concatenate([render(voice_for(patch=patch), 4096) for _ in range(4)])
        mag = np.abs(np.fft.rfft(out))
        freqs = np.fft.rfftfreq(len(out), 1.0 / SR)
        low = mag[(freqs > 100) & (freqs < 400)].mean()
        high = mag[(freqs > 4000) & (freqs < 16000)].mean()
        return low / high

    assert spectrum_for("pink") > spectrum_for("white") * 3.0


def test_pitch_lfo_moves_the_oscillator_frequency():
    patch = simple_patch()
    patch.lfo.rate = 4.0
    patch.lfo.depth = 1.0
    patch.lfo.destination = "pitch"
    patch.lfo.waveform = "saw"                   # non-zero from phase 0
    modulated = np.concatenate([render(voice_for(69, patch=patch), 512) for _ in range(8)])
    flat = np.concatenate([render(voice_for(69, patch=simple_patch()), 512) for _ in range(8)])
    assert not np.allclose(modulated, flat, atol=1e-6)


def test_amp_lfo_only_attenuates():
    patch = simple_patch()
    patch.lfo.rate = 30.0
    patch.lfo.depth = 1.0
    patch.lfo.destination = "amp"
    flat = np.abs(np.concatenate([render(voice_for(patch=simple_patch()), 512) for _ in range(8)])).max()
    tremolo = np.concatenate([render(voice_for(patch=patch), 512) for _ in range(8)])
    assert np.abs(tremolo).max() <= flat + 1e-9
    assert np.abs(tremolo).min() < flat


def test_a_deep_pitch_lfo_cannot_walk_a_note_off_the_top_of_its_band():
    """The mip band is picked from the block's *highest* modulated
    frequency, so modulation cannot push the read past what its table was
    band-limited for."""
    patch = simple_patch()
    patch.osc1.waveform = "saw"
    patch.lfo.rate = 5.0
    patch.lfo.depth = 1.0
    patch.lfo.destination = "pitch"
    out = np.concatenate([render(voice_for(100, patch=patch), 512) for _ in range(16)])
    assert np.all(np.isfinite(out))
    assert np.abs(out).max() < 2.0


def test_glide_starts_at_the_previous_frequency_and_arrives_at_its_own():
    patch = simple_patch()
    patch.voice.glide = 0.05
    gliding = SynthVoice(NoteOn(69), SR, patch=patch, glide_from_hz=220.0)
    plain = SynthVoice(NoteOn(69), SR, patch=patch, glide_from_hz=None)
    assert gliding._glide_offset == pytest.approx(-12.0)
    assert plain._glide_offset == 0.0

    def peak_of(voice, blocks, skip):
        chunks = [render(voice, 1024) for _ in range(blocks)]
        samples = np.concatenate(chunks[skip:])
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        return np.fft.rfftfreq(len(samples), 1.0 / SR)[spectrum.argmax()]

    early = peak_of(SynthVoice(NoteOn(69), SR, patch=patch, glide_from_hz=220.0), 2, 0)
    assert early < 400.0                                   # still sliding up
    settled = peak_of(gliding, 12, 6)                      # long after the glide
    assert settled == pytest.approx(440.0, rel=0.02)


def test_zero_glide_time_ignores_the_previous_note():
    patch = simple_patch()
    patch.voice.glide = 0.0
    voice = SynthVoice(NoteOn(69), SR, patch=patch, glide_from_hz=220.0)
    assert voice._glide_offset == 0.0


def test_a_voice_never_builds_a_table_at_note_on(monkeypatch):
    tables_for("saw", SR)                                   # warm the cache
    monkeypatch.setattr(synth_engine, "build_tables",
                        lambda *a, **k: pytest.fail("built a table at note-on"))
    voice_for(patch=simple_patch(osc1__waveform="saw"))


def test_voice_carries_every_piece_of_state_103_listed():
    """#103 recommendation 5, verbatim: oscillator phase, filter zi,
    envelope stage, LFO phase, velocity."""
    voice = voice_for(velocity=0.6)
    render(voice, 512)
    assert any(p != 0.0 for p in voice._phases)
    assert np.any(voice._zi != 0.0)
    assert voice.amp_env.stage != DELAY
    assert voice.velocity == pytest.approx(0.6)
    assert voice.base_frequency == pytest.approx(frequency_for(60))


def test_sub_block_size_is_configurable_and_defaults_to_the_config_value():
    assert voice_for().sub_block == config.SYNTH_CONTROL_SUB_BLOCK
    assert voice_for(sub_block=32).sub_block == 32


def test_a_finer_sub_block_tracks_modulation_more_closely_but_agrees_broadly():
    """The sub-block is a resolution knob, not a correctness one: halving
    it must not change the sound's character, only its stepping."""
    patch = simple_patch()
    patch.osc1.waveform = "saw"
    patch.filter.cutoff = 900.0
    patch.filter.env_amount = 0.8
    coarse = render(voice_for(patch=patch, sub_block=128), 2048)
    fine = render(voice_for(patch=patch, sub_block=32), 2048)
    assert not np.allclose(coarse, fine, atol=1e-6)
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))
    assert rms(coarse) == pytest.approx(rms(fine), rel=0.1)


# --------------------------------------------------------------------------
# SynthEngine
# --------------------------------------------------------------------------

def test_engine_satisfies_the_engine_protocol():
    engine = SynthEngine()
    voice = engine.note_on(NoteOn(60, 0.8), SR)
    assert isinstance(voice, SynthVoice)
    for attribute in ("render", "note_off", "released", "finished", "amplitude"):
        assert hasattr(voice, attribute)


def test_engine_resolves_named_patches_and_degrades_for_an_unknown_name():
    """An unknown name falls back to the default rather than raising --
    the same degrade-don't-crash posture `patch_format.load_patch()`
    takes for a missing file."""
    default = simple_patch()
    named = simple_patch(filter__cutoff=444.0)
    engine = SynthEngine(patch=default, patches={"bell": named})
    assert engine.patch_for(None) is default
    assert engine.patch_for("bell") is named
    assert engine.patch_for("nope") is default
    assert engine.note_on(NoteOn(60, patch="bell"), SR).patch is named


def test_engine_remembers_the_last_frequency_for_glide():
    """The only state the engine keeps between notes."""
    engine = SynthEngine(patch=simple_patch(voice__glide=0.05))
    assert engine._last_frequency is None
    first = engine.note_on(NoteOn(60), SR)
    assert engine._last_frequency == pytest.approx(first.base_frequency)
    second = engine.note_on(NoteOn(72), SR)
    assert second._glide_offset == pytest.approx(-12.0)


def test_engine_passes_its_sub_block_through_to_its_voices():
    engine = SynthEngine(sub_block=16)
    assert engine.note_on(NoteOn(60), SR).sub_block == 16


def test_default_patch_exercises_every_stage_of_the_signal_path():
    """The init patch is chosen so that *every* stage is audible in the
    default rather than having to be switched on before the engine proves
    it works."""
    patch = default_patch()
    assert patch.name == "Init"
    assert patch.osc1.level > 0.0 and patch.osc2.level > 0.0
    assert patch.osc2.fine != 0.0                    # detuned, the fat-sound reason `fine` exists
    assert patch.filter.resonance > 0.0
    assert patch.filter.env_amount != 0.0
    assert patch.filter.key_tracking != 0.0
    assert 0.0 < patch.amp_env.sustain < 1.0
    assert patch.voice.volume > 0.0


def test_default_patch_renders_a_real_signal():
    engine = SynthEngine()
    voice = engine.note_on(NoteOn(60, 0.9), SR)
    out = np.concatenate([render(voice, 512) for _ in range(4)])
    assert np.all(np.isfinite(out))
    assert 0.0 < np.sqrt(np.mean(out ** 2)) < 1.0
    assert np.abs(out).max() <= 1.0


def test_default_patch_is_rebuilt_fresh_each_call():
    """Two engines must not share one mutable patch object."""
    a, b = default_patch(), default_patch()
    assert a is not b
    a.filter.cutoff = 111.0
    assert b.filter.cutoff != 111.0


def test_sound_engine_defaults_to_the_synth(monkeypatch):
    """#113's one-line swap: `sound_engine._default_engine()` is what the
    `Engine` Protocol existed to make replaceable."""
    import sound_engine
    assert isinstance(sound_engine._default_engine(), SynthEngine)
