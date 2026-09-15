"""Patch persistence for the arbitrary graph (#209, decision 69), and
migration of the old fixed-topology format into it.

Two formats share one file kind and one directory
(`patch_format.patches_dir()`, same `*.toml` files the Synth View already
saves into), told apart by a `version` field the way `read_project_manifest()`
tells a version 1 manifest's `tracks` from a version 2's `parts` (CLAUDE.md's
glossary note, #150):

- **No `version` key at all** (every patch on disk before this ticket) is
  decision #106's fixed-topology schema -- `patch_format.Patch` -- exactly as
  `patch_format.py` already parses it. Nothing in that file changes: an old
  patch opened by the old TUI synth tool behaves exactly as it always has.
- **`version = 2`** is this file's own schema: `[[node]]` and `[[cable]]`
  array-tables under an arbitrary graph, described below.

`load_graph_patch()` is the one entry point the Synth View needs: given any
patch file, old or new, it returns a `GraphPatchResult` -- a `PatchGraph`
(nodes, cables, both sound and modulation) plus construction settings and
parameter values, ready to hand to `patch_bridge.build_graph()`. A version 1
file is migrated on the way through `migrate_fixed_patch()`; **the file on
disk is never rewritten by loading it** -- the GitHub issue's own words,
"silently rewriting the user's files on open is a surprise," are decisive
here, and there is a working precedent already: `read_project_manifest()`
accepts a version 1 manifest without ever turning it into a version 2 one
until something explicit asks it to. Saving a graph (`save_graph_patch()`)
is the explicit act that writes a version 2 file; nothing does that on its
own.

## What the format carries, precisely

One `[[node]]` per module instance: `id` (the canvas's own node id -- today
always equal to its type key, since the canvas allows only one instance per
type; kept as its own field rather than folding it into `module` for the day
that stops being true, decision 56 §2's "Osc 2 is an instance"), `module`
(the stable engine identity `contract.ModuleDescriptor.module_id` names --
a plugin's would be namespaced, `"clap:com.example.reverb"`), `title`, `side`
(`"poly"`/`"mono"` -- which side of the Mix stripe), `out_kind`
(`"audio"`/`"mod"`/`"both"`), `can_in`/`can_out`/`is_delay` (canvas-only
cosmetics -- what jacks to draw -- with no bearing on what the engine builds,
which asks the real module instead), a `[node.settings]` sub-table
(construction choices, an oscillator's waveform) and a `[node.parameters]`
sub-table (`{param_id: value}`, in the module's own plain units -- exactly
`contract.ParamBlock.snapshot()`'s shape, because that is what it was built
for: a caller with a live graph calls `snapshot()` on each node's module and
hands the result straight to `save_graph_patch()`; a caller loading a patch
gets the same shape back and can call `restore()` with it once the modules
exist).

One `[[cable]]` per connection: `source` always; then either `dest` (a sound
cable into a socket) or `mod_dest`/`mod_label`/`depth` (a modulation cable
onto a knob -- `depth` inline on the cable, not in a separate table, because
`patch_graph.Cable` already carries it as one field per cable with no
sharing between cables, so a side table would only add a level of
indirection the model itself does not have).

**The Mix node is never written and never read from the file.** It is not
patch content -- every graph has exactly one, always called `"mix"`, always
the boundary -- so it is a structural constant `graph_patch_from_data()`
synthesises before looking at a single `[[node]]` line, the same way
`patch_canvas.Canvas` always adds one regardless of what a workspace
restores. A cable can still name `"mix"` as its `dest`.

**What does *not* go in this file.** Two things a naive reading of the
GitHub issue ("module positions on the canvas") might expect here, that
this decision puts elsewhere on purpose -- see decision 69 for the reasoning
in full:

- **Window position** is workspace layout (#169's ticket, not this one) --
  where a window happens to sit the last time someone looked at the screen,
  not a fact about the sound. It has no field here.
- **Cable sag/swing/colour scheme and the rest of decision 57 §5's six
  appearance values** are settings (the eventual #213 Settings window),
  global to every patch, not per-patch data. No field here either.

## A missing module fails with a sentence, never a `KeyError`

Contract's own words: a patch referencing a plugin that is not installed
"fails with a nameable module rather than a key error." `known_module_ids()`
is every `module` id this build can actually construct; a node naming
anything else still gets a `NodeSpec` (so a cable touching it does not
vanish, and `patch_bridge.build_graph()`'s own existing Passthrough/dropped-
silently fallback still applies) plus an explicit notice naming the id, in
`GraphPatchResult.notices` -- read out loud by whatever calls this, the same
`notices()` convention `patch_bridge.py` already uses for "this knob does
nothing yet."
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from notecolor.audio import effects as effects_audio
from notecolor.gui import patch_bridge
from notecolor.gui import patch_graph as pg
from notecolor.settings import patch_format

#: This file's own schema version. Anything else on disk -- no `version`
#: key at all -- is decision #106's fixed-topology schema.
CURRENT_VERSION = 2

#: Mirrors `synth_view.MONO_TYPES` (effect types plus the utility types),
#: without importing `synth_view` itself -- this module stays Qt-free, the
#: same discipline `patch_graph.py` and `patch_bridge.py` already keep, so
#: it can be exercised headless. Both sides read from the same underlying
#: registries (`effects_audio.EFFECT_TYPES`, `patch_bridge`'s own
#: `MODULE_FACTORIES`/`NOT_IN_ENGINE`); if a module type's side ever became
#: a real per-instance choice instead of a fact about its type key, both
#: copies would need to learn that the same day.
_MONO_TYPE_KEYS = frozenset(effects_audio.EFFECT_TYPES) | frozenset({"level"})

#: The dual-output softening (decision 66 §5): today only the LFO carries
#: both a sound and a modulation jack.
_DUAL_OUTPUT_TYPE_KEYS = frozenset({"lfo"})

#: Type keys with no engine module at all yet, but that still send
#: modulation (decision 67's `mod_env`).
_MOD_ONLY_TYPE_KEYS = frozenset({"mod_env"})

#: Type keys with no sound input -- they generate rather than process.
_NO_SOUND_IN_TYPE_KEYS = frozenset({"osc1", "osc2", "noise"})

MIX_NODE_ID = "mix"


def known_module_ids():
    """Every `module` id this build can actually construct: our own
    modules (`patch_bridge.MODULE_FACTORIES`), the deliberately-inert-but-
    real canvas types (`patch_bridge.NOT_IN_ENGINE` -- `filter_env`,
    `voice`), and the effect types that become a `Passthrough` for want of
    a real module (`chorus`) -- all three are "known" in the sense this
    file cares about: a node naming one of them behaves exactly as the live
    canvas already behaves for it. Anything else is what
    `graph_patch_from_data()` reports as missing."""
    return (frozenset(patch_bridge.MODULE_FACTORIES)
            | patch_bridge.NOT_IN_ENGINE
            | frozenset(effects_audio.EFFECT_TYPES))


def _side_for(module_id):
    return pg.SIDE_MONO if module_id in _MONO_TYPE_KEYS else pg.SIDE_POLY


def _out_kind_for(module_id):
    if module_id in _DUAL_OUTPUT_TYPE_KEYS:
        return pg.KIND_BOTH
    if module_id in _MOD_ONLY_TYPE_KEYS:
        return pg.KIND_MOD
    return pg.KIND_AUDIO


def _can_in_for(module_id):
    return module_id not in _NO_SOUND_IN_TYPE_KEYS


# -- the in-memory result ----------------------------------------------------


@dataclass
class GraphPatchResult:
    """What loading any patch file (old or new) yields, ready for
    `patch_bridge.build_graph()`."""

    name: str
    graph: pg.PatchGraph
    #: `{node_id: {setting_name: value}}` -- construction choices.
    settings: dict = field(default_factory=dict)
    #: `{node_id: {param_id: value}}` -- `ParamBlock.snapshot()`'s shape.
    parameters: dict = field(default_factory=dict)
    #: Sentences worth telling the person who opened this file.
    notices: list = field(default_factory=list)
    #: True when this came from a version 1 (fixed-topology) file, migrated
    #: in memory -- the file on disk is untouched either way.
    migrated: bool = False


def _empty_result(name):
    graph = pg.PatchGraph()
    graph.add_node(pg.NodeSpec(MIX_NODE_ID, "MIX", side=pg.SIDE_BOUNDARY, is_mix=True))
    return GraphPatchResult(name=name, graph=graph)


# -- version 2: writing -------------------------------------------------------


def _dump_node_lines(spec, settings, parameters):
    lines = [
        "[[node]]",
        f"id = {patch_format._dump_value(spec.node_id)}",
        f"module = {patch_format._dump_value(spec.node_id)}",
        f"title = {patch_format._dump_value(spec.title)}",
        f"side = {patch_format._dump_value(spec.side)}",
        f"out_kind = {patch_format._dump_value(spec.out_kind)}",
        f"can_in = {patch_format._dump_value(spec.can_in)}",
        f"can_out = {patch_format._dump_value(spec.can_out)}",
        f"is_delay = {patch_format._dump_value(spec.is_delay)}",
        "",
    ]
    if settings:
        lines.append("[node.settings]")
        for key, value in settings.items():
            rendered = patch_format._dump_value(value)
            if rendered is not None:
                lines.append(f"{key} = {rendered}")
        lines.append("")
    if parameters:
        lines.append("[node.parameters]")
        for key, value in parameters.items():
            rendered = patch_format._dump_value(float(value))
            if rendered is not None:
                lines.append(f"{key} = {rendered}")
        lines.append("")
    return lines


def _dump_cable_lines(cable):
    lines = ["[[cable]]", f"source = {patch_format._dump_value(cable.source)}"]
    if cable.dest is not None:
        lines.append(f"dest = {patch_format._dump_value(cable.dest)}")
    else:
        dest_id, label = cable.knob
        lines.append(f"mod_dest = {patch_format._dump_value(dest_id)}")
        lines.append(f"mod_label = {patch_format._dump_value(label)}")
        lines.append(f"depth = {patch_format._dump_value(float(cable.depth))}")
    lines.append("")
    return lines


def graph_patch_to_toml(name, graph, settings=None, parameters=None):
    """The version 2 TOML text for one graph. Pure: no file I/O.

    `graph` is a `patch_graph.PatchGraph`; `settings`/`parameters` are
    `{node_id: {...}}`, the same shapes `GraphPatchResult` carries and
    `patch_bridge.PatchBridge` already keeps (`_settings`/`_parameters`) --
    a caller with a live graph passes those straight through, and one
    without a live graph (a test building a `PatchGraph` by hand) passes
    whatever it likes.
    """
    settings = settings or {}
    parameters = parameters or {}
    lines = [
        f"version = {CURRENT_VERSION}",
        f"name = {patch_format._dump_value(name)}",
        "",
    ]
    for spec in graph.nodes():
        if spec.is_mix:
            continue
        lines += _dump_node_lines(
            spec, settings.get(spec.node_id, {}), parameters.get(spec.node_id, {}))
    for cable in graph.cables:
        lines += _dump_cable_lines(cable)
    return "\n".join(lines).rstrip("\n") + "\n"


def save_graph_patch(path, name, graph, settings=None, parameters=None):
    """Write a version 2 patch file. Creates the directory if needed, the
    same as `patch_format.save_patch()`."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(graph_patch_to_toml(name, graph, settings, parameters))
    return path


# -- version 2: reading --------------------------------------------------


def _as_str(value, default=""):
    return value if isinstance(value, str) else default


def _as_bool(value, default):
    return value if isinstance(value, bool) else default


def _as_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def graph_patch_from_data(data, default_name="Untitled"):
    """Build a `GraphPatchResult` from an already-parsed version 2 mapping.
    Pure, and as forgiving as `patch_format.patch_from_toml()` is for the
    old format: a malformed or duplicate node/cable entry is skipped rather
    than raising, degrading towards "less patch" instead of "no patch."""
    notices = []
    result = _empty_result(_as_str(data.get("name"), default_name))
    graph = result.graph
    known = known_module_ids()
    seen_ids = {MIX_NODE_ID}

    raw_nodes = data.get("node")
    for raw in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(raw, dict):
            continue
        node_id = _as_str(raw.get("id"))
        if not node_id or node_id in seen_ids:
            continue
        module_id = _as_str(raw.get("module"), node_id)
        title = _as_str(raw.get("title"), node_id)
        side = raw.get("side")
        if side not in (pg.SIDE_POLY, pg.SIDE_MONO):
            side = _side_for(module_id)
        out_kind = raw.get("out_kind")
        if out_kind not in (pg.KIND_AUDIO, pg.KIND_MOD, pg.KIND_BOTH):
            out_kind = _out_kind_for(module_id)
        can_in = _as_bool(raw.get("can_in"), _can_in_for(module_id))
        can_out = _as_bool(raw.get("can_out"), True)
        is_delay = _as_bool(raw.get("is_delay"), module_id == "delay")

        graph.add_node(pg.NodeSpec(node_id, title, side=side, can_in=can_in,
                                    can_out=can_out, out_kind=out_kind, is_delay=is_delay))
        seen_ids.add(node_id)

        node_settings = raw.get("settings")
        if isinstance(node_settings, dict):
            result.settings[node_id] = dict(node_settings)
        node_params = raw.get("parameters")
        if isinstance(node_params, dict):
            result.parameters[node_id] = {
                k: float(v) for k, v in node_params.items() if _as_number(v)}

        if module_id not in known:
            notices.append(
                f"Patch node {node_id!r} wants module {module_id!r}, which this "
                f"build does not have; it will behave as if nothing were there.")

    raw_cables = data.get("cable")
    for raw in raw_cables if isinstance(raw_cables, list) else []:
        if not isinstance(raw, dict):
            continue
        source = _as_str(raw.get("source"))
        if not source or source not in seen_ids:
            continue
        dest = raw.get("dest")
        if isinstance(dest, str):
            if dest in seen_ids:
                graph.cables.append(pg.Cable(source=source, dest=dest))
            continue
        mod_dest = raw.get("mod_dest")
        mod_label = raw.get("mod_label")
        if isinstance(mod_dest, str) and mod_dest in seen_ids and isinstance(mod_label, str):
            depth = raw.get("depth", 1.0)
            depth = float(depth) if _as_number(depth) else 1.0
            graph.cables.append(pg.Cable(source=source, knob=(mod_dest, mod_label), depth=depth))

    result.notices = notices
    return result


def load_graph_patch(path):
    """Load any patch file -- version 2 or the old fixed-topology one --
    as a `GraphPatchResult`. Never raises: an unreadable path yields an
    empty graph named after the file, the same posture
    `patch_format.load_patch()` already has."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return _empty_result(patch_format.patch_name_for_path(path))

    data = patch_format.parse_patch_text(text)
    version = data.get("version")
    default_name = patch_format.patch_name_for_path(path)
    if _as_number(version) and int(version) >= CURRENT_VERSION:
        return graph_patch_from_data(data, default_name=default_name)

    old_patch = patch_format.patch_from_toml(data)
    if not old_patch.name:
        old_patch.name = default_name
    return migrate_fixed_patch(old_patch)


# -- migrating the old fixed-topology format ---------------------------------


def _numeric(obj, names):
    return {name: float(getattr(obj, name)) for name in names if hasattr(obj, name)}


def migrate_fixed_patch(patch):
    """What a version 1 (`patch_format.Patch`) patch *means* as a graph:
    the default chain -- Osc 1 -> Filter -> Amp Env -> Mix -- with that
    patch's own knob values applied, plus whichever optional stages the
    patch actually turned on. Never rewrites anything; the caller decides
    whether the result is ever saved back as a version 2 file (this
    ticket's own answer: only an explicit Save does that).

    A patch is a mechanical fact about the fixed engine's own signal path
    (`synth_engine.SynthVoice`), not a guess: Osc 1, Filter and Amp Env
    always ran, so they always become nodes. Osc 2 and Noise are real
    instances only when they were actually contributing (`level > 0`) --
    decision 56 §2's "a patch with no Osc 2 has no Osc 2" applied to what
    the old engine actually rendered, not to a struct field that always
    existed whether or not it did anything.
    """
    result = _empty_result(patch.name or "Untitled")
    result.migrated = True
    graph = result.graph
    notices = result.notices

    if patch.engine != "synth":
        notices.append(
            f"This is a {patch.engine!r} patch; the graph engine only plays "
            f"synth-style patches, so nothing was migrated.")
        return result

    def add(node_id, title, parameters=None, settings=None):
        graph.add_node(pg.NodeSpec(
            node_id, title, side=_side_for(node_id), can_in=_can_in_for(node_id),
            can_out=True, out_kind=_out_kind_for(node_id), is_delay=node_id == "delay"))
        if settings:
            result.settings[node_id] = settings
        if parameters:
            result.parameters[node_id] = parameters

    add("osc1", "OSC 1",
        parameters=_numeric(patch.osc1, ("octave", "semitones", "fine", "pulse_width", "level")),
        settings={"waveform": patch.osc1.waveform})
    add("filter", "FILTER", parameters={
        **_numeric(patch.filter, ("cutoff", "resonance", "key_tracking")),
        "type": patch_bridge.position_of("filter", "type", patch.filter.type),
    })
    add("amp_env", "AMP ENV", parameters={
        **_numeric(patch.amp_env, ("delay", "hold", "attack", "decay", "sustain", "release")),
        "velocity": patch.voice.velocity_to_amp,
    })
    graph.cables.append(pg.Cable(source="osc1", dest="filter"))
    graph.cables.append(pg.Cable(source="filter", dest="amp_env"))
    last = "amp_env"

    if patch.osc2.level > 0:
        add("osc2", "OSC 2",
            parameters=_numeric(patch.osc2, ("octave", "semitones", "fine", "pulse_width", "level")),
            settings={"waveform": patch.osc2.waveform})
        graph.cables.append(pg.Cable(source="osc2", dest="filter"))

    if patch.noise.level > 0:
        add("noise", "NOISE", parameters={
            "level": patch.noise.level,
            "colour": patch_bridge.position_of("noise", "colour", patch.noise.colour),
        })
        graph.cables.append(pg.Cable(source="noise", dest="filter"))

    if patch.lfo.depth > 0:
        add("lfo", "LFO", parameters={"rate": patch.lfo.rate})
        depth = max(0.0, min(1.0, patch.lfo.depth))
        if patch.lfo.destination == "filter":
            graph.cables.append(pg.Cable(source="lfo", knob=("filter", "Cutoff"), depth=depth))
        elif patch.lfo.destination == "pitch":
            graph.cables.append(pg.Cable(source="lfo", knob=("osc1", "Fine"), depth=depth))
            if "osc2" in {n.node_id for n in graph.nodes()}:
                graph.cables.append(pg.Cable(source="lfo", knob=("osc2", "Fine"), depth=depth))
        else:
            notices.append(
                "This patch's LFO targeted amp modulation, which the graph "
                "engine has no destination for yet; the LFO was migrated "
                "unconnected.")

    seen_effect_types = set()
    for effect in patch.effects:
        if not effect.type or effect.type in seen_effect_types:
            if effect.type in seen_effect_types:
                notices.append(
                    f"This patch's second {effect.type!r} effect has no way to "
                    f"become a second node yet (only one module of each type is "
                    f"on the canvas); it was dropped.")
            continue
        seen_effect_types.add(effect.type)
        parameters = {k: float(v) for k, v in effect.params.items() if _as_number(v)}
        add(effect.type, effect.type.title(), parameters=parameters)
        graph.cables.append(pg.Cable(source=last, dest=effect.type))
        last = effect.type

    graph.cables.append(pg.Cable(source=last, dest=MIX_NODE_ID))

    default_voice = patch_format.VoiceSettings()
    if patch.voice.glide != default_voice.glide \
            or patch.voice.velocity_to_filter != default_voice.velocity_to_filter \
            or patch.voice.volume != default_voice.volume \
            or patch.voice.polyphony != default_voice.polyphony:
        notices.append(
            "This patch's Voice settings (polyphony, glide, velocity-to-filter, "
            "master volume) have no equivalent in the graph engine yet and were "
            "not migrated.")

    return result
