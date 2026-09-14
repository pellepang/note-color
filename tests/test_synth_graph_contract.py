"""The module contract (#202) and the two modules written against it.

Headless and silent, per this repo's "pure logic unit-tested, real I/O
smoke-tested" convention -- no audio device is opened anywhere in here, and
none is needed: every claim the contract makes is a claim about arrays.

The four claims worth testing, as opposed to the many that are just
dataclass plumbing:

- a parameter survives the round trip through the 0..1 a plugin ABI and a
  knob widget both speak, including the log-scaled ones;
- `process()` allocates nothing, which is the rule that is easy to write
  down and easy to break on the next edit;
- a per-note module really is independent per instance, because that is the
  difference between sixteen voices and one voice sixteen times as loud;
- the delay's output for a block does not depend on that block's input,
  which is the whole basis of decision 56 §4's cycle rule.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Activation, Module, ModuleDescriptor, NoteContext, ParamBlock, ParamSpec,
    PortSpec, ProcessContext, audio_in, audio_out,
)
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.oscillator import WavetableOscillator

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    """Activate `module` and build the `ProcessContext` a real host would:
    one buffer per declared port, allocated once, reused every block."""
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.values, note=note,
    )


# -- parameters --------------------------------------------------------------


def test_linear_parameter_round_trips_through_normalized():
    spec = ParamSpec("mix", "Mix", 0.0, 1.0, 0.5)
    for value in (0.0, 0.25, 0.5, 1.0):
        assert spec.denormalize(spec.normalize(value)) == pytest.approx(value)


def test_log_parameter_puts_the_midpoint_at_the_geometric_mean():
    # The reason log exists: a 20Hz..20kHz cutoff knob whose middle is
    # 10kHz is a knob that does nothing for its first three quarters.
    spec = ParamSpec("cutoff", "Cutoff", 20.0, 20000.0, 1000.0, log=True)
    assert spec.denormalize(0.5) == pytest.approx(np.sqrt(20.0 * 20000.0))
    assert spec.normalize(1000.0) == pytest.approx(
        np.log(50.0) / np.log(1000.0), rel=1e-9)


def test_stepped_parameter_lands_on_a_position():
    spec = ParamSpec("octave", "Octave", -4, 4, 0, steps=9)
    assert spec.denormalize(0.5) == 0
    assert spec.denormalize(0.0) == -4
    assert spec.denormalize(1.0) == 4
    assert spec.denormalize(0.6) == round(spec.denormalize(0.6))


def test_clamp_keeps_a_knob_from_handing_the_audio_thread_nonsense():
    spec = ParamSpec("level", "Level", 0.0, 1.0, 0.8)
    assert spec.clamp(2.0) == 1.0
    assert spec.clamp(-5.0) == 0.0


def test_a_log_parameter_may_not_start_at_zero():
    with pytest.raises(contract.ContractError):
        ParamSpec("time", "Time", 0.0, 2.0, 0.25, log=True)


def test_param_block_is_a_float_array_not_an_object_graph():
    block = ParamBlock([ParamSpec("a", "A", 0.0, 1.0, 0.25),
                        ParamSpec("b", "B", 0.0, 10.0, 4.0)])
    assert block.values.dtype == np.float64
    assert list(block.values) == [0.25, 4.0]
    block.set("b", 99.0)
    assert block.values[1] == 10.0  # clamped, not rejected
    assert block.dirty == 1
    assert block.snapshot() == {"a": 0.25, "b": 10.0}


def test_param_block_restore_ignores_parameters_it_does_not_have():
    # A patch saved by a newer build, or one naming a plugin parameter that
    # went away between versions: drop the stranger, keep the patch.
    block = ParamBlock([ParamSpec("a", "A", 0.0, 1.0, 0.25)])
    block.restore({"a": 0.5, "gone": 1.0})
    assert block.get("a") == 0.5


def test_asking_for_an_unknown_parameter_is_a_contract_error():
    block = ParamBlock([ParamSpec("a", "A", 0.0, 1.0, 0.25)])
    with pytest.raises(contract.ContractError):
        block.index("nope")


# -- validation --------------------------------------------------------------


class _Bare(Module):
    """A module with the minimum the contract requires, for validation
    tests -- deliberately not one of the real two, so a change to either
    cannot quietly make these pass."""

    def __init__(self, ports):
        self._ports = tuple(ports)

    def descriptor(self):
        return ModuleDescriptor("test.bare", "Bare")

    def ports(self):
        return self._ports

    def process(self, ctx):
        for buf in ctx.outputs:
            buf[:ctx.frames] = 0.0


def test_validate_rejects_a_duplicate_port_id():
    with pytest.raises(contract.ContractError):
        contract.validate(_Bare([audio_out("out"), audio_out("out", "Out 2")]))


def test_validate_rejects_a_module_nothing_can_be_patched_out_of():
    with pytest.raises(contract.ContractError):
        contract.validate(_Bare([audio_in("in")]))


def test_unknown_port_kind_is_caught_at_construction():
    with pytest.raises(contract.ContractError):
        PortSpec("x", "X", kind="smell")


def test_an_unknown_poly_mode_is_caught_at_construction():
    with pytest.raises(contract.ContractError):
        ModuleDescriptor("test.x", "X", poly="sometimes")


def test_processing_before_activate_names_the_module():
    module = WavetableOscillator()
    with pytest.raises(contract.ContractError) as excinfo:
        module.require_active()
    assert "osc.wavetable.saw" in str(excinfo.value)


# -- the oscillator ----------------------------------------------------------


def dominant_hz(samples, sample_rate=SAMPLE_RATE):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return np.fft.rfftfreq(len(samples), 1.0 / sample_rate)[int(np.argmax(spectrum))]


def test_oscillator_plays_the_note_the_host_put_in_the_context():
    module = WavetableOscillator("saw")
    note = NoteContext(pitch=69, frequency=440.0)
    ctx = host(module, frames=4096, note=note)
    module.process(ctx)
    assert dominant_hz(ctx.outputs[0]) == pytest.approx(440.0, abs=15.0)


def test_oscillator_follows_its_tuning_parameters():
    module = WavetableOscillator("saw")
    ctx = host(module, frames=4096, note=NoteContext(frequency=440.0))
    module.params.set("octave", 1)
    module.params.set("semitones", 0)
    module.process(ctx)
    assert dominant_hz(ctx.outputs[0]) == pytest.approx(880.0, abs=20.0)


def test_a_block_split_in_two_renders_the_same_samples():
    # Phase has to survive a block boundary, or every 512 samples there is a
    # click. The direct test, as `test_synth_engine.py` does it for voices.
    whole = WavetableOscillator("saw")
    ctx = host(whole, frames=512, note=NoteContext(frequency=440.0))
    whole.process(ctx)
    reference = ctx.outputs[0].copy()

    split = WavetableOscillator("saw")
    ctx2 = host(split, frames=512, note=NoteContext(frequency=440.0))
    ctx2.frames = 256
    split.process(ctx2)
    first = ctx2.outputs[0][:256].copy()
    split.process(ctx2)
    second = ctx2.outputs[0][:256].copy()

    assert np.allclose(np.concatenate([first, second]), reference)


def test_each_note_gets_its_own_instance_with_its_own_phase():
    # `new_instance()` is what makes a per-note module sixteen voices
    # rather than one voice read sixteen times.
    template = WavetableOscillator("square")
    a, b = template.new_instance(), template.new_instance()
    assert a is not b and b.waveform == "square"
    ctx_a = host(a, note=NoteContext(frequency=220.0))
    ctx_b = host(b, note=NoteContext(frequency=330.0))
    a.process(ctx_a)
    b.process(ctx_b)
    assert not np.allclose(ctx_a.outputs[0], ctx_b.outputs[0])
    assert a._phase != b._phase


def test_a_silent_oscillator_writes_silence_rather_than_leaving_the_buffer():
    # The host's buffers are reused every block; a module that returns early
    # without writing leaks the previous block into the mix.
    module = WavetableOscillator("saw")
    ctx = host(module, note=NoteContext(frequency=440.0))
    module.process(ctx)
    assert np.any(ctx.outputs[0] != 0.0)
    module.params.set("level", 0.0)
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)


def test_the_square_waveform_is_a_pulse_and_its_width_is_continuous():
    module = WavetableOscillator("square")
    ctx = host(module, frames=4096, note=NoteContext(frequency=220.0))
    module.process(ctx)
    even = ctx.outputs[0].copy()
    module.reset()
    module.params.set("pulse_width", 0.15)
    module.process(ctx)
    # A narrow pulse spends most of its cycle at one rail, so its mean
    # moves; a table-per-waveform oscillator could not do this at all.
    assert abs(even.mean()) < abs(ctx.outputs[0].mean())


# -- the delay ---------------------------------------------------------------


def test_the_delay_output_cannot_depend_on_this_block_s_input():
    """Decision 56 §4's whole basis: the graph may order a cycle through
    this module because block K's output is fixed before block K's input is
    read. Tested by changing only the current block's input."""
    module = Delay()
    ctx = host(module)
    module.params.set("time", 0.001)  # below one block; floored anyway
    module.params.set("mix", 1.0)

    ctx.inputs[0][:] = 1.0
    module.process(ctx)
    first = ctx.outputs[0].copy()

    module.reset()
    ctx.inputs[0][:] = -7.0
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], first)
    assert np.all(first == 0.0)  # and it is silence, not the input


def test_a_delay_reports_the_block_delay_the_cycle_rule_looks_for():
    assert Delay().descriptor().block_delay >= 1
    assert WavetableOscillator().descriptor().block_delay == 0


def test_an_impulse_comes_back_one_delay_time_later():
    module = Delay()
    ctx = host(module)
    module.params.set("time", BLOCK * 3 / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.0)

    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    ctx.inputs[0][0] = 0.0
    for _ in range(2):
        module.process(ctx)
        assert np.all(ctx.outputs[0] == 0.0)
    module.process(ctx)
    assert ctx.outputs[0][0] == pytest.approx(1.0)


def test_feedback_decays_rather_than_growing():
    module = Delay()
    ctx = host(module)
    module.params.set("time", BLOCK / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.5)
    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    ctx.inputs[0][0] = 0.0
    peaks = []
    for _ in range(4):
        module.process(ctx)
        peaks.append(np.max(np.abs(ctx.outputs[0])))
    assert peaks == sorted(peaks, reverse=True)
    assert peaks[0] == pytest.approx(1.0)
    assert peaks[1] == pytest.approx(0.5)


def test_mix_at_zero_passes_the_dry_signal_through_untouched():
    module = Delay()
    ctx = host(module)
    module.params.set("mix", 0.0)
    ctx.inputs[0][:] = np.linspace(-1.0, 1.0, BLOCK)
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], ctx.inputs[0])


def test_reset_clears_the_line_so_a_new_note_does_not_inherit_an_echo():
    module = Delay()
    ctx = host(module)
    module.params.set("time", BLOCK / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    ctx.inputs[0][:] = 1.0
    module.process(ctx)
    module.reset()
    ctx.inputs[0][:] = 0.0
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)


# -- the rule that is easiest to break ---------------------------------------


#: The two block sizes the allocation test compares. Far enough apart that a
#: single missing `out=` is unmistakable.
SMALL_BLOCK, LARGE_BLOCK = 64, 2048


def peak_bytes(module, frames, note):
    """Peak traced memory over 32 blocks at `frames`, above the steady
    state.

    *Peak*, not a snapshot difference, and that distinction is the whole
    test. A NumPy temporary is freed the moment the expression ends, so
    before/after snapshots of live memory show nothing at all whether the
    module allocates or not; the peak is the only measure that sees a
    buffer which existed briefly. Real hosts feel exactly that buffer --
    an allocator call inside a 11.6ms deadline.
    """
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
    64-frame one. `tracemalloc`'s own bookkeeping costs a few kB either
    way; that cost does not care how long the block is, and a leaked
    temporary does."""
    small = build()
    small.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    large = build()
    large.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    return (peak_bytes(large, LARGE_BLOCK, note)
            - peak_bytes(small, SMALL_BLOCK, note))


class _Allocating(WavetableOscillator):
    """A module that breaks the rule in the most ordinary way: an
    expression without `out=`. Present so the test above is known to be
    capable of failing -- an allocation check that has never seen an
    allocation is a check nobody should trust."""

    def process(self, ctx):
        n = ctx.frames
        ctx.outputs[0][:n] = np.arange(n) * 0.5 + 1.0


def test_the_allocation_check_can_actually_fail():
    assert allocation_growth(_Allocating, NoteContext(frequency=440.0)) > 16 * 1024


@pytest.mark.parametrize("build,note", [
    (lambda: WavetableOscillator("saw"), NoteContext(frequency=440.0)),
    (lambda: WavetableOscillator("square"), NoteContext(frequency=440.0)),
    (Delay, None),
])
def test_process_allocates_nothing_that_scales_with_the_block(build, note):
    """Contract rule 2, measured rather than asserted in a comment.

    The bar is one 2048-frame float64 array (16kB) -- the smallest thing a
    missing `out=` can cost at this block size. Nothing here comes close:
    the real modules measure within a few dozen bytes of flat.
    """
    grew = allocation_growth(build, note)
    assert grew < 16 * 1024, f"{build().descriptor().module_id} grew {grew} bytes with the block"
