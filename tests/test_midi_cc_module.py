"""`audio/graph/modules/midi_cc.ExternalCc` (issue #173, decision 72): a
mod-wheel-shaped modulation source with no waveform of its own -- just
whatever `set_value()` was last told. No device, no Qt; a direct
contract-level test the same way `test_synth_poly.py` exercises other
modules against a plain `Activation`/`ProcessContext`.
"""

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, ProcessContext
from notecolor.audio.graph.modules.midi_cc import ExternalCc

SAMPLE_RATE = 44100
BLOCK = 512


def _activated():
    module = ExternalCc()
    module.activate(Activation(SAMPLE_RATE, BLOCK))
    return module


def test_descriptor_is_poly_once_with_one_mod_out_port():
    module = ExternalCc()
    descriptor = module.descriptor()
    assert descriptor.poly == contract.POLY_ONCE
    ports = module.ports()
    assert len(ports) == 1
    assert ports[0].kind == contract.PORT_MOD
    assert ports[0].direction == contract.DIRECTION_OUT


def test_default_value_is_zero():
    module = _activated()
    out = np.zeros(BLOCK)
    ctx = ProcessContext(frames=BLOCK, sample_rate=SAMPLE_RATE, outputs=(out,),
                         params=module.params.values)
    module.process(ctx)
    assert np.all(out == 0.0)


def test_set_value_is_held_flat_for_the_whole_block():
    module = _activated()
    module.set_value(0.6)
    out = np.zeros(BLOCK)
    ctx = ProcessContext(frames=BLOCK, sample_rate=SAMPLE_RATE, outputs=(out,),
                         params=module.params.values)
    module.process(ctx)
    assert np.all(out == 0.6)


def test_set_value_is_clamped_to_0_1():
    module = _activated()
    module.set_value(5.0)
    out = np.zeros(BLOCK)
    ctx = ProcessContext(frames=BLOCK, sample_rate=SAMPLE_RATE, outputs=(out,),
                         params=module.params.values)
    module.process(ctx)
    assert np.all(out == 1.0)

    module.set_value(-5.0)
    module.process(ctx)
    assert np.all(out == 0.0)


def test_new_instance_starts_at_zero_regardless_of_the_original():
    """A voice clone must not inherit whatever value the once-only original
    happened to hold -- moot in practice (this module is always POLY_ONCE,
    never cloned into voices), but the base `Module.new_instance()`
    contract this relies on is still worth pinning down."""
    module = ExternalCc()
    module.set_value(0.9)
    clone = module.new_instance()
    assert isinstance(clone, ExternalCc)
    assert clone._value == 0.0
