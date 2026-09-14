# 64. A master filter, and a short delay: the owner's two drawer calls (tickets #205, #206)

Two questions #205 and #206 deliberately left open, put to the owner in
plain terms and answered. Both are small; both were left open because they
are about what the instrument *is*, not about how it works.

## 1. The filter runs on both sides of Mix

Asked as: *"every note gets its own filter — do you also want one filter
that shapes the whole sound at the end?"* Answer: **both.** Each singer with
their own tone control, and one more on the whole choir.

`StateVariableFilter` becomes `POLY_EITHER`, which was genuinely the one-word
change #205 said it was. The two worries behind the original
`POLY_PER_NOTE` both dissolve on inspection:

- Its `zi` is voice history, and a master filter's voice *is* the whole mix.
  One recurrence over the sum is the instrument being asked for, not an
  accident of sharing.
- Key tracking reads `ctx.note.pitch`, which does not exist right of Mix.
  `process()` already fell back to middle C there, so the knob becomes a
  **no-op rather than an error** — the honest answer, since a filter over
  sixteen notes at once has no single note to track. The knob stays visible
  and does nothing, which is what every modular does with a control that has
  no source.

## 2. A second, short delay module

Asked as: *"the echo can't go shorter than about 1/100th of a second — want
a second, much shorter one?"*, with the catch stated up front: it could not
be used for feedback loops. Answer: **add it.**

`modules/short_delay.py`, `ShortDelay`, `block_delay = 0`. It exists because
the delays that *make* a sound rather than repeat one all live below the
block floor: chorus at 15–30 ms, flanger at 1–10 ms, comb well under a
millisecond. None fit above 11.61 ms.

**Why it has to be a second module and not a knob.** A delay shorter than a
block has its output depending on that block's input, which makes
`block_delay = 1` false — and every feedback loop the graph ordered around
it wrong (decision 60 §2). The two behaviours cannot coexist in one
instance. So the graph refuses a feedback cable through this module with its
ordinary cycle refusal, which names the Delay it wants instead.

**The cost, accepted knowingly:** two parts in the drawer that look alike and
behave differently. The mitigation is naming — "Delay" repeats, "Short
Delay" colours — plus that refusal sentence, which arrives at the exact
moment the difference matters and is the only moment it needs explaining.

Implementation ports `effects.py`'s chunking (#205's "port, don't rewrite"):
the block is walked in chunks no longer than the delay, so a chunk's reads
can only touch samples an earlier chunk wrote. Unlike `effects.py`, the
reads are one contiguous run in the ring, so no index arrays are needed —
two slice copies per chunk, and the allocation test is deliberately run at
two block sizes because an allocation per *chunk* would be far louder than
one per block.

Two small departures from `Delay`, both because the lengths are different:
**feedback is bipolar** (a negative comb cancels the fundamental instead of
reinforcing it — half of what a flanger sounds like, and free here), and
**mix defaults to half** (at these lengths the wet signal alone is not the
effect; the interference between wet and dry is). **No damping**: at 5 ms the
tail is gone before dulling could be heard, and a one-zero average there is a
comb filter on top of a comb filter.
