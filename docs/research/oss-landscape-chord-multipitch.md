# OSS/SOTA landscape: polyphonic multi-pitch detection and automatic chord recognition

Research request: survey existing open-source and state-of-the-art approaches
to multi-pitch detection and automatic chord recognition (ACR), report real
published accuracy numbers where available, and give a concrete, opinionated
recommendation for what note-color's chord-mode pipeline
(`chroma.py`/`multipitch.py`/`chord_templates.py`/`chord_smoother.py`) should
borrow, if anything, given this project's hard constraints: pure NumPy
strongly preferred, Raspberry-Pi-class real-time budget (<150ms end-to-end),
and a "no aubio/librosa in the live path" rule that (per `CLAUDE.md`) already
has two narrowly-scoped, deliberate exceptions
(`batch_transcribe.py`/`rhythm_reanalysis.py`, both offline or explicit-
on-demand-while-frozen, never the live per-hop path).

No ticket number yet assigned at time of writing — this is landscape
research, not yet tied to a specific proposed change.

## Question

1. What do classic chroma+template approaches (NNLS-Chroma/Chordino,
   librosa's `chroma_cqt`/`chroma_cens` + template matching, essentia's
   `ChordsDetection`) actually achieve, and how do they work internally?
2. What do ML-based multi-pitch/transcription systems (Spotify's
   basic-pitch, Magenta's Onsets-and-Frames/MT3, Omnizart, madmom's CNN/CRF
   chord recognition) achieve, and what do they cost computationally?
3. What do MIREX's public score histories for Audio Chord Estimation and
   Multiple-F0 Estimation & Tracking actually show, in real numbers?
4. Given note-color's constraints and already-documented open limitations,
   what specifically should this project adopt — a whole library, a
   lightweight technique borrowed from one, or nothing at all?

## Answer

### 1. Classic chroma + template approaches

**NNLS-Chroma / Chordino** (Matthias Mauch, QMUL, 2008-2010; still the
reference baseline most later papers compare against)
[[GitHub]](https://github.com/c4dm/nnls-chroma)
[[Isophonics]](https://isophonics.net/nnls-chroma):

- Pipeline: log-frequency spectrum → **non-negative least squares (NNLS)
  approximate note transcription** against a fixed dictionary of harmonic
  note profiles (geometrically decaying harmonic magnitudes) → fold the
  *transcribed note activations* (not the raw spectrum) into a 12-bin
  chroma vector → frame-wise chord-template cosine/correlation similarity
  → smoothing, either a simple heuristic "chord change" rule or a full
  HMM/Viterbi decode (default on) over the whole frame sequence.
- The key idea, and the paper's own explicit finding
  ([Mauch & Dixon, ISMIR 2010](https://www.eecs.qmul.ac.uk/~simond/pub/2010/Mauch-Dixon-ISMIR-2010.pdf)):
  doing an **approximate NNLS transcription pass before folding to chroma**
  raised overall chord-recognition accuracy from MIREX 2009's then-best
  74% to **80%** (+6 percentage points), with the gain concentrated
  specifically on harmonically ambiguous chord types — **up to +12
  percentage points** on the hardest chord classes. The stated mechanism:
  transcribing notes first (even approximately) resolves exactly the kind
  of harmonic-collision ambiguity a raw chroma vector can't — one
  detected note's higher harmonics landing on another pitch class's chroma
  bin and inflating/corrupting it.
- Computational character: NNLS is a per-frame convex optimization solve
  (iterative, not a closed form) against a fixed dictionary — heavier than
  a single FFT+matrix-multiply chroma fold, but still classical DSP/linear
  algebra, no training, no learned weights, and no reason it couldn't run
  causally frame-by-frame. **The default smoothing stage (HMM/Viterbi) is
  non-causal** — it decodes the globally-optimal chord sequence over an
  entire buffered recording, the same shape as note-color's own
  `rhythm_reanalysis.py`/`batch_transcribe.py` non-causal passes, not its
  live per-hop `chord_smoother.py`. Chordino does expose the simpler
  causal "chord change" smoothing as a non-default option for streaming
  use.

**librosa `chroma_cqt`/`chroma_cens` + template matching**: the "textbook"
baseline nearly every chord-recognition paper compares against and nearly
every one beats. No NNLS/approximate-transcription step, so it inherits
the same "raw harmonic content pollutes neighboring chroma bins" weakness
NNLS-chroma was built to fix. Already effectively superseded inside this
project — note-color's own `chroma.fold()` (Gaussian log-frequency
weighting summing 1st-4th harmonics, tuned to a narrow 0.25-semitone
sigma) is a more considered, purpose-tuned version of exactly this same
"weighted-sum harmonic folding" family, already validated (per
`CLAUDE.md`) against the specific large-chord-out-scoring-sparse-chord
failure mode a naive template match shows.

**Essentia's `ChordsDetection`/`ChordsDetectionBeats`**
[[docs]](https://essentia.upf.edu/algorithms_reference.html): frame-wise
HPCP (essentia's own chroma variant) → best-matching major/minor triad
template only — **no seventh chords, no jazz-symbol vocabulary, no
NNLS-style pre-transcription**. Strictly less capable than what
note-color's `chord_templates.py` already does (~360 templates, 30
qualities × 12 roots, slash-chord/inversion naming). Essentia itself
(C++ core, Python bindings) is real-time-capable and has been used on
embedded/Pi-class targets, but its chord-recognition algorithm
specifically is a weaker baseline than NNLS-chroma/Chordino, not a
stronger one — not worth adopting even setting aside the "avoid a big
new C++ dependency" concern.

### 2. ML-based multi-pitch/transcription and chord recognition

**Spotify's basic-pitch**
([ICASSP 2022 paper, arXiv:2203.09893](https://arxiv.org/abs/2203.09893);
[GitHub](https://github.com/spotify/basic-pitch)):

- Architecture: fully convolutional, built on a **Harmonic CQT (HCQT)**
  input (3 bins/semitone, 7 harmonics + 1 sub-harmonic stacked as a third
  tensor dimension so small conv kernels can see harmonically-related
  content directly) rather than a mel-spectrogram — deliberately chosen so
  the model doesn't need pitch-shifting data augmentation to generalize
  across instruments/registers.
- **Genuinely tiny by deep-learning standards: 16,782 parameters total.**
  This is a real, notable data point — orders of magnitude smaller than
  a typical transcription CNN.
- Accuracy: note-level F-measure (no offset) **70.9% on MAESTRO**
  (piano); frame-level accuracy 37.5% on MAESTRO (the paper's own
  headline claim is *cross-instrument generalization and state-of-the-art
  on GuitarSet*, not beating piano-specialist models on piano specifically).
  Substantially outperforms its own MI-AMT baseline across five
  instrument datasets while using ~4x less peak memory (951MB vs 3.3GB on
  a 7:45 test file) and ~4x less wall-clock (24s vs 96s on the same file,
  2017 MacBook Pro) — i.e. these efficiency numbers are relative to a
  *heavier* research baseline, not an absolute Pi-class benchmark.
- Ships pretrained weights in TensorFlow, CoreML, TFLite, and ONNX formats
  (platform-specific default: TFLite on Linux, ONNX on Windows, CoreML on
  macOS) and claims ~10x-faster-than-real-time inference on a modern
  desktop/laptop CPU. **No published Raspberry-Pi-class benchmark, and no
  causal/streaming inference mode** — the public model and reference
  implementation process a file (or a fixed buffered chunk) at a time;
  turning it into a genuinely causal <150ms-latency live detector would be
  a real reimplementation effort, not a drop-in.
- Runtime dependency: TFLite or ONNX Runtime — both are real, non-trivial
  binary dependencies with their own wheel/ABI risk on 32-bit or unusual-
  architecture Pi builds, exactly the category of risk `CLAUDE.md`
  already cites as the reason this project avoided `aubio`/`librosa` in
  the live path.

**Magenta Onsets-and-Frames / MT3**
([MT3 paper](https://arxiv.org/pdf/2111.03017),
[magenta/README](https://github.com/magenta/magenta/blob/main/magenta/models/onsets_frames_transcription/README.md)):
piano-specialist (Onsets-and-Frames, trained on MAESTRO) and
transformer-based multi-instrument (MT3) transcription. Both are TensorFlow
models sized for GPU training/inference, evaluated via Frame-F1/Onset-F1/
Onset-Offset-F1 on curated datasets; MT3 is "not definitively better than
Onsets-and-Frames" despite a much larger model and more training data per
a later comparison ("OaFS" — Onsets-and-Frames-Streaming — reportedly
outperforms MT3 on multiple datasets). **Desktop/GPU-class only** — no
credible path to Pi-class real-time, and not something this project's
own research needs to look past to reach a decision.

**Omnizart** ([JOSS paper](https://www.theoj.org/joss-papers/joss.03391/10.21105.joss.03391.pdf),
[GitHub](https://github.com/Music-and-Culture-Technology-Lab/omnizart)):
a multi-task toolkit (piano/instrument-ensemble transcription, vocal
melody, chord, drum, beat) built on deep models with TensorFlow
dependencies and pretrained-checkpoint downloads. Convenient as a
one-stop offline transcription tool, but architecturally the same
category as MT3/Onsets-and-Frames for this project's purposes — **desktop/
offline-batch only**, not a real-time Pi candidate, and its chord module
specifically isn't reported to beat NNLS-chroma/madmom-class systems.

**madmom's CNN/CRF chord recognition (Deep Chroma)**
([Korzeniowski & Widmer, ISMIR 2016](https://arxiv.org/abs/1612.05065);
[madmom docs](https://madmom.readthedocs.io/en/v0.16/modules/features/chords.html)):
a CNN learns chroma-like features directly from a log-frequency
spectrogram (replacing hand-crafted chroma folding), then a linear-chain
CRF decodes the most likely major/minor chord sequence. One commonly
cited evaluation number: **~80.4% accuracy** for the Deep Chroma chord
recognizer (in the range other MajMin-vocabulary systems report, and
roughly matching NNLS-chroma's own 80% figure above — corroborating that
80% MajMin accuracy is genuinely where the strongest systems in this
literature, ML and non-ML alike, cluster, not a number specific to one
paper's own eval protocol).

**The single most important madmom finding for this project specifically:**
madmom's neural-network module (`madmom.ml.nn`/`madmom.ml.rnn`) is a
**from-scratch pure NumPy/SciPy forward-pass engine** for pretrained
networks — it takes zero TensorFlow/PyTorch/ONNX dependency at inference
time; it exists specifically so madmom's shipped pretrained weights can
run without requiring whatever framework originally trained them. This
is architecturally the *one* ML-adjacent option in this whole survey that
doesn't reopen the "big ML runtime as a Pi wheel/ABI risk" concern
`CLAUDE.md` already flags for `aubio`/`librosa` — see the recommendation
below for how much weight that actually deserves.

### 3. MIREX public score history

**Audio Chord Estimation.** Directly fetched the 2020 results page
([music-ir.org/mirex/wiki/2020:Audio_Chord_Estimation_Results](https://music-ir.org/mirex/wiki/2020:Audio_Chord_Estimation_Results)):
**only one system was submitted that year** (HL2, Yuan-Hao Ku & Hsueh-Han
Lee) — MIREX ACE participation has clearly thinned out since its
mid-2010s peak, so a single year's numbers should be read as "one
system's score," not "the field's current best." Its scores by dataset
and vocabulary:

| Dataset | Root | MajMin | MajMinBass | Sevenths | SeventhsBass |
|---|---|---|---|---|---|
| RobbieWilliams (best) | 77.20 | 72.65 | 71.38 | 65.05 | 63.88 |
| USPOP2002Chords | 74.01 | 70.36 | 67.66 | 58.80 | 56.34 |
| RWC-Popular | 72.73 | 67.92 | 65.22 | 53.87 | 51.36 |
| Isophonics2009 | 71.21 | 66.97 | 65.63 | 57.75 | 56.58 |
| Billboard2012 | 69.41 | 65.94 | 64.90 | 53.69 | 52.75 |

Cross-checked against the earlier, better-attended era: NNLS-chroma's own
2010 paper reports 80% overall (vs. MIREX 2009's prior best of 74%), and
Deep Chroma-class CNN/CRF systems report ~80% MajMin in the mid-2010s
literature. **Reading these together: the field's practical ceiling for
MajMin-vocabulary chord recognition on real recorded pop/rock music has
sat around 75-80% for roughly a decade**, richer vocabularies (sevenths,
inversions/bass) scoring meaningfully lower (~55-65%), and that ceiling
hasn't moved dramatically even as the underlying technique shifted from
NNLS+templates to learned CNN features — diminishing returns, not a
solved problem. Recent (2023-2025) papers report full-chord-vocabulary
accuracy "above 75%" on pop music and ~68% on classical (BACHI), broadly
consistent with this decade-long plateau rather than a step-change past
it.

**Multiple Fundamental Frequency Estimation & Tracking**, directly
fetched from the 2019 results page
([music-ir.org/mirex/wiki/2019:Multiple_Fundamental_Frequency_Estimation_%26_Tracking_Results_-_MIREX_Dataset](https://music-ir.org/mirex/wiki/2019:Multiple_Fundamental_Frequency_Estimation_%26_Tracking_Results_-_MIREX_Dataset)):

- Task 1 (frame-level multi-F0 estimation, best system AR2): accuracy
  **0.690**, precision 0.748, recall 0.833.
- Task 2 (note tracking, onset-offset evaluation, mixed instrument set,
  best system KY1): F-measure **0.438** (precision 0.457, recall 0.432).
  Piano-only subset tops out similarly low (BK1: F 0.482).

**This is the single most load-bearing number for note-color's own
self-assessment**: even the best-performing systems MIREX has evaluated
for genuine note-level multi-pitch tracking (onset+offset, not just
frame-level pitch presence) sit in the **0.40-0.48 F-measure range** —
i.e., real multi-pitch/multipitch-tracking is still a long way from
"solved" across the field generally, not just in this project's own
hand-rolled pipeline. note-color's own documented open limitations
(residual harmonic-collision ambiguity between a root and a
3rd-harmonic-coincident note, density-recall gaps at 3-6 simultaneous
notes) are the same class of failure the whole field's best systems still
show measurable error on, not a sign of an under-tuned implementation
falling short of an otherwise-solved problem.

## Synthesis / recommendation

Three things given real weight, then a concrete call.

**1. MIREX and peer-reviewed numbers confirm note-color's own documented
limitations are the field's limitations, not this project's shortfall.**
Best-in-class multi-pitch *note tracking* F-measure tops out around
0.40-0.48; best-in-class chord recognition on a MajMin vocabulary has
plateaued around 75-80% for roughly a decade regardless of technique.
Concretely: the harmonic_number≤4 root/3rd-harmonic collision ambiguity
`docs/DECISIONS.md` documents as unresolved, and the "Am7 vs. C6
pitch-class-set ambiguity is inherent, not a bug" framing, are both
genuinely inherent to the problem — no system surveyed here, ML or
classical, claims to have solved either. **This should reduce, not
increase, the pressure to keep chasing these specific residual gaps** —
they're consistent with the state of the art, not below it.

**2. The one concrete, adoptable technique: NNLS-style approximate note
transcription before chroma folding.** This is the one idea in the whole
survey that (a) is pure signal processing, not ML — a per-frame NNLS
solve against a fixed harmonic dictionary, implementable with
`scipy.optimize.nnls` or a hand-rolled iterative NNLS in NumPy, no
training/weights/model file involved; (b) has a *measured, specific*
accuracy improvement directly relevant to note-color's own documented
weak spot — Mauch & Dixon's own ISMIR 2010 result (+6pp overall, **+12pp
specifically on harmonically ambiguous chords**) is exactly the failure
mode class this project's `docs/DECISIONS.md` already names (root/3rd-
harmonic collisions, dense-chord confusability); and (c) sits at
roughly the same place in the pipeline `chroma.fold()` already occupies —
this would be an enhancement/replacement of the folding step, not a new
pipeline stage. **This is worth a real prototype**, not a full adoption:
build a small NNLS-transcription-then-fold path, benchmark its per-hop
cost on real Pi Zero 2 W-class hardware (the existing acoustic test
suite's `chords`/`density` tiers are the right harness), and A/B its
chord-match accuracy against the current Gaussian-weighted `fold()` on
the same synthesized harmonic-collision cases `docs/DECISIONS.md`
already documents as unresolved. If the per-hop cost doesn't fit the
<150ms budget on real Pi hardware, that's a clean, evidence-based reason
to not pursue it further — but it hasn't been tried, and it's the one
technique in this survey with a directly-applicable, quantified track
record against this exact problem.

**3. Every ML-based system surveyed (basic-pitch, Onsets-and-Frames/MT3,
Omnizart, madmom's CNN/CRF) belongs in the offline/batch or
explicit-on-demand tier, if adopted at all — not the live per-hop path.**
This mirrors the boundary `librosa` already sits at in this codebase
(`batch_transcribe.py`/`rhythm_reanalysis.py`). basic-pitch is the most
tempting of these on paper — genuinely tiny (16,782 parameters) and fast
on desktop hardware — but its published numbers are desktop-CPU
benchmarks with no Pi-class measurement, no causal/streaming inference
mode exists in the reference implementation, and its runtime needs
TFLite or ONNX Runtime — real wheel/ABI risk on Pi, the exact category of
dependency `CLAUDE.md` already ruled out `aubio`/`librosa` for in the
live path. **Verdict: basic-pitch is realistic only as a future addition
to the already-offline `batch_transcribe.py` path** (e.g. an alternative,
optionally-selected transcription backend for `virtualnote transcribe`),
never a live-pipeline replacement for `multipitch.detect()`. madmom's
pure-NumPy neural-net inference engine (`madmom.ml.nn`) is the one ML
option that's philosophically compatible with this project's "no big ML
framework dependency" stance, since it needs no PyTorch/TensorFlow/ONNX
at inference — but it would still mean vendoring/depending on `madmom`
itself (a real, if lighter-weight, new dependency) and its pretrained
Deep Chroma weights, for a chord-recognition accuracy ceiling
(~80% MajMin) that isn't meaningfully higher than NNLS-chroma's own
non-ML 80% figure from a decade earlier — **not a clearly better
cost/benefit trade than the pure-NumPy NNLS-transcription idea above**,
so not recommended as a first move. Essentia's `ChordsDetection` is
strictly weaker than what `chord_templates.py` already does (triads
only, no sevenths/slash-chord naming) and isn't worth adopting under any
framing.

**Bottom line:** don't adopt a library. Prototype NNLS-based approximate
note transcription as an enhancement to `chroma.fold()`'s input, measure
it against real Pi-class hardware and against the specific harmonic-
collision cases already documented as open limitations, and treat every
ML-based system in this survey as, at most, a future offline
`batch_transcribe.py` backend option — never a live-pipeline change.

## Sources

- [c4dm/nnls-chroma (GitHub)](https://github.com/c4dm/nnls-chroma)
- [Chordino and NNLS Chroma (Isophonics)](https://isophonics.net/nnls-chroma)
- [Mauch & Dixon, "Approximate Note Transcription for the Improved
  Identification of Difficult Chords", ISMIR 2010 (QMUL PDF)](https://www.eecs.qmul.ac.uk/~simond/pub/2010/Mauch-Dixon-ISMIR-2010.pdf)
- [Music Machinery summary of the above paper](https://musicmachinery.com/2010/08/10/approximate-note-transcription-for-the-improved-identification-of-difficult-chords/)
- [Spotify basic-pitch paper, arXiv:2203.09893](https://arxiv.org/abs/2203.09893)
  ([ar5iv HTML version fetched directly](https://ar5iv.labs.arxiv.org/html/2203.09893))
- [spotify/basic-pitch (GitHub)](https://github.com/spotify/basic-pitch)
- [MT3: Multi-Task Multitrack Music Transcription, arXiv:2111.03017](https://arxiv.org/pdf/2111.03017)
- [Magenta Onsets-and-Frames transcription README](https://github.com/magenta/magenta/blob/main/magenta/models/onsets_frames_transcription/README.md)
- [Omnizart JOSS paper](https://www.theoj.org/joss-papers/joss.03391/10.21105.joss.03391.pdf)
- [Music-and-Culture-Technology-Lab/omnizart (GitHub)](https://github.com/Music-and-Culture-Technology-Lab/omnizart)
- [Korzeniowski & Widmer, "Feature Learning for Chord Recognition: The
  Deep Chroma Extractor", arXiv:1612.05065](https://arxiv.org/abs/1612.05065)
- [madmom chords module docs](https://madmom.readthedocs.io/en/v0.16/modules/features/chords.html)
- [madmom.ml.rnn docs (pure-NumPy/SciPy inference engine)](https://madmom.readthedocs.io/en/v0.13.2/modules/ml/rnn.html)
- [essentia algorithms reference (`ChordsDetection`)](https://essentia.upf.edu/algorithms_reference.html)
- [essentia documentation/overview](https://essentia.upf.edu/documentation.html)
- [MIREX 2020 Audio Chord Estimation Results (fetched directly)](https://music-ir.org/mirex/wiki/2020:Audio_Chord_Estimation_Results)
- [MIREX 2019 Multiple Fundamental Frequency Estimation & Tracking
  Results — MIREX Dataset (fetched directly)](https://music-ir.org/mirex/wiki/2019:Multiple_Fundamental_Frequency_Estimation_%26_Tracking_Results_-_MIREX_Dataset)
- Various secondary/survey search results (BACHI, Serenade, "Chord
  Recognition with Deep Learning" arXiv papers) corroborating the
  ~75-80% full/MajMin chord accuracy plateau in more recent (2023-2025)
  work — cited for triangulation, not directly fetched in full.
