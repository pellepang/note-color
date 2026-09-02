# Subtractive synth architecture in NumPy: signal path, oscillators, filters, modulation (issue #103)

Research for [issue #103](https://github.com/pellepang/note-color/issues/103)
("Research: subtractive synth architecture in NumPy — oscillators,
filters, envelopes, LFOs"), a child of map
[#99](https://github.com/pellepang/note-color/issues/99) (sound engine).
It exists to give the engine's event/voice-model ticket
([#105](https://github.com/pellepang/note-color/issues/105)) and the
oscillator/filter build ticket
([#106](https://github.com/pellepang/note-color/issues/106)) a real
signal path to design against, rather than a hand-wave.

The seed is `/home/pelle/note-color/playback.py` (map #24, decision
[#32](https://github.com/pellepang/note-color/issues/32)): a fixed
3-partial harmonic stack (`config.PLAYBACK_HARMONIC_WEIGHTS`) and a
linear ADSR, both baked into one `synthesize_note()` call that renders a
whole note up front, plus a `LiveScheduler` whose `sd.OutputStream`
callback mixes already-finished buffers. That shape is correct for
replaying a transcription and structurally incapable of being *played*:
there is no filter, no modulation, and — the load-bearing limitation —
no way for a note's parameters to change after `trigger_note()` returns,
because the note's samples already exist by then.

## Questions

1. What is the conventional block-based subtractive signal path, and
   what state does a voice carry across blocks?
2. Why do naive saw/square waveforms alias, and what do the three
   standard fixes (PolyBLEP, wavetables, additive band-limiting) actually
   cost and actually buy — measured, not asserted?
3. Which resonant filter topologies survive per-block NumPy, given that
   an IIR filter's per-sample feedback cannot be vectorized along time?
4. Are envelopes and LFOs applied per-block or per-sample, and where is
   the line drawn in practice?
5. What parameter set does a musician expect on such a synth?
6. What do small Python synths in the wild actually do?

All timing numbers below were measured on this dev machine (Intel
i5-7300U @ 2.60GHz, 4 threads, NumPy 2.5.2 / SciPy 1.18.0 in this repo's
`.venv`) by `scripts/synth_engine_bench.py`, committed alongside this
document so every figure here is reproducible. Everything is quoted
against **one 512-frame block at 44100Hz — an 11.61ms deadline** — which
is exactly `config.PLAYBACK_BLOCK_SIZE` / `config.PLAYBACK_SAMPLE_RATE`,
i.e. today's real `LiveScheduler` callback budget, not a hypothetical.

## Answers

### 1. The conventional signal path, and what a voice carries across blocks

The textbook subtractive chain, unchanged since the Minimoog and still
the shape every modern soft-synth exposes, is:

```
  [osc 1]  \
  [osc 2]   >-- mixer --> resonant filter (VCF) --> amplifier (VCA) --> bus --> FX
  [noise]  /                    ^                        ^
                                |                        |
                          filter envelope           amp envelope (ADSR)
                          + LFO + key tracking      + velocity
```

The third-party corroboration that this is *the* expected shape, rather
than one arrangement among many, is that two independent published
standards encode exactly it:

- **The MIDI 1.0 Control Change table** reserves CC 70–79 as "Sound
  Controllers" with these defaults: 70 Sound Variation, **71
  Timbre/Harmonic Intensity** (i.e. filter resonance), 72 **Release
  Time**, 73 **Attack Time**, **74 Brightness** (i.e. filter cutoff), 75
  **Decay Time**, 76 **Vibrato Rate**, 77 **Vibrato Depth**, 78 **Vibrato
  Delay**, 79 undefined (verified by extracting the text of the MIDI
  Association's own "Control Change Messages (Data Bytes)" table, not
  from a secondary CC list). That is a filter with cutoff and resonance,
  an ADSR, and a pitch LFO with a delay — the chain above, and nothing
  more.
- **The SoundFont 2 generator set**, which every SF2 file and every SF2
  player implements, is the same chain again: `GEN_FILTERFC` /
  `GEN_FILTERQ`, a *delay-attack-hold-decay-sustain-release* volume
  envelope (`GEN_VOLENVDELAY`…`GEN_VOLENVRELEASE`), a second identical
  *modulation* envelope routed to pitch and to filter cutoff
  (`GEN_MODENVTOFILTERFC`), and two LFOs — a general mod LFO with
  `GEN_MODLFOTOPITCH` / `GEN_MODLFOTOVOL` / `GEN_MODLFOTOFILTERFC`, and a
  dedicated vibrato LFO — each with its own delay and frequency
  (FluidSynth's generator documentation, which enumerates the SF2 spec's
  own names).

This matters concretely for map #99 beyond "it is conventional": the
map's three engines (native synth, sampler, SF2 player) are supposed to
share one voice manager. **SF2's generator list is a complete
specification of the voice model that can host all three** — an SF2 voice
*is* a sampler voice with a 2-pole resonant lowpass, a DAHDSR amp
envelope, a DAHDSR mod envelope and two LFOs, and the native subtractive
synth is the same voice with oscillators substituted for the sample
playhead. Designing the voice against SF2's generator set rather than
inventing a parallel vocabulary is close to free and makes the SF2 ticket
a mapping exercise instead of a second engine.

**What a voice must carry across blocks.** The whole difference between
`playback.py`'s `synthesize_note()` and a playable voice is that a
playable voice is a *resumable* object. Per block it needs, as persistent
state:

| State | Why it must persist |
|---|---|
| oscillator phase (one float per oscillator, in `[0,1)`) | recomputing `np.arange(n)*f/sr` from note start would drift and, once frequency is modulated, click at every block boundary |
| filter state (`zi`, 2 floats per biquad section per voice) | an IIR filter's output depends on its own past; discarding it restarts the filter every block, which is audible as a buzz at the block rate |
| envelope stage + position (which of D/A/H/D/S/R, and how far in) | gate-off can arrive mid-block, so the envelope cannot be a function of "time since onset" alone |
| LFO phase | same reason as oscillator phase; also why SF2 has an explicit LFO *delay* generator |
| the note's own identity (pitch, velocity, channel, voice id) | needed for note-off matching and for voice stealing |

Only the first two of those exist in any form today, and only implicitly
(`LiveScheduler._active` holds `[samples, position]` — a playhead into a
finished buffer, which is a sampler's state, not a synth's).

### 2. Band-limited oscillators: why naive waveforms alias, and what the fixes cost

A sawtooth or square is discontinuous. Its ideal spectrum has harmonics
at every integer multiple of the fundamental, decaying only as 1/k, so it
never ends — there is always energy above Nyquist. Sampling a naive
`2*phase - 1` therefore folds every partial above `sr/2` back down as an
*inharmonic* component. Because the fold is inharmonic it does not
disappear into the tone the way an extra harmonic would; it beats against
it, which is the characteristic gritty-and-slightly-out-of-tune sound of
an unfixed digital saw, worst in the high register (where more partials
are above Nyquist) and worst when sweeping (where the aliases move the
*wrong* way relative to the note).

Välimäki & Huovilainen, *"Antialiasing Oscillators in Subtractive
Synthesis"*, IEEE Signal Processing Magazine 24(2):116–125, 2007, is the
standard survey; it groups the fixes as **bandlimited** (additive /
BLIT-derived), **quasi-bandlimited** (BLEP/BLIT-with-correction), and
**alias-reducing**, and is where PolyBLEP is introduced, as a BLEP
variant that replaces the table lookup with a closed-form polynomial. The
earlier bandlimited-impulse-train (BLIT) formulation is Stilson & Smith,
*"Alias-Free Digital Synthesis of Classic Analog Waveforms"* (ICMC 1996,
CCRMA).

**Measured, on this machine** (`scripts/synth_engine_bench.py`,
experiment 1 — frequencies snapped to exact FFT bin centres and analysed
with a rectangular window, so spectral leakage is identically zero and
every non-harmonic bin counted really is aliasing). "Worst" is the
loudest single alias relative to the fundamental; "total" is the RMS of
all of them:

| f (Hz) | naive worst / total | PolyBLEP worst / total | mip wavetable | per-note additive |
|---|---|---|---|---|
| 110.4 | −46.0 / −23.0 dBc | −53.9 / −39.1 | −86.0 / −66.1 | −275.7 / −249.7 |
| 440.1 | −34.2 / −17.0 | −42.3 / −33.3 | −99.3 / −84.5 | −264.5 / −243.5 |
| 1174.9 | −25.6 / −12.7 | −33.6 / −28.4 | −112.3 / −103.1 | −257.4 / −239.7 |
| 2092.8 | −20.8 / −10.2 | −29.4 / −26.2 | −118.3 / −111.5 | −255.6 / −237.4 |
| 3519.3 | −16.9 / −8.1 | −26.8 / −25.2 | −118.3 / −111.5 | −253.7 / −235.6 |

Three things fall out of that table.

- **The naive saw's aliasing is not subtle and gets steadily worse with
  pitch**: at C7 the total alias energy is only 8 dB below the
  fundamental. This is not an edge case to accept; it is the reason
  band-limiting exists.
- **PolyBLEP buys a consistent ~8–17 dB** over naive and, crucially,
  *keeps buying it* as pitch rises — but it is a correction, not a cure:
  −25 dBc of residual alias energy at 3.5kHz. That is the honest
  characterisation of PolyBLEP, and it matches Välimäki & Huovilainen's
  own "quasi-bandlimited" classification.
- **The mip-mapped wavetable is 60–90 dB better than PolyBLEP** and is
  *cheaper* to evaluate (32.6µs/block vs 51.1µs for PolyBLEP, experiment
  2). Its cost is entirely up front: each table must be built once, per
  octave band, and a note plays the table whose band limit is at or above
  its own pitch — which means the top octave of each band is slightly
  duller than ideal (harmonics that *could* have been represented at that
  pitch are absent, because the table was built safe for the top of the
  band). That dullness is the standard, accepted wavetable tradeoff, and
  it is inaudible against the alternative of −25 dBc of inharmonic grit.
- **Per-note additive is essentially exact** (−240 dBc is the float64
  noise floor) but is priced per *partial*: it is a sum of `k` sine
  arrays, so a 55Hz saw needs 400 of them every block. It is the right
  tool for building the wavetables once, offline, and the wrong tool for
  running them.

Per-block oscillator cost, per voice (experiment 2; budget 11.61ms):

| oscillator | µs/block | % of budget |
|---|---|---|
| `np.sin`, 1 partial | 18.6 | 0.16% |
| harmonic stack ×3 (today's `playback.py`) | 50.4 | 0.43% |
| naive saw | 38.4 | 0.33% |
| PolyBLEP saw | 51.1 | 0.44% |
| mip wavetable saw (linear interp) | 32.6 | 0.28% |

**Every one of these is negligible.** Oscillators are not where a NumPy
synth's budget goes; the choice between them is entirely a
quality-and-complexity choice, not a performance one. The PolyBLEP
implementation used here is fully vectorized across the block (the
residual is applied with a boolean mask, `np.where`, no Python loop) —
worth stating explicitly, because the reference C implementations are
all written as per-sample `if` branches: transcribing one of those
literally into Python costs 132.0µs/block instead of 51.1µs (experiment
2b), a 2.6× penalty. That is smaller than the 54× penalty the same
mistake carries in the filter (§3), for a reason worth internalising —
the oscillator's branch is *not* a feedback dependency, so the vectorized
form exists at all; the filter's is, so it does not.

### 3. Filters: per-sample feedback is where the whole design is decided

An IIR filter is `y[n] = b·x[n…] − a·y[n−1…]`. The dependence of `y[n]`
on `y[n−1]` means it **cannot be vectorized along time**, at all, by any
arrangement of NumPy array ops. This is the single hard constraint in
the whole engine, and it is why `playback.py` has no filter today.

Three topologies are conventional:

- **Biquad** (direct form I/II, RBJ cookbook coefficients) — 2 poles,
  12 dB/octave, resonance via Q. Linear and cheap. What FluidSynth's SF2
  voice actually uses: `fluid_iir_filter.c`'s state is exactly
  `{b02, b1, a1, a2}` plus two history samples, i.e. a biquad with
  `b0 == b2`.
- **State-variable filter (SVF)** — Chamberlin's classic 2-integrator
  form, or Zavalishin's topology-preserving-transform ("zero-delay
  feedback") version in *The Art of VA Filter Design* (Native
  Instruments, free PDF), or Andy Simper's optimised trapezoidal
  formulation (Cytomic, `SvfLinearTrapOptimised2.pdf`). Gives lowpass,
  highpass, bandpass and notch simultaneously from one structure, and —
  the practical reason it is the standard choice in VA synths — its
  cutoff/resonance coefficients can be recomputed *every sample* without
  the filter blowing up, unlike a naively-retuned direct-form biquad.
- **Ladder** (Moog, 4 poles / 24 dB/oct, with the nonlinear
  saturation in the feedback path that gives the Moog its character;
  Huovilainen's DAFx-04 non-linear digital ladder is the standard
  reference). Strictly a per-sample proposition because the nonlinearity
  is inside the feedback loop.

Measured cost of one 512-frame block (experiment 3):

| implementation | µs | % of budget |
|---|---|---|
| scalar Python SVF loop (1 voice) | 593.7 | 5.11% |
| `scipy.signal.lfilter` biquad (1 voice) | 10.9 | 0.09% |
| `scipy.signal.sosfilt`, 1 section (1 voice) | 78.8 | 0.68% |

A 54× gap, and it is entirely the Python interpreter: 512 loop
iterations is 512 rounds of bytecode dispatch. `lfilter` runs the same
recurrence in C.

The obvious NumPy escape — **keep the per-sample loop but vectorize it
across voices**, so each iteration processes all V voices' sample *n* at
once — does not work, and this is worth stating plainly because it is
the design people reach for first:

| V voices, own cutoff each | NumPy SVF (loop over time) | `scipy.lfilter` × V | numba njit SVF |
|---|---|---|---|
| 1 | 4.96 ms (42.8%) | 0.01 ms (0.1%) | 0.01 ms (0.1%) |
| 8 | 5.08 ms (43.8%) | 0.10 ms (0.9%) | 0.03 ms (0.2%) |
| 16 | 5.97 ms (51.5%) | 0.23 ms (1.9%) | 0.05 ms (0.4%) |
| 32 | 5.37 ms (46.2%) | 0.42 ms (3.6%) | 0.09 ms (0.8%) |
| 64 | 6.27 ms (54.0%) | 0.84 ms (7.3%) | 0.17 ms (1.4%) |

The across-voice version *does* amortize beautifully — 64 voices cost
barely more than 1 — but its **floor is ~5ms, at one voice**, because the
512-iteration Python loop is there regardless of how wide each iteration
is. That is 43% of the entire block budget spent before a single
oscillator has run. It is not viable.

**So the design is forced, and it is a good forcing:** hold each voice's
filter coefficients **fixed for the duration of a block** (or sub-block),
and let `scipy.signal.lfilter` run the recurrence in C, passing the
voice's own `zi` in and storing the returned `zf` back on the voice.
Coefficients that are constant within a call is precisely `lfilter`'s
contract, and `zi`/`zf` is precisely the "resume this filter where it
left off" API a block-based voice needs. This is the same answer
FluidSynth reached in C: it recomputes biquad coefficients per block and
*linearly interpolates* cutoff and Q across the block
(`fres_incr`/`q_incr`, applied over `FLUID_BUFSIZE` samples) rather than
stepping them.

`sosfilt` is 7× slower than `lfilter` here for a single section and
should be reached for only when cascading enough sections that
direct-form numerical conditioning actually bites — at 2 or 4 poles it
does not.

**The cost of block-rate coefficients is zipper noise.** At a 512-frame
block the coefficient update rate is only 86 Hz, which is audibly steppy
on a fast filter sweep. Pricing finer sub-blocks (experiment 3, V=16):

| sub-block | modulation rate | ms/block | % of budget |
|---|---|---|---|
| 512 | 86.1 Hz | 0.23 | 2.0% |
| 256 | 172.3 Hz | 0.38 | 3.2% |
| 128 | 344.5 Hz | 0.74 | 6.4% |
| **64** | **689.1 Hz** | **1.24** | **10.7%** |
| 32 | 1378.1 Hz | 2.32 | 20.0% |
| 16 | 2756.2 Hz | 5.18 | 44.6% |

64 samples is the knee, and it is not a coincidence: **FluidSynth's
`FLUID_BUFSIZE` is 64 samples** (`src/utils/fluidsynth_priv.h`), and
torchsynth's default `control_rate` is 441 Hz against a 44100 Hz
`sample_rate` — a 100-sample control block. Two independent
implementations landed within a factor of ~1.5 of the same granularity
that this benchmark independently identifies as the price knee.

What this rules out: **the nonlinear Moog ladder is not available in pure
NumPy.** Its saturation sits inside the feedback loop, so it cannot be
expressed as a fixed-coefficient LTI recurrence and `lfilter` cannot run
it — it needs a real per-sample loop, i.e. ~5ms/block minimum in Python.
Numba (an njit'd SVF is 30–100× faster than the NumPy across-voice loop,
and *would* make the ladder affordable) is technically already present in
this repo's `.venv` as a transitive `librosa` dependency, but `librosa`
lives in the optional `[batch]` extra and is deliberately never on the
live path — adopting numba for the synth would be a genuine new
first-class dependency decision, and should be a ticket of its own if the
ladder's character is ever wanted, not a quiet import.

### 4. Envelopes and LFOs: per-block, per-sample, or both

The convention is **three rates**, not two:

- **Event rate.** Note-on/note-off, patch changes. Handled at block
  boundaries by the voice manager.
- **Control rate.** Envelope and LFO *values*, and anything derived from
  them that feeds a coefficient — filter cutoff, filter Q, pitch. One
  value per control block. torchsynth makes this explicit and structural:
  `ADSR` and `LFO` subclass `ControlRateModule`, whose `sample_rate` and
  `buffer_size` properties *raise `NotImplementedError`* to force use of
  `control_rate`/`control_buffer_size`; a separate
  `ControlRateUpsample` module linearly interpolates the result back up
  to audio rate.
- **Audio rate.** The final amplitude multiply, and anything else that
  can be applied as a plain elementwise array op.

The line is drawn by *what the value feeds*, not by the value's own
nature: a value that becomes a **filter coefficient** must be control
rate (recomputing coefficients per sample means a per-sample loop, §3),
while a value that becomes a **gain** can be audio rate for free, because
a gain is just `x * env` on an array.

Measured (experiment 2b; one 512-frame block):

| operation | µs | % of budget |
|---|---|---|
| per-block scalar gain (`x * float`) | 1.0 | 0.009% |
| per-sample amp envelope ramp (`np.linspace` + multiply) | 15.5 | 0.134% |
| per-sample sine LFO across the block | 10.3 | 0.088% |
| `np.tanh` soft clip | 5.8 | 0.050% |

So the practical rule for this engine:

- **Amp envelope: audio rate.** It costs 0.13% of a block and a
  per-block-constant gain is *audibly* steppy — the classic zipper on a
  fast attack. `playback.py`'s `_adsr_envelope()` already builds
  per-sample linear segments with `np.linspace`; the only change needed
  is to make it resumable (emit the next `n` samples from a stored stage
  and position) instead of computing a whole note at once. That is the
  minimum-viable delta and it is small.
- **Filter cutoff/resonance envelope and LFO: control rate**, one value
  per 64-sample sub-block, for the reason in §3.
- **Pitch LFO (vibrato): audio rate is free but pointless.** Phase
  increment per sample would have to be integrated (`np.cumsum`) rather
  than multiplied, which is fine cost-wise; but at a few Hz of vibrato a
  64-sample control block is already 689 Hz of update rate, orders of
  magnitude above the modulation itself. Control rate, then linear
  interpolation, is the standard answer and it is what both cited
  implementations do.

One implementation detail that is not obvious and bit both cited
projects: **the LFO's phase must persist across blocks**, and its *delay*
(SF2's `GEN_MODLFODELAY`/`GEN_VIBLFODELAY`) is per-voice, counted from
note-on. Restarting an LFO's phase at every block boundary produces a
buzz at the block rate that is easy to mistake for a filter problem.

### 5. The parameter set a musician expects

Synthesising §1's two standards with what a small synth can plausibly
ship, the expected panel is:

**Oscillators (×2, plus noise)**
`waveform` (saw / square / triangle / sine — the four every subtractive
synth has), `octave` (−2…+2), `semitones` (−12…+12), `fine` (cents;
detuning osc 2 by a few cents against osc 1 is *the* standard way to get
a fat sound and costs nothing), `pulse width` (square only), `level`.
Noise `level` and optionally `colour`.

**Filter**
`type` (LP / HP / BP — free with an SVF, one extra output tap),
`cutoff` (Hz, exposed logarithmically — this is MIDI CC 74), `resonance`
(CC 71), `envelope amount` (bipolar), `key tracking` (0–100%: how much
the cutoff follows pitch; SF2 has no generator for this but every
hardware synth does, and without it high notes sound muffled relative to
low ones).

**Envelopes (×2: amp, filter/mod)**
`attack` (CC 73), `decay` (CC 75), `sustain`, `release` (CC 72). SF2 adds
`delay` and `hold` ahead of the attack; they are cheap and worth having
if the voice model is going to host SF2 anyway.

**LFO**
`rate` (CC 76), `depth` (CC 77), `delay` (CC 78), `waveform`,
`destination` (pitch / filter / amplitude — SF2's
`GEN_MODLFOTOPITCH` / `TOFILTERFC` / `TOVOL`).

**Voice / global**
`polyphony` (voice count), `glide/portamento` time, `velocity → amp` and
`velocity → filter` amounts, master `volume`.

Two notes for the patch-format ticket. First, that list is ~30 scalars —
small enough that a patch is one flat TOML table with no nesting beyond
`[osc1]`/`[osc2]`/`[filter]`/`[amp_env]`/`[filter_env]`/`[lfo]`, which
matches map #99's "one hand-editable TOML file per patch" decision
directly. Second, **velocity appears twice** (to amp and to filter),
which is the concrete form map #99's "velocity is the tell" standing
decision takes in the voice: a voice that does not carry velocity as a
first-class field cannot implement either routing, and retrofitting it
later touches every module.

### 6. What small Python synths in the wild actually do

The most instructive comparable is **Irmen de Jong's `synthplayer`**
(formerly `irmen/synthesizer` on GitHub, now at
`codeberg.org/irmen/synthesizer`; ~190 stars at the time it left GitHub)
— a pure-Python synthesizer with a keyboard GUI and a drum-kit example,
i.e. very nearly the tool map #99 describes. Reading its source:

- Its block size is **`params.norm_osc_blocksize = 512`** — the same
  number `config.PLAYBACK_BLOCK_SIZE` already uses here, arrived at
  independently.
- Its oscillators are **per-sample Python generators** (`Sine`,
  `Sawtooth`, `Square`, `Pulse`, … each with a `for i in
  range(params.norm_osc_blocksize)` loop), chained through
  `Filter`-subclass generators.
- Its anti-aliasing strategy is **additive band-limiting with a fixed
  partial count**: `SawtoothH` / `SquareH` take `num_harmonics=16`
  alongside the naive `Sawtooth` / `Square`. No BLEP, no wavetables.
- **It has no resonant filter at all.** Its `Filter` subclasses are
  `EnvelopeFilter` (ADSR), `AmpModulationFilter`, `MixingFilter`,
  `DelayFilter`, `EchoFilter`, `ClipFilter`, `AbsFilter`, `NullFilter` —
  amplitude, mixing and delay-line operations, every one of which is
  expressible without per-sample feedback. There is no VCF.

That absence is the finding. A pure-Python synth stops exactly where §3
says it must, and it is not a gap the author overlooked — it is the wall.
`synthplayer`'s ADSR is also a per-sample generator (`single_samples()`
yields `next(oscillator)*amp` with `amp += amp_change`), which is the
per-sample envelope of §4 written the expensive way.

The two projects that *do* have resonant filters both escape Python for
the recurrence: **torchsynth** (GPU-optional, PyTorch tensors, explicit
`ControlRateModule`/`ControlRateUpsample` split) and **FluidSynth**
(C, `FLUID_BUFSIZE = 64`, biquad with linearly-interpolated cutoff/Q).
`pyo` and `signalflow`, the two best-known Python audio-synthesis
libraries, are both C/C++ engines with Python bindings rather than
Python DSP.

**note-color's position is better than `synthplayer`'s**, because SciPy's
`lfilter` is a C recurrence reachable from NumPy — the escape hatch
`synthplayer` did not take. That is the one dependency this design turns
on.

## Recommendation

A concrete signal path for tickets #105/#106 to design against:

1. **Keep the 512-frame `sd.OutputStream` callback** (`playback.py`'s
   existing `LiveScheduler` shape and `config.PLAYBACK_BLOCK_SIZE`).
   Subdivide it internally into **64-sample control sub-blocks** —
   independently corroborated by FluidSynth's `FLUID_BUFSIZE` and by the
   10.7%-of-budget knee measured above.
2. **Oscillators: mip-mapped wavetables**, tables built once at startup
   by per-note additive synthesis, one band per octave, linear
   interpolation on read. 60–90 dB better than PolyBLEP *and* cheaper per
   block; the cost is a startup table build and a slightly duller top
   octave per band. Keep a PolyBLEP saw/square as the fallback for any
   waveform whose shape must change continuously (pulse-width
   modulation), where a table set does not apply.
3. **Filter: a 2-pole SVF's coefficient set, run through
   `scipy.signal.lfilter`** with per-voice persistent `zi`, coefficients
   recomputed per 64-sample sub-block from the control-rate cutoff/Q, and
   linearly interpolated across the sub-block as FluidSynth does. LP/HP/BP
   from the same structure. **Explicitly not the nonlinear ladder** —
   record that as a known limitation, not an oversight.
4. **Amp envelope at audio rate** (resumable `np.linspace` segments,
   0.13% of budget); **filter envelope and LFOs at control rate**, one
   value per sub-block, linearly upsampled — torchsynth's split, for
   torchsynth's reason.
5. **Voice state is the design.** phase / `zi` / envelope stage+position /
   LFO phase / note identity+velocity, per voice, mutated in place each
   block. This is what `playback.py` structurally lacks and what
   `LiveScheduler._active`'s `[samples, position]` must be replaced by.
6. **Model the parameter set on the SF2 generator list** (§1/§5), so the
   synth, sampler and SF2 engines share one voice model rather than three
   parallel vocabularies.

**Polyphony this buys, measured end to end** (experiment 4: two PolyBLEP
oscillators → mix → per-voice `lfilter` biquad → per-sample amp envelope
→ sum → `tanh`, 3000 consecutive blocks, GC left enabled, tail reported
because a real-time callback must hit its deadline on *every* block, not
on average):

| voices | mean | p99 | max | blocks over budget |
|---|---|---|---|---|
| 8 | 0.54 ms | 2.51 ms | 4.08 ms | 0 / 3000 |
| 16 | 0.98 ms | 3.52 ms | 5.00 ms | 0 / 3000 |
| 32 | 2.05 ms | 5.95 ms | 8.87 ms | 0 / 3000 |
| 48 | 3.14 ms | 8.29 ms | 10.59 ms | 0 / 3000 |
| 64 | 4.64 ms | 10.72 ms | 12.50 ms | **12 / 3000** |

**A safe polyphony target is 32 voices**, with 48 reachable and 64 the
point where this machine starts missing deadlines. Note the shape of that
distribution: **the max is 4–9× the mean**, so a design sized by mean cost
would be wrong by nearly an order of magnitude. 32 voices is comfortably
more than the map needs (a keyboard mode, a drum pad, and note audition
in the editor), and it leaves roughly 5ms/block of the budget for the
effects chain that research ticket #104 prices separately.

## Key risks/unknowns flagged for design

- **SciPy would become a new first-class dependency.** It is not in
  `pyproject.toml`'s core `dependencies` today; it arrives only
  transitively via `librosa` in the optional `[batch]` extra. Every
  number in §3 depends on `scipy.signal.lfilter`, and there is no pure
  NumPy substitute for an IIR recurrence — the FIR-truncation dodge
  (`np.convolve` against a truncated impulse response) is not viable for
  a resonant filter, whose impulse response is long precisely in
  proportion to its Q. This is a real decision for the implementation
  ticket, mitigated by the map's own "laptop-class hardware, not Pi"
  standing decision and by SciPy's excellent wheel coverage.
- **These numbers are one machine, and Python is the wrong end of the
  variance.** All measurements are an i5-7300U with GC enabled and no
  real audio device in the loop; a real `OutputStream` callback also
  competes with the GIL against the render thread and (in live views) the
  analysis thread. The p99/max columns are the honest ones, and even they
  do not include PortAudio's own jitter. Treat 32 voices as a
  measured-on-one-laptop figure to re-verify against real hardware, in
  this project's established "confirmed only on this dev machine" posture.
- **Wavetable table-build cost at startup is not measured here.** Per-note
  additive synthesis of ~10 octave bands × 4 waveforms × 2048 points is
  plainly small, but it is inside the synth tool's open-the-screen
  latency, and this document did not time it.
- **The mip wavetable's dullness at the top of each band is asserted, not
  auditioned.** The measured alias figures are unambiguous; whether the
  missing top harmonics are perceptible against a PolyBLEP saw is an
  ear question, and this document did not answer it. A prototype that
  A/Bs the two at the same pitch would settle it cheaply.
- **Zipper noise at 689 Hz coefficient update was not measured as an
  artifact**, only priced as a cost. FluidSynth interpolates cutoff/Q
  *within* the sub-block rather than stepping it, which suggests stepping
  at 689 Hz is not quite good enough on a fast sweep; the interpolation is
  cheap and should probably be built in from the start rather than added
  after someone hears it.
- **Velocity's source on QWERTY remains open** (map #99 says so
  explicitly). Nothing in this design forecloses it: velocity is a
  per-voice float feeding two amounts (§5), and a keyboard that supplies
  a constant 1.0 exercises the same code path a MIDI device later will.

## Sources

Primary sources, all read directly rather than via secondary write-ups:

- `/home/pelle/note-color/playback.py`, `/home/pelle/note-color/config.py`
  (`PLAYBACK_*`), `/home/pelle/note-color/pyproject.toml` — read in full.
- **MIDI 1.0 Control Change Messages (Data Bytes)** table — the MIDI
  Association's own table, text extracted from the PDF at
  `https://electronics.koncon.nl/wp-content/uploads/2022/02/Control-Change-Messages-Data-Bytes.pdf`
  (itself a mirror of `midi.org/specifications-old/item/table-3-control-change-messages-data-bytes-2`);
  CC 70–79 default names quoted verbatim from that extraction.
- **SoundFont 2 generator enumerators** — FluidSynth's own generator
  documentation, `https://www.fluidsynth.org/api/group__generators.html`
  (the SF2 2.04 spec PDF is at `http://www.synthfont.com/sfspec24.pdf`).
- **FluidSynth source** —
  `https://github.com/FluidSynth/fluidsynth/blob/master/src/rvoice/fluid_iir_filter.c`
  and `.h` (biquad state `{b02,b1,a1,a2}`, `fres_incr`/`q_incr` linear
  smoothing over `FLUID_BUFSIZE`), and
  `src/utils/fluidsynth_priv.h` (`#define FLUID_BUFSIZE 64`).
- **torchsynth source** —
  `https://github.com/torchsynth/torchsynth/blob/main/torchsynth/module.py`
  (`ControlRateModule`, `ControlRateUpsample`) and `torchsynth/config.py`
  (`control_rate=441` against `sample_rate=44100`).
- **`synthplayer` source** — `codeberg.org/irmen/synthesizer`,
  `synthplayer/params.py` (`norm_osc_blocksize = 512`),
  `synthplayer/oscillators.py` (per-sample generator oscillators;
  `SquareH`/`SawtoothH` additive band-limiting; the complete `Filter`
  subclass list, containing no resonant filter),
  `synthplayer/synth.py`.
- **Välimäki & Huovilainen**, "Antialiasing Oscillators in Subtractive
  Synthesis," *IEEE Signal Processing Magazine* 24(2):116–125, 2007
  (`https://ieeexplore.ieee.org/document/4117934/`) — the PolyBLEP
  introduction and the bandlimited / quasi-bandlimited / alias-reducing
  taxonomy.
- **Stilson & Smith**, "Alias-Free Digital Synthesis of Classic Analog
  Waveforms," ICMC 1996, CCRMA
  (`https://ccrma.stanford.edu/~stilti/papers/blit.pdf`) — BLIT.
- **Zavalishin**, *The Art of VA Filter Design*, Native Instruments
  (`https://www.native-instruments.com/fileadmin/ni_media/downloads/pdf/VAFilterDesign_1.1.1.pdf`)
  — TPT/zero-delay-feedback SVF and ladder.
- **Simper**, *Solving the continuous SVF equations using trapezoidal
  integration*, Cytomic
  (`https://www.cytomic.com/files/dsp/SvfLinearTrapOptimised2.pdf`).
- **All timing and aliasing figures**: `scripts/synth_engine_bench.py`,
  committed with this document; run with this repo's `.venv` on an Intel
  i5-7300U @ 2.60GHz (4 threads), NumPy 2.5.2, SciPy 1.18.0. Numbers
  quoted are that run's actual output, not estimates.
