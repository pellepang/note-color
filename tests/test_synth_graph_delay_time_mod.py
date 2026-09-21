"""Issue #228, decision 73: delay `time` becomes a real modulation
destination, on the back of a per-sample interpolated ring read.

Decision 67 left `time` `modulatable=False` on both delay modules with a
sentence, because a moving read offset is not a knob substitution -- it is
a different read path, and a half-built one crackles on every integer jump.
`tests/test_synth_graph_mod_stage2.py` used to assert that refusal; it now
asserts the acceptance, and this file is the proof the acceptance is
earned rather than a `modulatable=True` that reads nothing (exactly the
gap stage 2 found in `key_tracking`).

What is pinned here, in order of how much it would hurt to lose:

- **The interpolation is real and correct.** A constant *fractional* delay
  splits an impulse across two output samples with the linear weights the
  fraction asks for -- the one test that would fail if the read quietly
  rounded to whole frames, which is precisely the artefact #228 exists to
  avoid.
- **The two paths agree.** A live buffer holding the knob's own value
  produces (to within a fraction of a sample) what the unmodulated path
  produces, so patching a cable does not step the sound.
- **The block-delay guarantee survives the move** (decision 63): however
  far a modulation excursion pushes the time down, no sample of a block
  reads anything that same block wrote. `Delay` stays a whole block behind
  and `ShortDelay`'s chunk loop stays behind its own chunk -- including
  the one extra frame the *upper* interpolation tap needs.
- **Contract rule 2**: the new read path allocates nothing per block.
- **It survives a real LFO through a compiled graph**, not just a
  hand-driven module.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, NoteContext, ProcessContext
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.lfo import Lfo
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.modules.short_delay import ShortDelay

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    """`test_synth_graph_mod_stage2.py`'s helper verbatim -- a module driven
    by hand with the scalar-or-buffer arrays bound the way `compile()`
    binds them."""
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.smoothed, note=note,
        param_buffers=module.params.buffers, param_mod_active=module.params.mod_active,
    )


def drive_time(module, ctx, seconds):
    """Put a live `time` buffer on `module`, filled with `seconds` (a
    scalar or a per-sample array), the way a mod cable would."""
    idx = module.params.index("time")
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:ctx.frames] = seconds


# -- the cable is accepted at all ---------------------------------------


@pytest.mark.parametrize("module_factory", [Delay, ShortDelay])
def test_time_accepts_a_mod_cable_on_both_modules(module_factory):
    g = ModuleGraph()
    g.add("lfo", Lfo())
    g.add("delay", module_factory())
    verdict = g.judge_modulation("lfo", "mod", "delay", "time")
    assert verdict.ok


# -- the interpolation is real ------------------------------------------


@pytest.mark.parametrize("fraction,expect", [
    (0.0, (1.0, 0.0)),
    (0.25, (0.75, 0.25)),
    (0.5, (0.5, 0.5)),
    (0.75, (0.25, 0.75)),
])
def test_a_constant_fractional_delay_splits_an_impulse_linearly(fraction, expect):
    """The sharp test. At a delay of `10 + f` frames an impulse must come
    back weighted `1-f` at output sample 10 and `f` at sample 11 -- which
    is linear interpolation between the two ring positions either side,
    and which an integer-rounded read could not produce for any `f` but 0.

    Run at a 1 kHz sample rate purely so the frame counts are small enough
    to write down: nothing in the read path knows the rate.
    """
    rate = 1000.0
    frames = 64
    module = ShortDelay()
    ctx = host(module, frames=frames, sample_rate=rate)
    module.params.set_immediate("mix", 1.0)      # wet only -- the dry copy
    module.params.set_immediate("feedback", 0.0)  # would sit on top of it
    ctx.inputs[0][0] = 1.0
    drive_time(module, ctx, (10.0 + fraction) / rate)
    module.process(ctx)

    out = ctx.outputs[0]
    assert out[10] == pytest.approx(expect[0], abs=1e-9)
    assert out[11] == pytest.approx(expect[1], abs=1e-9)
    rest = np.delete(out, [10, 11])
    assert np.max(np.abs(rest)) < 1e-9
    # And the energy is conserved rather than rounded away.
    assert np.sum(out) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("module_factory,seconds", [
    (Delay, 0.05),
    (ShortDelay, 0.012),
])
def test_a_live_buffer_at_the_knob_value_matches_the_unmodulated_path(
        module_factory, seconds):
    """Patching a cable that happens to be sitting still must not change
    the sound. The two read paths are different code, so this is the test
    that keeps them the same signal -- to within the sub-sample offset the
    interpolated path's own one-frame floor can introduce, hence a
    correlation/RMS comparison rather than equality."""
    source = np.sin(2 * np.pi * 220.0 * np.arange(BLOCK) / SAMPLE_RATE)

    plain = module_factory()
    ctx_a = host(plain)
    plain.params.set_immediate("time", seconds)
    plain.params.set_immediate("mix", 1.0)
    plain.params.set_immediate("feedback", 0.0)
    ctx_a.inputs[0][:] = source
    for _ in range(4):
        plain.process(ctx_a)

    modulated = module_factory()
    ctx_b = host(modulated)
    modulated.params.set_immediate("time", seconds)
    modulated.params.set_immediate("mix", 1.0)
    modulated.params.set_immediate("feedback", 0.0)
    ctx_b.inputs[0][:] = source
    drive_time(modulated, ctx_b, seconds)
    for _ in range(4):
        modulated.process(ctx_b)

    a, b = ctx_a.outputs[0], ctx_b.outputs[0]
    assert np.sqrt(np.mean((a - b) ** 2)) < 0.02 * np.sqrt(np.mean(a ** 2)) + 1e-6


@pytest.mark.parametrize("module_factory,low,high", [
    (Delay, 0.02, 0.06),
    (ShortDelay, 0.004, 0.02),
])
def test_a_swept_time_changes_the_output(module_factory, low, high):
    """`modulatable=True` that reads nothing is the failure mode stage 2
    found elsewhere; this is the check that it did not happen here. A
    swept buffer and a still one at the same mean must not produce the
    same block."""
    source = np.sin(2 * np.pi * 330.0 * np.arange(BLOCK) / SAMPLE_RATE)
    sweep = np.linspace(low, high, BLOCK)
    outs = []
    for seconds in (sweep, (low + high) / 2):
        module = module_factory()
        ctx = host(module)
        module.params.set_immediate("mix", 1.0)
        module.params.set_immediate("feedback", 0.0)
        ctx.inputs[0][:] = source
        drive_time(module, ctx, seconds)
        for _ in range(4):
            module.process(ctx)
        outs.append(ctx.outputs[0].copy())
    assert np.max(np.abs(outs[0] - outs[1])) > 0.05
    assert np.all(np.isfinite(outs[0]))


# -- the block-delay guarantee, under modulation ------------------------


def test_delay_still_reads_a_whole_block_behind_when_time_is_driven_to_zero():
    """Decision 63's `block_delay = 1` is what makes a cable-built feedback
    loop legal, and it is a *read-position* promise, not a knob range. An
    LFO asking for a zero delay must be clamped, not obeyed: nothing this
    block wrote may appear in this block's output."""
    module = Delay()
    ctx = host(module)
    module.params.set_immediate("mix", 1.0)
    module.params.set_immediate("feedback", 0.0)
    ctx.inputs[0][:] = 1.0
    drive_time(module, ctx, 0.0)
    module.process(ctx)
    assert np.max(np.abs(ctx.outputs[0])) < 1e-12
    assert module.descriptor().block_delay == 1


def test_short_delay_terminates_and_stays_finite_at_a_zero_driven_time():
    """`ShortDelay`'s chunk length is derived from the *smallest* delay in
    the block, so a buffer driven to zero is the case that could divide by
    nothing and spin forever. It is clamped to two frames, which is a
    chunk of one -- slow, but finite, and the module's own floor already
    allowed a delay that short from the knob."""
    module = ShortDelay()
    ctx = host(module, frames=64)
    module.params.set_immediate("mix", 1.0)
    module.params.set_immediate("feedback", 0.9)
    ctx.inputs[0][:] = 0.5
    drive_time(module, ctx, -1.0)   # past zero: a bipolar depth can do this
    for _ in range(4):
        module.process(ctx)
    assert np.all(np.isfinite(ctx.outputs[0]))


def test_short_delay_never_reads_a_sample_its_own_block_wrote():
    """The chunked equivalent of the test above it: with unity feedback and
    a wet-only mix, an impulse must emerge as a train of *separated*
    echoes. If a chunk's interpolated read reached into the samples that
    same chunk was about to write, the impulse would feed back on itself
    within the chunk and the output would run away instead."""
    rate = 1000.0
    frames = 64
    module = ShortDelay()
    ctx = host(module, frames=frames, sample_rate=rate)
    module.params.set_immediate("mix", 1.0)
    module.params.set_immediate("feedback", 0.95)
    ctx.inputs[0][0] = 1.0
    drive_time(module, ctx, 5.5 / rate)
    for _ in range(8):
        module.process(ctx)
        # Unity-ish feedback on a linear delay can only ever decay here;
        # anything above the input is the read having eaten its own write.
        assert np.max(np.abs(ctx.outputs[0])) <= 1.0 + 1e-9
        ctx.inputs[0][0] = 0.0


# -- contract rule 2 -----------------------------------------------------


@pytest.mark.parametrize("module_factory,seconds", [
    (Delay, 0.05),
    (ShortDelay, 0.012),
])
def test_the_interpolated_read_allocates_nothing_per_block(module_factory, seconds):
    """Contract rule 2, aimed at the path this ticket added. The
    `FractionalReader`'s seven scratch arrays are filled in `activate()`;
    if any of them were being built per block the peak would scale with
    the block size, which is what the two sizes here separate."""
    def peak_bytes(frames):
        module = module_factory()
        ctx = host(module, frames=frames)
        module.params.set_immediate("mix", 0.5)
        ctx.inputs[0][:] = 0.25
        drive_time(module, ctx, seconds)
        for _ in range(4):
            module.process(ctx)
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

    grew = peak_bytes(2048) - peak_bytes(64)
    assert grew < 4096, f"per-block allocation scales with block size: {grew} bytes"


# -- a real LFO, through a compiled graph --------------------------------


def test_an_lfo_swept_onto_short_delay_time_runs_as_a_chorus():
    """The end the ticket was opened for: an LFO onto a short delay's time
    is a chorus. Driven through `ModuleGraph.compile()` rather than by
    hand, so the cable, the depth and the per-block buffer binding are all
    the real ones."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("saw"))
    g.add("lfo", Lfo())
    g.add("delay", ShortDelay())
    assert g.connect("osc", "out", "delay", "in").ok
    assert g.connect_modulation("lfo", "mod", "delay", "time", depth=0.5).ok
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    compiled = g.compile()
    node = g.node("delay").module
    node.params.set_immediate("time", 0.012)
    node.params.set_immediate("mix", 0.5)
    node.params.set_immediate("feedback", 0.2)
    g.node("lfo").module.params.set_immediate("rate", 4.0)

    note = NoteContext(frequency=220.0)
    seen = []
    for _ in range(16):
        compiled.process(BLOCK, note=note)
        buffer = compiled.buffer("delay")
        assert np.all(np.isfinite(buffer))
        seen.append(buffer.copy())
    # The LFO is moving, so successive blocks of a *constant* input must
    # not be identical -- that is the sweep being audible at all.
    assert np.max(np.abs(seen[-1] - seen[-2])) > 1e-6
