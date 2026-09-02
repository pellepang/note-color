# Effects chain: delay and chorus over a block-based NumPy stream

Research for issue
[#104](https://github.com/pellepang/note-color/issues/104) ("delay and
chorus in NumPy, and the effects-chain seam"), a child of the sound-engine
map [#99](https://github.com/pellepang/note-color/issues/99). #99 already
settled *what* ships first (delay and chorus, both variants of one
delay-line primitive; reverb deferred to its own ticket); nothing about the
chain itself was settled. This doc is the pre-design research the follow-up
decision/implementation tickets should cite.

Everything measured here was measured on this repo's own target block shape
— 512 frames at 44100Hz, i.e. `config.PLAYBACK_BLOCK_SIZE` /
`config.PLAYBACK_SAMPLE_RATE`, an **11.6ms** deadline per callback — on the
project owner's dev machine (Intel i5-7300U, AVX2, no AVX-512; Python
3.14.6, NumPy 2.5.2). The benchmark/artifact script is committed as
`scripts/effects_chain_bench.py`; every number below is reproducible with
one command (see "Reproducing").

## Question

1. How is a delay line actually implemented over a *block*-based stream —
   circular buffer, read/write index arithmetic, and what specifically
   breaks at block boundaries?
2. How do feedback and wet/dry mixing work, and what are the stability and
   ordering constraints?
3. What is chorus, concretely, in terms of that same primitive — LFO depth/
   rate, voices, parameter ranges — and what makes it sound good rather
   than mechanical?
4. Do effects belong per-voice or on a shared bus, and what does each cost
   given the sound engine's real-time budget?
5. How is a chain of effects usually expressed, so that a patch can declare
   one?
6. Is `pedalboard` (Spotify's JUCE-backed C++ effects with Python bindings)
   a better answer than hand-rolling — maintenance, wheels, latency, and
   whether it can process arbitrary NumPy blocks inside a real-time
   callback?

## Answer

### 1. The delay line: one circular buffer, and three invariants

A delay line is the elementary unit here: *"delay by `M` samples is
trivially implemented"* in the digital domain, and the whole thing is
`y(n) = x(n-M)` ([JOS, *Physical Audio Signal Processing*, "Delay
Lines"](https://ccrma.stanford.edu/~jos/pasp/Delay_Lines.html)). Delay,
chorus, flanger, vibrato and (eventually) reverb are all the same buffer
read differently — which is exactly why #99 grouped delay and chorus as one
primitive shipped twice.

Over a block-based stream the buffer is a fixed-size `np.float32` array plus
a persistent write index, and a whole block's worth of read positions is
computed as one vector — no Python per-sample loop:

```python
offsets = np.arange(n)                                  # n = len(block)
read  = (self.write - self.delay_samples + offsets) % size
wet   = self.buf[read]
self.buf[(self.write + offsets) % size] = block + self.feedback * wet
self.write = (self.write + n) % size
```

Three invariants, and all three are block-boundary concerns rather than
DSP-theory ones:

- **State must survive across calls.** The write index, the LFO phase, and
  the buffer contents are the effect. An effect object that is rebuilt (or
  reset) per block is not an effect; it is a click generator — measured in
  §3.
- **Buffer length must exceed `max_delay + block_size`,** so a block's read
  window can never overtake its own write window.
- **Read before write, or chunk.** The snippet above reads the entire
  block's delayed samples *before* writing the block, which is only correct
  while `delay_samples >= block_size` (11.6ms at these settings). A
  *feedback* delay shorter than one block would need a sample inside the
  block to read back its own output; the standard fix is to process in
  sub-chunks of `min(delay_samples, n)` rather than to go per-sample in
  Python. Chorus sidesteps this because its ~7ms delay is written first and
  read after, and its feedback is zero by default — but if chorus feedback
  is ever exposed (JUCE allows −1..1, see §3), the same chunking rule
  applies to it.

That "state must survive" requirement is not theoretical: processing the
same signal block-by-block with a stateful chain must be **bit-identical**
to processing it in one shot. Measured against `pedalboard` (which exposes
exactly this via `reset=False`), 4 seconds of audio through a
delay→chorus chain, 512-frame blocks vs. one call:

```
blockwise(reset=False) vs one-shot max abs diff: 0.000e+00
```

That is the acceptance test any hand-rolled implementation should be held
to, and it is cheap to write.

### 2. Feedback and wet/dry mix

Both are one line each, and neither is subtle:

- **Feedback** writes the delayed output back into the line, attenuated:
  `push(input + feedback * delayed)`. This is literally what pedalboard's
  own `Delay` does
  ([`Delay.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/plugins/Delay.h)),
  and it constrains `|feedback| < 1` for stability — pedalboard clamps it
  to `0.0..1.0`. Feedback is what turns a single echo into repeats; the
  repeat count is roughly `log(threshold)/log(feedback)`.
- **Wet/dry mix** is a plain linear crossfade — pedalboard computes
  `dryVolume = 1.0 - mix` and sums. `mix=0.5` is its default for both
  Delay and Chorus.

Two practical notes worth carrying into implementation, flagged as
*convention, not measured here*: a one-pole lowpass in the feedback path is
the usual way repeats decay into the background instead of ringing on
identically forever (neither JUCE's nor pedalboard's `Delay` has one, so
this would be note-color's own addition); and this repo already soft-clips
its mixed output with `np.tanh` in `playback.render_offline()`, which is the
right guard to keep once a feedback path exists at all.

### 3. Chorus is a *modulated* delay — and two mistakes make it mechanical

Chorus makes one source *"sound like many such sources singing (or playing)
in unison"*; modern implementations use *"multiple interpolating taps"* on
one delay line that *"oscillate back and forth about the positions they
would have while implementing a fixed tapped delay line,"* with each tap
ideally spatialized to its own stereo position ([JOS, PASP, "Chorus
Effect"](https://ccrma.stanford.edu/~jos/pasp/Chorus_Effect.html)). Moving
the read position *is* the pitch effect: the tap frequency-shifts by Doppler
as it moves. The canonical industry reference is Dattorro, ["Effect Design,
Part 2: Delay-Line Modulation and
Chorus"](https://ccrma.stanford.edu/~dattorro/EffectDesignPart2.pdf), *JAES*
45(10):764–788 (1997) — cited here as the field's primary text; the CCRMA
scan is page images, so no quotable text was extracted from it for this doc.

The neighbouring effect settles the parameter question by contrast: flanging
keeps the delay *"below the threshold of echo perception (e.g., only a few
milliseconds)"*, varies it *"according to a triangular or sinusoidal
waveform"* driven by an LFO, and — importantly — *"it is clearly necessary
to use an interpolated delay line"* because the delay must vary smoothly
([JOS, PASP, "Flanging"](https://ccrma.stanford.edu/~jos/pasp/Flanging.html)).
Chorus is the same structure at a longer centre delay.

Concrete ranges, from a shipping implementation rather than folklore —
`juce::dsp::Chorus`, which is what pedalboard's `Chorus` wraps
([JUCE docs](https://docs.juce.com/master/classdsp_1_1Chorus.html),
[`Chorus.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/plugins/Chorus.h)):

| Parameter | Range | pedalboard default | Notes |
|---|---|---|---|
| `rate_hz` | < 100 Hz | 1.0 | LFO frequency |
| `depth` | 0–1 | 0.25 | modulation amount |
| `centre_delay_ms` | 1–100 ms | 7.0 | *"around 7–8 ms"* is the classic chorus; lower + higher feedback becomes a flanger |
| `feedback` | −1..1 | 0.0 | negative values are a real chorus variant, not a bug |
| `mix` | 0–1 | 0.5 | dry/wet |

The maximum delay-line length is 100ms, so a chorus buffer is tiny (4410
float32 samples at 44100Hz ≈ 17KB).

Depth and rate are not independent knobs perceptually — together they set
how much detune the effect produces. The read position moves at peak rate
`2π · rate · depth_seconds`, so peak detune is
`1200 · log2(1 ± 2π·rate·depth)` cents. At 1Hz / ±2ms that is **±22 cents**;
at 0.5Hz / ±3ms, ±16 cents. Anything much past that stops sounding like
several players and starts sounding like a warped tape.

**What makes it mechanical, measured.** Two implementation shortcuts are the
usual culprits, and both are quantifiable. Running a 440Hz sine through a
1Hz / ±2ms chorus (fully wet) and measuring everything at least 50Hz from
the carrier, relative to the carrier (dBc):

| Implementation | worst artifact | rms floor | max sample-to-sample step |
|---|---|---|---|
| linear interpolation, LFO phase carried across blocks | **−88.9 dBc** | −107.5 dBc | 0.032 |
| nearest whole sample (no interpolation) | −48.4 dBc | −65.7 dBc | 0.063 |
| linear interpolation, LFO phase reset each block | −23.7 dBc | −56.9 dBc | 0.230 |

Reading the nearest whole sample instead of interpolating is **40dB
worse** — that is the "zipper noise" a stepped delay line makes as the read
index jumps a sample at a time. Restarting the LFO phase every block is
worse still (65dB), and the giveaway is the 7× larger sample step: a
discontinuity injected at exactly the block rate (86Hz at these settings),
i.e. an audible buzz locked to the buffer size. Both are avoided by the
same two decisions — fractional read index with linear interpolation
(`np.floor` + `frac` blend of two taps, vectorized), and LFO phase stored on
the effect and advanced by `n` samples per block.

Linear interpolation is the right default here: it *"sounds very good when
the signal bandwidth is small compared with half the sampling rate"*, while
allpass interpolation — better for feedback loops because it has no gain
distortion — is the wrong tool for a *time-varying* delay ([JOS, PASP,
"Delay-Line Interpolation"](https://ccrma.stanford.edu/~jos/pasp/Delay_Line_Interpolation.html)).

**Voices.** Multiple taps at spread LFO phases (`2π·v/voices`) sharing one
buffer is the standard richness knob, and it is nearly free: 3 voices cost
186µs/block vs. 81µs for one (§4's table), 1.6% of the block budget.

### 4. Bus, not per-voice — and the reason is arithmetic, not just cost

Both effects are **linear** operators, so `effect(a) + effect(b)` and
`effect(a + b)` are the same signal. Measured, block-processed, two notes:

```
delay,  identical settings          : 5.960e-08     (float32 epsilon)
chorus, identical LFO phase         : 5.960e-08
chorus, decorrelated LFO phases     : 2.999e-01     (peak signal 0.473)
```

Per-voice effects with identical settings therefore produce **literally the
same output** as one shared bus, at N× the cost:

| Configuration (512 frames @ 44100Hz) | mean | median | p99 | % of 11.6ms budget |
|---|---|---|---|---|
| delay only | 30µs | 22µs | 241µs | 0.3% |
| chorus, 1 voice | 81µs | 56µs | 546µs | 0.7% |
| chorus, 3 voices | 186µs | 128µs | 1135µs | 1.6% |
| delay → chorus chain | 120µs | 79µs | 871µs | 1.0% |
| **8× per-voice chorus** | **736µs** | 514µs | 2515µs | **6.3%** |

(Ticket #104 asked for this against "the throughput prototype's budget";
[#100](https://github.com/pellepang/note-color/issues/100) is still open at
time of writing, so the budget used here is the block deadline itself,
11.6ms, which is what #100 will be measuring voices against too. A shared
chain at ~1% leaves that prototype essentially its whole budget.)

The only thing per-voice routing genuinely buys is *decorrelated*
modulation — a different LFO phase per voice, which is the 0.30 row above
and is a real, audible unison-detune effect. That belongs to the **voice/
oscillator layer** (a per-voice detune/drift parameter in the patch), not to
the effects chain, and it should be built there if wanted. Two further
reasons the bus wins for the chain itself: a per-voice effect's state is
destroyed when its voice is released, which cuts off the delay tail that was
the entire point of the delay; and a shared chain is one object to
configure, reset, and reason about in a patch file.

**Recommendation: one shared effects chain on the output bus,** applied
after voice mixing and before the existing `np.tanh` soft-clip, in both
`LiveScheduler._callback()` and `render_offline()`.

### 5. How a chain is expressed

Three shipping designs, all converging on the same shape:

- **pedalboard**: `Pedalboard` *"acts like a Python list"* of `Plugin`
  objects — `append`/`insert`/`remove` — and every plugin (including a
  `Pedalboard` itself) exposes the identical
  `process(input_array, sample_rate, buffer_size=8192, reset=True)`, aliased
  to `__call__`. Because `Pedalboard` objects are themselves plugins, chains
  nest, and a `Mix` plugin runs several chains in parallel on the same
  audio ([reference](https://spotify.github.io/pedalboard/reference/pedalboard.html),
  [README](https://github.com/spotify/pedalboard#readme)).
- **Faust**: composition operators over blocks — `:` sequential (*"connects
  each output of A to the corresponding input of B"*), `,` parallel, `<:`
  split, `:>` merge, `~` recursive (feedback with an implicit one-sample
  delay) ([Faust manual, syntax](https://faustdoc.grame.fr/manual/syntax/)).
- **JUCE**: `dsp::ProcessorChain`, a compile-time tuple of processors, each
  with `prepare(spec)` / `process(context)` / `reset()`, processed in place
  via `ProcessContextReplacing`.

All three are: an ordered sequence of objects with one uniform method, plus
`prepare`/`reset` for sample-rate and state. That is the seam note-color
should copy, and it is the same shape this repo already established in
`detection_backends.py` (a `typing.Protocol` mirroring an existing
function's call shape exactly, with algorithm-specific config captured at
`__init__` time instead of threaded through every call).

Concretely:

```python
class Effect(Protocol):
    def prepare(self, sample_rate: float, block_size: int) -> None: ...
    def process(self, block: np.ndarray) -> np.ndarray: ...   # float32 in, float32 out, same length
    def reset(self) -> None: ...

class EffectsChain:            # itself an Effect, so chains nest (pedalboard's trick)
    def __init__(self, effects): self.effects = list(effects)
    def process(self, block):
        for fx in self.effects:
            block = fx.process(block)
        return block
```

Because the chain is an ordered list of named, parameterized entries, a
patch declares one as a TOML **array of tables** — the standard TOML
construct for an ordered list of records, and a natural fit for #99's
"patches are one hand-editable TOML file each" standing decision:

```toml
[[effects]]
type = "delay"
delay_seconds = 0.25
feedback = 0.35
mix = 0.30

[[effects]]
type = "chorus"
rate_hz = 1.0
depth_ms = 2.0
centre_delay_ms = 7.0
voices = 3
mix = 0.5
```

Order in the file is order in the chain (TOML guarantees array-of-tables
ordering), `type` selects the class from a small registry, and the remaining
keys are that effect's own constructor kwargs — so adding reverb later is a
new class plus one registry entry, with no format change. Unknown `type`
values should be skipped with a warning rather than raising, matching
`config_store.py`'s existing "absent, empty, or malformed reproduces today's
exact behavior" posture.

One constraint worth writing into the seam now: **parameters are read once
per block, not per sample.** A parameter changed mid-block should either be
applied at the next block boundary or ramped across the block; delay *time*
specifically must be ramped or interpolated, because stepping it is exactly
the −48 dBc artifact measured in §3.

### 6. `pedalboard`, assessed honestly

The ticket asked for evidence rather than reflex. Here is the evidence, both
directions.

**What is genuinely good about it:**

- **Alive and well-maintained.** 6.3k stars, releases v0.9.20 → v0.9.24
  between Jan and Jul 2026, commits within the last week of this research
  (Aug 2026), Python 3.10–3.15 support already landed.
- **Wheels everywhere that matters**: manylinux *and* musllinux for x86_64
  and aarch64, macOS universal2 (Intel + Apple Silicon), Windows amd64,
  CPython 3.10–3.14 — i.e. no compiler needed on any platform note-color
  targets for this subsystem.
- **It really can process arbitrary NumPy blocks in a real-time callback.**
  `process(array, sample_rate, buffer_size, reset=False)` is explicitly
  documented for streaming (*"If calling `process` multiple times while
  processing the same audio or MIDI stream, set `reset` to `False`"*), and
  measured bit-identical to one-shot processing (§1). It **releases the
  GIL** while processing (`py::gil_scoped_release` in
  [`process.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/process.h))
  and locks each plugin's own mutex in pointer order to avoid deadlocks —
  so calling it from a `sounddevice` callback thread is safe by
  construction.
- **Fast**, as expected of C++: delay→chorus 32µs/block mean (0.3% of
  budget) vs. 120µs hand-rolled; reverb 44µs, which is a real datapoint for
  the deferred reverb ticket.

**What disqualifies it here:**

1. **License: GPL-3.0.** Every source file carries the GPLv3 header, and the
   README states the project is GPLv3 (it bundles JUCE under its GPL option,
   plus the VST3 SDK, Rubber Band, LAME). note-color currently ships **no
   LICENSE file at all**, so this is not a compatibility question but an
   irreversible project-level choice: making pedalboard a required
   dependency effectively commits note-color to GPLv3 for any distribution.
   That is the owner's call, not an implementation detail — and it is a
   heavy price for two effects.
2. **The current published wheel does not even import on this machine.**
   Measured, first-hand, inside and outside the tool sandbox:
   `import pedalboard` for **v0.9.24 and v0.9.23 dies with SIGILL**
   (illegal instruction, exit 132) during native-module load on Python
   3.14 / manylinux x86_64 / Intel i5-7300U (AVX2, no AVX-512).
   **v0.9.20 imports and runs fine** — which is why every pedalboard number
   in this doc is from 0.9.20. So the two most recent releases are broken on
   the project owner's own development CPU, almost certainly a build-flags
   regression (the changelog around 0.9.23–0.9.24 is a cibuildwheel 3→4 and
   musl upgrade). This is precisely the binary-dependency failure mode this
   repo has twice declined (`aubio`/`essentia`, then FluidSynth in #28) —
   arriving in a form the earlier reasoning did not even anticipate:
   *the wheel exists, installs cleanly, and then segfaults.*
3. **Size and scope mismatch.** ~12MB installed (an 8.2MB native `.so`) — a
   whole JUCE audio framework, VST3/AU host included — to obtain two effects
   that are ~90 lines of NumPy, measured at 1% of the block budget.
4. **It would actually give note-color *less* control.** Its `Delay` is
   `juce::dsp::DelayLine<..., DelayLineInterpolationTypes::None>` — integer
   sample delay, no interpolation — so changing delay time live steps
   audibly (§3's −48 dBc case); and its `Chorus` is a single-voice
   `juce::dsp::Chorus`, with no per-voice phase spread. Hand-rolling gets
   fractional delay and N voices for free.

**On the SF2 precedent.** #104 rightly notes that #99 accepts a binary
dependency for SF2, so "no binary deps" cannot be the argument. The
distinction is not the dependency policy but *replaceability*: FluidSynth
implements the whole SoundFont 2 specification plus a sample-playback
engine, which is genuinely not hand-rollable at this project's scale, and
#99 already scoped it as an **optional** dependency that degrades to
"unavailable". Delay and chorus are 90 lines with a measured artifact floor
of −89 dBc. Accepting a GPLv3, currently-crashing, 12MB framework for those
is a different trade entirely, and it fails on its own merits rather than by
reflex.

**Verdict: hand-roll.** Keep pedalboard as (a) the reference implementation
this doc benchmarked against, (b) a reference for parameter ranges, and (c)
an option to revisit *if and only if* the reverb ticket concludes that a
good reverb is not worth hand-rolling **and** the owner has settled
note-color's own license question. Pin `pedalboard==0.9.20` if it is ever
used for benchmarking again on this machine.

## Recommendation: the concrete shape for note-color

1. **New module `effects.py`**, imported by `playback.py` only — no live
   detection path touches it, same isolation convention as
   `score_writer.py`/`batch_transcribe.py` (usage isolation, not import
   cost; NumPy is already a hard dependency).
2. **`Effect` Protocol** (`prepare`/`process`/`reset`) mirroring
   `detection_backends.py`'s existing seam, with `EffectsChain` itself
   satisfying it so chains nest.
3. **`Delay`** — circular buffer, feedback with `|g|<1`, linear dry/wet
   crossfade, optional one-pole damping in the feedback path; sub-chunk
   when `delay_samples < block_size`.
4. **`Chorus`** — same buffer, LFO-modulated fractional read index with
   **linear interpolation** and **phase carried across blocks** (the two
   non-negotiables from §3), `voices` taps at spread phases, defaults
   `rate_hz=1.0`, `depth_ms=2.0`, `centre_delay_ms=7.0`, `mix=0.5`
   (JUCE-derived, ≈±22 cents peak detune).
5. **One chain on the output bus**, applied after voice mixing and before
   the existing `np.tanh` soft-clip, in both `LiveScheduler._callback()` and
   `render_offline()`. Not per-voice (§4).
6. **Per-voice detune stays a voice-layer parameter**, if it is wanted at
   all — it is the one thing the bus cannot reproduce.
7. **Patch declaration as a TOML `[[effects]]` array of tables**, `type` +
   kwargs, order = chain order, unknown types skipped with a warning.
8. **Tests, matching this repo's "pure logic unit-tested, real I/O
   smoke-tested" convention**: (a) block-wise processing equals one-shot
   processing bit-for-bit; (b) a fully-wet delay of exactly D samples
   reproduces the input shifted by D; (c) feedback decays monotonically;
   (d) the artifact assertion — a fully-wet chorus over a sine keeps
   out-of-band content below roughly −80 dBc, which fails loudly if anyone
   ever drops the interpolation or the LFO phase carry.
9. **Reverb slots in as a fourth class later** with zero format change,
   which is exactly what #99 wanted from "the effects-chain seam".

## Open questions (for the decision ticket, not guesses)

- **Stereo.** Everything above is mono, matching `playback.py` today
  (`channels=1`). JOS's chorus advice is explicitly that each tap *"should
  be individually spatialized"*, and stereo is where chorus earns its keep.
  Going stereo doubles the buffers and changes `OutputStream(channels=)`;
  worth deciding deliberately rather than discovering later.
- **Whether effects are per-patch or global.** A patch-declared chain (§5)
  implies each patch owns its effects, which conflicts with one shared bus
  chain the moment two patches sound at once. The likely resolution — patch
  chain applied per patch-bus, a global chain after — needs deciding when
  the voice manager ([#105](https://github.com/pellepang/note-color/issues/105))
  lands.
- **Feedback-path damping** is convention here, not measured; pick by ear
  during implementation and document it the way
  `PLAYBACK_HARMONIC_WEIGHTS` already is.
- **Tempo-synced delay time** (delay set in beats against the score/tempo
  estimate this app already computes) is an obvious fit for a music-notation
  app and costs nothing structurally — but it is a feature decision, not
  research.

## Caveats on this research pass

- All timings are single-machine, single-run, on a 2017 dual-core mobile i5
  under a normal desktop load; treat the p99/max columns as jitter
  indicators (Python allocation and GC, not DSP cost) rather than hard
  worst cases. The means are what matters for the budget argument, and
  they clear it by two orders of magnitude.
- The hand-rolled implementations in `scripts/effects_chain_bench.py` are
  benchmark-grade, not ship-grade: they allocate a fresh output array per
  block, and they have no parameter smoothing. Preallocating output buffers
  is the obvious first optimization if it ever matters (it does not, at 1%
  of budget).
- Nothing here was listened to. Every quality claim is a measured spectral
  one (dBc sideband level) plus published parameter ranges; "sounds good" is
  ultimately the owner's ear, and the defaults in §3 are a starting point in
  the same provisional spirit as this repo's chord/rhythm constants.
- The Dattorro paper is cited as the field's primary reference but is a
  page-image scan; no text was extracted from it. The parameter guidance
  above comes from JUCE/pedalboard source and JOS's PASP pages instead.

## Reproducing

```
.venv/bin/python scripts/effects_chain_bench.py
```

Runs all three experiments (per-block cost, artifact levels, per-voice vs.
bus). The pedalboard section is skipped with a message if pedalboard is not
importable — it is deliberately **not** a dependency of this repo. To
reproduce the pedalboard numbers:

```
python -m venv /tmp/pbenv && /tmp/pbenv/bin/pip install "pedalboard==0.9.20" numpy
/tmp/pbenv/bin/python scripts/effects_chain_bench.py
```

`pedalboard>=0.9.23` currently crashes with SIGILL on import on this
machine — see §6.

## Sources

- [JOS, *Physical Audio Signal Processing*, "Delay Lines"](https://ccrma.stanford.edu/~jos/pasp/Delay_Lines.html)
- [JOS, PASP, "Chorus Effect"](https://ccrma.stanford.edu/~jos/pasp/Chorus_Effect.html)
- [JOS, PASP, "Flanging"](https://ccrma.stanford.edu/~jos/pasp/Flanging.html)
- [JOS, PASP, "Delay-Line Interpolation"](https://ccrma.stanford.edu/~jos/pasp/Delay_Line_Interpolation.html)
- [Dattorro, "Effect Design, Part 2: Delay-Line Modulation and Chorus", JAES 45(10):764–788, 1997](https://ccrma.stanford.edu/~dattorro/EffectDesignPart2.pdf) ([AES e-library entry](https://aes2.org/publications/elibrary-page/?id=10159))
- [JUCE `dsp::Chorus` class reference](https://docs.juce.com/master/classdsp_1_1Chorus.html)
- [pedalboard API reference (`Pedalboard`, `process`, `Delay`, `Chorus`)](https://spotify.github.io/pedalboard/reference/pedalboard.html)
- [pedalboard `pedalboard.io.AudioStream` reference](https://spotify.github.io/pedalboard/reference/pedalboard.io.html) — live streaming; *"Introduced in v0.7.0 for macOS and Windows. Linux support introduced in v0.9.14."*
- [pedalboard README (features, license, platform/Python support)](https://github.com/spotify/pedalboard#readme)
- pedalboard source, read directly: [`plugins/Delay.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/plugins/Delay.h), [`plugins/Chorus.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/plugins/Chorus.h), [`Plugin.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/Plugin.h), [`process.h`](https://github.com/spotify/pedalboard/blob/master/pedalboard/process.h)
- [PyPI `pedalboard` release metadata](https://pypi.org/pypi/pedalboard/json) (wheel tags, `requires-python`); GitHub API for release dates and commit activity
- [Faust manual, syntax — block-diagram composition operators](https://faustdoc.grame.fr/manual/syntax/)
- This repo: `playback.py`, `config.py` (`PLAYBACK_*`), `detection_backends.py`, `config_store.py`; issues [#99](https://github.com/pellepang/note-color/issues/99), [#100](https://github.com/pellepang/note-color/issues/100), [#104](https://github.com/pellepang/note-color/issues/104), [#28](https://github.com/pellepang/note-color/issues/28)/[#32](https://github.com/pellepang/note-color/issues/32)
