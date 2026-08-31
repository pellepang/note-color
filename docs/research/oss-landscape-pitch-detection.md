# Monophonic pitch/F0 detection: open-source & state-of-the-art landscape

Research to inform whether note-color's hand-rolled YIN (`pitch_detect.py` —
pure NumPy, FFT-autocorrelation, parabolic interpolation, sub-harmonic
octave-doubling correction per issue #69, threshold-based unvoiced-frame
rejection per issue #71) should borrow a technique, take on a library
dependency, or stay as-is. No ticket number yet assigned; this doc is
survey/synthesis research, not a design doc for a specific feature.

## Question

1. What do classical DSP pitch trackers (aubio's five algorithms, pYIN,
   TarsosDSP, essentia, Praat/parselmouth) actually report for accuracy —
   cents error, octave-error rate, voicing recall/precision — against
   standard eval sets, and what do they cost computationally?
2. Same question for ML-based trackers (CREPE, torchcrepe, SPICE, and
   any newer lightweight neural alternative), specifically: is any of
   them realistically real-time on Pi-class hardware, and what's the
   packaging/dependency weight (model download, compiled runtime)?
3. Does anything exist specifically built/measured for microcontrollers
   or Raspberry-Pi-class embedded pitch detection?
4. Given note-color's actual constraints (Pi portability as a hard
   requirement, pure NumPy strongly preferred, <150ms end-to-end latency
   budget, and two already-fixed, well-characterized bugs — issue #69's
   low-register octave-doubling, issue #71's noise false-confidence) —
   is there a concrete technique or dependency worth adopting, or is the
   current approach already competitive?

## Answer

### 1. Classical DSP: a lot of prior art, and one paper that benchmarks nearly all of it together

The single most useful source found for this whole question is **SwiftF0**
(Nieradzik, 2025, arXiv:2508.18440 — see Sources), a recent lightweight-CNN
pitch paper whose Table 1/2/3 directly benchmarks nine algorithms —
five classical DSP, four neural — on the *same* three held-out datasets
(Bach10-mf0-synth, Vocadito, and the paper's own SpeechSynth) under both
clean and 10dB-SNR-noisy conditions, plus CPU wall-clock runtime for a
5-second clip. This is a rare apples-to-apples comparison and is quoted
directly below rather than reconstructed from each project's own
(usually incompatible) self-reported numbers.

**Classical DSP results** (Praat via parselmouth, RAPT via SPTK, SWIPE via
SPTK, YAAPT via pYAAPT, pYIN via librosa):

| Algorithm | Clean HM | Noisy (10dB) HM | Clean OA (octave acc.) | Noisy OA | CPU time / 5s clip |
|---|---|---|---|---|---|
| Praat (autocorrelation) | 90.13 | 76.10 | 84.37 | 56.55 | 7.0ms |
| RAPT | 82.53 | 74.70 | 75.58 | 60.65 | 13.6ms |
| SWIPE | 81.33 | 71.43 | 63.76 | 85.13 | 133.3ms |
| YAAPT | 78.80 | 65.30 | 59.86 | 36.54 | 318.5ms |
| pYIN | 85.27 | 79.99 | 77.88 | 66.83 | 1420.6ms |

("HM" is the paper's own 6-metric harmonic mean — RPA/Cents-Accuracy/
Voicing-Precision/Voicing-Recall/Octave-Accuracy/Gross-Error-Accuracy —
not a standard MIR metric on its own, but useful as a single number to
rank by; "OA" specifically penalizes octave confusions, the failure mode
most relevant to note-color's issue #69.)

Two findings worth flagging explicitly:

- **pYIN is not free lunch on octave accuracy** in this benchmark — 66.83%
  clean-noisy-condition OA, actually *worse* than plain autocorrelation
  Praat's 84.37% clean OA and RAPT's 75.58%. This cuts against pYIN's
  general reputation (its own ICASSP 2014 paper and multiple third-party
  write-ups report octave-error rates around 0.5–1.7% on singing/speech
  test sets — see Sources — a very different number, almost certainly
  because "octave error rate" there is measured only on already-voiced,
  more speech/singing-like material, not this paper's noisier synthetic
  mix). The two results aren't directly comparable (different datasets,
  different noise conditions, different exact metric), but together they
  say pYIN's octave-robustness reputation is **evaluation-set-dependent**,
  not an unconditional property — exactly the kind of caveat this project
  already applies to its own YIN fix (issue #69's docstring: "confirmed
  only synthetically... a real-mic re-verification is still pending").
- **Praat's plain windowed-autocorrelation method is startlingly cheap**
  (7ms for 5s of audio — ~700x faster than real-time on this benchmark's
  desktop) and clean-condition-competitive (90.13 HM, matching or beating
  pYIN), but its accuracy craters under noise (76.10 HM, and octave
  accuracy specifically drops to 56.55%) — a direct, measured illustration
  of exactly the tradeoff note-color's own issue #71 write-up already
  describes qualitatively for a simple threshold-based approach under
  degraded periodicity evidence.

**aubio** (yin/yinfft/mcomb/schmitt/fcomb) has no entry in the above
benchmark (not included by that paper), but a monophonic-singing-specific
study found ordering by accuracy: **YinFFT 56% > MComb 52% > FComb 49% >
plain Yin 37% > Schmitt 23%** correct-pitch-contour rate, with the caveat
that post-processing (median filtering etc. — which note-color's own
`note_smoother.py` already layers on top of raw YIN) improved every
algorithm's usable accuracy "by more than five times" over the raw
per-frame numbers (source: ResearchGate summary of a real-time monophonic
singing pitch detection study — see Sources; exact paper/dataset not
independently re-verified beyond this secondary summary). Notably,
**plain YIN is aubio's worst performer of the four "real" algorithms**
in that ranking — YinFFT (a spectral-domain variant) and the comb-filter
methods beat it. aubio's own docs also confirm this is its considered
default: yinfft, not yin, ships as `aubiopitch`'s default method.

**TarsosDSP** (Java) implements YIN, a "FastYIN" (FFT-accelerated
difference function — essentially the same optimization note-color's own
`_difference_function()` already does), MPM ("A Smarter Way to Find
Pitch," McLeod & Wyvill), AMDF, and a Dynamic Wavelet method — no
head-to-head accuracy numbers were found for these against each other or
external benchmarks; it's presented as a practical toolkit, not a
research/benchmarking project. Not directly relevant to note-color (Java,
not Python) beyond MPM being a second algorithm family worth knowing
exists.

**essentia**'s `PitchYinFFT` (spectral-domain YIN, matches aubio's
yinfft) and `PitchMelodia` (contour-tracking, adapted from the
polyphonic-melody-extraction MELODIA algorithm for monophonic use) are
documented but no independent accuracy numbers were found for either in
this research pass; essentia is a large C++ library (its own build/wheel
story on Pi is not meaningfully lighter than aubio's) and wasn't
investigated further given no clear accuracy edge found.

### 2. ML-based: CREPE is the well-known baseline, but a 2025 successor changes the calculus for a Pi-class budget

**CREPE** (Kim/Salamon/Li/Bello, 2018) — six-layer CNN over 1024-sample
raw audio, 360 pitch bins spanning six octaves at a fixed 20-cent
resolution, trained on RWC/MDB-STEM-Synth/etc. Reports 96.7%/95.3%/90.9%
RPA at 50/25/10-cent thresholds on MDB-STEM-Synth, and is described in its
own related-work summaries as achieving "state-of-the-art accuracy" with
strong noise robustness relative to pre-2018 baselines. Its cost is real:
**~22M parameters**, and in the SwiftF0 paper's own direct CPU benchmark,
the (PyTorch, i.e. `torchcrepe`) full-capacity model took **5.5 seconds of
CPU time to process a 5-second clip** — i.e. *not* real-time on ordinary
desktop CPU, let alone Pi-class hardware, without smaller-capacity model
variants (CREPE ships tiny/small/medium/large/full presets — smaller
presets trade accuracy for speed, e.g. one third-party report puts a
486k-parameter variant at 91.5% RPA vs. the full 22M-parameter model's
higher number, still needing GPU or a very forgiving latency budget to
be practical). Packaging: needs TensorFlow (original) or PyTorch
(`torchcrepe`) plus downloaded model weights — categorically the kind of
"heavy build toolchain + runtime + model download" dependency note-color's
CLAUDE.md already rules out for exactly this reason (aubio was rejected
for *less* wheel risk than this).

**SPICE** (Google, self-supervised, 2019) — reports 90.6% RPA on MIR-1K
and 89.1% on MDB-stem-synth, "matching CREPE's accuracy despite no
ground-truth labels during training." Ships as a TensorFlow Hub /
TensorFlow-Lite model specifically because Google targeted mobile/web
deployment for it — meaningfully lighter than full CREPE, but still a
TFLite-runtime dependency, not a pure-NumPy option, and no CPU-cycle
numbers comparable to the SwiftF0 paper's table were found for it in this
pass.

**basic-pitch** (Spotify, 2022) is polyphonic/multi-pitch note-and-onset
transcription, not a monophonic F0 tracker, but worth noting for its
*efficiency* story since it's the most Pi-plausible neural option found
in this whole survey: **~17K parameters, <20MB peak memory, ~20x faster
than CREPE**, using CQT input rather than raw audio or STFT. In the
SwiftF0 benchmark specifically evaluated as a monophonic tracker anyway,
it performs poorly (29–31 HM, far behind every DSP method) — expected,
since it isn't built for that task; only worth citing as evidence that a
sub-20K-parameter CQT/CNN architecture *can* be made to run fast on CPU,
not as a usable option for note-color as-is.

**PENN** (Morrison et al., 2023) — a CREPE-accuracy-improving successor
(1440 bins at 5-cent resolution vs. CREPE's 360 at 20-cent, entropy-based
periodicity estimation), optimized specifically for real-time CPU
performance. In the SwiftF0 table: 89.23 clean HM (close to Praat/pYIN),
but only 59.87 noisy HM — the single steepest clean→noisy accuracy drop
of any algorithm benchmarked (a full 29.4-point fall, vs. SwiftF0's 2.3
points) — and 919ms CPU time for 5s audio (6x faster than full CREPE,
but still ~5.5x slower than real-time by wall clock, and ~7x slower than
SwiftF0). Genuinely faster than CREPE, but its own noise fragility and
still-sub-real-time CPU cost make it a weaker fit than the option below.

**SwiftF0** (Nieradzik, 2025, arXiv:2508.18440, MIT-licensed,
`pip install swift-f0`, GitHub `lars76/swift-f0`) is the standout finding
of this research pass and deserves its own callout:

- **95,842 parameters** — three orders of magnitude smaller than CREPE's
  22M, a five-layer 2D-CNN over an STFT magnitude spectrogram (Hann
  window, 1024-point FFT, 256-sample hop at 16kHz — i.e. the same
  "windowed-FFT-then-classify" shape note-color's own
  `pitch_detect.compute_spectrum()` already produces, just consumed by a
  small CNN instead of YIN's difference function).
- **132.6ms CPU time for a 5-second clip** in the paper's own benchmark —
  a **42x speedup over CREPE**, and comfortably real-time on the
  benchmark's "standard desktop computer with a modern CPU." No Pi-class
  hardware number exists yet (not measured by the paper, and not found
  elsewhere in this research pass) — this is an extrapolation risk, not
  a confirmed number, same caveat this project already applies to its own
  Pi-class latency assumptions elsewhere.
- **Accuracy**: highest harmonic mean in both the clean (94.07%) and noisy
  (91.80%) conditions of every algorithm tested, including CREPE, pYIN,
  Praat, and PENN — and specifically the *most stable* under noise (only
  a 2.27-point HM drop clean→noisy, vs. CREPE's ~1.6-point drop from a
  lower baseline, PENN's 29.4-point drop, or Praat's 14-point drop).
  Octave accuracy specifically: 96.75% clean / 93.52% noisy — the
  highest of any algorithm in the table, including every classical DSP
  method.
- **Packaging**: pip-installable, `numpy` as the only confirmed hard
  dependency (librosa/matplotlib/mido are optional, for audio loading and
  visualization/MIDI export, not inference); no `onnxruntime`,
  TensorFlow, or PyTorch dependency was found in its PyPI metadata or
  README, meaning inference likely runs via a **hand-rolled NumPy
  forward pass** rather than requiring a heavy DL runtime — the same
  "pure NumPy, no build toolchain" property note-color's own
  `pitch_detect.py` already insists on. (Caveat: this project did not
  fetch and read SwiftF0's actual inference source code to confirm the
  forward-pass implementation directly — this is inferred from the
  absence of a runtime dependency in its packaging metadata, not verified
  by reading `swift_f0/*.py` line-by-line. If pursued, that file is the
  first thing to actually open.)
- Its own frequency-range restriction — 46.875Hz–2093.75Hz (G1–C7),
  chosen specifically to prune 74% of STFT bins for efficiency — is
  structurally the same idea as note-color's own issue #74 fix
  (`config.FMIN`/`FMAX`, 65–1000Hz, bounding `multipitch.detect()`'s
  candidate peaks) applied to a different algorithm family. Convergent
  evidence that "narrow the frequency search range to the instrument's
  real register" is a sound, cheap technique regardless of which
  underlying pitch algorithm it's paired with.

### 3. Raspberry Pi / microcontroller-specific pitch detection: mostly plain YIN/FFT reimplementations, no novel algorithm found

No microcontroller- or Pi-specific *algorithm* research was found —
every embedded/tuner project located (ESP32 guitar tuners via Arduino
Forum/GitHub, a TM4C123GXL-based tuner, various Arduino writeups) uses
either **plain YIN** (the exact same "delay-and-compare, find the minimum-
difference lag" approach note-color already implements, just without
FFT-based autocorrelation acceleration in the smallest microcontroller
cases) or a straightforward **FFT-then-peak-pick**, at sample rates and
buffer sizes chosen for that specific chip's ADC/memory limits (e.g. one
project: 125kHz ADC sampling, 600 samples/100ms window). None reported
comparable accuracy benchmarks to the desktop-eval papers above, and none
described anything conceptually beyond what note-color's own YIN already
does (arguably note-color's version is more sophisticated, given its
FFT-autocorrelation speedup, parabolic interpolation, and the two
empirically-validated bug fixes from issues #69/#71 — none of the
embedded-tuner writeups found mention an equivalent sub-harmonic or
noise-false-positive correction). **Conclusion: this space validates
note-color's existing approach (hand-rolled, FFT-accelerated YIN is
exactly what constrained-hardware practitioners independently converge
on) but offers no additional borrowable technique.**

## Synthesis: what, if anything, should note-color borrow?

**Recommendation: no dependency change, but one concrete algorithmic
technique is worth prototyping — SwiftF0's approach, if its inference can
genuinely run on plain NumPy without a DL runtime, is the one option in
this whole survey that doesn't trip note-color's own stated hard
constraints. Everything else surveyed either fails the Pi/latency budget
outright or offers no measured advantage over what's already shipped.**

Specifics, in order of how seriously each is worth pursuing:

1. **Do nothing to the core algorithm, but reconsider `YIN_THRESHOLD`
   framing.** Given Praat's own benchmarked result (90% clean HM at
   near-zero cost, but octave accuracy collapsing to 56.55% under noise)
   directly mirrors the exact tradeoff note-color's own issue #71 fix
   already reasoned through empirically (a 0.12–0.30 threshold sweep
   finding "zero recoverable margin" at `moderate` noise) — this is
   independent, external confirmation that the project's own conclusion
   ("an honest statistical limit, not a bug to chase further") is
   consistent with what happens to *every* threshold-based autocorrelation
   method under real noise, not a sign the implementation is deficient.
   No action needed beyond noting this in `docs/DECISIONS.md` if useful
   context is wanted there.

2. **SwiftF0 is worth a scoped, throwaway prototype, not an immediate
   dependency swap.** If (and only if) reading its actual source confirms
   inference runs as a plain NumPy forward pass over a small stack of
   5x5 conv layers (5 layers, channel counts 8→16→32→64→1, per its own
   architecture diagram) with no ONNX/TF/PyTorch runtime requirement, this
   is a genuinely pure-NumPy-compatible option that:
   - Measured 42x faster than CREPE and real-time on desktop CPU already
     (132.6ms/5s clip) — the open question is purely whether that holds
     up on Pi-class hardware, not whether it's fast on a desktop.
   - Reports the best octave-accuracy and noise-robustness numbers of
     any algorithm in the one paper that benchmarks it against pYIN/
     Praat/CREPE/PENN head-to-head — directly relevant to note-color's
     own two hardest-fought bugs (#69 octave-doubling, #71 noise false-
     confidence), since a differently-failing algorithm might sidestep
     both failure modes rather than needing hand-tuned margins/thresholds
     to correct for them after the fact.
   - Would still need real validation against note-color's own actual
     acoustic test suites (`scripts/acoustic_pipeline_test.py`'s `noise`/
     `tempo` suites) before any adoption decision — the SwiftF0 paper's
     own eval sets (speech-heavy: PTDB-TUG, SpeechSynth/Mandarin TTS,
     MIR-1k singing) are not obviously representative of note-color's
     actual target signal (played musical instruments, not voice), and
     "best in this paper's benchmark" is not the same claim as "best for
     note-color's specific signal." A scoped prototype (run SwiftF0's
     released model against note-color's own existing synthetic YIN test
     fixtures and its `--source loopback` acoustic suites, no product
     integration yet) is the right next step, not a rewrite.
   - Real risk to flag honestly: even at 96k parameters, a NumPy conv
     forward pass every ~23ms hop (note-color's own `BLOCK_SIZE`/
     `SAMPLE_RATE` cadence) is a materially different computational shape
     than YIN's own array ops — worth an actual timed prototype on
     whatever Pi hardware is available before trusting the desktop-only
     132.6ms number scales down safely, exactly the same "measured, not
     assumed" discipline this project already applies everywhere else
     (see, e.g., issue #77's own explicit 3-8x desktop-to-Pi
     extrapolation caveat).

3. **aubio remains not worth the dependency risk** — this research
   reconfirms, rather than overturns, note-color's existing decision.
   Beyond the already-known wheel/build risk (piwheels lists only
   armv6l/armv7l wheels, capped at aubio 0.4.9 from Feb 2019 — no
   aarch64 wheel confirmed, and note-color's own CLAUDE.md targets
   64-bit/aarch64 Raspberry Pi OS Bookworm+ specifically), the actual
   *accuracy* case for switching is weak: the one comparative study found
   in this pass ranks plain aubio `yin` as the **worst** of its four real
   algorithms (37% correct-contour rate vs. yinfft's 56%), and aubio's own
   stable release has had no meaningful update since 2019 — a real
   maintenance-risk signal for a multi-year-horizon project, independent
   of the accuracy question.

4. **pYIN (via librosa) is already structurally unavailable** as a
   drop-in for the *live* pitch path regardless of its accuracy — it's
   the exact dependency note-color's CLAUDE.md already documents as
   "isolated to `batch_transcribe.py` and `rhythm_reanalysis.py` ...
   never imported by `main.py` or `analysis_loop()`'s own per-hop path
   directly." Its 1420.6ms-per-5s-clip CPU cost in the SwiftF0 benchmark
   (4x faster than CREPE, but still ~280ms/second of audio — nowhere
   close to this project's <150ms *end-to-end* budget for the whole
   pipeline, not just pitch detection) reconfirms that boundary was drawn
   in the right place; nothing in this research suggests revisiting it
   for the live path. (It remains perfectly fine, and already used,
   offline in `batch_transcribe.py`/`rhythm_reanalysis.py` — this doesn't
   change.)

5. **TarsosDSP's MPM ("A Smarter Way to Find Pitch")** is a genuinely
   different algorithm family (normalized square difference, not YIN's
   cumulative mean normalized difference) worth knowing exists as a
   *conceptual* alternative if a future YIN-shaped fix hits a wall — but
   no accuracy benchmark comparing it to YIN/pYIN/CREPE was found in this
   pass, and it's a Java implementation with no direct NumPy port found,
   so there's no concrete action to take on it now beyond flagging it for
   future reference.

## Sources

- SwiftF0 paper (primary source for nearly all comparative numbers
  above): Nieradzik, "SwiftF0: Fast and Accurate Monophonic Pitch
  Detection," arXiv:2508.18440, Aug 2025 — full PDF read directly
  (all 14 pages, including Tables 1–3, architecture diagram, and
  reference list), not a secondary summary.
  https://arxiv.org/abs/2508.18440 / https://arxiv.org/pdf/2508.18440
- SwiftF0 GitHub repo: https://github.com/lars76/swift-f0 (README
  fetched — parameter count, CPU latency, dependency claims cross-checked
  against the paper directly)
- SwiftF0 PyPI page: https://pypi.org/project/swift-f0/ (Python version
  support, package size)
- aubio pitch algorithm accuracy ranking (YinFFT/MComb/FComb/Yin/Schmitt):
  ResearchGate summary of "Real-time monophonic singing pitch detection,"
  https://www.researchgate.net/publication/361909956_Real-time_monophonic_singing_pitch_detection
  (secondary summary via search snippet, not independently re-verified
  against the primary paper's own tables)
- aubio manpage/docs (default algorithm = yinfft, algorithm descriptions):
  https://aubio.org/manpages/latest/aubiopitch.1.html
- aubio installing/dependency docs: https://aubio.org/manual/latest/installing.html
- aubio releases page (confirms 0.4.9, Feb 2019, is still the latest
  stable tag): https://github.com/aubio/aubio/releases
- piwheels aubio project page (confirms armv6l/armv7l wheels only, no
  aarch64 listed): https://www.piwheels.org/project/aubio/
- pYIN paper: Mauch & Dixon, "PYIN: A fundamental frequency estimator
  using probabilistic threshold distributions," ICASSP 2014 —
  https://webspace.eecs.qmul.ac.uk/s.e.dixon/pub/2014/MauchDixon-PYIN-ICASSP2014.pdf
  (octave-error-rate/voicing-recall figures quoted in Q1 are from search
  snippets referencing this paper's own tables, not independently
  re-extracted from the PDF in this pass — flagged as secondary)
- pYIN project page: https://code.soundsoftware.ac.uk/projects/pyin
- CREPE paper: Kim, Salamon, Li, Bello, "CREPE: A Convolutional
  Representation for Pitch Estimation," ICASSP 2018 —
  https://arxiv.org/abs/1802.06182 (MDB-STEM-Synth RPA figures via
  search-result summary, cross-confirmed as consistent with the SwiftF0
  paper's own characterization of CREPE's clean-condition accuracy)
- CREPE GitHub: https://github.com/marl/crepe ; capacity/parameter-count
  breakdown (486k/1.6M/5.9M/22M params at multipliers 4/8/16/32) via
  GitHub issue discussion, https://github.com/marl/crepe/issues/20
- torchcrepe: https://github.com/maxrmorrison/torchcrepe
- SPICE paper: Gfeller et al., "SPICE: Self-supervised Pitch Estimation,"
  arXiv:1910.11664, and Google AI blog announcement,
  http://blog.research.google/2019/11/spice-self-supervised-pitch-estimation.html
- basic-pitch: Bittner et al., "A Lightweight Instrument-Agnostic Model
  for Polyphonic Note Transcription and Multipitch Estimation," 2022,
  arXiv:2203.09893; Spotify Engineering blog,
  https://engineering.atspotify.com/2022/6/meet-basic-pitch ; GitHub
  https://github.com/spotify/basic-pitch (parameter count and CPU
  speed-vs-CREPE figures cross-confirmed against the SwiftF0 paper's own
  Table 3)
- PENN: Morrison, Hsieh, Pruyne, Ewert, "Cross-domain neural pitch and
  periodicity estimation," 2023 — figures in this doc are from the
  SwiftF0 paper's own benchmark of it, not PENN's own paper directly
- TarsosDSP GitHub / algorithm docs:
  https://github.com/ederwander/tarsosdsp ; MPM reference: McLeod &
  Wyvill, "A Smarter Way to Find Pitch"
- essentia algorithm reference (PitchYinFFT, PredominantPitchMelodia):
  https://essentia.upf.edu/algorithms_reference.html
- Parselmouth/Praat pitch documentation:
  https://github.com/YannickJadoul/Parselmouth ; Praat manual pages on
  autocorrelation/cross-correlation pitch analysis and octave-cost
  parameters
- Embedded/microcontroller pitch tuner examples (no novel algorithm
  found, cited only to support the "converges on plain YIN/FFT" Q3
  conclusion): ESP32 guitar tuner,
  https://github.com/LucasWanJZ/ESP32-Guitar-Tuner ; Arduino Forum
  thread, https://forum.arduino.cc/t/esp32-pitch-detection-for-musical-instruments-tuner/1409600
- This repo's own code, read directly for accurate citation of existing
  behavior/constants: `/home/pelle/note-color/pitch_detect.py` (read in
  full), `/home/pelle/note-color/config.py` lines 4-12 (`SAMPLE_RATE`,
  `BLOCK_SIZE`, `WINDOW_SIZE`, `FMIN`, `FMAX`, `YIN_THRESHOLD`),
  `/home/pelle/note-color/docs/research/live-noncausal-rhythm-reanalysis.md`
  (read in full, for this doc's tone/format convention)
