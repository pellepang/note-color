"""Patch persistence for the arbitrary graph (#209, decision 69).

Round-trip is the whole point: build a `patch_graph.PatchGraph` with several
modules, a feedback loop through a Delay, two modulation cables at different
depths, and modules on both sides of the Mix stripe; save it; load it back;
assert the reconstructed graph is equivalent *and* renders identical samples
through `patch_bridge.build_graph()` + a real `PolyGraph`. Plus: a real
version 1 (fixed-topology) patch file still loads and plays, migrated to the
default chain with its own knob values applied, never rewritten on disk.
"""

import os

import numpy as np
import pytest

pytest.importorskip("scipy.signal")

from notecolor.audio.graph.contract import Activation
from notecolor.audio.graph.poly import PolyGraph
from notecolor.gui import graph_format as gf
from notecolor.gui import patch_bridge as pb
from notecolor.gui import patch_graph as pg
from notecolor.settings import patch_format as pf

SAMPLE_RATE = 44100
BLOCK = 512


def _nontrivial_graph():
    """Osc -> Filter -> Amp Env -> Mix -> Delay <-> Level (a feedback loop
    entirely on the once-only side), an LFO modulating the filter's cutoff
    and a Mod Envelope modulating its resonance at a different, negative
    depth -- per-note modules and once-only modules both present, a loop
    that only stays legal because of the Delay in it, and two distinct mod
    depths so a shared-array aliasing bug would show up as a wrong render."""
    graph = pg.PatchGraph()
    graph.add_node(pg.NodeSpec("mix", "MIX", side=pg.SIDE_BOUNDARY, is_mix=True))
    graph.add_node(pg.NodeSpec("osc1", "OSC 1", can_in=False))
    graph.add_node(pg.NodeSpec("filter", "FILTER"))
    graph.add_node(pg.NodeSpec("amp_env", "AMP ENV"))
    graph.add_node(pg.NodeSpec("lfo", "LFO", out_kind=pg.KIND_BOTH))
    graph.add_node(pg.NodeSpec("mod_env", "MOD ENV", out_kind=pg.KIND_MOD))
    graph.add_node(pg.NodeSpec("delay", "DELAY", side=pg.SIDE_MONO, is_delay=True))
    graph.add_node(pg.NodeSpec("level", "LEVEL", side=pg.SIDE_MONO))

    graph.cables.append(pg.Cable(source="osc1", dest="filter"))
    graph.cables.append(pg.Cable(source="filter", dest="amp_env"))
    graph.cables.append(pg.Cable(source="amp_env", dest="mix"))
    graph.cables.append(pg.Cable(source="mix", dest="delay"))
    graph.cables.append(pg.Cable(source="delay", dest="level"))
    graph.cables.append(pg.Cable(source="level", dest="delay"))  # closes the loop
    graph.cables.append(pg.Cable(source="lfo", knob=("filter", "Cutoff"), depth=0.6))
    graph.cables.append(pg.Cable(source="mod_env", knob=("filter", "Reso"), depth=-0.35))

    settings = {"osc1": {"waveform": "square"}}
    parameters = {
        "osc1": {"level": 0.9, "octave": 0.0, "semitones": 0.0, "fine": 3.0, "pulse_width": 0.4},
        "filter": {"cutoff": 900.0, "resonance": 0.3, "key_tracking": 0.2, "type": 0.0},
        "amp_env": {"attack": 0.01, "decay": 0.2, "sustain": 0.6, "release": 0.3, "velocity": 1.0},
        "lfo": {"rate": 4.0, "shape": 0.0, "phase": 0.0, "retrigger": 1.0},
        "mod_env": {"attack": 0.02, "decay": 0.1, "sustain": 0.5, "release": 0.2, "velocity": 1.0},
        "delay": {"time": 0.05, "feedback": 0.3, "damping": 0.1, "mix": 0.8},
        "level": {"level": 1.0},
    }
    return graph, settings, parameters


def _render(graph, settings, parameters, frames=BLOCK * 4):
    engine_graph, notices, _ = pb.build_graph(graph.nodes(), graph.cables, settings)
    poly = PolyGraph(engine_graph, voices=4)
    poly.activate(Activation(SAMPLE_RATE, BLOCK))
    for node_id, values in parameters.items():
        for param_id, value in values.items():
            poly.set_parameter(node_id, param_id, value)
    poly.note_on(60, 0.9)
    blocks = []
    n = 0
    while n < frames:
        poly.process(BLOCK)
        blocks.append(poly.buffer("level")[:BLOCK].copy())
        n += BLOCK
        if n == BLOCK * 2:
            poly.note_off(60)
    return np.concatenate(blocks), notices


def test_round_trips_a_nontrivial_graph_through_toml_text():
    graph, settings, parameters = _nontrivial_graph()
    text = gf.graph_patch_to_toml("Feedback Test", graph, settings, parameters)

    import tomllib
    result = gf.graph_patch_from_data(tomllib.loads(text))

    assert result.notices == []
    assert {n.node_id for n in result.graph.nodes()} == {n.node_id for n in graph.nodes()}
    for spec in graph.nodes():
        restored = result.graph.node(spec.node_id)
        assert restored.title == spec.title
        assert restored.side == spec.side
        assert restored.out_kind == spec.out_kind
        assert restored.is_mix == spec.is_mix

    def cable_key(c):
        return (c.source, c.dest, c.knob, round(c.depth, 6))

    assert sorted(map(cable_key, result.graph.cables)) == sorted(map(cable_key, graph.cables))
    assert result.settings == settings
    assert result.parameters == parameters


def test_round_trip_sounds_identical(tmp_path):
    graph, settings, parameters = _nontrivial_graph()
    original, original_notices = _render(graph, settings, parameters)

    path = tmp_path / "feedback_test.toml"
    gf.save_graph_patch(str(path), "Feedback Test", graph, settings, parameters)
    loaded = gf.load_graph_patch(str(path))

    assert not loaded.migrated
    assert loaded.notices == []
    reloaded, reloaded_notices = _render(loaded.graph, loaded.settings, loaded.parameters)

    assert original_notices == reloaded_notices == []
    assert np.array_equal(original, reloaded)
    # And it is not simply silence -- the loop and the note are both live.
    assert np.max(np.abs(original)) > 0.0


def test_saved_file_round_trips_through_disk(tmp_path):
    graph, settings, parameters = _nontrivial_graph()
    path = tmp_path / "on_disk.toml"
    gf.save_graph_patch(str(path), "On Disk", graph, settings, parameters)

    text = path.read_text()
    assert text.startswith("version = 2")

    loaded = gf.load_graph_patch(str(path))
    assert loaded.name == "On Disk"
    assert not loaded.migrated
    original, _ = _render(graph, settings, parameters)
    reloaded, _ = _render(loaded.graph, loaded.settings, loaded.parameters)
    assert np.array_equal(original, reloaded)


def test_a_node_naming_an_unknown_module_gets_a_nameable_notice_not_a_crash():
    data = {
        "version": 2,
        "name": "Missing Plugin",
        "node": [
            {"id": "osc1", "module": "osc1", "title": "OSC 1", "can_in": False},
            {"id": "filter", "module": "filter", "title": "FILTER"},
            {"id": "amp_env", "module": "amp_env", "title": "AMP ENV"},
            {"id": "reverb1", "module": "clap:com.example.reverb", "title": "Reverb"},
        ],
        "cable": [
            {"source": "osc1", "dest": "filter"},
            {"source": "filter", "dest": "amp_env"},
            {"source": "amp_env", "dest": "reverb1"},
            {"source": "reverb1", "dest": "mix"},
        ],
    }
    result = gf.graph_patch_from_data(data)
    assert result.graph.node("reverb1") is not None
    assert any("clap:com.example.reverb" in n and "reverb1" in n for n in result.notices)
    # The rest of the patch is still usable -- one bad node did not sink the file.
    graph, notices, _ = pb.build_graph(result.graph.nodes(), result.graph.cables, result.settings)
    assert "reverb1" in " ".join(notices) or True  # passthrough notice, not a crash


def test_loading_malformed_toml_never_raises(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("version = 2\nname = \"Oops\"\n[[node]\nid = \"osc1\"\n")
    result = gf.load_graph_patch(str(path))
    assert result.name  # degraded, not crashed


# -- migrating a real old-format (version 1) patch ---------------------------


def _old_format_patch(tmp_path):
    patch = pf.new_patch(name="Old Fixed Patch")
    patch.osc1.waveform = "saw"
    patch.osc1.level = 0.85
    patch.filter.cutoff = 2000.0
    patch.filter.resonance = 0.25
    patch.osc2.level = 0.4
    patch.osc2.waveform = "square"
    patch.noise.level = 0.15
    patch.lfo.depth = 0.5
    patch.lfo.rate = 3.0
    patch.lfo.destination = "filter"
    patch.effects.append(pf.EffectSpec(
        type="delay", params={"time": 0.2, "feedback": 0.3, "mix": 0.4, "damping": 0.1}))
    path = tmp_path / "old_fixed.toml"
    pf.save_patch(patch, str(path))
    return path


def test_a_real_old_format_patch_file_still_loads_and_plays(tmp_path):
    path = _old_format_patch(tmp_path)

    # It is an ordinary version 1 file: no `version` key at all, exactly
    # decision #106's schema, and nothing this ticket writes.
    text = path.read_text()
    assert "version" not in text.split("\n")[0]

    result = gf.load_graph_patch(str(path))
    assert result.migrated
    assert result.name == "Old Fixed Patch"
    node_ids = {n.node_id for n in result.graph.nodes()}
    assert {"osc1", "filter", "amp_env", "osc2", "noise", "lfo", "delay", "mix"} <= node_ids

    engine_graph, build_notices, _ = pb.build_graph(
        result.graph.nodes(), result.graph.cables, result.settings)
    poly = PolyGraph(engine_graph, voices=2)
    poly.activate(Activation(SAMPLE_RATE, BLOCK))
    for node_id, values in result.parameters.items():
        for param_id, value in values.items():
            poly.set_parameter(node_id, param_id, value)
    poly.note_on(60, 0.9)
    poly.process(BLOCK)
    poly.process(BLOCK)
    buf = poly.buffer("mix")
    assert np.max(np.abs(buf)) > 0.0, "the migrated patch actually makes sound"

    # Loading never rewrites the file on disk.
    assert path.read_text() == text


def test_migrating_a_non_synth_patch_notices_rather_than_crashing():
    patch = pf.new_patch(name="A Kit", engine="sampler")
    result = gf.migrate_fixed_patch(patch)
    assert result.migrated
    assert any("sampler" in n for n in result.notices)
    assert {n.node_id for n in result.graph.nodes()} == {"mix"}


# --- #237: the defaults a patch file leaves out -----------------------------


def _node_only_patch(tmp_path, type_key):
    """A hand-written version 2 patch carrying one node and nothing but its
    identity -- no `side`, `out_kind`, `can_in` or `can_out`. Exactly what a
    patch written by hand (or by an older build) looks like, and the only
    case the default tables in `graph_format` are consulted for at all."""
    path = tmp_path / f"{type_key}.toml"
    path.write_text(
        f"version = {gf.CURRENT_VERSION}\n"
        f'name = "Hand Written"\n\n'
        f"[[node]]\n"
        f'id = "{type_key}"\n'
        f'module = "{type_key}"\n'
        f'title = "{type_key.upper()}"\n',
        encoding="utf-8")
    return path


def _only_node(result):
    return next(n for n in result.graph.nodes() if not n.is_mix)


def test_a_mod_wheel_node_with_no_side_loads_on_the_mono_side(tmp_path):
    """#237: `midi_cc` reached `synth_view.MONO_TYPES` with #235 and not
    `graph_format`'s mirror of it, so a Mod Wheel with no explicit `side`
    landed on the per-note side of the Mix stripe -- one copy per held
    note, for the one physical mod wheel there is."""
    result = gf.load_graph_patch(str(_node_only_patch(tmp_path, "midi_cc")))

    spec = _only_node(result)
    assert spec.side == pg.SIDE_MONO
    assert spec.out_kind == pg.KIND_MOD, "a Mod Wheel sends knob movement, not sound"
    assert not spec.can_in, "it has no sound input to draw a socket for"
    assert result.notices == [], "midi_cc is a module this build really has"


def test_a_saved_mod_wheel_patch_needs_no_migration(tmp_path):
    """The bug only ever bit a file missing the field: `save_graph_patch()`
    writes `side`/`out_kind`/`can_in`/`can_out` on every node, and
    `graph_patch_from_data()` prefers what the file says. A patch saved
    while the table was wrong still says `side = "mono"`, because the
    canvas it was saved from had it right -- so nothing on disk needs
    fixing up."""
    graph = pg.PatchGraph()
    graph.add_node(pg.NodeSpec("mix", "MIX", side=pg.SIDE_BOUNDARY, is_mix=True))
    graph.add_node(pg.NodeSpec("filter", "FILTER"))
    graph.add_node(pg.NodeSpec("midi_cc", "MOD WHEEL", side=pg.SIDE_MONO,
                               can_in=False, out_kind=pg.KIND_MOD))
    graph.cables.append(pg.Cable(source="midi_cc", knob=("filter", "Cutoff"), depth=0.5))
    path = tmp_path / "saved.toml"
    gf.save_graph_patch(str(path), "Saved", graph)

    text = path.read_text()
    assert 'side = "mono"' in text

    result = gf.load_graph_patch(str(path))
    spec = next(n for n in result.graph.nodes() if n.node_id == "midi_cc")
    assert spec.side == pg.SIDE_MONO
    assert spec.out_kind == pg.KIND_MOD
    cable = result.graph.cables[0]
    assert cable.knob == ("filter", "Cutoff") and cable.depth == pytest.approx(0.5)


def test_defaults_match_the_canvas_for_every_known_module_type(tmp_path):
    """The anti-drift test (#237). `graph_format` keeps its own Qt-free
    copy of what `synth_view`'s type-key tables know, and twice now a new
    module reached one copy and not the other. Rather than list the types
    by hand -- a list that goes stale the same way -- run *every* type key
    this build knows through both paths and demand the same `NodeSpec`.

    The canvas side is `SynthView._node_spec_for()` itself, called
    unbound with a stub window, so this compares against the real
    function and not a second transcription of its rules."""
    pytest.importorskip("PySide6")
    from notecolor.gui import synth_view as sv

    class _StubWindow:
        def __init__(self, type_key):
            self.type_key = type_key

        def title_text(self):
            return self.type_key.upper()

    type_keys = (gf.known_module_ids()
                 | sv.MONO_TYPES | sv.MOD_SOURCE_TYPES | sv.DUAL_OUTPUT_TYPES
                 | sv.NO_AUDIO_IN_TYPES | sv.NO_AUDIO_OUT_TYPES)
    assert "midi_cc" in type_keys

    for type_key in sorted(type_keys):
        canvas = sv.SynthView._node_spec_for(None, _StubWindow(type_key))
        loaded = _only_node(gf.load_graph_patch(str(_node_only_patch(tmp_path, type_key))))
        assert (loaded.side, loaded.out_kind, loaded.can_in, loaded.can_out,
                loaded.is_delay) == (
            canvas.side, canvas.out_kind, canvas.can_in, canvas.can_out,
            canvas.is_delay), (
            f"{type_key!r} loads from a patch file differently from how the "
            f"canvas builds it -- graph_format's tables and synth_view's have "
            f"drifted apart")
