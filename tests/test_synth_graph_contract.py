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


def test_the_delay_floors_its_own_minimum_rather_than_trusting_the_knob():
    """#206's second requirement: the minimum is the *module's* to enforce.

    Turned right down, the delay must still be a whole block long, because
    `graph.py` has already ordered a feedback loop around the promise that
    it is. Nothing in the graph re-checks this -- shortening it later (a
    compiled core, sub-blocks) is meant to need no graph change, which only
    holds while the rule lives here.
    """
    module = Delay()
    ctx = host(module, frames=BLOCK)
    module.params.set("time", 0.0)          # clamped to the spec's 1ms...
    assert module.params.get("time") == pytest.approx(0.001)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.0)

    # ...and 1ms at 44100 is 44 frames, an eighth of a block. If the knob
    # were obeyed the impulse would come back inside this same block.
    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)
    ctx.inputs[0][0] = 0.0
    module.process(ctx)
    assert ctx.outputs[0][0] == pytest.approx(1.0)  # exactly one block later


def test_a_short_block_size_shortens_the_floor_with_it():
    """The floor is `max_block`, not a constant. A host running 64-frame
    blocks gets a 1.45ms minimum delay for free -- which is the mechanism
    the ticket points at when it says the minimum can shrink later."""
    module = Delay()
    ctx = host(module, frames=64)
    module.params.set("time", 0.0)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.0)
    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    ctx.inputs[0][0] = 0.0
    module.process(ctx)
    assert ctx.outputs[0][0] == pytest.approx(1.0)


def test_the_delay_runs_on_either_side_of_mix():
    """#205: per-note for short uses, once-only for a real echo. Declaring
    it is the easy half; `tests/test_synth_poly.py` proves the other."""
    assert Delay().descriptor().poly == contract.POLY_EITHER


def test_a_clone_keeps_the_ring_length_it_was_built_with():
    """`max_seconds` is a construction choice, not a parameter, so the
    default `new_instance()` would silently hand sixteen voices a
    two-second buffer each when the module on the canvas was built for
    fifty milliseconds. Sixteen times 706kB is the difference."""
    original = Delay(max_seconds=0.05)
    clone = original.new_instance()
    assert clone.max_seconds == original.max_seconds
    assert clone.activation is None          # fresh and inactive, per the contract
    clone.activate(Activation(SAMPLE_RATE, BLOCK))
    assert clone.params.specs[0].maximum == pytest.approx(0.05)


def test_two_instances_do_not_share_a_delay_line():
    """The per-note case's real risk: sixteen voices sharing one ring is
    one voice heard sixteen times, and it is invisible until two notes are
    held at once."""
    a, b = Delay(), Delay().new_instance()
    ctx_a, ctx_b = host(a, frames=64), host(b, frames=64)
    for module in (a, b):
        module.params.set("time", 64 / SAMPLE_RATE)
        module.params.set("mix", 1.0)
        module.params.set("feedback", 0.0)
    ctx_a.inputs[0][:] = 1.0
    a.process(ctx_a)
    b.process(ctx_b)
    a.process(ctx_a)
    b.process(ctx_b)
    assert np.allclose(ctx_a.outputs[0], 1.0)
    assert np.all(ctx_b.outputs[0] == 0.0)


def test_how_the_host_splits_its_blocks_does_not_change_the_signal():
    """Ported from `tests/test_effects.py`, where `effects.py` earns it by
    chunking. Here it falls out of the one-block floor: any sub-block is
    shorter than the delay, so its reads can only touch samples an earlier
    sub-block wrote. Worth asserting for the same reason it is asserted
    there -- the claim is exact, not approximate, so drift fails loudly.
    """
    signal = np.sin(np.linspace(0.0, 40.0, 4 * BLOCK))

    def render(chunk):
        module = Delay()
        ctx = host(module, frames=BLOCK)
        module.params.set("time", BLOCK / SAMPLE_RATE)
        module.params.set("mix", 0.5)
        module.params.set("feedback", 0.7)
        module.params.set("damping", 0.4)   # carried state, so it is in the claim
        out = np.zeros(signal.size)
        for start in range(0, signal.size, chunk):
            n = min(chunk, signal.size - start)
            ctx.frames = n
            ctx.inputs[0][:n] = signal[start:start + n]
            module.process(ctx)
            out[start:start + n] = ctx.outputs[0][:n]
        return out

    whole = render(BLOCK)
    assert np.array_equal(render(BLOCK // 2), whole)
    assert np.array_equal(render(BLOCK // 8), whole)


def test_damping_makes_each_repeat_darker_than_the_last():
    """Ported from `effects.py`: the one-zero average sits in the feedback
    path, so it compounds over repeats instead of colouring the output
    once. Measured on an 8kHz tone, which is what a high-end rolloff is
    supposed to remove."""
    def tail(damping):
        module = Delay()
        ctx = host(module, frames=BLOCK)
        module.params.set("time", BLOCK / SAMPLE_RATE)
        module.params.set("mix", 1.0)
        module.params.set("feedback", 0.9)
        module.params.set("damping", damping)
        ctx.inputs[0][:] = np.sin(2 * np.pi * 8000 * np.arange(BLOCK) / SAMPLE_RATE)
        module.process(ctx)
        ctx.inputs[0][:] = 0.0
        levels = []
        for _ in range(10):
            module.process(ctx)
            levels.append(float(np.sqrt(np.mean(ctx.outputs[0] ** 2))))
        return levels

    dry, damped = tail(0.0), tail(1.0)
    # Undamped, feedback 0.9 costs about 0.9 of the level per repeat; damped,
    # an 8kHz tone loses far more than that, and the gap compounds. By the
    # tenth repeat it is a factor of three.
    assert damped[-1] < dry[-1] * 0.4
    assert all(later < earlier for earlier, later in zip(damped, damped[1:]))


def test_damping_off_is_the_untouched_signal_not_a_filter_at_zero():
    """Off means bypassed, not "a one-zero with a coefficient of zero" --
    the default has to be bit-identical to the module before damping
    existed, or every existing delay test is measuring something new."""
    def render(damping):
        module = Delay()
        ctx = host(module, frames=BLOCK)
        module.params.set("time", BLOCK / SAMPLE_RATE)
        module.params.set("mix", 1.0)
        module.params.set("feedback", 0.8)
        module.params.set("damping", damping)
        ctx.inputs[0][:] = np.linspace(-1.0, 1.0, BLOCK)
        out = []
        for _ in range(4):
            module.process(ctx)
            out.append(ctx.outputs[0].copy())
        return np.concatenate(out)

    assert np.array_equal(render(0.0), render(0.0))
    assert not np.array_equal(render(0.0), render(0.5))


# -- stability: counted, never corrected (decision 63 §4) --------------------


def test_a_non_finite_line_is_counted_and_left_alone():
    """The stability position, made testable. A NaN reaching the line is
    reported and **not** repaired: the module does not decide what the
    user's signal should have been. See decision 63 §4 for why a runaway is
    the patch's business and this is still worth counting -- a NaN never
    leaves a ring buffer on its own, so without a number to show, a patch
    that has gone silent-and-NaN looks exactly like a patch that is quiet.
    """
    module = Delay()
    ctx = host(module, frames=64)
    module.params.set("time", 64 / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.5)

    ctx.inputs[0][0] = float("nan")
    module.process(ctx)
    # Nothing yet: this block's output is the (clean) line from before, which
    # is the one-block guarantee showing up in the diagnostics too.
    assert module.nonfinite_blocks == 0

    ctx.inputs[0][0] = 0.0
    for _ in range(4):
        module.process(ctx)
    assert module.nonfinite_blocks == 4
    assert np.isnan(ctx.outputs[0]).any(), "the signal was repaired, which is not this module's call"

    # Recoverable, but only by the one operation that is allowed to discard
    # a signal: an explicit reset.
    module.reset()
    module.process(ctx)
    assert module.nonfinite_blocks == 0
    assert not np.isnan(ctx.outputs[0]).any()


def test_an_ordinary_loud_signal_is_not_reported_as_non_finite():
    """Loud is not broken. A patch-level runaway stays finite for thousands
    of blocks and is deliberately not flagged -- level is the user's."""
    module = Delay()
    ctx = host(module, frames=64)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.95)
    ctx.inputs[0][:] = 1e6
    for _ in range(32):
        module.process(ctx)
    assert module.nonfinite_blocks == 0


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


def _damped_delay():
    """A `Delay` whose damping knob starts on.

    A subclass rather than a `params.set()` because `allocation_growth()`
    activates whatever the factory returns and never touches it again --
    and damping off short-circuits the whole branch, so measuring the
    default would measure nothing.
    """
    class _Damped(Delay):
        def parameters(self):
            return tuple(
                ParamSpec(s.param_id, s.name, s.minimum, s.maximum,
                          0.4 if s.param_id == "damping" else s.default,
                          s.unit, s.log, s.steps, s.modulatable)
                for s in super().parameters())
    return _Damped()


def test_the_allocation_check_can_actually_fail():
    assert allocation_growth(_Allocating, NoteContext(frequency=440.0)) > 16 * 1024


@pytest.mark.parametrize("build,note", [
    (lambda: WavetableOscillator("saw"), NoteContext(frequency=440.0)),
    (lambda: WavetableOscillator("square"), NoteContext(frequency=440.0)),
    (Delay, None),
    # Damping is a second code path through `process()` with two more
    # buffers and a carried sample; it has to obey rule 2 as well.
    (_damped_delay, None),
])
def test_process_allocates_nothing_that_scales_with_the_block(build, note):
    """Contract rule 2, measured rather than asserted in a comment.

    The bar is one 2048-frame float64 array (16kB) -- the smallest thing a
    missing `out=` can cost at this block size. Nothing here comes close:
    the real modules measure within a few dozen bytes of flat.
    """
    grew = allocation_growth(build, note)
    assert grew < 16 * 1024, f"{build().descriptor().module_id} grew {grew} bytes with the block"
