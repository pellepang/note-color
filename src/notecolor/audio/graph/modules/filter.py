"""The per-note filter (#205): the 2-pole state-variable filter of decision
42, unchanged, wearing the module contract.

The DSP is `synth_engine.svf_coefficients()` -- the bilinear transform of
the analog SVF `1 / (s^2 + k*s + 1)` with a `tan` prewarp, so the cutoff
knob lands where it says it does -- run by `scipy.signal.lfilter` with a
`zi` this instance carries between blocks. None of that is rewritten here
and none of it should be: decision 42 established that an IIR recurrence
cannot be vectorized along time by any arrangement of NumPy ops (594us a
block in a Python loop, versus 10.9us in `lfilter`'s C), and decision 56 §7
established that the graph engine lives beside the fixed engine rather than
forking it. What is new is only the shape.

**Why three filter types cost nothing.** All three share one denominator
and differ only in the numerator, which is the "one structure, one extra
output tap" property decision 42 chose the SVF for. So `type` is a stepped
parameter rather than three modules -- and it is `modulatable=False`,
because a modulation cable landing on it would sweep the filter through
highpass on its way from lowpass to bandpass, which is not a sound anyone
patched for.

**The one place this module cannot satisfy the contract, stated plainly.**
Contract rule 2 says `process()` allocates nothing. `scipy.signal.lfilter`
returns a freshly allocated output array and a freshly allocated `zf`; it
has no `out=` parameter, and there is no pure-NumPy substitute for the
recurrence (decision 42 is the measurement). So this module allocates
exactly one block-sized float64 array per block and nothing else -- 4kB at
this app's 512-frame block -- and copies it into the host's buffer. That is
a real, measured departure, it is bounded, it is scipy's and not ours, and
`tests/test_synth_graph_voice.py` asserts the bound rather than pretending
the number is zero. Removing it means a C or Cython inner loop behind
#145's seam, which is the same seam that would replace this whole module.

**Modulated cutoff/resonance (#208).** The scalar fast path above -- one
coefficient recompute a block -- updates at most 86 times a second (512
frames / 44100 Hz), which #103's own research already measured and named:
"a smooth filter sweep... versus an audibly stepped one". So when
`cutoff` or `resonance` has a live modulation buffer this block
(`ctx.param_mod_active`), `process()` instead recomputes coefficients
every `config.SYNTH_CONTROL_SUB_BLOCK` (64) frames -- the same control
rate `synth_engine`'s filter envelope and LFO already run at, for the
same reason, chosen there as #103's measured price knee. That means
several smaller `lfilter` calls instead of one -- still one allocation
per call, still scipy's, and smaller per call than the unmodulated case,
so the bound `test_synth_graph_voice.py` asserts does not move.

**SciPy is an optional extra (#111).** Imported lazily through
`synth_engine.signal_module()`, never at module scope, and required in
`_allocate()` -- off the audio thread -- so a patch containing a filter
refuses to *activate* on an install without SciPy rather than activating
and producing silence.
"""

from __future__ import annotations

import numpy as np

from notecolor.settings import config
from notecolor.audio import synth_engine
from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)

#: Stepped-parameter positions, in `patch_format.FILTER_TYPES` order so a
#: patch file's `filter.type` and this module's knob position mean the same
#: thing and neither has to be translated into the other.
FILTER_TYPES = ("lp", "hp", "bp")

#: Above middle C, in semitones, is what key tracking follows. The same
#: reference `synth_engine.modulated_cutoff()` uses.
KEY_TRACKING_CENTRE = 60


class StateVariableFilter(Module):
    """One 2-pole SVF, on either side of Mix.

    **Per-note**, the usual case: one filter per held note, each with its
    own `zi`, so sixteen notes are sixteen filters. That is what makes a
    filter sweep sound like a filter sweep on every note rather than on the
    chord as a whole.

    **Once-only**, right of Mix: a master filter over the summed voices.
    The owner asked for both (decision 64) -- "each singer with their own
    tone control, and one more on the whole choir" -- and #205's original
    per-note-only declaration was a caution rather than a limit. The two
    things it worried about turn out to be fine:

    - The `zi` is voice history, and a master filter's voice *is* the whole
      mix; one recurrence over the sum is exactly the instrument being
      asked for, not an accident.
    - Key tracking reads `ctx.note.pitch`, which does not exist on the
      once-only side. `process()` already falls back to
      `KEY_TRACKING_CENTRE` there, which makes key tracking a no-op rather
      than an error -- the honest answer, since a filter over sixteen notes
      at once has no single note to track. The knob is still shown; it just
      does nothing until the module is patched left of Mix, which is the
      same thing every modular does with a control that has no source.
    """

    def __init__(self):
        self._zi = None

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id="filter.svf",
            name="Filter",
            poly=contract.POLY_EITHER,
            category="filter",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def parameters(self):
        return (
            # Log, for the reason `config.SYNTH_PARAM_CUTOFF_RATIO` already
            # encodes for the terminal synth: a linear 20Hz..20kHz knob does
            # nothing audible for its first three quarters.
            ParamSpec("cutoff", "Cutoff", config.SYNTH_CUTOFF_MIN_HZ, 20000.0,
                      12000.0, unit="Hz", log=True),
            ParamSpec("resonance", "Reso", 0.0, 1.0, 0.1),
            ParamSpec("type", "Type", 0, len(FILTER_TYPES) - 1, 0,
                      steps=len(FILTER_TYPES), modulatable=False),
            # 1.0 means "the cutoff follows the note exactly", which is what
            # keeps high notes from sounding muffled next to low ones.
            ParamSpec("key_tracking", "Key", 0.0, 1.0, 0.0),
        )

    # -- lifecycle ----------------------------------------------------------

    def _allocate(self, activation):
        self._in_index = self.port_index("in", contract.DIRECTION_IN)
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        # #111's rule, applied at the only moment it can be: refuse to open,
        # never open filterless. Binding `lfilter` here rather than reaching
        # for it per block keeps the callback down to one call.
        self._lfilter = synth_engine.signal_module().lfilter
        self._b = np.zeros(3, dtype=np.float64)
        self._a = np.zeros(3, dtype=np.float64)
        self._zi = np.zeros(2, dtype=np.float64)
        # Last inputs to the coefficient calculation, one scalar each --
        # not a tuple, because packing one per block to compare against
        # would be an allocation made for the sake of avoiding allocations.
        # NaN so the first block always misses, whatever the knobs say.
        self._last_cutoff = float("nan")
        self._last_resonance = float("nan")
        self._last_type = -1
        self._last_tracking = float("nan")
        self._last_pitch = -1

    def reset(self):
        """Clear the recurrence. A voice slot handed a new note must not
        start with the last note's filter history ringing in it."""
        if self._zi is not None:
            self._zi[:] = 0.0

    # -- the audio thread ---------------------------------------------------

    def process(self, ctx):
        n = ctx.frames
        out = ctx.outputs[self._out_index]
        if n <= 0:
            return
        source = ctx.inputs[self._in_index]
        values = ctx.params
        p = self._p

        ftype = int(values[p["type"]])
        tracking = values[p["key_tracking"]]
        pitch = ctx.note.pitch if ctx.note is not None else KEY_TRACKING_CENTRE

        cutoff_live = ctx.param_mod_active is not None and ctx.param_mod_active[p["cutoff"]]
        resonance_live = ctx.param_mod_active is not None and ctx.param_mod_active[p["resonance"]]

        if not cutoff_live and not resonance_live:
            cutoff = values[p["cutoff"]]
            resonance = values[p["resonance"]]
            # Five float compares against knobs that are not moving, versus
            # a `tan`, a divide and two tuple builds that are not needed:
            # most blocks of most notes take the cheap branch. This is what
            # `ParamBlock.dirty` is for in spirit; it is done by value
            # because `ProcessContext` carries the values array, not the
            # block.
            if (cutoff != self._last_cutoff or resonance != self._last_resonance
                    or ftype != self._last_type or tracking != self._last_tracking
                    or pitch != self._last_pitch):
                self._recompute(cutoff, resonance, ftype, tracking, pitch, ctx.sample_rate)
            # The one allocation this module makes: `lfilter` has no
            # `out=`. See the module docstring -- it is measured, not
            # waved away.
            filtered, zf = self._lfilter(self._b, self._a, source[:n], zi=self._zi)
            np.copyto(out[:n], filtered)
            np.copyto(self._zi, zf)
            return

        # Live modulation on cutoff and/or resonance (#208): recompute at
        # control rate rather than once a block -- see the module
        # docstring's "Modulated cutoff/resonance" section.
        cutoff_buf = ctx.param_buffers[p["cutoff"]] if cutoff_live else None
        resonance_buf = ctx.param_buffers[p["resonance"]] if resonance_live else None
        base_cutoff = values[p["cutoff"]]
        base_resonance = values[p["resonance"]]
        sub = config.SYNTH_CONTROL_SUB_BLOCK
        start = 0
        while start < n:
            end = min(start + sub, n)
            cutoff = float(cutoff_buf[start]) if cutoff_buf is not None else base_cutoff
            resonance = (float(resonance_buf[start]) if resonance_buf is not None
                         else base_resonance)
            self._recompute(cutoff, resonance, ftype, tracking, pitch, ctx.sample_rate)
            filtered, zf = self._lfilter(self._b, self._a, source[start:end], zi=self._zi)
            np.copyto(out[start:end], filtered)
            np.copyto(self._zi, zf)
            start = end
        # The scalar cache no longer describes what just ran; a later
        # block that returns to the unmodulated path must recompute rather
        # than compare against a value that was never really current.
        self._last_cutoff = float("nan")
        self._last_resonance = float("nan")

    def _recompute(self, cutoff, resonance, ftype, tracking, pitch, sample_rate):
        """New coefficients into the arrays `lfilter` already reads.

        Key tracking is applied in *octaves*, never in Hz, for the reason
        `synth_engine.modulated_cutoff()` gives: pitch is logarithmic, so a
        fixed Hz offset means something completely different at C1 and at
        C7.
        """
        hz = cutoff * 2.0 ** (tracking * (pitch - KEY_TRACKING_CENTRE) / 12.0)
        b, a = synth_engine.svf_coefficients(
            hz, resonance, sample_rate, FILTER_TYPES[ftype])
        self._b[0], self._b[1], self._b[2] = b
        self._a[0], self._a[1], self._a[2] = a
        self._last_cutoff = cutoff
        self._last_resonance = resonance
        self._last_type = ftype
        self._last_tracking = tracking
        self._last_pitch = pitch
