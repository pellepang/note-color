"""Tests for the shared effects bus (map #99, ticket #114, research #104):
the `Delay` and `Chorus` primitives, `EffectsChain`, the patch-spec
registry, and the offline tail estimate.

Everything here is a NumPy buffer -- no audio device, same "pure logic
unit-tested, real I/O smoke-tested" split as `test_sound_engine.py`.
Nothing is listened to (the machine is muted, and the ear is not a
test): every quality claim is numerical. The two load-bearing assertions
are #114's own acceptance criteria:

1. **Block-wise processing is bit-identical to one-shot processing**, for
   any block size -- `np.array_equal` on float32, not `allclose`. This is
   what proves the three block-boundary invariants (state persists,
   buffer > max_delay + block, read-before-write-or-sub-chunk) all hold,
   including the hard case of a *feedback* delay shorter than one block.
2. **A fully-wet chorus over a sine keeps out-of-band content below
   -80 dBc** by FFT. #104 measured -88.9 dBc with fractional-delay
   interpolation and a carried LFO phase, -48.4 without interpolation,
   -23.7 with the LFO reset per block; the floor here fails loudly if
   either decision is ever dropped, and one test deliberately induces the
   phase-reset failure to show the measurement is not vacuous.
"""

import math
import tomllib

import numpy as np
import pytest

import config
import effects
from effects import (
    Chorus, Delay, EffectsChain, build_effect, chain_from_patch, chain_from_specs,
    tail_seconds,
)
from patch_format import EffectSpec, patch_from_toml

SR = 44100
BLOCK = 512


def sine(freq, seconds=1.0, amplitude=0.5, sample_rate=SR):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


def impulse(length, at=0):
    signal = np.zeros(length, dtype=np.float32)
    signal[at] = 1.0
    return signal


def blockwise(effect, signal, block):
    return np.concatenate([effect.process(signal[i:i + block]) for i in range(0, len(signal), block)])


def sideband_dbc(signal, carrier_hz, sample_rate=SR):
    """Worst spectral content at least 50Hz from the carrier, relative to
    the carrier peak -- `scripts/effects_chain_bench.py`'s own measure."""
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / sample_rate)
    band = (np.abs(freqs - carrier_hz) > 50.0) & (freqs > 50.0) & (freqs < 5000.0)
    return 20.0 * math.log10(spectrum[band].max() / spectrum.max())


# --- Delay ----------------------------------------------------------------

def test_fully_wet_delay_reproduces_the_input_shifted_by_exactly_d_samples():
    delay = Delay(time=0.01, feedback=0.0, mix=1.0, sample_rate=SR, block_size=BLOCK)
    assert delay.delay_samples == 441
    signal = np.random.default_rng(1).standard_normal(3000).astype(np.float32)
    out = blockwise(delay, signal, BLOCK)
    assert np.all(out[:441] == 0.0)
    assert np.array_equal(out[441:], signal[:-441])


def test_delay_feedback_repeats_decay_geometrically():
    delay = Delay(time=0.01, feedback=0.5, mix=1.0, sample_rate=SR, block_size=BLOCK)
    d = delay.delay_samples
    out = delay.process(impulse(d * 6))
    repeats = [out[k * d] for k in range(1, 6)]
    assert repeats == pytest.approx([0.5 ** (k - 1) for k in range(1, 6)])
    # and nothing but the repeats
    mask = np.ones(len(out), dtype=bool)
    mask[d::d] = False
    assert np.all(out[mask] == 0.0)


def test_delay_dry_wet_mix_is_a_linear_crossfade():
    delay = Delay(time=0.01, feedback=0.0, mix=0.25, sample_rate=SR, block_size=BLOCK)
    d = delay.delay_samples
    out = delay.process(impulse(d * 2))
    assert out[0] == pytest.approx(0.75)
    assert out[d] == pytest.approx(0.25)


def test_delay_damping_rolls_off_the_repeats_but_is_off_by_default():
    assert Delay(sample_rate=SR).damping == 0.0
    undamped = Delay(time=0.01, feedback=0.5, mix=1.0, damping=0.0, sample_rate=SR)
    damped = Delay(time=0.01, feedback=0.5, mix=1.0, damping=1.0, sample_rate=SR)
    d = undamped.delay_samples
    # A Nyquist-rate alternating signal is exactly what a two-point
    # average kills; the first repeat is untouched (it has not been round
    # the loop yet), the second has been averaged once.
    signal = np.zeros(d * 3, dtype=np.float32)
    signal[:d] = np.where(np.arange(d) % 2 == 0, 1.0, -1.0)
    a = undamped.process(signal)
    b = damped.process(signal)
    assert np.array_equal(a[d:2 * d], b[d:2 * d])
    # (the repeat's very first sample is averaged against the silence
    # before it, so it survives at half level -- skip it)
    assert np.abs(b[2 * d + 1:3 * d]).max() < 0.05 < np.abs(a[2 * d + 1:3 * d]).max()


def test_delay_state_persists_across_calls_and_reset_clears_it():
    delay = Delay(time=0.01, feedback=0.0, mix=1.0, sample_rate=SR, block_size=BLOCK)
    d = delay.delay_samples
    delay.process(impulse(100))                      # impulse is now in flight
    later = delay.process(np.zeros(d, dtype=np.float32))
    assert later[d - 100] == 1.0                     # it arrives d samples after it went in
    delay.process(impulse(100))
    delay.reset()
    assert np.all(delay.process(np.zeros(d, dtype=np.float32)) == 0.0)


def test_delay_chunks_to_no_longer_than_its_own_delay():
    short = Delay(time=0.001, sample_rate=SR, block_size=BLOCK)   # 44 samples < one block
    assert short.delay_samples == 44 and short.chunk == 44
    long = Delay(time=0.5, sample_rate=SR, block_size=BLOCK)
    assert long.chunk == BLOCK
    assert long.size > long.delay_samples + BLOCK                # invariant 2


# --- the acceptance test: block-wise == one-shot, bit for bit -------------

def _cases():
    return [
        ("delay longer than a block", lambda: Delay(time=0.1, feedback=0.5, mix=0.5, sample_rate=SR, block_size=BLOCK)),
        ("feedback delay shorter than a block", lambda: Delay(time=0.002, feedback=0.6, mix=0.5, sample_rate=SR, block_size=BLOCK)),
        ("damped feedback delay", lambda: Delay(time=0.003, feedback=0.6, mix=0.5, damping=0.7, sample_rate=SR, block_size=BLOCK)),
        ("chorus, 3 voices", lambda: Chorus(mix=1.0, voices=3, sample_rate=SR, block_size=BLOCK)),
        ("chorus with feedback", lambda: Chorus(mix=0.7, voices=2, feedback=0.4, sample_rate=SR, block_size=BLOCK)),
        ("flanger-ish chorus (short centre, big depth)", lambda: Chorus(centre_delay_ms=2.0, depth_ms=1.0, rate_hz=3.0,
                                                                        mix=1.0, feedback=-0.5, sample_rate=SR, block_size=BLOCK)),
        ("delay -> chorus chain", lambda: EffectsChain([
            Delay(time=0.05, feedback=0.4, mix=0.4, sample_rate=SR, block_size=BLOCK),
            Chorus(mix=0.5, voices=3, sample_rate=SR, block_size=BLOCK)])),
    ]


@pytest.mark.parametrize("label,make", _cases(), ids=[c[0] for c in _cases()])
@pytest.mark.parametrize("block", [BLOCK, 100, 777, 4096])
def test_blockwise_processing_is_bit_identical_to_one_shot(label, make, block):
    signal = sine(440.0, seconds=0.6) + 0.1 * np.random.default_rng(7).standard_normal(int(SR * 0.6)).astype(np.float32)
    one_shot = make().process(signal)
    streamed = blockwise(make(), signal, block)
    assert one_shot.dtype == np.float32 and streamed.dtype == np.float32
    assert np.array_equal(one_shot, streamed), label


def test_blockwise_identity_holds_even_one_sample_at_a_time():
    signal = sine(440.0, seconds=0.05)
    for make in (lambda: Delay(time=0.002, feedback=0.6, mix=0.5, sample_rate=SR),
                 lambda: Chorus(mix=1.0, voices=2, feedback=0.3, sample_rate=SR)):
        assert np.array_equal(make().process(signal), blockwise(make(), signal, 1))


def test_blockwise_identity_survives_a_buffer_wraparound():
    # Enough audio that the circular buffers wrap many times over, and a
    # block size chosen so chunk boundaries fall at every buffer offset.
    signal = sine(220.0, seconds=1.5)
    for make in (lambda: Delay(time=0.007, feedback=0.5, mix=0.5, sample_rate=SR),
                 lambda: Chorus(mix=1.0, voices=3, feedback=0.3, sample_rate=SR)):
        assert np.array_equal(make().process(signal), blockwise(make(), signal, 131))


# --- Chorus ---------------------------------------------------------------

def test_chorus_artifacts_stay_below_minus_80_dbc():
    chorus = Chorus(rate_hz=1.0, depth_ms=2.0, centre_delay_ms=7.0, mix=1.0, voices=1, sample_rate=SR)
    wet = blockwise(chorus, sine(440.0, seconds=2.0), BLOCK)
    assert sideband_dbc(wet, 440.0) < -80.0


def test_chorus_artifact_floor_catches_a_per_block_lfo_phase_reset():
    """The -80 dBc floor above is only worth having if the failure modes
    it guards against actually breach it. Forcing the LFO's clock back to
    zero before every block reproduces #104's -23.7 dBc buzz-at-the-block-
    rate case."""
    chorus = Chorus(rate_hz=1.0, depth_ms=2.0, centre_delay_ms=7.0, mix=1.0, voices=1, sample_rate=SR)
    signal = sine(440.0, seconds=2.0)
    chunks = []
    for i in range(0, len(signal), BLOCK):
        chorus._elapsed = 0
        chunks.append(chorus.process(signal[i:i + BLOCK]))
    assert sideband_dbc(np.concatenate(chunks), 440.0) > -40.0


def test_chorus_with_no_modulation_is_a_plain_delay_at_the_centre():
    # 10ms at 44100Hz is exactly 441 samples, so the fractional read has
    # frac == 0 and must reproduce the input shifted by 441 exactly.
    chorus = Chorus(rate_hz=0.0, depth_ms=0.0, centre_delay_ms=10.0, mix=1.0, voices=1, sample_rate=SR)
    signal = np.random.default_rng(3).standard_normal(2000).astype(np.float32)
    out = blockwise(chorus, signal, BLOCK)
    assert np.all(out[:441] == 0.0)
    assert np.array_equal(out[441:], signal[:-441])


def test_chorus_interpolates_a_fractional_delay():
    # 50.5 samples of fixed delay: an impulse lands half on each of two
    # neighbouring output samples.
    per_ms = SR / 1000.0
    chorus = Chorus(rate_hz=0.0, depth_ms=0.0, centre_delay_ms=50.5 / per_ms, mix=1.0, voices=1, sample_rate=SR)
    out = chorus.process(impulse(128))
    assert out[50] == pytest.approx(0.5) and out[51] == pytest.approx(0.5)
    assert np.count_nonzero(out) == 2


def test_chorus_lfo_phase_carries_across_blocks():
    chorus = Chorus(rate_hz=1.0, sample_rate=SR)
    assert chorus.phase == 0.0
    chorus.process(np.zeros(SR // 4, dtype=np.float32))
    assert chorus.phase == pytest.approx(math.pi / 2)
    chorus.process(np.zeros(SR // 4, dtype=np.float32))
    assert chorus.phase == pytest.approx(math.pi)
    chorus.reset()
    assert chorus.phase == 0.0


def test_chorus_peak_detune_matches_the_research_figure():
    assert Chorus(rate_hz=1.0, depth_ms=2.0, sample_rate=SR).peak_detune_cents() == pytest.approx(21.7, abs=0.2)
    assert Chorus(rate_hz=0.5, depth_ms=3.0, sample_rate=SR).peak_detune_cents() == pytest.approx(16.2, abs=0.2)


def test_chorus_depth_is_clamped_so_the_read_never_overtakes_the_write():
    chorus = Chorus(centre_delay_ms=5.0, depth_ms=50.0, sample_rate=SR)
    assert chorus.depth_ms == 5.0 - config.EFFECT_CHORUS_MIN_DELAY_MS
    assert chorus.min_delay == pytest.approx(config.EFFECT_CHORUS_MIN_DELAY_MS * SR / 1000.0)
    assert chorus.chunk >= 1
    assert chorus.size > chorus.max_delay + BLOCK


def test_chorus_voice_count_is_clamped_and_taps_are_spread():
    assert Chorus(voices=0, sample_rate=SR).voices == 1
    assert Chorus(voices=99, sample_rate=SR).voices == config.EFFECT_CHORUS_MAX_VOICES
    assert Chorus(voices="lots", sample_rate=SR).voices == config.EFFECT_CHORUS_VOICES
    spreads = Chorus(voices=4, sample_rate=SR)._spreads
    assert np.allclose(spreads, [0, math.pi / 2, math.pi, 3 * math.pi / 2])


# --- shared bus: linearity -------------------------------------------------

def test_effects_are_linear_so_bus_equals_per_voice_to_float32_epsilon():
    """#104's arithmetic argument for one shared bus, re-measured: effect(a
    + b) and effect(a) + effect(b) agree to float32 epsilon for both
    effects, so per-voice routing would buy nothing but cost."""
    a, b = sine(261.63, seconds=0.5), sine(329.63, seconds=0.5)
    for make in (lambda: Delay(time=0.05, feedback=0.4, mix=0.5, sample_rate=SR),
                 lambda: Chorus(mix=0.5, voices=3, sample_rate=SR)):
        bus = blockwise(make(), a + b, BLOCK)
        per_voice = blockwise(make(), a, BLOCK) + blockwise(make(), b, BLOCK)
        assert np.max(np.abs(bus - per_voice)) < 4 * np.finfo(np.float32).eps


# --- process() contract ----------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda: Delay(sample_rate=SR), lambda: Chorus(sample_rate=SR),
    lambda: EffectsChain([Delay(sample_rate=SR)]), lambda: EffectsChain(),
])
def test_process_never_mutates_its_input_and_returns_float32_of_the_same_length(make):
    effect = make()
    block = sine(440.0, seconds=0.02)
    before = block.copy()
    out = effect.process(block)
    assert np.array_equal(block, before)
    assert out is not block
    assert out.dtype == np.float32 and out.shape == block.shape
    assert effect.process(np.zeros(0, dtype=np.float32)).shape == (0,)


def test_process_accepts_float64_input_and_still_returns_float32():
    out = Delay(sample_rate=SR).process(np.zeros(16, dtype=np.float64))
    assert out.dtype == np.float32


# --- EffectsChain ------------------------------------------------------------

def test_empty_chain_is_the_identity_and_returns_a_copy():
    chain = EffectsChain()
    block = sine(440.0, seconds=0.01)
    out = chain.process(block)
    assert np.array_equal(out, block) and out is not block
    assert len(chain) == 0 and list(chain) == []


def test_chain_applies_effects_in_order_and_is_itself_an_effect():
    d = Delay(time=0.01, feedback=0.0, mix=1.0, sample_rate=SR)
    c = Chorus(rate_hz=0.0, depth_ms=0.0, centre_delay_ms=10.0, mix=1.0, voices=1, sample_rate=SR)
    chain = EffectsChain([d, c])
    out = chain.process(impulse(2000))
    assert out[882] == 1.0 and np.count_nonzero(out) == 1      # 441 + 441
    nested = EffectsChain([EffectsChain([Delay(time=0.01, feedback=0.0, mix=1.0, sample_rate=SR)]),
                           Chorus(rate_hz=0.0, depth_ms=0.0, centre_delay_ms=10.0, mix=1.0, voices=1, sample_rate=SR)])
    assert np.array_equal(nested.process(impulse(2000)), out)


def test_chain_prepare_and_reset_propagate():
    chain = EffectsChain([Delay(time=0.01, mix=1.0, feedback=0.0, sample_rate=SR), Chorus(sample_rate=SR)])
    chain.prepare(22050, 256)
    assert chain[0].sample_rate == 22050 and chain[0].delay_samples == 220 and chain[0].block_size == 256
    assert chain[1].sample_rate == 22050 and chain[1].block_size == 256
    chain.process(impulse(100))
    chain.reset()
    assert np.all(chain.process(np.zeros(1000, dtype=np.float32)) == 0.0)


# --- the patch-spec registry -------------------------------------------------

def test_build_effect_from_an_effectspec_a_dict_and_a_synonym():
    spec = EffectSpec(type="delay", params={"time": 0.5, "feedback": 0.2, "mix": 0.1})
    delay = build_effect(spec, sample_rate=SR, block_size=BLOCK)
    assert isinstance(delay, Delay)
    assert (delay.time, delay.feedback, delay.mix) == (0.5, 0.2, 0.1)
    assert delay.sample_rate == SR and delay.block_size == BLOCK
    from_dict = build_effect({"type": "Chorus", "rate": 2.0, "depth": 1.0, "delay_ms": 8.0, "voices": 2})
    assert isinstance(from_dict, Chorus)
    assert (from_dict.rate_hz, from_dict.depth_ms, from_dict.centre_delay_ms, from_dict.voices) == (2.0, 1.0, 8.0, 2)
    assert build_effect({"type": "delay", "delay_seconds": 0.75}).time == 0.75


def test_unknown_effect_type_is_none_not_an_error():
    assert build_effect(EffectSpec(type="reverb", params={"size": 0.8})) is None
    assert build_effect({"type": ""}) is None
    assert build_effect(EffectSpec()) is None


def test_bad_param_values_degrade_like_patch_format_does():
    delay = build_effect({"type": "delay", "time": "soon", "feedback": 7.0, "mix": -3})
    assert delay.time == config.EFFECT_DELAY_TIME_SECONDS       # wrong type -> default
    assert delay.feedback == config.EFFECT_DELAY_MAX_FEEDBACK    # out of range -> clamped
    assert delay.mix == 0.0
    assert build_effect({"type": "delay", "time": float("nan")}).time == config.EFFECT_DELAY_TIME_SECONDS


def test_chain_from_specs_keeps_file_order_and_records_skipped_types():
    chain = chain_from_specs([
        EffectSpec(type="chorus"), EffectSpec(type="reverb", params={"size": 0.9}),
        {"type": "delay", "time": 0.3},
    ], sample_rate=SR, block_size=BLOCK)
    assert [type(e).__name__ for e in chain] == ["Chorus", "Delay"]
    assert chain.skipped == ["reverb"]
    assert chain_from_specs(None).effects == [] and chain_from_specs([]).skipped == []


def test_chain_from_patch_reads_a_real_patch_and_preserves_an_unknown_type_for_round_trip():
    patch = patch_from_toml(tomllib.loads("""
name = "Wet"
[[effects]]
type = "delay"
time = 0.125
feedback = 0.5
[[effects]]
type = "reverb"
size = 0.7
[[effects]]
type = "chorus"
voices = 2
"""))
    chain = chain_from_patch(patch, sample_rate=SR, block_size=BLOCK)
    assert [type(e).__name__ for e in chain] == ["Delay", "Chorus"]
    assert chain[0].time == 0.125 and chain[1].voices == 2
    assert chain.skipped == ["reverb"]
    assert [e.type for e in patch.effects] == ["delay", "reverb", "chorus"]   # untouched


def test_defaults_come_from_config():
    delay, chorus = Delay(sample_rate=SR), Chorus(sample_rate=SR)
    assert (delay.time, delay.feedback, delay.mix) == (
        config.EFFECT_DELAY_TIME_SECONDS, config.EFFECT_DELAY_FEEDBACK, config.EFFECT_DELAY_MIX)
    assert (chorus.rate_hz, chorus.depth_ms, chorus.centre_delay_ms, chorus.mix, chorus.voices) == (
        config.EFFECT_CHORUS_RATE_HZ, config.EFFECT_CHORUS_DEPTH_MS, config.EFFECT_CHORUS_CENTRE_DELAY_MS,
        config.EFFECT_CHORUS_MIX, config.EFFECT_CHORUS_VOICES)
    assert "Delay(" in repr(delay) and "Chorus(" in repr(chorus) and "EffectsChain(" in repr(EffectsChain())


# --- tail_seconds ------------------------------------------------------------

def test_tail_seconds_counts_repeats_until_inaudible_and_is_capped():
    assert tail_seconds(EffectsChain()) == 0.0
    assert tail_seconds(EffectsChain([Chorus(sample_rate=SR)])) == 0.0
    single = tail_seconds(EffectsChain([Delay(time=0.5, feedback=0.0, sample_rate=SR)]))
    assert single == pytest.approx(1.0)                          # one echo, plus its own length
    fed = tail_seconds(EffectsChain([Delay(time=0.1, feedback=0.5, sample_rate=SR)]))
    assert fed == pytest.approx(0.1 * (math.log(config.EFFECT_TAIL_FLOOR) / math.log(0.5) + 1.0))
    long = tail_seconds([Delay(time=2.0, feedback=0.95, sample_rate=SR)])
    assert long == config.EFFECT_MAX_TAIL_SECONDS
