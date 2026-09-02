# SwiftF0 evaluation: feasibility, measured accuracy, measured latency (issue #80)

Investigation for [issue #80](https://github.com/pellepang/note-color/issues/80)
— "Investigate: SwiftF0 as a possible YIN replacement/supplement for live
pitch detection." Issue #80 defines four steps: (1) verify SwiftF0's
claimed dependency-free inference by reading its source, (2) benchmark
real inference latency on Pi-class hardware, (3) run it against this
project's own acoustic suites rather than the paper's speech/singing eval
sets, (4) report adopt / supplement / reject with numbers.

## Relationship to `swiftf0-source-verification.md` (read that first)

`docs/research/swiftf0-source-verification.md` already exists and already
answers **step 1**, correctly and in the negative: SwiftF0 hard-requires
`onnxruntime`, ships a bundled `model.onnx`, and has no NumPy-only
forward pass. **This document does not re-litigate that finding — it is
confirmed again here independently (same conclusion, same evidence) and
then built on.** What this document adds is everything that doc listed as
still open:

- **Step 2, partially**: real, measured per-hop inference latency — on
  this dev machine's x86 CPU, not on a Pi. Pi numbers are given as an
  explicitly-labelled *extrapolation*, not a measurement. Still open.
- **Step 3, in the offline/synthetic form**: SwiftF0 run head-to-head
  against `pitch_detect.detect_pitch()` on synthetic signals built with
  this repo's own conventions (`tests/test_pitch_detect.py`'s
  `make_tone()`, `scripts/acoustic_pipeline_test.py`'s additive-noise
  idea), across timbre profiles, registers and SNRs. **Not** a real
  speaker→mic acoustic run — see Caveats.
- **Two feasibility blockers the source-verification doc did not surface**:
  SwiftF0's 16kHz-only input requirement (which forces a resampler onto
  the live path), and its dormant maintenance status.
- **Step 4**: a concrete recommendation.

One correction of emphasis relative to that doc: it concluded SwiftF0's
accuracy numbers "are unaffected by this finding and remain a real, if
unconfirmed-on-this-project's-own-signal, data point." That confirmation
has now been attempted, and the result is **mixed, not simply positive** —
SwiftF0 is measurably *worse* than this project's YIN on clean signal and
measurably *far better* under noise. That changes the framing from
"better detector, too heavy" to "differently-shaped detector, too heavy
for the live path but genuinely complementary."

## Question

1. What is SwiftF0, concretely, as of 2026-09: licence, runtime,
   packaging on aarch64, model size, claimed accuracy and on what data,
   claimed latency, maintenance status?
2. Does it fit this project's real constraints — Pi-class CPU budget,
   wheel/build-toolchain risk, the live path's per-hop budget?
3. On this project's *own* kind of signal, does it actually beat
   `pitch_detect.detect_pitch()` on the two failure modes that motivated
   the interest — issue #69's low-register octave doubling and issue
   #71's behavior under broadband noise?
4. Replacement, optional backend, batch-only tool, or reject?

## Answer

### 1. What SwiftF0 actually is (verified, 2026-09-02)

| Property | Finding | How established |
|---|---|---|
| Licence | MIT | PyPI metadata + GitHub repo licence field |
| Paper | Nieradzik, *SwiftF0: Fast and Accurate Monophonic Pitch Detection*, arXiv:2508.18440, submitted 2025-08-25 | arXiv abstract page |
| Runtime | **ONNX Runtime.** `swift_f0/core.py` line 1 is `import onnxruntime`; every call routes through an `onnxruntime.InferenceSession` over a bundled `model.onnx`. No NumPy-only path, no fallback, no conditional import. | Read installed source (confirms `swiftf0-source-verification.md`) |
| Model size | `model.onnx` = 399,114 bytes (390KB); 95,842 parameters per the paper | `ls` on installed package; paper |
| Package size | `swift_f0` 476KB installed; `onnxruntime` **61MB installed** (22.1MB wheel), plus `protobuf`, `flatbuffers` | `du -sh` on throwaway venv |
| aarch64 wheels | **Yes.** onnxruntime 1.29.0 publishes `manylinux_2_28_aarch64` wheels for cp311, cp312, cp313, cp313t, cp314, cp314t. Raspberry Pi OS Bookworm is glibc 2.36 ≥ 2.28, so these install cleanly. `swift-f0` itself is `py3-none-any`. | PyPI JSON API, read directly |
| Python-version risk | **None found.** cp314 wheels exist; installing `onnxruntime` under this machine's Python 3.14.6 succeeded. | Direct install test |
| Input constraint | **16kHz only.** Anything else is resampled — and `core.py`'s resample path does `import librosa` and raises `ImportError` if absent. Model range is hard-limited to 46.875–2093.75 Hz. | Read `core.py` lines 232-245, class constants |
| Internal framing | STFT, 1024-sample frame, 256-sample hop, at 16kHz | `core.py` class constants |
| API shape | Whole-signal: `detect_from_array(audio, sr) -> PitchResult(pitch_hz[], confidence[], timestamps[], voicing[])`. There is no single-frame entry point. | Read `core.py` |
| Claimed accuracy | 91.80% harmonic mean at 10dB SNR; ~94.07% clean; "outperforms CREPE by over 12 percentage points"; 96.75%/93.52% octave accuracy | Paper abstract + this repo's `oss-landscape-pitch-detection.md`, which quotes the paper's tables |
| Claimed eval data | Bach10-mf0-synth, Vocadito, and the author's own synthetic **SpeechSynth** (phoneme-level TTS). Speech/singing-heavy; no played-instrument-through-a-mic set. | Paper |
| Claimed latency | 132.6ms per 5s clip on CPU, ~42x faster than CREPE. **Desktop only — no embedded/Pi figure anywhere in the paper or README.** | Paper, README |
| Maintenance | **Dormant.** 8 commits total; repo created 2025-07-08; last commit **2025-09-02** — exactly 12 months before this evaluation. Last PyPI release v0.1.2, 2025-07-24 (14 months). Single author. 184 stars, 3 open issues. Not archived. | GitHub API, read directly |

Two things worth flagging that follow from the table:

- **The last commit changed the ONNX graph after the last release.**
  `Chore: remove redundant ONNX nodes to simplify model graph (Fixes #2)`
  landed 2025-09-02; the newest PyPI artifact predates it (2025-07-24).
  So the model you get from `pip install swift-f0` is not the model at
  HEAD. A minor provenance wrinkle, not a blocker, but it means "the
  published package" and "the repo" are not the same artifact.
- **The 16kHz constraint is a live-path problem this project has already
  ruled on.** note-color runs at `config.SAMPLE_RATE = 22050`. SwiftF0's
  own resampler is `librosa.resample` — and `librosa` on the live path is
  precisely what `docs/DECISIONS.md` isolates to `batch_transcribe.py`/
  `rhythm_reanalysis.py` and forbids elsewhere. The alternatives are
  each their own decision: add `scipy` (not currently a dependency at
  all), hand-roll a 320/441 polyphase resampler, or change
  `config.SAMPLE_RATE` to 16000 globally — the last of which would
  invalidate the empirical calibration behind `YIN_THRESHOLD`,
  `YIN_SUBHARMONIC_MARGIN`, `chroma.py`'s weighting matrix,
  `multipitch.py`'s peak-picking constants and
  `MULTIPITCH_LOW_WINDOW_SIZE`. None is free.

### 2. Measured latency (x86 desktop — NOT Pi)

Measured on this dev machine: Intel Core i5-7300U @ 2.60GHz, 4 threads,
x86_64, Linux. Throwaway venv (`uv venv --python 3.12`), Python 3.12.14,
`swift-f0==0.1.2`, `onnxruntime==1.29.0`, `numpy==2.5.2`,
`scipy==1.18.1`. Nothing was installed into the project `.venv` and
nothing was added to `pyproject.toml`. 200 iterations each after two
warm-up calls; ONNX session configured as the package ships it
(`inter_op_num_threads=1`, `intra_op_num_threads=1`, CPU provider).

| Operation | mean | median | p95 |
|---|---|---|---|
| `pitch_detect.detect_pitch()` on 2048@22050 (incl. `compute_spectrum()`) | **0.544ms** | 0.359ms | 1.357ms |
| — of which `compute_spectrum()` alone | 0.048ms | 0.039ms | 0.080ms |
| `SwiftF0.detect_from_array()`, 1486 samples @16k (same ~93ms of context, 5 model frames) | **3.982ms** | 3.852ms | 5.896ms |
| `SwiftF0.detect_from_array()`, 512 samples @16k (minimal, 2 model frames) | 1.192ms | 1.008ms | 2.426ms |
| `scipy.signal.resample_poly(x, 320, 441)` on a 2048-sample window | **2.687ms** | 2.458ms | 5.015ms |
| `import onnxruntime` (one-time) | 186ms | — | — |
| `SwiftF0()` session construction (one-time) | 12ms | — | — |

**Per-hop budget check.** `config.BLOCK_SIZE = 512` at 22050Hz is a
**23.2ms hop interval** — the analysis thread's entire budget per hop,
shared with chroma folding, multipitch, the smoothers, the duration
tracker and the reanalysis buffer. Today YIN uses 0.54ms of that on this
machine, and the chord pipeline's own documented worst case is ~3ms/hop
*on a Pi Zero 2 W*.

A live `SwiftF0Backend` would need resample + inference every hop:
**~6.7ms/hop on this desktop** (2.7 + 4.0), versus YIN's 0.54ms — a
**~12x** cost increase for the monophonic stage alone. That is still
inside 23.2ms on x86, so it would work on a desktop.

**Extrapolation to Pi-class hardware (labelled: NOT measured).** A Pi
Zero 2 W (Cortex-A53 @ 1GHz) is typically 5–10x slower than this i5 on
single-threaded scalar/NEON work. That puts the SwiftF0 path at roughly
**33–67ms/hop**, i.e. **1.4x to 2.9x over the entire 23.2ms hop
interval**, before any of the existing pipeline runs at all. On that
extrapolation the analysis thread cannot keep up and the drop-oldest
queue starts shedding audio. A Pi 4/5 (Cortex-A72/A76, 1.5–2.4GHz, wider
cores) would likely land at ~2–4x this desktop, i.e. ~13–27ms/hop —
marginal, possibly workable, definitely not comfortable.

**This is the single most important open question in this document and
it needs real hardware.** The extrapolation is a scaling estimate, not a
benchmark. It is however consistent in direction with the paper itself,
which never claims an embedded figure.

### 3. Measured accuracy against this project's own signal type

Method: synthetic, offline, no audio hardware — the repo's own "synthesize
the signal, no binary fixtures" convention (`tests/test_chroma.py`'s
`make_tone()`, `tests/test_pitch_detect.py`). Tones are RMS-normalized
sums of harmonics; both detectors get the *same* tone at their own native
rate (YIN: 2048 samples @22050; SwiftF0: 1486 samples @16000 — identical
~92.9ms of context) so no resampler sits between them and confounds the
comparison. Noise is additive white Gaussian at a stated SNR in dB.
`fmin`/`fmax` = 65/1000Hz for both, matching `config.FMIN`/`FMAX`.
Sweep is every semitone from MIDI 36 (C2) to MIDI 83 (B5) — 48 notes,
3 random-phase trials each, n=144 per cell.

"Correct" = within 50 cents. "Octave error" = within 100 cents of exactly
±1200 cents off. "Wrong-and-confident" = >50 cents off *and* reported
confidence ≥ 0.5 — the failure mode issue #71 specifically eliminated.

#### 3a. Accuracy vs. SNR (SwiftF0 at its default 0.9 voicing threshold)

| Condition | | reported | silent | correct | octave err | wrong+confident | median abs cents |
|---|---|---|---|---|---|---|---|
| clean | YIN | 144 | 0 | **100.0%** | 0.0% | 0.0% | **0.08** |
| | SwiftF0 | 126 | 18 | 87.5% | 0.0% | 0.0% | 9.28 |
| 20dB | YIN | 144 | 0 | **100.0%** | 0.0% | 0.0% | 0.49 |
| | SwiftF0 | 120 | 24 | 83.3% | 0.0% | 0.0% | 9.43 |
| 10dB | YIN | 144 | 0 | **100.0%** | 0.0% | 0.0% | 2.67 |
| | SwiftF0 | 124 | 20 | 86.1% | 0.0% | 0.0% | 9.27 |
| 5dB | YIN | 0 | **144** | 0.0% | 0.0% | 0.0% | — |
| | SwiftF0 | 127 | 17 | **88.2%** | 0.0% | 0.0% | 8.97 |
| 0dB | YIN | 0 | **144** | 0.0% | 0.0% | 0.0% | — |
| | SwiftF0 | 112 | 32 | **77.8%** | 0.0% | 0.0% | 8.91 |

Restricted to octave 2 only (MIDI 36–47, issue #69's register):

| Condition | YIN correct | SwiftF0 correct |
|---|---|---|
| clean | 100.0% | 100.0% |
| 10dB | 100.0% | 100.0% |
| 5dB | **0.0% (all silent)** | **100.0%** |
| 0dB | **0.0% (all silent)** | **86.1%** |

Three things fall out, and they point in opposite directions:

1. **YIN's noise cliff is real, sharp, and exactly where issue #71 said
   it was.** Between 10dB and 5dB SNR, `detect_pitch()` goes from 100%
   correct to 100% silent. That is not a bug — it is issue #71's fix
   working as designed (report nothing rather than report a confident
   wrong note), and the "wrong+confident = 0.0%" column confirms the fix
   holds at every SNR tested. But the practical consequence is that below
   ~10dB SNR this app currently detects *nothing at all*, and SwiftF0
   detects 78–88% of notes correctly, at every register including the
   low one. **This is the strongest single argument in SwiftF0's favour
   and it is a measurement, not a claim.** It independently corroborates
   the paper's noise-robustness result on a signal type the paper never
   tested.
2. **SwiftF0 is worse than YIN on clean signal, on this project's
   signal.** 87.5% vs 100%, and it silently drops 12–17% of notes even
   with no noise at all. Its pitch precision is also ~100x coarser:
   median 9.3 cents vs YIN's 0.08 cents. (For *this* app the precision
   gap is largely academic — everything is quantized to a semitone for
   colour — but the drop rate is not.)
3. **SwiftF0 does not deliver an octave-doubling advantage here.** On
   clean synthetic tones, issue #69's already-shipped subharmonic fix
   gives YIN 100% with zero octave errors across the whole 4-octave
   sweep. There is no headroom for SwiftF0 to win. (The real-world #69
   failure was mic-coloration-dependent and cannot be reproduced without
   a physical speaker→mic session, so this test cannot rule *out* a
   SwiftF0 advantage on real audio either — it just finds none here.)

#### 3b. Raw model behavior with the voicing gate removed

Running SwiftF0 with `confidence_threshold=0.0` (so every frame's raw
prediction is scored, exposing what the model actually says rather than
what its voicing gate lets through), across four timbre profiles,
sustained and exponentially-decaying, n=144 each:

| Timbre profile | | correct | octave err | other err |
|---|---|---|---|---|
| 4-harmonic (this repo's `make_tone()` convention) | SwiftF0 | 92.4% | **7.6%** | 0.0% |
| | YIN | **100.0%** | 0.0% | 0.0% |
| 4-harmonic, decaying | SwiftF0 | 91.7% | **8.3%** | 0.0% |
| | YIN | **100.0%** | 0.0% | 0.0% |
| pure sine | SwiftF0 | 88.2% | 2.1% | 9.7% |
| | YIN | **100.0%** | 0.0% | 0.0% |
| sawtooth-ish (8 harmonics, 1/n) | SwiftF0 | **100.0%** | 0.0% | 0.0% |
| | YIN | **100.0%** | 0.0% | 0.0% |
| odd-harmonic (clarinet-like) | SwiftF0 | 95.8% | 0.0% | 4.2% |
| | YIN | **100.0%** | 0.0% | 0.0% |

Per-note inspection makes the mechanism concrete. On the 4-harmonic
profile, SwiftF0's raw output on A♯3 (233.08Hz) is 463.35Hz and on B3
(246.94Hz) is 490.64Hz — **exact octave doublings**, the same failure
class issue #69 fixed in YIN. Also D♯4→616.97Hz and F♯4→746.78Hz. In
every one of these cases the model's own confidence sagged (0.42–0.77)
and the default 0.9 voicing gate correctly suppressed the answer, which
is why 3a shows 0.0% octave errors and a high silence rate instead:
**SwiftF0's headline octave-accuracy is partly a voicing gate declining
to answer, not the model being right.** Lowering the threshold to 0.7
raises clean accuracy from 83.3% to 91.7% and stops there — the residual
failures are the octave doublings, which no threshold recovers.

The honest caveat, stated plainly: **these synthetic tones are
out-of-distribution for SwiftF0.** It was trained on speech, singing and
music with augmentation — not on 4-exact-harmonic algebraic tones with
no attack transient, no vibrato and no noise floor. A mathematically
perfect harmonic stack is arguably a harder, weirder input for a learned
model than for an autocorrelation method, and the sawtooth row (100%,
the most instrument-like profile tested) is evidence for exactly that.
So 3b should be read as *"SwiftF0 has no octave-error immunity on
synthetic tones"*, **not** as *"SwiftF0 octave-doubles on real
instruments"* — that second claim would need real audio. Conversely,
3a's noise result is the one that generalizes most safely, because
"add white noise to a periodic signal" is a distortion neither method is
specially fitted to.

### 4. Fit against the `detection_backends.py` seam

The seam introduced per `architecture-modernization-plan.md` §3.1 is
exactly the right place for this, and a `SwiftF0Backend` would satisfy
`MonoPitchBackend` cleanly in shape — the Protocol asks for
`(freq_hz | None, confidence)` and nothing algorithm-specific. Three
mechanical mismatches are worth writing down before anyone scopes it:

1. **The Protocol is per-window; SwiftF0's API is per-signal.** A backend
   must call `detect_from_array()` on each hop's window and reduce the
   ~5 returned frames to one `(freq, confidence)`. That is what the
   benchmark above does (median over voiced frames). It works, but it is
   wasteful: at a 512-sample/22050Hz hop, only ~1.5 of SwiftF0's own
   256-sample/16kHz frames are actually new each hop, so ~70% of the
   inference is recomputed over audio already seen. A backend that
   maintained its own 16kHz ring buffer and ran a minimal window
   (1.19ms measured, 2 frames) instead of a full one (3.98ms) would
   recover most of that — worth scoping, not free.
2. **The resampler has nowhere to live.** It is not a
   `MonoPitchBackend` concern (the Protocol hands the backend the app's
   22050Hz window), so either the backend owns a private resampler or
   `analysis_loop()` grows a rate conversion. This is the same category
   of decision as `multipitch.select_window()`'s deliberate exclusion
   from `PolyphonicBackend` — algorithm-specific plumbing that the
   architecture doc explicitly warns against pushing into the Protocol.
3. **`compute_spectrum()`'s shared FFT is wasted.** YIN, chroma and (via
   its own windowed variant) multipitch all reuse it. SwiftF0 does its
   own STFT inside the ONNX graph over its own 16kHz audio, so a
   SwiftF0 backend shares nothing with the rest of the hop. Replacing
   YIN outright would not let `compute_spectrum()` be removed — chroma
   still needs it — so the 0.54ms YIN currently costs is not even
   recovered in full.

## Recommendation

**Reject for the live path. Adopt-as-optional-backend is not recommended
yet either, and should stay gated. Batch-only is the one genuinely
defensible slot, and even that is not worth starting without a concrete
complaint.**

Reasoning, in the order that decides it:

1. **The Pi budget is the binding constraint and it is very likely
   blown.** ~6.7ms/hop measured on a desktop i5, against a 23.2ms hop
   interval that already carries the chord pipeline (~3ms on a Pi Zero
   2 W) and everything else. The 5–10x Pi scaling extrapolation puts it
   1.4–2.9x over the entire hop budget on the smallest supported target.
   This project's founding decision rejected `aubio`/`librosa` on the
   live path for exactly this class of reason.
2. **The dependency is real and it is not just onnxruntime.** 61MB
   installed for a 390KB model, *plus* a resampler that this project
   does not currently have and cannot take from `librosa` without
   breaking its own isolation rule. The wheel *risk* is genuinely fine —
   verified aarch64 manylinux_2_28 wheels for cp311–cp314, which is
   materially better than aubio/essentia — but "installs cleanly" is not
   "affordable."
3. **It is not better on this project's signal in the general case.**
   87.5% vs 100% clean, with a 12–17% silent rate and demonstrable raw
   octave doublings suppressed only by a voicing gate. Swapping YIN out
   would be a measured regression in the conditions this app spends most
   of its time in.
4. **Maintenance status argues against a hard live-path dependency.**
   One author, 8 commits, no release in 14 months, no commit in 12, and
   a published wheel whose model no longer matches HEAD. Fine for an
   opt-in offline tool; poor for something on the critical path of every
   frame.
5. **But the noise result is real and should not be discarded.** Below
   ~10dB SNR this app detects *nothing* and SwiftF0 detects 78–88%
   correctly. If a concrete complaint ever arrives of the form "it stops
   working in a noisy room," SwiftF0 is now the best-evidenced candidate
   answer on file, and this document is the measurement to start from.
   That complaint does not currently exist — consistent with this
   project's own repeatedly-stated posture of not chasing a fix without
   a reproduced symptom (issues #68, #70, #75).

### What a follow-up implementation ticket would need to scope

Only if one of the gates above opens. In rough dependency order:

1. **A real Pi-class latency measurement** (Pi Zero 2 W and Pi 4/5,
   `swift-f0==0.1.2` + `onnxruntime` aarch64 wheel, the 1486-sample and
   512-sample window cases from §2). This single number decides whether
   any live-path variant is discussable at all. Nothing else should start
   before it.
2. **A resampler decision**, which is a human call (see Open questions).
3. **A real speaker→mic acoustic run** through
   `scripts/acoustic_pipeline_test.py`'s `chromatic` and `noise` suites
   with a `SwiftF0Backend` swapped in via `SessionState.pitch_backend` —
   the thing this synthetic evaluation explicitly cannot substitute for,
   and the only way to test the issue #69 real-mic failure mode that
   motivated the interest in the first place.
4. **If batch-only**: a `SwiftF0` mono backend behind an explicit
   `virtualnote transcribe` flag, in its own isolated-import module
   following the `librosa`/`music21` precedent exactly, with
   `onnxruntime` in a new `[project.optional-dependencies]` extra
   (not `batch` — a Pi user installing `[batch]` for `--write-score`
   should not be made to pull 61MB of ONNX Runtime). This is the same
   shape `detection-systems-survey.md` recommendation 5 already scopes
   for basic-pitch, and the two should be evaluated together rather than
   as separate tickets, since they need identical isolation treatment.

### Explicitly considered and rejected

- **Replace `pitch_detect.detect_pitch()` outright.** Rejected on the
  measured clean-signal regression (§3a/§3b) alone, before the Pi budget
  or the dependency even enter into it. Issue #80's own framing
  ("replacement/supplement") is answered: not a replacement.
- **Run SwiftF0 alongside YIN as a live cross-check** (issue #80's
  "supplement" option). Rejected: it costs strictly *more* than
  replacement (both detectors every hop), and the two disagree in a way
  that gives no obvious arbitration rule — YIN is more accurate when it
  answers, SwiftF0 answers more often under noise, and there is no cheap
  signal that says which regime you are in. A confidence-gated fallback
  ("use SwiftF0 only when YIN returns `None`") is the one arbitration
  rule that is actually principled and cheap, since YIN returning `None`
  *is* the noise-cliff signal — but it still needs SwiftF0 loaded and
  runnable on the target hardware, so it is blocked behind the same Pi
  measurement and buys nothing until that lands. Recorded here as the
  strongest live-path variant if the gate ever opens, not as a
  recommendation now.
- **Change `config.SAMPLE_RATE` to 16000 to remove the resampler.**
  Rejected as disproportionate: it would invalidate the empirical
  calibration behind `YIN_THRESHOLD`, `YIN_SUBHARMONIC_MARGIN`,
  `chroma.py`'s Gaussian weighting matrix, `multipitch.py`'s peak-picking
  constants and `MULTIPITCH_LOW_WINDOW_SIZE` — several of which
  (issues #63, #69, #71) took real measurement effort to arrive at — in
  service of an optional backend that isn't adopted.
- **Vendor the ONNX model and hand-roll a NumPy forward pass** to dodge
  `onnxruntime`. Not evaluated in depth, and not recommended: 95,842
  parameters through hand-written NumPy convolutions would very plausibly
  be *slower* than ONNX Runtime's optimized kernels, not faster, and it
  would fork a dormant upstream's model with no update path. Recorded as
  considered because the "pure NumPy" framing that started this
  investigation makes it the obvious next thought.

## Open questions (human decisions, not guesses)

1. **Is a new resampler dependency acceptable on the live path, and
   which?** `scipy` (not currently a dependency; large, but excellent
   aarch64 wheels) vs. a hand-rolled 320/441 polyphase filter (no new
   dependency, but new DSP code to get right and calibrate) vs. never.
   Measured cost of the `scipy` option: 2.687ms/hop on desktop — by
   itself already 5x what YIN currently costs.
2. **What Pi hardware is actually in scope for the "must run on" claim?**
   The extrapolation's verdict differs materially between a Pi Zero 2 W
   (clearly over budget) and a Pi 5 (marginal). CLAUDE.md says "Raspberry
   Pi class" and "64-bit Bookworm+" without naming a floor model.
3. **Does the "stops working in a noisy room" complaint actually exist?**
   Nothing in the issue tracker or `docs/DECISIONS.md` records one. §3a
   says the app is genuinely deaf below ~10dB SNR, which is a real
   property that has apparently never been complained about — possibly
   because real playing rooms are quieter than 10dB SNR, in which case
   SwiftF0's main advantage never applies in practice.

## Caveats on this evaluation

- **Everything in §3 is synthetic and offline.** No microphone, no
  speaker, no room, no `--source loopback` run, no
  `scripts/acoustic_pipeline_test.py` execution. The harness was read
  (for its noise-suite structure and synthesis conventions) but not run —
  it drives real audio hardware, which this environment does not have.
- **Everything in §2's Pi column is extrapolated, not measured.** The
  x86 numbers are real; the 5–10x scaling factor is a general estimate
  for Cortex-A53 vs. Kaby Lake and could be off by 2x in either
  direction. Issue #80 asked specifically for Pi numbers and this
  document still does not supply them.
- **SwiftF0 was tested through its public API at its shipped defaults**,
  with the one deliberate exception of §3b's `confidence_threshold=0.0`
  probe. No retraining, no threshold tuning beyond the reported sweep,
  no attempt to feed it audio shaped more like its training
  distribution.
- **n is small by ML-evaluation standards** (144 per cell). Adequate to
  separate 0% from 88%, or 100% from 87.5%; not adequate to resolve, say,
  86.1% from 88.2% as a real difference.

## Reproducing

The benchmark scripts live in this session's scratchpad, not in the repo
(per this project's convention that machine-specific measurements are not
committed). They are ~120 lines total and are fully described by §2 and
§3's method paragraphs: create a throwaway venv, `pip install swift-f0
scipy`, add the repo root to `sys.path`, and drive `detect_pitch()` and
`SwiftF0.detect_from_array()` over `make_tone()`-style signals. Nothing
was installed into the project `.venv`; `pyproject.toml` is unmodified.

## Sources

Primary, read directly this session:

- SwiftF0 paper: Nieradzik, arXiv:2508.18440 — https://arxiv.org/abs/2508.18440
- SwiftF0 repo (commit log, stars, issues, licence, README) —
  https://github.com/lars76/swift-f0 , plus the GitHub API
  (`repos/lars76/swift-f0`, `/commits`) for exact commit dates.
- `swift-f0` PyPI metadata (versions, dates, licence, requires-python,
  dependencies, wheel names/sizes) — https://pypi.org/pypi/swift-f0/json
- `onnxruntime` PyPI release files (aarch64 manylinux_2_28 wheel
  availability, cp311–cp314) — https://pypi.org/pypi/onnxruntime/json
- The installed package source itself: `swift_f0/core.py` (14,822 bytes),
  `swift_f0/__init__.py`, `swift_f0/model.onnx` — read in a throwaway
  venv, not from PyPI metadata.

This project's own files, read before writing: `CLAUDE.md`,
`docs/research/swiftf0-source-verification.md` (in full),
`docs/research/oss-landscape-pitch-detection.md`,
`docs/research/detection-systems-survey.md`, `pitch_detect.py`,
`config.py`, `pyproject.toml`, `tests/test_pitch_detect.py`,
`scripts/acoustic_pipeline_test.py`, and issue #80 including both of its
comments.
