# Prototype: real-time voice-mixing throughput under a real audio callback

Answers [issue #100](https://github.com/pellepang/note-color/issues/100)
(map [#99](https://github.com/pellepang/note-color/issues/99)): **how many
simultaneous voices can a Python/NumPy `sounddevice.OutputStream` callback
sustain on laptop-class hardware without underruns**, with a filter and an
effects chain in the path.

[#103](https://github.com/pellepang/note-color/issues/103) and
[#104](https://github.com/pellepang/note-color/issues/104) priced the pieces
in synthetic timing loops (`scripts/synth_engine_bench.py`,
`scripts/effects_chain_bench.py`) and predicted **32 safe / 48 reachable /
64 missing deadlines**. This prototype assembles their two recommendations
into one engine and runs it inside an **actual `sd.OutputStream` callback
against an actual audio device**, so it can report the one thing a timing
loop structurally cannot: **driver-reported xruns**.

Self-contained; imports nothing from note-color (only `numpy`, `scipy`,
`sounddevice`). Throwaway — it exists to produce the numbers below.

## Files

- `engine.py` — the signal path #103/#104 recommend, made resumable and
  block-driven: mip-mapped wavetable oscillator ×2 (detuned) → per-voice
  resonant biquad run by `scipy.signal.lfilter` with persistent `zi`,
  coefficients recomputed per **64-sample control sub-block** → audio-rate
  resumable ADSR → sum to a mono bus → shared `Delay` → `Chorus` → `tanh`.
  Every stage is independently switchable so the harness can price it.
- `harness.py` — the demo. Six experiments through a real output stream.

## How to run it

```
cd ~/note-color
.venv/bin/python prototypes/voice-mixing-throughput/harness.py
```

Takes about 90 seconds and **makes sound** (a detuned saw cluster at low
gain) — that is the point; it is real audio out of a real device.

## What it actually produced (real output, not hypothetical)

Intel i5-7300U @ 2.60GHz, Arch Linux, PipeWire (`default` ALSA device),
NumPy 2.5.2, SciPy 1.18.0, sounddevice 0.5.6, Python 3.14, GC enabled.
`over-budget` counts callbacks whose own render exceeded the block deadline;
`xruns` is PortAudio's own `output_underflow` flag.

### 1. Voice sweep, full path, 512-frame blocks (11.61 ms deadline)

```
configuration                     mean     p50     p95     p99     max   budget     load   over-budget  xruns
1 voices                          3.06    2.73    6.35    7.90    9.07    11.61    26.3%     0/258         0
8 voices                          3.89    3.45    6.79    8.78   11.73    11.61    33.5%     1/258         0
16 voices                         4.75    4.21    8.33   12.56   16.12    11.61    40.9%     4/258         0
24 voices                         5.38    4.71    8.81   17.04   25.63    11.61    46.3%     6/258         0
32 voices                         6.32    5.82    9.81   11.92   21.64    11.61    54.4%     4/258         0
40 voices                         7.52    7.22   11.29   15.06   18.29    11.61    64.8%    10/258         0
48 voices                         9.35    8.85   13.91   26.55   30.27    11.61    80.5%    45/258         2
64 voices                        12.26   12.16   16.29   18.00   22.11    11.61   105.6%   158/258        11
```

**The headline finding is the gap between the two rightmost columns.** Blocks
routinely blow the nominal deadline *without producing an xrun*: at 32 voices,
4 of 258 callbacks took longer than 11.61 ms and the stream never underran.
The reason is visible in experiment 3 — PortAudio reports a **34.8 ms stream
latency**, roughly three 512-frame blocks of ring buffer, and a callback that
overruns simply spends buffer the previous fast callbacks banked. Deadline
misses are therefore a *statistical* risk, not an instant glitch, which is
exactly why #103's advice to size by the tail rather than the mean is right —
and also why "zero over-budget blocks" is the wrong bar. The right bar is
zero xruns.

By that bar, on this machine: **40 voices sustained cleanly; 48 is marginal
(0 xruns on one run, 2 on the next); 64 fails outright** (61% of callbacks
over budget, 11 xruns in 3 seconds). #103's synthetic prediction of "32 safe,
48 reachable, 64 misses deadlines" **survives contact with a real driver
essentially intact** — and this despite the engine here being strictly more
expensive than #103's own experiment 4 (which ran one `lfilter` call per voice
per *block*; this one runs eight, per the 64-sample sub-block recommendation).

### 2. What each stage costs, at 32 voices

```
configuration                     mean     p50     p95     p99     max   budget     load   over-budget  xruns
effects bus only (0 voices)       1.73    1.55    3.06    4.16    5.37    11.61    14.9%     0/258         0
oscillators only                  3.59    3.15    6.99    8.91   12.50    11.61    31.0%     1/258         0
+ filter @ block rate             3.99    3.69    7.03    9.83   11.88    11.61    34.3%     1/258         0
+ filter @ 64-sample sub          5.97    5.52    9.24   10.84   18.87    11.61    51.4%     1/258         0
+ effects bus (full path)         6.98    6.08   11.20   23.81   27.63    11.61    60.1%    11/258         0
```

- **The 64-sample control sub-block is the single most expensive decision in
  the design.** Going from block-rate coefficients to 64-sample sub-blocks
  costs ~2.0 ms/block at 32 voices — nearly 50% more than everything else the
  filter does, and 17% of the whole budget. #103 priced this at 10.7% of
  budget for 16 voices; at 32 it is ~17%. It is the price of not having zipper
  noise, and it is real money.
- **The effects bus is nearly free, as #104 promised** — 1.73 ms standalone,
  and roughly 1.0 ms of marginal cost when added on top of 32 voices. #104
  measured ~120 µs for its delay→chorus chain; the difference here is the
  3-tap chorus plus per-block allocation, and it is still under 10% of budget.
  A shared bus, not per-voice, is confirmed as the right call at full load.
- **Oscillators are ~30% of budget at 32 voices** and are no longer the
  negligible item #103 measured per-voice.

### 3. Block size / latency tradeoff, 32 voices, full path

```
configuration                     mean     p50     p95     p99     max   budget     load   over-budget  xruns
block 128                         1.82    1.31    4.19    5.25   11.39     2.90    62.8%   144/1033        0   stream latency 34.8ms
block 256                         3.34    2.64    6.24    7.75   17.13     5.80    57.6%    37/516         0   stream latency 34.8ms
block 512                         6.97    6.07   12.04   22.48   28.67    11.61    60.0%    16/258         0   stream latency 34.8ms
block 1024                       13.07   12.62   17.84   31.30   40.06    23.22    56.3%     4/129         0   stream latency 46.4ms
```

Two things fall out.

- **CPU load is flat across block sizes (56–63%).** NumPy's per-call overhead
  does not dominate even at 128 frames, so smaller blocks are not
  fundamentally more expensive here — but the *fraction of callbacks that miss
  their own deadline* rises sharply (14% at 128 frames vs 3% at 1024), because
  a fixed ~1 ms of Python/OS jitter is a much bigger share of a 2.9 ms budget
  than of a 23.2 ms one.
- **Shrinking the block did not shrink the latency.** 128, 256 and 512 all
  reported the same 34.8 ms stream latency — PipeWire's own buffering, not the
  callback size. Going *below* 512 buys nothing measurable on this stack while
  making every deadline tighter. **512 (`config.PLAYBACK_BLOCK_SIZE`, already
  the value in the repo) is the right choice and should not be reduced.**

### 4. Oscillator choice, 32 voices, oscillators only

```
wavetable saw x2                  3.53    3.23    6.76    9.10   11.54    11.61    30.4%     0/258         0
polyblep saw x2                   3.21    2.70    6.34    7.53    8.73    11.61    27.6%     0/258         0
```

A correction to #103. #103 measured the mip wavetable as *cheaper* than
PolyBLEP (32.6 µs vs 51.1 µs per block) — true for **one voice reading a 1-D
table**. Vectorised across a voice array, each voice needs its own mip band,
so the read becomes a 2-D fancy-index gather `tables[level[:,None], idx]`,
and the gather costs more than PolyBLEP's branch-free arithmetic. At 32 voices
the wavetable is ~10% *slower*. This does not overturn #103's recommendation —
the wavetable is still 60–90 dB cleaner and the difference is 0.3 ms — but the
"cheaper *and* better" framing only holds per-voice, not per-block.

### 5. GIL contention with the app's own analysis thread

The one risk #103 explicitly flagged as unmeasured. `AnalysisLoad` runs
2048-point FFT work at ~86 hops/s — note-color's real analysis-thread cadence
— in a background Python thread while the callback renders 32 voices.

```
configuration                     mean     p50     p95     p99     max   budget     load   over-budget  xruns
full path + 0 analysis thr        6.72    6.13   11.29   17.85   25.85    11.61    57.9%    10/258         0
full path + 1 analysis thr        8.02    6.92   14.82   25.27   39.89    11.61    69.1%    32/258         1
full path + 2 analysis thr        8.92    7.32   21.50   31.34   33.48    11.61    76.8%    48/258         5
```

**One competing analysis thread costs about 1.3 ms/block at the mean and 7 ms
at p99, and is enough to turn a clean 32-voice run into an xrunning one.** The
tail is hit far harder than the mean: p95 nearly doubles. This is the concrete
form of the live views' "sonify what the mic detects" feature — synth and
detection running in one process — and it means **the polyphony budget is not
one number**: it is ~40 voices for the standalone `synth` tool and the score
editor (nothing else running), and closer to **24 voices when the live
analysis pipeline is also running**.

### 6. Per-voice cost

```
   mean:   142.2us/voice +  2.42ms fixed   -> deadline reached at  64.6 voices
    p99:   212.8us/voice +  8.53ms fixed   -> deadline reached at  14.5 voices
    max:   241.7us/voice + 12.32ms fixed   -> deadline reached at  -2.9 voices
```

**~140 µs/voice at the mean, ~210 µs/voice at p99**, over a fixed overhead of
~2.4 ms (the effects bus, the block's own allocation, and NumPy call overhead
that does not scale with voice count). The three rows disagree by more than
4× on where the deadline falls, which is the same "max is 4–9× the mean" shape
#103 found, now confirmed on real hardware — the `max` fit is literally
negative, i.e. *some* callback blows the deadline at any voice count. That is
not a reason to panic (see experiment 1: the driver buffer absorbs it) but it
is a reason never to size this engine by mean cost.

## Verdict

**Yes — the design #103 recommends survives real-world deadlines, at roughly
the polyphony it predicted, with two amendments.**

1. **Sustainable polyphony is ~40 voices standalone, ~24 with the live
   analysis pipeline also running** (experiments 1 and 5). #103's "32 safe"
   is a good headline number; it is *not* safe if the detection thread is
   live, and the voice manager's default polyphony should probably be a
   setting rather than a constant.
2. **Per-block NumPy synthesis holds up.** No structural rewrite is needed —
   at 32 voices the full path is 54% of budget with zero xruns.
3. **Voice stealing must be driven by a hard cap, not by load measurement.**
   Because the driver buffer absorbs individual overruns, the engine gets no
   feedback signal when it is over budget until it is already xrunning. A
   fixed `polyphony` limit with oldest/quietest stealing is the only
   workable control.
4. **Keep the block at 512.** Smaller blocks did not reduce measured latency
   on this stack (PipeWire buffers ~34.8 ms regardless) and only tightened
   the deadline.
5. **The 64-sample control sub-block costs ~17% of the budget at 32 voices,**
   more than doubling the filter's price versus block-rate coefficients. It
   should be a patch- or engine-level setting, so a user on slower hardware
   (or a patch with no filter modulation at all) can buy polyphony back.

## Caveats

- One machine, one run each (a second full run agreed to within ~10%, except
  at the 48-voice knee, which flipped between 0 and 2 xruns — that is what
  "marginal" means).
- PipeWire's `default` ALSA device. A JACK or exclusive-ALSA path would have
  a different (probably smaller) buffer and would therefore be *less*
  forgiving of over-budget callbacks, not more.
- The amp envelope's stage transitions are evaluated at block boundaries
  rather than per sub-block — a simplification that is fine for throughput
  measurement and would need tightening in real code.
- No note-on/note-off scheduling, no voice allocation, no patch loading: this
  measures steady-state mixing cost, which is the ceiling the voice manager
  has to design against, not the voice manager itself.
