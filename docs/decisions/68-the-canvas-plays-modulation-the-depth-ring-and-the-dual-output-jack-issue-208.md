# 68. The canvas plays modulation: the depth ring, and the dual-output jack (issue #208)

Stage 1 (decision 66) and stage 2 (decision 67) built the engine's
modulation layer and proved it from `graph.py`/`poly.py` directly, by
design: "nothing here needed a change under `gui/`... `patch_bridge.py`
belongs to the parallel canvas agent this round and was only read, not
edited." This is that canvas work. Done when a dragged modulation cable is
a real `ModConnection`, its depth ring edits live, a refusal reads the
engine's own sentence, and the LFO's two jacks both patch.

## 1. The gap: a mod cable never reached the engine at all

Before this ticket, `gui/patch_bridge.py` put `"lfo"` in `NOT_IN_ENGINE`
and `build_graph()` only ever looked at `cable.dest` -- a knob cable
(`cable.knob`) was invisible to it. The canvas could draw a mod cable and
judge it (against its own hand-kept copy of the rules, predating #208),
but nothing built an engine module for the source and nothing called
`ModuleGraph.force_connect_modulation()`. Turning an LFO's rate did
nothing to any patched knob, ever -- there was no cable underneath the
cable.

Worse: the LFO's own knob panel (`_build_synth_module("lfo")`) was reading
`tui/synth_params.py`'s **old fixed-engine** LFO section --
`rate`/`depth`/`delay`/`waveform`/`destination` -- against the **new**
`graph/modules/lfo.Lfo`'s real parameters -- `rate`/`shape`/`phase`/
`retrigger`. Only `rate` happened to line up by name. `Shape`/`Phase`/
`Retrig` were unreachable from the window; `Depth`/`Delay`/`Destination`
adjusted nothing. This wasn't a follow-on bug from #208 landing -- it
predates this ticket -- but it made "make a mod cable real" impossible to
finish without fixing it, so `synth_view.UTILITY_PARAM_SPECS["lfo"]`
replaces it outright with a spec matching the real module, the same
`UTILITY_TYPES` path #218's Level already established for "an engine
module with no `Patch` section." `GRAPH_ONLY_TYPES` is `UTILITY_TYPES`
plus `{"lfo", "mod_env"}` -- kept separate from `UTILITY_TYPES` itself
because that set also decides `MONO_TYPES` (which side of Mix a type key
defaults to), and an LFO belongs on the per-note side, not the once-only
one a utility defaults to.

## 2. `mod_env`: stage 2's `ModEnvelope` needed a `gui/` half too

Stage 2 (decision 67) built `graph/modules/envelope.ModEnvelope` --
unipolar DAHDSR, `PORT_MOD` only, per-note only -- but the agent that
built it was fenced out of `gui/` by this round's split. It could not add
a drawer row, so the module existed with no way onto the canvas at all.
Folded into this ticket rather than left for a third round: a distinct
type key (`"mod_env"`, not a second module hung off `"filter_env"`, which
still has no engine module and should not silently gain one by
association), routed through the same `GRAPH_ONLY_TYPES` path as the LFO,
with its own knob spec matching `ModEnvelope.parameters()` one for one
(`Vel` included, no amount/polarity knob -- decision 67's own point:
depth lives on the cable).

## 3. Building a mod-only source: `carries_sound()` wasn't the right gate

`module_for()` used to build a module only when `carries_sound(spec)` --
right for the LFO's *old* single-`KIND_MOD` shape, wrong now that a
mod-only node can have a real engine module (`mod_env`) with nothing to
build a `Passthrough` fallback out of (there is no such thing as a wire
that passes modulation through unchanged). Split into two questions:
`carries_sound()` (unchanged meaning, now also true for a `KIND_BOTH`
node's Out jack) and the new `sends_modulation()` -- true for a node whose
declared Mod jack has a real factory behind it. `module_for()` tries
sound first, modulation second, and only a node passing neither (`voice`,
or `filter_env` until it gets a module) is built as nothing.

## 4. The LFO's two jacks: `KIND_BOTH`, and one flat slot number per node

Stage 1's `Lfo` module always had both a `PORT_MOD` and a `PORT_AUDIO`
port; #211's canvas never had a way to say that about one node --
`NodeSpec.out_kind` was `KIND_AUDIO` xor `KIND_MOD`. `KIND_BOTH` is the
third value, and it only ever applies to the LFO
(`synth_view.DUAL_OUTPUT_TYPES`) -- not a general "any module can have
both" widening, the same restraint decision 66 §5 already put on the
engine side.

The mechanical question this raised: decision 57 §4's "as many jacks as
cables, plus one spare" is stated per node, and a dual-output node needs
it applied *per kind* -- an Out cable and a Mod cable must never compete
for the same jack slot. `PatchGraph.output_groups(node_id)` answers "which
kinds, how many each" (`KIND_AUDIO` then `KIND_MOD`, fixed order); each
kind's cables are counted and slotted independently
(`out_cables(node_id, kind=...)`), but the **jack key stays the plain
`(node_id, "out", slot)` the canvas already used** -- `slot_of()`'s new
`_kind_offset()` just makes a `KIND_MOD` slot's number start where the
`KIND_AUDIO` group's slots end, rather than restart at zero and collide.
Nothing outside `patch_graph.py`/the one loop in `patch_canvas.relayout()`
that walks `output_groups()` had to change shape at all -- every existing
single-kind node's numbering is untouched, because its one group's offset
is always zero.

## 5. Modulation judging is delegated for real now

`PatchGraph.judge()`'s knob branch used to be a hand-kept copy with no
`modulatable` check at all -- a cable onto `filter.type` or `oscillator.
octave` was silently accepted, because nothing asked. `_engine_verdict_
modulation()` mirrors `_engine_verdict()` exactly: when `PatchBridge.
judge_modulation()` (new) can answer -- both nodes have real engine
modules, the knob resolves to a real parameter -- its `Verdict` wins,
translated the same two ways the sound-cable path already translates one
(the engine's `code` is dropped; a duplicate keeps the canvas's own
sentence, since the engine's names a parameter id the user has never seen
spelled out). Every other sentence -- not-modulatable, self-modulation,
the poly-boundary refusal -- is `judge_modulation()`'s own wording,
unedited, per this ticket's own instruction. With no engine attached (or a
knob with no real parameter yet, e.g. `filter.env_amount`), the old
hand-kept copy still answers, now including a self-modulation check it did
not have before either.

**Resolving a knob's label to a parameter id.** `Knob.label()`'s own
docstring settles that a cable is keyed on the knob's *display label*, not
a parameter id the user never sees. Most of the time
`param_id_for_label()` can match that label straight against the engine
module's own `ParamSpec.name` (`filter.py`'s "Reso" for `resonance`), the
same convention `CHOICE_OPTIONS` already leans on. Building the
screenshots below found four places where the canvas's abbreviated label
and the engine's own name disagree outright -- oscillator `pulse_width`
("PW" vs "Width"), filter `key_tracking` ("KeyTrk" vs "Key"), delay
`feedback`/`damping` ("Fdbk"/"Damp" vs "Feedback"/"Damping") -- each a real
"a mod cable here does nothing" bug, not a hypothetical. `LABEL_ALIASES`
is the small, explicit table for exactly those four; everything else still
resolves by name.

## 6. Depth lives on the cable, edited at the ring -- the judgment calls

`patch_graph.Cable` gained `depth` (default `1.0`, the engine's own
unity), and `PatchBridge.set_modulation_depth()`/`.judge_modulation()`
resolve a canvas `(source, dest, label)` triple down to the engine's
`(source, source_port, dest, param_id)` the same way the sound-cable path
already resolves `(source, dest)`. None of that was specified down to the
pixel by decision 57 §3/66 §4, so three calls were made and should be
judged on the real canvas, not rubber-stamped here:

- **One ring per source, concentric.** Decision 66 §4's words -- "showing
  every source currently tugging on that knob" -- read as plural; a single
  ring can't show two depths distinctly, so each cable on a knob gets its
  own ring, stacked outward 4px at a time (`RING_STACK_STEP`). At two
  sources this reads fine on the real screenshot below; whether it still
  reads at four or five is untested and worth trying.
- **Depth as a filled arc.** Clockwise from noon for positive, counter-
  clockwise for negative, swept out to a half-circle (`DEPTH_ARC_MAX_
  DEGREES = 180`) at the bipolar extreme -- a Massive/Serum-style
  convention, not anything this codebase had before. The alternative (no
  arc, just the ring's own brightness) would have thrown away the sign
  entirely.
- **Drag-the-ring is a vertical drag, like a knob.** Grabbing the ring
  itself needed a hit-test with no existing gesture to reuse; the click
  point's angle around the ring picks *which* source's ring was grabbed
  (reusing the same angle `_endpoints()` already computes for where a
  cable meets the ring), and from there the drag is `Knob`'s own up-is-
  more vertical convention, scaled by a guessed `DEPTH_DRAG_PIXELS_PER_
  UNIT = 120`. Untested against a real hand, the same standing caveat
  decision 57 §5 already puts on sag/swing/resting-brightness.
- **One shared "turning" flag, not per-cable.** Dragging a ring (or
  turning the knob itself) lights every cable landing on that knob
  together, reusing the existing `_turning` tuple rather than adding a
  per-cable one. Simpler, and it is what the existing wheel-turn lighting
  already did for a single cable; whether the owner wants only the
  *dragged* cable to light when several share a knob is an open question,
  not a bug -- it would be a small, separable follow-up.

The write path is genuinely live: `set_modulation_depth()` writes into
`ModuleGraph.mod_depths`, an array every voice's `ModRoute` already reads
by index (decision 66 §1), so dragging the ring never triggers
`PatchBridge.rebuild()`. The cable's own `depth` field is written
alongside it so the *next* rebuild (a cable added, a module closed) starts
from the value the ring left it at rather than snapping back to `1.0`.

## What needs the owner's eyes and hands

Everything in §6's bullet list above, plus: the ring/arc's exact colours
and stroke widths were picked to read against the existing Copper theme,
not measured against anything. Screenshots
(`modulation-canvas`/`modulation-refusal-not-modulatable`/`modulation-
refusal-poly-boundary`, `scripts/ui_states.py`) are the starting point for
that conversation, not a substitute for it -- decision 8's standing rule.

## What is left open, deliberately out of this ticket's scope

- **The Settings window (#213)** would be where `RING_STACK_STEP`/
  `DEPTH_ARC_MAX_DEGREES`/`DEPTH_DRAG_PIXELS_PER_UNIT` become named
  settings, the same six decision 57 §5 already flagged for cable
  appearance. Not built here, per this ticket's own stated scope.
- **`filter_env` still has no engine module.** Its cable still goes onto a
  knob and does nothing, named in `notices()` exactly as before. Not
  folded into this round; #211/#213/#214/#219 remain untouched, also per
  scope.

## Index

See `docs/DECISIONS.md`.
