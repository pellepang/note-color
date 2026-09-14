# 65. The first merge: the canvas's patch becomes the sound (ticket #207, map #179)

`gui/patch_bridge.py`, with additions to `audio/sound_engine.py`,
`audio/graph/poly.py`, `audio/graph/graph.py`, `gui/patch_graph.py` and
`gui/synth_view.py`. Fifth and last ticket of decision 56's engine stream,
and the one the other four were for: decision 56 §8 says the milestone is
sound through a chain the user wired, reorderable by moving cables, plus one
working feedback loop.

Nothing in the engine changed shape here. What changed is that it is
plugged in.

## 1. The graph arrives beside the old engine, not instead of it

Decision 56 §7's rule is unchanged and is now load-bearing rather than
aspirational: `synth_engine.py`'s fixed osc → filter → env path still
serves score-editor audition, frozen-buffer playback and QWERTY note entry,
and none of them know a graph exists.

`SoundEngine.set_graph()` is deliberately shaped exactly like
`set_effects()`: prepare and activate off the audio thread, install by a
**single attribute store**, so the callback reads the whole old graph or the
whole new one and never a half-built anything. There is no lock and nothing
to tear, for the same reason `CompiledGraph` is immutable — an edit does not
change what is playing, it builds a replacement.

In the callback the graph's block is added into the same mix the voices
render into, **before** the effects bus and the `np.tanh` soft-clip. That
placement is the one decision 63 §7 already predicted: the clip sits after
the graph, outside every loop, so it bounds what reaches the device and can
never stabilise a feedback patch.

**Two voice pools, on purpose.** A graph note goes to `PolyGraph.note_on()`
and spends none of `VoiceManager`'s polyphony budget; the pads and the old
synth keep theirs untouched. They have different lifetimes (a graph voice is
reclaimed when a module sets `note.finished`; a `Voice` when it reports
`finished`), different stealing, and different owners. Merging them would
mean one of the two adopting the other's data model, and neither wants it.
What they share is the output stream, the effects bus and the clip — one
device, one master path.

The price is that `all_notes_off()` and `stop()` have to reach both. They
do, and it is the one place the split could plausibly have produced a bug
worth the name: a panic that silenced half of what is sounding.

## 2. Where the sound comes out: the last node, not an Out node

The graph had no designated output. Two answers were available.

**Chosen: anything you do not patch onward goes to the speakers.** A
once-only node that Mix can reach and that feeds nothing further is an
output; all of them are summed; if nothing follows Mix, Mix is the output.

The reason is failure modes, not elegance. This rule cannot be silently got
wrong: forget to patch the last module anywhere, and it is still the output.
The alternative's failure is a finished-looking patch that makes no sound
because one cable is missing, which is exactly the class of bug decision 56
set out to eliminate, arriving through a different door.

The cost is real and is stated rather than waved at: "output" is *inferred*,
and decision 56 §3's principle is that the boundary is drawn rather than
hidden. The same argument would want an output to be drawn.

**Not built, and the owner's call on #211: an explicit Out node**, beside
the Mix node, in the same spirit — visible, patchable, and the one place the
signal leaves. If it lands, this rule becomes its default rather than its
competitor.

### The refinement a feedback loop forced

Stated as "no outgoing cable", the rule is silent on the patch acceptance 3
asks for. In `MIX → Delay → Bypass → Delay` every node feeds something, so
nothing qualifies and the whole patch goes quiet — the exact failure the
rule exists to prevent.

So the test is not "feeds nothing" but **"feeds nothing new"**: a node is an
output when every node it feeds can reach it back again. A loop has no end,
so every node on it counts as one, and both are summed. On a patch without
loops the two readings are identical, which is why the short sentence is
still the one worth remembering.

## 3. The canvas node with no engine module is a wire, and says so

The drawer offers more than the engine can play. `chorus` is a real effect
in `effects.py` with no graph module; a restored workspace can name anything.

Rejected: **leaving it out of the engine graph.** The cable into it would go
nowhere and the cable out of it would come from nowhere, so the chain the
user drew and the chain that makes sound would be different chains. That is
decision 56's founding objection, reintroduced.

Rejected: **refusing to build the patch.** One unimplemented module silences
the canvas, and the refusal blames a cable the user had every right to draw.

Taken: **`modules/passthrough.Passthrough`** — one in, one out, the same
samples. It holds its place in the signal path, obeys every rule, and
`block_delay` stays 0 so a loop cannot become legal by containing one.

And it is **named**. `PatchBridge.notices()` lists every node built this way
and the Synth View appends it to its status line ("Chorus passes sound
through unchanged"). A module that quietly does nothing is a bug report
waiting to be written; the sentence is the whole user-facing contract.

`lfo`, `filter_env` and `voice` are a different case and are simply absent
from the engine graph, which is correct rather than a gap: the first two
send modulation, which goes onto a knob and never into a socket (and has no
engine layer until #208), and `voice` carries no jacks at all. **None of the
three can be an end of a sound cable**, so leaving them out removes nothing
from any signal path. They are named in the same status line anyway, because
"this knob does nothing yet" is worth saying out loud.

## 4. `PatchGraph.judge()` becomes the delegation it promised to be

Its own docstring said so. The refusals a user argues with and the rules the
sound obeys are now one set of rules: self-feed, the poly boundary, the loop
rule and its naming of the whole cycle all come from `PolyGraph.judge()`.

The canvas **gained** a refusal it never had — the loop that runs back
through Mix (decision 63 §4), which the stand-in could not see because it
did not know what a voice was.

Three things stay on the canvas side, each for a reason rather than by
omission:

- **Modulation.** A knob is not an engine port until #208. There is nothing
  to delegate to, and the copy in `patch_graph.py` is the only rule there is.
- **Two sentences about the canvas's own furniture** — "no longer on the
  canvas", "has nothing to take sound in". The engine's equivalents talk
  about ports, and this canvas draws unlabelled holes.
- **The duplicate-cable sentence.** The engine's names a port ("… into In on
  FILTER"); the canvas's does not, because there is nothing else it could be.

Every other sentence is now the engine's, and each was read against the one
it replaced before the switch. The stand-in stays, works, and is still
tested — with no engine attached, `PatchGraph` answers everything itself,
which is what keeps it testable without a sound card.

## 5. Two facts a host knows better than a module

`ModuleGraph.add()` grew two optional overrides, both from this bridge being
the first real host.

**`poly`.** Decision 61 §2 says a `POLY_EITHER` module's side is decided by
the patch. The canvas has already decided it — its drawer puts effects on
the once-only side and everything else on the per-note one — so the decision
is handed down rather than made twice. Without it the two disagree about an
*unpatched* Filter (the canvas says per-note; the engine's fallback for an
unpatched `POLY_EITHER` module is once-only) and the first cable of the
default chain, Osc into Filter, is refused on an empty canvas.

**`title`.** A refusal names a module. The user is looking at a window
captioned "FILTER"; a sentence that says "Filter" is naming something else
on the same screen.

## 6. A rebuild is the whole graph, and held notes survive it

A structural edit builds a new `PolyGraph` off the UI thread, activates it
(sixteen copies of the per-note subgraph, every buffer allocated), compiles
it, and installs it. There is no incremental rebinder and there should not
be one, for `graph.py`'s stated reason.

The audible consequence is that the new graph's sixteen voices are new
objects with nobody in them, so a held chord would stop dead the moment a
cable moved — during precisely the gesture that exists to demonstrate that
moving a cable changes the sound. `SynthView` therefore re-triggers every
key still down straight after a rebuild.

An oscillator's **waveform** is a construction choice, not a parameter (it
picks the module's wavetable set), so turning that knob rebuilds too. That
is a few milliseconds off the audio thread and is the honest cost of a
choice that is not a knob.

## 7. Measured: 48% of the block budget at sixteen voices

Decision 55's revisit trigger is sustained callback cost above ~70% of the
block budget at the view's voice cap, **in a real `sounddevice` callback**.
`scripts/graph_callback_cost.py` is that measurement; it is a script and not
a test for `sound_engine_smoke.py`'s reason (a suite that needs a sound card
fails on the machine that has none).

On this project's own machine, 512 frames at 44100 Hz (an 11.61 ms
deadline), the Synth View's default patch, sixteen notes held, ten seconds,
warm:

| Patch | mean | p99 | xruns |
| --- | --- | --- | --- |
| no graph (baseline) | 1.1% | 2.8% | 0 |
| Osc → Filter → Amp Env → Mix, 16 voices | **48.2%** | 111.7% | 0 |
| the same plus a feedback loop after Mix | 43.6% | 77.4% | 0 |

**Under the trigger, and the seam stays shut.** For scale, decision 55
measured the old fixed engine at 30.7% for the same sixteen voices, so the
graph costs about half again as much for a signal path the user can rewire.

Two honest caveats, neither of which moves the conclusion:

- **p99 exceeds the deadline while the driver reports no xruns.** That is
  the ring buffer doing what prototype #100 said it does — hiding overruns
  until the engine has already failed. Decision 55's trigger is written
  about *sustained* cost and the mean is what answers it; the tail is worth
  watching, and is the reason the script reports p99 at all.
- The once-only side costs the same at one voice as at sixteen, which is why
  adding the feedback loop lowered the percentage rather than raising it
  (the loop's cost is fixed and the run's voice attack pattern differs).

## 8. What this ticket found and did not fix

- **`StateVariableFilter` allocates one block per call**, because
  `scipy.signal.lfilter` returns a new array. It predates this ticket
  (decision 62's module, and `synth_engine.py`'s filter before that) and is
  in the graph's callback path now. The allocation test in
  `tests/test_patch_bridge.py` excludes it and says why. Flagged, not fixed.
- **Any graph feedback loop around the Delay has loop gain ≥ 1 as soon as
  the Delay's own Fdbk knob is above zero.** A mix-controlled delay is
  unity-gain — dry plus wet — so patching its output back to its input adds
  the whole of it again, and there is no attenuator on the once-only side of
  the canvas to bring it back under one. With Fdbk at 0 the loop decays and
  is a real, usable feedback sound; above it the patch builds toward the
  clip. Decision 63 §7 says stability is the user's and nothing clamps, and
  nothing does — but "there is no module that *can* turn a loop down" is a
  gap in the drawer rather than a principle. **A simple Level module on the
  once-only side is the owner's call**, and is the missing piece for this.
- `Delay.nonfinite_blocks` still has no consumer. Decision 63 §7 handed that
  to this ticket and it is not taken: the counter is per-module and the
  status line is per-patch, and inventing the surface for it here would be
  guessing at a panel #213 is going to own.
