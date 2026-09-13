"""The patch canvas's Qt half (ticket #211, decision 57): jacks on the
module windows, the cable overlay, the Mix stripe, and the connect gesture.

Same offscreen-Qt discipline as `test_synth_workspace.py`: no display, no
audio device, and direct calls into the gesture's own methods where
synthesizing real Qt drag input is impractical -- `QApplication.widgetAt`
answers about a screen there is none of, so what a drag lands on is
supplied directly and everything after that is the real code path.

The rules themselves live in `test_patch_graph.py`; this file is about the
widgets.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from notecolor.gui import patch_canvas as pc  # noqa: E402
from notecolor.gui import patch_graph as pg  # noqa: E402
from notecolor.gui import synth_workspace as sw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _module(type_key, title, knobs=("Cutoff", "Reso")):
    return sw.ModuleWindow(type_key, title,
                           knob_specs=[(label, "0.50", None) for label in knobs])


class _Fixture:
    """A canvas with a layer attached and a few modules on it, wired the
    way `synth_view` wires the real one."""

    def __init__(self):
        self.canvas = sw.Canvas(lambda _key, _pos: None)
        self.canvas.resize(900, 600)
        self.layer = pc.PatchLayer()
        self.layer.attach(self.canvas)
        self.canvas.windowAdded.connect(self._register)
        self.specs = {}
        self.statuses = []
        self.layer.statusChanged.connect(
            lambda text, bad: self.statuses.append((text, bad)))

    def _register(self, window):
        spec = self.specs[window.type_key]
        self.layer.add_node(spec, window)
        self.layer.register_knobs(
            window.type_key, [(k.label(), k) for k in window.knobs()])

    def open(self, spec, knobs=("Cutoff", "Reso"), at=(40, 40)):
        self.specs[spec.node_id] = spec
        window = _module(spec.node_id, spec.title, knobs)
        self.canvas.add_window(window)
        window.move(*at)
        self.layer.relayout()
        return window

    def sockets(self, node_id, io):
        return sorted((s for (n, i, _slot), s in self.layer._sockets.items()
                       if n == node_id and i == io), key=lambda s: s.slot)


@pytest.fixture
def fixture(app):
    return _Fixture()


def _poly(node_id, title, **kwargs):
    return pg.NodeSpec(node_id, title, **kwargs)


def _mono(node_id, title, **kwargs):
    return pg.NodeSpec(node_id, title, side=pg.SIDE_MONO, **kwargs)


# -- attachment ---------------------------------------------------------

def test_attaching_adds_the_mix_node_and_its_stripe(fixture):
    assert fixture.layer.graph.node(pc.MixStripe.NODE_ID).is_mix
    assert fixture.layer.stripe.width() == pc.STRIPE_WIDTH
    assert fixture.layer.stripe.height() == fixture.canvas.height()


def test_the_stripe_allowance_tidy_reserves_matches_the_stripe(app):
    """`synth_workspace` keeps its own copy of the number so it needs no
    cable vocabulary; the two must not drift."""
    assert sw.STRIPE_ALLOWANCE == pc.STRIPE_WIDTH


def test_a_canvas_with_no_layer_still_works(app):
    """The layer is an extension, not a rewrite: nothing in `Canvas`
    requires one."""
    canvas = sw.Canvas(lambda _key, _pos: None)
    canvas.resize(600, 400)
    canvas.add_window(_module("osc1", "OSC 1"))
    canvas.tidy()
    assert canvas.window_count() == 1


# -- sockets grow one at a time (decision 57 §4) ------------------------

def test_an_unpatched_module_shows_one_spare_jack_a_side(fixture):
    fixture.open(_poly("filter", "FILTER"))
    assert len(fixture.sockets("filter", "in")) == 1
    assert len(fixture.sockets("filter", "out")) == 1
    assert fixture.sockets("filter", "in")[0].spare


def test_plugging_in_grows_a_jack_and_leaves_a_new_spare(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.relayout()

    inputs = fixture.sockets("filter", "in")
    assert len(inputs) == 2
    assert not inputs[0].spare and inputs[1].spare


def test_a_module_with_nothing_to_take_in_shows_no_input_jacks(fixture):
    fixture.open(_poly("noise", "NOISE", can_in=False))
    assert fixture.sockets("noise", "in") == []


def test_a_modulation_source_gets_round_jacks(fixture):
    fixture.open(_poly("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD))
    assert fixture.sockets("lfo", "out")[0].kind == pg.KIND_MOD


def test_jacks_hang_off_the_edges_of_their_module(fixture):
    window = fixture.open(_poly("filter", "FILTER"), at=(200, 120))
    left = fixture.sockets("filter", "in")[0]
    right = fixture.sockets("filter", "out")[0]
    # Half outside the module on each side -- which is why they are
    # children of the canvas and not of the window: parented to the
    # window, Qt would clip the outer half away.
    half = pc.SOCKET_HOLE // 2
    assert left.centre_in(fixture.canvas).x() == window.x() - pc.SOCKET_OVERHANG + half
    assert right.centre_in(fixture.canvas).x() == \
        window.x() + window.width() + pc.SOCKET_OVERHANG - half
    assert left.centre_in(fixture.canvas).y() == window.y() + pc.SOCKET_FIRST_Y


def test_jacks_follow_their_module_when_it_moves(fixture):
    window = fixture.open(_poly("filter", "FILTER"), at=(100, 100))
    before = fixture.sockets("filter", "in")[0].centre_in(fixture.canvas)
    window.move(400, 260)
    fixture.layer.relayout()
    after = fixture.sockets("filter", "in")[0].centre_in(fixture.canvas)
    assert (after.x() - before.x(), after.y() - before.y()) == (300, 160)


def test_the_mix_stripes_jacks_sit_on_its_plate_not_at_its_top(fixture):
    socket = fixture.sockets(pc.MixStripe.NODE_ID, "in")[0]
    assert abs(socket.centre_in(fixture.canvas).y()
               - fixture.canvas.height() // 2) <= pc.SOCKET_PITCH


# -- the connect gesture ------------------------------------------------

def _drag(fixture, source_id, target, ok_target=True):
    """Runs the whole gesture: arm the source's spare out jack, then land
    on `target`. `QApplication.widgetAt` cannot answer offscreen, so the
    landing is supplied directly; everything else is the real path."""
    source = fixture.sockets(source_id, "out")[-1]
    fixture.layer._target_at = lambda _global: target if ok_target else None
    fixture.layer._on_socket_pressed(source)
    fixture.layer.drag_move(QtCore.QPoint(0, 0))
    fixture.layer.drag_release(QtCore.QPoint(0, 0))


def test_an_accepted_cable_is_patched_and_says_so(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    _drag(fixture, "osc1", pg.Target("socket", "filter"))

    assert [(c.source, c.dest) for c in fixture.layer.graph.cables] == [("osc1", "filter")]
    assert fixture.statuses[-1] == ("patched · OSC 1 → FILTER", False)


def test_a_refused_cable_is_not_patched_and_explains_itself(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_mono("chorus", "CHORUS"), at=(600, 40))
    _drag(fixture, "osc1", pg.Target("socket", "chorus"))

    assert fixture.layer.graph.cables == []
    assert fixture.layer._refusal is not None
    assert "MIX" in fixture.layer._refusal["text"]
    assert fixture.statuses[-1][1] is True


def test_a_refusal_flashes_the_jack_it_was_dropped_on(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_mono("chorus", "CHORUS"), at=(600, 40))
    _drag(fixture, "osc1", pg.Target("socket", "chorus"))
    assert fixture.sockets("chorus", "in")[-1].state == "flash"


def test_the_next_press_clears_a_standing_refusal(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_mono("chorus", "CHORUS"), at=(600, 40))
    _drag(fixture, "osc1", pg.Target("socket", "chorus"))
    fixture.layer.clear_refusal()
    assert fixture.layer._refusal is None
    assert fixture.sockets("chorus", "in")[-1].state == ""


def test_the_hovered_target_jack_shows_whether_it_will_take_the_cable(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.open(_mono("chorus", "CHORUS"), at=(600, 40))
    source = fixture.sockets("osc1", "out")[-1]
    fixture.layer._on_socket_pressed(source)

    fixture.layer._set_target(pg.Target("socket", "filter"))
    assert fixture.sockets("filter", "in")[-1].state == "ok"
    fixture.layer._set_target(pg.Target("socket", "chorus"))
    assert fixture.sockets("filter", "in")[-1].state == ""
    assert fixture.sockets("chorus", "in")[-1].state == "bad"


def test_the_armed_jack_is_released_when_the_drag_ends(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    source = fixture.sockets("osc1", "out")[-1]
    fixture.layer._on_socket_pressed(source)
    assert source.state == "armed"
    fixture.layer._target_at = lambda _global: None
    fixture.layer.drag_release(QtCore.QPoint(0, 0))
    assert source.state == ""
    assert fixture.layer._drag is None


def test_modulation_lands_on_a_knob(fixture):
    fixture.open(_poly("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    _drag(fixture, "lfo", pg.Target("knob", "filter", "Cutoff"))

    cables = fixture.layer.graph.cables
    assert [c.knob for c in cables] == [("filter", "Cutoff")]


# -- unplugging ---------------------------------------------------------

def test_clicking_a_cable_pulls_it_out(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    cable = fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.relayout()
    fixture.layer._tick()

    fixture.layer.unplug(cable)
    assert fixture.layer.graph.cables == []
    assert fixture.statuses[-1] == ("unplugged · OSC 1 → FILTER", False)
    assert len(fixture.sockets("filter", "in")) == 1


def test_a_click_on_a_knob_never_unplugs_the_cable_crossing_over_it(fixture):
    """Cables are drawn above the modules, so they cross knobs and title
    bars constantly -- turning the knob has to keep winning."""
    window = fixture.open(_poly("filter", "FILTER"), at=(100, 100))
    knob = window.knobs()[0]
    event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(5, 5),
                              QtCore.QPointF(5, 5), QtCore.Qt.LeftButton,
                              QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    assert fixture.layer._try_unplug(knob, event) is False


def test_closing_a_module_takes_its_cables_and_jacks_with_it(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    window = fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.relayout()

    window.request_close()
    assert fixture.layer.graph.cables == []
    assert fixture.sockets("filter", "in") == []
    assert fixture.layer.graph.node("filter") is None


# -- what is lit (decision 57 §3) ---------------------------------------

def test_a_cable_is_dim_until_something_touches_it(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    cable = fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    assert fixture.layer._is_lit(cable) is False


def test_selecting_a_module_lights_its_cables(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    cable = fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.set_focused_node("filter")
    assert fixture.layer._is_lit(cable) is True


def test_hovering_a_jack_lights_the_cable_in_it(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    cable = fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.relayout()
    fixture.layer._on_socket_hovered(fixture.sockets("osc1", "out")[0], True)
    assert fixture.layer._is_lit(cable) is True


def test_turning_a_knob_lights_the_modulation_cable_feeding_it(fixture):
    fixture.open(_poly("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD))
    window = fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    cable = fixture.layer.graph.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    assert fixture.layer._is_lit(cable) is False

    window.knobs()[0].turning.emit(True)
    assert fixture.layer._is_lit(cable) is True
    window.knobs()[0].turning.emit(False)
    # Reaching for a knob also selects its module, and a selected module's
    # cables stay lit -- so the fade-back is only visible once the
    # selection moves on.
    fixture.layer.set_focused_node(None)
    assert fixture.layer._is_lit(cable) is False


def test_a_wheel_step_lights_the_cable_too_then_lets_it_fade(fixture):
    """A wheel step has no "let go" to end the highlight, so it is held
    for a beat instead."""
    fixture.open(_poly("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD))
    window = fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    cable = fixture.layer.graph.connect("lfo", pg.Target("knob", "filter", "Cutoff"))

    window.knobs()[0].wheelStepped.emit(1)
    assert fixture.layer._is_lit(cable) is True
    fixture.layer._end_wheel_turn()
    assert fixture.layer._is_lit(cable) is False


# -- colour, pips, and the loop marking ---------------------------------

def test_jacks_wear_the_colour_of_the_cable_in_them(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.relayout()
    fixture.layer._tick()

    assert fixture.sockets("osc1", "out")[0].pip_colour == pg.ROLE_COLOURS[pg.SIDE_POLY]
    assert fixture.sockets("filter", "in")[1].pip_colour is None   # the spare


def test_a_looping_cable_is_marked_and_the_setting_can_turn_it_off(fixture):
    fixture.open(_mono("delay", "DELAY", is_delay=True))
    fixture.open(_mono("chorus", "CHORUS"), at=(400, 40))
    forward = fixture.layer.graph.connect("delay", pg.Target("socket", "chorus"))
    fixture.layer.graph.connect("chorus", pg.Target("socket", "delay"))
    fixture.layer.graph.loop_members()

    assert fixture.layer._cable_colour(forward, 0) == pg.LOOP_COLOUR
    fixture.layer.appearance = fixture.layer.appearance.replace(loop_marking="off")
    assert fixture.layer._cable_colour(forward, 0) == pg.ROLE_COLOURS[pg.SIDE_MONO]


# -- the frame loop -----------------------------------------------------

def test_the_animation_parks_itself_once_the_cables_have_settled(fixture):
    """A canvas that repaints forever is not acceptable on the small
    hardware this project targets."""
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    fixture.layer.relayout()
    for _ in range(500):
        fixture.layer._tick()
    assert fixture.layer._timer.isActive() is False

    fixture.layer.wake()
    assert fixture.layer._timer.isActive() is True


def test_the_overlay_paints_a_full_patch_without_falling_over(fixture):
    """A smoke test over every painted branch at once: sound cables, a
    modulation cable and its ring, a loop's badge, a hover label, a
    refusal callout and a drag in flight."""
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD), at=(40, 300))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.open(_mono("delay", "DELAY", is_delay=True), at=(600, 40))
    fixture.open(_mono("chorus", "CHORUS"), at=(600, 300))
    graph = fixture.layer.graph
    graph.connect("osc1", pg.Target("socket", "filter"))
    graph.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    graph.connect("delay", pg.Target("socket", "chorus"))
    graph.connect("chorus", pg.Target("socket", "delay"))
    fixture.layer.relayout()
    for _ in range(30):
        fixture.layer._tick()

    fixture.layer._hover_cable = graph.cables[0]
    fixture.layer.refuse(pg.Target("socket", "chorus"), "a reason, at length " * 6)
    fixture.layer._drag = {"node": "osc1", "kind": pg.KIND_AUDIO, "target": None,
                           "ok": False, "start": (10.0, 10.0), "point": (200.0, 200.0)}

    pixmap = QtGui.QPixmap(fixture.canvas.size())
    pixmap.fill(QtCore.Qt.transparent)
    fixture.layer.overlay.render(pixmap)
    assert not pixmap.isNull()


# -- tidy keeps modules on their own side of the boundary ---------------

def test_tidy_packs_each_side_into_its_own_half(fixture):
    fixture.canvas.partition = lambda w: (
        pg.SIDE_MONO if w.type_key in ("delay", "chorus") else pg.SIDE_POLY)
    fixture.canvas.boundary = fixture.layer.stripe_x
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"))
    fixture.open(_mono("delay", "DELAY", is_delay=True))
    fixture.open(_mono("chorus", "CHORUS"))
    fixture.canvas.tidy()

    stripe_left = fixture.layer.stripe_x()
    stripe_right = stripe_left + pc.STRIPE_WIDTH
    for window in fixture.canvas.windows():
        if window.type_key in ("delay", "chorus"):
            assert window.x() >= stripe_right
        else:
            assert window.x() + window.width() <= stripe_left


# -- persistence --------------------------------------------------------

def test_cables_survive_a_snapshot_and_restore(fixture):
    fixture.open(_poly("osc1", "OSC 1"))
    fixture.open(_poly("filter", "FILTER"), at=(300, 40))
    fixture.layer.graph.connect("osc1", pg.Target("socket", "filter"))
    saved = fixture.layer.snapshot()

    fixture.layer.graph.cables = []
    fixture.layer.restore(saved)
    assert [(c.source, c.dest) for c in fixture.layer.graph.cables] == [("osc1", "filter")]
    assert len(fixture.sockets("filter", "in")) == 2
