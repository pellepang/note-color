# Pretrained weights for polyphonic music transcription: what we can take off the shelf

Research for issue
[#125](https://github.com/pellepang/note-color/issues/125), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"). The question the map needs answered before
anything else is chosen:

> Is there an accurate open-source weight set we can simply take, so this
> project never has to train anything — and if the honest answer for some
> part of the problem is no, which part, and why?

This is **not** a re-derivation of
`docs/research/oss-landscape-transcription-and-prior-art.md` or
`docs/research/detection-systems-survey.md`. Both were read in full first;
they establish the shape of the field (basic-pitch is tiny and
instrument-agnostic; ByteDance is piano-only and excellent; MT3 is batch;
`onnxruntime` ships aarch64 wheels; neural AMT is offline-only for this
project). Two things have changed since they were written and one thing
was never asked:

1. **The field moved.** MT3 is no longer the frontier — it is the
   *baseline* that most 2025 challenge entrants failed to beat. YourMT3+
   (2024) and MuScriptor (ISMIR 2026) are the current open multi-instrument
   models, and the 2025 AMT Challenge gives us the first head-to-head
   numbers across them on one held-out corpus.
2. **The constraint changed.** The earlier docs judge everything against
   Pi-class portability and a <150ms live budget. Map #123 explicitly
   removes both for this path: 4-core Kaby Lake-U, no GPU, 15 GB RAM,
   Python 3.14 with `torch` 2.14 / `onnxruntime` 1.29, offline, up to
   ~1 hour per song. Several models that were correctly rejected for the
   live path are viable here.
3. **Licensing of the *weights*, separately from the code, was never
   checked.** It turns out to be the single most decision-relevant axis,
   and it splits the candidate set cleanly.

Everything below is sourced; anything I could not verify from a primary
source is marked **unverified** in place rather than smoothed over.

## Questions

1. What open pretrained weight sets exist for polyphonic / multi-instrument
   transcription, and what does each actually score, on which corpus?
2. What are the licences of the **code** and of the **weights**,
   separately? Are weights redistributable, or downloaded at first use?
3. What does each cost to run on a 4-core CPU with no GPU, and does each
   run at all on Python 3.14 / torch 2.14 / onnxruntime 1.29?
4. Single-instrument or genuinely multi-instrument?
5. Is there a weight set that covers the *whole* job map #123 describes —
   audio to an editable multi-track **score**, not just note events?

## 1. What the field looks like as of September 2026

### The 2025 AMT Challenge is the one apples-to-apples number we have

The challenge ("Advancing Multi-Instrument Music Transcription: Results
from the 2025 AMT Challenge", arXiv 2603.27528) evaluated 8 valid
submissions plus an MT3 baseline on **a new evaluation set of 76 pieces
written by five professional composers**, covering eight instruments
(piano, violin, viola, cello, flute, bassoon, trombone, oboe), at most
three instruments per piece. The metric is **multi-instrument note onset
F1** — program, pitch and onset (±50 ms) must all match.

| Rank | System | F1 | Precision | Recall | Overlap |
|---|---|---|---|---|---|
| 1 | MIROS (YourMT3+ framework + MusicFM encoder) | **0.5998** | 0.6558 | 0.5724 | 0.7391 |
| 2 | YourMT3-YPTF-MoE-M | 0.5938 | 0.6010 | 0.5888 | 0.7305 |
| 3 | YourMT3-YPTF-S | 0.5581 | 0.5565 | 0.5615 | 0.7326 |
| 5 | **MT3 (baseline)** | **0.3932** | 0.3811 | 0.4115 | 0.7180 |

Two findings matter more than the ranking:

- **Only two of eight submissions beat MT3.** MT3 remains a strong
  baseline; most attempts to improve on it do not.
- **The audio was "rendered directly from the MIDI scores using FluidSynth
  with the FluidR3 GM soundfont."** This is synthesised audio, not
  recorded band mixes. The best multi-instrument system in the world
  scores ~0.60 note-onset F1 on *clean synthetic* three-instrument audio.
  That is the ceiling to calibrate expectations against, and real
  recordings will be worse — how much worse is not established by this
  paper, and I found no primary source that establishes it. **This is the
  most important number in this document.**

Contrast that with piano-only, where the same metric is in the mid-90s
(below). The gap is not a tooling gap; it is where the science is.

### MuScriptor (ISMIR 2026) is the newest open-weight multi-instrument model

Released 9 July 2026 by Rouard, Krause, Roebel, Simon-Gabriel and Défossez
(Kyutai / Mirelo), arXiv 2607.08168. Decoder-only transformer over
mel-spectrograms (512 bins, 100 Hz frames), autoregressively predicting
MIDI-like tokens, with optional conditioning on instrument presence.
Trained on 1.45M synthetic MIDI files (250+ soundfonts), then 170,000 real
recordings (11,000+ hours) with aligned annotations, then RL-refined on 300
curated tracks.

On its own 372-track test set (1.3B model):

| Configuration | Onset F1 | Frame F1 | Offset F1 | Drums F1 | Multi F1 |
|---|---|---|---|---|---|
| Synthetic only | 34.5 | 48.9 | 16.1 | 21.0 | 16.2 |
| + Real | 54.4 | 69.3 | 42.3 | 43.3 | 41.6 |
| + Real + RL | **60.4** | **72.4** | **48.6** | **49.6** | **47.8** |
| YourMT3+ (their re-run) | 32.52 | 45.54 | 17.79 | 41.4 | 21.9 |

Caveats, stated plainly: this is the **authors' own test set**, not a
shared benchmark, and the YourMT3+ row is a **second-party re-run** of a
competitor. It is not directly comparable to the AMT Challenge table
above. What is credible from it is the *ordering of the ablations* — real
data is worth ~20 F1 points over synthetic-only, and RL post-training
another ~6 — which is the paper's actual contribution.

Sizes: small 103M / medium (default) 307M / large 1.4B parameters. The
README says "**small is the practical choice on CPU-only machines**",
medium is the speed/accuracy default, and "large ... really wants a GPU".
No CPU timing figure is published — **unverified**.

### Piano alone is a solved-enough problem

| System | MAESTRO note onset F1 | Note onset+offset F1 |
|---|---|---|
| Transkun v2 (aug), ISMIR 2024 | **0.9505** | **0.9314** |
| YourMT3+ (YPTF.MoE+Multi) | 0.9698 (onset) | — |
| ByteDance high-resolution (Kong 2020) | 0.9672 | 0.8247 |
| Onsets & Frames (2018) | 0.9480 | — |
| basic-pitch (NMP) | 0.709 | — |

The ByteDance/Onsets & Frames figures are carried over from
`oss-landscape-transcription-and-prior-art.md` (already primary-sourced
there). The Transkun numbers are from its own README; the YourMT3+ figure
is from arXiv 2407.04822.

Note the size of the piano-vs-ensemble gap: ~0.95 F1 on solo piano,
~0.60 on three synthetic instruments. **Any accuracy claim for map #123
that quotes a piano number is quoting the wrong number.**

## 2. Comparison table

Legend: "bundled" = weights ship inside the installable artifact;
"download" = fetched at first use. Python column is what the project
*declares*, not what has been tested here — nothing in this table was
installed or run during this research (see Unverified).

| System | Scope | Best published accuracy (corpus) | Code licence | **Weights licence** | Weight delivery | Size | Runtime / Python | CPU cost |
|---|---|---|---|---|---|---|---|---|
| **Transkun v2** | Piano only | 0.9505 onset F1, 0.9314 onset+offset (MAESTRO) | MIT | MIT (in the MIT wheel) | **bundled** (50.8 MB wheel) | ~50 MB | torch, `requires_python >=3.6`, no upper pin | `--device` **defaults to `cpu`**; no published RTF |
| **YourMT3+** (YPTF.MoE+Multi) | Multi-instrument + drums | 0.5938 F1 (AMT Challenge 2025); 84.56 onset F1 Slakh; 96.98 MAESTRO; 91.65 GuitarSet | **GPL-3.0** (repo LICENSE) | **Apache-2.0** (HF `mimbres/YourMT3`) | download (HF) | 45.8M params | PyTorch; py3.9+/torch2.4+ via `mt3-infer` | 36× real-time on a **T4 GPU** fp16; CPU **unbenchmarked** |
| **MuScriptor** small / medium / large | Multi-instrument + drums | 60.4 onset F1 / 47.8 multi F1 (own 372-track test set, 1.3B) | MIT | **CC BY-NC 4.0** (non-commercial, gated) | download (HF, token + licence acceptance) | 103M / 307M / 1.4B | `requires_python >=3.10`, `torch>=2.0` | "small is the practical choice on CPU-only machines"; **no number published** |
| **MT3** (magenta) | Multi-instrument | 0.3932 F1 (AMT Challenge 2025) | Apache-2.0 | not stated on the repo | download (Colab/GCS) | ~ T5-small class | JAX/T5X; excluded by `mt3-infer` for dependency conflicts | 12× real-time on RTX 4090 (PyTorch port) |
| **MR-MT3** | Multi-instrument | improved onset F1 over MT3 on Slakh2100 (paper) | MIT (per `mt3-infer`) | re-hosted at HF `gudgud1014/MR-MT3` | download | MT3-class | PyTorch | 57× real-time on RTX 4090 |
| **basic-pitch** | Instrument-agnostic polyphonic notes | 0.709 note F1 (MAESTRO); 72.5±3.8 frame acc (Bach10) | Apache-2.0 | **Apache-2.0** | **bundled** — `nmp.onnx` 230 KB, `nmp.tflite` 204 KB | **16,782 params** | pip pkg declares **py3.8–3.11 only**; TF/TFLite/CoreML/ONNX | 24 s + 951 MB peak for a 7:45 file (paper's efficiency table) |
| **ByteDance piano_transcription** | Piano only | 0.9672 onset F1, 0.8247 onset+offset (MAESTRO) | Apache-2.0 | not stated separately | download (`piano_transcription_inference`) | ~ 160 MB class | "Python 3.7 and PyTorch 1.4.0" | not published |
| **hFT-Transformer** (Sony) | Piano only | SOTA at ISMIR 2023 on MAPS/MAESTRO | MIT | **not stated** (GitHub release asset) | download (GH releases) | not disclosed | trained on A100; dev on py3.6.9 | not published |
| **Omnizart** | Multi-task: pitched, drum, vocal, chord, beat | none published in repo | MIT | via `omnizart download-checkpoints` | download | not disclosed | `requires_python >=3.8`; **`tf-nightly` for py>=3.14**; 0.6.3 released 2026-05-31 | not published; drum model "has unknown bugs" |
| **ADTOF** | Drums only (5 classes) | matches SOTA ADT per its ISMIR 2021 paper | **CC BY-NC-SA 4.0** | same (NC-SA) | notebook-driven | not disclosed | Python 3.10, TF/Keras (PyTorch alt ≈ −0.2% F) | not published |
| **PM2S** | **MIDI → score** (beats, downbeats, key sig, time sig, hand part, quantised times) | "significantly better than commercial software" on MV2H (ISMIR 2022) | MIT | in-repo / demo notebook | with repo | small CRNN | "Python 3.8 and PyTorch 1.12.0" | not published |
| Transformer rhythm quantization (arXiv 2604.22290) | **MIDI + beats → score** | 97.3% onset F1, 83.3% note-value accuracy (ASAP) | **no release found** | — | — | 2-layer T5, 128-dim, 187 tokens | — | linear in measures |

## 3. Answers

### 3.1 Licensing is the axis that actually decides this

Three distinct situations, and they are not interchangeable:

**Cleanly permissive, code and weights.** `transkun` (MIT, weights inside
an MIT wheel), `basic-pitch` (Apache-2.0 both; the ONNX graph is a 230 KB
file in the Apache-2.0 repo), `mt3-infer` (MIT), `PM2S` (MIT),
`hFT-Transformer` code (MIT — but its checkpoint carries **no stated
licence**, which is not the same as permissive).

**Non-commercial weights.** MuScriptor's code is MIT but its weights are
**CC BY-NC 4.0**, gated behind HF licence acceptance, with a model-card
term that the user must hold the rights to whatever they transcribe.
ADTOF is **CC BY-NC-SA 4.0** throughout — the ShareAlike term is the
sharper edge of the two. For a hobby project that is never sold, NC is
survivable; for anything this project might later want to distribute as a
product, it is a trap, and NC-SA additionally reaches into derived work.

**A genuine discrepancy that needs resolving before use.** YourMT3's
GitHub repo `LICENSE` is **GPL-3.0**, its Hugging Face weights repo
declares **apache-2.0**, and the third-party `mt3-infer` toolkit lists its
vendored YourMT3 code as "Apache-2.0". At most one of those is right about
the code. The defensible reading is: **weights Apache-2.0, code GPL-3.0**
— which means calling YourMT3's *inference code* from note-color would
make the combined work GPL-3.0 on distribution, while the *weights* alone
are free to use. **Unverified**: I did not find a maintainer statement
reconciling this. Anyone acting on it should open an issue upstream first.

A related in-repo fact worth stating because it changes how much any of
this matters: **`note-color` currently has no `LICENSE` file at all** and
`pyproject.toml` declares none. The repo is public but not
open-source-licensed, i.e. all rights reserved by default. GPL obligations
attach to *distribution*, so nothing here is an immediate problem — but
"we'll pick a licence later" and "we depend on GPL inference code" are
decisions that constrain each other, and the second silently makes the
first.

### 3.2 Python 3.14 / torch 2.14 / onnxruntime 1.29 compatibility

- **Fine, in all likelihood:** `transkun` (unpinned `torch`,
  `requires_python >=3.6`), `MuScriptor` (`>=3.10`, `torch>=2.0`),
  `mt3-infer` (`py3.9+`, `torch2.4+`), YourMT3 weights loaded through any
  of those.
- **Declared-stale but probably fine:** `PM2S` ("Python 3.8 and PyTorch
  1.12.0 ... other versions can work, but they are not tested"),
  ByteDance ("Python 3.7 and PyTorch 1.4.0"). These are old *statements*,
  not necessarily old *code*; the model definitions are plain PyTorch
  modules. Risk is real but low, and cheap to check.
- **A real problem:** **`basic-pitch` declares Python 3.8–3.11 only**
  (PyPI classifiers on 0.4.0, released 2024-08-16 — no release since), and
  its platform-conditional dependencies route Linux to `tflite-runtime`
  only for `python_version < "3.11"`. The package will not install cleanly
  on 3.14. **The weights are not the problem** — `nmp.onnx` is 230 KB of
  Apache-2.0 data, and `onnxruntime` 1.29 will load it. If basic-pitch is
  ever wanted here, the route is to vendor the ONNX file and reimplement
  its (also Apache-2.0, also small) pre/post-processing, not to
  `pip install basic-pitch`. **Unverified**: I did not confirm the ONNX
  graph's input signature (raw audio vs. precomputed CQT), which decides
  how much preprocessing has to be reimplemented.
- **Awkward:** Omnizart 0.6.3 (2026-05-31) resolves to **`tf-nightly` on
  Python >= 3.14**. Depending on a nightly TensorFlow build for an offline
  batch feature is a maintenance liability, and Omnizart's own README
  concedes its drum model "has unknown bugs".
- **Excluded:** magenta/MT3 proper (JAX/T5X) — `mt3-infer` drops it
  outright "due to dependency conflicts", and it is the weakest system in
  the AMT Challenge table anyway.

### 3.3 CPU inference cost: nobody publishes it, and I could not measure it

This is the largest evidence gap in this document, and I want it visible
rather than papered over.

- Every published throughput figure I found is **GPU**: YourMT3+ at 36×
  real-time on a T4 (fp16); `mt3-infer` at 57× (MR-MT3), 15× (YourMT3),
  12× (MT3-PyTorch) on an RTX 4090. `mt3-infer` states outright: "CPU
  inference supported but **not benchmarked**."
- MuScriptor says "small is the practical choice on CPU-only machines" and
  reports Apple Silicon MPS running "several times faster than real time",
  but publishes **no x86 CPU number**.
- basic-pitch's paper reports 24 s and 951 MB peak for a 7:45 file, but my
  extraction did not establish whether that was CPU or GPU. Treat as
  order-of-magnitude only.
- **Nothing was measured here.** The project venv currently has
  `numpy 2.5.2`, `librosa 1.0.0`, `music21 10.5.0` and **no `torch` or
  `onnxruntime` installed**, so no local benchmark was possible within
  this research pass.

What can be said without measuring: the map's budget is **~1 hour per
song**. For a 4-minute track that is a **~15× real-time budget** —
i.e. the machine may run 15× slower than the T4 figure and still fit.
A 45.8M-parameter encoder-decoder (YourMT3+) or a 103M decoder-only
(MuScriptor-small) at that budget is *plausible* on 4 Kaby Lake cores.
Plausible is not measured. **Ticket #129 (which #125 blocks) should
start by measuring exactly this**, because it is the one number that
decides between the small and medium MuScriptor tiers and between
YourMT3+ and everything else.

### 3.4 Single- vs. multi-instrument

Genuinely multi-instrument, with per-note instrument assignment (the thing
map #123's multi-track output needs): **MT3, MR-MT3, YourMT3+,
MuScriptor**. YourMT3+ and MuScriptor both transcribe drums as a distinct
class; MuScriptor reports drums F1 49.6 on its own set, YourMT3+ claims
SOTA on ENST-Drums.

Instrument-*agnostic* but not instrument-*aware*: **basic-pitch** — one
undifferentiated stream of notes regardless of source. For a full band mix
that is a pile of notes, not tracks. It becomes useful only *after*
source separation has already split the mix (map #123's separation ticket),
where it is a small, permissive, per-stem note detector.

Single-instrument: Transkun, ByteDance, hFT-Transformer (piano); ADTOF
(drums).

### 3.5 The part where the honest answer is "no"

**Yes, for note events, on piano:** take `transkun` — MIT code, MIT
weights bundled in the wheel, CPU by default, 0.9505 onset F1. There is no
argument for training a piano model.

**Qualified yes, for multi-instrument note events:** YourMT3+'s weights
(Apache-2.0) or MuScriptor's (CC BY-NC 4.0) are both takeable, and both
comfortably beat anything this project could train. The qualification is
licence (GPL code / NC weights) and unmeasured CPU cost, not accuracy.

**No, for accuracy on a real band mix.** The best open multi-instrument
system scores ~0.60 note-onset F1 on *synthetic* three-instrument audio.
Nobody has published a comparable number on real recorded band mixes, and
the direction of the error is not in doubt. This is a limit of the field
in September 2026, not a shopping problem — training our own would be
worse, by a wide margin, so "never train anything" remains the right call
even though the ceiling is lower than one might hope. It also means map
#123's evaluation-before-algorithm stance is load-bearing: the number has
to be measured on our own corpus, because no published number answers it.

**No, for the score layer.** Every model in the table above outputs
**note events** — pitch, onset, offset, sometimes velocity and instrument.
None of them outputs barlines, a time signature, a key signature, beamed
note values, or pitch spelling. That conversion (performance MIDI →
score) is a separate research area with a much thinner open ecosystem:

- **PM2S** (MIT, ISMIR 2022) is the one usable open toolkit — CRNN beat/
  downbeat tracking that also predicts key signature, time signature,
  quantised times and hand part. It is trained on **classical piano**
  (ASAP-adjacent), which is a poor match for a band mix, and its stated
  environment is Python 3.8 / torch 1.12.
- The 2026 transformer rhythm-quantization work (arXiv 2604.22290) is
  clearly better — 97.3% onset F1, 83.3% note-value accuracy on ASAP,
  beating end-to-end PM2S on MUSTER ε_onset (12.30 vs 15.55) — but I found
  **no code or weight release**, so it is a design reference, not a
  dependency.

So the honest split is: **notes are a solved shopping problem; the
score is not.** Map #123's drums-as-timing-oracle decision is exactly the
right instinct for this gap, because the beat grid is the part nobody will
hand us pretrained for a band mix.

## 4. Shortlist

**Tier 1 — take these, they are the spine of the pipeline.**

1. **YourMT3+ weights** (Apache-2.0, HF `mimbres/YourMT3`), driven through
   **`mt3-infer`** (MIT, py3.9+/torch2.4+) rather than the GPL-3.0
   upstream repo. This is the best licence/accuracy combination available:
   the #2 system in the 2025 AMT Challenge (0.5938 F1, 0.0060 behind the
   winner), 45.8M parameters, genuinely multi-instrument, drums included.
   *Action before adopting:* resolve the GPL-vs-Apache code discrepancy
   with the upstream maintainer, and confirm `mt3-infer`'s vendored copy is
   what it says it is.
2. **Transkun v2** (MIT, weights bundled, CPU by default) for the piano
   stem. There is no reason to run a general model on a piano stem when a
   0.95-F1 MIT-licensed one installs with `pip` and defaults to CPU.

**Tier 2 — evaluate against Tier 1 in ticket #129, adopt if the licence is
acceptable.**

3. **MuScriptor-small (103M) / -medium (307M)** — newest, explicitly
   CPU-conscious, and the only model in the table trained on 11,000+ hours
   of *real* audio with RL post-training. Blocked only by **CC BY-NC 4.0**
   weights. If this project is and stays non-commercial, this is likely the
   accuracy leader; if it might ever be sold, it cannot be the default.

**Tier 3 — situational.**

4. **basic-pitch's `nmp.onnx`** (230 KB, Apache-2.0, vendored and run
   directly under `onnxruntime` 1.29 — *not* `pip install basic-pitch`,
   which does not support Python 3.14) as a cheap per-stem note detector
   after separation, and as the "no heavy extra installed" fallback tier
   above today's DSP path.
5. **PM2S** (MIT) as the starting point for MIDI → score: beats,
   downbeats, time signature, key signature. Expect to have to retrain or
   heavily post-process it for non-piano material; treat arXiv 2604.22290
   as the design target it should be measured against.

**Explicitly not recommended.**

- **magenta/MT3** — superseded (0.3932 F1), JAX/T5X dependency conflicts,
  weights licence unstated.
- **Omnizart** — broad task coverage is tempting, but it resolves to
  `tf-nightly` on Python 3.14, publishes no accuracy numbers of its own,
  and admits its drum model is buggy. Its breadth is better served by
  YourMT3+/MuScriptor plus a dedicated beat tracker.
- **ADTOF** for drums — technically strong, but **CC BY-NC-SA 4.0**, and
  YourMT3+/MuScriptor already transcribe drums as a class. Revisit only if
  drum-class accuracy is measured as the binding constraint.
- **hFT-Transformer** — MIT code, but the checkpoint has **no stated
  licence**, and Transkun beats it on the metric that matters with clean
  MIT weights. No reason to take the ambiguity.

## 5. What this means for map #123

- The map's standing decision that "a pretrained neural model on the
  offline path is acceptable and expected" is well founded: the pretrained
  options beat anything trainable here by a very large margin, and two of
  them are permissively licensed.
- The map's **evaluation-before-algorithm** decision is doing real work.
  No published number tells us how any of these performs on a real band
  mix; the AMT Challenge's 0.60 F1 on *synthetic* three-instrument audio
  is the closest thing to a ceiling, and it needs re-measuring on our own
  corpus before any accuracy claim is made in this project's voice.
- The map's **drums-as-timing-oracle** decision lands in exactly the gap
  the pretrained ecosystem leaves open — the score layer (beats,
  downbeats, meter, quantisation) is where nothing shippable exists for
  band-mix input.
- A **licence decision for this repo** is now on the critical path, not a
  tidy-up task. It is currently unlicensed, and the choice between
  YourMT3+ (GPL code / Apache weights) and MuScriptor (MIT code / NC
  weights) is partly a choice about what this project wants to be.

## 6. Unverified / open

Stated explicitly so nobody cites this document for something it did not
establish:

- **No model in this document was installed or run.** No CPU benchmark,
  no accuracy reproduction, no dependency-resolution check against Python
  3.14 was performed. Every compatibility claim is read off declared
  metadata (`requires_python`, `requires_dist`, README statements).
- **The YourMT3 code licence discrepancy is unresolved** (GPL-3.0 in the
  repo LICENSE vs. Apache-2.0 claimed by `mt3-infer` for the same vendored
  code). Weights being Apache-2.0 on Hugging Face is separately sourced
  and is the firmer of the two facts.
- **MuScriptor's accuracy numbers are on the authors' own 372-track test
  set**, not a shared benchmark, and its YourMT3+ comparison row is a
  second-party re-run. The AMT Challenge table and the MuScriptor table
  are **not** comparable to each other.
- **hFT-Transformer's and ByteDance's checkpoint licences were not
  found** — absence of a statement is not permission.
- **basic-pitch's ONNX input signature was not inspected**, so the effort
  of running it standalone under `onnxruntime` is estimated, not measured.
- **No real-band-mix accuracy figure exists** for any system here, from
  any source I could find. Every number above is on synthetic renders,
  studio piano, or curated stems.
- The 2025 AMT Challenge paper's ranking table was extracted from the
  arXiv HTML rendering; rank 4 was not captured in that extraction (ranks
  1, 2, 3 and 5/baseline are reported above as retrieved).

## Sources

Primary sources, fetched September 2026:

- 2025 AMT Challenge results: arXiv 2603.27528
  (`arxiv.org/html/2603.27528v1`); challenge page
  `ai4musicians.org/transcription/2025transcription.html`.
- MuScriptor: arXiv 2607.08168 (`arxiv.org/html/2607.08168v1`);
  `github.com/muscriptor/muscriptor` README; `huggingface.co/muscriptor`
  and the `MuScriptor/muscriptor-small` model card; PyPI JSON metadata for
  `muscriptor` 0.3.0 (2026-08-05).
- YourMT3 / YourMT3+: `github.com/mimbres/YourMT3` (repo LICENSE =
  GPL-3.0); `huggingface.co/mimbres/YourMT3` (weights = apache-2.0);
  arXiv 2407.04822 (`arxiv.org/html/2407.04822v1`) for parameter counts
  and per-dataset F1.
- `mt3-infer`: `github.com/openmirlab/mt3-infer` (MIT; checkpoint
  provenance table; RTX 4090 throughput; "CPU inference supported but not
  benchmarked").
- MT3: `github.com/magenta/mt3` (Apache-2.0). MR-MT3: arXiv 2403.10024;
  weights `huggingface.co/gudgud1014/MR-MT3`.
- basic-pitch: `github.com/spotify/basic-pitch` README (Apache-2.0) and
  GitHub contents API for `basic_pitch/saved_models/icassp_2022`
  (`nmp.onnx` 230,444 B; `nmp.tflite` 204,448 B); PyPI JSON for
  `basic-pitch` 0.4.0 (2024-08-16, classifiers py3.8–3.11);
  `huggingface.co/spotify/basic-pitch` (weights Apache-2.0); paper
  arXiv 2203.09893 via `ar5iv.labs.arxiv.org/html/2203.09893` (16,782
  params; MAESTRO Fno 70.9 vs Onsets & Frames 95.2; Bach10 72.5±3.8 vs
  Deep Salience 55.7±2.9; Su 37.7±15.4 vs 43.6±7.9; 24 s / 951 MB on a
  7:45 file).
- Transkun: `github.com/Yujia-Yan/Transkun` (MIT; MAESTRO 0.9505 /
  0.9314; `--device` default `cpu`); PyPI JSON for `transkun` 2.0.1
  (2024-09-28, 50.76 MB wheel).
- ByteDance: `github.com/bytedance/piano_transcription` (Apache-2.0;
  "Python 3.7 and PyTorch 1.4.0"); MAESTRO F1 figures carried from
  `oss-landscape-transcription-and-prior-art.md` (arXiv 2010.01815).
- hFT-Transformer: `github.com/sony/hFT-Transformer` (MIT; checkpoint
  `model_016_003.pkl` via GitHub releases, licence unstated).
- Omnizart: `github.com/Music-and-Culture-Technology-Lab/omnizart` (MIT;
  task table; drum-model bug note); PyPI JSON for `omnizart` 0.6.3
  (2026-05-31; `requires_python >=3.8`; `tf-nightly` for py>=3.14).
- ADTOF: `github.com/MZehren/ADTOF` (CC BY-NC-SA 4.0; Python 3.10;
  PyTorch alternative ≈ −0.2% F-measure); arXiv 2111.11737;
  arXiv 2509.24853 (drum stem separation + ADT, 2025).
- PM2S: `github.com/cheriell/PM2S` (MIT; "Python 3.8 and PyTorch
  1.12.0"); ISMIR 2022 paper `archives.ismir.net/ismir2022/paper/000047.pdf`.
- Rhythm quantization: arXiv 2604.22290
  (`arxiv.org/html/2604.22290v1`) — 97.3% onset F1, 83.3% note-value
  accuracy on ASAP; MUSTER ε_onset 12.30 vs end-to-end PM2S 15.55; no
  code release located.

In-repo cross-references: `docs/research/oss-landscape-transcription-and-prior-art.md`,
`docs/research/detection-systems-survey.md`, map
[#123](https://github.com/pellepang/note-color/issues/123), ticket
[#125](https://github.com/pellepang/note-color/issues/125).
