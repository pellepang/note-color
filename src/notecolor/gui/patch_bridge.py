"""The bridge: what is drawn on the canvas becomes what is played (#207,
decision 65).

The Synth View has had two graphs in it for a while and they were not the
same graph. `gui/patch_graph.PatchGraph` is what the user draws -- nodes
with type keys, cables between whole modules, a refusal contract written to
match an engine that did not exist yet. `audio/graph/` is that engine:
typed ports, real DSP modules, sixteen voices, a compiled execution order.
This file is the one place the two meet, and it exists so that neither has
to learn the other's vocabulary:

    canvas node (type_key)  ->  engine module      -- `MODULE_FACTORIES`
    canvas cable (node->node)  ->  engine cable (out -> in)
    canvas knob (section.attr) ->  engine parameter -- same names, mostly

Everything above it still speaks in type keys; everything below it still
speaks in ports and buffers. `synth_view.py` holds one of these and tells
it three things: rebuild, a knob moved, a note happened.

## Why the whole graph is rebuilt on every edit

Because `audio/graph/graph.py` says to. A cable change builds a new
`PolyGraph` off the UI thread, activates it (sixteen copies of the per-note
subgraph, every buffer allocated), compiles it, and hands it to
`SoundEngine.set_graph()`, which is a single attribute store. The graph
that is playing is never mutated. At this size a rebuild is a few
milliseconds on the UI thread and the alternative -- an incremental
rebinder -- is where torn state would come from.

The cost the user can hear: held notes do not survive a rebuild, because
the new graph's voices are new objects with nobody in them.
`SynthView` re-triggers whatever keys are still down straight after a
rebuild, so moving a cable while holding a chord changes the sound rather
than stopping it.

## The nodes with no engine module

`chorus` -- and anything else the canvas offers that the engine cannot play
-- is built as `modules/passthrough.Passthrough`: a wire. It stays in the
signal path, it obeys every rule, and it is *named* in `notices()` so the
Synth View can say so in its status bar. The reasoning is in
`modules/passthrough.py`; the short version is that a node silently missing
from the signal path would make the cables lie, which is the one thing
decision 56 exists to prevent.

`lfo`, `filter_env` and `voice` are different and are simply not in the
engine graph at all, which is correct rather than a gap: the first two send
modulation, which has no cables to sockets and no engine layer until #208,
and `voice` carries no jacks at all -- it is the patch's polyphony settings
drawn as a window. None of the three can be an end of a sound cable, so
leaving them out removes nothing from any signal path. They are named in
`notices()` anyway, because "this knob does nothing yet" is worth saying
out loud.

## Which side of Mix a module is on

The canvas's answer, not the module's. Decision 61 §2 says a `POLY_EITHER`
module's side is settled by the patch; the canvas has already settled it
(`synth_view.MONO_TYPES`), so the bridge hands that down through
`ModuleGraph.add(poly=...)`. Without it the two would disagree about an
unpatched Filter -- the canvas calls it per-note, the engine's fallback for
an unpatched `POLY_EITHER` module is once-only -- and the very first cable
of the default chain, Osc into Filter, would be refused.
"""

from __future__ import annotations

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.envelope import AmpEnvelope
from notecolor.audio.graph.modules.filter import FILTER_TYPES, StateVariableFilter
from notecolor.audio.graph.modules.noise import COLOURS as NOISE_COLOURS, Noise
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.modules.passthrough import Passthrough
from notecolor.audio.graph.poly import MixModule, PolyGraph
from notecolor.gui import patch_graph as pg

#: Canvas `type_key` -> how to build the engine module for it. Each factory
#: takes the node's construction settings (a dict of knob values), because
#: an oscillator's waveform is a construction choice and not a parameter --
#: it decides which wavetable set the instance reads. Everything else about
#: a module arrives later, through `set_parameter()`.
MODULE_FACTORIES = {
    "osc1": lambda cfg: WavetableOscillator(_waveform(cfg)),
    "osc2": lambda cfg: WavetableOscillator(_waveform(cfg)),
    "noise": lambda cfg: Noise(),
    "filter": lambda cfg: StateVariableFilter(),
    "amp_env": lambda cfg: AmpEnvelope(),
    "delay": lambda cfg: Delay(),
}

#: Canvas nodes that are deliberately absent from the engine graph: they
#: cannot be an end of a sound cable, so leaving them out takes nothing out
#: of the signal path. See the module docstring.
NOT_IN_ENGINE = frozenset({"lfo", "filter_env", "voice"})

#: Construction settings a factory reads, per type key. Anything not listed
#: reaches the module as a parameter instead.
CONSTRUCTION_KEYS = {"osc1": ("waveform",), "osc2": ("waveform",)}

#: `(type_key, param_id) -> the option names, in the engine's own order`.
#:
#: The canvas names a choice ("lp", "pink"); the contract carries every
#: parameter as a float, so a discrete one is a position. These lists are
#: the engine module's own tuples, imported rather than retyped, so the two
#: orders cannot drift apart -- the canvas's lists in `tui/synth_params.py`
#: happen to match today, and `position_of()` below maps by *name* anyway,
#: so they need not stay matched.
CHOICE_OPTIONS = {
    ("filter", "type"): FILTER_TYPES,
    ("noise", "colour"): NOISE_COLOURS,
}


def position_of(node_id, param_id, value):
    """A knob value the engine can store: a number, unchanged, or a choice
    name turned into its position. Returns `None` for a name nothing knows,
    which `set_parameter()` treats as "this knob has no engine parameter
    yet" -- the same answer it gives for `filter.env_amount`."""
    if not isinstance(value, str):
        return value
    options = CHOICE_OPTIONS.get((node_id, param_id))
    if options is None or value not in options:
        return None
    return float(options.index(value))


def _waveform(cfg):
    wanted = cfg.get("waveform", "saw")
    from notecolor.audio.graph.modules.oscillator import WAVEFORMS
    return wanted if wanted in WAVEFORMS else "saw"


def carries_sound(spec):
    """Does a canvas node sit on the sound path at all?

    True for anything that can take sound in or send sound out; false for a
    modulation source (its cable goes onto a knob, never into a socket) and
    for `voice`, which has no jacks. This is asked of the canvas's own
    `NodeSpec` rather than of a type-key list, so a node type added to the
    drawer later is classified by what it declares.
    """
    if spec.node_id in NOT_IN_ENGINE:
        return False
    return bool(spec.can_in or (spec.can_out and spec.out_kind == pg.KIND_AUDIO))


def module_for(spec, settings=None):
    """The engine module one canvas node becomes, or `None` for a node that
    is not on the sound path.

    A type key with no factory becomes a `Passthrough` rather than nothing
    -- see `modules/passthrough.py`. The caller can tell the two apart by
    asking `MODULE_FACTORIES`, which is what `build_graph()` does to fill in
    `notices`.
    """
    if spec.is_mix:
        return MixModule()
    if not carries_sound(spec):
        return None
    factory = MODULE_FACTORIES.get(spec.node_id)
    if factory is None:
        return Passthrough(name=spec.title)
    return factory(dict(settings or {}))


def takes_sound_in(type_key):
    """Does the engine module behind this type key have a sound input?

    False for an oscillator and for Noise, which generate rather than
    process. The canvas asks this when it decides how many jacks to draw,
    so that a module with nowhere to put sound does not get a hole you can
    plug a cable into and then be told about a port you have never seen
    (#207). Unknown type keys answer True, because the passthrough they
    become has an input.
    """
    factory = MODULE_FACTORIES.get(type_key)
    if factory is None:
        return True
    module = factory({})
    return any(p.direction == contract.DIRECTION_IN and p.kind == contract.PORT_AUDIO
               for p in module.ports())


def poly_for(spec):
    """The side of Mix the canvas has put this node on, in the engine's own
    vocabulary. Mix itself declares its own -- it is the boundary, not a
    side of it."""
    if spec.is_mix:
        return None
    return (contract.POLY_ONCE if spec.side == pg.SIDE_MONO
            else contract.POLY_PER_NOTE)


def build_graph(specs, cables, settings=None):
    """A `ModuleGraph` for what is on the canvas, plus the notices the user
    should be told about it.

    `specs` are `patch_graph.NodeSpec`s, `cables` are `patch_graph.Cable`s
    (only the ones with a `dest` -- a knob cable is modulation, which has no
    engine layer yet), and `settings` is `{type_key: {name: value}}` for the
    construction choices in `CONSTRUCTION_KEYS`.

    Returns `(graph, notices)`. `notices` is a list of plain sentences, in
    the order the nodes appear, each naming a node whose behaviour on the
    canvas and in the engine are not yet the same thing.
    """
    settings = settings or {}
    graph = ModuleGraph()
    notices = []
    built = set()
    for spec in specs:
        module = module_for(spec, settings.get(spec.node_id))
        if module is None:
            notices.append(f"{spec.title} turns nothing yet")
            continue
        graph.add(spec.node_id, module, poly=poly_for(spec), title=spec.title)
        built.add(spec.node_id)
        if not spec.is_mix and spec.node_id not in MODULE_FACTORIES:
            notices.append(f"{spec.title} passes sound through unchanged")
    for cable in cables:
        if cable.dest is None:
            continue
        if cable.source in built and cable.dest in built:
            # `force_connect`, not `connect`: the canvas has already judged
            # every cable it accepted, and a restored workspace may hold one
            # that predates a rule. A cable the engine cannot build shows up
            # as a refusal from `activate()`, where it can be reported, not
            # as a cable silently dropped here.
            graph.force_connect(cable.source, "out", cable.dest, "in")
    return graph, notices


class PatchBridge:
    """Keeps a live `PolyGraph` in step with one `PatchLayer`'s canvas.

    One per Synth View. The view calls `rebuild()` whenever the nodes or
    cables change and `set_parameter()` when a knob moves; everything else
    -- activation, compilation, installing into the sound engine, and
    reporting what went wrong -- happens in here.

    `judge()` is the other half of its job and is deliberately usable
    without any audio at all: it answers from a `PolyGraph` that has been
    built but not activated, so `patch_graph.PatchGraph` can delegate its
    refusals to the real rules on a machine with no sound card and in a
    headless test.
    """

    def __init__(self, sound_engine_provider=None, voices=None):
        #: A zero-argument callable returning the process's `SoundEngine`,
        #: or None. The same shape `synth_view` already uses, and for the
        #: same reason: a view with no audio device must still work.
        self.sound_engine_provider = sound_engine_provider
        self.voices = voices
        #: The graph the rules are asked about. Present as soon as
        #: `rebuild()` has run once, whether or not it could be activated.
        self.poly = None
        #: The graph that is playing, or None. Distinct from `poly` because
        #: a patch can be judgeable and unplayable at the same time -- a
        #: workspace restored with a cable a newer rule refuses, say.
        self.playing = None
        #: Sentences for the status bar, from the last `rebuild()`.
        self.notices = []
        #: What stopped the last rebuild from playing, or "".
        self.error = ""
        self._settings = {}
        self._parameters = {}

    # -- what the canvas knows ----------------------------------------------

    def set_settings(self, settings):
        """`{type_key: {name: value}}` for construction choices -- an
        oscillator's waveform. Changing one needs a rebuild (a waveform
        picks the module's wavetable set at construction), which is why it
        is separate from `set_parameter()`."""
        self._settings = {k: dict(v) for k, v in (settings or {}).items()}

    def set_parameters(self, parameters):
        """`{type_key: {param_id: value}}` to apply after every rebuild, so
        a freshly built graph starts at the knob positions on screen rather
        than at its own defaults."""
        self._parameters = {k: dict(v) for k, v in (parameters or {}).items()}

    # -- building ------------------------------------------------------------

    def rebuild(self, specs, cables):
        """Build, activate, compile and install a graph for this canvas.

        Runs on the UI thread and takes as long as it takes; the graph that
        is currently playing keeps playing until the new one is ready, and
        the swap is `SoundEngine.set_graph()`'s single attribute store.

        Never raises. A patch the engine cannot build -- no Mix node, a loop
        through Mix, a cable across the boundary -- stops the sound and puts
        the reason in `error`, because a traceback out of a cable drag is
        not a thing a person can act on. Returns True when a graph is
        playing afterwards.
        """
        self.error = ""
        specs = list(specs)
        graph, self.notices = build_graph(specs, cables, self._settings)
        try:
            poly = PolyGraph(graph, voices=self.voices)
        except contract.ContractError as exc:
            self.poly = None
            self._install(None)
            self.error = str(exc)
            return False
        self.poly = poly

        engine = self._engine()
        if engine is None:
            self._install(None)
            return False
        try:
            poly.activate(Activation(engine.sample_rate, engine.block_size))
        except contract.ContractError as exc:
            self._install(None)
            self.error = str(exc)
            return False
        self._apply_parameters(poly)
        self._install(poly)
        return True

    def _engine(self):
        """The process's `SoundEngine`, or None when there is nothing to
        play through.

        "Nothing" covers three cases that all mean the same thing here: no
        provider, no audio device, and an object standing in for one that
        cannot host a graph. The last is checked by asking for the two
        things a graph needs rather than by a type test, which is the same
        duck-typed contract `synth_view._register_patch_live()` already
        applies to `.engine.patches` -- a stub controller in a test is a
        normal caller, not an error.
        """
        provider = self.sound_engine_provider
        engine = provider() if provider is not None else None
        if engine is None:
            return None
        if not hasattr(engine, "set_graph") or not hasattr(engine, "block_size"):
            return None
        return engine

    def _install(self, poly):
        self.playing = poly
        engine = self._engine()
        if engine is not None:
            # Already activated above (or None): `set_graph` must not
            # activate it a second time, which would reallocate every buffer
            # the compiled graph is bound to.
            engine.set_graph(poly, activate=False)

    def _apply_parameters(self, poly):
        for node_id, values in self._parameters.items():
            for param_id, value in values.items():
                self._set_one(poly, node_id, param_id, value)

    # -- knobs ---------------------------------------------------------------

    def set_parameter(self, node_id, param_id, value):
        """One knob, to all sixteen voices.

        Silently ignores a parameter the engine module does not have --
        `filter.env_amount` is a real knob on the canvas with no engine
        parameter behind it until #208, and a knob that raises is worse for
        the person turning it than a knob that waits. The *set* of such
        knobs is reported once per rebuild through `notices()`; this path is
        per-turn and has to stay quiet.
        """
        self._parameters.setdefault(node_id, {})[param_id] = value
        if self.playing is None:
            return False
        return self._set_one(self.playing, node_id, param_id, value)

    @staticmethod
    def _set_one(poly, node_id, param_id, value):
        if poly.graph.node(node_id) is None:
            return False
        numeric = position_of(node_id, param_id, value)
        if numeric is None:
            return False
        try:
            poly.set_parameter(node_id, param_id, numeric)
        except contract.ContractError:
            return False
        return True

    # -- notes ---------------------------------------------------------------

    @property
    def active(self):
        """True when a note-on would make a sound through the graph. The
        Synth View asks this to decide whether to play a key through the
        graph or through the old fixed engine -- never both."""
        return self.playing is not None

    def note_on(self, pitch, velocity=1.0):
        """A note into the graph's own sixteen voices.

        Deliberately not through `SoundEngine.note_on()`: that allocates a
        `VoiceManager` slot out of the polyphony budget the sampler and the
        old synth share, and a graph note occupies no such slot. The two
        voice pools are separate on purpose (decision 65) -- they have
        different lifetimes, different stealing and different owners.
        """
        if self.playing is None:
            return None
        return self.playing.note_on(int(pitch), float(velocity))

    def note_off(self, pitch):
        if self.playing is None:
            return 0
        return self.playing.note_off(int(pitch))

    def all_notes_off(self):
        if self.playing is not None:
            self.playing.all_notes_off()

    # -- the rules -----------------------------------------------------------

    def judge(self, source_id, dest_id):
        """The engine's verdict on one sound cable, or None when there is no
        graph to ask -- in which case `patch_graph` falls back to its own
        copy of the rules.

        Node to node, because that is all a canvas cable is: a module has
        one sound in and one sound out on this canvas (several cables meet
        at the one input and the graph sums them, decision 61 §1), so the
        ports are always "out" and "in" and the canvas never has to learn
        the word port.
        """
        poly = self.poly
        if poly is None:
            return None
        if poly.graph.node(source_id) is None or poly.graph.node(dest_id) is None:
            return None
        return poly.judge(source_id, "out", dest_id, "in")

    # -- what to tell the user ------------------------------------------------

    def status(self):
        """One short clause for the status bar, or "". The error first --
        a patch that is not making sound is the more urgent fact -- then
        whatever the notices say, joined."""
        if self.error:
            return self.error
        return " · ".join(self.notices)
