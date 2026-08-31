# OSS/SOTA landscape: onset detection, beat/tempo tracking, rhythm quantization

Research survey to inform note-color's own onset/tempo/duration pipeline
(`onset_detect.py`, `tempo_tracker.py`, `duration_tracker.py`,
`batch_transcribe.py`, `rhythm_reanalysis.py` — issues #55/#77). No ticket
number yet assigned; cite this doc from whatever ticket acts on it, same
convention as `docs/research/live-noncausal-rhythm-reanalysis.md`.

## Question

1. What onset-detection-function variants does aubio implement, and how
   do they actually compare in reported accuracy?
2. What does librosa's `onset_strength`/`beat_track()` (Ellis 2007 DP beat
   tracker) actually claim/achieve, and how does it compare to madmom?
3. What does madmom claim/achieve for beat/downbeat tracking, and —
   specifically relevant to note-color's causal-live requirement — does
   it have a genuine online/causal mode, and what does that mode cost in
   accuracy versus its offline mode?
4. What real MIREX beat-tracking/onset-detection numbers exist, across
   both classic DSP and ML/deep approaches?
5. Is there real prior art for rhythm quantization (continuous duration →
   notated value) beyond nearest-standard-value snapping, and does it
   report a measured improvement?
6. Synthesis: given note-color's constraints (Pi portability, pure NumPy
   on the live path, <150ms end-to-end budget, genuinely causal live
   tempo tracking, an already-accepted offline non-causal librosa path),
   what specifically should this project adopt or change?

## Answer

### 1. aubio's onset detection functions

aubio (Brossier's 2006 QMUL PhD thesis, "Automatic annotation of musical
audio for interactive systems," is the primary source for the underlying
algorithms/evaluation methodology; the library itself ships eight onset
novelty functions selectable via one `onset_type` string) implements:
`energy` (local energy), `hfc` (high-frequency content), `complex`
(complex-domain — combines magnitude *and* phase prediction error),
`phase` (phase-deviation only), `wphase` (magnitude-weighted phase
deviation), `specdiff` (spectral difference), `kl` (Kullback-Leibler
divergence between consecutive spectral frames), `mkl` (modified KL,
numerically regularized), and `specflux` (spectral flux — the same family
note-color's own `onset_detect.spectral_flux()` belongs to: half-wave-
rectified positive magnitude difference between consecutive frames).

A comparison run over a labeled onset dataset at a ±25ms detection
window reports (accuracy, higher is better): **hfc 0.750**, **complex
0.700**, kl 0.672, specdiff 0.653, mkl 0.647, energy 0.608, phase 0.516.
**hfc wins outright** in this comparison — it's cheap (a single weighted
sum over spectral bins, no phase unwrapping) and specifically good at
percussive attacks (broadband high-frequency energy jump), which is
exactly aubio's own stated rationale for it. `complex` is close behind
and is aubio's own recommended default for polyphonic/pitched material,
since it folds in phase-prediction error (a steady tone's phase advances
predictably frame-to-frame; a genuine onset breaks that prediction) on
top of magnitude — closer in spirit to what a monophonic *and* polyphonic
signal both need than a magnitude-only measure. `specflux`/`specdiff`
(note-color's own family) sit mid-pack, not top — aubio's own numbers put
plain spectral difference/flux behind both `hfc` and `complex`. `phase`
alone is clearly the weakest of the eight — noisy without a magnitude
term to gate it.

**Causal-feasibility note:** all eight are frame-local (each needs only
the current and previous STFT frame) — every one of them is naturally
causal/online, same as note-color's own `spectral_flux()`/`chroma_flux()`.
This is not a discriminator between them for note-color's purposes; the
discriminator is *accuracy per unit of extra compute*, and `hfc`/
`complex` both win on that axis over plain spectral flux/difference,
per aubio's own comparison.

### 2. librosa: `onset_strength` + `beat_track()` (Ellis 2007)

`librosa.beat.beat_track()` is a direct implementation of Ellis, D. P. W.,
"Beat tracking by dynamic programming," *Journal of New Music Research*
36.1 (2007): 51-60 — a three-stage pipeline: (1) compute an onset-strength
envelope (by default, `onset_strength()`'s own mel-spectrogram-based
spectral-flux measure — note, richer than a 12-bin chroma-flux signal:
it operates over dozens of mel bands, not 12 pitch classes), (2) estimate
a single global tempo via autocorrelation of that envelope (the *same*
family of technique note-color's own `TempoTracker._estimate()` already
uses, just applied non-causally over a whole clip rather than causally
over a rolling window), (3) a dynamic-programming pass picks the beat
sequence that best balances two objectives: high onset strength at each
picked beat, and close-to-constant inter-beat interval near the estimated
tempo.

This is **offline/non-causal by construction** — the DP pass optimizes a
globally-best path over the *entire* onset-strength sequence, so it
cannot emit a beat time before it has seen data past that beat (this
matches exactly what `docs/research/live-noncausal-rhythm-reanalysis.md`
already found by direct signature inspection: `onset_envelope=` accepts
any pre-computed array, `y=` requires the whole waveform up front — there
is no incremental/streaming call form). This confirms nothing new
architecturally beyond what that doc already established; it's included
here for completeness on "how good is it," not "can it run live."

**How good is it:** a direct benchmarking comparison (dance-motion beat-
alignment study, cited below) found librosa the more accurate *global
average tempo* estimator between the two tools tested, but madmom's
RNN-based tracker better represented fine-grained rhythmic structure and
recovered more of the individual beats — i.e. librosa is a solid global-
tempo estimator but a less precise per-beat tracker than a modern
ML-based one. The same source found a **systematic 0.02-0.06s late bias**
in librosa's individual beat times versus ground truth (also independently
reported as a currently-open librosa GitHub issue, "Beats are slightly
late," #1052) — a real, measured detail relevant if note-color's batch/
reanalysis paths ever need beat-time precision beyond "roughly where the
bar line falls," which today's stated tolerance (barline placement
"approximation," per CLAUDE.md) already accepts.

### 3. madmom: RNN+DBN beat/downbeat tracking, and its online mode

madmom (Böck et al., "madmom: A New Python Audio and Music Signal
Processing Library," ACM Multimedia 2016, arXiv:1605.07008) is widely
regarded as one of the strongest open-source beat/downbeat trackers.
Its core beat tracker is a bidirectional RNN (onset/beat-activation
estimator) feeding a Dynamic Bayesian Network — in practice implemented
as an HMM over a tempo/phase state space — decoded by Viterbi for a
globally-optimal beat sequence (`DBNBeatTrackingProcessor`, default
`online=False`).

**MIREX standing:** Böck/Krebs submissions using this RNN+DBN approach
placed at or near the top of MIREX's audio beat-tracking task repeatedly
across 2015-2016 (e.g. reported per-dataset F-measures around **0.908 on
Ballroom** and **~0.97 on HJDB** for the 2016 submission "BK4"). madmom's
own reported per-dataset F-scores (from the library's own evaluation
scripts/paper) run **~0.83 on Ballroom** (relatively easy, steady dance
tempi) down to **~0.52 on SMC** — the "Structural Musical Complexity"
dataset, deliberately built from expressive, tempo-varying, hard-to-track
recordings specifically *because* every prior beat tracker scored badly
on it. That 0.52-vs-0.83 spread is itself the single most informative
number here: **even the strongest available beat tracker's accuracy is
heavily repertoire-dependent**, not a fixed "beat tracking is solved"
number — directly relevant context for note-color's own already-honest
"barline placement is approximate" framing.

**Does madmom have a genuine online/causal mode? Yes — mechanically
confirmed by reading `madmom/features/beats.py` directly.**
`DBNBeatTrackingProcessor(online=True)` swaps Viterbi (which needs the
complete activation sequence to find the single globally-optimal path)
for the **forward algorithm**, processed one frame at a time via
`process_online()`, maintaining running HMM forward-probability state
(`self.counter`, `self.last_beat`, etc.) rather than a whole-sequence
backward pass — this is genuinely causal, frame-by-frame, no future
lookahead, and it is a real, shipped, documented mode, not a hack. The
`correct` post-processing step (snapping a beat to the nearest local
peak in the activation function) only applies in offline mode, since it
needs to look at samples after the candidate beat.

**What online mode costs in accuracy — this is the number that actually
answers the note-color question.** madmom's shipped `online=True` isn't
benchmarked with its own published F-measure table (not found in this
research), but a close architectural cousin gives a real, directly
comparable number: **BeatNet** (Heydari & Duan, ISMIR 2021,
arXiv:2108.03576) uses the same RNN-activation-plus-probabilistic-
tracker structure, published *both* an online (particle-filter) variant
and an offline (swap in a DBN, same activations) variant of the same
system, evaluated on the same data — an apples-to-apples online-vs-
offline comparison the madmom paper itself doesn't publish. On GTZAN:
**online 75.44 / offline 80.64** beat F-measure, **online 46.49 /
offline 54.07** downbeat F-measure — a **~5-point (beat) to ~8-point
(downbeat) absolute F-measure cost for going causal**, on top of an
already-imperfect offline baseline. A follow-on paper by the same
architecture family ("Don't Look Back," Heydari & Duan, ICASSP 2021,
arXiv:2011.02619) explicitly frames its own contribution as closing that
online/offline gap versus *prior* online beat trackers, reporting that
its enhanced-particle-filter approach "significantly improves accuracy
over state-of-the-art online beat-tracking methods, yielding similar
performance to offline methods" — i.e. **the online/offline accuracy gap
is real, well-documented, an active research problem in this exact
literature, and not fully closed even by the specialized methods
attacking it directly.**

**Real-time feasibility, concretely:** BeatNet reports **<50ms latency**
in third-party benchmarking (biff.ai's roundup, cited below); its own
paper benchmarks processing a 30-second clip in 5.2s (1000 particles) to
8.9s (1750 particles) on a desktop Ryzen 9 3900X — i.e. **not real-time
end-to-end at that particle count** (30s of audio taking 5-9s to process
is a >10x real-time factor, not <1x), though the *decision latency* per
frame (once running) is still small; the throughput cost is dominated by
the RNN forward pass plus per-particle likelihood evaluation, both far
heavier than anything in note-color's hand-rolled NumPy pipeline.
madmom's own `online=True` forward-algorithm mode is architecturally
lighter than particle filtering (no per-particle resampling), but still
requires madmom's RNN activation function to run every frame — a
multi-layer BiLSTM/GRU forward pass over spectral features, not a NumPy
autocorrelation. Neither madmom nor BeatNet publishes a Raspberry-Pi-class
benchmark; both are desktop-CPU-benchmarked only, and both depend on
numpy+scipy at minimum, madmom also on Cython-compiled extensions and
(for the newer variants) a trained neural net forward pass — categorically
heavier than note-color's <150ms whole-pipeline budget was ever scoped to
absorb even before accounting for Pi Zero 2 W's weak quad-core Cortex-A53.

**Bottom line on madmom's online mode changing the calculus:** it
doesn't, for the *live* path. It confirms causal beat tracking is
possible without waiting for a whole clip (useful validation that
note-color's own causal `TempoTracker` isn't chasing something
architecturally unreasonable), but every online-capable ML tracker
found here trades measured accuracy for causality (~5-8 F-measure
points, BeatNet's own numbers) and costs meaningfully more CPU per
frame than a hand-rolled autocorrelation-over-novelty approach — the
opposite of what a Pi-portable <150ms live budget needs. This is
additional, independent confirmation of note-color's existing decision
(`librosa`/madmom isolated to `batch_transcribe.py` + `rhythm_
reanalysis.py`, never the live per-hop path) rather than a reason to
revisit it.

### 4. MIREX beat-tracking/onset-detection numbers, gathered directly

MIREX's own standard metrics: **F-measure** (±70ms tolerance, binary
hit/miss), **Cemgil** (continuous Gaussian-weighted score, ~40ms sigma,
penalizes near-misses gracefully rather than all-or-nothing), **P-score**
(McKinney et al. 2006, a normalized cross-correlation-style measure), and
**Goto** (a stricter "did it track continuously" pass/fail measure).
Concrete historical numbers found directly (not estimated):

- MIREX 2016, Böck/Krebs "BK4" (RNN+DBN): **F=0.908 (Ballroom)**,
  **F≈0.97 (HJDB)**, F=0.599 (RWC Classical, harder repertoire),
  Durand et al. "DBDR2" (DNN+Viterbi): F=0.872 (Beatles).
- MIREX 2015, Krebs/Böck "FK3" (HMM-based): F=0.824 (HJDB).
- madmom's own SMC-dataset number (~0.52 F-score) stands in sharp
  contrast to its Ballroom number (~0.83) — the single clearest
  illustration in this research that **repertoire difficulty, not
  algorithm choice alone, dominates achievable accuracy** for any of
  these systems, classic-DSP or ML alike.

No head-to-head MIREX table pitting aubio's or librosa's trackers
directly against madmom's was found (aubio/librosa are not typically
MIREX submissions in their own right — MIREX entries are usually named
research-group submissions, not library releases); the comparisons found
above are between named research submissions (madmom's authors, BeatNet,
etc.), with librosa's comparative standing coming from independent
third-party benchmarking studies instead (the dance-motion study cited
above) rather than MIREX itself.

### 5. Rhythm quantization prior art beyond nearest-value snapping

Read directly: Cemgil, Desain & Kappen, "Rhythm Quantization for
Transcription" (SNN/NICI, University of Nijmegen — the standard citation
for this exact problem). Its framing is precisely note-color's own
duration-classification problem, one level up: given a performed rhythm
(a list of onset times / durations), find the best-fitting notated
values. The paper's central critique of naive nearest-grid-point
snapping — note-color's own `duration_class_for_beats()` approach,
i.e. "quantize each onset/duration independently to the closest standard
value" — is that **it treats every note's duration as independent of its
neighbors**, discarding the fact that real (expressively-played, not
metronomic) timing deviations are *correlated* between nearby notes: a
note played slightly early is very often followed by a compensating
slightly-late neighbor, and a snap-to-nearest-grid approach can pick a
notation that is locally "most accurate" per-note yet globally *less
readable* (their own worked example: naive quantization reads a smooth
triplet passage as multiple inconsistent nearby fractions/tuplets
instead of the "obviously right" reading a human transcriber would
choose).

**Their alternative, concretely:** a Bayesian MAP framework where the
notated score/tempo pair is chosen to maximize
`P(score, tempo | performance)`, decomposed into (a) a Gaussian
performance-likelihood term (how far the actual durations sit from
"mechanically correct," with a covariance matrix that explicitly encodes
*correlation between nearby onsets* — closer onsets get a higher assumed
correlation coefficient, per their own perceptual-experiment-fit data),
(b) a complexity prior over the code-vector/notation itself (penalizing
"deeper" subdivisions — more/smaller tuplets — the same instinct behind
preferring simpler notation when it fits nearly as well), and (c) a
tempo prior favoring slower/simpler grids over pathologically fast ones
that could notate any rhythm as whole notes. This "vector quantization"
(their term — grouping several onsets jointly rather than quantizing
each independently) is empirically shown, on both a synthetic example
and a real recorded solo-piano excerpt, to recover the musically "right"
notation where naive independent-onset snapping produces a technically-
closer-but-uglier/inconsistent one (their own real-performance example:
naive per-onset quantization reads a passage as "4223234422" — an
inconsistent grid — while the vector quantizer recovers the intended
smooth, consistent subdivision).

Other cited prior art in the same paper, for completeness: Longuet-
Higgins (1987, hierarchical-structure-based quantization), Desain &
Honing (1991/1992, a connectionist/relaxation-network approach that
pulls pairs of time intervals toward simple integer ratios), Pressing &
Lawrence (1993, multiple template grids scored by a distance criterion —
closest in spirit to note-color's own current "snap to nearest standard
value" but scored across several candidate grids rather than one fixed
one), and IRCAM's Kant system (Agon et al. 1994, similar template-scoring
heuristics). None of these is a genetic-algorithm approach specifically —
no GA-based rhythm quantizer was found in this research despite
searching for it directly; the actual lineage here is Bayesian/
statistical (Cemgil et al.) and connectionist/relaxation (Desain &
Honing), not evolutionary computation. More recent work (found but not
read in full) applies transformer-based sequence models to the same
problem given a performance-to-score alignment (a 2026 arXiv preprint,
"Transformer-Based Rhythm Quantization of Performance MIDI Using Beat
Annotations") — a substantially heavier, training-data-dependent
approach, not evaluated here in depth since it's clearly out of scope
for a NumPy-only live/offline pipeline.

**Does this apply to note-color's specific quantization step?** Only
partially, and the fit is worth being precise about. Cemgil et al.'s
correlation structure is between **onset timings of neighboring notes in
a single performed passage** (i.e., timing/tempo-curve estimation
jointly across a phrase) — it's solving "where exactly did the beat
grid fall, and how do several nearby onsets jointly inform that,"
which is much closer to what `librosa.beat.beat_track()`'s DP already
does (a joint, sequence-wide fit) than to note-color's own
`duration_class_for_beats()` (a single already-measured duration →
nearest note-value snap, done independently per note, after the beat
grid/tempo is already known from a separate step). Note-color's
duration snapping is deliberately the simpler, later-stage problem — it
already gets a tempo estimate from a separate, dedicated tracker
(`TempoTracker` live, `beat_track()` batch) before ever calling
`duration_class_for_beats()`; correlated joint-onset quantization
would only be a genuinely different approach if note-color started
*inferring* tempo/grid from note durations directly rather than from a
dedicated onset/beat-novelty signal, which it doesn't.

## Synthesis: concrete recommendations for note-color

Given the constraints repeated in the prompt — Pi-class portability,
pure NumPy strongly preferred for the live per-hop path, hard <150ms
end-to-end budget, causal-online tempo tracking required live, an
already-accepted offline non-causal librosa path for batch/reanalysis —
here is an opinionated read, not a survey recap:

**1. madmom/BeatNet's online mode does not change the "no librosa/madmom
in the live path" decision — it reinforces it.** The one number that
matters most from this whole research: BeatNet's own paper shows going
causal costs it **~5 points of beat F-measure and ~8 points of downbeat
F-measure**, even for a system purpose-built and specifically optimized
for the online case — and it still isn't real-time-cheap (a desktop
Ryzen 9 needing >1x real time to process 1000-1750 particles per frame).
madmom's own `online=True` swaps Viterbi for a forward-algorithm pass,
which is architecturally cheaper than particle filtering, but still
needs its RNN activation function evaluated every frame — a BiLSTM/GRU
forward pass is not "cheap enough for Pi Zero 2 W at <150ms end-to-end,"
and neither library publishes any Pi-class benchmark to check that
assumption against. Nothing in this research suggests either library's
online mode is a free or even cheap accuracy win over a hand-rolled
NumPy autocorrelation approach at note-color's hardware floor. **No
change recommended here** — the existing architecture (hand-rolled
causal `TempoTracker` live, librosa isolated to two explicitly offline/
throwaway-thread call sites) is the right call, and this research adds a
second independent data point (BeatNet's measured online/offline gap) on
top of the project's own prior reasoning for it.

**2. One concrete, cheap onset-detection upgrade worth prototyping:
add aubio's `complex`-domain novelty measure (or at minimum `hfc`) as an
alternative/additional novelty signal, not a replacement.** aubio's own
comparison puts `hfc` (0.750) and `complex` (0.700) meaningfully ahead of
plain spectral-difference/flux methods (0.647-0.672) at equivalent,
trivially-cheap-in-NumPy cost — both are simple per-bin weighted sums
(`hfc`) or magnitude+phase-prediction-error terms (`complex`) over the
same spectrum note-color's `pitch_detect.compute_spectrum()` already
computes every hop; neither needs new dependencies or a second FFT.
Given that note-color's own documented known-limitation list already
flags `ONSET_FLUX_THRESHOLD` as provisional and untuned against real
playing (CLAUDE.md's Known Limitations), and issue #70's own writeup
found a real-audio-only failure mode (a note's own attack-fade straddling
a hop boundary and firing a spurious re-onset) that a purely amplitude/
spectral-difference-based measure is inherently sensitive to — `hfc`'s
percussive-attack sensitivity and `complex`'s phase-prediction term are
both differently shaped novelty signals that could plausibly reduce this
specific false-positive class (a decaying-but-still-broadband attack
tail versus a genuine fresh onset), though this is a hypothesis to test
against real audio, not a proven fix. Recommend: implement `hfc`
alongside `spectral_flux()` in `onset_detect.py` (a five-line function —
weighted sum of bin magnitudes, weight = bin index or frequency), run
it through the existing acoustic-test-suite convention
(`scripts/acoustic_pipeline_test.py`) against the same real-mic
recordings already used for issue #70/#71/#75's onset-related
investigations, and adopt it only if it measurably reduces a concrete,
already-documented failure mode — consistent with this project's own
stated practice of evidence-based threshold changes, not speculative
retuning.

**3. Tempo-octave-error mitigation (2x/0.5x lock) is the one real,
literature-backed technique worth porting into the *live* `TempoTracker`,
not just the already-recommended offline ensemble.** `docs/research/
live-noncausal-rhythm-reanalysis.md` already recommended a multi-`start_
bpm`-hypothesis ensemble for the *offline*/reanalysis path specifically
because octave-locking is "the single most common failure mode for
exactly this class of DP beat tracker" in the literature — this research
confirms that framing generically (it's a documented, actively-researched
problem across both classic autocorrelation trackers and modern DBN/ML
ones alike, not specific to librosa's DP tracker). Since note-color's
*live* `TempoTracker._estimate()` already computes a full autocorrelation
array (`acf`) over the lag range `[lag_min, lag_max]` before picking
`best_lag` via a single `argmax`, checking whether `acf[2*best_lag]` (half
the current guessed tempo) or `acf[best_lag//2]` (double it) also clears
a comparable fraction of the confidence-ratio gate already in place
(`TEMPO_MIN_CONFIDENCE`) is a nearly-free addition — the autocorrelation
is already computed, no new FFT or window needed, just checking one or
two more array indices and a tie-break rule (prefer the tempo closer to
some plausible human range, e.g. favor lower BPM/wider inter-beat spacing
when two candidates are both well within confidence, since octave errors
in this literature skew toward misreporting *double* the true tempo more
often than half). This is a targeted, evidence-motivated addition to an
existing, working module — not a rearchitecture.

**4. Rhythm quantization: Cemgil et al.'s joint/correlated quantization
is real, well-evidenced prior art, but it answers a different question
than note-color's `duration_class_for_beats()` currently asks — not a
recommended port.** Their vector quantizer's win comes from jointly
inferring tempo-grid-plus-notation across *several* onsets at once,
modeling real correlation between neighboring notes' timing deviations;
note-color already separates that concern into a dedicated, independent
tempo/grid estimator (`TempoTracker` live, `beat_track()` batch) before
ever reaching `duration_class_for_beats()`'s much narrower single-note
snap. Porting their joint approach would mean merging duration
classification back into tempo estimation — a real architectural change,
not a drop-in improvement, and not clearly motivated by any concrete,
already-observed note-color symptom (the project's own Known Limitations
list frames duration/rhythm thresholds as "provisional, not yet tuned
against extended real playing" — a tuning gap, not a wrong-algorithm
gap). Recommended instead: leave `duration_class_for_beats()`'s
independent-nearest-value approach as-is until real extended playing
surfaces a concrete symptom this paper's failure mode would actually
predict (systematically "ugly"/inconsistent notation *despite* individual
durations each measuring close to correct) — consistent with this
project's own stated practice everywhere else (issue #75, #71's threshold
sweep) of not chasing a fix without a reproduced, concrete complaint.

## Sources

- Brossier, P., PhD thesis, "Automatic annotation of musical audio for
  interactive systems," QMUL, 2006 (aubio's foundational evaluation
  methodology/reference; https://aubio.org/phd/).
- aubio onset-detection-function comparison numbers (hfc 0.750, complex
  0.700, kl 0.672, specdiff 0.653, mkl 0.647, energy 0.608, phase 0.516,
  ±25ms window) — via web search of aubio-adjacent literature; exact
  original paper/table not independently re-verified beyond the search
  synthesis, flagged as such.
- aubio spectral-features manual: https://aubio.org/manual/latest/py_spectral.html
- Ellis, D. P. W., "Beat tracking by dynamic programming," Journal of New
  Music Research 36.1 (2007): 51-60 (librosa's `beat_track()` reference).
- librosa `beat_track()`/`onset_strength()` docs (multiple versions),
  e.g. https://librosa.org/doc/main/api/generated/librosa.beat.beat_track.html
- librosa GitHub issue #1052, "Beats are slightly late":
  https://github.com/librosa/librosa/issues/1052
- Böck, S. et al., "madmom: A New Python Audio and Music Signal
  Processing Library," ACM Multimedia 2016, arXiv:1605.07008.
- `madmom/madmom/features/beats.py` (`DBNBeatTrackingProcessor`,
  `online`/`correct` parameters, forward-vs-Viterbi decoding) — read
  directly via https://raw.githubusercontent.com/CPJKU/madmom/main/madmom/features/beats.py
- Heydari, M. & Duan, Z., "BeatNet: CRNN and particle filtering for
  online joint beat downbeat and meter tracking," ISMIR 2021,
  arXiv:2108.03576 (online-vs-offline F-measure numbers read directly
  from https://ar5iv.labs.arxiv.org/html/2108.03576).
- Heydari, M. & Duan, Z., "Don't look back: an online beat tracking
  method using RNN and enhanced particle filtering," ICASSP 2021,
  arXiv:2011.02619.
- MIREX 2015/2016 beat-tracking submission results (BK4, FK3, DBDR2) —
  via web search of MIREX results-page-adjacent literature/reviews;
  original MIREX results tables (nema.lis.illinois.edu) not directly
  fetched/re-verified in this pass, flagged as such.
- biff.ai, "A rundown of open-source beat detection models (madmom,
  BeatNet & more)," https://biff.ai/a-rundown-of-open-source-beat-detection-models/
  (BeatNet <50ms latency figure, qualitative madmom/BeatNet/BEAST
  comparison table).
- Dance-motion beat-tracking benchmarking study (librosa-vs-madmom
  comparative accuracy, late-beat bias, synthesized-vs-real-audio
  degradation numbers) — found via web search of PMC/Frontiers-hosted
  studies on beat/movement synchronization; not independently re-fetched
  in full in this pass, flagged as such.
- Cemgil, A. T., Desain, P. & Kappen, B., "Rhythm Quantization for
  Transcription," SNN/NICI, University of Nijmegen — read in full via
  https://www.snn.ru.nl/v2/serve.php?doc=Cemgil_aisb99.pdf.
- Note: a claimed aubio-hfc "98.4% F1" figure surfaced in one search
  result (from an unrelated vocal-percussion-analysis paper's own
  narrower dataset) — not included as a general aubio accuracy claim
  above, since it's a single-dataset, single-paper number rather than
  aubio's own general evaluation; flagged here rather than silently
  dropped.
- This repo, read directly for context on the existing implementation:
  `onset_detect.py`, `tempo_tracker.py`, `config.py` (`ONSET_FLUX_
  THRESHOLD`, `TEMPO_MIN_BPM`/`MAX_BPM`/`UPDATE_INTERVAL_HOPS`/
  `MIN_CONFIDENCE`, `DURATION_DECAY_RATIO`), and `docs/research/
  live-noncausal-rhythm-reanalysis.md` (existing offline-ensemble
  recommendation this doc extends to the live path).

## Caveats on this research pass

- Several numbers above (the aubio ODF comparison table, the MIREX
  2015/2016 submission scores) were surfaced via WebSearch's own
  synthesis of search results rather than a directly-fetched, read-in-
  full primary source table — flagged inline above and in Sources,
  consistent with this project's own convention of distinguishing
  "read directly" from "reported by a secondary summary." Treat these
  specific numbers as *plausible, sourced-but-not-independently-verified*
  rather than as fully confirmed as, e.g., the Cemgil paper's content
  (read in full, page-by-page, above) or madmom's `beats.py` source
  (fetched and read directly).
- No Raspberry-Pi-class (or any ARM) benchmark exists in the literature
  for madmom or BeatNet — the "too heavy for Pi" conclusion in the
  Synthesis is a reasoned extrapolation from desktop-CPU benchmarks and
  architectural comparison (RNN forward pass + particle filter/DBN vs.
  a NumPy FFT autocorrelation), not a measured Pi number. Consistent
  with this project's own repeated practice of flagging extrapolated-
  not-measured claims explicitly (see CLAUDE.md's Known Limitations
  section's own repeated Pi-extrapolation caveats elsewhere).
