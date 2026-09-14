"""The Level module (#218): a plain gain stage so a feedback loop can be
brought under unity gain, per decision 65 §8's flagged gap.

Same `host()` helper `test_synth_graph_voice.py` uses, repeated rather than
imported for the same reason that file gives: neither file's conventions
should drift into the other's.
"""

import numpy as np
import pytest

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, ProcessContext
from notecolor.audio.graph.modules.level import Level

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.values, note=note,
    )


def test_satisfies_the_contract():
    module = Level()
    assert contract.validate(module) is module
    descriptor = module.descriptor()
    assert descriptor.poly == contract.POLY_EITHER
    # A wire, not a delay: it must not be able to legalise a feedback loop.
    assert descriptor.block_delay == 0


def test_default_is_unity_gain():
    module = Level()
    ctx = host(module)
    ctx.inputs[0][:] = 0.5
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], 0.5)


def test_level_scales_the_signal():
    module = Level()
    ctx = host(module)
    module.params.set("level", 0.25)
    ctx.inputs[0][:] = 1.0
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], 0.25)


def test_negative_level_inverts_rather_than_refusing():
    """Bipolar on purpose (see the module docstring): inverting a repeat
    inside a feedback loop is a real timbral choice, not a mistake to
    guard against."""
    module = Level()
    ctx = host(module)
    module.params.set("level", -1.0)
    ctx.inputs[0][:] = np.linspace(-1.0, 1.0, BLOCK)
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], -ctx.inputs[0])


def test_level_can_bring_a_delay_loop_under_unity_gain():
    """The scenario decision 65 §8 names directly: a unity-gain delay
    (dry + wet) patched back into its own input has loop gain >= 1 the
    moment feedback is above zero. A Level at some value below one in that
    loop is what brings the round-trip gain back under one."""
    module = Level()
    ctx = host(module)
    module.params.set("level", 0.5)
    round_trip_gain = 1.05   # e.g. a delay's dry+wet just over unity
    ctx.inputs[0][:] = round_trip_gain
    module.process(ctx)
    assert float(ctx.outputs[0][0]) * round_trip_gain < round_trip_gain


def test_out_of_range_level_is_clamped_to_the_spec():
    module = Level()
    module.activate(Activation(SAMPLE_RATE, BLOCK))
    module.params.set("level", 10.0)
    assert module.params.get("level") == pytest.approx(2.0)
    module.params.set("level", -10.0)
    assert module.params.get("level") == pytest.approx(-2.0)


SMALL_BLOCK, LARGE_BLOCK = 64, 2048

#: Headroom for `tracemalloc`'s own bookkeeping -- `test_synth_graph_voice.py`
#: uses the same constant for the same reason: measuring the *growth*
#: between two block sizes is what makes a fixed amount of bookkeeping noise
#: wash out, rather than budgeting for it directly.
SLACK_BYTES = 4096


def peak_bytes(module, frames):
    """Peak traced memory over 32 blocks at `frames`, above the steady
    state. Same measurement `test_synth_graph_voice.py`'s allocation tests
    use, repeated here rather than imported so the two files' conventions
    stay independent."""
    import gc
    import tracemalloc

    ctx = ProcessContext(
        frames=frames, sample_rate=SAMPLE_RATE,
        inputs=tuple(np.full(LARGE_BLOCK, 0.5)
                     for p in module.ports() if p.direction == contract.DIRECTION_IN),
        outputs=tuple(np.zeros(LARGE_BLOCK)
                      for p in module.ports() if p.direction == contract.DIRECTION_OUT),
        params=module.params.values, note=None,
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
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak - base


def test_process_allocates_nothing():
    """No `lfilter` here, no departure to justify: `Level` is a multiply
    into a buffer the host already owns, so its budget is zero."""
    small = Level()
    small.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    large = Level()
    large.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    grew = peak_bytes(large, LARGE_BLOCK) - peak_bytes(small, SMALL_BLOCK)
    assert grew < SLACK_BYTES
