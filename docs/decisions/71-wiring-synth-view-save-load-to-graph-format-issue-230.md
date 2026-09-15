# 71. Wiring Synth View's Save/Load to `graph_format` (issue #230)

Decision 69/#209 built `save_graph_patch()`/`load_graph_patch()` and
deliberately stopped there: "Scope was deliberately capped at the format and
its migration, not the Synth View's Save/Load menu wiring." That gap is
issue #230, and this is it closed. Done when Save writes the canvas's real
graph and Load accepts both versions, without inventing a second notice
mechanism or reopening #174's own scope.

## 1. Save writes the live graph, gated on `engine == "synth"`

`_open_save_dialog()` used to call `patch_format.save_patch(self.
current_patch, path)` unconditionally -- the old fixed-topology object, never
the canvas. It now branches on `current_patch.engine`:

- **`"synth"`**: `graph_format.save_graph_patch(path, name, self.patch_layer.
  graph, settings=self._graph_settings(), parameters=self.
  _graph_snapshot_parameters())`. `patch_layer.graph` is the same
  `PatchGraph` the bridge already rebuilds from on every edit -- there is
  nothing else in the view that would be more "real."
- **`"sampler"`/`"sf2"`**: unchanged, `patch_format.save_patch()`. The graph
  format has no field for a zone list or an SF2 bank/preset at all
  (decision 69 §2 never claimed to carry them) -- writing one for a kit
  would not degrade gracefully, it would just lose the kit.

This is a real behaviour fork, not a temporary shim: a kit's Save was never
this ticket's to redesign, and the graph format staying synth-only is the
same boundary decision 69 already drew for window position (#169) and cable
appearance (#213) -- named here rather than guessed at by whoever hits it
next.

### `_graph_snapshot_parameters()`

`_graph_parameters()` already exists (`_rebuild_graph()`'s own reader) and
returns every knob on screen as `{node_id: {param_id: value}}` -- but a
choice knob (`filter.type`) is a display name there (`"lp"`), not the
`ParamBlock.snapshot()` float decision 69 §6 says the format wants. The one
line of difference is `patch_bridge.position_of()`, already built for
exactly this conversion and already exercised by every knob turn
(`PatchBridge.set_parameter()` runs the same call before writing to a live
`ParamBlock`). A name `position_of()` does not recognise (there is no such
case today, but a future construction-only key would produce one) is
dropped rather than written as `None` -- the same "less patch, never a
crash" posture the format itself keeps.

Values are read from the view's own `Patch`/`effects`/`_utility_params`
state, not from `PatchBridge.poly`'s live `ParamBlock`s. `PatchBridge.
rebuild()` only calls `_apply_parameters()` after a successful `poly.
activate()`, which needs a real `SoundEngine` -- on a machine with no audio
device (or the test harness's stub, which deliberately doesn't carry
`set_graph`/`block_size`) `self.poly` exists but sits at construction
defaults, disagreeing with whatever the knobs on screen actually show.
Reading the view's own state is what makes Save correct with no sound card
attached at all, matching this project's "must run across a range of
hardware" constraint and `graph_format.py`'s own audio-free test discipline.

## 2. Load: version 2 direct, version 1 migrated, non-synth still the old path

`_load_patch_file(path)` replaces `_open_load_dialog()`'s inline
`self._apply_patch(patch_format.load_patch(path))`. It always calls
`graph_format.load_graph_patch(path)` first, then branches on `result.
migrated`:

- **Not migrated** (a version 2 file): `patch_format.new_patch(name=result.
  name, engine="synth")` stands in for `current_patch` -- there is no old
  `Patch` to recover, and there does not need to be one; a version 2 file is
  definitionally a synth patch.
- **Migrated, `engine == "synth"`**: the real old `Patch` (`patch_format.
  load_patch(path)`, read a second time -- the file is small TOML, and this
  keeps `graph_format.py`'s own contract untouched rather than growing
  `GraphPatchResult` a field nothing else needs) is kept as `current_patch`,
  and `_apply_graph_result()` rebuilds the canvas from the migrated graph.
- **Migrated, `engine != "synth"`**: `migrate_fixed_patch()`'s own answer is
  an empty graph plus a notice ("nothing was migrated") -- there is nothing
  for `_apply_graph_result()` to build from. Load falls back to the
  unchanged `_apply_patch(patch)`, the same path a sampler kit or an SF2
  program has always gone through. This is the one branch it would have
  been wrong to force through the graph path: an empty canvas would be a
  strictly worse experience than the three meaningless-but-harmless default
  modules the old path already opens, for no gain.

**Loading never rewrites the file** -- decision 69's own requirement,
carried through unchanged: nothing in `_load_patch_file()`/
`_apply_graph_result()` ever calls `save_graph_patch()`. A version 1 file
stays a version 1 file until an explicit Save.

### `_apply_graph_result()`: reconstructing the canvas from a graph

Every non-Mix node in `result.graph` is classified the same way `graph_
format.known_module_ids()` already classifies it, into whichever of the
view's three existing backing stores drives that node's knob panel:

- **An effect type** (`effects_audio.EFFECT_TYPES`): a fresh `EffectSpec`
  appended to `patch.effects`, `params` set straight from `result.
  parameters[node_id]` -- every effect parameter is already a plain float or
  int (`EFFECT_PARAM_SPECS` has no choice knob), so no reverse mapping is
  needed here.
- **A graph-only utility type** (`GRAPH_ONLY_TYPES`: `level`, `lfo`,
  `mod_env`): `self._utility_params[node_id]`, seeded from `UTILITY_
  DEFAULTS` and overlaid with the loaded values -- a value the file did not
  carry keeps its module's own default rather than reading as zero.
- **Everything else** (`osc1`, `osc2`, `noise`, `filter`, `amp_env`,
  `filter_env`, `voice`): `setattr()` onto the matching `Patch` section,
  guarded by `hasattr()` so a parameter id with nowhere to land on the old
  typed model is silently skipped rather than raising (see §3 below for the
  one real case of this).

A choice-typed knob (`filter.type`, `noise.colour`, an LFO's `shape`/
`retrigger`) needs the reverse of `position_of()`: the file holds a float
position, the `Patch` attribute wants the name back. `patch_bridge.
choice_name_of()` is that reverse, added beside `position_of()` rather than
folded into `_apply_graph_result()` itself -- the engine vocabulary
(`CHOICE_OPTIONS`) belongs with the rest of the canvas/engine translation
table, the same reasoning `patch_bridge.py`'s own docstring already gives
for keeping "the one place the two [vocabularies] meet" in one file.

Only after every node's backing data is in place does the canvas actually
change: every open window closes, one opens per non-Mix node via the
existing `_module_factory()` (which already returns `None` for a node type
this build cannot construct -- the missing-module case falls out of code
that already existed, not new gating), then `patch_layer.restore()`
reconnects the saved cables through the same accept/refuse gate a live drag
already goes through (`restore()` already emits `cablesChanged`, so the
existing `_rebuild_graph()` wiring picks the new graph up with no separate
call). Window position and the keyboard band are untouched, on purpose:
neither is patch content (decision 69's own boundary, #169, #213), and
touching them here would have been the "helpfully" behaviour decision 69
explicitly ruled out for the file version, just moved to the workspace
instead.

## 3. A known, pre-existing gap surfaced rather than papered over

`migrate_fixed_patch()` folds the old `Patch.voice.velocity_to_amp` into the
new `amp_env` node's own `velocity` parameter -- a real, representable value
in the graph model. But the Amp Env module window's knob panel
(`SECTION_TITLE_FOR_TYPE["amp_env"]` → `synth_params.sections_for()`) still
shows only the old fixed engine's DAHDSR knobs; it has never had a Velocity
knob, independent of this ticket. `_apply_graph_result()`'s `hasattr()` guard
means this loads without crashing (there is no `Envelope.velocity` field to
set), but it also means the value is invisible on screen and a subsequent
Save silently drops it again -- the exact "knowing loss, named" posture
decision 69 §5 already keeps for Voice settings, just one link further down
the chain than that decision anticipated. Fixing it is a knob-panel feature
(giving Amp Env a real Velocity knob backed by the graph parameter), not a
wiring change, and is left for the owner to schedule rather than folded into
this ticket.

## 4. #174 (the flat Load dialog) is a separate ticket, left alone on purpose

#174 asks for a real preset browser -- search, tags, favourites, audition-on-
hover -- in place of `_open_load_dialog()`'s flat `QInputDialog.getItem()`
name list. This ticket only changes what happens *after* a name is picked
(`_load_patch_file()` replacing the inline `_apply_patch(patch_format.
load_patch(path))`); the picker itself, and everything #174 is actually
about, is untouched. There was no "wire it twice" risk to guard against
here: the format underneath and the browser on top are orthogonal, and
building #174's panel now would have been scope the owner did not ask this
ticket to take on. Assessed, not defaulted into.

## What needs the owner's eyes

- **How Save/Load *feels*** -- whether losing the three default modules on
  a bare canvas for a non-synth load (unchanged, always did this) or the
  reconstructed canvas after a synth Load (new: closes every window and
  reopens exactly what the file names, at the canvas's default tidy
  position rather than wherever they were left) reads as expected, is a
  judgement this ticket does not make for itself.
- **§3's Amp Env Velocity knob** -- worth adding now, or tracked as its own
  small ticket.
- The loader's notices still only reach the status line (#227, unresolved
  at the time this landed) -- unchanged by this ticket on purpose, per its
  own instruction to use what exists rather than invent a second mechanism.

## Index

See `docs/DECISIONS.md`.
