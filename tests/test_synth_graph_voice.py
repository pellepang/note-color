"""The per-note voice modules (#205): filter, amp envelope, noise -- and the
parity test that asks whether wiring them up really does reproduce the fixed
engine they were ported from.

Headless and silent, like every other test here: no audio device is opened,
and none is needed, because every claim below is a claim about arrays.

What is worth testing, as opposed to what is plumbing:

- the filter is the *same* filter as `synth_engine`'s, coefficients and all,
  and each instance carries its own recurrence -- sixteen voices sharing one
  `zi` is one filter fed a chord;
- **the envelope ends the note**, which is the single fact `poly.PolyGraph`
  is waiting for and the reason a released note currently drones
  (decision 61 §4);
- nothing allocates per block except the two places SciPy forces it to, and
  those are measured rather than excused;
- and the parity claim from #205's "done when", tested for what it can
  actually establish and documented for what it cannot.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.settings import config
from notecolor.settings import patch_format
from notecolor.audio import synth_engine
from notecolor.audio.sound_engine import NoteOn, frequency_for
from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, NoteContext, ProcessContext
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.poly import MixModule, PolyGraph
from notecolor.audio.graph.modules.envelope import AmpEnvelope
from notecolor.audio.graph.modules.filter import FILTER_TYPES, StateVariableFilter
from notecolor.audio.graph.modules.noise import PINK, WHITE, Noise
from notecolor.audio.graph.modules.oscillator import WavetableOscillator

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    """Activate `module` and build the `ProcessContext` a real host would --
    the same helper `test_synth_graph_contract.py` uses, repeated rather than
    imported so neither file's conventions can drift into the other's."""
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.values, note=note,
    )


def band_energy(samples, low, high, sample_rate=SAMPLE_RATE):
    """Energy between `low` and `high` Hz. How a filter claim is stated
    without asserting anything about a particular sample."""
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / sample_rate)
    mask = (freqs >= low) & (freqs < high)
    return float(np.sum(spectrum[mask] ** 2))


def white_noise(frames, seed=7):
    return np.random.default_rng(seed).uniform(-1.0, 1.0, frames)


# -- every module is a legal module ------------------------------------------


@pytest.mark.parametrize("module,poly", [
    # The filter runs on either side of Mix (decision 64): per-note for a
    # filter per voice, once-only for a master filter over the summed mix.
    (StateVariableFilter(), contract.POLY_EITHER),
    # The other two are per-note and could hardly be otherwise: an amp
    # envelope is opened by a note's own gate, and a noise source right of
    # Mix would be one hiss rather than one per voice.
    (AmpEnvelope(), contract.POLY_PER_NOTE),
    (Noise(), contract.POLY_PER_NOTE),
])
def test_every_new_module_satisfies_the_contract(module, poly):
    assert contract.validate(module) is module
    assert module.descriptor().poly == poly
    # None of these may close a feedback loop; only a delay line may.
    assert module.descriptor().block_delay == 0


# -- the filter ---------------------------------------------------------------


def test_the_lowpass_keeps_what_is_below_cutoff_and_removes_what_is_above():
    module = StateVariableFilter()
    ctx = host(module, frames=8192, note=NoteContext(pitch=60))
    module.params.set("cutoff", 1000.0)
    ctx.inputs[0][:] = white_noise(8192)
    module.process(ctx)
    low = band_energy(ctx.outputs[0], 200.0, 700.0)
    high = band_energy(ctx.outputs[0], 6000.0, 12000.0)
    assert high < low / 100.0


def test_the_type_knob_turns_the_same_filter_into_a_highpass():
    """The whole reason decision 42 chose an SVF: one denominator, one extra
    output tap, so three filter types cost one structure."""
    module = StateVariableFilter()
    ctx = host(module, frames=8192, note=NoteContext(pitch=60))
    module.params.set("cutoff", 1000.0)
    module.params.set("type", FILTER_TYPES.index("hp"))
    ctx.inputs[0][:] = white_noise(8192)
    module.process(ctx)
    low = band_energy(ctx.outputs[0], 200.0, 700.0)
    high = band_energy(ctx.outputs[0], 6000.0, 12000.0)
    assert low < high / 100.0


def test_resonance_puts_a_peak_at_the_cutoff():
    module = StateVariableFilter()
    ctx = host(module, frames=8192, note=NoteContext(pitch=60))
    module.params.set("cutoff", 2000.0)
    ctx.inputs[0][:] = white_noise(8192)
    module.process(ctx)
    flat = band_energy(ctx.outputs[0], 1800.0, 2200.0)

    module.reset()
    module.params.set("resonance", 1.0)
    module.process(ctx)
    assert band_energy(ctx.outputs[0], 1800.0, 2200.0) > flat * 4.0


def test_key_tracking_opens_the_filter_for_a_higher_note():
    """Without it, high notes sound muffled relative to low ones -- the
    reason `synth_engine.modulated_cutoff()` has the term at all. Applied in
    octaves, so the same knob means the same thing at C1 and at C7."""
    def brightness(pitch):
        module = StateVariableFilter()
        ctx = host(module, frames=8192, note=NoteContext(pitch=pitch))
        module.params.set("cutoff", 500.0)
        module.params.set("key_tracking", 1.0)
        ctx.inputs[0][:] = white_noise(8192)
        module.process(ctx)
        return band_energy(ctx.outputs[0], 1500.0, 4000.0)

    assert brightness(84) > brightness(36) * 100.0


def test_key_tracking_at_one_follows_the_note_exactly():
    # The claim "1.0 means the cutoff follows the note" is a claim about
    # coefficients, and is best checked against them rather than inferred
    # from a spectrum.
    module = StateVariableFilter()
    ctx = host(module, note=NoteContext(pitch=72))
    module.params.set("cutoff", 1000.0)
    module.params.set("key_tracking", 1.0)
    module.process(ctx)
    expected, _ = synth_engine.svf_coefficients(2000.0, 0.1, SAMPLE_RATE, "lp")
    assert module._b == pytest.approx(np.array(expected))


def test_a_filtered_block_split_in_two_renders_the_same_samples():
    # `zi` has to survive a block boundary or the recurrence restarts from
    # silence every 512 samples, which is a buzz at the block rate.
    source = white_noise(BLOCK)

    whole = StateVariableFilter()
    ctx = host(whole, note=NoteContext(pitch=60))
    whole.params.set("cutoff", 800.0)
    ctx.inputs[0][:] = source
    whole.process(ctx)
    reference = ctx.outputs[0].copy()

    split = StateVariableFilter()
    ctx2 = host(split, note=NoteContext(pitch=60))
    split.params.set("cutoff", 800.0)
    ctx2.inputs[0][:] = source
    ctx2.frames = 256
    split.process(ctx2)
    first = ctx2.outputs[0][:256].copy()
    ctx2.inputs[0][:256] = source[256:]
    split.process(ctx2)
    second = ctx2.outputs[0][:256].copy()

    assert np.allclose(np.concatenate([first, second]), reference)


def test_reset_clears_the_recurrence_so_a_new_note_starts_silent():
    module = StateVariableFilter()
    ctx = host(module, note=NoteContext(pitch=60))
    module.params.set("cutoff", 300.0)
    ctx.inputs[0][:] = 1.0
    module.process(ctx)
    module.reset()
    ctx.inputs[0][:] = 0.0
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)


def test_each_voice_gets_a_filter_with_its_own_history():
    # A shared `zi` is sixteen notes going through one filter, which is a
    # chord through one filter -- a different and much duller instrument.
    template = StateVariableFilter()
    a, b = template.new_instance(), template.new_instance()
    assert a is not b
    ctx_a = host(a, note=NoteContext(pitch=60))
    ctx_b = host(b, note=NoteContext(pitch=60))
    ctx_a.inputs[0][:] = 1.0
    a.process(ctx_a)
    b.process(ctx_b)  # fed silence
    assert np.any(ctx_a.outputs[0] != 0.0)
    assert np.all(ctx_b.outputs[0] == 0.0)


# -- the amp envelope ---------------------------------------------------------


def envelope_host(frames=BLOCK, note=None, **params):
    module = AmpEnvelope()
    ctx = host(module, frames=frames, note=note)
    for param_id, value in params.items():
        module.params.set(param_id, value)
    module.reset()  # so a delay/attack set after activation really applies
    ctx.inputs[0][:] = 1.0
    return module, ctx


def test_the_envelope_rises_to_one_and_settles_at_sustain():
    note = NoteContext(velocity=1.0)
    module, ctx = envelope_host(frames=4096, note=note,
                                attack=0.01, decay=0.02, sustain=0.5)
    module.process(ctx)
    out = ctx.outputs[0]
    assert out[0] < 0.1                         # starts from silence
    assert np.max(out) == pytest.approx(1.0)    # reaches the top of attack
    assert out[-1] == pytest.approx(0.5)        # and settles at sustain


def test_a_delay_stage_set_after_activation_still_holds_the_note_off():
    # `reset()` is what re-reads the stage lengths, so a delay knob moved
    # after `activate()` has to survive into the next note rather than being
    # skipped because the envelope already decided it was in ATTACK.
    note = NoteContext(velocity=1.0)
    module, ctx = envelope_host(frames=4096, note=note, delay=0.05, attack=0.001)
    module.process(ctx)
    assert np.all(ctx.outputs[0][:2000] == 0.0)
    assert ctx.outputs[0][-1] > 0.9


def test_a_released_envelope_finishes_the_note():
    """The fact the whole voice lifecycle turns on (decision 61 §4): nothing
    reclaims a voice until a module says the note is over, and this is the
    module that says it."""
    note = NoteContext(velocity=1.0)
    module, ctx = envelope_host(frames=BLOCK, note=note,
                                attack=0.001, decay=0.001, sustain=1.0,
                                release=0.005)
    module.process(ctx)
    assert not note.finished  # held: the gate is still up

    note.gate = False
    module.process(ctx)       # 512 frames is longer than a 5ms release
    assert note.finished
    assert ctx.outputs[0][-1] == pytest.approx(0.0)


def test_a_held_note_is_never_finished_however_long_it_is_held():
    note = NoteContext(velocity=1.0)
    module, ctx = envelope_host(note=note, attack=0.001, sustain=0.6)
    for _ in range(40):
        module.process(ctx)
    assert not note.finished
    assert ctx.outputs[0][-1] == pytest.approx(0.6)


def test_a_repeated_note_off_does_not_restart_the_release():
    """Otherwise a key held down by a stuck event source would ring the note
    on forever, one restarted fade at a time."""
    note = NoteContext(velocity=1.0)
    module, ctx = envelope_host(note=note, attack=0.001, sustain=1.0, release=0.05)
    module.process(ctx)
    note.gate = False
    module.process(ctx)
    after_first = ctx.outputs[0][-1]
    module.process(ctx)       # the gate is still down: a second note-off
    assert ctx.outputs[0][0] < after_first


def test_the_release_resumes_across_a_block_boundary():
    """A note-off can arrive mid-note, so the envelope cannot be a function
    of time-since-onset. Rendering one long block and two short ones has to
    give the same samples."""
    def render(chunks):
        note = NoteContext(velocity=1.0)
        module, ctx = envelope_host(frames=1024, note=note,
                                    attack=0.001, sustain=1.0, release=0.02)
        module.process(ctx)
        note.gate = False
        pieces = []
        for n in chunks:
            ctx.frames = n
            module.process(ctx)
            pieces.append(ctx.outputs[0][:n].copy())
        return np.concatenate(pieces)

    assert np.allclose(render([1024]), render([300, 724]))


def test_velocity_scales_the_note_and_can_be_switched_off():
    def peak(velocity, amount):
        note = NoteContext(velocity=velocity)
        module, ctx = envelope_host(note=note, attack=0.001, sustain=1.0,
                                    velocity=amount)
        module.process(ctx)
        return np.max(ctx.outputs[0])

    assert peak(0.5, 1.0) == pytest.approx(0.5)
    # At amount 0 a patch ignores velocity entirely -- an organ.
    assert peak(0.5, 0.0) == pytest.approx(1.0)


def test_the_envelope_is_silent_when_nothing_is_patched_into_it():
    # An unconnected input is the host's shared zero buffer; a gain applied
    # to silence is silence, and never the previous block left in place.
    note = NoteContext(velocity=1.0)
    module = AmpEnvelope()
    ctx = host(module, note=note)
    ctx.outputs[0][:] = 99.0
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)


# -- noise --------------------------------------------------------------------


def test_white_noise_is_broadband_and_pink_noise_tilts_downwards():
    module = Noise(seed=3)
    ctx = host(module, frames=16384)
    module.params.set("level", 1.0)
    module.process(ctx)
    white_low = band_energy(ctx.outputs[0], 100.0, 400.0, SAMPLE_RATE)
    white_high = band_energy(ctx.outputs[0], 6400.0, 12800.0, SAMPLE_RATE)
    # White is flat per Hz, so a five-octave-wide high band holds far more
    # energy than a two-octave-wide low one.
    assert white_high > white_low

    module.params.set("colour", PINK)
    module.reset()
    module.process(ctx)
    pink_low = band_energy(ctx.outputs[0], 100.0, 400.0, SAMPLE_RATE)
    pink_high = band_energy(ctx.outputs[0], 6400.0, 12800.0, SAMPLE_RATE)
    assert pink_low / pink_high > (white_low / white_high) * 10.0


def test_pink_noise_sits_at_roughly_the_same_loudness_as_white():
    """`config.SYNTH_PINK_GAIN` exists so the colour knob changes the colour
    and not the volume; a make-up gain that is wrong is heard as the knob
    being a second level control."""
    module = Noise(seed=5)
    ctx = host(module, frames=32768)
    module.params.set("level", 1.0)
    module.process(ctx)
    white_rms = float(np.sqrt(np.mean(ctx.outputs[0] ** 2)))
    module.params.set("colour", PINK)
    module.reset()
    module.process(ctx)
    pink_rms = float(np.sqrt(np.mean(ctx.outputs[0] ** 2)))
    assert 0.5 < pink_rms / white_rms < 2.0


def test_two_voices_get_uncorrelated_noise():
    # Sixteen voices from one seed would be one signal summed sixteen times
    # -- a correlated 16x boost, not noise.
    template = Noise(seed=11)
    a, b = template.new_instance(), template.new_instance()
    ctx_a, ctx_b = host(a, frames=4096), host(b, frames=4096)
    a.params.set("level", 1.0)
    b.params.set("level", 1.0)
    a.process(ctx_a)
    b.process(ctx_b)
    correlation = float(np.corrcoef(ctx_a.outputs[0], ctx_b.outputs[0])[0, 1])
    assert abs(correlation) < 0.1


def test_noise_at_level_zero_writes_silence_rather_than_leaving_the_buffer():
    module = Noise(seed=13)
    ctx = host(module)
    module.process(ctx)
    assert np.any(ctx.outputs[0] != 0.0)
    module.params.set("level", 0.0)
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)


def test_the_colour_knob_is_stepped_and_modulation_cannot_reach_it():
    colour = next(p for p in Noise().parameters() if p.param_id == "colour")
    assert colour.steps == 2 and not colour.modulatable
    assert colour.denormalize(0.0) == WHITE and colour.denormalize(1.0) == PINK


# -- the rule that is easiest to break ----------------------------------------


SMALL_BLOCK, LARGE_BLOCK = 64, 2048

#: One block-sized float64 array. The exact size of the temporary
#: `scipy.signal.lfilter` returns, and therefore the budget for the two
#: modules that call it.
ONE_BLOCK_BYTES = 8 * LARGE_BLOCK

#: Headroom for `tracemalloc`'s own bookkeeping and the handful of ndarray
#: headers a block touches. Constant with respect to the block size, which
#: is the whole point of measuring two block sizes rather than one.
SLACK_BYTES = 4096


def peak_bytes(module, frames, note):
    """Peak traced memory over 32 blocks at `frames`, above the steady state
    -- `test_synth_graph_contract.py`'s measurement, repeated here for the
    reason given in that file: a NumPy temporary is freed the moment the
    expression ends, so only the *peak* can see it."""
    ctx = ProcessContext(
        frames=frames, sample_rate=SAMPLE_RATE,
        inputs=tuple(np.full(LARGE_BLOCK, 0.5)
                     for p in module.ports() if p.direction == contract.DIRECTION_IN),
        outputs=tuple(np.zeros(LARGE_BLOCK)
                      for p in module.ports() if p.direction == contract.DIRECTION_OUT),
        params=module.params.values, note=note,
    )
    for _ in range(4):
        module.process(ctx)  # warm every cache and lazy import
    gc.collect()
    tracemalloc.start()
    for _ in range(4):
        module.process(ctx)
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    for _ in range(32):
        module.process(ctx)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak - base


def allocation_growth(build, note):
    """How much more a module allocates at a 2048-frame block than at a
    64-frame one. `build(max_block)` returns an already-activated module, so
    a test can set a stepped parameter that changes which code path runs."""
    small = build(LARGE_BLOCK)
    large = build(LARGE_BLOCK)
    return peak_bytes(large, LARGE_BLOCK, note) - peak_bytes(small, SMALL_BLOCK, note)


def _activated(module, max_block, **params):
    module.activate(Activation(SAMPLE_RATE, max_block))
    for param_id, value in params.items():
        module.params.set(param_id, value)
    module.reset()
    return module


@pytest.mark.parametrize("build,note,budget", [
    (lambda n: _activated(AmpEnvelope(), n, sustain=0.8), NoteContext(velocity=0.9), 0),
    (lambda n: _activated(Noise(seed=1), n, colour=WHITE), NoteContext(), 0),
    # The two that call `scipy.signal.lfilter`, which has no `out=` and
    # returns a fresh block-sized array every call. See `filter.py`'s
    # docstring: the departure from contract rule 2 is real, it is scipy's
    # and not ours, and the honest thing is to bound it rather than to
    # pretend the number is zero.
    (lambda n: _activated(StateVariableFilter(), n, cutoff=800.0),
     NoteContext(pitch=60), ONE_BLOCK_BYTES),
    (lambda n: _activated(Noise(seed=1), n, colour=PINK), NoteContext(), ONE_BLOCK_BYTES),
])
def test_process_allocates_no_more_than_its_stated_budget(build, note, budget):
    grew = allocation_growth(build, note)
    assert grew < budget + SLACK_BYTES, (
        f"{build(SMALL_BLOCK).descriptor().module_id} grew {grew} bytes with the "
        f"block, against a budget of {budget}")


def test_the_two_scipy_modules_really_do_pay_for_that_budget():
    """The budget above is only honest if it is *used*. If a future SciPy
    grew an `out=` and the allocation went away, this test fails and the
    budget should be deleted rather than quietly carried forever."""
    grew = allocation_growth(
        lambda n: _activated(StateVariableFilter(), n, cutoff=800.0), NoteContext(pitch=60))
    assert grew > ONE_BLOCK_BYTES / 2


# -- parity with the fixed engine ---------------------------------------------


def parity_patch():
    """A patch chosen so that the graph and `SynthVoice` are describing the
    same signal path -- see `test_the_graph_matches_the_fixed_engine` for
    what each setting is switching *off* and why."""
    patch = patch_format.new_patch(name="Parity")
    patch.osc1.waveform = "saw"
    patch.osc1.level = 1.0
    patch.osc2.level = 0.0          # no second oscillator module yet
    patch.noise.level = 0.0         # noise is random; it cannot be compared
    patch.filter.type = "lp"
    patch.filter.cutoff = 1800.0
    patch.filter.resonance = 0.3
    patch.filter.env_amount = 0.0   # no filter-envelope module yet
    patch.filter.key_tracking = 0.4
    patch.lfo.depth = 0.0           # no LFO module yet
    patch.voice.glide = 0.0         # no glide anywhere in the graph
    patch.voice.volume = 1.0        # the graph has no master volume
    patch.voice.velocity_to_amp = 1.0
    patch.voice.velocity_to_filter = 0.0
    patch.amp_env.delay = 0.0
    patch.amp_env.hold = 0.0
    patch.amp_env.attack = 0.008
    patch.amp_env.decay = 0.25
    patch.amp_env.sustain = 0.7
    patch.amp_env.release = 0.25
    return patch


def parity_graph(patch, pitch, velocity):
    """Osc -> Filter -> Amp Env -> Mix, one voice, knobs set from `patch`."""
    graph = ModuleGraph()
    graph.add("osc", WavetableOscillator(patch.osc1.waveform))
    graph.add("filter", StateVariableFilter())
    graph.add("env", AmpEnvelope())
    graph.add("mix", MixModule())
    assert graph.connect("osc", "out", "filter", "in").ok
    assert graph.connect("filter", "out", "env", "in").ok
    assert graph.connect("env", "out", "mix", "in").ok

    poly = PolyGraph(graph, voices=1).activate(Activation(SAMPLE_RATE, BLOCK))
    poly.set_parameter("osc", "level", patch.osc1.level)
    poly.set_parameter("filter", "cutoff", patch.filter.cutoff)
    poly.set_parameter("filter", "resonance", patch.filter.resonance)
    poly.set_parameter("filter", "type", FILTER_TYPES.index(patch.filter.type))
    poly.set_parameter("filter", "key_tracking", patch.filter.key_tracking)
    for stage in ("delay", "hold", "attack", "decay", "sustain", "release"):
        poly.set_parameter("env", stage, getattr(patch.amp_env, stage))
    poly.set_parameter("env", "velocity", patch.voice.velocity_to_amp)
    poly.note_on(pitch, velocity=velocity, frequency=frequency_for(pitch))
    return poly


def test_the_graph_matches_the_fixed_engine(request):
    """#205's "done when": the same patch, wired in the equivalent order,
    against the engine the modules were ported from.

    **What this establishes.** Osc -> Filter -> Amp Env through a real
    `ModuleGraph`/`PolyGraph` reproduces `synth_engine.SynthVoice` *sample
    for sample* over the whole life of a note, note-off and release
    included. That is a strong claim and it holds because nothing was
    reimplemented: the tables, the SVF coefficients, the `lfilter` call and
    the envelope walk are the same code reached from two different shapes.
    The first block is **bit-identical**. Later blocks drift, by 1.1e-12 at
    block 1 and ~8e-12 by the end of the note -- about -220 dB, and growing
    linearly with the note's length rather than diverging. The cause is
    known and is not the signal path: the two phase accumulators associate
    the same terms differently (`(phase + steps) - dt` against
    `steps + (phase - dt)`, and `phase + steps[-1]` against `phase + dt*n`
    for the carried phase), so they round differently in the last bit.

    **What it cannot establish, stated plainly.** The patch above switches
    off every part of `SynthVoice` the graph has no module for yet, and each
    of those is a real gap rather than a test convenience:

    - **the LFO** (`lfo.depth = 0`) -- there is no LFO module (#208);
    - **the filter envelope** (`filter.env_amount = 0`) -- there is no
      second envelope module, and no modulation cable to carry it;
    - **glide** (`voice.glide = 0`) -- nothing in the graph slides a pitch;
    - **osc 2 and noise** -- osc 2 needs a second oscillator and a summed
      input (both exist, but then `SynthVoice._source_scale` has no
      counterpart in the graph, which is decision 61's marked-jack summing
      and not a gain stage); noise is random and cannot be compared at all;
    - **velocity to filter** and **master volume** -- `SynthVoice` voice
      settings with no module to live on.

    Most of that list has one consequence in common, and it is the real
    limitation: with every modulation source off, **nothing in this patch
    varies at control rate**. `SynthVoice` runs a 64-sample sub-block grid
    and updates filter coefficients on it; the graph module computes
    coefficients once per *block*. This test cannot see that difference,
    because with a constant cutoff `SynthVoice`'s run-merging collapses its
    grid to one `lfilter` call per block -- the identical operation. A
    patch with a moving cutoff would **not** match, and that is a known
    difference in the graph engine, not a bug in this test.
    """
    patch = parity_patch()
    pitch, velocity = 72, 0.8   # not 60, so key tracking is actually exercised
    poly = parity_graph(patch, pitch, velocity)
    voice = synth_engine.SynthVoice(
        NoteOn(pitch=pitch, velocity=velocity), SAMPLE_RATE, patch=patch)

    blocks = []
    for index in range(32):
        if index == 4:  # note-off at the same boundary on both sides
            poly.note_off(pitch)
            voice.note_off()
        poly.process(BLOCK)
        reference = np.zeros(BLOCK, dtype=np.float64)
        voice.render(reference, BLOCK)
        blocks.append((poly.buffer("mix")[:BLOCK].copy(), reference))

    # `PolyGraph.process()` scales the Mix sum by `config.GRAPH_MIX_HEADROOM`
    # (#222) before the once-only side runs; `SynthVoice` has no such stage
    # and never will (decision 56 §7 keeps the fixed engine untouched), so
    # the reference is scaled by the same constant for the comparison --
    # the same `np.multiply` the graph itself applies, so bit-exactness
    # survives it.
    first_graph, first_reference = blocks[0]
    assert np.any(first_reference != 0.0)           # the comparison is not of silence
    assert np.array_equal(first_graph, first_reference * config.GRAPH_MIX_HEADROOM)

    for index, (got, want) in enumerate(blocks):
        assert np.max(np.abs(got - want * config.GRAPH_MIX_HEADROOM)) < 1e-10, \
            f"block {index} diverged"

    # And the note really did end on both sides, at the same block.
    assert voice.finished
    assert not poly.active_voices
    assert np.all(blocks[-1][0] == 0.0)


def test_the_graph_and_the_fixed_engine_disagree_once_the_filter_moves():
    """The honest other half of the parity claim. With `filter.env_amount`
    turned up, `SynthVoice` sweeps its cutoff on a 64-sample grid and the
    graph holds it flat for the whole block, because there is no
    filter-envelope module and no modulation layer (#208) to carry one. The
    two therefore differ -- and asserting that they *do* is what keeps the
    test above from being read as a claim the graph reproduces the engine in
    general."""
    patch = parity_patch()
    patch.filter.env_amount = 0.8
    patch.filter_env.attack = 0.05
    patch.filter_env.decay = 0.2
    patch.filter_env.sustain = 0.3
    pitch, velocity = 72, 0.8
    poly = parity_graph(patch, pitch, velocity)
    voice = synth_engine.SynthVoice(
        NoteOn(pitch=pitch, velocity=velocity), SAMPLE_RATE, patch=patch)

    poly.process(BLOCK)
    reference = np.zeros(BLOCK, dtype=np.float64)
    voice.render(reference, BLOCK)
    assert not np.allclose(poly.buffer("mix")[:BLOCK], reference)


def test_the_envelope_hands_its_voice_slot_back_to_the_poly_graph():
    """The end-to-end version of decision 61 §4: `note_off()` only clears the
    gate, and the voice is reclaimed because the envelope in the patch said
    the note was over."""
    patch = parity_patch()
    patch.amp_env.release = 0.01
    poly = parity_graph(patch, 60, 1.0)
    poly.process(BLOCK)
    assert len(poly.active_voices) == 1

    poly.note_off(60)
    poly.process(BLOCK)     # 512 frames at 44.1kHz is longer than 10ms
    assert not poly.active_voices


def test_a_patch_with_no_envelope_still_drones():
    """The other side of the same decision, and the reason it is a module's
    job: take the envelope out and the released note keeps sounding, exactly
    as a modular with no envelope patched does."""
    graph = ModuleGraph()
    graph.add("osc", WavetableOscillator("saw"))
    graph.add("mix", MixModule())
    assert graph.connect("osc", "out", "mix", "in").ok
    poly = PolyGraph(graph, voices=1).activate(Activation(SAMPLE_RATE, BLOCK))
    poly.note_on(60)
    poly.process(BLOCK)
    poly.note_off(60)
    poly.process(BLOCK)
    assert poly.active_voices
    assert np.any(poly.buffer("mix")[:BLOCK] != 0.0)


def test_sixteen_voices_each_run_their_own_copy_of_every_module():
    """`new_instance()` across a three-module chain, which is what makes the
    per-note side sixteen voices rather than one voice sixteen times as
    loud."""
    patch = parity_patch()
    poly = parity_graph(patch, 60, 1.0)
    graph = ModuleGraph()
    graph.add("osc", WavetableOscillator("saw"))
    graph.add("filter", StateVariableFilter())
    graph.add("env", AmpEnvelope())
    graph.add("mix", MixModule())
    for source, dest in (("osc", "filter"), ("filter", "env"), ("env", "mix")):
        assert graph.connect(source, "out", dest, "in").ok
    many = PolyGraph(graph, voices=config.POLYPHONY_SYNTH_VIEW)
    many.activate(Activation(SAMPLE_RATE, BLOCK))
    filters = {id(many.module("filter", voice=i)) for i in range(many.voice_count)}
    envelopes = {id(many.module("env", voice=i)) for i in range(many.voice_count)}
    assert len(filters) == many.voice_count
    assert len(envelopes) == many.voice_count
    assert poly.voice_count == 1
