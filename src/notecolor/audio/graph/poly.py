"""The Mix node and the poly boundary (#204, decision 56 §3): where sixteen
held notes become one signal, as a graph object rather than an implicit
layer.

Decision 56 §3 settled that the boundary is **drawn, not hidden**, because
the owner's stated reason for wanting cables was seeing the true path, and a
boundary that summed invisibly would make the cables lie. This module is
that decision made real: one `MixModule` on the canvas, everything cabled
into it running once per held note, everything downstream running once.

## How the split is decided

Not by a property of a module, and not by where its window sits on screen --
by **the cables**. A node that can reach the Mix node is on the per-note
side; a node Mix can reach is on the once-only side. A module declaring
`POLY_PER_NOTE` or `POLY_ONCE` constrains which side it may be cabled onto
(`graph.judge()` refuses the crossing), but for everything declaring
`POLY_EITHER` -- which is most things -- the patch decides, which is the
whole point of a patch.

A module cabled to neither side falls back to its own declaration, and to
the once-only side when it has no opinion: an unpatched module has no voice
to belong to.

A module cabled to *both* -- which only a feedback loop drawn through Mix
can do -- has no side at all, and `straddlers()` refuses it rather than
picking one. See that method; it is #206's one addition to this file.

## What is instantiated how many times

`PolyGraph.activate()` builds `voices` complete copies of the per-note
subgraph, through `Module.new_instance()` -- a fresh module of the same type
and configuration, not a clone of a live one. That is 16 copies at
`config.POLYPHONY_SYNTH_VIEW`, all allocated up front, off the audio thread,
because a note-on that allocates is a note-on that can miss its deadline.
The once-only subgraph is built once, from the modules the caller put in the
graph.

## Voice lifecycle

`note_on()` takes a slot, resets its modules and points its `NoteContext` at
the new note. `note_off()` clears `gate`. A voice is reclaimed when a module
sets `note.finished` -- which is an amp envelope's job (#205), and until one
exists a released note **drones**, exactly as a modular with no envelope
patched does. That is not a gap being papered over: the alternative, having
the voice manager decide when a note has stopped sounding, is how a synth
ends up clicking on release.

Stealing, when all slots are busy: the oldest released voice, else the
oldest voice. `sound_engine.select_steal_index()` has the same policy for
the non-graph engines; this one is separate because it steals *subgraphs*,
not `Voice` objects, and sharing the function would mean sharing a data
model neither side wants.

## What the once-only side never sees

Note-off tears down nothing to the right of Mix. That is what keeps a delay's
tail ringing after the key is up (the test that proves it is in
`tests/test_synth_poly.py`), and it is the practical reason the boundary is
worth drawing at all: the two sides have different lifetimes, and the canvas
says which is which.
"""

from __future__ import annotations

import numpy as np

from notecolor.settings import config
from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Activation, Module, ModuleDescriptor, NoteContext, audio_in, audio_out,
)
from notecolor.audio.graph.graph import (
    REFUSE_POLY, Connection, ModuleGraph, Verdict,
)

#: How the two sides are named everywhere below, and in `gui/patch_graph.py`.
SIDE_POLY = "poly"
SIDE_MONO = "mono"


class MixModule(Module):
    """The boundary itself, as a node you can see and patch into.

    It does no arithmetic of its own. `PolyGraph` sums the voices into this
    module's output buffer before the once-only subgraph runs, so by the
    time `process()` is called the answer is already in place --
    deliberately, because the sum is across *graphs*, one per voice, and no
    module can see more than its own.

    That makes this the one module in the project whose `process()` leaves
    its output untouched, and the exception is worth the docstring: the
    alternative is a module that reaches outside its own context, which is
    the thing the contract exists to forbid.
    """

    def descriptor(self):
        return ModuleDescriptor(
            module_id="mix",
            name="Mix",
            poly=contract.POLY_ONCE,
            is_boundary=True,
            category="utility",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def process(self, ctx):
        """Intentionally empty -- see the class docstring."""


class Voice:
    """One slot: a complete copy of the per-note subgraph, plus the note it
    is currently rendering.

    Slots are made once and reused forever. A retired voice keeps its
    modules and its buffers; all that changes is the `NoteContext` and a
    `reset()`, which is what makes note-on free of allocation.
    """

    __slots__ = ("index", "graph", "compiled", "note", "age", "active")

    def __init__(self, index, graph):
        self.index = index
        self.graph = graph
        self.compiled = None
        self.note = NoteContext()
        self.age = 0
        self.active = False

    def start(self, note_id, pitch, velocity, frequency, age):
        for step in self.compiled.steps:
            step.module.reset()
        self.note.note_id = note_id
        self.note.pitch = pitch
        self.note.velocity = velocity
        self.note.frequency = frequency
        self.note.gate = True
        self.note.finished = False
        self.age = age
        self.active = True

    def retire(self):
        self.active = False
        self.note.gate = False
        self.note.finished = True


class PolyGraph:
    """A `ModuleGraph` containing exactly one `MixModule`, run as sixteen
    per-note subgraphs feeding one once-only subgraph.

    Everything here runs off the audio thread except `process()`.
    """

    def __init__(self, graph: ModuleGraph, voices=None):
        self.graph = graph
        self.voice_count = voices or config.POLYPHONY_SYNTH_VIEW
        self.boundary_id = self._find_boundary(graph)
        self.voices: list[Voice] = []
        self._mono = None
        self._mono_compiled = None
        self._mix_sources: tuple = ()
        self._mix_buffer = None
        #: Preallocated in `activate()`: where `output_block()` sums the
        #: patch's output nodes. One buffer for the life of the graph, so
        #: the host's callback allocates nothing to read the patch.
        self._out_buffer = None
        self._out_sources: tuple = ()
        self._next_note_id = 0
        self._clock = 0

    # -- the split -----------------------------------------------------------

    @staticmethod
    def _find_boundary(graph):
        found = [n.node_id for n in graph.nodes() if n.descriptor.is_boundary]
        if len(found) != 1:
            raise contract.ContractError(
                f"a patch needs exactly one Mix node; this one has {len(found)}")
        return found[0]

    def _cables(self, extra=None):
        """The patch's cables, optionally plus one being considered. Lets
        every reachability question below be asked about a cable that is not
        plugged in yet, which is what `judge()` needs."""
        if extra is None:
            return self.graph.connections
        return list(self.graph.connections) + [extra]

    def sides(self, extra=None):
        """`{node_id: SIDE_POLY | SIDE_MONO}` for every node but the Mix
        node, decided by the cables (see the module docstring)."""
        cables = self._cables(extra)
        upstream = self._reaches(self.boundary_id, forward=False, cables=cables)
        downstream = self._reaches(self.boundary_id, forward=True, cables=cables)
        sides = {}
        for node in self.graph.nodes():
            node_id = node.node_id
            if node_id == self.boundary_id:
                continue
            if node_id in upstream:
                sides[node_id] = SIDE_POLY
            elif node_id in downstream:
                sides[node_id] = SIDE_MONO
            else:
                # Cabled to neither side: its own declaration, and the
                # once-only side when it has none. An unpatched module has
                # no voice to belong to.
                sides[node_id] = (SIDE_POLY
                                  if node.descriptor.poly == contract.POLY_PER_NOTE
                                  else SIDE_MONO)
        return sides

    def straddlers(self, extra=None):
        """Nodes that both feed Mix and are fed by it -- i.e. nodes sitting
        on a loop that passes *through* the boundary.

        There is no coherent thing to build for one of these, which is why
        they are named rather than assigned a side. A loop through Mix would
        have to take the sum of sixteen voices and send it back into one
        voice, which is not a signal path the poly split can express: the
        node would have to be sixteen copies (it is upstream of Mix) and one
        copy (it is downstream) at the same time.

        Found here rather than in `graph.judge()` for decision 61 §2's
        reason: whether a node is upstream of Mix is a fact about the
        *patch*, and `judge()` only knows what a module declares. Before
        this existed the graph accepted such a cable -- correctly, since a
        Delay was in the loop -- and `PolyGraph` then silently dropped the
        half of it that crossed the boundary, so the loop the user drew did
        not exist and nothing said so.
        """
        cables = self._cables(extra)
        upstream = self._reaches(self.boundary_id, forward=False, cables=cables)
        downstream = self._reaches(self.boundary_id, forward=True, cables=cables)
        return upstream & downstream

    # -- where the sound comes out (#207) -------------------------------------

    def outputs(self):
        """The once-only node outputs that reach the speakers, as
        `[(node_id, port_id), ...]`.

        There is no Out node. The rule is: **anything you do not patch
        onward goes to the speakers.** Concretely, a once-only node that Mix
        can reach and that feeds nothing further is an output, and all of
        them are summed. If nothing at all is patched after Mix, Mix itself
        is the output, so a patch is audible the moment its voices reach the
        boundary.

        Chosen over an explicit Out node for one reason: it is impossible to
        get silently wrong. Forget to patch the last module anywhere and it
        is still the output; the failure mode of the alternative -- a
        finished patch that makes no sound because one cable is missing --
        does not exist here. The cost is that "output" is inferred rather
        than drawn, which decision 56 §3 would rather it were not; an
        explicit Out node beside the Mix node is the alternative and is
        flagged for the owner on #211.

        **Feedback loops need the rule stated more carefully than "no
        outgoing cable".** In `MIX → Delay → Bypass → Delay` every node
        feeds something, so a literal reading finds no output and the patch
        is silent -- which is exactly the failure this rule exists to avoid.
        So the test is not "feeds nothing" but "feeds nothing *new*": a node
        is an output when every node it feeds can reach it back again. A
        loop has no end, so every node on it counts as one. On a patch with
        no loops the two readings are identical, which is why the short
        sentence above is the one worth remembering.
        """
        sides = self.sides()
        downstream = self._reaches(self.boundary_id, forward=True)
        mono = [n for n in downstream if sides.get(n) == SIDE_MONO]
        if not mono:
            return [(self.boundary_id, "out")]
        found = []
        for node_id in mono:
            onward = [c.dest for c in self.graph.connections if c.source == node_id]
            if all(self._any_path_back(dest, node_id) for dest in onward):
                found.append(node_id)
        if not found:                       # unreachable; belt and braces
            return [(self.boundary_id, "out")]
        node_order = [n.node_id for n in self.graph.nodes()]
        found.sort(key=node_order.index)
        return [(node_id, self.graph.node(node_id).outputs[0].port_id)
                for node_id in found]

    def _any_path_back(self, start, goal):
        """Can `start` reach `goal` by following cables forwards? True means
        the cable into `start` is closing a loop rather than carrying the
        signal onward, which is what `outputs()` needs to tell apart."""
        seen = set()
        stack = [start]
        while stack:
            node_id = stack.pop()
            if node_id == goal:
                return True
            if node_id in seen:
                continue
            seen.add(node_id)
            for cable in self.graph.connections:
                if cable.source == node_id:
                    stack.append(cable.dest)
        return False

    def _reaches(self, start, forward, cables=None):
        """Every node reachable from `start` following cables forwards, or
        every node that can reach it following them backwards."""
        if cables is None:
            cables = self.graph.connections
        seen = set()
        stack = [start]
        while stack:
            node_id = stack.pop()
            for cable in cables:
                if forward and cable.source == node_id and cable.dest not in seen:
                    seen.add(cable.dest)
                    stack.append(cable.dest)
                elif not forward and cable.dest == node_id and cable.source not in seen:
                    seen.add(cable.source)
                    stack.append(cable.source)
        seen.discard(start)
        return seen

    def crossings(self):
        """Cables that leave the per-note side and land on the once-only
        side without passing through Mix.

        `graph.judge()` cannot see these on its own: it knows what a module
        *declares*, and a module declaring `POLY_EITHER` only becomes
        per-note by being cabled that way. So the patch-aware half of
        decision 56 §3's rule lives here, and `judge()` below is what the
        canvas should call.
        """
        sides = self.sides()
        return [c for c in self.graph.connections
                if c.dest != self.boundary_id
                and sides.get(c.source) == SIDE_POLY
                and sides.get(c.dest) == SIDE_MONO]

    def judge(self, source, source_port, dest, dest_port):
        """`graph.judge()` plus the side rule. The call the canvas makes."""
        verdict = self.graph.judge(source, source_port, dest, dest_port)
        if not verdict.ok:
            return verdict
        # Asked before the poly/mono question, because a node on both sides
        # of Mix has no side for that question to be about. This is the one
        # refusal a feedback cable can still collect after the cycle rule has
        # accepted it (decision 63 §3).
        straddling = self.straddlers(Connection(source, source_port, dest, dest_port))
        if straddling:
            named = ", ".join(sorted(self.graph.title(n) for n in straddling))
            return Verdict(False, REFUSE_POLY, (
                f"That runs the summed voices back into the per-note side "
                f"({named}). A feedback loop has to stay on one side of MIX — "
                f"put the Delay before MIX to loop inside a voice, or after it "
                f"to loop the mix."))
        if dest == self.boundary_id:
            return verdict
        sides = self.sides()
        if sides.get(source) == SIDE_POLY and sides.get(dest) == SIDE_MONO:
            return Verdict(False, REFUSE_POLY, (
                f"{self.graph.title(source)} is on the per-note side of MIX and "
                f"{self.graph.title(dest)} is on the once-only side. Send it "
                f"through MIX first — that is what MIX is for."))
        return verdict

    def connect(self, source, source_port, dest, dest_port):
        verdict = self.judge(source, source_port, dest, dest_port)
        if verdict.ok:
            self.graph.connect(source, source_port, dest, dest_port)
        return verdict

    # -- building ------------------------------------------------------------

    def activate(self, activation: Activation):
        """Build one once-only subgraph and `voice_count` per-note ones, and
        activate every module in all of them. Every allocation this design
        makes happens here."""
        straddling = self.straddlers()
        if straddling:
            raise contract.ContractError(
                "these modules sit on a loop through the Mix node, which has no "
                "side to be built on: " + ", ".join(sorted(straddling)))
        crossings = self.crossings()
        if crossings:
            named = ", ".join(f"{c.source} → {c.dest}" for c in crossings)
            raise contract.ContractError(
                f"these cables cross the Mix boundary without going through Mix: {named}")
        sides = self.sides()
        poly_ids = [n for n, side in sides.items() if side == SIDE_POLY]
        mono_ids = [n for n, side in sides.items() if side == SIDE_MONO]

        self._mono = self._subgraph(mono_ids + [self.boundary_id], clone=False)
        self._mono.activate(activation)
        mono_compiled = self._mono.compile()

        self.voices = []
        for index in range(self.voice_count):
            sub = self._subgraph(poly_ids, clone=True)
            sub.activate(activation)
            voice = Voice(index, sub)
            voice.compiled = sub.compile()
            self.voices.append(voice)

        # Which per-note outputs the Mix node is fed by. Held as node/port
        # pairs rather than buffers, because every voice has its own buffer
        # for the same pair.
        self._mix_sources = tuple(
            (c.source, c.source_port) for c in self.graph.connections
            if c.dest == self.boundary_id and c.source in set(poly_ids))
        self._mono_compiled = mono_compiled
        self._mix_buffer = mono_compiled.buffer(self.boundary_id)
        # Bound here, not in `output_block()`: which nodes are outputs is a
        # fact about the patch, and the patch cannot change without a
        # rebuild. Resolving it per block would be a dict lookup and a
        # graph walk inside the callback.
        self._out_sources = tuple(mono_compiled.buffer(node_id, port_id)
                                  for node_id, port_id in self.outputs())
        self._out_buffer = np.zeros(activation.max_block, dtype=np.float64)
        return self

    def _subgraph(self, node_ids, clone):
        """A `ModuleGraph` over `node_ids` and the cables between them.

        `clone=True` goes through `Module.new_instance()` rather than
        reusing the module object: sixteen voices need sixteen filters with
        sixteen `zi` histories, and a shared one is one voice played
        sixteen times as loud.
        """
        wanted = set(node_ids)
        sub = ModuleGraph()
        for node_id in node_ids:
            module = self.graph.node(node_id).module
            sub.add(node_id, module.new_instance() if clone else module)
        for cable in self.graph.connections:
            if cable.source in wanted and cable.dest in wanted:
                sub.force_connect(cable.source, cable.source_port,
                                  cable.dest, cable.dest_port)
        return sub

    def module(self, node_id, voice=None):
        """The live module behind a node -- the once-only one, or one
        voice's copy. How a knob edit reaches all sixteen copies: the caller
        loops, because there is no shared parameter store and inventing one
        would put a second source of truth on the audio thread."""
        if voice is None:
            return self._mono.node(node_id).module
        return self.voices[voice].graph.node(node_id).module

    def set_parameter(self, node_id, param_id, value):
        """One knob, everywhere it exists. The ordinary way to drive a
        per-note module from the UI."""
        mono = self._mono.node(node_id)
        if mono is not None:
            mono.module.params.set(param_id, value)
            return
        for voice in self.voices:
            voice.graph.node(node_id).module.params.set(param_id, value)

    # -- notes ---------------------------------------------------------------

    @property
    def active_voices(self):
        return [v for v in self.voices if v.active]

    def note_on(self, pitch, velocity=1.0, frequency=None):
        """Take a slot for a new note and return it. Never refuses: a synth
        that drops a note because it is busy is worse than one that steals
        the oldest, which is the same conclusion `sound_engine.VoiceManager`
        reached (decision 38)."""
        self._clock += 1
        self._next_note_id += 1
        voice = self._free_voice() or self._steal()
        if frequency is None:
            from notecolor.audio.sound_engine import frequency_for
            frequency = frequency_for(pitch)
        voice.start(self._next_note_id, pitch, velocity, frequency, self._clock)
        return voice

    def note_off(self, pitch):
        """Release every sounding voice on `pitch`. Clears the gate and
        nothing else -- what happens next is the patch's business, and with
        no envelope patched the answer is that it keeps sounding."""
        released = 0
        for voice in self.voices:
            if voice.active and voice.note.pitch == pitch and voice.note.gate:
                voice.note.gate = False
                released += 1
        return released

    def all_notes_off(self):
        for voice in self.voices:
            if voice.active:
                voice.retire()

    def _free_voice(self):
        for voice in self.voices:
            if not voice.active:
                return voice
        return None

    def _steal(self):
        """Oldest released voice, else oldest voice. Released first because
        a note the player has let go of is the one they will miss least."""
        released = [v for v in self.voices if not v.note.gate]
        pool = released or self.voices
        return min(pool, key=lambda v: v.age)

    # -- the audio thread ----------------------------------------------------

    def process(self, frames):
        """Run every sounding voice, sum them at Mix, then run the once-only
        side once.

        The summing here is the only place sixteen become one, which is the
        claim decision 56 §3 makes and the reason the node is drawn. It is
        not the only place *any* summing happens -- several cables meeting
        one input also sum, at a jack the canvas marks (decision 60 §3, the
        owner's call) -- but that is two signals inside one voice, not a
        voice count collapsing.

        The sum is scaled by `config.GRAPH_MIX_HEADROOM` (#222) right here,
        once, before the once-only side runs -- so a downstream filter or
        delay sees the same headroom-adjusted signal the speakers do, not a
        hot one it has to cope with separately. A fixed constant, not a
        1/N or 1/sqrt(active) normalisation: either of those would duck a
        held chord the instant another note joined it, which is a
        compressor nobody asked for and makes the instrument feel unstable
        under the hands. See `config.GRAPH_MIX_HEADROOM` for the
        measurements behind the value.
        """
        mix = self._mix_buffer
        mix[:frames] = 0.0
        for voice in self.voices:
            if not voice.active:
                continue
            voice.compiled.process(frames, note=voice.note)
            for node_id, port_id in self._mix_sources:
                np.add(mix[:frames], voice.compiled.buffer(node_id, port_id)[:frames],
                       out=mix[:frames])
            if voice.note.finished:
                voice.active = False
        np.multiply(mix[:frames], config.GRAPH_MIX_HEADROOM, out=mix[:frames])
        self._mono_compiled.process(frames)

    def output_block(self, frames):
        """Run one block and return the patch's output: `outputs()` summed
        into one preallocated buffer.

        The host's whole audio-thread interface. Allocates nothing -- the
        buffer and the list of sources were both fixed at `activate()` --
        and returns a float64 array the caller must copy out of rather than
        keep, because the next block overwrites it.
        """
        self.process(frames)
        out = self._out_buffer
        sources = self._out_sources
        if not sources:
            out[:frames] = 0.0
            return out
        np.copyto(out[:frames], sources[0][:frames])
        for extra in sources[1:]:
            np.add(out[:frames], extra[:frames], out=out[:frames])
        return out

    def buffer(self, node_id, port_id="out"):
        """A once-only node's output buffer -- including Mix's own, which is
        the patch's summed voices."""
        return self._mono_compiled.buffer(node_id, port_id)
