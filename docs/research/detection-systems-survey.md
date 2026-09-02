# Detection systems survey: synthesis and staged recommendations

This is a **synthesis** document, not a from-scratch survey — four much
deeper landscape docs already exist in this directory and were read in
full before writing this one:

- `oss-landscape-pitch-detection.md` (monophonic YIN/pYIN/CREPE/SwiftF0/
  aubio, primary-sourced, incl. a direct 9-algorithm benchmark table)
- `oss-landscape-chord-multipitch.md` (NNLS-chroma/Chordino, basic-pitch,
  madmom Deep Chroma, MIREX ACE/multi-F0 numbers)
- `oss-landscape-rhythm-tempo.md` (aubio ODFs, librosa `beat_track()`,
  madmom's online/offline beat-tracking gap via BeatNet, Cemgil rhythm
  quantization)
- `oss-landscape-transcription-and-prior-art.md` (full AMT systems,
  audio-to-color prior art, MusicXML tooling, general Pi DSP practice)

Those four already contain the load-bearing numbers (MIREX scores,
SwiftF0's benchmark table, BeatNet's online-vs-offline F-measure gap,
NNLS-chroma's +12pp result on hard chords, etc.) and their own citations
— this document does not re-derive them. What it adds:

1. Ties the four together against **this project's actual documented
   weak points** (pulled from `CLAUDE.md`/`docs/DECISIONS.md`, not
   invented) rather than a generic "here's what exists" framing.
2. A single comparison table across pitch/chord/onset/tempo candidates,
   which none of the four source docs presents (each stays scoped to its
   own subsystem).
3. Three new, independently-verified data points not in the source docs:
   essentia's ARM wheels are **32-bit (armhf) only** on piwheels — a
   direct conflict with this project's 64-bit-Pi-OS decision, not just
   "a heavy C++ library"; **onnxruntime does ship official aarch64
   wheels** on PyPI (unlike aubio/essentia's ARM wheels), which measurably
   changes the wheel-risk calculus for an ONNX-based offline option like
   basic-pitch; and **madmom's PyPI release cadence is currently
   inactive** (no release in the past 12 months per libraries.io/Snyk),
   an independent maintenance-risk signal on top of its already-documented
   architectural weight.
4. Staged (near/mid/long-term) recommendations in the specific shape
   requested for this pass, and an explicit section cross-checking every
   recommendation against what `docs/DECISIONS.md` has already tried,
   rejected, or settled — so nothing here quietly re-proposes a closed
   question.

Read the four source docs for full sourcing/quotes; this one cites them
by filename rather than re-footnoting every number.

## 1. What's actually wrong today (from this project's own record, not invented)

Pulled directly from `CLAUDE.md`'s Key design decisions / Known
limitations and `docs/DECISIONS.md` — every item below is a real,
already-documented gap, not a new critique:

**Monophonic pitch (`pitch_detect.py`, hand-rolled YIN):**
- Issue #69's low-register sub-harmonic fix has been through two rounds
  (margin 0.5 → 0.1) and is explicitly flagged as **"provisionally
  fixed... a real-mic re-verification is still pending"** — every
  validation pass so far is synthetic or `--source loopback` (no physical
  mic coloration), and this exact issue already burned the project once
  (a synthetic/loopback-clean fix that then regressed real-mic accuracy).
- Issue #71 removed YIN's loose global-argmin fallback because it
  confidently (0.6-0.9) reported a wrong note near `FMIN` on 72.8% of
  moderate-noise hops — fixed, but the tradeoff is now **honest silence**
  under sustained noise at that SNR, confirmed via a threshold sweep to
  have "zero recoverable margin" — a real accuracy ceiling for a
  single-93ms-window, single-hop technique, not a tuning gap.
- Same #71 fix cost recall at the fastest tested tempo (280bpm, 107ms
  notes): 88% → 71%, unresolved, explicitly not chased further.
- Octave-error blips during note decay (~100ms) are a known, unfixed,
  low-priority limitation.

**Polyphonic/chord (`multipitch.py`, `chroma.py`, `chord_templates.py`):**
- A root note and another note landing within ~2 cents of that root's own
  3rd/4th harmonic (a genuine, common musical interval — a 12-TET
  fifth-plus-octave) are **spectrally indistinguishable from a single
  hop's magnitude spectrum alone** — documented as an inherent, unresolved
  limitation after three distinct fix attempts (tolerance narrowing,
  magnitude-consistency check, self-corroborating-harmonic-series check)
  were each tried and rejected for concrete, measured reasons.
- Issue #75: a snare drum's ~200Hz "poc" attack transient, landing ~35
  cents from G3, gets tracked as a real short phantom chord-mode note —
  root-caused precisely, but **two full rounds of candidate fixes** (four
  distinct approaches) were each empirically rejected as indistinguishable
  from legitimate cases (a real octave-doubled note, a genuine chord
  attack) — left open, with the doc's own conclusion being that closing it
  needs "a genuine transient/onset classifier," out of scope for a
  peak-picking pipeline.
- Chord-mode thresholds (`CHORD_MATCH_THRESHOLD`, `CHORD_MEDIAN_WINDOW`,
  etc.) are explicitly "provisional starting values... not yet tuned
  against extended real playing."

**Onset/tempo/duration (`onset_detect.py`, `tempo_tracker.py`,
`duration_tracker.py`):**
- `ONSET_FLUX_THRESHOLD`, `DURATION_DECAY_RATIO`, and every `TEMPO_*`
  constant are documented as "likewise provisional... not yet tuned
  against extended real playing."
- A short note's own 20ms attack fade can straddle a hop boundary and
  fire a spurious re-onset on real (not idealized synthetic) audio —
  found via real acoustic testing, left as a known limitation because
  tightening the onset heuristic risks the opposite failure (missing a
  genuine fast repeated note).
- `chroma_flux()` as `librosa.beat.beat_track()`'s onset envelope
  (`rhythm_reanalysis.py`) is confirmed as a frame-alignment win but an
  **open empirical question on tempo-tracking accuracy** — never measured
  against real playing.

**Cross-cutting:** several of the most consequential fixes in this
project's history (#69, #71) were validated synthetically or via
`--source loopback` and only later found to regress under real
speaker→mic conditions — the project's own stated posture is
"provisional until real-mic-confirmed," which the cross-project survey in
section 3 below should be read through the same lens: nothing in the
literature is a guaranteed win on this project's actual signal until
tested against its own acoustic-test-suite convention
(`scripts/acoustic_pipeline_test.py`).

## 2. Comparison table

Cost/feasibility columns are Pi Zero 2 W-relative (quad-core Cortex-A53,
the project's stated hardware floor) unless marked "desktop only."
Sourced from the four landscape docs (see them for full citations) plus
this pass's own three new findings (marked †).

| Technique | What it targets | Reported accuracy signal | Pi-class compute cost | Dependency weight / licensing | Integration point |
|---|---|---|---|---|---|
| **pYIN** (`librosa.pyin`) | Mono pitch, octave-error robustness | Mixed: octave accuracy *worse* than plain autocorrelation (66.83% vs. Praat's 84.37%) in the one rigorous cross-algorithm benchmark found — reputation is eval-set-dependent, not unconditional | 1420ms/5s-clip on desktop (SwiftF0 paper) — ~280ms per second of audio; fine for an offline tool, unusable live | Zero *new* risk — `librosa` already isolated to `batch_transcribe.py`/`rhythm_reanalysis.py` (BSD, pure-Python+SciPy) | Optional offline mode inside `batch_transcribe.py` only |
| **SwiftF0** (2025, `pip install swift-f0`) | Mono pitch, octave-error + noise robustness | Best-in-class across CREPE/pYIN/Praat/PENN on the one paper that benchmarks all of them: 94.07% clean / 91.80% noisy HM, 96.75%/93.52% octave accuracy | 132.6ms/5s clip on **desktop** (42x faster than CREPE) — **no Pi number exists anywhere**, genuinely unmeasured | Small (95,842-param CNN); claims `numpy`-only inference, **not independently confirmed by reading its source** | Would replace/augment `pitch_detect.detect_pitch()` live — the one live-path candidate that doesn't structurally fail the constraints on paper |
| **CREPE / torchcrepe** | Mono pitch | 96.7% RPA (clean, MDB-STEM-Synth) at full 22M-param capacity | 5.5s to process a 5s clip on desktop CPU at full size — **not real-time even on desktop**; smaller presets untested here | PyTorch or TensorFlow + downloaded weights — exactly the dependency class `CLAUDE.md` already rejected aubio for, at a higher weight | Not viable live; only plausible as a desktop-tier optional backend, unverified even there |
| **SPICE** (Google) | Mono pitch, mobile-targeted | 90.6%/89.1% RPA (MIR-1K/MDB-stem-synth), comparable to CREPE with no labeled training data | Unmeasured — no CPU-cycle numbers found for any platform | TFLite runtime — real dependency, lighter than full CREPE/TF | Same tier as SwiftF0 as a live-path candidate, but with zero cost data vs. SwiftF0's own desktop numbers |
| **aubio** (yin/yinfft/hfc/complex) | Mono pitch + onset | yinfft (56%) beats plain yin (37%) on one singing-pitch study; `hfc` (0.750) and `complex` (0.700) beat plain spectral-flux/difference (0.647-0.672) for onset detection | Cheap, causal, purpose-built for exactly this | **Already rejected in `docs/DECISIONS.md`'s founding rationale** — armv6l/armv7l-only piwheels, no confirmed aarch64 wheel, last stable release Feb 2019 (no meaningful update since) | N/A as a library; its `hfc`/`complex` novelty-function *math* is cheaply reimplementable in `onset_detect.py` with zero new dependency |
| **NNLS-chroma / Chordino-style approximate transcription** | Chord/multipitch harmonic-collision accuracy | +6pp overall, **+12pp specifically on harmonically ambiguous chords** (Mauch & Dixon, ISMIR 2010) vs. raw-chroma template matching | Per-frame NNLS solve (iterative convex optimization) — heavier than one FFT+matmul, but pure linear algebra, no training, plausibly causal | Zero new dependency — `scipy.optimize.nnls` (already transitively available) or hand-rolled iterative NNLS in NumPy | Would replace/augment `chroma.fold()`'s input to `chord_templates.match()` |
| **Spotify basic-pitch** | Polyphonic note transcription | ~17K params, <20MB; 70.9% note-F1 on MAESTRO (piano); no published Pi number | CQT needs >1s audio buffering + ~120ms model latency — **structurally incompatible with a causal <150ms live pipeline**, independent of raw inference speed | ONNX Runtime (or TFLite/CoreML) — **†onnxruntime does ship official aarch64 manylinux wheels on PyPI**, unlike aubio/essentia's ARM wheels; still a real, non-trivial binary dependency | Offline-only: an optional, selectable backend for `batch_transcribe.py`/`virtualnote transcribe`, never live `multipitch.detect()` |
| **madmom** (Deep Chroma chords; RNN+DBN beat/downbeat) | Chord accuracy (~80.4% MajMin) and beat/downbeat tracking (F≈0.83-0.91 easy repertoire, ~0.52 hard repertoire) | Comparable to NNLS-chroma's own ~80% chord ceiling — not a step-change; genuine online mode exists but costs ~5-8 F-measure points vs. offline (BeatNet's measured analog) | RNN/BiLSTM forward pass every frame even in online mode — categorically heavier than a NumPy autocorrelation; no Pi benchmark published anywhere | Cython-compiled, `pure-NumPy inference engine for pretrained nets` is a genuine plus, but **†PyPI release cadence is currently inactive** (no release in 12+ months per libraries.io/Snyk) — real maintenance risk on top of build weight | Not recommended at any tier given cost/benefit vs. NNLS above |
| **essentia** (`PitchYinFFT`, `MultiPitchKlapuri`, `ChordsDetection`) | Alternative DSP pitch/chord backend | No accuracy edge found over what's already shipped; `ChordsDetection` is **strictly weaker** than `chord_templates.py` (triads only, no sevenths/slash naming) | C++ core, real-time-capable in principle | **†piwheels' essentia wheels are 32-bit (armhf) only** — a direct, concrete conflict with `CLAUDE.md`'s own "Target 64-bit Raspberry Pi OS (Bookworm+) — 32-bit is a wheel risk" decision, not just generic C++-library weight | Not recommended — no accuracy case, and now a confirmed wheel-tier conflict |
| **Harmonic-sum / two-way-mismatch classical multipitch** (Klapuri-family) | Alternative to peak-pick + prune | No head-to-head number found vs. current approach; conceptually close to NNLS above | Pure NumPy-feasible | None | Subsumed by the NNLS recommendation above — a genuinely distinct implementation of the same underlying idea (score candidate fundamentals by their harmonic support rather than pick-then-prune single peaks) |
| **Cemgil et al. joint/correlated rhythm quantization** | Duration-class snapping readability | Recovers musically "right" notation where independent nearest-value snapping produces inconsistent grids, on real solo-piano data | N/A — algorithmic reframing, not a compute question | None (pure algorithm) | Would require merging duration classification back into tempo/grid estimation — a real architectural change, not a drop-in; **not motivated by any current concrete complaint** (see conflicts section) |

## 3. Staged recommendations

### Near-term, low-risk (worth doing now or soon)

1. **Port `hfc`/`complex`-domain onset novelty into `onset_detect.py` as
   an additional signal, not a replacement.** Five-ish lines each — a
   weighted bin-magnitude sum (`hfc`) and a magnitude+phase-prediction
   term (`complex`) over the spectrum `compute_spectrum()` already
   produces every hop. aubio's own comparison (0.750/0.700 vs. plain
   spectral flux's 0.647-0.672) is the concrete accuracy signal; the
   integration cost is genuinely near-zero (no new dependency, no new
   FFT). Test against `scripts/acoustic_pipeline_test.py`'s existing
   real-loopback suites before adopting — same "evidence, not
   speculation" discipline the project already applies (see conflicts
   section on why this is *not* a re-proposal of anything rejected).
   Detailed in `oss-landscape-rhythm-tempo.md` §Synthesis point 2.

2. **Add tempo-octave-lock (2x/0.5x) mitigation to the live
   `TempoTracker._estimate()`.** `acf`, the full autocorrelation array,
   is already computed before `best_lag` is picked by a single `argmax`
   — checking `acf[2*best_lag]`/`acf[best_lag//2]` against the same
   confidence-ratio gate already in place costs one or two extra array
   lookups, no new FFT, no new window. Octave-locking is documented in
   the literature (this survey's rhythm-tempo doc) as *the* most common
   beat-tracker failure mode, both classic and ML. This is the single
   cheapest, most literature-backed win in this whole survey.

3. **Prototype NNLS-based approximate note transcription ahead of
   `chroma.fold()`.** The one technique in the whole survey with a
   *specific, measured* accuracy improvement (+12pp on hard chords)
   directly targeting a class of failure this project's own docs already
   name as open (`docs/DECISIONS.md`'s harmonic-collision residual after
   issues #67/#68). Zero new dependency (`scipy.optimize.nnls` or a
   hand-rolled NumPy iterative solve). Real cost is unknown — a per-frame
   convex solve is heavier than the current FFT+matmul chroma fold — so
   this needs a genuine timed prototype against Pi Zero 2 W-class
   hardware (or at minimum the existing acoustic suite's `chords`/
   `density` tiers) before any adoption call, not a blind swap.

4. **Time-box a from-source read of SwiftF0's actual inference code**
   before anything else neural-shaped gets seriously considered. Its
   claimed no-DL-runtime, pure-NumPy inference (if real) is the one thing
   in this entire survey that would change the "no ML on the live path"
   calculus — but that claim comes from absent-dependency inference in
   package metadata, not from reading `swift_f0/*.py`. This is a cheap
   (hours, not a build), high-information first step before any
   prototype-hardware-timing investment.

### Mid-term: optional heavier backend, selectable by hardware tier / explicit opt-in

5. **basic-pitch (ONNX) as an optional `virtualnote transcribe` backend,
   never a live-path change.** This is squarely batch-scoped: CQT's
   >1s-buffering requirement plus ~120ms model latency rules it out for
   the live per-hop path on structural grounds, independent of raw
   inference speed — this isn't a "too slow on Pi" call, it's "doesn't
   fit the causal shape of the problem at all." But `batch_transcribe.py`
   already has an accepted precedent (`librosa`, isolated to exactly this
   kind of offline module) for exactly this shape of dependency exception,
   and onnxruntime's aarch64 wheel situation is genuinely better than
   aubio's/essentia's — official PyPI wheels exist for 64-bit Pi OS,
   unlike either of those. Recommend gating behind an explicit CLI flag
   (`virtualnote transcribe --backend basic-pitch`, falling back to the
   existing chroma+multipitch path when `onnxruntime`/the model file
   aren't present) rather than making it a hardware-auto-detected default
   — mirrors `menu_perf_mode`'s existing "explicit override available,
   sane default otherwise" pattern. **Not recommended to build
   proactively** — per `oss-landscape-transcription-and-prior-art.md`'s
   own conclusion, this is worth doing only once real (non-synthetic)
   `virtualnote transcribe` usage surfaces a concrete polyphonic-accuracy
   complaint, not preemptively.

### Long-term, bigger bet (explicit tradeoffs, no action recommended yet)

6. **A genuinely hardware-tiered live neural pitch backend** (SwiftF0
   confirmed-pure-NumPy, or SPICE/CREPE-tiny behind a TFLite/PyTorch
   runtime if SwiftF0's no-runtime claim doesn't hold up) — used only on
   desktop-class hardware, hand-rolled YIN remaining the unconditional
   default everywhere else, same shape as `menu_perf_mode`'s existing
   full/perf split. This is a large bet: a wholly new dependency chain
   even if scoped to one hardware tier, meaningfully more test/validation
   surface, and it cuts directly against this project's own repeated,
   explicit prioritization of portability over raw accuracy (mic-default
   design, the grand-staff decision, the aubio/librosa rejection itself).
   **Do not pursue without a concrete, currently-unmet accuracy need** —
   nothing in this survey identifies one; the project's own hardest bugs
   (#69, #71) are honest statistical limits of a single-93ms-window
   technique under noise, and it's genuinely unknown whether a neural
   tracker does better on *this project's actual signal* (played
   instruments via a real mic, not the speech/singing-heavy eval sets
   SwiftF0/CREPE/pYIN are benchmarked against) without direct testing.

7. **A transient/onset classifier for the percussion-phantom-note
   residual (issue #75).** `docs/DECISIONS.md` itself states closing #75
   "would need a genuine transient/onset classifier, a materially bigger
   feature than this issue's scope" — this survey found no ready-made,
   Pi-feasible, off-the-shelf component for exactly this (madmom's own
   onset/beat-activation RNNs are the closest thing in the literature,
   and are explicitly too heavy per the rhythm-tempo doc's own
   architecture analysis). A from-scratch small classifier (even a
   hand-tuned decision rule over multiple engineered features — attack
   sharpness, spectral centroid/flatness, decay-envelope shape — trained
   or tuned against `scripts/acoustic_pipeline_test.py`'s synthesized
   percussion fixtures) is a real, scoped research project in its own
   right, not a library-adoption decision — flagged here as a legitimate
   long-term direction explicitly because the project's own docs already
   named it as the missing piece, not because this survey found a
   library that does it.

## 4. Explicit conflicts with `docs/DECISIONS.md` / the existing landscape docs

Checked every recommendation above against what's already been tried,
rejected, or settled, per this task's explicit instruction not to
re-propose something already rejected without flagging the conflict.

- **No conflict — aubio.** Every angle here (yinfft/hfc/complex accuracy
  numbers) reconfirms, not overturns, the original "Hand-rolled YIN
  instead of `aubio`" decision. Recommendation 1 above takes only aubio's
  *math* (a formula for a novelty function), never the library itself —
  this is deliberately not "add aubio as a dependency."
- **No conflict — essentia.** `oss-landscape-chord-multipitch.md` and
  `oss-landscape-transcription-and-prior-art.md` already treated it as
  not worth adopting on accuracy grounds; this pass adds a *harder*,
  independently-verified reason (32-bit-only ARM wheels, a direct
  conflict with the explicit 64-bit-Pi-OS decision in `CLAUDE.md`'s Key
  design decisions) rather than reconsidering it.
- **No conflict — madmom.** Same outcome, reinforced by a maintenance-
  inactivity signal this pass found independently. Not recommended at
  any tier.
- **Not a conflict, a scoping clarification — CREPE/basic-pitch/MT3 for
  the live path.** `CLAUDE.md` never explicitly ruled out neural pitch
  models by name (the "hand-rolled YIN instead of aubio/librosa" decision
  is about *classical* DSP library dependency risk, since YIN was easy to
  hand-roll) — but the *spirit* of that decision (avoid a heavy,
  ARM-wheel-risky dependency chain for the live path) applies just as
  hard to a neural runtime, arguably harder. Nothing here proposes
  reopening that decision for the live pipeline; recommendation 6 is
  explicitly gated behind "desktop-tier only, no action without a
  concrete accuracy need."
- **Potential tension worth naming directly — the harmonic-collision
  "inherent limitation" framing.** `docs/DECISIONS.md` documents the
  root/3rd-harmonic collision as unresolvable "from a single hop's
  magnitude spectrum alone" after three rejected fix attempts (tolerance
  narrowing, magnitude-consistency check, self-corroborating-harmonic
  check) — all three of those rejected attempts share one property: they
  all operate on **a single hop's magnitude spectrum**, exactly the
  constraint the docs cite as the reason none of them can work. The NNLS
  recommendation above (#3) doesn't reopen any of those three specific
  rejected mechanisms — it replaces the peak-picking step itself with a
  different mathematical operation (constrained least-squares against a
  harmonic dictionary, still single-hop) — so it isn't a re-proposal
  either, but it's honest to note it's *also* still single-hop, and there
  is no strong a priori reason to expect it dissolves the identical
  spectral-identity problem (a peak at exactly 3x another peak's frequency
  really is ambiguous from magnitude alone, regardless of which
  single-hop algorithm reads that magnitude). Its documented win
  (Mauch & Dixon, +12pp on hard chords) came from a different mechanism —
  suppressing *spurious* chroma bin pollution from misfolded harmonics
  broadly, not specifically resolving exact-coincidence collisions — so
  frame this prototype as "worth trying because it measurably helps
  *chord accuracy generally*," not as a claimed fix for the specific
  documented-unfixable collision case.
- **No conflict, but explicitly not recommended — Cemgil-style joint
  rhythm quantization.** `oss-landscape-rhythm-tempo.md` already reached
  this conclusion directly: real, well-evidenced prior art, but it
  answers a different question than `duration_class_for_beats()` asks,
  and the project's own stated practice (issues #71, #75) is not to chase
  a fix without a concrete, reproduced complaint — this survey doesn't
  add a new complaint, so it isn't recommended here either.
- **No conflict — this project's general "don't fix without a concrete
  complaint" posture.** Every mid/long-term item above is explicitly
  gated on a future concrete symptom (batch transcription accuracy
  complaints, a live accuracy need not yet identified) rather than
  proposed as work to start now — consistent with how `docs/DECISIONS.md`
  itself repeatedly declines to chase issues #68's residual gap, #75, and
  #70's real-audio-jitter case absent new evidence.

## Sources

New sources this pass added beyond the four existing landscape docs
(themselves fully cited in their own Sources sections):

- piwheels essentia project page (confirms 32-bit/armhf-only wheels):
  https://www.piwheels.org/project/essentia/
- essentia install docs (independently confirms the 32-bit-only
  statement): https://essentia.upf.edu/installing.html
- madmom-prebuilt / madmom PyPI health signals (inactive release
  cadence): https://libraries.io/pypi/madmom-prebuilt ,
  https://snyk.io/advisor/python/madmom
- onnxruntime PyPI project page (confirms official aarch64 manylinux
  wheels): https://pypi.org/project/onnxruntime/
- Raspberry Pi forum threads on onnxruntime aarch64 install experience
  (corroborating, non-primary): https://forums.raspberrypi.com/viewtopic.php?t=313139
- SPICE paper/blog (Gfeller et al., arXiv:1910.11664; Google AI blog) —
  cross-checked against the pitch-detection landscape doc's own citation
  of the same source.

This project's own files, read in full or in relevant part before
writing this document: `CLAUDE.md`, `docs/DECISIONS.md` (all ~2090
lines), `pitch_detect.py`, `multipitch.py`, `onset_detect.py`,
`tempo_tracker.py`, `config.py`, and all four existing
`docs/research/oss-landscape-*.md` files in full.
