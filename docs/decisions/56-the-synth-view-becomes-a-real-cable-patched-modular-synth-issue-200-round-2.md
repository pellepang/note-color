# 56. The Synth View becomes a real cable-patched modular synth (map #179, grilling round 2, 2026-09-12)

Settled by a grilling run the same day as #180, and it **partly overturns
decision 55**, which is the point: #180 asked "how do the effect modules route"
and answered it well; it never asked "what is a module", and inherited a
series-slot answer the project owner had not actually been offered. Asked
directly, the answer was cables.

Prior art for everything below is
`docs/research/modular-routing-prior-art.md` (commit `b3c4f4a`), which surveyed
VCV Rack, Bitwig Grid, Reaktor, Massive X, Phase Plant and Max/MSP.

## What the owner asked for

> "i want it to have wires where i can plug them in in any order i want, i
> understand that some of the ways it can be wired are unusable/redundant but i
> want a visible wire where i can see what goes in to what and i want it to be
> the real path of the sound."

And, on feedback loops:

> "if synths let you do that we should build it, we are not making a toy this
> should be the real deal ... i do not care how long this takes."

## 1. Cables, not slots

Decision 55 concluded series slots on the grounds that the owner's phrasing
("plug the different modules sequentially in any way I want") was satisfied by a
reorderable list. It was a defensible reading of the words and the wrong reading
of the want. Superseded: the Synth View gets a real node graph with **visible
cables that are the actual signal path**.

Decision 55's *runtime* findings all stand -- the shared effects bus, the 16-voice
cap, desktop-first, the measured per-block costs. Only its routing conclusion
(Fog 3) is replaced.

## 2. Modules are instances (Q2)

The drawer is a palette. Dragging adds an instance; a patch with no Osc 2 has no
Osc 2. Today the gesture means two different things -- `SYNTH_CORE_MODULES`
(Osc 2, Noise, LFO, Filter Env, Voice) already exist inside every voice, so
dragging one only opens a window onto knobs that were always there, while an
effect is genuinely appended to `Patch.effects`. One meaning from now on. This
also removes the standing ambiguity behind several #193 bugs: closing a window
is closing a window, removing a module is an explicit act.

## 3. One canvas, with a visible Mix node (Q4, Q10)

Everything **left** of the Mix node runs once per held note; everything **right**
of it runs once. The boundary is drawn, not hidden, because the owner's stated
reason for wanting cables is seeing the true path -- a boundary that summed
invisibly would make the cables lie.

The research found four positions on this and we took the most explicit one.
Massive X splits its routing canvas into a labelled Polyphonic Area and
Monophonic Area; Reaktor Primary marks mono/poly on every module icon and makes
poly->mono a hard error requiring an explicit Voice Combiner; Bitwig splits the
two into separate devices entirely; VCV alone hides it, summing automatically
(`Port::getVoltageSum()`), at the cost of every module having to be poly-aware.

**A per-note cable dropped on a once-only module is refused** (Reaktor's
behaviour, not VCV's): the input flashes and says to route through Mix. Silent
auto-summing was rejected specifically because it would put summing in places the
Mix node doesn't represent.

## 4. Feedback loops: allowed, through a delay you can see (Q5, Q7)

The owner asked for real feedback and it is being built. The mechanism is
**Bitwig's**, not VCV's, and the difference is worth recording because the first
pass of this grilling got it wrong.

Every system in the survey breaks the cycle with a unit delay; they differ only
in where it lives and how big it is. VCV steps all modules and *then* copies
cables, so every cable is unconditionally a one-sample delay -- which is only
affordable because the engine is C++ and runs per sample. Bitwig **refuses a
feedback cable** and requires an explicit Long Delay of at least one block.
Reaktor Core detects the loop, inserts an implicit `z^-1` and highlights the whole
loop orange. Max's gen~ forbids loops and offers `history`.

We take Bitwig's: the loop is legal, the delay is a module you place, and its
minimum is one block (11.61 ms at 512 frames / 44100 Hz). It is visible, which
matches decision 3 above, and it needs no cycle-breaking magic.

**The correction that made this affordable.** An earlier round of this grilling
told the owner that real feedback required opening #145's compiled-engine seam.
That conflated per-sample *graph traversal* (genuinely impossible in Python) with
per-sample *recursion inside a module* (already shipping -- the SVF's recurrence
runs in C inside `scipy.signal.lfilter`, decision 42). Only the former is barred.

## 5. Modulation is a separate layer with its own cable (Q3, Q6, Q12)

Modulation sources (LFO, Filter Env) are not audio nodes. They are sources
assigned to destinations, drawn as a **visibly different cable** -- thinner,
different colour, snapping to a knob rather than a socket. Full-modular
"every knob is a jack" was rejected as the single largest complexity fork
available, and nothing here blocks it later.

Worth noting against the survey: no system separates modulation from audio for
*signal-flow* reasons. Bitwig colours signals cosmetically (five types, all the
same cable underneath); Max and Reaktor split on event-vs-signal, an execution
model distinction. Our split is a **UI** decision, made for legibility. The one
split that would pay off technically in numpy -- scalar vs vector modulation --
has no UI precedent and stays an internal optimisation.

**LFOs carry a per-note / global switch**, defaulting to per-note. The two are
different musical tools (out-of-phase shimmer across held notes vs one locked
wobble), and the Mix node already expresses the difference: a per-note LFO lives
left of it, a global one right of it.

## 6. Numpy now, compiled core later -- and the contract is shaped for it (Q7)

The graph runs block-at-a-time in numpy. Measured locally for this decision, at
512 frames (11.61 ms budget): a Python call is 0.054 us, a numpy binary op
~0.6 us, an `lfilter` biquad 8.05 us. A 16-voice by 8-module graph is **~0.38 ms,
about 3% of budget** -- dispatch overhead is not the constraint. A VCV-style
per-sample engine is flatly impossible: a bare Python per-sample loop already
costs 32.7 us per 512 frames before doing any work.

#145's seam stays shut, but the **module contract is deliberately compiled-shaped**
-- block-at-a-time, no allocation in the callback, parameters delivered out of
band -- so the inner loop can be swapped to C or Rust without touching the UI,
the patch format or the graph model. Decision 55's revisit trigger (sustained
callback cost above ~70% of budget at the view's voice cap, measured in a real
`sounddevice` callback) carries over unchanged and now also covers the graph.

## 7. The graph engine lives beside the old one, for now (Q8)

`synth_engine.py`'s fixed osc->filter->env path also serves score-editor audition,
frozen-buffer playback and QWERTY note entry, none of which want a graph. The new
engine is additive: Synth View uses it, everything else keeps the simple path, and
a half-built graph cannot regress the rest of the app. Revisited once the graph is
proven.

## 8. First merge (Q9)

Sound through a chain the user wired themselves, reorderable by moving cables,
**plus one working feedback loop** through an explicit delay module. Not
modulation, not persistence, not the full effect set. The feedback loop is in the
milestone because it is the feature that justifies the graph's existence.

## 9. The UI is its own track, and it grows from what exists

The owner likes the current canvas and wants it expanded rather than replaced by
something foreign: today's module windows become graph nodes, gaining sockets.
The look is settled by **HTML prototypes and conversation, before any Qt work**
-- the owner asked for this explicitly, and it is how this view's footer was
designed (`synth_workspace.py` still refers to "the prototype's `.knob` stack").
Those earlier prototypes were never committed; new ones live in
`docs/prototypes/`.

This is also what #193's Phase 5 deferral was waiting for. Its bugs (#163 window
sizing, #175 drop behaviour, #167 drag feedback) were held back until the module
definition settled; it has now settled, and they get fixed once, against the
final design.

## 10. Plugins: CLAP still, for a different reason (informational)

#145 chose CLAP over VST3 on licensing. **That reasoning is dead**: VST3
relicensed to MIT with SDK 3.8 in October 2025 (verified in
`steinbergmedia/vst3sdk`'s `LICENSE.txt` and submodules; the dev portal states
GPLv3 and the Steinberg proprietary licence are no longer offered).

The conclusion survives on new grounds -- CLAP is plain C, `ctypes`-bindable, with
per-method thread specifications and 1.x binary compatibility. The cost is now
explicit and was not before: **no Python CLAP host exists** (PyPI's `python-clap`
is an argparse wrapper), while the mature Python VST3 route, `pedalboard`, is
GPL-3.0 because it embeds JUCE -- reintroducing exactly the licence problem CLAP
was picked to avoid. Plugin hosting is a real project, not a checkbox, and stays
a later phase. Every module being a node with typed ports is what keeps the door
open.

## What this obsoletes

- Decision 55's Fog 3 answer (series slots). Its other findings stand.
- #181, the prototype scoped to prove the series-slot model.
- #191's Phase 4 as written (a headless series-chain engine).
