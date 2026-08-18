# Chord-mode compute-path latency budget

Research for ticket #8 (https://github.com/pellepang/note-color/issues/8),
part of the chord-mode planning epic (#1). Builds on the algorithm decided
in #2 (chroma computation) and #4 (template matching, on top of #3's
360-template dictionary).

## Question

Does chroma computation + template matching (as specified in #2/#4) fit
within the existing <150ms end-to-end latency target on Raspberry Pi-class
hardware, reusing the FFT already computed for YIN?

## Verdict

**Fits comfortably, with a large margin at every level** — added compute is
roughly four orders of magnitude smaller than the ~23ms-per-hop budget it
has to keep up with, even under deliberately pessimistic hardware-efficiency
assumptions for the weakest 64-bit-OS-capable Pi. No implementation risk
from raw compute cost. One real implementation detail is worth carrying
into the build: **precompute chord-template norms once at import time, not
per frame** (see below) — free, and the natural way to write the matching
code anyway.

## What has to fit, and in what budget

From `config.py`: `SAMPLE_RATE = 22050`, `BLOCK_SIZE = 512` (~23.2ms hop —
`512/22050`), `WINDOW_SIZE = 2048` (~92.9ms analysis window). The
analysis thread must finish one hop's work (YIN + chord mode, if enabled)
before the next block arrives, or the bounded drop-oldest queue
(`QUEUE_SIZE = 8`, `audio_capture.py`) starts shedding data — so the
relevant budget for this estimate is **the ~23ms hop interval**, not just
the 150ms end-to-end target (per the ticket's framing); 150ms end-to-end is
checked too, as the outer bound.

Per #2, chord mode reuses YIN's existing 2048-sample window, zero-padded to
a 4096-point FFT (`np.fft.rfft`, "next power of two ≥ 2×WINDOW_SIZE"),
which YIN already computes every hop regardless of chord mode. That FFT is
**not** new cost — it's charged to the pipeline already, and the existing
app already keeps up with the 23ms hop on its target hardware range without
chord mode. It's used here only as a sanity-check reference point (see
"Context: comparison to the existing FFT" below).

## Target hardware baseline

Repo docs (`CLAUDE.md`, `docs/DECISIONS.md`) specify only "64-bit Raspberry
Pi OS (Bookworm+)" as the low-power baseline, not a specific Pi generation.
64-bit Raspberry Pi OS requires an ARMv8-A core, which rules out the
original Pi Zero/Zero W and Pi 1/2 (ARM11/Cortex-A7) — the weakest
Pi model that actually qualifies is the **Raspberry Pi Zero 2 W**
(quad-core 64-bit Arm Cortex-A53, 1GHz, RP3A0 SiP — confirmed directly from
the official product page,
https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/). This is used
below as the conservative floor. For context, the **Raspberry Pi 4 Model B**
(Broadcom BCM2711, quad-core Cortex-A72, 1.8GHz — confirmed from the
official specifications page,
https://www.raspberrypi.com/products/raspberry-pi-4-model-b/specifications/)
is a much more typical real-world target and gives a substantially larger
margin; all numbers below use the Zero 2 W's weaker Cortex-A53 as the
pessimistic case.

Both are single-threaded-relevant here: this is one hop of work on the
analysis thread, not a workload worth parallelizing across the 4 cores.

## Operation-by-operation FLOP count, per hop

All three additions from #2/#4, counting each multiply and each add as one
FLOP (standard "2×rows×cols" convention for a matrix-vector product):

**1. Main chroma fold** (#2): `chroma_fb @ magnitude_spectrum`, where
`chroma_fb` is `(12, 2049)` — 12 pitch classes × the positive-frequency
half of the 4096-point `rfft` (`4096/2 + 1 = 2049` bins). This is the
`(12, 1025)` matrix described in #2's findings doc, scaled up from the
2048-point case (`2048/2+1=1025`) to the recommended 4096-point reuse case.
Cost: `2 × 12 × 2049 ≈ 49,176 FLOPs`.

**2. Bass chroma fold** (#4): same technique, restricted to the spectrum
below ~250Hz. Bin width at 4096 points / 22050Hz is `22050/4096 ≈ 5.383Hz`,
so ~250Hz falls at bin `250/5.383 ≈ 46.4` → the first **47 bins** (indices
0–46). A `(12, 47)` fold: `2 × 12 × 47 ≈ 1,128 FLOPs`. (Even the pessimistic
alternative — reusing the full `(12, 2049)` shape with the upper columns
zeroed rather than slicing — only doubles the total chroma-fold cost; still
negligible either way, so the estimate doesn't hinge on this
implementation choice.)

**3. Template matching** (#3/#4): cosine similarity between the observed
12-bin chroma vector and each of 360 templates (30 qualities × 12
rotations). As a single `(360, 12) @ (12,)` matvec: `2 × 360 × 12 ≈ 8,640
FLOPs` for the dot products. Plus, per hop: one 12-element norm for the
observed vector (~24 FLOPs, shared across all 360 comparisons), 360
divisions by `‖observed‖ × ‖template‖`, and a 360-way argmax. Call it
**~9,700 FLOPs** for the whole matching stage, generously counted.
(Bass-chroma tie-break logic in #4 is a handful of scalar comparisons on
top — noise-level, not counted separately.)

**Total added compute per hop: ~60,000 FLOPs (~60 kFLOPs).**

### Implementation detail worth flagging: precompute template norms

The 360 templates are fixed binary pitch-class masks (#3) that never change
at runtime — their norms (`‖template‖ = sqrt(popcount)`) are constant and
should be computed once at import time, not recomputed inside the per-hop
matching loop. Better still: since a template's norm depends only on how
many pitch classes its *quality* has (rotation doesn't change popcount),
there are really only **30 distinct norm values** (one per quality), not
360 — trivial to precompute and index by quality. This doesn't change the
timing verdict (the 360 sqrt calls it would otherwise cost are themselves
noise-level, a few thousand cycles), but it's a free correctness/cleanliness
win worth writing into the matching code from the start rather than as a
later optimization pass.

## Converting FLOPs to wall-clock time

A search for a directly-verifiable, reproducible NumPy/OpenBLAS GFLOPS
benchmark specific to Raspberry Pi Cortex-A53/A72 hardware did not turn up
a source that held up under direct verification (one forum thread a search
engine summarized as showing "~8 GFLOPS, 33% of peak" for a Pi 3B+, did
not, on direct fetch, actually contain that figure — so it's not used
here, per the instruction to avoid secondhand/unverified claims). Instead,
the estimate below is built bottom-up from confirmed clock speeds and
publicly-documented Cortex-A53 microarchitecture behavior, with an explicit,
generous efficiency derate for small operations — auditable rather than a
single opaque number.

**Theoretical peak, Cortex-A53 (Pi Zero 2 W, 1GHz):** per independent
architectural write-ups of the A53's NEON pipeline (destevez.net,
"Coding NEON kernels for the Cortex-A53"; chipsandcheese.com, "ARM's Cortex
A53: Tiny But Important"), the A53 has two 64-bit NEON half-units that
combine to execute one 128-bit NEON instruction per cycle (they cannot be
used to dual-issue two separate 128-bit ops) and supports fused
multiply-add. One 128-bit FP32 FMA = 4 lanes × 2 FLOPs = 8 FLOPs/cycle.
At 1GHz, single-core theoretical peak ≈ **8 GFLOPS**.

**Efficiency derate:** the operations here (a 12×2049 matvec, a 360×12
matvec) are small and dominated by loop/load-store overhead rather than
sustained FMA throughput — real-world efficiency for such small,
non-compute-bound NumPy/BLAS calls is commonly well under 10% of
theoretical peak. Using a deliberately pessimistic **1% efficiency**
floor: effective throughput ≈ 80 MFLOPS.

**Compute time:** `60,000 FLOPs / 80,000,000 FLOPs/sec ≈ 0.75ms`.

**Python/NumPy call overhead** (separate from raw FLOPs, per the ticket's
explicit ask — interpreted-language dispatch cost doesn't show up in a FLOP
count at all): a chord-mode hop issues on the order of ~10 NumPy calls
(two fold matmuls, a norm, a divide, an argmax, plus bass-chroma
bookkeeping). Per-call dispatch overhead on desktop x86 NumPy is typically
low single-digit microseconds; a 1GHz in-order Cortex-A53 running CPython
has meaningfully lower single-thread throughput and no out-of-order
execution to hide dispatch latency, so a generous **100µs/call** ceiling
(itself several times any figure actually reported for even slow
interpreted-dispatch NumPy on desktop hardware) is used here:
`10 × 100µs = 1ms`.

**Combined pessimistic per-hop total: ~1.75ms** (0.75ms compute + 1ms call
overhead), both figures deliberately over-padded.

## Margin against the budgets

| Budget | Value | Pessimistic estimate | Margin |
|---|---|---|---|
| Per-hop (must keep up with the audio stream) | ~23.2ms | ~1.75ms | ~13× |
| End-to-end target | <150ms | ~1.75ms (added to an unchanged pipeline) | ~85× |

On a Pi 4 (Cortex-A72 @ 1.8GHz, out-of-order, wider issue — theoretical
peak alone is well over 3× the Zero 2 W's before accounting for its faster
single-thread dispatch), the margin is larger still; the Zero 2 W numbers
above are the floor, not the expected case.

## Context: comparison to the existing FFT

As a sanity check independent of the derate assumptions above: the
4096-point real FFT that YIN already computes every hop (unconditionally,
chord mode or not) costs on the order of `5 × N × log2(N) ≈ 5 × 4096 × 12
≈ 245,760 FLOPs` by the standard real-FFT FLOP-count approximation — about
**4× more arithmetic than all of chord mode's added compute combined**
(~60k FLOPs). The existing pipeline already pays that FFT cost every hop
as part of YIN, on the same hardware range, without chord mode. Chord
mode's entire addition is smaller than a cost the app already absorbs
today.

## Sources

- `config.py` (this repo): `SAMPLE_RATE`, `BLOCK_SIZE`, `WINDOW_SIZE`.
- Ticket #2 findings: https://github.com/pellepang/note-color/blob/research/chroma-vector-computation/docs/research/chroma-vector-computation.md
  (FFT size 4096 via zero-padding, `(12, 1025)` chroma matrix for the
  2048-point case, `chroma_fb @ magnitude_spectrum` cost model).
- Ticket #3 resolution (30 qualities × 12 roots = 360 templates):
  https://github.com/pellepang/note-color/issues/3
- Ticket #4 resolution (cosine similarity matching, bass-chroma <250Hz
  fold, tie-break logic): https://github.com/pellepang/note-color/issues/4
- Raspberry Pi Zero 2 W official specifications:
  https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/
- Raspberry Pi 4 Model B official specifications:
  https://www.raspberrypi.com/products/raspberry-pi-4-model-b/specifications/
- Cortex-A53 NEON pipeline behavior (independent architectural analysis,
  used for the theoretical-peak derivation): "Coding NEON kernels for the
  Cortex-A53," https://destevez.net/2025/02/coding-neon-kernels-for-the-cortex-a53/ ;
  "ARM's Cortex A53: Tiny But Important,"
  https://old.chipsandcheese.com/2023/05/28/arms-cortex-a53-tiny-but-important/
