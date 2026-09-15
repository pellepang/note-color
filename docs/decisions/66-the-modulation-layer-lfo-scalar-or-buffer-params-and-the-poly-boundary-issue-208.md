# 66. The modulation layer: LFO, scalar-or-buffer parameters, and the poly boundary (issue #208, stage 1 of 3)

Implements the six questions settled with the project owner in the
2026-09-14 planning session recorded on #208, plus the LFO-jacks softening
that session named directly. Stage 1 of three (Mod Envelope and broader
destinations are later agents' work); done when an LFO can audibly wobble a
filter cutoff, per-note and global -- proved in
`tests/test_synth_graph_lfo.py::test_a_per_note_lfo_audibly_wobbles_the_filter_cutoff`
and `test_a_global_lfo_reaches_every_per_note_voice`.

## 1. A mod cable lands on a knob, via a second routing table

`graph.ModConnection` is that table: `(source node, source port) -> (dest
node, param_id, depth_index)`. No hidden `mod_in` port was added to any
module -- exactly the settlement's rejection of that alternative.
`ModuleGraph.mod_connections` holds the table; `ModuleGraph.mod_depths` is a
parallel float64 array, one slot per connection ever added, and
`ModConnection.depth_index` names a slot rather than carrying the depth
itself, for the reason `ParamBlock.values` is an array rather than
attributes: the ring UI (#210 §4) turns this number live, and a single
array slot the audio thread reads every block is what lets it do that
without a recompile (`ModuleGraph.set_modulation_depth()`,
`test_set_modulation_depth_does_not_touch_the_graph_revision`).

`ModuleGraph.judge_modulation()` is `judge()`'s counterpart: the source
port must be an output of kind `PORT_MOD` (not `PORT_AUDIO` -- a mod cable
still has to originate from the jack decision 56 §5 says is a different
kind of cable), the destination parameter must exist and declare
`modulatable=True`, self-modulation and duplicates are refused. Every
refusal is a `Verdict` with a sentence, #203's own grammar, never an
exception.

## 2. Scalar-or-buffer: the shape this ticket most needs described precisely

`contract.ParamBlock` gained, per parameter: `smoothed` (the audible
value), `buffers` (one row, preallocated at `max_block` in `__init__` --
the constraint the settlement names explicitly), and `mod_active` (which
rows are live this block). `ProcessContext` carries these as
`param_buffers`/`param_mod_active` alongside the unchanged `params`
(now bound to `smoothed`, not `values`).

A module that has never heard of #208 is unaffected: it reads `ctx.params`
and gets the smoothed scalar for free. A module that cares (`filter.py`'s
`cutoff`/`resonance`, `oscillator.py`'s `level`/`fine`) checks
`ctx.param_mod_active[i]` and, if set, reads `ctx.param_buffers[i]`
instead -- a plain float64 array, no different in kind from `ctx.inputs`.

**What "materialise" means, precisely.** The buffer's *memory* is always
there (one row per parameter, allocated once at `activate()`, regardless of
whether anything is ever patched into it) -- that is the "every
per-parameter buffer is preallocated" half of the settlement. What is
avoided is the *write*: `mod_active[i]` is only set, and `buffers[i]` only
filled, for a parameter that is either (a) receiving a live modulation
route this block, or (b) still mid-fade from a knob edit (see §6). Nothing
else in the parameter path scales with how many parameters a module
*declares*; it scales with how many are actually being pushed on right
now, which for the overwhelming majority of parameters on the overwhelming
majority of blocks is zero.
`tests/test_synth_graph_lfo.py::test_process_allocates_nothing_extra_with_an_unmodulated_lfo_in_the_patch`
and `test_an_unmodulated_parameter_never_goes_mod_active` are the two
halves of that claim, proved rather than asserted in prose.

`graph.ModRoute` is the router applied per destination parameter, right
after that parameter's `advance()` (§6) and right before the module's own
`process()` (`graph.CompiledGraph.process()`): seed the buffer from the
current smoothed scalar if nothing else has already seeded it this block,
add every incoming source scaled by `depth * (maximum - minimum)`, clamp
once. That is settlement §4, executed literally: *"multiple sources sum,
then clamp once at the destination, never per source."*

## 3. Per-note vs global: which direction is refused

`poly.PolyGraph.judge_modulation()` wraps `graph.judge_modulation()` with
the side rule, using the same `sides()` reachability the audio-cable
poly-crossing rule (`judge()`) already computes: **global source -> per-
note knob is legal; per-note source -> once-only knob is refused**, with a
sentence naming why -- *"there is no MIX node in the modulation layer to
sum sixteen voices into one number"* -- matching #203's `Verdict` grammar.
`PolyGraph.activate()` also asserts this defensively over whatever ended up
in `graph.mod_connections`, the same belt-and-braces `crossings()` already
gives the audio-cable version of this rule.

A cable dropped on a `modulatable=False` knob (a waveform, an octave, a
filter type, a shape) is refused by `judge_modulation()` itself, before the
poly question is even asked.

## 4. Depth lives on the cable, is edited at the knob

Covered in §1/§2 above: bipolar -1..+1 (`ModuleGraph._clip_depth()`), a
fraction of the destination's own range, summed then clamped once. Tested
directly in
`test_two_sources_sum_then_clamp_once_at_the_destination`.

## 5. The sources: the LFO, with both jacks (the softening)

`modules/lfo.py`'s `Lfo` is the first source. It has `PORT_MOD` *and*
`PORT_AUDIO` outputs, carrying the identical signal -- decision 56 §5's
deliberate softening, recorded here as the settlement instructed: *"do not
let it silently widen into 'every module gets every port kind'"*. The
reason this module gets both and nothing else does automatically is that
an LFO is, mechanically, just a slow oscillator, and refusing to let a
slow oscillator feed an audio input is a toy's restriction the owner's
standing "this should be the real deal" instruction (decision 56) already
overrides. This is not a precedent for the Mod Envelope (stage 2) or
anything else to inherit without its own justification.

**The per-note/global switch is baked into construction, not a runtime
parameter** -- `Lfo(mode="per_note" | "global")`, exactly the pattern
`oscillator.WavetableOscillator`'s waveform already uses, and for the same
reason: `descriptor().poly` has to be a fixed fact by the time
`PolyGraph.activate()` decides how many copies of a subgraph to build,
and there is no point in the pipeline where a live toggle could take
effect. `new_instance()` carries `mode` across to voice clones, the same
way the oscillator carries its waveform.

## 6. Smoothing: the parameter half of #224, taken as a freebie

`ParamBlock.advance(frames)`, called once per parameter block by
`CompiledGraph.process()` before that step's `ModRoute`s and its module's
`process()`, chases `values` (the edited target) with `smoothed` (the
audible value) via a one-pole filter, `DEFAULT_SMOOTH_SECONDS = 0.008`
(8ms) -- short enough to read as immediate, long enough that a single
block is not a discontinuity. Discrete parameters (`steps` truthy -- a
waveform, a filter type) get coefficient 1.0 and snap instead of fading;
smoothing a filter type to 1.4 is a coefficient recompute for a position
that does not exist.

**The one refinement the settlement's text does not spell out, and had
to be decided against the code**: a parameter set *before the stream's
first block* (loading a patch, setting up a test) snaps instead of fading
(`ParamBlock._first_block`). There is nothing sounding yet for a fade to
protect, and the alternative broke every parity/setup test in the suite
that (correctly, by every convention already in this codebase) sets a
parameter once before ever calling `process()` and expects it to be in
effect on the first real block --
`tests/test_synth_graph_voice.py::test_the_graph_matches_the_fixed_engine`'s
bit-parity claim depends on exactly this. A live edit mid-note still
fades; `ParamBlock.set_immediate()` is the escape hatch for the rare case
that wants an instant snap deliberately after the first block too (one
test, `test_synth_feedback.py`'s loop-timing test, needed it -- its claim
is about the loop's ordering, not about smoothing, and the fade would have
blurred the one-block burst its assertions depend on).

## Measured cost, against the 70% trigger

512 frames / 44100 Hz = 11.61ms budget. At 16 voices
(`osc -> filter -> amp env -> mix`, `sounddevice` not involved --
measured as wall-clock around `PolyGraph.process()`, the same methodology
decision 56 §6 and decision 65 used):

| patch | ms/block | % of budget |
|---|---|---|
| baseline, no LFO | 1.53 | 13.2% |
| + LFO in the patch, **unpatched** | 2.18 | 18.8% |
| + LFO patched onto filter cutoff | 3.88 | 33.4% |

33.4% is well clear of decision 55's 70% revisit trigger. Two things worth
naming in the number rather than leaving implicit:

- **An LFO sitting in the patch unused still costs ~0.65ms** (16 voices'
  worth of sine generation each block) -- the scalar-or-buffer split saves
  the *destination's* cost when nothing is patched into it, not the
  *source's*. Every module in a patch runs every block regardless of what
  it is wired to, the same convention `CLAUDE.md` already documents for
  the chord and rhythm pipelines.
- **The jump from "LFO present" to "LFO patched onto cutoff" is ~1.7ms**,
  the real cost of this ticket's modulation machinery: `filter.py`'s
  chunked coefficient recompute at `config.SYNTH_CONTROL_SUB_BLOCK` (64
  frames, 8 sub-blocks instead of 1) once cutoff is live, plus
  `ParamBlock.advance()` and `ModRoute.apply()` running for every
  parameter of every module of every voice. This is the number a later
  agent broadening the destination list should re-measure against, not
  assume stays flat.

## What fought the spec, and what is left open

- **LFO retrigger** (left to the code, per the settlement): added a
  `retrigger` parameter, default on, matching the settlement's own
  description of per-note mode ("each held note's LFO starts with that
  note"). A `mode="global"` instance is never reset after its first
  activation regardless of the knob -- there is no note-on on the
  once-only side for it to restart from -- so the knob only does anything
  on a per-note instance, which is where the settlement's open question
  was actually about.
- **A cycle built only from modulation edges (or a mix of mod and audio
  edges) does not yet get a graceful `Verdict` refusal.** `ModConnection`s
  are folded into the same ordering-edge set audio cables use
  (`ModuleGraph._ordering_edges()`), so such a cycle is still caught --
  `compile()` raises `CycleError` rather than silently producing a wrong
  graph -- but as a hard error, not a sentence aimed at the person holding
  the cable the way `_cycle_verdict()` gives an audio cycle. Flagged for
  #211's canvas work rather than solved here; nothing in stage 1's
  destinations (filter cutoff, oscillator pitch/level) can actually form
  one, so it did not block "done when".
- **Nothing here needed a change under `gui/`.** The whole layer is
  exercised at the `graph.py`/`poly.py` level, per the brief; `patch_bridge.py`
  belongs to the parallel canvas agent this round and was only read, not
  edited.
- **Nothing here could be verified without a real audio device**, and
  nothing needed to be: every claim, including the "audible wobble", is
  measured as an RMS swing over rendered blocks (headless), the same
  convention every other graph test in this stream already uses.

## Index

See `docs/DECISIONS.md`.
