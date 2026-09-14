"""The short delay, and the master filter (decision 64).

Both are the owner's answers to questions #205 and #206 left open, and both
are small. The claims worth testing are the ones that distinguish these two
modules from the ones they sit beside:

- a delay **shorter than a block** is correct, which is the whole reason
  this module exists and the one thing `modules/delay.py` may not do;
- and it **cannot close a feedback loop**, which is the price and must stay
  true, because the graph orders loops on that promise;
- the filter works on the once-only side of Mix without a note to track.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.audio.graph import contract, graph
from notecolor.audio.graph.contract import Activation, NoteContext, ProcessContext
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.filter import StateVariableFilter
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.modules.short_delay import ShortDelay
from notecolor.audio.graph.poly import MixModule, PolyGraph, SIDE_MONO

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, note=None):
    module.activate(Activation(SAMPLE_RATE, frames))
    ports = module.ports()
    return ProcessContext(
        frames=frames, sample_rate=SAMPLE_RATE,
        inputs=tuple(np.zeros(frames) for p in ports
                     if p.direction == contract.DIRECTION_IN),
        outputs=tuple(np.zeros(frames) for p in ports
                      if p.direction == contract.DIRECTION_OUT),
        params=module.params.values, note=note,
    )


# -- the short delay ---------------------------------------------------------


def test_an_impulse_comes_back_inside_the_same_block():
    """The claim the module exists for. `modules/delay.py` floors every
    delay to a whole block, so the echo of sample 0 cannot arrive before
    the next block; here it arrives 200 samples later, in this one."""
    module = ShortDelay()
    ctx = host(module)
    module.params.set("time", 200 / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.0)
    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    assert ctx.outputs[0][200] == pytest.approx(1.0)
    assert np.all(ctx.outputs[0][:200] == 0.0)


def test_feedback_repeats_inside_one_block():
    # Several trips round the loop within 512 samples: the case chunking
    # exists for, and the one a naive read-whole-block-then-write gets
    # silently wrong.
    module = ShortDelay()
    ctx = host(module)
    module.params.set("time", 100 / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    module.params.set("feedback", 0.5)
    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    for trip in range(1, 5):
        assert ctx.outputs[0][100 * trip] == pytest.approx(0.5 ** (trip - 1))


def test_negative_feedback_inverts_every_other_repeat():
    # Half of what a flanger sounds like, and free at these lengths.
    module = ShortDelay()
    ctx = host(module)
    module.params.set("time", 100 / SAMPLE_RATE)
    module.params.set("mix", 1.0)
    module.params.set("feedback", -0.5)
    ctx.inputs[0][0] = 1.0
    module.process(ctx)
    assert ctx.outputs[0][100] == pytest.approx(1.0)
    assert ctx.outputs[0][200] == pytest.approx(-0.5)


def test_splitting_a_block_in_two_renders_the_same_samples():
    """Exact, not approximate. A delay whose output depends on where the
    host happened to cut the buffer is a delay that sounds different at a
    different block size."""
    whole = ShortDelay()
    ctx = host(whole)
    whole.params.set("time", 70 / SAMPLE_RATE)
    whole.params.set("feedback", 0.6)
    ctx.inputs[0][:] = np.sin(np.linspace(0, 40, BLOCK))
    whole.process(ctx)
    reference = ctx.outputs[0].copy()

    split = ShortDelay()
    ctx2 = host(split)
    split.params.set("time", 70 / SAMPLE_RATE)
    split.params.set("feedback", 0.6)
    np.copyto(ctx2.inputs[0], ctx.inputs[0])
    ctx2.frames = 256
    split.process(ctx2)
    first = ctx2.outputs[0][:256].copy()
    np.copyto(ctx2.inputs[0][:256], ctx.inputs[0][256:])
    split.process(ctx2)
    second = ctx2.outputs[0][:256].copy()
    assert np.array_equal(np.concatenate([first, second]), reference)


def test_mix_at_zero_passes_the_dry_signal_through_untouched():
    module = ShortDelay()
    ctx = host(module)
    module.params.set("mix", 0.0)
    ctx.inputs[0][:] = np.linspace(-1.0, 1.0, BLOCK)
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], ctx.inputs[0])


def test_reset_clears_the_line():
    module = ShortDelay()
    ctx = host(module)
    module.params.set("mix", 1.0)
    ctx.inputs[0][:] = 1.0
    module.process(ctx)
    module.reset()
    ctx.inputs[0][:] = 0.0
    module.process(ctx)
    assert np.all(ctx.outputs[0] == 0.0)


def test_a_voice_copy_keeps_its_own_ring_size():
    # `max_seconds` is a construction choice, so `new_instance()` has to
    # carry it -- the same trap `Delay` fell into.
    template = ShortDelay(max_seconds=0.01)
    copy = template.new_instance()
    assert copy.max_seconds == 0.01
    assert copy is not template


# -- the price: it cannot close a loop ---------------------------------------


def test_the_short_delay_promises_nothing_about_being_a_block_behind():
    assert ShortDelay().descriptor().block_delay == 0
    assert Delay().descriptor().block_delay == 1


def test_a_feedback_cable_through_the_short_delay_is_refused():
    """The price of sub-block times, and it must stay true: the graph
    orders every loop on the promise `block_delay >= 1` makes, and this
    module does not make it."""
    g = ModuleGraph()
    g.add("tone", StateVariableFilter())
    g.add("short", ShortDelay())
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    g.connect("tone", "out", "short", "in")
    verdict = g.judge("short", "out", "tone", "in")
    assert verdict.code == graph.REFUSE_CYCLE
    assert "Delay" in verdict.reason


def test_the_same_loop_is_accepted_through_the_long_delay():
    # Same shape, the other module: the refusal above is about this
    # module's promise, not about the patch.
    g = ModuleGraph()
    g.add("tone", StateVariableFilter())
    g.add("echo", Delay())
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    g.connect("tone", "out", "echo", "in")
    assert g.judge("echo", "out", "tone", "in").ok


# -- the master filter -------------------------------------------------------


def test_the_filter_may_now_sit_on_either_side_of_mix():
    assert StateVariableFilter().descriptor().poly == contract.POLY_EITHER


def test_a_master_filter_right_of_mix_runs_over_the_summed_voices():
    """The owner's ask (decision 64): each singer with their own tone
    control, and one more on the whole choir."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("saw"))
    g.add("mix", MixModule())
    g.add("master", StateVariableFilter())
    g.connect("osc", "out", "mix", "in")
    assert g.connect("mix", "out", "master", "in").ok
    p = PolyGraph(g, voices=4).activate(Activation(SAMPLE_RATE, BLOCK))
    assert p.sides()["master"] == SIDE_MONO

    p.module("master").params.set("cutoff", 300.0)
    for pitch in (60, 64, 67):
        p.note_on(pitch, frequency=110.0 * (1 + 0.3 * (pitch - 60)))
    for _ in range(4):
        p.process(BLOCK)

    # A lowpass well below the content: the master output must be quieter
    # than what went into it, and must not be silent.
    before = float(np.sqrt(np.mean(p.buffer("mix") ** 2)))
    after = float(np.sqrt(np.mean(p.buffer("master") ** 2)))
    assert 0.0 < after < before * 0.8


def test_key_tracking_is_a_no_op_on_the_once_only_side_rather_than_an_error():
    """A filter over sixteen notes at once has no single note to track, so
    the knob does nothing there -- the same thing every modular does with a
    control that has no source. What it must not do is crash."""
    module = StateVariableFilter()
    ctx = host(module, note=None)
    module.params.set("key_tracking", 1.0)
    ctx.inputs[0][:] = np.sin(np.linspace(0, 60, BLOCK))
    module.process(ctx)
    assert np.any(ctx.outputs[0] != 0.0)


# -- the audio thread's rule -------------------------------------------------


def test_the_short_delay_allocates_nothing_that_scales_with_the_block():
    def peak(frames):
        module = ShortDelay()
        module.activate(Activation(SAMPLE_RATE, 2048))
        module.params.set("feedback", 0.5)
        ctx = ProcessContext(
            frames=frames, sample_rate=SAMPLE_RATE,
            inputs=(np.full(2048, 0.25),), outputs=(np.zeros(2048),),
            params=module.params.values,
        )
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
        top = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return top - base

    # The chunk loop runs many more times at 2048 frames than at 64, so an
    # allocation per *chunk* -- an `np.arange` for read indices, which is
    # how `effects.py` does it -- would show up here far louder than an
    # allocation per block would.
    assert peak(2048) - peak(64) < 16 * 1024
