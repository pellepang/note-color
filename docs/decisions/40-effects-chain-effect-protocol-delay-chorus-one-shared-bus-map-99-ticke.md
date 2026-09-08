# Effects chain: `Effect` Protocol, delay, chorus, one shared bus (map #99, ticket #114, research #104)

The rationale for the settled decisions -- shared bus over per-voice,
hand-rolled over `pedalboard`, fractional-delay interpolation and a
carried LFO phase as the two non-negotiables, no reverb yet -- is
research #104 (`docs/research/effects-chain-delay-chorus.md` on the
`research/effects-chain-numpy` branch, `gh issue view 104`/`114`) and is
not re-argued here. This entry records what the build decided beyond
that, and what it was verified against. Nothing was listened to: the
machine was muted throughout, so every claim below is numerical.

**Transparent sub-chunking instead of a "read before write" special
case.** #104 named the third block-boundary invariant as "read before
write, *or* sub-chunk when a feedback delay is shorter than one block".
The build picked sub-chunking unconditionally: every effect splits any
input it is given into internal chunks no longer than its own shortest
delay (`Delay.chunk = min(delay_samples, block_size)`;
`Chorus.chunk = min(int(min_delay) - 1, block_size)`, the `-1` because
the fractional read touches `floor(pos)+1` too) and processes those.
A chunk's reads can then only ever land on samples an *earlier* chunk
wrote, so read-before-write is correct by construction -- for a 2ms
feedback delay under a 512-frame block, and equally for a single
`process()` call covering a whole offline render. The pay-off is that
chunking is *transparent*: each chunk computes exactly the recursion's
true value, so any partitioning of the input gives the same answer, which
is what turns #114's acceptance test from "close" into "bit-identical".

**Bit-identical, not `allclose` -- and one hazard fixed to earn it.**
`tests/test_effects.py` asserts `np.array_equal` on float32 between
one-shot and block-wise processing across block sizes 100/512/777/4096,
one-sample-at-a-time, and a 1.5s run that wraps the circular buffers
many times over, for a delay longer than a block, a feedback delay
shorter than a block, a damped one, a 3-voice chorus, a chorus with
feedback, a flanger-ish short-centre/negative-feedback chorus, and a
delay->chorus chain. Two implementation choices make that hold rather
than merely tend to:

1. The chorus LFO's phase is *derived* from an absolute int64 sample
   counter (`_elapsed`), never accumulated as a float per block. An
   accumulated phase is continuous (what #104 measured) but its last bits
   depend on how many additions it took to get there, i.e. on chunking.
2. The fractional read position is computed as `((write + offsets) %
   size - delay) % size`, reducing the *integer* write position modulo
   the buffer before subtracting the float delay. The obvious `(write +
   offsets - delay) % size` yields a pre-modulo float that can differ by
   exactly `size` between two chunkings of the same sample, and
   `(x - d) + size` is not bit-identical to `(x + size) - d` in float64.
   This did not happen to bite at the shipped buffer sizes (every case
   was already identical when probed), but it was a dependency on
   magnitudes rather than on structure, so it was removed.

**Damping is a one-zero, not the one-pole #104 named as convention, and
it ships off.** A one-pole lowpass is a per-sample recursion with no
vectorized NumPy form and no `scipy.signal.lfilter` in this project's
hard dependencies; a one-zero two-point average (Karplus-Strong's) is two
shifted arrays plus one carried sample, blended by `damping` in 0..1,
and stays chunking-transparent. It defaults to 0.0 because #104 measured
nothing about damping at all -- it is a by-ear knob in
`PLAYBACK_HARMONIC_WEIGHTS`' provisional spirit, not a tuned constant.

**Artifact floor asserted by FFT, and shown to be non-vacuous.** A
fully-wet 1Hz/+-2ms chorus over a 440Hz sine keeps its worst
out-of-band content below -80 dBc (`sideband_dbc()`, the same measure as
`scripts/effects_chain_bench.py`); the shipped implementation measures
**-88.9 dBc**, reproducing #104's figure exactly. A second test forces
the LFO clock back to zero before every block -- #104's "phase reset"
mistake -- and asserts the result breaches -40 dBc, so the floor is known
to catch what it was written to catch rather than passing by accident.

**Unknown effect types are data, never errors.** `build_effect()`
returns `None` for a `type` this build does not know and
`chain_from_specs()` collects those names into `EffectsChain.skipped`,
because `patch_format.py` deliberately preserves unknown types across a
save round trip: a patch written on a build that grew reverb must open
(and play, minus the reverb) on one that has not. Patch values follow
`patch_format.py`'s posture too -- wrong type or missing falls back to
the `config.EFFECT_*` default, out of range clamps -- and a couple of
synonym spellings per parameter are accepted (`time`/`delay_seconds`,
`rate`/`rate_hz`, `depth`/`depth_ms`, `delay_ms`/`centre_delay_ms`/
`center_delay_ms`) since a hand-edited TOML file is exactly where a
plausible synonym gets typed.

**Where the bus attaches, in both modes.** `SoundEngine` owns one
`EffectsChain` (empty by default), applied in `_callback()` between
`VoiceManager.render_block()` and the `np.tanh` soft-clip;
`set_effects(chain)` `prepare()`s the chain against the engine's own
rate/block and installs it by one attribute assignment, so a swap lands
at the next block boundary with no lock (the callback reads
`self.effects` once per block, and an old chain mid-`process()` finishes
on its own object). `stop()` resets the chain along with dropping the
voices; `all_notes_off()` deliberately does not, since a delay tail
outliving its notes is the entire point of a delay. Offline,
`playback.render_offline(effects=...)` applies the same chain once to
the summed mix before its own `tanh`, and extends the buffer by
`effects.tail_seconds()` -- a geometric-decay estimate
(`log(EFFECT_TAIL_FLOOR)/log(feedback)` repeats of `time` each, capped at
`EFFECT_MAX_TAIL_SECONDS`), not a measurement -- so repeats are not cut
off at the last note's end.

**Cost, measured on the shipped code** (512 frames at 44100Hz, this dev
machine, 400 blocks): delay 29us mean (0.2% of the 11.6ms budget), 2ms
sub-chunked feedback delay 119us (1.0%), 1-voice chorus 169us (1.5%),
the default 3-voice chorus 339us (2.9%), a delay->chorus chain 405us
(3.5%). Higher than #104's benchmark-grade 120us for the chain because
the default is now three voices and the wet sum accumulates in float64,
but still two orders of magnitude inside the deadline; the p99 columns
(up to ~2ms) are Python allocation/GC jitter, as #104 already noted.
Preallocating per-chunk scratch is the obvious first optimization if it
ever matters.

**Left open, for the tickets that own them:**

- *Per-patch vs. global chain* (#104's open question, deferred to the
  voice manager). `SoundEngine` holds exactly one chain and knows nothing
  about patches; which patch's `[[effects]]` gets installed, and whether
  a per-patch bus sits inside a global one (chains nest, so the shape is
  ready), is for #113's patch loading to decide.
- *Parameter changes while running.* Parameters are captured at
  construction; there is no live ramp of delay time, which #104 flagged
  as the -48 dBc zipper case if ever stepped. A live edit today means
  building a new effect and `set_effects()`-ing a new chain.
- *Stereo.* Everything is mono, matching `channels=1` everywhere.
  `Chorus` is where it would attach -- JOS's advice is to spatialize each
  tap individually, i.e. pan tap `v` rather than summing taps -- and
  nothing is pre-built for it.
- *Reverb* is one more class plus one `EFFECT_TYPES` entry, no format
  change; deliberately not here (#99's fog).
- *Tempo-synced delay time* against the app's own tempo estimate: an
  obvious fit, a feature decision rather than an implementation one.
