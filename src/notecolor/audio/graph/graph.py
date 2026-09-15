"""The graph that holds modules and cables, and decides what runs in what
order (#203, decision 56 §3/§4).

Two halves, deliberately separated, because they run on different threads
and have opposite rules:

- **`ModuleGraph`** is the editable patch. Nodes, cables, and `judge()` --
  the accept/refuse-with-reason contract the canvas argues with. It is
  allowed to allocate, raise, and take as long as it likes. It never runs
  on the audio thread.
- **`CompiledGraph`** is what the audio thread runs: a flat tuple of steps
  in execution order, every buffer already bound. It cannot be edited, and
  building one never disturbs the one already playing.

`ModuleGraph.compile()` turns the first into the second. The host holds one
attribute pointing at a `CompiledGraph`; an edit builds a replacement off
the audio thread and rebinds that one attribute, which is a single store
the callback either sees or does not. That is the whole of "graph edits are
staged and swapped" -- there is no lock, no queue and no torn state,
because nothing is ever mutated in place.

## Execution order

A topological sort, recomputed in `compile()` and never inside the
callback. The one subtlety is which edges constrain it: **a cable leaving a
module with `block_delay >= 1` does not**. Such a module's output for this
block was computed from input it read a block ago, so whatever it feeds may
legitimately run before it. Cutting those edges is what makes a feedback
loop an ordinary DAG -- the cycle is not solved, it is *ordered*, which is
the only thing a block-at-a-time engine can do (decision 56 §4).

Two consequences worth stating here, because both are easy to rediscover the
hard way (decision 63):

- **The frame arithmetic is not here and must not be.** This file asks one
  question of a module -- `descriptor().block_delay >= 1` -- and takes the
  answer as a guarantee. Flooring a delay time to a whole block is
  `modules/delay.py`'s job, deliberately, so that shortening the minimum
  later (sub-blocks, a compiled core) touches one module and no graph code.
  A second copy of the rule in here would be a second thing to keep true.
- **A loop's period is the delay plus one block.** The cut cable is read
  *after* the block that wrote it, so the module the loop comes back into
  sees the delay's previous output. That extra block is the ordering's, not
  the delay's, and it is why a one-block delay in a loop repeats every two.

## The refusal contract

`judge()` returns a `Verdict`: yes, or no with a `code` the UI can style and
a sentence meant for the person holding the cable. Decision 56's stated
reason for cables is seeing the true path; a refusal that cannot say what is
wrong *and* what to do instead would undo that, so a bare "invalid
connection" is a bug in `judge()` and not a terse style.

`gui/patch_graph.PatchGraph.judge()` is the canvas's stand-in for this,
written before the engine existed and shaped to match. It becomes a
delegation, not a rewrite.

## Summing at an input

Several cables may land on one input port; the graph sums them. This is what
`gui/patch_graph.socket_counts()` already assumes (a module shows as many
holes as it has cables, plus a spare) and what any usable patch needs --
osc1 and osc2 both reaching one filter, inside one voice, is the most
ordinary patch there is, and the Mix node cannot do it because Mix is the
*poly boundary*: anything through it comes out once-only.

Worth being explicit, because #204's wording says Mix is "the only place
summing happens". That sentence is decision 56 §3's rule about the **poly
boundary** -- sixteen voices must not become one anywhere except where it is
drawn -- and this is a different operation: two sources on the same side of
the boundary, in the same voice, at a jack you can see. Flagged on #204 for
the owner rather than settled here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, ProcessContext

# -- refusal codes -----------------------------------------------------------
#
# A code for the UI to style on, beside the sentence for the person. Both,
# always: the code alone cannot be shown to anyone and the sentence alone
# cannot be branched on.

REFUSE_NO_SUCH_NODE = "no_such_node"
REFUSE_NO_SUCH_PORT = "no_such_port"
REFUSE_DIRECTION = "direction"
REFUSE_TYPE = "type"
REFUSE_POLY = "poly"
REFUSE_CYCLE = "cycle"
REFUSE_DUPLICATE = "duplicate"
REFUSE_SELF = "self"
#: A mod cable dropped on a knob `ParamSpec.modulatable=False` marks off
#: limits (#208, decision 56 §5's settlement §1) -- a waveform choice, an
#: octave, a filter type. Distinct from `REFUSE_NO_SUCH_PORT` because the
#: knob exists; it simply does not take this kind of cable.
REFUSE_NOT_MODULATABLE = "not_modulatable"


@dataclass(frozen=True)
class Verdict:
    """`judge()`'s answer. `ok` is the branch; `reason` is the sentence the
    canvas puts beside the flashing jack; `code` is for styling and tests,
    so neither has to be parsed out of the other."""

    ok: bool
    code: str = ""
    reason: str = ""

    def __bool__(self):
        return self.ok


ACCEPT = Verdict(True)


@dataclass(frozen=True)
class Connection:
    """One cable, by node and port id. Ids rather than objects, so a cable
    survives a module being replaced -- which is what a plugin rescan, or
    #204's per-note instancing, does underneath it."""

    source: str
    source_port: str
    dest: str
    dest_port: str


@dataclass(frozen=True)
class ModConnection:
    """One modulation cable: the second routing table (#208, decision 56
    §5's settlement §1), addressed `(source node, source port) -> (dest
    node, param_id, depth)`. The destination is a *parameter id*, never a
    port -- there is no hidden `mod_in` socket to hang this off, which is
    the whole reason this is a separate table rather than an ordinary
    `Connection`.

    `depth_index` is a slot in the owning `ModuleGraph.mod_depths` array,
    not the depth value itself, for the same reason `ParamBlock.values` is
    an array rather than attributes: the ring UI (#210 §4) turns this
    number in real time, and a single float64 store the audio thread reads
    every block is what lets it do that without a recompile.
    """

    source: str
    source_port: str
    dest: str
    param_id: str
    depth_index: int


class Node:
    """One module on the canvas, with the bookkeeping the graph needs cached
    off the audio thread: its descriptor, and its ports split by direction in
    the order `ProcessContext.inputs`/`outputs` expect."""

    def __init__(self, node_id, module):
        self.node_id = node_id
        self.module = module
        self.descriptor = module.descriptor()
        ports = tuple(module.ports())
        self.inputs = tuple(p for p in ports if p.direction == contract.DIRECTION_IN)
        self.outputs = tuple(p for p in ports if p.direction == contract.DIRECTION_OUT)
        self._by_id = {p.port_id: p for p in ports}

    @property
    def title(self):
        return self.descriptor.name

    @property
    def is_delayed(self):
        """True when this module's output is at least a whole block behind
        its input -- the property that lets a cable leaving it close a
        loop."""
        return self.descriptor.block_delay >= 1

    def port(self, port_id):
        return self._by_id.get(port_id)

    def input_index(self, port_id):
        return next(i for i, p in enumerate(self.inputs) if p.port_id == port_id)

    def output_index(self, port_id):
        return next(i for i, p in enumerate(self.outputs) if p.port_id == port_id)


class CycleError(contract.ContractError):
    """A cycle survived into `compile()`. Only reachable by building a graph
    without asking `judge()` -- which tests do on purpose, and the UI never
    does."""


class ModuleGraph:
    """The editable patch: modules, cables, and the rules over them.

    Every method here runs off the audio thread. Nothing here is called from
    inside a callback, and `compile()` is the only thing that produces
    something that is.
    """

    def __init__(self):
        self._nodes: dict[str, Node] = {}
        self.connections: list[Connection] = []
        #: The modulation routing table (#208). Parallel to `connections`
        #: but a different shape, per decision 56 §5's settlement §1: a
        #: mod cable's destination is a parameter id, not a port.
        self.mod_connections: list[ModConnection] = []
        #: One depth per entry ever added to `mod_connections`, indexed by
        #: `ModConnection.depth_index`. Never shrunk on disconnect -- a
        #: freed slot is simply never read again -- so an index handed to a
        #: compiled graph stays valid for that graph's whole life.
        self.mod_depths: np.ndarray = np.zeros(0, dtype=np.float64)
        self._activation: Activation | None = None
        #: `{node_id: name}` -- the host's name for a node, where it knows
        #: one the module cannot. See `add()` and `title()`.
        self.titles: dict[str, str] = {}
        #: Bumped on every structural edit, so a host can tell whether the
        #: `CompiledGraph` it is holding is still the current one.
        self.revision = 0

    # -- nodes ---------------------------------------------------------------

    def add(self, node_id, module, poly=None, title=None):
        """Put a module on the canvas.

        `poly` and `title` are the two facts a *host* can know better than
        the module does, and both exist because `gui/patch_bridge.py` is one
        (#207):

        - `poly` replaces `descriptor().poly` for this node only. Decision
          61 §2 says a `POLY_EITHER` module's side is decided by the patch;
          the Synth View's canvas has already decided it (its drawer sorts
          effects onto the once-only side and everything else onto the
          per-note one), and handing that decision down here is what stops
          the canvas's answer and the engine's answer from being two
          different answers about the same screen. It replaces the *node's*
          cached descriptor, never the module's -- sixteen voices share one
          module type, and a type has no side.
        - `title` is the name a refusal sentence uses for this node. The
          user is looking at a window captioned "FILTER"; a sentence that
          says "Filter" is naming something else on the same screen.

        Both default to None, which is "ask the module", so every existing
        caller is unaffected.
        """
        if node_id in self._nodes:
            raise contract.ContractError(f"a node called {node_id!r} is already on the canvas")
        contract.validate(module)
        node = Node(node_id, module)
        if poly is not None:
            node.descriptor = dataclasses.replace(node.descriptor, poly=poly)
        self._nodes[node_id] = node
        if title is not None:
            self.titles[node_id] = title
        if self._activation is not None:
            module.activate(self._activation)
        self.revision += 1
        return node

    def remove(self, node_id):
        """Drop a module and every cable touching it. Removing a module is
        an explicit act (decision 56 §2) -- closing its window is not this."""
        node = self._nodes.pop(node_id, None)
        if node is None:
            return False
        self.connections = [c for c in self.connections
                            if c.source != node_id and c.dest != node_id]
        self.mod_connections = [c for c in self.mod_connections
                                 if c.source != node_id and c.dest != node_id]
        self.titles.pop(node_id, None)
        if self._activation is not None:
            node.module.deactivate()
        self.revision += 1
        return True

    def node(self, node_id) -> Node | None:
        return self._nodes.get(node_id)

    def nodes(self):
        return list(self._nodes.values())

    def title(self, node_id):
        """What a refusal sentence calls this node: the host's name for it
        if it gave one (`add(title=...)`), else the module's own."""
        if node_id in self.titles:
            return self.titles[node_id]
        node = self._nodes.get(node_id)
        return node.title if node else node_id

    # -- activation ----------------------------------------------------------

    def activate(self, activation: Activation):
        """Give every module its sample rate and maximum block, and build
        the buffers the compiled graph will bind. Allocation happens here
        and in `add()`, never in `compile()` and never in the callback."""
        self._activation = activation
        for node in self._nodes.values():
            node.module.activate(activation)
        return self

    def deactivate(self):
        for node in self._nodes.values():
            node.module.deactivate()
        self._activation = None

    # -- cables --------------------------------------------------------------

    def sources_of(self, node_id, port_id):
        return [c for c in self.connections
                if c.dest == node_id and c.dest_port == port_id]

    def summed_inputs(self):
        """`{(node_id, port_id): cable count}` for every input carrying more
        than one cable.

        The owner's answer to decision 60 §3: inputs **do** sum, and the
        canvas **marks the jack where it happens**. Summing itself was never
        the objection -- decision 56 §3 objected to summing somewhere the
        screen does not admit to. So the graph publishes exactly where it
        sums and the canvas draws it (#211); neither has to infer it from
        counting cables itself.
        """
        counts: dict[tuple[str, str], int] = {}
        for cable in self.connections:
            key = (cable.dest, cable.dest_port)
            counts[key] = counts.get(key, 0) + 1
        return {key: n for key, n in counts.items() if n > 1}

    def judge(self, source, source_port, dest, dest_port) -> Verdict:
        """Would this cable be accepted? Every refusal names what is wrong
        and what to do instead."""
        src = self._nodes.get(source)
        dst = self._nodes.get(dest)
        if src is None or dst is None:
            return Verdict(False, REFUSE_NO_SUCH_NODE,
                           "That module is no longer on the canvas.")

        out_port = src.port(source_port)
        in_port = dst.port(dest_port)
        if out_port is None:
            return Verdict(False, REFUSE_NO_SUCH_PORT,
                           f"{self.title(source)} has no socket called {source_port!r}.")
        if in_port is None:
            return Verdict(False, REFUSE_NO_SUCH_PORT,
                           f"{self.title(dest)} has no socket called {dest_port!r}.")

        if out_port.direction != contract.DIRECTION_OUT:
            return Verdict(False, REFUSE_DIRECTION, (
                f"{out_port.name} on {self.title(source)} is an input. A cable runs "
                f"out of one module and into another."))
        if in_port.direction != contract.DIRECTION_IN:
            return Verdict(False, REFUSE_DIRECTION, (
                f"{in_port.name} on {self.title(dest)} is an output. A cable runs "
                f"out of one module and into another."))

        if out_port.kind != in_port.kind:
            return Verdict(False, REFUSE_TYPE,
                           self._type_sentence(source, out_port, dest, in_port))

        if source == dest:
            return Verdict(False, REFUSE_SELF, (
                f"{self.title(source)} cannot feed itself. A loop needs a Delay in it, "
                f"so the sound comes back a block later instead of instantly."))

        if any(c.source == source and c.source_port == source_port
               and c.dest == dest and c.dest_port == dest_port
               for c in self.connections):
            return Verdict(False, REFUSE_DUPLICATE, (
                f"{self.title(source)} is already patched into {in_port.name} on {self.title(dest)}."))

        poly = self._poly_verdict(src, dst)
        if not poly.ok:
            return poly

        return self._cycle_verdict(src, dst, source_port, dest_port)

    def connect(self, source, source_port, dest, dest_port) -> Verdict:
        """Judge, and plug in if accepted. Returns the same `Verdict` either
        way, so a caller that does not care about the reason can just test
        it."""
        verdict = self.judge(source, source_port, dest, dest_port)
        if verdict.ok:
            self.connections.append(Connection(source, source_port, dest, dest_port))
            self.revision += 1
        return verdict

    def force_connect(self, source, source_port, dest, dest_port):
        """Plug in without judging. For tests that need to build a patch the
        rules forbid, so `compile()`'s own defences can be exercised.
        Nothing in the app calls this."""
        self.connections.append(Connection(source, source_port, dest, dest_port))
        self.revision += 1

    def disconnect(self, source, source_port, dest, dest_port):
        wanted = Connection(source, source_port, dest, dest_port)
        if wanted in self.connections:
            self.connections.remove(wanted)
            self.revision += 1
            return True
        return False

    # -- modulation cables (#208) ---------------------------------------------

    @staticmethod
    def _clip_depth(value):
        """Bipolar, -1..+1, as a fraction of the destination knob's own
        range (decision 56 §5's settlement §4) -- clamped here so a
        malformed UI drag cannot hand the audio thread a depth that blows
        the clamp-after-sum step's assumptions."""
        return min(max(float(value), -1.0), 1.0)

    @staticmethod
    def param_spec(node, param_id):
        for spec in node.module.parameters():
            if spec.param_id == param_id:
                return spec
        return None

    def _existing_mod_connection(self, source, source_port, dest, param_id):
        for c in self.mod_connections:
            if (c.source, c.source_port, c.dest, c.param_id) == (
                    source, source_port, dest, param_id):
                return c
        return None

    def judge_modulation(self, source, source_port, dest, param_id) -> Verdict:
        """Would a mod cable from `source_port` land on `dest`'s `param_id`
        knob? The rules that do not need to know which side of Mix
        anything is on -- `poly.PolyGraph.judge_modulation()` adds that
        one, the same way it wraps `judge()` for audio cables.

        Every refusal names what is wrong and what to do instead, per this
        file's own rule for `judge()`.
        """
        src = self._nodes.get(source)
        dst = self._nodes.get(dest)
        if src is None or dst is None:
            return Verdict(False, REFUSE_NO_SUCH_NODE,
                           "That module is no longer on the canvas.")

        out_port = src.port(source_port)
        if out_port is None:
            return Verdict(False, REFUSE_NO_SUCH_PORT,
                           f"{self.title(source)} has no socket called {source_port!r}.")
        if out_port.direction != contract.DIRECTION_OUT:
            return Verdict(False, REFUSE_DIRECTION, (
                f"{out_port.name} on {self.title(source)} is an input. A modulation "
                f"cable runs out of a module and onto a knob."))
        if out_port.kind != contract.PORT_MOD:
            names = {contract.PORT_AUDIO: "sound", contract.PORT_EVENT: "notes"}
            return Verdict(False, REFUSE_TYPE, (
                f"{out_port.name} on {self.title(source)} sends "
                f"{names.get(out_port.kind, out_port.kind)}, and a knob takes knob "
                f"movement. Patch its Mod output instead."))

        spec = self.param_spec(dst, param_id)
        if spec is None:
            return Verdict(False, REFUSE_NO_SUCH_PORT,
                           f"{self.title(dest)} has no knob called {param_id!r}.")
        if not spec.modulatable:
            return Verdict(False, REFUSE_NOT_MODULATABLE, (
                f"{spec.name} on {self.title(dest)} does not take modulation."))

        if source == dest:
            return Verdict(False, REFUSE_SELF,
                           f"{self.title(source)} cannot modulate its own knob.")

        if self._existing_mod_connection(source, source_port, dest, param_id) is not None:
            return Verdict(False, REFUSE_DUPLICATE, (
                f"{self.title(source)} is already modulating {spec.name} on "
                f"{self.title(dest)}."))

        return ACCEPT

    def _add_mod_connection(self, source, source_port, dest, param_id, depth):
        """Off the audio thread only -- grows `mod_depths`, which the
        compiled graph then reads by index, never by name."""
        index = self.mod_depths.shape[0]
        self.mod_depths = np.append(self.mod_depths, self._clip_depth(depth))
        self.mod_connections.append(
            ModConnection(source, source_port, dest, param_id, index))
        self.revision += 1
        return index

    def connect_modulation(self, source, source_port, dest, param_id, depth=1.0) -> Verdict:
        """Judge, and patch the mod cable in if accepted -- `connect()`'s
        counterpart for the routing table."""
        verdict = self.judge_modulation(source, source_port, dest, param_id)
        if verdict.ok:
            self._add_mod_connection(source, source_port, dest, param_id, depth)
        return verdict

    def force_connect_modulation(self, source, source_port, dest, param_id, depth=1.0):
        """Plug in without judging -- `force_connect()`'s counterpart, for
        tests that need to build a routing the rules forbid."""
        return self._add_mod_connection(source, source_port, dest, param_id, depth)

    def disconnect_modulation(self, source, source_port, dest, param_id):
        c = self._existing_mod_connection(source, source_port, dest, param_id)
        if c is None:
            return False
        self.mod_connections.remove(c)
        self.revision += 1
        return True

    def set_modulation_depth(self, source, source_port, dest, param_id, value):
        """The ring's write path (decision 56 §5's settlement §4, and #210
        §3's dashed ring): depth lives on the connection and is edited here
        without touching graph structure, so turning it never triggers a
        recompile -- the same reasoning `ParamBlock.set()` already gives a
        knob edit."""
        c = self._existing_mod_connection(source, source_port, dest, param_id)
        if c is None:
            return False
        self.mod_depths[c.depth_index] = self._clip_depth(value)
        return True

    # -- the rules ------------------------------------------------------------

    def _type_sentence(self, source, out_port, dest, in_port):
        """Decision 56 §5: sound and knob movement are different cables, and
        a refusal has to say which one the user is holding."""
        names = {contract.PORT_AUDIO: "sound",
                 contract.PORT_MOD: "knob movement",
                 contract.PORT_EVENT: "notes"}
        return (f"{out_port.name} on {self.title(source)} sends "
                f"{names.get(out_port.kind, out_port.kind)}, and {in_port.name} on "
                f"{self.title(dest)} takes {names.get(in_port.kind, in_port.kind)}.")

    def _poly_verdict(self, src, dst) -> Verdict:
        """Decision 56 §3. A per-note output into a once-only input is
        refused rather than silently summed -- Reaktor's behaviour, not
        VCV's, because auto-summing puts summing in places the Mix node does
        not represent.

        `POLY_EITHER` is compatible with both sides; which one it actually
        ends up on is #204's problem, and this rule does not need the
        answer to refuse the case that is wrong either way.

        The Mix node is the exception in both directions, because it is the
        boundary rather than a side of it: per-note cables are what it
        exists to accept, and what leaves it is once-only however per-note
        the sixteen things that went in were.
        """
        src_poly = (contract.POLY_ONCE if src.descriptor.is_boundary
                    else src.descriptor.poly)
        if dst.descriptor.is_boundary:
            return ACCEPT
        if (src_poly == contract.POLY_PER_NOTE
                and dst.descriptor.poly == contract.POLY_ONCE):
            return Verdict(False, REFUSE_POLY, (
                f"{self.title(src.node_id)} runs once per held note; "
                f"{self.title(dst.node_id)} runs once. "
                f"Send it through MIX first — that is what MIX is for."))
        return ACCEPT

    def _cycle_verdict(self, src, dst, source_port, dest_port) -> Verdict:
        """Decision 56 §4, Bitwig's rule: a loop is legal only through a
        module that is at least one block behind.

        Asked as an ordering question rather than as a graph-theory one. A
        cable *leaving* a delay does not constrain execution order, so if
        the proposed cable closes a loop that contains one, the loop is
        already gone from the order and there is nothing to refuse. What is
        left is exactly the illegal case.
        """
        if src.is_delayed:
            return ACCEPT
        path = self._ordering_path(dst.node_id, src.node_id)
        if path is None:
            return ACCEPT
        named = " → ".join(self.title(n) for n in path) + f" → {self.title(dst.node_id)}"
        return Verdict(False, REFUSE_CYCLE, (
            f"That closes a loop with no Delay in it ({named}). A feedback "
            f"loop needs a Delay module, so the sound comes back one block "
            f"later instead of instantly."))

    def _ordering_edges(self):
        """The cables that constrain execution order: every audio/mod-port
        cable except those leaving a module whose output is already a
        block old, plus every modulation-routing-table entry -- a knob's
        modulation has to be computed from a value the source already
        produced this block, exactly like an audio cable, and reuses the
        same ordering machinery rather than a second copy of it.

        Known gap, left for #211's canvas work: a cycle built only from mod
        edges (or a mix of mod and audio edges) is not yet given a graceful
        `Verdict` refusal the way an audio-only cycle is by
        `_cycle_verdict()` -- it surfaces as `compile()`'s `CycleError`
        instead, which is safe (nothing runs on a graph that would not
        terminate) but not yet a sentence aimed at the person holding the
        cable.
        """
        edges = [c for c in self.connections if not self._nodes[c.source].is_delayed]
        edges += [Connection(c.source, c.source_port, c.dest, "")
                  for c in self.mod_connections
                  if c.source in self._nodes and c.dest in self._nodes]
        return edges

    def _ordering_path(self, start, goal):
        """Depth-first search for `goal` from `start` over ordering edges
        only. Returns the node ids along the way, or `None` -- the ids are
        what lets a refusal name the whole loop back to the user instead of
        just asserting one exists."""
        edges = self._ordering_edges()
        seen = set()
        stack = [(start, [start])]
        while stack:
            node_id, path = stack.pop()
            if node_id == goal:
                return path
            if node_id in seen:
                continue
            seen.add(node_id)
            for cable in edges:
                if cable.source == node_id:
                    stack.append((cable.dest, path + [cable.dest]))
        return None

    def loop_members(self):
        """Every node sitting on a legal feedback loop. The canvas colours
        these (decision 57 §5's `loop_marking`); searched over the real cable
        set, including the delay edges the ordering ignores, because the
        thing being drawn is the loop the user made, not the DAG we run."""
        members = set()
        for cable in self.connections:
            path = self._any_path(cable.dest, cable.source)
            if path is not None:
                members.update(path)
        return members

    def _any_path(self, start, goal):
        seen = set()
        stack = [(start, [start])]
        while stack:
            node_id, path = stack.pop()
            if node_id == goal:
                return path
            if node_id in seen:
                continue
            seen.add(node_id)
            for cable in self.connections:
                if cable.source == node_id:
                    stack.append((cable.dest, path + [cable.dest]))
        return None

    # -- ordering and compilation --------------------------------------------

    def order(self):
        """Node ids in execution order.

        Kahn's algorithm over the ordering edges, with ties broken by
        insertion order so the result is deterministic -- a graph that
        reorders itself between two identical compiles would make every
        ordering bug unreproducible.
        """
        edges = self._ordering_edges()
        remaining = {node_id: 0 for node_id in self._nodes}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in self._nodes}
        for cable in edges:
            if cable.source == cable.dest:
                continue
            remaining[cable.dest] += 1
            outgoing[cable.source].append(cable.dest)

        ready = [node_id for node_id in self._nodes if remaining[node_id] == 0]
        ordered = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(node_id)
            for target in outgoing[node_id]:
                remaining[target] -= 1
                if remaining[target] == 0:
                    ready.append(target)
        if len(ordered) != len(self._nodes):
            stuck = sorted(n for n in self._nodes if n not in ordered)
            raise CycleError(
                "a cycle with no Delay in it reached compile(): " + ", ".join(stuck))
        return ordered

    def compile(self) -> "CompiledGraph":
        """Build what the audio thread runs.

        Allocates freely -- one buffer per output port, one more per input
        port carrying several cables -- because it runs off the audio
        thread, on a cable change, while the previous `CompiledGraph` keeps
        playing. That is the trade the staged-and-swapped design buys: all
        the cost lands here, and none of it lands in `process()`.

        There is no incremental path and there should not be one. Rewiring
        a patch rebuilds the whole thing; at this size that is microseconds,
        and an incremental binder is where torn state would come from.
        """
        if self._activation is None:
            raise contract.ContractError("compile() before activate()")
        max_block = self._activation.max_block
        zeros = np.zeros(max_block, dtype=np.float64)
        out_buffers = {}
        for node in self._nodes.values():
            for port in node.outputs:
                out_buffers[(node.node_id, port.port_id)] = np.zeros(
                    max_block, dtype=np.float64)

        # `(dest, param_id) -> [(source_buffer, depths_array, depth_index)]`
        # -- every modulation route whose source has an output buffer in
        # *this* graph. A cross-boundary route (a global source reaching a
        # per-note destination) never has one here: `poly.PolyGraph`
        # resolves those itself, one graph up, because the source and dest
        # buffers this needs live in two different `CompiledGraph`s.
        mod_targets: dict[tuple[str, str], list] = {}
        for c in self.mod_connections:
            source_buf = out_buffers.get((c.source, c.source_port))
            if source_buf is None or c.dest not in self._nodes:
                continue
            mod_targets.setdefault((c.dest, c.param_id), []).append(
                (source_buf, self.mod_depths, c.depth_index))

        steps = []
        for node_id in self.order():
            node = self._nodes[node_id]
            inputs = []
            sums = []
            for port in node.inputs:
                sources = [out_buffers[(c.source, c.source_port)]
                           for c in self.sources_of(node_id, port.port_id)
                           if (c.source, c.source_port) in out_buffers]
                if not sources:
                    # Unconnected: the shared zero buffer, so no module ever
                    # has to test for `None` and no branch enters the callback.
                    inputs.append(zeros)
                elif len(sources) == 1:
                    # One cable: bind straight through. A copy here would be
                    # pure cost -- no module writes to its inputs.
                    inputs.append(sources[0])
                else:
                    target = np.zeros(max_block, dtype=np.float64)
                    sums.append((target, tuple(sources)))
                    inputs.append(target)

            params = node.module.params
            mods = []
            if params is not None:
                for spec in params.specs:
                    sources = mod_targets.get((node_id, spec.param_id))
                    if not sources:
                        continue
                    mods.append(ModRoute(
                        param_index=params.index(spec.param_id),
                        minimum=spec.minimum, maximum=spec.maximum,
                        buffers=params.buffers, smoothed=params.smoothed,
                        mod_active=params.mod_active,
                        scratch=np.zeros(max_block, dtype=np.float64),
                        sources=tuple(sources),
                    ))

            ctx = ProcessContext(
                frames=0, sample_rate=self._activation.sample_rate,
                inputs=tuple(inputs),
                outputs=tuple(out_buffers[(node_id, p.port_id)] for p in node.outputs),
                params=params.smoothed if params else None,
                param_buffers=params.buffers if params else None,
                param_mod_active=params.mod_active if params else None,
            )
            steps.append(Step(node.module, ctx, tuple(sums), tuple(mods), node_id))

        return CompiledGraph(tuple(steps), out_buffers, self.revision)


@dataclass(frozen=True)
class ModRoute:
    """One destination knob's incoming modulation, prebound for the audio
    thread (#208, decision 56 §5's settlement §2/§4).

    `buffers`/`smoothed`/`mod_active` are the destination's own
    `ParamBlock` arrays -- the same objects `ProcessContext` binds, so
    writing here is exactly what the module reads. `sources` is
    `(source_buffer, depths_array, depth_index)` per incoming cable:
    `depths_array[depth_index]` is read fresh every block, never copied,
    which is what lets the ring (#210 §4) change a depth without a
    recompile.
    """

    param_index: int
    minimum: float
    maximum: float
    buffers: np.ndarray
    smoothed: np.ndarray
    mod_active: np.ndarray
    #: Reused across sources and blocks -- one route's sources are applied
    #: serially, so one scratch array is enough.
    scratch: np.ndarray
    sources: tuple

    def apply(self, frames):
        """Sum every source into the destination's buffer, scaled by its
        own depth as a fraction of the knob's range, then clamp once --
        decision 56 §5's settlement §4: multiple sources sum, then clamp
        at the destination, never per source.

        Starts from `smoothed` rather than zero unless a knob edit is
        already mid-fade and has seeded the buffer itself (`mod_active`
        already true coming in) -- modulation adds *around* wherever the
        knob currently sits, edited or not.
        """
        buf = self.buffers[self.param_index]
        if not self.mod_active[self.param_index]:
            buf[:frames] = self.smoothed[self.param_index]
            self.mod_active[self.param_index] = True
        span = self.maximum - self.minimum
        scratch = self.scratch
        for source_buf, depths, depth_index in self.sources:
            depth = depths[depth_index]
            np.multiply(source_buf[:frames], depth * span, out=scratch[:frames])
            np.add(buf[:frames], scratch[:frames], out=buf[:frames])
        np.clip(buf[:frames], self.minimum, self.maximum, out=buf[:frames])


@dataclass(frozen=True)
class Step:
    """One module's turn: sum whatever lands on its shared inputs, apply
    whatever modulation lands on its knobs, then run it. Frozen and
    prebound, because the audio thread must not discover anything."""

    module: object
    ctx: ProcessContext
    #: `(target, sources)` for each input port carrying more than one cable.
    #: Empty for the overwhelming majority of nodes.
    sums: tuple
    #: One `ModRoute` per modulated parameter. Empty for every module
    #: nothing is patched into, which is most of them (#208's whole reason
    #: for the scalar-or-buffer split).
    mods: tuple = ()
    #: This step's node id -- not needed to *run* the step, only to find it
    #: again. `poly.PolyGraph.with_extra_mods()` is the one caller: a
    #: global-source-to-per-note-destination route lives in a different
    #: `CompiledGraph` than its destination, so it cannot be an ordinary
    #: `mod_targets` entry inside this graph's own `compile()`.
    node_id: str = ""


class CompiledGraph:
    """A patch, flattened for the audio thread.

    Immutable by construction and by intent. An edit does not change one of
    these; it builds another, and the host rebinds the attribute pointing at
    it. The callback therefore sees either the whole old graph or the whole
    new one, and never a half-rewired anything.
    """

    __slots__ = ("steps", "_buffers", "revision", "block_index")

    def __init__(self, steps, buffers, revision):
        self.steps = steps
        self._buffers = buffers
        self.revision = revision
        self.block_index = 0

    def buffer(self, node_id, port_id="out"):
        """The output buffer of one port -- how a host finds the patch's
        final signal, and how a test reads an intermediate one."""
        return self._buffers[(node_id, port_id)]

    def with_extra_mods(self, node_id, extra_mods) -> "CompiledGraph":
        """A new `CompiledGraph`, identical to this one except that
        `node_id`'s step has `extra_mods` appended to its `ModRoute`s.

        The one caller is `poly.PolyGraph.activate()`, for a modulation
        cable that crosses the Mix boundary from a global source to a
        per-note destination (decision 56 §5's settlement §3): the source
        and destination compile into two different `CompiledGraph`s
        (mono's and one per voice), so the route cannot be an ordinary
        entry in either one's own `compile()`. Called once, off the audio
        thread, before the graph ever runs -- it is a second assembly
        pass, not a live edit, and `CompiledGraph`'s own immutability is
        unaffected: this builds a new one rather than mutating `self`.
        """
        steps = tuple(
            dataclasses.replace(step, mods=step.mods + tuple(extra_mods))
            if step.node_id == node_id else step
            for step in self.steps)
        if all(step.node_id != node_id for step in self.steps):
            raise contract.ContractError(f"no such node in this compiled graph: {node_id!r}")
        return CompiledGraph(steps, self._buffers, self.revision)

    def process(self, frames, note=None):
        """Run every module once, in order. The audio thread's whole job.

        No allocation, no dict lookup, no branch that depends on the patch:
        everything conditional was decided in `compile()`.
        """
        self.block_index += 1
        for step in self.steps:
            # Smoothing and modulation both land on the parameter path
            # before the module ever runs (#208): a knob edit fades in
            # (`advance()`), and anything patched onto it is added on top
            # (`ModRoute.apply()`), so `process()` sees one already-settled
            # array either way and never has to know which happened.
            step.module.params.advance(frames)
            for route in step.mods:
                route.apply(frames)
            for target, sources in step.sums:
                np.copyto(target[:frames], sources[0][:frames])
                for extra in sources[1:]:
                    np.add(target[:frames], extra[:frames], out=target[:frames])
            ctx = step.ctx
            ctx.frames = frames
            ctx.note = note
            ctx.block_index = self.block_index
            step.module.process(ctx)
