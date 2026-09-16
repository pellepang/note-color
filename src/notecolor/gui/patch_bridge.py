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
from notecolor.audio.graph.modules.envelope import AmpEnvelope, ModEnvelope
from notecolor.audio.graph.modules.filter import FILTER_TYPES, StateVariableFilter
from notecolor.audio.graph.modules.level import Level
from notecolor.audio.graph.modules.lfo import SHAPES as LFO_SHAPES, Lfo
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
    "level": lambda cfg: Level(),
    # `mode` is always "per_note" here: the canvas offers one "lfo" type
    # key, never dropped on the once-only side (it is not in `MONO_TYPES`),
    # so the construction choice #208's module docstring describes as
    # fixed-at-drop never has a second value to choose on this canvas yet.
    "lfo": lambda cfg: Lfo(mode="per_note"),
    # Per-note only, no mode switch -- decision 67: "a DAHDSR without a
    # note's gate to key off has no cycle to run."
    "mod_env": lambda cfg: ModEnvelope(),
}

#: Canvas nodes that are deliberately absent from the engine graph: they
#: cannot be an end of a sound cable and send no modulation the engine can
#: route yet, so leaving them out takes nothing out of the signal path.
#: See the module docstring. `lfo` and `mod_env` are *not* here -- #208
#: gave them real modules (`lfo` a sound path too, via its softened Out
#: jack; `mod_env` a Mod jack only, decision 67). `filter_env` stays until
#: it has one of its own.
NOT_IN_ENGINE = frozenset({"filter_env", "voice"})

#: A modulation source's Mod-out port id, by canvas type key -- what
#: `build_graph()` names as the `source_port` half of `ModConnection`.
#: `filter_env` has no entry -- it has no engine module yet (stage 2, #208
#: §"what is left open"), so no source port to name.
MOD_SOURCE_PORTS = {"lfo": "mod", "mod_env": "mod"}

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
    ("lfo", "shape"): LFO_SHAPES,
    # `retrigger` is a real 0/1 `ParamSpec` (steps=2), not a string on the
    # engine side -- this entry exists only because the canvas shows it as
    # an "off"/"on" choice for a nicer knob than a raw 0/1 int, `synth_view.
    # UTILITY_PARAM_SPECS["lfo"]`'s own reason for choosing `KIND_CHOICE`.
    ("lfo", "retrigger"): ("off", "on"),
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


def choice_name_of(node_id, param_id, value):
    """`position_of()`'s reverse: a saved graph-patch parameter (#209,
    always a plain float -- `ParamBlock.snapshot()`'s shape) back into
    whatever the canvas's own `Patch`-backed fields expect to hold, for a
    knob whose display is a discrete choice rather than a number. Used by
    `synth_view._apply_graph_result()` (#230) to put a loaded value back
    where the fixed `Patch` attribute it drives (`filter.type`,
    `noise.colour`, ...) can show it. A `param_id` not in `CHOICE_OPTIONS`
    is a plain number already and is returned unchanged."""
    options = CHOICE_OPTIONS.get((node_id, param_id))
    if options is None:
        return value
    try:
        index = int(round(float(value)))
    except (TypeError, ValueError):
        return value
    if 0 <= index < len(options):
        return options[index]
    return value


def _waveform(cfg):
    wanted = cfg.get("waveform", "saw")
    from notecolor.audio.graph.modules.oscillator import WAVEFORMS
    return wanted if wanted in WAVEFORMS else "saw"


def carries_sound(spec):
    """Does a canvas node sit on the *sound* path at all?

    True for anything that can take sound in or send sound out (including
    a `KIND_BOTH` node's softened Out jack); false for a mod-only source
    (its cable goes onto a knob, never into a socket) and for `voice`,
    which has no jacks. This is asked of the canvas's own `NodeSpec` rather
    than of a type-key list, so a node type added to the drawer later is
    classified by what it declares. A node failing this can still be built
    -- see `sends_modulation()` and `module_for()`.
    """
    if spec.node_id in NOT_IN_ENGINE:
        return False
    return bool(spec.can_in
                or (spec.can_out and spec.out_kind in (pg.KIND_AUDIO, pg.KIND_BOTH)))


def sends_modulation(spec):
    """Does a canvas node have a *real* Mod jack -- one an engine module
    actually emits, rather than #208's remaining "no module yet" case
    (`filter_env`, `NOT_IN_ENGINE`)?

    A mod-only source has no sound-path fallback the way an unrecognised
    sound node becomes a `Passthrough`: there is no such thing as a wire
    that passes modulation through unchanged and does nothing else, so a
    mod-only type key with no factory stays out of the engine entirely
    rather than becoming some invented stand-in.
    """
    if spec.node_id in NOT_IN_ENGINE:
        return False
    return spec.out_kind in (pg.KIND_MOD, pg.KIND_BOTH) and spec.node_id in MODULE_FACTORIES


def module_for(spec, settings=None):
    """The engine module one canvas node becomes, or `None` for a node that
    is on neither the sound path nor a real modulation route.

    A type key on the sound path with no factory becomes a `Passthrough`
    rather than nothing -- see `modules/passthrough.py`. The caller can
    tell the two apart by asking `MODULE_FACTORIES`, which is what
    `build_graph()` does to fill in `notices`. A mod-only node (`lfo`
    excepted -- it carries sound too, `KIND_BOTH`) has no such fallback:
    see `sends_modulation()`.
    """
    if spec.is_mix:
        return MixModule()
    if carries_sound(spec):
        factory = MODULE_FACTORIES.get(spec.node_id)
        if factory is None:
            return Passthrough(name=spec.title)
        return factory(dict(settings or {}))
    if sends_modulation(spec):
        return MODULE_FACTORIES[spec.node_id](dict(settings or {}))
    return None


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


#: `(type_key, canvas label) -> param_id`, for the knobs where the canvas's
#: own label (abbreviated to fit a knob, `tui/synth_params.py`'s and
#: `synth_view.EFFECT_PARAM_SPECS`'s house style -- "Reso", "EnvAmt")
#: doesn't read letter-for-letter like the engine `ParamSpec.name` beside
#: it. Most labels do match (`filter.py`'s own "Reso" for `resonance`), so
#: `param_id_for_label()` tries that first and this table only carries the
#: exceptions: `oscillator.py`'s `pulse_width` is named "Width" there but
#: "PW" on the canvas (`_osc_specs`); `filter.py`'s `key_tracking` is
#: "Key" there but "KeyTrk" on the canvas; `delay.py`'s `feedback` and
#: `damping` are "Feedback"/"Damping" there but "Fdbk"/"Damp" on the
#: canvas (`EFFECT_PARAM_SPECS`). Found by a mod cable onto Delay's
#: Feedback silently doing nothing in a screenshot check -- the exact
#: "rots" `_graph_parameters()`'s own docstring already warns a
#: hand-kept second mapping does, so this one stays as small as the real
#: mismatches, not a wholesale retyping of every label.
LABEL_ALIASES = {
    ("osc1", "PW"): "pulse_width",
    ("osc2", "PW"): "pulse_width",
    ("filter", "KeyTrk"): "key_tracking",
    ("delay", "Fdbk"): "feedback",
    ("delay", "Damp"): "damping",
}


def param_id_for_label(type_key, label):
    """The engine parameter a canvas knob's display label names, or `None`
    -- a knob with no engine parameter behind it yet (`filter.env_amount`)
    or a label belonging to a type key the engine has no module for.

    Matched by name first: most engine `ParamSpec.name`s are written to
    read exactly like the knob beside them (`filter.py`'s "Reso" for
    `resonance`, `oscillator.py`'s "Fine" for `fine`), the same convention
    `CHOICE_OPTIONS` already leans on for a choice's options. `LABEL_
    ALIASES` above covers the labels that do not, so this is what lets a
    modulation cable's destination -- a knob the user sees a *label* on,
    never a parameter id -- become a real `ModConnection` (#208) without a
    second, hand-kept label table for every knob, only for the exceptions.
    """
    alias = LABEL_ALIASES.get((type_key, label))
    if alias is not None:
        return alias
    factory = MODULE_FACTORIES.get(type_key)
    if factory is None:
        return None
    module = factory({})
    for spec in module.parameters():
        if spec.name == label:
            return spec.param_id
    return None


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

    `specs` are `patch_graph.NodeSpec`s, `cables` are `patch_graph.Cable`s --
    both sound cables (`dest` set) and modulation cables (`knob` set,
    #208) -- and `settings` is `{type_key: {name: value}}` for the
    construction choices in `CONSTRUCTION_KEYS`.

    Returns `(graph, notices, node_notices)`. `notices` is a list of plain
    sentences, in the order the nodes appear, each naming a node whose
    behaviour on the canvas and in the engine are not yet the same thing --
    unchanged, for the status bar. `node_notices` is the same information
    keyed by `node_id`, as `(kind, text)`, `kind` being `"no_module"` (not
    in the engine graph at all -- `filter_env`, `voice`) or `"passthrough"`
    (built as a wire -- Chorus today). #227: the canvas marks the node
    itself with this, because a status-bar sentence is easy to miss with
    your eyes on the knob.
    """
    settings = settings or {}
    graph = ModuleGraph()
    notices = []
    node_notices = {}
    built = set()
    for spec in specs:
        module = module_for(spec, settings.get(spec.node_id))
        if module is None:
            text = f"{spec.title} turns nothing yet"
            notices.append(text)
            node_notices[spec.node_id] = ("no_module", text)
            continue
        graph.add(spec.node_id, module, poly=poly_for(spec), title=spec.title)
        built.add(spec.node_id)
        if not spec.is_mix and spec.node_id not in MODULE_FACTORIES:
            text = f"{spec.title} passes sound through unchanged"
            notices.append(text)
            node_notices[spec.node_id] = ("passthrough", text)
    for cable in cables:
        if cable.dest is not None:
            if cable.source in built and cable.dest in built:
                # `force_connect`, not `connect`: the canvas has already
                # judged every cable it accepted, and a restored workspace
                # may hold one that predates a rule. A cable the engine
                # cannot build shows up as a refusal from `activate()`,
                # where it can be reported, not as a cable silently dropped
                # here.
                graph.force_connect(cable.source, "out", cable.dest, "in")
            continue
        if cable.knob is None:
            continue
        dest_id, label = cable.knob
        if cable.source not in built or dest_id not in built:
            continue
        source_port = MOD_SOURCE_PORTS.get(cable.source)
        param_id = param_id_for_label(dest_id, label)
        if source_port is None or param_id is None:
            # Either the source has no engine Mod jack yet (`filter_env`,
            # stage 2) or the destination knob has no engine parameter yet
            # (`filter.env_amount`) -- both already covered by the "turns
            # nothing yet" / "does nothing yet" notices at build time
            # (`patch_bridge`'s module docstring), so a cable onto one is
            # silently inert here rather than a second thing to report.
            continue
        # `force_connect_modulation`, not `connect_modulation`: same reason
        # as the sound-cable path above -- the canvas has already judged
        # this cable, and a restored workspace's leftover poly-crossing
        # cable is `PolyGraph.activate()`'s `illegal_mod` check to catch
        # and report, not this function's.
        graph.force_connect_modulation(cable.source, source_port, dest_id, param_id,
                                        depth=cable.depth)
    return graph, notices, node_notices


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
        #: The same notices, keyed by `node_id` as `(kind, text)` -- #227,
        #: so the canvas can mark the node itself rather than only the
        #: status bar. See `build_graph()`.
        self.node_notices = {}
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
        graph, self.notices, self.node_notices = build_graph(specs, cables, self._settings)
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

    # -- modulation (#208) -----------------------------------------------------

    def set_modulation_depth(self, source_id, dest_id, label, value):
        """The ring's write path (decision 66 §4, #210 §3's dashed ring),
        live and without a rebuild -- `ModuleGraph.set_modulation_depth()`
        edits a shared array slot every voice's `ModRoute` already reads,
        so a value written here is audible on the very next block.

        Silently does nothing when there is no graph playing, or when
        `label` does not name a real engine parameter -- the same "a knob
        that waits is better than one that raises" convention
        `set_parameter()` already follows.
        """
        if self.playing is None:
            return False
        source_port = MOD_SOURCE_PORTS.get(source_id)
        param_id = param_id_for_label(dest_id, label)
        if source_port is None or param_id is None:
            return False
        return self.playing.graph.set_modulation_depth(
            source_id, source_port, dest_id, param_id, value)

    def judge_modulation(self, source_id, dest_id, label):
        """The engine's verdict on one modulation cable, or None when there
        is no graph to ask, or when either end has no engine module or the
        knob names no real engine parameter -- in every such case
        `patch_graph` falls back to its own copy of the rules, the same
        contract `judge()` above keeps for a sound cable.

        `label` is the display text the knob was registered under
        (`Knob.label()`'s docstring: "the half of its identity the patch
        layer keys modulation cables on"), turned into the engine's
        `param_id` by `param_id_for_label()` -- the canvas never learns the
        word `param_id`, the same way it never learns the word `port`.
        """
        poly = self.poly
        if poly is None:
            return None
        if poly.graph.node(source_id) is None or poly.graph.node(dest_id) is None:
            return None
        source_port = MOD_SOURCE_PORTS.get(source_id)
        if source_port is None:
            return None
        param_id = param_id_for_label(dest_id, label)
        if param_id is None:
            return None
        return poly.judge_modulation(source_id, source_port, dest_id, param_id)

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
