# 55. The Synth View's runtime: desktop-first, 16 voices, and the effects bus that was already there (issue #180, map #179)

Settled 2026-09-12 by grilling #180, which was asked to answer map #179's Fog
questions 1-3 before anything got built. Two of the three turned out to have
been answered in 2026-09 already, by decision 40; the grilling's real product is
the third, plus the discovery that #179's premise was wrong.

## What #179 believed, and what is actually in the tree

#179 recorded, under a heading reading "Confirmed current state (read directly
from code, not guessed)", that the effect modules are decorative: *"grep for
delay/chorus in `synth_engine.py` finds no wet/dry mix, no feedback, no effect
bus at all"*, and concluded that giving the modules real audio was a scope
change against map #145.

The grep was right and the conclusion was wrong, because it was run against the
wrong file. `synth_engine.py` is the *per-voice* subtractive engine and correctly
contains no effects. The bus lives one layer out, in `sound_engine.py`:

```python
mix = np.zeros(frames, dtype=np.float32)
self.voices.render_block(mix, frames)
mix = self.effects.process(mix)   # the shared bus (#114), before the clip
outdata[:, 0] = np.tanh(mix)
```

`audio/effects.py` has shipped since ticket #114: an `Effect` Protocol, working
`Delay` and `Chorus`, an `EffectsChain` that processes its effects **in list
order**, an `EFFECT_TYPES` registry, and `chain_from_patch()`. `Patch.effects` is
an ordered list of `EffectSpec` that `patch_format` already persists, and
`SynthView._build_effect_module()` already appends to it when a module is dropped
in and removes from it when the window is closed.

So the user's report -- turning a Delay knob does nothing -- is true, and its
cause is one missing call rather than a missing architecture. Nothing installs
the chain: `set_effects()`'s only caller is `SoundEngine.__init__`, and
`gui/app.py` constructs `SoundEngine()` with no chain. `_register_patch_live()`
pushes the patch into `SynthEngine.patches`, which is why the osc/filter/envelope
knobs work and only those. #179 is re-scoped to that wiring, and #180 closes.

**The lesson is the one #191's traps section already states.** "This was fixed
before" and "this was never built" are both claims to verify, not premises to
build on. A grep that comes back empty answers "is it in this file", never "does
it exist".

## Fog 1 -- per-voice or shared bus: shared, and already decided

Decision 40 settled it for #114 and the argument is unchanged: both shipped
effects are linear, so per-voice routing produces the *identical* signal at N
times the cost, and a delay loses its tail the moment its voice is released --
which is a bug, not a feature. Re-measured here rather than taken on trust
(512 frames at 44100 Hz = **11.61 ms per callback**, warm, this machine):

| Load | ms/block | % of budget |
| --- | --- | --- |
| 8 voices | 1.68 | 14.4% |
| 16 voices | 3.57 | 30.7% |
| 24 voices (`POLYPHONY_WITH_DETECTION`) | 5.89 | 50.7% |
| 40 voices (`POLYPHONY_STANDALONE`) | 10.87 | **93.6%** |

| Effect | As a mix bus | Per-voice at 40 voices |
| --- | --- | --- |
| Delay | 0.05 ms (0.4%) | 17.2% |
| Chorus | 0.23 ms (2.0%) | 79.3% |

A per-voice chorus needs 79% of a callback that voices alone have already spent
94% of. This is not a tradeoff to weigh; it is arithmetic that rules the option
out. The whole shipped bus costs ~2.4% and does not grow with polyphony.

## Fog 2 -- where the DSP lives: in Python, and #145's C seam stays shut

On the numbers above the runtime is not the binding constraint at the polyphony
this view actually needs, so opening the C engine seam #145 reserved would be
paying its full cost against a budget that is not exhausted. The seam stays
reserved and unopened. Decision 01's posture holds here too.

The trigger to revisit is stated so it can be checked rather than argued:
sustained callback cost above ~70% of the block budget at the view's own voice
cap, measured in a real `sounddevice` callback rather than a benchmark. Two
things could plausibly cause it -- a non-linear effect that has to run per voice
to be correct (a per-voice saturator is the honest example, unlike delay or
chorus), or MIDI input (#173) raising the realistic voice count.

## Fog 3 -- cable graph or series slots: series, and already built

`EffectsChain` processes an ordered list; the patch format persists that order.
The user's own words were *"plug the different modules sequentially in any way I
want"*, and a serial chain expresses every ordering that phrasing asks for --
delay before or after chorus, saturation into the filter or the filter into
saturation. Parallel branches and feedback loops are not in the ask and are not
built. What is genuinely missing is the *UI*: nothing lets the order be changed,
so the model's ordering is real but unreachable. That is part of #179's re-scope.

## The voice budget: 16, and it is the price of the bus

`config.POLYPHONY_SYNTH_VIEW = 16`, claimed by `SynthView` on first show and
handed back in `closeEvent` -- the `SoundEngine` is process-wide and outlives the
window, so a cap claimed and never released would silently shrink every other
tool in the session. Guarded by one test in `tests/test_synth_view.py` covering
both halves.

Sixteen rather than forty because the headroom is what pays for everything
downstream of the mix, and because a two-row computer key band cannot physically
ask for more: ten fingers, plus release tails. The project owner confirmed 16.

The figure is explicitly provisional against MIDI input (#173): a sustain pedal
holds far more notes than ten fingers, and a MIDI keyboard is the first input
that could genuinely want a larger cap. Noted on that ticket.

## Portability: desktop-first, cross-platform later

CLAUDE.md's "Raspberry Pi class up to full desktops" constraint was written for
the *detector* -- the mic-to-colour pipeline at `SAMPLE_RATE = 22050` -- and has
never been tested against a 40-voice polysynth. At 4-8x slower than the machine
measured above, a Pi is already over budget on voices alone, before any of this.
The project owner settled it directly: the target is *this machine*, running
well; every operating system is a later goal; the Pi is not a constraint on the
Synth View.

Recorded here because an unstated constraint vetoes decisions silently. The
detector's portability is unchanged.
