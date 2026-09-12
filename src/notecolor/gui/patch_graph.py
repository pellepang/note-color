"""The patch as data: nodes, cables, the accept/refuse contract, the cable
physics, and the six appearance values decision 57 §5 names as settings.

Deliberately Qt-free. Everything here is arithmetic and bookkeeping over
plain values, so the rules that decide whether a cable may be plugged in --
the part a user actually argues with -- can be tested without a display,
and so the Qt layer (`gui/patch_canvas.py`) is left with nothing but
painting and gestures.

**This is a stand-in for the engine, not a second copy of it.** Issues
#203 (the graph and its refusal contract) and #204 (the Mix node and the
poly boundary) own these rules for real; neither exists yet, and #211 --
the canvas -- cannot wait for them to draw a refused cable. `judge()` is
therefore shaped exactly like the contract those tickets will expose: one
call, one `Verdict`, a sentence of plain English on refusal. When the
engine lands, `PatchGraph.judge` becomes a delegation rather than a
rewrite, and the canvas above it does not change at all.

The rules themselves are decision 56 §3 (per-note modules and once-only
modules are separated by the Mix node, and a cable crossing that boundary
the wrong way is refused rather than silently summed), §4 (a feedback loop
is legal only with a Delay in it) and §5 (sound goes into a socket;
modulation goes onto a knob).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

#: The two sides of the Mix boundary, plus the boundary itself.
SIDE_POLY = "poly"        # instantiated once per held note
SIDE_MONO = "mono"        # instantiated once
SIDE_BOUNDARY = "boundary"  # the Mix node; belongs to neither side

KIND_AUDIO = "audio"
KIND_MOD = "mod"


@dataclass(frozen=True)
class NodeSpec:
    """One patchable thing on the canvas: a module window, or the Mix node.

    `side` is the one field that makes a connection legal or refused
    (decision 56 §3). `out_kind` is #208's port type: a module either sends
    sound or sends knob movement, never both.

    No socket list: a module declares no fixed jacks at all. It shows as
    many as it has cables plus one spare (decision 57 §4) -- see
    `socket_counts()`.
    """

    node_id: str
    title: str
    side: str = SIDE_POLY
    can_in: bool = True
    can_out: bool = True
    out_kind: str = KIND_AUDIO
    is_delay: bool = False
    is_mix: bool = False


@dataclass
class Cable:
    """One patched connection. Either `dest` (sound into a socket) or
    `knob` (modulation onto a knob) is set, never both.

    `px/py/vx/vy` are the physics particle (decision 57 §2) and `in_loop`
    is recomputed every draw; they live on the cable rather than in a
    side table so nothing has to be kept in step with the cable list as
    cables come and go.
    """

    source: str
    dest: str | None = None
    knob: tuple[str, str] | None = None
    uid: int = 0
    #: Particle position/velocity in canvas coordinates; `None` until the
    #: cable has been laid out once.
    px: float | None = None
    py: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    in_loop: bool = False

    @property
    def kind(self):
        return KIND_MOD if self.knob is not None else KIND_AUDIO


@dataclass(frozen=True)
class Target:
    """Where a dragged cable is being dropped: an input socket, or a knob."""

    kind: str          # "socket" or "knob"
    node_id: str
    knob: str | None = None


@dataclass(frozen=True)
class Verdict:
    """The engine contract's answer (#203): yes, or no *and why*, in a
    sentence meant for the person holding the cable -- not an error code
    and not a log line. A refusal that cannot explain itself is the thing
    decision 56 set out to avoid."""

    ok: bool
    reason: str = ""


@dataclass
class CableAppearance:
    """Decision 57 §5: every appearance value in one place, so wiring the
    Settings window (#213) up to them is mechanical rather than a hunt
    through scattered literals. Nothing here is a design choice still open
    -- the defaults are the owner's, and the *values* of the first three
    are the prototype's, which decision 57 says to re-judge against the
    real Qt canvas.
    """

    #: How far a cable hangs below the straight line between its jacks.
    sag: float = 0.55
    #: 0 = critically stiff (no swing at all), 1 = loose and springy.
    swing: float = 0.70
    #: Opacity of a cable nobody is touching. Low enough to read knobs
    #: through (decision 57 §3).
    resting_brightness: float = 0.28
    #: "role" = colour by meaning (per-note sound / once-only sound / knob
    #: movement), the owner's default; "voice" = a different hue per cable.
    colour_scheme: str = "role"
    #: "cables" = the loop's cables turn Copper, "badge" = a label only,
    #: "off" = no marking.
    loop_marking: str = "cables"
    #: Where a refusal explains itself: "inline" (a callout by the jack),
    #: "status" (the status bar), or "both".
    explain: str = "inline"

    def replace(self, **changes):
        return replace(self, **changes)


#: Cable colours by role -- the "colour by meaning" scheme, which is the
#: shipped default (decision 57 §5). Three hues: per-note sound, once-only
#: sound, knob movement. Taken from `gui/theme.py`'s palette by name, not
#: by new hex values -- the Copper palette is unchanged (decision 57).
ROLE_COLOURS = {SIDE_POLY: "#5A8A9A", SIDE_MONO: "#F0A855", KIND_MOD: "#E65C4F"}

#: The by-cable scheme, for the setting's other position.
VOICE_COLOURS = ["#f3374b", "#ffb437", "#00b56e", "#3695ef", "#8b4ade"]

#: What a loop's cables turn when `loop_marking` is "cables".
LOOP_COLOUR = "#FF7034"

#: Constant downward pull on every cable's particle, per frame.
GRAVITY = 0.42

#: Below this speed (pixels per frame, both axes summed) a cable counts as
#: hung and still -- see `step_physics()` for why this is a speed and not a
#: distance.
SETTLED_SPEED = 0.05


class PatchGraph:
    """The nodes and cables of one patch, and the rules over them.

    Nothing in here knows about widgets or pixels. `judge()` is the whole
    point of the class; everything else exists to answer it.
    """

    def __init__(self):
        self._nodes: dict[str, NodeSpec] = {}
        self.cables: list[Cable] = []
        self._uid = 0

    # -- nodes ---------------------------------------------------------

    def add_node(self, spec: NodeSpec):
        self._nodes[spec.node_id] = spec
        return spec

    def remove_node(self, node_id):
        """Drops the node and every cable touching it -- unplugging by
        closing a module window, which is the only way a node disappears."""
        self._nodes.pop(node_id, None)
        self.cables = [c for c in self.cables
                       if c.source != node_id and c.dest != node_id
                       and not (c.knob and c.knob[0] == node_id)]

    def node(self, node_id) -> NodeSpec | None:
        return self._nodes.get(node_id)

    def nodes(self):
        return list(self._nodes.values())

    def title(self, node_id):
        spec = self._nodes.get(node_id)
        return spec.title if spec else node_id

    # -- cables --------------------------------------------------------

    def in_cables(self, node_id):
        return [c for c in self.cables if c.dest == node_id]

    def out_cables(self, node_id):
        return [c for c in self.cables if c.source == node_id]

    def knob_cables(self, node_id, knob_label):
        return [c for c in self.cables
                if c.knob is not None and c.knob == (node_id, knob_label)]

    def slot_of(self, cable, io):
        """Which jack a cable occupies: its index among that module's
        cables on that side. A cable's jack is therefore its own -- nothing
        sums invisibly at a shared input (decision 57 §4)."""
        listing = self.out_cables(cable.source) if io == "out" else self.in_cables(cable.dest)
        return listing.index(cable) if cable in listing else -1

    def socket_counts(self, node_id):
        """`(in_count, out_count)`: as many jacks as there are cables, plus
        one spare on each side the node accepts (decision 57 §4). The spare
        is what you plug the next cable into; it is drawn as a dashed empty
        hole by the canvas."""
        spec = self._nodes.get(node_id)
        if spec is None:
            return (0, 0)
        return (len(self.in_cables(node_id)) + 1 if spec.can_in else 0,
                len(self.out_cables(node_id)) + 1 if spec.can_out else 0)

    def connect(self, source_id, target: Target):
        """Plugs a cable in. Callers are expected to have asked `judge()`
        first -- this does not re-check, so a test can build an illegal
        patch on purpose."""
        self._uid += 1
        if target.kind == "knob":
            cable = Cable(source=source_id, knob=(target.node_id, target.knob), uid=self._uid)
        else:
            cable = Cable(source=source_id, dest=target.node_id, uid=self._uid)
        self.cables.append(cable)
        return cable

    def disconnect(self, cable):
        if cable in self.cables:
            self.cables.remove(cable)
            return True
        return False

    # -- reachability and loops (decision 56 §4) ------------------------

    def audio_path(self, start, goal, extra=None):
        """Depth-first search over sound cables only. Returns the list of
        node ids from `start` to `goal` inclusive, or `None`.

        `extra` is a cable that does not exist yet -- the one being judged
        -- so the same search answers both "is this patch looping?" and
        "would this cable close a loop?".
        """
        edges = [c for c in self.cables if c.dest]
        if extra is not None:
            edges = edges + [extra]
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
        """Marks every cable with whether it sits on a cycle and returns
        the set of nodes those cycles run through. Searched over the real
        cable set, never mocked -- that is what lets a refusal name the
        whole cycle back to the user."""
        in_loop = set()
        for cable in self.cables:
            if not cable.dest:
                cable.in_loop = False
                continue
            back = self.audio_path(cable.dest, cable.source)
            cable.in_loop = back is not None
            if back:
                in_loop.update(back)
        return in_loop

    # -- the contract --------------------------------------------------

    def cable_colour_role(self, cable):
        """Which of the three meanings a cable carries -- the key into
        `ROLE_COLOURS`."""
        if cable.kind == KIND_MOD:
            return KIND_MOD
        return self.node_colour_role(cable.source)

    def node_colour_role(self, node_id):
        """What sound leaving this node means: per-note, or once-only. The
        Mix node's output is once-only -- turning sixteen into one is the
        whole reason it is drawn."""
        spec = self._nodes.get(node_id)
        if spec is None:
            return SIDE_POLY
        return SIDE_MONO if (spec.side == SIDE_MONO or spec.is_mix) else SIDE_POLY

    def effective_side(self, node_id):
        """The Mix node is the boundary itself: its inputs are per-note
        (that is what it is summing) and its outputs are once-only."""
        spec = self._nodes.get(node_id)
        if spec is None:
            return SIDE_POLY
        return SIDE_POLY if spec.is_mix else spec.side

    def judge(self, source_id, target: Target) -> Verdict:
        """The accept/refuse-with-reason contract (#203), against decision
        56 §3/§4/§5. Every refusal says what is wrong *and* what to do
        instead; a bare "invalid connection" would be a bug in this
        method, not a terse style."""
        source = self._nodes.get(source_id)
        if source is None:
            return Verdict(False, "That module is no longer on the canvas.")
        source_is_mod = source.out_kind == KIND_MOD
        dest = self._nodes.get(target.node_id)
        if dest is None:
            return Verdict(False, "That module is no longer on the canvas.")

        if target.kind == "knob":
            if not source_is_mod:
                return Verdict(False, (
                    f"{source.title} sends sound, not knob movement. Sound goes "
                    f"into a square socket; only an LFO or an envelope can grab "
                    f"a knob."))
            if source.side == SIDE_POLY and self.effective_side(target.node_id) == SIDE_MONO:
                return Verdict(False, (
                    f"{source.title} runs once per held note, and {dest.title} "
                    f"runs once — there is no single value to send it."))
            if any(c.source == source_id
                   for c in self.knob_cables(target.node_id, target.knob)):
                return Verdict(False, f"{source.title} is already on that knob.")
            return Verdict(True)

        # A sound cable, into a socket.
        if source_is_mod:
            return Verdict(False, (
                f"{source.title} sends knob movement, not sound. Drop it on the "
                f"knob you want it to turn."))
        if not dest.can_in:
            return Verdict(False, f"{dest.title} has nothing to take sound in.")
        if target.node_id == source_id:
            return Verdict(False, (
                f"{source.title} cannot feed itself. A loop needs a Delay "
                f"module in it."))
        if any(c.source == source_id and c.dest == target.node_id for c in self.cables):
            return Verdict(False, f"{source.title} is already patched into {dest.title}.")

        if source.side == SIDE_POLY and self.effective_side(target.node_id) == SIDE_MONO:
            return Verdict(False, (
                f"{source.title} runs once per held note; {dest.title} runs "
                f"once. Send it through MIX first — that is what MIX is for."))

        proposed = Cable(source=source_id, dest=target.node_id)
        path = self.audio_path(target.node_id, source_id, extra=proposed)
        if path is not None and not any(self._is_delay(n) for n in path):
            named = " → ".join(self.title(n) for n in path) + f" → {dest.title}"
            return Verdict(False, (
                f"That closes a loop with no Delay in it ({named}). A feedback "
                f"loop needs a Delay module, so the sound comes back one block "
                f"later instead of instantly."))
        return Verdict(True)

    def _is_delay(self, node_id):
        spec = self._nodes.get(node_id)
        return bool(spec and spec.is_delay)

    # -- persistence ---------------------------------------------------

    def snapshot(self):
        """Cables as plain tuples, for the per-patch workspace state
        `synth_view` already keeps. Particle state is deliberately not
        saved: a restored cable settles into its hang on the first frame."""
        return [(c.source, c.dest, c.knob) for c in self.cables]

    def restore(self, entries):
        self.cables = []
        for source, dest, knob in entries:
            if source not in self._nodes:
                continue
            if knob is not None:
                if knob[0] not in self._nodes:
                    continue
                self.connect(source, Target("knob", knob[0], knob[1]))
            elif dest in self._nodes:
                self.connect(source, Target("socket", dest))


# -- cable physics (decision 57 §2) -------------------------------------
#
# One particle per cable, at its midpoint. Each frame it is pulled toward a
# rest point below the straight line between the two jacks, with a constant
# downward term and damping. Move a module and the ends jump while the
# middle lags, overshoots and settles -- so swing falls out of the ends
# moving and there is no separate animation to trigger.


def rest_sag(distance, appearance, is_mod=False):
    """How far below the chord a cable's rest point sits. Longer cables
    hang further; a modulation cable is a thinner, lighter thing and hangs
    about half as far."""
    return (0.25 + appearance.sag * 1.15) * (26 + distance * 0.16) * (0.45 if is_mod else 1.0)


def step_physics(cables, endpoints_for, appearance):
    """Advances every cable's particle one frame.

    `endpoints_for(cable)` returns `((ax, ay), (bx, by))` in canvas
    coordinates, or `None` for a cable whose jacks are not laid out yet.
    Returns whether anything is still moving, which is what lets the
    canvas's timer park itself when the patch has settled.
    """
    moving = False
    for cable in cables:
        ends = endpoints_for(cable)
        if ends is None:
            cable.px = None
            continue
        (ax, ay), (bx, by) = ends
        distance = math.hypot(bx - ax, by - ay)
        rest_x = (ax + bx) / 2
        rest_y = (ay + by) / 2 + rest_sag(distance, appearance, cable.kind == KIND_MOD)
        if cable.px is None:
            cable.px, cable.py, cable.vx, cable.vy = rest_x, rest_y, 0.0, 0.0

        stiffness = 0.42 - appearance.swing * 0.33
        damping = 0.62 + appearance.swing * 0.28
        cable.vx = (cable.vx + (rest_x - cable.px) * stiffness) * damping
        cable.vy = (cable.vy + (rest_y - cable.py) * stiffness + GRAVITY) * damping
        cable.px += cable.vx
        cable.py += cable.vy
        # Settled is judged on velocity alone, not on the distance left to
        # the rest point. A hanging cable's equilibrium is *below* its rest
        # point by exactly gravity/stiffness -- that gap never closes, so
        # the prototype's distance test read "still moving" forever and its
        # animation loop never parked. Harmless in a browser tab; a canvas
        # that repaints at 60fps for the life of the window is not harmless
        # on the small hardware this project targets.
        if abs(cable.vx) + abs(cable.vy) > SETTLED_SPEED:
            moving = True
    return moving


def control_point(cable, start, end):
    """The quadratic Bézier control point that makes the drawn curve pass
    through the particle. Bézier is the shape, settled (decision 57 §2):
    straight and orthogonal routing cannot express weight."""
    return (2 * cable.px - (start[0] + end[0]) / 2,
            2 * cable.py - (start[1] + end[1]) / 2)


def curve_point(start, control, end, t):
    """A point on the quadratic at parameter `t`. Used for hit-testing a
    cable under the cursor, which is how hover (and unplug) find it."""
    u = 1 - t
    return (u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
            u * u * start[1] + 2 * u * t * control[1] + t * t * end[1])


def distance_to_curve(start, control, end, point, samples=24):
    """Closest approach of the cursor to a drawn cable, sampled along the
    curve. Twenty-four samples is plenty at the widths a cable is drawn at
    and costs nothing next to the repaint it guards."""
    best = float("inf")
    for i in range(samples + 1):
        cx, cy = curve_point(start, control, end, i / samples)
        best = min(best, math.hypot(cx - point[0], cy - point[1]))
    return best
