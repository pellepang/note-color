# 69. Patch persistence for an arbitrary graph, and migrating old patches (issue #209)

Engine 8, the last piece of decision 56's Phase 4, sequenced deliberately
after the modulation layer (decisions 66-68) so this format is written once
against a settled shape rather than twice. `src/notecolor/gui/graph_format.py`
is the whole of it: `graph_patch_to_toml()`/`save_graph_patch()` write a
version 2 patch file; `load_graph_patch()` reads either version, migrating a
version 1 file on the way through in memory.

## 1. One directory, one file kind, told apart by a version field

`patch_format.py`'s own words: "There is no version field" -- true of its
schema, unchanged by this ticket. This ticket's schema adds one, in the same
`*.toml` files under `patch_format.patches_dir()` the Synth View already
saves into (`synth_view._save_as()`, present before this ticket). No
`version` key at all is decision #106's fixed-topology schema, exactly as
`patch_format.patch_from_toml()` already parses it; `version = 2` is this
ticket's own `[[node]]`/`[[cable]]` array-table schema. This is the same
move `read_project_manifest()` already made for the Track/Part rename
(CLAUDE.md's glossary note, #150): one manifest, one entry point, a version
field deciding which reader gets used, not a second directory or a second
file extension to keep straight.

**A version 2 file opened by the old TUI synth tool degrades, it does not
crash.** `patch_format.patch_from_toml()` already ignores any key it does
not recognise (its own documented "additive overlay" posture) -- a `[[node]]`
array-table means nothing to it, so it silently produces an all-defaults
`Patch`, exactly the way a malformed file already degrades today. Nothing in
`patch_format.py` needed to change for this to be true; it was already true
before this ticket, which is what made sharing one directory the right call
rather than a risk to manage.

## 2. What the format carries, and what it deliberately does not

Per module instance (`[[node]]`): `id` (the canvas's node id -- today always
equal to its type key, since the canvas allows one instance per type, but
kept distinct from `module` for the day that stops being true), `module`
(the stable engine identity, `contract.ModuleDescriptor.module_id` --
namespaced for a plugin, `"clap:com.example.reverb"`), `title`, `side`
(which side of the Mix stripe), `out_kind` (audio/mod/both), `can_in`/
`can_out`/`is_delay` (canvas cosmetics, no bearing on what the engine
builds), a `[node.settings]` sub-table (construction choices) and a
`[node.parameters]` sub-table -- `{param_id: value}`, exactly
`contract.ParamBlock.snapshot()`'s shape, because that is what the contract
says it was built for. Per cable (`[[cable]]`): `source` plus either `dest`
(a sound cable) or `mod_dest`/`mod_label`/`depth` (a modulation cable, depth
inline, decided below).

**The Mix node is never written.** It is not patch content: every graph has
exactly one, it is always called `"mix"`, and it is always the boundary --
`graph_patch_from_data()` synthesises it before reading a single `[[node]]`
line, the same way `patch_canvas.Canvas` already adds one to every workspace
regardless of what is restored. A cable can still name `"mix"` as a `dest`
or, once past it, as a `source`.

**Two things the GitHub issue's wording ("module positions on the canvas")
could be read as wanting here, that this decision puts elsewhere on
purpose:**

- **Window position is workspace layout, not patch content** -- #169's
  ticket, not this one. Decision 57 §5 already drew this line for
  *appearance*; the same argument applies to *position*: where a window
  happens to sit the last time someone looked at the screen is a fact about
  a session, not a fact about the sound, and two people opening the same
  patch on two different screens should not fight over whose window
  arrangement is "the patch." No field carries it here.
- **Cable sag/swing/colour-scheme and the rest of decision 57 §5's six
  appearance values are settings** (the eventual #213 window), global to
  every patch. No field here either.

Stating the boundary here rather than merging it into "whatever this file
happens to also carry" is the instruction this ticket was given directly,
and it is worth restating why it matters in practice: without it, the very
first patch/workspace/settings feature built on top of this format would
have to guess which of the three a new field belongs to, and guess wrong at
least once.

## 3. Depth lives inline on the cable, not in a table

`patch_graph.Cable.depth` is already one field per cable with nothing shared
between cables (decision 66 §1 puts the *live* depth in a shared
`ModuleGraph.mod_depths` array purely so the audio thread can read one
`float64` slot without a recompile -- an implementation detail of the
running graph, not a fact the *file* needs to represent). A saved patch has
no audio thread reading it and no slot to protect; storing depth inline,
one cable one number, is the direct translation of what is already true of
the in-memory model, and a side table would only add a level of indirection
matching nothing on either side of the format.

## 4. A missing module fails with a sentence, never a `KeyError`

`known_module_ids()` is every `module` id this build can actually construct:
`patch_bridge.MODULE_FACTORIES`, `patch_bridge.NOT_IN_ENGINE` (the
deliberately-inert-but-real canvas types), and the effect types that already
degrade to a `Passthrough` for want of a module (`chorus`). A node naming
anything else still becomes a real `NodeSpec` -- so a cable touching it does
not vanish, and `patch_bridge.build_graph()`'s own existing fallback (sound
path -> `Passthrough`, mod-only -> silently absent, both already reported
through its own `notices()`) still applies unchanged -- plus an explicit
notice naming both the node and the module id in
`GraphPatchResult.notices`, read the same way every other notice in this
stream already is. This is deliberately the same degradation posture
`patch_format.py` already has for a malformed file: less patch, never no
patch, and never a traceback.

## 5. Migration: what a version 1 patch *means* as a graph

**Presumably the default chain with its knob values applied** -- the
GitHub issue's own words, and exactly what `migrate_fixed_patch()` builds:
Osc 1 -> Filter -> Amp Env -> Mix always (they always ran in
`synth_engine.SynthVoice`), with Osc 2 and Noise added only when they were
actually contributing (`level > 0`) -- decision 56 §2's "a patch with no
Osc 2 has no Osc 2," applied to what the old engine actually rendered, not
to a struct field that always exists whether or not it does anything. The
effects chain becomes nodes chained in file order after Amp Env, exactly
mirroring `patch_to_toml()`'s own "file order IS chain order." The LFO
becomes a per-note `Lfo` node plus a modulation cable, when its old
`destination` has a real graph equivalent -- `"filter"` onto Filter's
Cutoff, `"pitch"` onto every present oscillator's Fine -- and is left
migrated-but-unconnected, with a notice, for `"amp"`, which decision 67's
destination list has nothing to land on yet. `Filter Env`'s knobs are
dropped without a notice, because `filter_env` already has no engine module
on the live canvas today (`patch_bridge.NOT_IN_ENGINE`) -- migrating it
loses nothing that was ever audible.

**Two knowing losses, each named as a notice rather than silently
dropped:** the old per-patch Voice settings (polyphony, glide,
velocity-to-filter, master volume) have no home in the graph model at all
yet -- `PatchBridge`'s voice count is a fixed constant
(`config.POLYPHONY_SYNTH_VIEW`), not a per-patch knob -- so a patch that set
any of them away from default gets a notice saying so. A second effect of a
type the patch already has one of (two `[[effects]]` entries both
`type = "delay"`) is dropped with a notice, because the live canvas cannot
represent two instances of one type today either (`_module_open()`'s
one-window-per-type-key check) -- the format's `module`/`id` split is ready
for that day, `patch_bridge.py`'s factory lookup is not, and pretending
migration could do what the running engine cannot would be the dishonest
choice.

**Loading never rewrites the file.** The GitHub issue's own words again:
"silently rewriting the user's files on open is a surprise." `load_graph_
patch()` returns a `GraphPatchResult` with `migrated=True` for a version 1
source and touches the file on disk not at all --
`test_a_real_old_format_patch_file_still_loads_and_plays` asserts the
bytes on disk are unchanged after loading. Turning a migrated patch into a
version 2 file on disk is `save_graph_patch()`, called only by an explicit
Save -- an act, not a side effect of Open, the same distinction decision
57 §5 already draws between what a rebuild does automatically and what a
person has to ask for.

## 6. `ParamBlock.snapshot()`/`restore()`, used exactly where the contract says

The contract's docstring names them for this ticket by number:
`snapshot()` is `{param_id: value}` "for the patch writer (#209)." This
file's `[node.parameters]` sub-table is that shape, verbatim -- a caller
holding a live graph (a future Synth View Save action) calls
`module.params.snapshot()` per node and hands the result straight to
`save_graph_patch()`; a caller loading a patch gets the same shape back in
`GraphPatchResult.parameters` and can call `poly.set_parameter()` per entry
(which reaches `restore()`-equivalent behaviour through
`PolyGraph.set_parameter()`, since a per-note module exists sixteen times
and `restore()` alone only ever addresses one `ParamBlock`). This file
itself never touches a live `ParamBlock` -- it stays exactly as Qt-free and
audio-free as `patch_graph.py` and `patch_bridge.py` already are, testable
with no display and no sound card, which is what let every test above run
in under a second.

## What fought the design, and what needs the owner's eyes

- **Scope was deliberately capped at the format and its migration, not the
  Synth View's Save/Load menu wiring.** `synth_view.py`'s current
  `_save_as()`/`_open_load_dialog()` still write and read only the old
  fixed-topology `Patch` -- they do not yet call anything in this file, and
  the canvas's cables are, right now, exactly as unpersisted as they were
  before this ticket landed. This mirrors decisions 66/67's own split
  ("nothing here needed a change under `gui/`... belongs to the parallel
  canvas agent this round") in reverse: this ticket built the format canvas
  code will call, the same way stage 1/2 built the engine the canvas agent
  (decision 68) then wired up. Wiring `_save_as()`/`_open_load_dialog()` to
  `graph_format.py` -- reading `PatchBridge`'s live `ParamBlock`s via
  `snapshot()` for a real Save, restoring a `GraphPatchResult` into
  `patch_layer.graph` and `self.bridge` for a real Load -- is real,
  scoped work for a follow-up ticket, not a gap in this one's own "done
  when."
- **The one-instance-per-type-key ceiling surfaced sharply during
  migration**, not as an engine question but as a patch-file one: a
  version 1 patch's `[[effects]]` list can legally contain two delays and
  the file format is *ready* for two instances of one module type (`id`
  and `module` already split apart for exactly this), but `patch_bridge.
  MODULE_FACTORIES`/`_module_open()` are not, so migration's honest answer
  for a second same-type effect is to drop it with a notice rather than
  invent an instancing scheme the rest of the app cannot execute. Multiple
  instances of one module type is real, separate follow-up work,
  foreshadowed rather than solved here.
- **The LFO's old `destination = "amp"` has no home**, named in §5 above,
  because decision 67's widened destination list has nothing under Amp Env
  to land a modulation cable on (it sets `note.finished`, not a level). Left
  as a notice rather than invented a destination that does not exist yet.
- **Nothing here was verified against a real audio device**, and nothing
  needed to be -- every claim, including "sounds identical," is an
  `np.array_equal` over rendered blocks from `PolyGraph.process()`, the
  same convention every graph test in this stream already uses.

## Index

See `docs/DECISIONS.md`.
