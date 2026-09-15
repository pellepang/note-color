"""The module contract every node in the patch graph satisfies (#202,
decision 56 §6) -- including, eventually, a hosted third-party plugin.

The graph engine of decision 56 is a node canvas whose cables are the real
signal path. This file defines the one thing everything else in that stream
depends on: what a *module* is. Nothing here imports the graph, the canvas
or Qt, and nothing here is allowed to assume Python -- the shape is chosen
so the same contract can be satisfied by a NumPy class, by a C or Rust
inner loop behind #145's seam, and by an adapter around a CLAP or VST3
plugin.

## The five rules

1. **Block-at-a-time.** `process()` is handed N frames in and writes N
   frames out. There is no per-sample entry point and there never will be:
   a bare Python per-sample loop costs 32.7 us per 512 frames before doing
   any work (decision 56 §6). Sample-accurate recursion is legal *inside* a
   module -- the SVF's runs in C inside `scipy.signal.lfilter` -- but never
   across a module boundary.

2. **No allocation in the callback.** Every buffer a module needs is
   allocated in `activate()`, which is called off the audio thread with the
   sample rate and the largest block the host will ever ask for. `process()`
   writes into buffers the host owns and the module borrows. This is the
   rule #145 already imposes on the rest of the audio path, and it is also
   exactly the CLAP/VST3 `activate` -> `process` -> `deactivate` lifecycle,
   which is not a coincidence.

3. **Ports are declared by the instance, not the class.** `ports()` is an
   instance method returning a tuple of `PortSpec`. Our own modules return
   a constant; a plugin adapter returns whatever the plugin reported when it
   was scanned. A node's sockets on the canvas are drawn from this, so a
   hosted plugin gets sockets without the canvas knowing plugins exist.

4. **Typed ports.** `PORT_AUDIO`, `PORT_MOD` and `PORT_EVENT`. The type is
   what lets the canvas draw a different cable (decision 56 §5) and refuse a
   nonsense connection (§3). Audio and modulation are decision 56's two
   kinds; `PORT_EVENT` is the addition this contract makes for plugin
   hosting, because a hosted instrument is fed notes rather than sound, and
   a note port that only appears once a plugin needs one would be a second
   contract bolted on later.

5. **Parameters arrive out of band.** A knob edit is a write into a
   preallocated `ParamBlock`; `process()` reads a plain float64 array. No
   Python attribute is read from inside the callback, nothing is allocated,
   and the UI thread never blocks on the audio thread. Sample-accurate
   automation (an offset per change, which CLAP delivers as an event list)
   is deliberately not here yet -- the modulation layer, #208, is what
   needs it, and it extends `ParamBlock` rather than replacing it.

## Poly awareness

Every module declares whether it can run per held note, once only, or
either (`POLY_PER_NOTE` / `POLY_ONCE` / `POLY_EITHER`). This is the field
the Mix node's refusal rule is checked against (decision 56 §3), and it is
the same field `gui/patch_graph.NodeSpec.side` already carries on the
canvas -- #203 is where the two stop being two.

A per-note module is instantiated once per voice, up to
`config.POLYPHONY_SYNTH_VIEW`. `new_instance()` is how: a fresh, inactive
module of the same type and configuration, which the host then activates.
It exists because cloning a live module is not the same thing as making a
new one, and because a plugin adapter has to ask the plugin for a second
instance rather than copying a pointer.

## Block delay

`descriptor().block_delay` is how many whole blocks of delay a module
guarantees its output is behind its input. It is 0 for everything except a
delay line, and it is the single fact #203's cycle rule turns on: a
feedback loop is legal only when it passes through a module whose
`block_delay` is at least 1 (decision 56 §4, Bitwig's model). It is a
*guarantee*, not a measurement -- a module that reports 1 must still be one
block behind when its delay-time knob is at zero.

`latency_frames()` is the different, ordinary thing: reported latency for
compensation, which a plugin routinely has and our own modules mostly do
not.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

# -- port and poly vocabulary ------------------------------------------------

#: Sound. One float64 array per port per block, at the host's sample rate.
PORT_AUDIO = "audio"
#: Knob movement (decision 56 §5). Same array shape as audio -- the split is
#: for legibility, not for the execution model -- but drawn as a different
#: cable and refused where sound is wanted.
PORT_MOD = "mod"
#: Notes. Carried as a `NoteContext` rather than a buffer; the port exists so
#: a hosted instrument plugin can declare that it wants notes, and so the
#: canvas can draw that.
PORT_EVENT = "event"

PORT_KINDS = (PORT_AUDIO, PORT_MOD, PORT_EVENT)

DIRECTION_IN = "in"
DIRECTION_OUT = "out"

#: Instantiated once per held note, left of the Mix node.
POLY_PER_NOTE = "per_note"
#: Instantiated once, right of the Mix node.
POLY_ONCE = "once"
#: Either, decided by where it is patched. The graph resolves it; a module
#: that declares this must not care which side it ends up on.
POLY_EITHER = "either"

POLY_MODES = (POLY_PER_NOTE, POLY_ONCE, POLY_EITHER)


class ContractError(ValueError):
    """A module violates the contract in a way that is a programming error,
    not a user action -- a duplicate port id, an unknown port kind, a
    `process()` before `activate()`. Never raised in response to anything a
    person did on the canvas; those are #203's `Verdict` refusals, which are
    sentences, not exceptions."""


# -- ports -------------------------------------------------------------------


@dataclass(frozen=True)
class PortSpec:
    """One socket on a node.

    `port_id` is stable for the life of a module type and is what a saved
    patch stores, so renaming `name` (which is what the canvas prints) never
    breaks a patch file. That separation is not ours -- it is what every
    plugin ABI does, and a plugin adapter maps the plugin's own port index
    straight onto it.
    """

    port_id: str
    name: str
    kind: str = PORT_AUDIO
    direction: str = DIRECTION_IN
    #: Channel count. Everything in the view is mono today; the field exists
    #: because a hosted plugin is routinely stereo and discovering that after
    #: the fact would mean changing this dataclass in a released patch format.
    channels: int = 1

    def __post_init__(self):
        if self.kind not in PORT_KINDS:
            raise ContractError(f"{self.port_id}: unknown port kind {self.kind!r}")
        if self.direction not in (DIRECTION_IN, DIRECTION_OUT):
            raise ContractError(f"{self.port_id}: unknown direction {self.direction!r}")
        if self.channels < 1:
            raise ContractError(f"{self.port_id}: channels must be at least 1")


def audio_in(port_id="in", name="In", channels=1):
    return PortSpec(port_id, name, PORT_AUDIO, DIRECTION_IN, channels)


def audio_out(port_id="out", name="Out", channels=1):
    return PortSpec(port_id, name, PORT_AUDIO, DIRECTION_OUT, channels)


def mod_out(port_id="mod", name="Mod", channels=1):
    return PortSpec(port_id, name, PORT_MOD, DIRECTION_OUT, channels)


def mod_in(port_id="mod", name="Mod", channels=1):
    return PortSpec(port_id, name, PORT_MOD, DIRECTION_IN, channels)


# -- parameters --------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """One knob.

    Values travel through the contract in their *plain* unit -- Hz, seconds,
    a 0..1 level -- because that is what a patch file stores and what a
    module's arithmetic wants. `normalize()`/`denormalize()` convert to the
    0..1 both a plugin ABI and a knob widget speak, with `log=True` for the
    parameters where linear travel is useless (cutoff, time), which is the
    same reasoning `config.SYNTH_PARAM_CUTOFF_RATIO` already encodes for the
    terminal synth.

    `steps` marks a discrete choice -- a waveform, a filter type. Plugins
    have these too; making them a flag on the ordinary parameter rather than
    a second kind of thing is what keeps one automation path.
    """

    param_id: str
    name: str
    minimum: float
    maximum: float
    default: float
    unit: str = ""
    log: bool = False
    #: 0 for a continuous parameter; otherwise the number of positions,
    #: with values 0..steps-1.
    steps: int = 0
    #: False for a parameter modulation must not touch (a waveform choice).
    #: Decision 56 §5's modulation cables are refused onto these.
    modulatable: bool = True

    def __post_init__(self):
        if self.maximum <= self.minimum:
            raise ContractError(f"{self.param_id}: maximum must exceed minimum")
        if self.log and self.minimum <= 0.0:
            raise ContractError(f"{self.param_id}: a log parameter needs a positive minimum")
        if not self.minimum <= self.default <= self.maximum:
            raise ContractError(f"{self.param_id}: default {self.default} out of range")

    def clamp(self, value):
        return min(max(float(value), self.minimum), self.maximum)

    def normalize(self, value):
        """Plain unit -> 0..1."""
        value = self.clamp(value)
        if self.log:
            return math.log(value / self.minimum) / math.log(self.maximum / self.minimum)
        return (value - self.minimum) / (self.maximum - self.minimum)

    def denormalize(self, unit_value):
        """0..1 -> plain unit. Discrete parameters land on a position."""
        u = min(max(float(unit_value), 0.0), 1.0)
        if self.log:
            value = self.minimum * (self.maximum / self.minimum) ** u
        else:
            value = self.minimum + u * (self.maximum - self.minimum)
        if self.steps:
            value = round(value)
        return self.clamp(value)


#: The parameter half of #224 (decision 56 §5's smoothing freebie): a knob
#: edit ramps to its new value over roughly this long rather than jumping,
#: which is what turns a jump into a fade instead of a zipper or a click.
#: Short enough that it reads as "immediate" to a person turning a knob,
#: long enough that a single-block step is not a discontinuity -- one block
#: at 512 frames / 44100 Hz is 11.61ms, so 8ms converges the bulk of a jump
#: within the first block and the remainder within the second.
DEFAULT_SMOOTH_SECONDS = 0.008

#: Below this fraction of a parameter's own range, the gap between its
#: edited value and its audible one is treated as settled rather than
#: still ramping -- so a param that has finished fading stops materialising
#: a buffer for it (contract rule 2, and the reason most parameters never
#: pay for one at all: see `ParamBlock.buffers`).
_SETTLE_FRACTION = 1e-4


class ParamBlock:
    """The out-of-band parameter channel.

    `values` is the knob's *edited* value -- what `set()` writes, what
    `snapshot()`/`restore()` persist, one preallocated float64 array, one
    slot per parameter, written by whichever thread holds the knob and read
    by the audio thread.

    There is no lock. A float64 store is a single bytecode operation on a
    NumPy array and the reader is only ever one block behind, which for a
    knob is inaudible and for correctness is irrelevant -- nothing here is a
    compound update. What the rule in decision 56 §6 actually forbids is a
    Python *object* read from the callback (an attribute chain that can
    trigger a dict lookup, a property, or worse a `Patch` dataclass walk) and
    any allocation; `values` is a plain array that exists before the stream
    opens and is never reallocated.

    `dirty` is a monotonic counter, not a boolean, so a module can notice it
    missed changes across several blocks and still coalesce them into one
    recomputation -- which is what a filter that rebuilds coefficients wants.

    ## The scalar-or-buffer extension (#208, decision 56 §5)

    Everything below this point is what #208 adds, and it is additive on
    purpose (contract rule 5): a module that only ever reads `values` (via
    `get()`, off the audio thread) is unaffected, and a module that only
    ever reads `ProcessContext.params` (the ordinary scalar path, unchanged
    in shape) is unaffected too -- what changed under it is that the array
    it is reading is `smoothed`, not `values`.

    - `smoothed` is the audible value: `values` chased by a one-pole filter
      (`advance()`), so a knob edit fades in over `DEFAULT_SMOOTH_SECONDS`
      rather than stepping. This is bound to `ProcessContext.params`, so
      every module gets the smoothing for free, including ones that have
      never heard of #208.
    - `buffers` is one row per parameter, preallocated at `max_block` in
      `__init__` -- the "every per-parameter buffer is preallocated"
      constraint decision 56 §5's settlement names -- and `mod_active`
      marks which rows are live *this block*: either a modulation route is
      landing on that parameter (`graph.py`'s router writes both), or the
      parameter is still mid-fade from an edit (`advance()` writes both).
      A module that wants per-sample precision checks `mod_active[i]`
      before reading `buffers[i]`; nothing else in this file ever sets
      `mod_active` back to `False` once a route exists, because which
      parameters *can* be modulated is a structural, compile-time fact
      (added back by the next `compile()` if a cable is removed) -- what
      varies block to block is only whether a currently-live route or fade
      is actually contributing, and the router recomputes that every time
      it runs.
    """

    __slots__ = ("specs", "_index", "values", "dirty", "max_block",
                 "smoothed", "buffers", "mod_active",
                 "_minima", "_maxima", "_settle_eps", "_coeffs", "_delta",
                 "_prev", "_ramp", "_discrete", "_smooth_seconds", "_sample_rate",
                 "_first_block")

    def __init__(self, specs, max_block=1, sample_rate=44100.0,
                 smooth_seconds=DEFAULT_SMOOTH_SECONDS):
        self.specs = tuple(specs)
        self._index = {spec.param_id: i for i, spec in enumerate(self.specs)}
        if len(self._index) != len(self.specs):
            raise ContractError("duplicate parameter id")
        n = len(self.specs)
        self.values = np.array([spec.default for spec in self.specs], dtype=np.float64)
        self.dirty = 0

        self.max_block = max(1, int(max_block))
        self.smoothed = np.array(self.values)
        self.buffers = np.zeros((n, self.max_block), dtype=np.float64)
        self.mod_active = np.zeros(n, dtype=bool)

        self._minima = np.array([s.minimum for s in self.specs], dtype=np.float64)
        self._maxima = np.array([s.maximum for s in self.specs], dtype=np.float64)
        self._settle_eps = np.maximum(1e-9, _SETTLE_FRACTION * (self._maxima - self._minima))
        # Discrete parameters (a waveform, a filter type) snap rather than
        # fade: a filter type smoothed to 1.4 is a coefficient recompute for
        # a position that does not exist. `_coeffs` is recomputed by
        # `advance()` every block from `smooth_seconds` and `sample_rate`
        # (which can change between blocks -- a block-size change is a
        # deactivate/activate cycle, but the sample rate the module was
        # activated with is fixed for its life); the discrete slots are
        # pinned to 1.0 afterwards, in `advance()`, not here.
        self._discrete = tuple(i for i, s in enumerate(self.specs) if s.steps)
        self._smooth_seconds = smooth_seconds
        self._sample_rate = sample_rate
        self._coeffs = np.ones(n, dtype=np.float64)
        self._delta = np.zeros(n, dtype=np.float64)
        self._prev = np.zeros(n, dtype=np.float64)
        # Shared 0..1 ramp template, built once: `advance()` scales it by
        # each settling parameter's own delta rather than building an
        # `arange` per parameter per block.
        self._ramp = (np.arange(self.max_block, dtype=np.float64)
                      / max(1, self.max_block - 1))
        # Anything set before the stream's first block is a preset being
        # loaded, not a knob being turned under a playing note -- there is
        # nothing sounding yet to click, so it snaps rather than fades.
        # `advance()` clears this after its first call, and every knob edit
        # afterwards fades, which is the behaviour #224 actually asks for.
        self._first_block = True

    def index(self, param_id):
        """The slot a parameter occupies. Resolved once, at activation --
        never from inside `process()`, where the dict lookup is exactly the
        Python-object read the contract forbids."""
        try:
            return self._index[param_id]
        except KeyError:
            raise ContractError(f"no such parameter: {param_id!r}") from None

    def set(self, param_id, value):
        """Set by name, from the UI thread. Clamped to the spec, so a knob
        cannot hand the audio thread a cutoff of zero."""
        i = self.index(param_id)
        self.values[i] = self.specs[i].clamp(value)
        self.dirty += 1

    def set_normalized(self, param_id, unit_value):
        i = self.index(param_id)
        self.values[i] = self.specs[i].denormalize(unit_value)
        self.dirty += 1

    def set_immediate(self, param_id, value):
        """Set by name and make it audible on the very next block, bypassing
        the fade an ordinary `set()` gets from #208's smoothing. For a host
        applying a whole patch at once, before anything is sounding for a
        fade to protect -- `activate()`'s own first block already does this
        for every parameter automatically; this is the same snap, offered
        for a live edit that wants it deliberately (a preset load into an
        already-running voice, say)."""
        i = self.index(param_id)
        self.values[i] = self.specs[i].clamp(value)
        self.smoothed[i] = self.values[i]
        self.dirty += 1

    def get(self, param_id):
        return float(self.values[self.index(param_id)])

    def snapshot(self):
        """`{param_id: value}` for the patch writer (#209). Off the audio
        thread only."""
        return {spec.param_id: float(self.values[i]) for i, spec in enumerate(self.specs)}

    def restore(self, mapping):
        for param_id, value in mapping.items():
            if param_id in self._index:
                self.set(param_id, value)

    # -- the audio thread (#208) ----------------------------------------

    def advance(self, frames):
        """Chase `values` with `smoothed` by one block, and mark which
        parameters need a per-sample buffer this block because they are
        still mid-fade.

        Called by the host immediately before `process()` -- never inside
        it, for the same reason `dirty` exists one level up: this is the
        recomputation a block-at-a-time engine can afford once per block
        and not once per sample.

        A modulation route landing on a settled parameter still has to
        turn `mod_active` on and seed `buffers` from `smoothed`; that half
        is `graph.py`'s `CompiledGraph`, applied after this runs and before
        the module's own `process()`.
        """
        if self._first_block:
            # Nothing has ever sounded yet: apply whatever was set before
            # this instant exactly, the way loading a preset should.
            np.copyto(self.smoothed, self.values)
            self.mod_active[:] = False
            self._first_block = False
            return

        rate = self._sample_rate
        seconds = self._smooth_seconds
        coeff = (1.0 if seconds <= 0.0 or rate <= 0.0
                 else 1.0 - math.exp(-frames / (seconds * rate)))
        self._coeffs[:] = coeff
        for i in self._discrete:
            self._coeffs[i] = 1.0

        np.copyto(self._prev, self.smoothed)
        np.subtract(self.values, self.smoothed, out=self._delta)
        np.multiply(self._delta, self._coeffs, out=self._delta)
        np.add(self.smoothed, self._delta, out=self.smoothed)

        # Still moving? -- the magnitude of *this block's* step, not the
        # remaining distance: a parameter that just landed exactly on
        # `values` this block still needs its buffer ramped through the
        # step it just took.
        np.subtract(self.smoothed, self._prev, out=self._delta)
        np.abs(self._delta, out=self._delta)
        np.greater(self._delta, self._settle_eps, out=self.mod_active)

        n = min(frames, self.max_block)
        ramp = self._ramp
        for i in range(len(self.specs)):
            if self.mod_active[i]:
                start = float(self._prev[i])
                step = float(self.smoothed[i] - start)
                buf = self.buffers[i]
                np.multiply(ramp[:n], step, out=buf[:n])
                buf[:n] += start


# -- identity ----------------------------------------------------------------


@dataclass(frozen=True)
class ModuleDescriptor:
    """What a module *is*, as opposed to what it is currently doing.

    `module_id` is the stable type id a patch file stores ("osc.wavetable").
    A hosted plugin's is namespaced by where it came from
    ("clap:com.example.reverb"), which is what lets a patch referencing a
    plugin that is not installed fail with a nameable module rather than a
    key error.
    """

    module_id: str
    name: str
    poly: str = POLY_EITHER
    #: Whole blocks of delay this module guarantees between input and
    #: output. Only a delay line reports more than 0, and only a module
    #: reporting at least 1 may close a feedback loop (decision 56 §4).
    block_delay: int = 0
    #: True for the Mix node and nothing else (#204, decision 56 §3). The
    #: boundary belongs to neither side: its inputs are per-note -- that is
    #: what it is summing -- and its output is once-only. A flag rather than
    #: a fourth `poly` mode, because every rule that asks about poly needs
    #: to ask about *which end of the cable*, and one field cannot answer
    #: two different questions.
    is_boundary: bool = False
    #: Free text for the drawer's grouping: "source", "filter", "effect",
    #: "modulator", "utility", "plugin".
    category: str = "utility"
    vendor: str = ""

    def __post_init__(self):
        if self.poly not in POLY_MODES:
            raise ContractError(f"{self.module_id}: unknown poly mode {self.poly!r}")
        if self.block_delay < 0:
            raise ContractError(f"{self.module_id}: block_delay cannot be negative")


@dataclass(frozen=True)
class Activation:
    """The two facts a module is allowed to allocate against: the sample
    rate, and the largest block it will ever be handed. Handed in once, off
    the audio thread. A change to either is a deactivate/activate cycle, not
    a message -- again the plugin lifecycle, and for the same reason."""

    sample_rate: float
    max_block: int

    def __post_init__(self):
        if self.sample_rate <= 0:
            raise ContractError("sample_rate must be positive")
        if self.max_block < 1:
            raise ContractError("max_block must be at least 1")


@dataclass
class NoteContext:
    """The held note a per-note module instance is rendering, for the block
    about to run. Mutated in place by the host between blocks; never
    replaced, so a module may keep the reference.

    `gate` going False is the note-off -- the envelope's cue to release.
    `finished` is the module's answer back: an amp envelope sets it when the
    voice has faded, and the host reclaims the voice slot."""

    note_id: int = 0
    pitch: int = 60
    velocity: float = 1.0
    frequency: float = 261.6255653005986
    gate: bool = True
    finished: bool = False


@dataclass
class ProcessContext:
    """Everything `process()` is given, in one preallocated object the host
    owns and reuses for the life of the stream.

    `inputs` and `outputs` are tuples of float64 arrays in the order the
    module declared its ports, bound once when the graph is compiled. Index
    them by position -- a module resolves its own positions in `activate()`
    (`self._out = 0`) and never looks a port up by name in the callback.

    An unconnected input is bound to the host's shared zero buffer, so a
    module never has to test for `None`; writing to an input is a contract
    violation the host does not defend against, in the same way a plugin
    host does not.
    """

    frames: int = 0
    sample_rate: float = 44100.0
    inputs: tuple = ()
    outputs: tuple = ()
    #: The parameter array, bound at activation. Same object as
    #: `module.params.values`; carried here so `process()` touches exactly
    #: one object.
    params: np.ndarray | None = None
    #: Present on a per-note instance, `None` on a once-only one.
    note: NoteContext | None = None
    #: Block counter since the stream opened. Modules that need wall-clock
    #: time derive it from this rather than calling into the clock.
    block_index: int = 0
    #: `ParamBlock.buffers`, bound at activation (#208): one row per
    #: parameter, live only where `param_mod_active` says so this block.
    #: A module that does not care never reads this field -- it is here
    #: unconditionally so one that does never has to test for `None`.
    param_buffers: np.ndarray | None = None
    #: `ParamBlock.mod_active`, same binding. `True` at index i means
    #: `param_buffers[i]` holds this block's real per-sample values --
    #: either a modulation route or a knob edit still fading -- and `False`
    #: means `params[i]` (the smoothed scalar) is the whole story.
    param_mod_active: np.ndarray | None = None


# -- the module itself -------------------------------------------------------


class Module(ABC):
    """The base every graph node satisfies.

    Subclass for our own modules; wrap for someone else's. The methods are
    ordered by lifecycle, which is also the order a host calls them:

        ports() / parameters() / descriptor()   -- scan, any thread
        activate(Activation)                    -- allocate, off the audio thread
        reset()                                 -- between notes, off the audio thread
        process(ProcessContext)                 -- the audio thread, no allocation
        deactivate()                            -- free, off the audio thread

    Only `descriptor()`, `ports()` and `process()` are abstract. A module
    with no knobs inherits an empty `parameters()`; a module with no state
    inherits a `reset()` that does nothing.
    """

    #: Set by `activate()`. `None` means the module is not ready to process.
    activation: Activation | None = None
    #: The `ParamBlock`, built in `activate()` from `parameters()`.
    params: ParamBlock | None = None

    # -- scan ---------------------------------------------------------------

    @abstractmethod
    def descriptor(self) -> ModuleDescriptor:
        """Identity, poly mode and block delay. Cheap and constant."""

    @abstractmethod
    def ports(self) -> tuple:
        """This instance's ports, in a fixed order. Ours are constant; a
        plugin's come from the plugin."""

    def parameters(self) -> tuple:
        return ()

    def new_instance(self) -> "Module":
        """A fresh, inactive module of the same type and configuration.

        This is how a per-note module becomes sixteen. The default
        reconstructs from `__class__` with no arguments, which is right for
        a module whose entire configuration is parameters; override it
        wherever construction takes a choice that is not a parameter (a
        waveform, a plugin handle).
        """
        return self.__class__()

    def latency_frames(self) -> int:
        """Reported latency for compensation. Distinct from
        `descriptor().block_delay`, which is a guarantee the cycle rule
        depends on."""
        return 0

    # -- lifecycle ----------------------------------------------------------

    def activate(self, activation: Activation):
        """Prepare to process at this sample rate and block size. Every
        allocation a module will ever make happens here or not at all.

        Subclasses override `_allocate()` rather than this, so the
        `ParamBlock` and port-index bookkeeping cannot be forgotten.
        """
        self.activation = activation
        self.params = ParamBlock(self.parameters(), max_block=activation.max_block,
                                  sample_rate=activation.sample_rate)
        self._allocate(activation)
        self.reset()
        return self

    def _allocate(self, activation: Activation):
        """Subclass hook: build every buffer and every cached index."""

    def deactivate(self):
        self._free()
        self.activation = None

    def _free(self):
        """Subclass hook. Rarely needed in Python; a plugin adapter needs it
        to tell the plugin, which is why it is in the contract at all."""

    def reset(self):
        """Clear running state -- filter history, phases, delay buffers --
        without reallocating. Called when a voice is assigned a new note and
        when the transport stops."""

    # -- the audio thread ---------------------------------------------------

    @abstractmethod
    def process(self, ctx: ProcessContext):
        """Write `ctx.frames` samples into each of `ctx.outputs`.

        No allocation, no locking, no logging, no exceptions expected. A
        module that cannot do its job this block writes silence."""

    # -- helpers, off the audio thread --------------------------------------

    def port_index(self, port_id, direction):
        """The position of one port among this module's ports of that
        direction -- the index into `ctx.inputs`/`ctx.outputs`. Resolve in
        `_allocate()`, never in `process()`."""
        matching = [p for p in self.ports() if p.direction == direction]
        for i, port in enumerate(matching):
            if port.port_id == port_id:
                return i
        raise ContractError(f"{port_id!r} is not a {direction} port of {self.descriptor().module_id}")

    def require_active(self):
        if self.activation is None:
            raise ContractError(
                f"{self.descriptor().module_id} was asked to process before activate()")
        return self.activation


def validate(module: Module):
    """Check a module against the parts of the contract that can be checked
    without running it. Called by the module registry at scan time -- and by
    the plugin adapter on everything it wraps, where the module was written
    by someone who has never read this file.

    Returns the module, so it can wrap a construction expression.
    """
    descriptor = module.descriptor()
    ports = tuple(module.ports())
    seen = set()
    for port in ports:
        if not isinstance(port, PortSpec):
            raise ContractError(f"{descriptor.module_id}: ports() must return PortSpec")
        if port.port_id in seen:
            raise ContractError(f"{descriptor.module_id}: duplicate port id {port.port_id!r}")
        seen.add(port.port_id)
    params = tuple(module.parameters())
    if len({p.param_id for p in params}) != len(params):
        raise ContractError(f"{descriptor.module_id}: duplicate parameter id")
    if not any(p.direction == DIRECTION_OUT for p in ports):
        raise ContractError(f"{descriptor.module_id}: a module with no output cannot be patched")
    return module
