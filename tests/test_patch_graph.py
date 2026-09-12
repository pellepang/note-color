"""The patch canvas's model half (ticket #211, decision 57): sockets that
grow one at a time, cables with weight, and the accept/refuse-with-reason
contract standing in for #203/#204 until those land.

No Qt at all -- that is the point of `gui/patch_graph.py` being Qt-free.
The rules a user argues with are testable without a display.
"""

import math

import pytest

from notecolor.gui import patch_graph as pg


def _graph():
    """A small patch shaped like the real one: two per-note sources into a
    filter, a modulation source, the Mix boundary, and two once-only
    effects with a Delay among them."""
    graph = pg.PatchGraph()
    graph.add_node(pg.NodeSpec("osc1", "OSC 1"))
    graph.add_node(pg.NodeSpec("osc2", "OSC 2"))
    graph.add_node(pg.NodeSpec("filter", "FILTER"))
    graph.add_node(pg.NodeSpec("filter_env", "FILTER ENV", out_kind=pg.KIND_MOD))
    graph.add_node(pg.NodeSpec("mix", "MIX", side=pg.SIDE_BOUNDARY, is_mix=True))
    graph.add_node(pg.NodeSpec("delay", "DELAY", side=pg.SIDE_MONO, is_delay=True))
    graph.add_node(pg.NodeSpec("chorus", "CHORUS", side=pg.SIDE_MONO))
    return graph


def _socket(node_id):
    return pg.Target("socket", node_id)


def _knob(node_id, label):
    return pg.Target("knob", node_id, label)


# -- sockets grow one at a time (decision 57 §4) -------------------------

def test_unpatched_module_shows_one_spare_jack_per_side():
    graph = _graph()
    assert graph.socket_counts("filter") == (1, 1)


def test_each_cable_brings_its_own_jack_plus_the_spare():
    graph = _graph()
    graph.connect("osc1", _socket("filter"))
    graph.connect("osc2", _socket("filter"))
    assert graph.socket_counts("filter") == (3, 1)
    assert graph.socket_counts("osc1") == (1, 2)


def test_unplugging_compacts_the_row_again():
    graph = _graph()
    first = graph.connect("osc1", _socket("filter"))
    second = graph.connect("osc2", _socket("filter"))
    graph.disconnect(first)
    assert graph.socket_counts("filter") == (2, 1)
    # The survivor slides down into the freed jack rather than leaving a
    # hole where the unplugged one was.
    assert graph.slot_of(second, "in") == 0


def test_a_source_that_takes_no_input_shows_no_input_jacks():
    graph = _graph()
    graph.add_node(pg.NodeSpec("noise", "NOISE", can_in=False))
    assert graph.socket_counts("noise") == (0, 1)


# -- the refusal contract: sound vs knob movement (decision 56 §5) -------

def test_sound_into_a_knob_is_refused_and_says_where_sound_goes():
    verdict = _graph().judge("osc1", _knob("filter", "Cutoff"))
    assert not verdict.ok
    assert "sends sound, not knob movement" in verdict.reason


def test_modulation_into_a_socket_is_refused_and_says_to_use_a_knob():
    verdict = _graph().judge("filter_env", _socket("filter"))
    assert not verdict.ok
    assert "Drop it on the knob" in verdict.reason


def test_modulation_onto_a_knob_is_accepted():
    assert _graph().judge("filter_env", _knob("filter", "Cutoff")).ok


def test_the_same_source_twice_on_one_knob_is_refused():
    graph = _graph()
    graph.connect("filter_env", _knob("filter", "Cutoff"))
    verdict = graph.judge("filter_env", _knob("filter", "Cutoff"))
    assert not verdict.ok
    assert "already on that knob" in verdict.reason


# -- the poly boundary (decision 56 §3) ---------------------------------

def test_per_note_into_once_only_is_refused_and_points_at_mix():
    verdict = _graph().judge("osc1", _socket("chorus"))
    assert not verdict.ok
    assert "MIX" in verdict.reason


def test_per_note_into_mix_is_accepted():
    assert _graph().judge("osc1", _socket("mix")).ok


def test_mix_output_into_a_once_only_module_is_accepted():
    assert _graph().judge("mix", _socket("delay")).ok


def test_per_note_modulation_onto_a_once_only_knob_is_refused():
    verdict = _graph().judge("filter_env", _knob("chorus", "Mix"))
    assert not verdict.ok
    assert "no single value" in verdict.reason


# -- loops need a Delay in them (decision 56 §4) ------------------------

def test_a_module_cannot_feed_itself():
    verdict = _graph().judge("filter", _socket("filter"))
    assert not verdict.ok
    assert "Delay" in verdict.reason


def test_a_loop_with_no_delay_is_refused_and_names_the_whole_cycle():
    graph = _graph()
    graph.add_node(pg.NodeSpec("second", "SECOND", side=pg.SIDE_MONO))
    graph.connect("mix", _socket("chorus"))
    graph.connect("chorus", _socket("second"))
    verdict = graph.judge("second", _socket("chorus"))
    assert not verdict.ok
    assert "CHORUS → SECOND → CHORUS" in verdict.reason


def test_a_loop_through_a_delay_is_allowed():
    graph = _graph()
    graph.connect("mix", _socket("delay"))
    graph.connect("delay", _socket("chorus"))
    assert graph.judge("chorus", _socket("delay")).ok


def test_loop_members_marks_the_cables_on_the_cycle():
    graph = _graph()
    graph.connect("mix", _socket("delay"))
    forward = graph.connect("delay", _socket("chorus"))
    back = graph.connect("chorus", _socket("delay"))
    members = graph.loop_members()
    assert members == {"delay", "chorus"}
    assert forward.in_loop and back.in_loop


def test_a_duplicate_cable_is_refused():
    graph = _graph()
    graph.connect("osc1", _socket("filter"))
    verdict = graph.judge("osc1", _socket("filter"))
    assert not verdict.ok
    assert "already patched" in verdict.reason


def test_closing_a_module_takes_its_cables_with_it():
    graph = _graph()
    graph.connect("osc1", _socket("filter"))
    graph.connect("filter_env", _knob("filter", "Cutoff"))
    graph.connect("osc2", _socket("mix"))
    graph.remove_node("filter")
    assert [c.source for c in graph.cables] == ["osc2"]


# -- cable physics (decision 57 §2) -------------------------------------

def _flat_ends(_cable):
    return ((0.0, 100.0), (200.0, 100.0))


def test_a_cable_hangs_below_the_line_between_its_jacks():
    appearance = pg.CableAppearance()
    cable = pg.Cable("a", "b")
    pg.step_physics([cable], _flat_ends, appearance)
    assert cable.py > 100.0


def test_more_sag_hangs_further():
    slack = pg.rest_sag(200, pg.CableAppearance(sag=0.9))
    tight = pg.rest_sag(200, pg.CableAppearance(sag=0.1))
    assert slack > tight


def test_a_modulation_cable_hangs_less_than_a_sound_cable():
    appearance = pg.CableAppearance()
    assert pg.rest_sag(200, appearance, is_mod=True) < pg.rest_sag(200, appearance)


def test_a_settled_cable_reports_nothing_moving_so_the_timer_can_park():
    appearance = pg.CableAppearance()
    cable = pg.Cable("a", "b")
    for _ in range(400):
        moving = pg.step_physics([cable], _flat_ends, appearance)
    assert moving is False


def test_moving_a_jack_makes_the_middle_lag_then_catch_up():
    """The whole point of the particle: the ends jump, the middle does
    not (decision 57 §2)."""
    appearance = pg.CableAppearance()
    cable = pg.Cable("a", "b")
    for _ in range(400):
        pg.step_physics([cable], _flat_ends, appearance)
    settled_x = cable.px

    moved = lambda _c: ((400.0, 100.0), (600.0, 100.0))  # noqa: E731
    pg.step_physics([cable], moved, appearance)
    assert cable.px - settled_x < 200.0          # lagging behind the jump
    for _ in range(400):
        pg.step_physics([cable], moved, appearance)
    assert cable.px == pytest.approx(500.0, abs=2.0)   # and it gets there


def test_a_cable_whose_jacks_are_not_laid_out_yet_is_left_undrawn():
    cable = pg.Cable("a", "b")
    pg.step_physics([cable], lambda _c: None, pg.CableAppearance())
    assert cable.px is None


def test_the_drawn_curve_passes_through_the_particle():
    appearance = pg.CableAppearance()
    cable = pg.Cable("a", "b")
    for _ in range(400):
        pg.step_physics([cable], _flat_ends, appearance)
    start, end = _flat_ends(cable)
    control = pg.control_point(cable, start, end)
    midpoint = pg.curve_point(start, control, end, 0.5)
    assert midpoint[0] == pytest.approx(cable.px, abs=0.01)
    assert midpoint[1] == pytest.approx(cable.py, abs=0.01)


def test_hit_testing_finds_the_cursor_on_a_cable_and_not_beside_it():
    cable = pg.Cable("a", "b")
    appearance = pg.CableAppearance()
    for _ in range(400):
        pg.step_physics([cable], _flat_ends, appearance)
    start, end = _flat_ends(cable)
    control = pg.control_point(cable, start, end)
    on_it = pg.distance_to_curve(start, control, end, (cable.px, cable.py))
    beside = pg.distance_to_curve(start, control, end, (cable.px, cable.py + 60))
    assert on_it < 1.0
    assert beside > 40.0


# -- colour by meaning, the shipped default (decision 57 §5) ------------

def test_cables_are_coloured_by_what_they_mean():
    graph = _graph()
    per_note = graph.connect("osc1", _socket("mix"))
    once_only = graph.connect("mix", _socket("delay"))
    modulation = graph.connect("filter_env", _knob("filter", "Cutoff"))
    assert graph.cable_colour_role(per_note) == pg.SIDE_POLY
    assert graph.cable_colour_role(once_only) == pg.SIDE_MONO
    assert graph.cable_colour_role(modulation) == pg.KIND_MOD


def test_every_appearance_value_lives_in_one_place():
    """Decision 57 §5: six named settings, so #213 is wiring rather than a
    hunt through scattered literals."""
    appearance = pg.CableAppearance()
    assert (appearance.sag, appearance.swing, appearance.resting_brightness) == (0.55, 0.70, 0.28)
    assert appearance.colour_scheme == "role"
    assert appearance.loop_marking == "cables"
    assert appearance.explain == "inline"
    assert appearance.replace(sag=0.1).sag == 0.1


# -- snapshot/restore, for the per-patch workspace state ----------------

def test_cables_survive_a_snapshot_restore_round_trip():
    graph = _graph()
    graph.connect("osc1", _socket("filter"))
    graph.connect("filter_env", _knob("filter", "Cutoff"))
    saved = graph.snapshot()

    restored = _graph()
    restored.restore(saved)
    assert restored.snapshot() == saved


def test_restore_drops_cables_whose_modules_are_gone():
    graph = _graph()
    graph.connect("osc1", _socket("filter"))
    saved = graph.snapshot()

    smaller = pg.PatchGraph()
    smaller.add_node(pg.NodeSpec("osc1", "OSC 1"))
    smaller.restore(saved)
    assert smaller.cables == []
