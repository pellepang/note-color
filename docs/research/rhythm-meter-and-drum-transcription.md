# Beat, downbeat, meter, barlines and drums: what the rhythm stack can honestly claim

Research for issue
[#127](https://github.com/pellepang/note-color/issues/127), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"). The map reverses map #24's ticket
[#31](https://github.com/pellepang/note-color/issues/31), which deferred
time-signature detection as a research-frontier problem, on the grounds
that this converter's output is a **correctable guess**. This document
establishes what that guess can honestly claim, field by field.

Three prior documents were read in full first and are not re-derived here:

- [`docs/research/pretrained-transcription-weights.md`](pretrained-transcription-weights.md)
  (#125) — **no open pretrained model emits the score layer** (barlines,
  meter, key, note values). PM2S is the one usable open MIDI-to-score
  toolkit, and it is classical-piano-trained. Weight and code licences
  diverge per project, and **this repo has no LICENSE file**, which puts
  the licence question on the critical path.
- [`docs/research/eval-harnesses-and-corpora.md`](eval-harnesses-and-corpora.md)
  (#124) — the measurement layer. `mir_eval` scores note events and beats
  but **no notation**; `musicdiff` and MV2H's Meter/Note-Value sub-metrics
  carry that half. And **`madmom` does not build on Python 3.14**, measured
  on this repo's own venv.
- [`docs/research/oss-landscape-rhythm-tempo.md`](oss-landscape-rhythm-tempo.md)
  — this repo's own earlier survey, written for the *live* path against
  Pi-class constraints. It predates the 2024–2026 work and is brought
  current in §1.5 below.

Everything here is sourced. Claims I could not verify from a primary
source are marked **unverified** in place. Things actually executed
against this repo's Python 3.14.7 venv, or a throwaway venv on the same
machine, are marked **measured here**, following #124's convention.

## Questions

1. What beat/downbeat tracker is current, accurate, **and installable on
   Python 3.14** under a licence this repo can live with? madmom is the
   field default and #124 ruled it out on both counts — what replaces it?
2. **Time signature / meter**: what genuinely exists, at what accuracy,
   over which candidate meters? Is a small candidate set (4/4, 3/4, 6/8,
   2/4) meaningfully more tractable than open-ended inference?
3. **Drum transcription** — for notating drums, and as a timing oracle.
   Does a transcribed drum pattern measurably improve downbeat/meter
   estimation over a raw onset envelope? What are the licences?
4. **Quantization and barlines over a whole song**, including
   non-metronomic playing: one tempo value or a tempo curve? Do joint
   tempo/quantization approaches beat this repo's current independent
   nearest-value snapping?

## Summary of the answer, before the evidence

- **Beat This!** (ISMIR 2024, CPJKU) is the recommendation for beats and
  downbeats: **MIT for code *and* weights**, 89.1 beat F1 / 78.3 downbeat
  F1 on GTZAN, CPU is the default device, and it **resolves on Python
  3.14 — measured here**. It needs madmom only for an *optional* DBN it
  does not use by default and does not need to beat the state of the art.
- **madmom is less dead than #124 concluded, in one specific way that
  matters.** PyPI 0.16.1 fails, as #124 found — but the diagnosis is
  sharper: on this machine it *builds* and then fails at `import madmom`
  on a Python-3.10-era `from collections import MutableSequence`.
  **Git `main` has that fixed, and builds, imports and correctly decodes
  downbeats on Python 3.14 — measured here.** Its *source code* is
  3-clause BSD; only its `.npy`/`.pkl` model files are CC BY-NC-SA, and
  the DBN downbeat decoder **loads no model at all**. So the licence-safe,
  installable combination "Beat This! activations + madmom's DBN" exists.
- **Meter is not a solved inference problem and should not be sold as
  one.** The field's own default candidate set is literally
  `beats_per_bar=[3, 4]`; the best open MIDI-to-score model restricts the
  numerator to `{2,3,4,6}` and the denominator to `{2,4,8}` with an
  explicit "other" class; and the **denominator is essentially never
  recovered from audio at all** — 4/4 vs 2/2, or 3/4 vs 6/8, is a
  notational choice the signal does not determine. A small candidate set
  is not a convenience, it is *how the field actually does it*.
- **Drums help downbeats, and the help is concentrated exactly where
  drums are.** Drum-aware separation lifts downbeat F1 from 0.699 to
  0.775 on drum-saturated material and from 0.399 to 0.415 on solo piano.
  But the published evidence is for **separated drum audio as an extra
  input channel**, not for a *transcribed* drum pattern. Nobody has
  published the latter comparison. Say so rather than assume it.
- **Store a tempo curve, not one number.** The clearest evidence in this
  whole document is PM2S's MV2H table: MuseScore and Finale quantize
  against a single constant tempo and score **15.3 and 9.9** on metrical
  alignment, against PM2S's 61.7 — and the paper attributes their
  collapse specifically to constant-tempo quantization.
- **Cemgil-style joint quantization is the right idea but the wrong
  packaging in 2026.** The modern form of it is "quantize against a
  neurally-tracked beat grid rather than against an independently
  estimated global tempo," and it wins: 97.3% onset F1 with a beat grid
  supplied, versus PM2S's end-to-end 15.55 → 12.30 MUSTER onset error.
  This repo's `duration_class_for_beats()` is not wrong so much as
  *upstream-starved* — it snaps against a live tempo scalar, and the fix
  is to give it a beat grid, not to replace the snapping.

## 1. Beat and downbeat tracking

### 1.1 Beat This! — the recommendation

Foscarin, Schlüter and Widmer, "Beat this! Accurate beat tracking without
DBN postprocessing," ISMIR 2024 (arXiv:2407.21658). Its thesis is that
the DBN post-processing everyone inherited from madmom is actively
harmful for general repertoire, because it "introduces constraints on the
meter and tempo" that break on pieces with changing time signatures or
tempi outside roughly 55–215 BPM. It replaces the DBN with peak-picking
over ±70 ms neighbourhoods and a 0.5 probability threshold, trains on 18
datasets (4,556 tracks, versus 3,144 for the prior best), and wins anyway.

Published F1 (8-fold cross-validation, and GTZAN as a held-out test set):

| Dataset | Beat F1 | Downbeat F1 | Prior best (Hung et al.), beat / downbeat |
|---|---|---|---|
| Ballroom | 97.5 | 95.3 | 96.2 / 93.7 |
| Beatles | 94.5 | 88.8 | 94.3 / 87.0 |
| Hainsworth | 91.9 | 80.0 | 87.7 / 74.8 |
| Harmonix | 95.8 | 90.7 | 95.3 / 90.8 |
| RWC Pop | 96.1 | 93.7 | 95.0 / 94.5 |
| **SMC** | **62.7** | — | 60.5 / — |
| **GTZAN (test)** | **89.1 ± 0.3** | **78.3 ± 0.4** | 88.7 / 75.6 |

Two of these numbers matter more than the rest.

**SMC 62.7.** SMC is the deliberately hard, expressive, tempo-unstable
corpus. It is roughly 35 F1 points below Ballroom. This is the same
repertoire-dominates-algorithm finding this repo's earlier
`oss-landscape-rhythm-tempo.md` drew from madmom's own 0.52-vs-0.83
spread, and it has *not* gone away with transformers — it moved from
0.52 to 0.627. **Expressive, rubato, non-metronomic material is still
where beat tracking fails, and that is exactly the material a "correctable
guess" has to be honest about.**

**GTZAN downbeat 78.3, against beat 89.1.** Downbeats are consistently
~11 points worse than beats, and downbeats are what barlines are made of.
Note also that the paper's own DBN ablation goes the *wrong* way for F1
(89.1 → 88.1 beat, 78.3 → 77.4 downbeat with the DBN) while going the
*right* way for the continuity metrics (downbeat CMLt 67.3 → 73.3). That
is a real trade-off for this map: the DBN produces a metrically *coherent*
bar sequence more often, at slightly lower per-downbeat F1. For a score
converter, where a bar grid that stays consistent is worth more than a
handful of individually-correct downbeats, **the DBN variant is probably
the right pick despite the lower F1** — and it is also the variant that
gives a meter (§2.2).

Practical facts, read from the repo source rather than the README:

- `pyproject.toml` declares `license = "MIT"` with `license-files =
  ["LICENSE"]`; the README states MIT covers **code and model weights**.
  GitHub API: MIT, 382 stars, last push **2026-05-28**, 4 open issues.
- Dependencies are `numpy`, `torch>=2`, `torchaudio`, `einops`,
  `rotary-embedding-torch`, `soxr`. No madmom, no librosa, no TensorFlow.
- `beat_this/inference.py` defaults to `device="cpu"` and `dbn=False`.
  madmom appears **once**, as a lazy import inside
  `Postprocessor.__init__` guarded by `type == "dbn"`. The default
  ("minimal") path never touches it.
- **Measured here:** `pip install --dry-run beat-this` on this repo's
  Python 3.14.7 venv resolves to `beat-this-1.1.0` with `torch-2.14.0`,
  `torchaudio-2.11.0`. It pulls the full CUDA wheel stack on Linux
  (`nvidia-*`, `triton`, `cuda-toolkit`), several GB, on a machine with no
  GPU — installing a CPU-only torch first avoids that. A dry-run
  resolution is not proof that it imports and runs.

### 1.2 madmom: the finding is sharper than "does not build"

#124 measured `pip install --dry-run madmom` failing at wheel-build with
`ModuleNotFoundError: No module named 'Cython'`. That is correct for a
clean venv. What was not established, and matters:

**Measured here (1) — PyPI 0.16.1 builds if Cython is present, then fails
at import.** madmom 0.16.1 is in fact already installed in this repo's
`.venv` (someone got the Cython extensions to compile). `import madmom`
raises:

```
File ".../madmom/processors.py", line 23, in <module>
    from collections import MutableSequence
ImportError: cannot import name 'MutableSequence' from 'collections'
```

The ABCs moved to `collections.abc` in Python 3.10. The failure is a
one-line 2018-era import, not a deep incompatibility.

**Measured here (2) — git `main` works on Python 3.14.** `main`'s
`processors.py` line 23 reads `from collections.abc import
MutableSequence`, and its most recent commit (2024-08-25, "CI and NumPy
compatibility updates (#540)") explicitly covers "Fix Cython and NumPy 2
compatibility". In a throwaway Python 3.14 venv on this machine:

```
pip install --no-build-isolation "git+https://github.com/CPJKU/madmom.git@main"
→ Successfully built madmom-0.17.dev0 (cp314 wheel, 26.6 MB)
```

and it imports and decodes correctly:

```python
from madmom.features.downbeats import DBNDownBeatTrackingProcessor
p = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
p(activations)   # 30 s of 100 fps activations → 0.75 s, correct 4/4 grid
```

It recovered the intended 4-beat bar from the `[3, 4]` candidate set and
returned beat positions 1,2,3,4,1,2,3,4,…, in **0.75 s for 30 s of
activations (~40× real time on this CPU)**. `madmom.evaluation` also
imports.

**Measured here (3) — the licence split is finer than "CC BY-NC-SA".**
madmom's `LICENSE` says source files (`.py`, `.pyx`, `.pxd`, `.c`) are
**3-clause BSD**, and only "data and model files" (`.npy`, `.npz`, `.h5`,
`.hdf5`, `.pkl`, `.mat`) are CC BY-NC-SA 4.0. `DBNDownBeatTrackingProcessor`
constructs its HMM transition and observation models analytically from
`beats_per_bar`; it loads **no model file** — confirmed by passing it a
bare NumPy array and getting beats back. The NC obligation attaches to
`RNNBeatProcessor`/`RNNDownBeatProcessor` (which load trained `.npz`
models), not to the decoder.

**So the correct verdict is narrower than "do not depend on it":** do not
depend on PyPI madmom, do not ship its pretrained models, and do not
assume a release is coming (last PyPI release 2017/2018; last commit
2024-08-25). But a **git-pinned madmom used only as a BSD-licensed DBN
decoder over someone else's MIT activations** is installable, licence-
clean and fast. It is still a build-from-source Cython dependency with a
dead release cadence, so it should be an *optional* extra, never a hard
requirement — same posture this repo already takes toward `[sf2]`.

### 1.3 The online trackers — BeatNet, BeatNet+, BEAST — are not for this map

Map #123 is offline-only, so the online literature is relevant only as a
ceiling check. It sits well below Beat This!:

| System | GTZAN beat F1 | GTZAN downbeat F1 |
|---|---|---|
| BeatNet (ISMIR 2021), online | 75.44 | 46.49 |
| BeatNet (ISMIR 2021), offline | 80.64 | 54.07 |
| BeatNet+ (TISMIR 2024), online | 80.62 | 56.51 |
| **Beat This! (ISMIR 2024), offline** | **89.1** | **78.3** |

BeatNet+ (Heydari & Duan, TISMIR 2024, CC BY 4.0, code at
`mjhydri/BeatNet-Plus`) adds an auxiliary training strategy for
percussion-invariant representations and a 4-layer LSTM, and closes much
of BeatNet's online/offline gap — but it is still ~22 downbeat F1 points
behind an offline transformer. **Downbeat tracking is where the online
constraint costs the most, and downbeats are what this map needs.**

Installability note: **measured here**, `pip install --dry-run BeatNet`
does resolve on Python 3.14 (`BeatNet-1.1.1`, `torch-2.14.0`) — but its
declared dependencies omit madmom while its README requires you to
pre-install it, and warns of incompatibility at "Python >= 3.10 and
NumPy >= 1.24" recommending Python 3.9. So a clean dry-run resolution is
misleading here; the real dependency is the one that fails.

### 1.4 Beat Transformer — superseded, but its ablation is the drum evidence

Zhao, Xia & Wang, ISMIR 2022 (arXiv:2209.07140) feeds a transformer
*demixed* spectrograms (Spleeter: drums, bass, vocals, piano, other) with
alternating time-wise and instrument-wise attention. Its scores are below
Beat This! (GTZAN beat 0.885, downbeat 0.714), so it is not the pick —
but its **demixing ablation is the single most decision-relevant
experiment for map #123's drums-as-timing-oracle decision**, and it is
read here directly from the paper's own table:

| Dataset | Beat F1, no demix → demix | Downbeat F1, no demix → demix |
|---|---|---|
| Ballroom | 0.968 → 0.968 | 0.930 → **0.941** |
| Hainsworth | 0.902 → 0.902 | 0.721 → **0.748** |
| Harmonix | 0.954 → 0.954 | 0.887 → **0.898** |
| GTZAN | 0.876 → 0.885 | 0.686 → **0.714** |

**Demixing buys ~1–3 absolute points of downbeat F1 and essentially
nothing for beats.** That is a real but modest effect.

⚠️ A secondary summary of this paper (surfaced during this research and
recorded here so the error is not repeated) reported "+9.9% GTZAN
downbeat". That is wrong: it is a *relative* restatement of the +2.8
absolute-point gain, and other rows in the same summary did not match the
paper's table at all. The numbers above are read from the paper's own
results table. Treat any demixing gain quoted above ~3 points as
unverified.

### 1.5 Bringing `oss-landscape-rhythm-tempo.md` current

That document's conclusions were correct for the *live* path and are
unchanged for it. Three things have moved, all in the offline direction
this map cares about:

1. **The DBN is no longer required for state-of-the-art accuracy.** That
   document treats madmom's RNN+DBN as the ceiling. Beat This! beats it
   with no DBN at all, and argues the DBN's meter/tempo priors are a
   liability on general repertoire.
2. **The madmom install verdict needs the refinement in §1.2**: not
   "unmaintained and unavailable", but "unreleased since 2017, buildable
   from git, BSD decoder, NC models".
3. **The Cemgil-vs-nearest-value question has a modern answer** (§4), and
   it is not the one that document reached. Its recommendation — leave
   `duration_class_for_beats()` alone until a concrete symptom appears —
   remains right for the *live* path, which has no beat grid to quantize
   against. For the offline path, which will have one, §4 supersedes it.

The document's own §5 recommendation ("do not port Cemgil; note-color
separates tempo estimation from duration snapping") rested on the premise
that the tempo estimator upstream is good. On the offline path it will be
*much* better, and that changes the answer rather than the argument.

## 2. Time signature and meter

This is the section #31 deferred, and the honest answer is worth stating
plainly before the evidence: **meter inference is not solved, the field
works around it with a small candidate set rather than solving it, and the
denominator is not recoverable from audio at all.**

### 2.1 What the survey actually says

Bhattarai et al., "Time Signature Detection: A Survey," *Sensors* 21(19),
2021 — the survey #31 cited — reviews "more than 110 publications". Its
reported per-system accuracies are, on their own, not bad:

| Method | Reported accuracy | On what |
|---|---|---|
| Self-Similarity Matrix (2004) | 95.5% | Greek music samples |
| Rhythm SSM (2014) | 93% | MIDI keyboard scores |
| Temporal Convolutional Nets (2019) | 93% | own annotated dataset |
| Comb filter (2011) | 88.7% | Indian classical |
| SVM (2013) | 90% | generated samples |
| CNN (2016 / 2017 / 2019) | 90% / 88% / 89% | mixed / MSD+MTAT / MSD |
| Auto-correlation (2003) | 73.5% | percussive music |
| Audio Similarity Matrix (2007) | 75% | commercial recordings |

The pattern is the point. **Every high number is on a narrow, favourable
corpus** — Greek folk, MIDI scores, Indian classical, generated samples —
and the two entries evaluated on *commercial recordings* score 73.5% and
75%. The survey's coverage note is the other half: systems address simple
(2/4, 3/4, 4/4) and compound (6/8, 9/8, 12/8) meters, while "irregular
signatures like 5/8, 7/8 and 11/8 received limited coverage," and it
concludes the task is "a difficult one," with tempo significantly
affecting audio-side accuracy.

So #31's "<50% on non-simple meters" is a defensible reading of the
field's *irregular-meter* frontier, and this map is right not to promise
that. It would be an overreading to extend it to 4/4-versus-3/4 on
ordinary pop-rock, which is where the numbers above actually live.

### 2.2 In practice, meter is a small candidate set — that is not a shortcut

Read directly from source, not from prose about it:

- **madmom**: `DBNDownBeatTrackingProcessor(beats_per_bar=...)`. Beat
  This!'s own optional DBN path instantiates it as
  `beats_per_bar=[3, 4]` (`beat_this/model/postprocessor.py:32`). Two
  candidates. The decoder runs one HMM per bar length and Viterbi picks
  the winner, so meter falls out of the same decode that produces
  downbeats — no separate classifier.
- **PM2S** (the one open MIDI-to-score toolkit, ISMIR 2022 best paper)
  defines its output vocabulary as: time signature numerator
  `tn ∈ {0, 2, 3, 4, 6}` and denominator `td ∈ {0, 2, 4, 8}`, where **0
  means "some other value"**. Five numerators and four denominators, with
  an explicit escape hatch.
- **"Skip That Beat"** (Morais, McFee & Fuentes, LAMIR 2024,
  arXiv:2502.12972) infers meter from beat positions by counting where
  the bar-position counter resets and taking the most frequent transition
  point — and states outright: "**The denominator is always 4 for the
  tracks we are using in this work.**"

That last one is the finding this map most needs. **Audio meter
estimation, as practised, estimates a numerator.** 4/4 versus 2/2, or 3/4
versus 6/8, is a *notational* distinction — same acoustic evidence,
different engraving convention — and no system surveyed here recovers it.
For a converter writing MusicXML, the denominator must be a convention
(default 4, or 8 when the numerator is 6/9/12) plus a user correction, not
a claimed inference.

**So: yes, a small candidate set is meaningfully more tractable.** Not
because the problem gets easier, but because a 2–5-way choice can be
folded into the downbeat decode that is happening anyway, whereas
open-ended inference needs a classifier nobody has trained on band audio.

### 2.3 The number for non-4/4, and why it is that low

"Skip That Beat" is the sharpest available evidence on *why* non-4/4
fails. Its training-data audit: of the 2,216 training tracks in the
reference TCN work, **1,120 are 4/4**, 882 have no beat-position
annotations at all, and the remaining **214 span four other time
signatures**. Their own pooled corpus is 28.41 h / 1,283 tracks, of which
17.48 h (1,096 tracks) is 4/4, 2.9 h (87 tracks) 3/4, 3.82 h (59 tracks)
2/4, and 1.21 h (41 tracks) everything else. The paper's own framing:
"This underrepresentation is not a problem for beat tracking, but it
affects downbeat tracking."

Their Table 1 (averaged over all time signatures; note both models are
deliberately weakened — the DBN is replaced with peak-picking so the
augmentation effect is visible, which the paper says costs about 5%):

| Model / training set | Beat F1 | Downbeat F1 |
|---|---|---|
| BayesBeat, 4/4-only baseline | 0.70 | **0.41** |
| BayesBeat, + 2/4 and 3/4 augmentation | 0.71 | **0.49** |
| TCN + peak-picking, baseline | 0.72 | **0.36** |
| TCN + peak-picking, augmented | 0.74 | **0.36** |

Augmentation buys BayesBeat 8 points of downbeat F1 and buys the TCN
nothing on average (it helps 2/4 and 3/4 specifically, at the cost of 4/4
being misread as 2/4 and vice versa — "those errors make sense given the
perceptual similarity between those meters").

And the out-of-domain collapse, quoted in the same paper from Maia et al.:
a TCN trained on Western music, evaluated on **BRID** (Brazilian samba, all
2/4), scores **F-measure 0.096, CMLt 5.9**. Ninety-six thousandths.
Meter tracking does not degrade gracefully out of domain; it falls over.

### 2.4 The honest accuracy expectation for this map

Nobody publishes "time signature accuracy on a band mix" — not the
survey, not PM2S (which predicts time signature and **never reports its
accuracy**), not Beat This! (which does not predict it at all). So the
number has to be reasoned from what *is* published, and marked as such:

| Field | Honest expectation | Basis |
|---|---|---|
| Beat positions | ~0.89 F1 on genre-diverse pop/rock; ~0.63 on expressive/rubato | Beat This! GTZAN / SMC |
| Downbeat positions | ~0.78 F1 on genre-diverse pop/rock | Beat This! GTZAN |
| Meter **numerator**, 4/4 vs 3/4 on Western pop-rock | good, but **unmeasured**; derived from downbeats, so bounded above by 0.78 downbeat F1 | inference from §2.2 |
| Meter numerator, anything else (2/4, 6/8, 5/4, 7/8) | poor. Assume it will be wrong and needs correcting | §2.3 |
| Meter **denominator** | not inferred. A convention. | §2.2 |
| Meter *changes* mid-piece | not attempted by anything surveyed here | — |

This is a defensible basis for map #123's "commit a correctable guess"
decision, and it is *not* a basis for showing the user a time signature
without an obvious way to change it. The guess is worth committing
because 4/4 is right most of the time in this repertoire and a downbeat
grid is worth having even when the meter label is wrong — not because
meter inference works.

## 3. Drum transcription

### 3.1 Does a drum stem improve downbeat and meter estimation?

**Yes, and the effect is concentrated exactly where drums are.** The
cleanest evidence is Chiu, Su & Yang, "Drum-Aware Ensemble Architecture
for Improved Joint Musical Beat and Downbeat Tracking," IEEE SPL 28
(2021), arXiv:2106.08685 — a three-way ensemble tracking the mixture, the
drum stem and the non-drum stem separately, fused adaptively. Separation
is a three-layer BLSTM in the Open-Unmix mould, supervised against
Spleeter output. Code at `SunnyCYC/drum-aware4beat`.

Evaluated across four test sets ordered by drum presence:

| Test set (drum presence) | Beat F1, baseline → drum-aware | Downbeat F1, baseline → drum-aware |
|---|---|---|
| ASAP (0% — solo classical piano) | 0.585 → 0.596 | 0.399 → 0.415 |
| Merged (72.7%) | 0.884 → 0.891 | 0.715 → **0.743** |
| Rock (83.2%) | 0.908 → 0.911 | 0.842 → 0.853 |
| HJDB (99.2% — hardcore/jungle/DnB) | 0.886 → 0.914 | 0.699 → **0.775** |
| Mean | 0.762 → 0.774 | 0.599 → **0.628** |

Downbeat gains: **+7.6 points on drum-saturated material, +1.6 points on
music with no drums at all.** Beat Transformer's demixing ablation (§1.4)
points the same way at a similar magnitude. For a map whose target input
is a full band mix, this vindicates the drums-as-timing-oracle decision —
with the caveat that it is worth roughly one to eight downbeat F1 points,
not a step change.

**But note precisely what was measured.** Both papers feed *separated drum
audio* into the tracker as an additional input channel. **Neither
evaluates a transcribed drum pattern (a symbolic kick/snare/hi-hat event
list) as a downbeat or meter feature.** I found no paper that does. So the
supported claim is "separate the drums and give the tracker the stem",
and the unsupported one is "transcribe the drums and infer the meter from
the pattern." The latter is intuitively appealing — a backbeat on 2 and 4
is a strong meter cue — and is a legitimate thing for this map to
*prototype*, but it must be labelled as unproven rather than cited.

Two supporting observations, both real but neither a substitute for the
missing experiment: PM2S's own ablation found **onset the single most
useful input feature** for beat tracking, with the authors noting "people
can usually infer beat times from drum beats without knowing other
information"; and BeatNet+'s whole contribution is a representation
*invariant* to percussion amount — i.e. the field's current move is to
make trackers robust to drums being absent, not to lean harder on them.

### 3.2 The ADT tools, and their licences

| System | Year | Classes | Accuracy (F-measure) | Code licence | Weights / data licence | Python 3.14 |
|---|---|---|---|---|---|---|
| **ADTOF** | ISMIR 2021 / Signals 2023 | 5 (kick, snare, hi-hat, toms, cymbals) | ~0.78–0.79 on MDB (5-class); 0.89 MDB / 0.85 ENST / 0.63 RBMA in the 2025 stem-separation follow-up | **CC BY-NC-SA 4.0** | **CC BY-NC-SA 4.0** | TF/Keras + **madmom**; "tested on macOS with Python 3.10" |
| **ADTOF-pytorch** (`xavriley/ADTOF-pytorch`) | 2025 | 5 | −0.2% vs ADTOF | **no LICENSE file** — and derivative of NC-SA weights | inherits NC-SA | torch only, no TF/madmom |
| **STAR Drums** | TISMIR 2025 | 18 | 0.79 MDB 5-class (vs ADTOF 0.78–0.79); 0.67 MDB 18-class | dataset + code released | **CC BY 4.0** | no pretrained weights released |
| **Noise-to-Notes (N2N)** | 2025 (arXiv 2509.21739) | 7 + velocity | E-GMD 89.68, MDB 87.86, IDMT 94.90 — best published | paper CC BY 4.0 | **no code or weights released** | — |
| **Omnizart** (drum module) | 0.6.3, 2026-05-31 | — | not established here | MIT | not established here | hard-depends `madmom>=0.16.1`; needs `tf-nightly` on 3.14 |

Reading of that table:

- **#125's flag on ADTOF is confirmed and is worse than flagged.** *Both*
  the code and the data are CC BY-NC-SA 4.0 — not just the weights. The
  README states this directly. ADTOF-pytorch removes the TensorFlow and
  madmom dependencies (which is genuinely useful) but has **no LICENSE
  file at all**, which is not permission; and it is a derivative of NC-SA
  weights either way. Given that this repo has no LICENSE file and #125
  already put that on the critical path, **ADTOF cannot be a shipped
  dependency of anything this project distributes.** It is fine for
  private evaluation, which is a different thing and should be recorded
  as such.
- **STAR Drums is the licence-clean path and the reason to be hopeful.**
  CC BY 4.0, 124.5 h (114.7 h train), 18 classes, real melodic
  instruments and vocals with synthesised drum stems, and it matches
  ADTOF at 5 classes (0.79 vs 0.78–0.79 on MDB Drums) while going far
  beyond it in class coverage. It ships no pretrained model, so using it
  means training one — real work, but on a corpus that can actually be
  redistributed alongside a result.
- **Omnizart is a trap on this platform.** MIT, genuinely maintained
  (0.6.3 released 2026-05-31, with explicit `tf-nightly` / `tf-keras-
  nightly` branches for `python_version >= "3.14"`), and — **measured
  here** — `pip install --dry-run omnizart` resolves on this venv. But
  that resolution is only clean because madmom 0.16.1 is *already
  installed* in this venv and pip therefore reports it satisfied. It
  does not import (§1.2). Omnizart's declared dependency is
  `madmom>=0.16.1`, which on Python 3.14 means a git build, plus
  `tf-nightly` (currently `2.22.0.dev20260905`) and a
  `tf-keras-nightly` pinned to a **2024-02-14 dev snapshot**. That is not
  a dependency footprint to put on a critical path.
- **N2N is the state of the art and is unusable**: no code, no weights.
  Its E-GMD number (89.68 vs ADTOF's 44.95) is a domain-shift artefact as
  much as a quality gap — E-GMD is electronic-drum-kit recordings, far
  from ADTOF's crowdsourced-chart training distribution — but its MDB and
  IDMT numbers (87.86 / 94.90 vs 86.87 / 94.67) are genuine, if narrow,
  wins. Design reference only, same status #125 gave the 2026 rhythm
  quantization work.

### 3.3 Consequence for the map

Drum *notation* and drums-as-*oracle* have different licence exposure,
and separating them is the useful move:

- As an **oracle**, this map does not need drum transcription at all. It
  needs a **drum stem**, which comes from source separation (in scope for
  #126 already) and can be fed to the tracker as an extra channel — the
  Chiu/Beat-Transformer result. No ADT model, no NC licence, no new
  weights.
- As **notation**, drums need a real ADT model, and the only
  licence-clean route today is training on STAR Drums (CC BY 4.0). That
  is a materially bigger commitment than "adopt a model", and it should
  be scoped as such rather than assumed away.

## 4. Quantization, tempo, and barlines over a whole song

### 4.1 One tempo value or a tempo curve? A curve, decisively

The evidence is PM2S's own MV2H comparison against the two commercial
tools that do exactly this job (Liu, Kong, Morfi & Benetos, ISMIR 2022,
best paper; classical piano, MIDI in, score out):

| Method | Fp (pitch) | Fvo (voice) | **Fme (metrical)** | Fva (note value) | Fha (harmony) | F (mean) |
|---|---|---|---|---|---|---|
| Finale v27 | 82.2 | 54.6 | **9.9** | 92.2 | 86.2 | 65.0 |
| MuseScore v3 | 10.0 | 65.0 | **15.3** | 95.0 | 84.5 | 54.0 |
| PM2S | 99.8 | 87.0 | **61.7** | 99.9 | 91.1 | 87.9 |

The paper's own diagnosis, quoted: MuseScore's "low performance on Fp is
caused by time shifts introduced when quantising notes according to a
**constant tempo estimated over the whole music piece**. Constant tempo
estimation also caused its low performance reported on Fme." A similar
limitation is found in Finale. PM2S wins because it "tracked tempo
changes during rhythm quantisation and preserved the expressiveness of
music performance as much as possible."

That is as direct an answer as this question gets. **A single global tempo
does not merely lose expression, it corrupts pitch-level alignment** —
MuseScore's Fp of 10.0 on *perfect MIDI input* is a quantization artefact,
not a transcription error. The score model must carry a tempo curve (a
per-beat or per-bar tempo series), and the MusicXML this repo writes
should carry the beat grid, not one `MetronomeMark`.

`score_editor_state.py` today stores exactly one `tempo_bpm` float per
`EditorScore`. That is fine for a hand-entered score and is a real
limitation for an imported one; it is the concrete schema consequence of
this section.

### 4.2 The honest ceiling on the score layer: Fme 61.7

Take the PM2S row seriously in the other direction too. **61.7 metrical
alignment is the best published number for open MIDI-to-score conversion —
on solo classical piano, from clean MIDI, with no transcription errors
upstream at all.** The paper says so itself: "the rhythm quantisation
performance (Fme) is far from satisfactory. Some typical errors include
double/half tempo error and errors introduced by missing/extra beat
predictions."

Map #123's target input is a band mix, transcribed by a model that #125
measured at ~0.60 note-onset F1 on *synthetic* three-instrument audio and
#124 measured at **37.87% onset F1 on real commercial band audio**. The
score layer sits downstream of that. **Nothing in this document supports
an expectation that barlines and note values on a real band mix will land
anywhere near 61.7.** The right posture is the map's own: commit a
correctable guess, measure it, and do not quote a number that was
produced on solo piano.

PM2S's own beat-level results, for completeness (MIDI input, piano):
beat F 85.7, downbeat F 63.3 for the beat/downbeat model; 86.2 / 69.8
when tempo is jointly predicted; against a 66.9 / 57.6 baseline. Note the
downbeat number, again, is the weak one.

### 4.3 Cemgil, and what replaced it

`oss-landscape-rhythm-tempo.md` read Cemgil, Desain & Kappen's "Rhythm
Quantization for Transcription" in full and concluded, correctly for the
live path, that its joint/correlated quantization answers a different
question than `duration_class_for_beats()` asks. The 2024–2026 work
resolves that tension in a way that is directly actionable here.

**Wachter, Murgul & Heizmann, "Transformer-Based Rhythm Quantization of
Performance MIDI Using Beat Annotations" (arXiv:2604.22290, 2026)** —
already noted by #125 — is Cemgil's idea in modern form: quantize
*jointly*, conditioned on a beat grid rather than on an independently
estimated global tempo.

- **97.3% onset F1, 83.3% note-value accuracy** on ASAP.
- **MUSTER ε_onset 12.30 versus end-to-end PM2S's 15.55** — a ~21%
  relative reduction in onset error.
- It **requires beat and downbeat annotations as a priori input** and
  assumes one-to-one correspondence between performance and score notes.
- Grid: **12 sub-beats per beat (32nd-note triplets)**, 48 note-value
  tokens, note values from a 16th-note triplet upward. **Tuplets are
  supported**; 32nd notes and irregular time signatures are not in
  training.
- **No code or weights released.** Design reference, not a dependency.

The same authors' earlier "Beat-Based Rhythm Quantization of MIDI
Performances" (arXiv:2508.19262, 2025) is the same idea; no code release
found either.

The one *usable* modern artefact is **MIDI2ScoreTransformer** (Beyer &
Dai, ISMIR 2024, arXiv:2410.00210) — an end-to-end transformer with
compound tokenization, "the first to directly predict notational details
like trill marks or stem direction from performance data", with **code
and models released under CC-BY-4.0** at
`TimFelixBeyer/MIDI2ScoreTransformer`. Its practical cost is high: Python
3.11, and it requires **custom forks of `music21`, `score_transformer`
and `muster` installed by hand, plus MuseScore**. Its MV2H/MUSTER table
was not retrieved (**unverified**).

### 4.4 What this means for this repo's own quantizer

Three separable findings:

1. **`duration_class_for_beats()`'s independent nearest-value snap is not
   the weak link — its input is.** It snaps a measured duration in beats
   against `_DURATION_CLASSES`, and the beats come from a live tempo
   scalar. The 2026 result says the win comes from conditioning on a
   *beat grid*, not from replacing the snapping rule. On the offline path
   Beat This! supplies exactly that grid. **Feed the grid in; leave the
   snapping alone until it is measured to be the problem** — which is
   also what `oss-landscape-rhythm-tempo.md` concluded, for a different
   reason that no longer applies.
2. **The tuplet gap becomes real on this path.** `_DURATION_CLASSES` has
   ten plain/dotted powers of two and no tuplet values; `log_import.py`
   documents deliberately omitting triplet grids "since
   `DURATION_CLASS_ORDER` has no tuplet values to write them as"; and
   `score_writer.py` records that issue #62 deferred tuplet detection.
   The reference system uses a 12-sub-beat grid because triplets are
   ordinary in the target repertoire. **A band-mix converter that cannot
   write a triplet will misnotate swing, shuffle and 6/8 outright** — and
   6/8 is on the map's own candidate meter list. This is a concrete,
   nameable scope item, not a nuance.
3. **`score_writer.py`'s existing 32nd-note *offset* quantization is a
   different thing and should stay.** It exists because music21 cannot
   express certain unquantized offsets as MusicXML rests — a format
   constraint, as its own docstring says. `log_import.py` already draws
   this distinction. Musical quantization against a beat grid is the new
   layer; neither replaces the other.

### 4.5 Barlines over a whole song, and non-metronomic playing

Barlines are downbeats plus a meter label, so their accuracy is bounded by
downbeat accuracy — **~0.78 F1 at best on genre-diverse pop/rock**, worse
on expressive material, and near-worthless out of domain (§2.3's BRID
0.096). Three consequences worth carrying into the spec:

- **Do not derive barlines from a beat-accumulator against a tempo
  estimate**, which is what `main.py`'s live `tab` view does and which
  CLAUDE.md already calls "explicitly approximate". Offline, place a
  barline at each *predicted downbeat*. That is not merely more accurate;
  it is the only formulation that survives a tempo change, because a
  downbeat is an event and a beat-accumulator is an extrapolation whose
  error compounds monotonically over a song.
- **Beat This!'s no-DBN design is the right posture for tempo drift.**
  Its whole argument against the DBN is that the DBN's tempo (55–215 BPM)
  and constant-meter priors break on real material. Whichever variant is
  chosen, the tempo curve should be read *back out* of the predicted beat
  times rather than imposed on them.
- **Expressive material is where this stack is weakest, and this repo
  already knows it.** SMC's 62.7 beat F1 and CMLt/CMLc's steeper drops
  (Beat This! GTZAN: beat F1 89.1 but beat CMLt 79.8, downbeat F1 78.3 but
  downbeat CMLt 67.3) both say the same thing: *continuous* correct
  tracking is meaningfully rarer than pointwise correct tracking. A score
  converter cares about continuity — one dropped bar shifts everything
  after it. **CMLt/AMLt, not just F-measure, should be in the harness
  #124 is building.** `mir_eval.beat` implements all of them.

## 5. Recommended rhythm stack

Offline path only; the live path is untouched, per the map's own scope.

1. **Source separation (#126) produces a drum stem**, which is used twice:
   as an extra input channel to the beat tracker (worth +1 to +8 downbeat
   F1 depending on how drum-heavy the mix is, §3.1) and, later and
   separately, as the input to drum notation.
2. **Beat This! (MIT, code and weights) for beats and downbeats.** CPU
   default, no madmom needed, resolves on Python 3.14 (measured). Expect
   ~0.89 beat / ~0.78 downbeat F1 on pop-rock, ~0.63 beat on expressive
   material.
3. **Meter numerator from the downbeat sequence**, over the candidate set
   `{4, 3, 2, 6}` — either as the mode of beats-between-downbeats from
   Beat This!'s minimal post-processing, or by running madmom's
   BSD-licensed, model-free `DBNDownBeatTrackingProcessor` over Beat
   This!'s activations with `beats_per_bar=[2, 3, 4, 6]`, which decides
   meter and downbeats in one Viterbi pass and produces a more
   metrically-coherent bar sequence. **Denominator is a convention** (4,
   or 8 when the numerator is 6/9/12), not an inference. Both are
   committed as correctable guesses; neither is presented as certain.
4. **Note events from #125's Tier 1** (`transkun` for piano; the
   multi-instrument choice is #125's open question).
5. **Quantize note onsets and durations against the beat grid from (2)**,
   not against a global tempo. Keep this repo's nearest-standard-value
   snapping; **add tuplet values to `_DURATION_CLASSES`** so a triplet can
   be written at all.
6. **Store a tempo curve** — per-beat or per-bar — in whatever multi-track
   score format #128 settles on. A single `tempo_bpm` scalar is the
   MuseScore/Finale failure mode, measured at Fme 9.9–15.3.
7. **Barlines at predicted downbeats**, never at accumulated beat counts.
8. **Drum notation is a separate, later, licence-constrained decision.**
   ADTOF is CC BY-NC-SA on both code and data and cannot be shipped; the
   clean route is training on STAR Drums (CC BY 4.0), which is real work.
   Evaluating against ADTOF privately is fine and is not distribution.

Optional-extra shape, matching this repo's existing `[batch]`/`[synth]`/
`[sf2]` convention: Beat This! and torch behind the offline extra;
madmom, if adopted at all, behind a *further* optional extra pinned to a
git commit, used only as a decoder, with its pretrained models never
downloaded.

## 6. Unverified / open

- **Nothing was measured for accuracy here.** No model was run on real
  audio. The only things executed on this machine were: `pip install
  --dry-run` resolutions on this repo's Python 3.14.7 venv
  (`beat-this` → 1.1.0 ok; `BeatNet` → 1.1.1 ok, but see §1.3; `omnizart`
  → 0.6.3 ok, but only because madmom is already present and reported
  satisfied); `import madmom` on the installed 0.16.1 (fails); and a
  from-git build of `madmom` `main` in a throwaway venv, which produced a
  cp314 wheel and correctly decoded a **synthetic** activation array.
  A dry-run resolution is not proof a package imports; a synthetic
  activation is not proof of tracking accuracy.
- **No CPU inference cost is published for Beat This!**, and none was
  measured (measuring it means installing multi-GB torch wheels). #125
  found the same gap across every model it surveyed. Someone will have to
  measure this before the speed/accuracy dial in map #123 can be
  calibrated.
- **The "transcribed drum pattern improves downbeat/meter" claim is
  unsupported by any paper found here.** What is supported is "a
  separated drum stem, as an audio input channel, improves downbeat
  tracking." The transcription-as-feature version is a prototype
  candidate, not a citation.
- **No time-signature accuracy figure exists for band audio.** PM2S
  predicts time signature and never reports its accuracy. Beat This! does
  not predict it. The survey's numbers are per-corpus and mostly not on
  commercial recordings. §2.4's expectations are reasoned, not measured,
  and are labelled so.
- **MIDI2ScoreTransformer's MV2H/MUSTER results were not retrieved** (the
  arXiv HTML for v3 404s and the README omits them), and its repo LICENSE
  file was not read — CC-BY-4.0 is from the arXiv listing.
  **MUSTER itself was not defined from a primary source**; it is referred
  to in three papers here without a definition being fetched.
- **Beat This!'s SMC 62.7 and the 8-fold table** come from the paper's
  HTML rendering (arXiv:2407.21658v1), not from a re-derivation. The
  GTZAN test-set numbers are the ones to trust most, being a held-out set.
- **Beat Transformer's ablation table** was read from the paper PDF and
  contradicts a widely-repeated secondary summary; §1.4 flags this. The
  column ordering (beat F1/CMLt/AMLt then downbeat F1/CMLt/AMLt) was
  inferred from the table layout and the paper's abstract claim of "up to
  4% point improvement in downbeat tracking" — **the column assignment is
  unverified**, though the demix-vs-no-demix *comparison within a row* is
  not affected by it.
- **Omnizart's drum module was not evaluated**, its checkpoint licence
  was not established, and the claim that it is blocked on 3.14 rests on
  its declared `madmom>=0.16.1` dependency plus §1.2's import failure, not
  on running it.
- **ADTOF-pytorch has no LICENSE file** (GitHub API returns `license:
  null`). This is recorded as "no permission granted", which is the
  correct default reading, not as a claim that the author intends
  restriction.
- **Whether madmom's git `main` DBN gives the same results as its last
  released version** was not checked, and `main` is a `0.17.dev0` with no
  release behind it. Pinning a commit is mandatory if this is adopted.
- **N2N's E-GMD comparison against ADTOF (89.68 vs 44.95)** is quoted as
  published; the domain-shift reading in §3.2 is my inference, not the
  paper's stated explanation.

## Sources

**Beat and downbeat tracking**

- Foscarin, F., Schlüter, J. & Widmer, G., "Beat this! Accurate beat
  tracking without DBN postprocessing", ISMIR 2024, arXiv:2407.21658 —
  results tables read from `arxiv.org/html/2407.21658v1`.
- `github.com/CPJKU/beat_this` — `pyproject.toml` (MIT, dependencies),
  `beat_this/inference.py` (CPU default, `dbn=False` default),
  `beat_this/model/postprocessor.py` (lazy madmom import,
  `beats_per_bar=[3, 4]`), README (MIT for code and weights), GitHub API
  (382 stars, last push 2026-05-28), all fetched directly.
- Zhao, J., Xia, G. & Wang, Y., "Beat Transformer: Demixed Beat and
  Downbeat Tracking with Dilated Self-Attention", ISMIR 2022,
  arXiv:2209.07140 — ablation table read from the PDF.
- Heydari, M. & Duan, Z., "BeatNet", ISMIR 2021, arXiv:2108.03576
  (online/offline numbers via this repo's own
  `oss-landscape-rhythm-tempo.md`, read there in full).
- Heydari, M. & Duan, Z., "BeatNet+: Real-Time Rhythm Analysis for
  Diverse Music Audio", TISMIR 2024, `transactions.ismir.net/articles/10.5334/tismir.198`
  (CC BY 4.0; code `mjhydri/BeatNet-Plus`).
- `github.com/mjhydri/BeatNet` README (CC BY 4.0; madmom prerequisite;
  Python 3.9 recommendation).
- `github.com/CPJKU/madmom` — `LICENSE` (BSD source / CC BY-NC-SA
  data-model split), `madmom/processors.py` on `main`, commit list
  (last commit 2024-08-25, "CI and NumPy compatibility updates (#540)"),
  PyPI JSON for 0.16.1 (11 releases, last 2018-11-14).

**Meter and time signature**

- Bhattarai, B. et al., "Time Signature Detection: A Survey", *Sensors*
  21(19):6494, 2021 — read via
  `pmc.ncbi.nlm.nih.gov/articles/PMC8512143/` (the MDPI copy returns 403).
- Morais, G., McFee, B. & Fuentes, M., "Skip That Beat: Augmenting Meter
  Tracking Models for Underrepresented Time Signatures", LAMIR 2024,
  arXiv:2502.12972 — Table 1, the training-data audit, the
  "denominator is always 4" statement and the BRID 0.096 figure all read
  from the PDF. Code: `github.com/giovana-morais/skip_that_beat`.
- Liu, L., Kong, Q., Morfi, V. & Benetos, E., "Performance MIDI-to-score
  conversion by neural beat tracking", ISMIR 2022 (best paper) —
  `archives.ismir.net/ismir2022/paper/000047.pdf`, Tables 2–5 and the
  time-signature vocabulary read from the PDF. Code:
  `github.com/cheriell/PM2S` (MIT).

**Drums**

- Chiu, C.-Y., Su, A. W.-Y. & Yang, Y.-H., "Drum-Aware Ensemble
  Architecture for Improved Joint Musical Beat and Downbeat Tracking",
  IEEE SPL 28:1100–1104 (2021), arXiv:2106.08685. Code:
  `github.com/SunnyCYC/drum-aware4beat`.
- Zehren, M., Alunno, M. & Bientinesi, P., "ADTOF: A large dataset of
  non-synthetic music for automatic drum transcription", ISMIR 2021,
  arXiv:2111.11737; "High-Quality and Reproducible Automatic Drum
  Transcription from Crowdsourced Data", *Signals* 4:768–787 (2023).
  `github.com/MZehren/ADTOF` README (CC BY-NC-SA 4.0 for code *and*
  data; Python 3.10; TF/Keras/madmom).
- `github.com/xavriley/ADTOF-pytorch` — GitHub API reports
  `license: null`; last push 2025-11-11.
- "STAR Drums: A Dataset for Automatic Drum Transcription", TISMIR 8(1):
  248–264, 2025, `transactions.ismir.net/articles/10.5334/tismir.244`;
  data at `doi.org/10.5281/zenodo.15690078` (CC BY 4.0).
- "Enhanced Automatic Drum Transcription via Drum Stem Source
  Separation", arXiv:2509.24853 (ADTOF + Jarredou separator + Demucs v4;
  5-class MDB 0.89 / ENST 0.85 / RBMA 0.63).
- "Noise-to-Notes: Diffusion-based Generation and Refinement for
  Automatic Drum Transcription", arXiv:2509.21739v2 (2025).
- `omnizart` PyPI JSON — 0.6.3 released 2026-05-31, MIT, deps include
  `madmom>=0.16.1`, `tf-nightly; python_version >= "3.14"`.

**Quantization**

- Wachter, M., Murgul, S. & Heizmann, M., "Transformer-Based Rhythm
  Quantization of Performance MIDI Using Beat Annotations",
  arXiv:2604.22290 (2026).
- Wachter, M., Murgul, S. & Heizmann, M., "Beat-Based Rhythm Quantization
  of MIDI Performances", arXiv:2508.19262 (2025).
- Beyer, T. & Dai, A., "End-to-end piano performance-MIDI to score
  conversion with transformers", ISMIR 2024, arXiv:2410.00210; code
  `github.com/TimFelixBeyer/MIDI2ScoreTransformer` (README: Python 3.11,
  custom `music21`/`score_transformer`/`muster` forks, MuseScore
  required).
- Cemgil, A. T., Desain, P. & Kappen, B., "Rhythm Quantization for
  Transcription" — read in full for this repo's earlier
  `oss-landscape-rhythm-tempo.md`, not re-fetched here.

**This repo, read directly**

`duration_tracker.py` (`_DURATION_CLASSES`, `duration_class_for_beats()`,
`DURATION_CLASS_ORDER`, `BEATS_BY_DURATION_CLASS`), `score_writer.py`
(32nd-note offset quantization; issue #62's tuplet deferral),
`log_import.py` (grid choice, no-tuplet rationale),
`score_editor_state.py` (single `tempo_bpm` per score), `CLAUDE.md`
(live barline placement as "explicitly approximate"), and the three
sibling research docs named at the top.
