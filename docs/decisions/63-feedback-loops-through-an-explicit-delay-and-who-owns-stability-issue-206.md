# 63. Feedback loops through an explicit delay, and who owns stability (ticket #206, plus the Delay half of #205, map #179)

`audio/graph/modules/delay.py`, with one addition to `audio/graph/poly.py`.
Fourth ticket of decision 56's engine stream, and the one the other three
were for: decision 56 §4 promised real feedback through a delay you place
yourself, and this is where a patch actually loops.

Most of the cycle rule was already built in #203 (decision 60 §2). What was
missing was a module that could be on both sides of Mix, a set of tests that
tried the cases a straight line does not cover, and a stated position on what
happens when the loop is louder than it should be.

## 1. The Delay works on both sides of Mix

#205's table says the Delay is the one module that has to. It now declares
`POLY_EITHER`, and the cables decide (decision 61 §2): once-only for a real
echo whose tail outlives the key, per-note for a delay inside a single voice.

Being true rather than declared came down to four things, three of which were
already right and are now tested rather than assumed:

- `reset()` zeroes the whole ring, so a reused voice slot cannot play the
  previous note's echo underneath the new one. This is the per-note case's
  one genuinely new requirement -- the once-only delay is never reset while
  the patch is running, which is precisely what makes its tail survive
  note-off.
- Nothing in `process()` reads `ctx.note`, so a voice's copy behaves
  identically to the once-only one.
- Buffers are sized from `Activation`, which every clone gets its own of.
- **`new_instance()` had to be overridden**, which is the one thing that
  changed. See §5.

## 2. What was ported from `effects.py`, and what was not

#205 says port the existing DSP rather than rewrite it. `effects.py`'s
`Delay` is the shipping, tested one (#104, decision 40).

**Taken: damping.** A one-*zero* averaging filter in the feedback path,
blended 0..1 -- Karplus-Strong's, not the one-pole the research named, for
`effects.py`'s own reason: a one-pole is a per-sample recursion with no
vectorized NumPy form, a one-zero is two shifted arrays and one carried
sample. It earns its place here beyond being free: inside a patch-level
feedback loop it is the only tone control the loop has until #205's filter
lands, and a loop with no rolloff rings metallically rather than decaying.
Off by default, exactly as it is there, because #104 measured nothing about
damping and the shipped default should be the untouched signal. Tested as
bypassed-when-zero, not "a filter with a coefficient of zero".

**Taken: block-partition transparency, as a test rather than as code.**
`effects.py` asserts that block-wise processing is bit-identical to one-shot
processing, and earns it by chunking. This module gets the same property free
from the one-block floor -- any sub-block is shorter than the delay, so its
reads can only touch samples an earlier sub-block wrote -- and the assertion
is worth having for the same reason it is worth having there: it is exact,
not approximate, so any drift fails loudly. The ported test includes damping,
whose carried sample is the part that could plausibly break it.

**Deliberately not taken: sub-delay chunking.** `effects.py` splits a block
into chunks no longer than the delay so a delay *shorter* than one block
still works. That is the one thing this module may not do. A delay shorter
than a block has its output depending on this block's input, which makes
`block_delay = 1` false, and every feedback loop the graph ordered around it
wrong. The two behaviours cannot coexist in one instance.

If short per-note delays are wanted later, they are a **second module** that
reports `block_delay = 0` and therefore cannot close a loop -- a module
choice, like the oscillator's waveform, not a knob, because a knob that
silently changed whether the cycle rule applies would change the legality of
cables already drawn. Flagged for the owner; not invented here.

**Not taken: float32 and the `Effect` protocol.** The graph is float64 and
speaks `Module`. Converting would bolt a second contract onto one module.

## 3. The cycle rule, and the cases a straight line does not cover

`graph.judge()`'s rule was already right and is unchanged: a cable closing a
loop is accepted exactly when the loop contains a module with
`block_delay >= 1`, asked as an ordering question rather than a graph-theory
one (decision 60 §2). Five cases the existing tests did not reach were tried,
and four of them were already correct:

- a delay that is **in the patch but not on the loop** does not legalise it,
  and the refusal names the loop rather than the patch;
- two loops **sharing one delay** are both legal;
- a legal loop **next to** an illegal one does not legalise it;
- a loop **entirely on the per-note side** is legal and gives each voice its
  own delay line -- which is what `POLY_EITHER` was for;
- a loop **through the Mix node** was accepted and should not have been.

That last one is the bug this ticket found. §4 below.

A related fact, discovered by a test that assumed otherwise and is now
written down in `graph.py`: **a loop's period is the delay plus one block.**
One block is the delay's guarantee; the second is the ordering's, because the
cable leaving the delay is the one the sort cuts, so the module the loop
returns into reads the buffer the delay filled *last* block. A one-block
delay in a loop therefore repeats every two blocks (23.2 ms at 512 frames).
This is inherent to ordering-instead-of-solving and is not a defect, but it
is the kind of thing that is discovered twice if nobody records it once.

## 4. A loop through Mix has no side, and is refused

A node that both feeds Mix and is fed by it would have to be sixteen copies
(it is upstream of the boundary) and one copy (it is downstream) at the same
time. There is no coherent thing to build.

`graph.judge()` cannot see this, and should not: it knows what a module
*declares*, and whether a node is upstream of Mix is a fact about the patch.
It is therefore decision 61 §2's split again, with a third case --
`PolyGraph.straddlers()`, checked in `PolyGraph.judge()` before the
poly/mono question (a node on both sides has no side for that question to be
about) and again in `activate()`.

The previous behaviour was worse than an accepted cable. `PolyGraph` put the
straddling node on the per-note side and then silently dropped the half of
the loop that crossed the boundary, so the patch built, ran, sounded like
nothing was connected, and said nothing about it. The refusal sentence names
the way out in both directions -- put the Delay before Mix to loop inside a
voice, after it to loop the mix -- because a refusal that cannot say what to
do instead undoes the reason for having visible cables at all.

## 5. `max_seconds` became a construction choice

`POLY_EITHER` has a price, and it is memory. The ring is sized for the
longest delay the knob can ask for, not for where the knob is, because the
knob moves and `activate()` is the only place the module may allocate. At two
seconds that is 44100 x 2 x 8 = **706 kB**, and a per-note delay is sixteen
of those: **11 MB**, allocated up front.

(The module's own docstring said 353 kB before this ticket. That was the
float32 figure; the graph is float64.)

11 MB is affordable on the desktop the Synth View targets (decision 55) and
is not obviously affordable on a Pi, so `max_seconds` is a constructor
argument defaulting to 2.0: a delay placed for per-note modulation can be
built with a fraction of a second and cost a sixteenth as much. That is also
the reason `new_instance()` is now overridden -- the default
`self.__class__()` would hand all sixteen voices a two-second ring when the
module on the canvas was built for fifty milliseconds, which is the exact
class of bug the oscillator's waveform override exists to prevent.

Whether the drawer should offer a short per-note Delay as a separate entry,
or expose `max_seconds` when a module is placed, is a UI question for #207
and is not answered here.

## 6. The minimum is the module's, and the graph does not know the number

`graph.py` asks one question -- `descriptor().block_delay >= 1` -- and takes
the answer as a guarantee. The frame arithmetic (floor the delay time to
`max_block`) is entirely in `delay.py`, and nothing in `graph.py` duplicates
it. That was already true; it is now stated in both files and tested from
both directions: the knob turned to its minimum still comes back a whole
block later, and a host running 64-frame blocks gets a 1.45 ms minimum for
free rather than the 11.61 ms a hard-coded constant would have pinned.

This is the property #206 asks for by name. Shortening the minimum later --
sub-blocks, or a compiled core behind #145's seam -- has to touch one module
and no graph code, and it only stays that way while the number lives on one
side of the line.

## 7. Stability is the user's problem. Nothing clamps

**The decision: a feedback path with gain >= 1 runs away, nothing refuses the
cable, and nothing turns the signal down.** This was already decision 60 §5's
passing remark; it is now the position, with a test named so that adding a
limiter fails it.

The reasoning, in order of how much weight it carries:

1. **It is what a modular does**, and it is what every system in decision 56
   §4's survey does -- Bitwig, VCV, Reaktor, gen~. None of them protect you
   from your own loop gain. A patch you can hear going wrong is one you can
   fix; a patch that was quietly corrected is one you cannot, because the
   thing you would fix is no longer audible.
2. **The engine cannot know the loop gain anyway.** It is the product of
   knob values around the loop and, once #205's filter is in it, a
   frequency-dependent response. A rule that refused "unstable" cables would
   have to guess, and would refuse patches that are fine while accepting
   patches that are not.
3. **The ticket says so explicitly**: do not silently clamp the user's sound.

### What the master soft-clip does and does not do

`sound_engine.SoundEngine._callback()` ends with `outdata[:, 0] =
np.tanh(mix)`, and `playback.render_offline()` has the same line. It is worth
being accurate about it, because "there is already a limiter" is the obvious
wrong answer to this section:

- It bounds what reaches the **device**, so a runaway cannot hand the sound
  card a sample of 10^17. That is real and worth having.
- It does **not** protect the patch. By the time a loop is into tanh's
  saturation the signal is a square wave; the clip stops the number growing,
  not the sound being destroyed.
- It is **not in the graph's path yet**. The graph engine is not wired into
  `SoundEngine` until #207, and when it is, the clip will sit after the
  graph, outside every loop -- so it can never be part of a loop's gain and
  can never stabilise one.
- It does **not** catch NaN. `tanh(nan)` is `nan`; `tanh(inf)` is 1.0.

### What does warn: a count, never a correction

Considered and rejected: a static warning on the canvas. Loop gain is not a
property of the cable being drawn (see 2 above), so any such warning is a
guess, and a guess that cries wolf is worse than silence.

Considered and taken: **counting the blocks in which the delay line held a
NaN or an infinity**, as `Delay.nonfinite_blocks`. The signal is not touched.
The case it exists for is specific and is not a loudness problem at all: a
NaN that reaches a ring buffer **never leaves it**, because the buffer's
contents are fed back into themselves. Turning the feedback knob down does
not recover; only `reset()` does. Without a number to show, a patch that has
gone silent-and-NaN looks exactly like a patch that is quiet, and the user
has no way to tell which.

It is measured with one reduction over the block (`self._wet[:n].sum()`) and
not a sampled element -- a NaN sits at a fixed offset in the ring and so
lands at the same position in every read window, which a first draft of this
check missed entirely and a test caught. A merely loud patch is finite and
deliberately not counted: level is the user's.

**Nothing consumes the counter yet.** Surfacing it is #207's, and that is the
open question this decision hands the owner: should a module be able to say
"I am in a state you cannot get out of by turning a knob", and should the
panel offer a reset for the once-only side, which today has no path to
`reset()` at all while a voice's copy gets one on every note-on?
