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


class ParamBlock:
    """The out-of-band parameter channel: one preallocated float64 array,
    one slot per parameter, written by whichever thread holds the knob and
    read by the audio thread.

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
    """

    __slots__ = ("specs", "_index", "values", "dirty")

    def __init__(self, specs):
        self.specs = tuple(specs)
        self._index = {spec.param_id: i for i, spec in enumerate(self.specs)}
        if len(self._index) != len(self.specs):
            raise ContractError("duplicate parameter id")
        self.values = np.array([spec.default for spec in self.specs], dtype=np.float64)
        self.dirty = 0

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
        self.params = ParamBlock(self.parameters())
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
