# CPU-only source separation for band-mix stem splitting

Research for issue
[#126](https://github.com/pellepang/note-color/issues/126), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"). The map's standing decision is that a **full
band mix is the target input**, which puts source separation on the
critical path before anything else runs. This document answers which
separator, at what cost, under what licence — and whether separation is
worth doing at all.

Three sibling documents were read first and are not re-derived here:

- [`docs/research/pretrained-transcription-weights.md`](pretrained-transcription-weights.md)
  (#125) — open multi-instrument transcription tops out near **0.60 note
  onset F1** on synthetic ≤3-instrument audio; **code and weight licences
  diverge per project**, and this repo has no LICENSE file.
- [`docs/research/eval-harnesses-and-corpora.md`](eval-harnesses-and-corpora.md)
  (#124) — on **real commercial band audio the best open model reaches
  37.87% onset F1**. That is the number separation has to move.
- [`docs/research/rhythm-meter-and-drum-transcription.md`](rhythm-meter-and-drum-transcription.md)
  (#127) — a separated **drum stem used as an extra beat-tracker channel
  lifts downbeat F1 from 0.699 to 0.775** on drum-heavy material. That is
  the one measured downstream win separation already has in this map.

Everything here is sourced. Claims I could not verify from a primary
source are marked **unverified** in place. Things actually executed on
this machine are marked **measured here**, following #124's convention.

## Questions

1. Which separator, given CPU-only hardware and Python 3.14?
2. What does it actually cost per minute of audio on *this* machine —
   the load-bearing number the issue asks for?
3. What licence covers the **weights**, separately from the code?
4. Does separation measurably help or hurt downstream transcription?
5. How many stems are worth having?
6. What sits at the fast end of the speed-vs-accuracy dial?

## Summary of the answer, before the evidence

- **Demucs is the recommendation, and it is not the dead project it looks
  like.** `facebookresearch/demucs` is archived (last push 2024-04-24),
  but the author's fork `adefossez/demucs` is alive (last push
  2026-08-31) and is what PyPI `demucs` **4.1.0, released 2026-07-11**,
  points at. It **installs cleanly on Python 3.14.7 with CPU-only torch
  2.14 — measured here** — which is more than #124 could say for madmom
  or #125 for `basic-pitch`.
- **The cost is affordable and the published figure understates it.**
  Demucs' README says CPU time is "roughly equal to 1.5 times the
  duration of the track". **Measured here on the target 4-core
  Kaby Lake-U: `htdemucs` runs at 2.02–2.14× real time wall-clock** across
  two warm runs on a 45 s clip, i.e. **≈2.1 minutes per minute of audio**
  (≈3.6 CPU-seconds per second of audio). A 4-minute song is **≈8.3
  minutes** of the map's ~1-hour budget. **The best model, `htdemucs_ft`,
  is 7.80×** — a 4-minute song costs **≈31 minutes**, half the budget, for
  a stated SDR gain of "a bit better". And the *quantized* `mdx_extra_q` is
  **6.10× and peaks at 4.2 GB RSS** — quantization there buys download
  size, not speed.
- **The weights are not MIT and never were.** The code is MIT. The
  pretrained weights are covered by a maintainer statement — *"The model
  weights are not covered by the MIT license, and are provided only for
  scientific purposes"* — and the Hugging Face repos `demucs` 4.1.0
  actually downloads from **declare no licence tag at all**. Same
  MUSDB18-inherited restriction reaches Open-Unmix (`umxl` is explicitly
  CC BY-NC-SA 4.0). This is #125's licence-divergence finding recurring,
  and it lands on the same unresolved fact: **this repo has no LICENSE
  file**.
- **Nobody has published the comparison this map most needs.** I found
  **no study measuring an off-the-shelf separator feeding an off-the-shelf
  transcriber, with and without the separation step, on a shared metric.**
  What exists is one primary source that *bypasses* separation on
  solo-piano input specifically "to avoid introducing spectral artifacts",
  without quantifying the harm. So separation must justify itself against
  #124's 37.87% baseline **in this repo's own harness**, not by citation.
- **Four stems, not six.** The 6-stem model's own authors say its piano
  stem shows "a lot of bleeding and artifacts". It is **measured here to
  be no more expensive than the 4-stem model**, so it is worth *offering* —
  but not worth *depending* on.
- **Spleeter is dead on this machine**, by its own metadata: PyPI pins
  `requires_python <3.12` and `tensorflow==2.12.1`.

---

## 1. What is actually current

`facebookresearch/demucs` is **archived**, last pushed 2024-04-24
([GitHub API, read here](https://github.com/facebookresearch/demucs)).
It is the repo every secondary write-up still links to, which makes the
project look abandoned.

It is not. `adefossez/demucs` — the original author's own fork — is not
archived and was last pushed **2026-08-31**, a week before this document
([GitHub API, read here](https://github.com/adefossez/demucs)). PyPI
`demucs` **4.1.0** was uploaded **2026-07-11** (the previous release,
4.0.1, was 2023-09-07) and its `Homepage` metadata points at
`github.com/adefossez/demucs`, not the archived Meta repo
([PyPI JSON API, read here](https://pypi.org/pypi/demucs/json); wheel
`METADATA`, read here from the installed package). `Requires-Python:
>=3.10`, with no upper bound — which is why it installs on 3.14 at all.

**Measured here**, on a throwaway venv (never the project's own):

```
python3.14 -m venv venv
venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
  -> torch 2.14.0+cpu, torchaudio 2.11.0+cpu          (cp314 wheels exist)
venv/bin/pip install demucs
  -> demucs 4.1.0, pure-Python wheel, all deps resolve (cp314 wheels exist
     for sphn, lameenc, safetensors, pyyaml, hf-xet)
venv/bin/pip install diffq        # only needed for the quantized *_q models
  -> diffq 0.2.4
```

No build failures, no pins fought. This is the cleanest install of any
neural component this map has examined so far.

The other candidates the issue names:

- **Spleeter** — PyPI 2.4.2 declares `Requires-Python: <3.12,>=3.8` and
  pins `tensorflow==2.12.1` exactly
  ([PyPI JSON API, read here](https://pypi.org/pypi/spleeter/json)).
  Not installable here, and not close. Ruled out on metadata alone; no
  attempt made.
- **Open-Unmix** — `openunmix` 1.3.0, `Requires-Python: >=3.9`,
  PyTorch-native. **Installs here** (measured). Architecturally a
  three-layer BiLSTM on magnitude spectrograms — much smaller than
  Demucs.
- **BS-RoFormer / Mel-Band RoFormer / SCNet** — the current SDR
  frontier. BS-RoFormer reports **9.80 dB average SDR on MUSDB18-HQ**
  without extra training data ([arXiv:2309.02612](https://arxiv.org/abs/2309.02612)),
  and Mel-Band RoFormer improves on it further
  ([arXiv:2310.01809](https://arxiv.org/abs/2310.01809)). Distribution
  is mostly through community checkpoint hubs
  ([ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training),
  MIT code) rather than a first-party release. **No published CPU cost
  figure exists for any of them** — and the architectural direction
  (attention over band splits, versus Demucs' convolutional/hybrid
  backbone) points the wrong way for a 4-core laptop. Not measured here;
  flagged as the obvious re-check if this decision is ever revisited on
  better hardware.
- **Bandit / Banquet** — cinematic/dialogue-oriented stem separation, a
  different problem shape from a band mix. Not pursued. **Unverified.**

## 2. The load-bearing number: measured CPU cost

**Machine** (`lscpu`, read here): Intel Core i5-7300U @ 2.60 GHz,
**2 physical cores / 4 threads**, 15 GB RAM, no GPU, Linux. Note that
`torch.get_num_threads()` returns **2**, not 4 — torch counts physical
cores, so the default run uses half the logical CPUs.

**Input**: a synthesised 45-second 44.1 kHz stereo mix (bass + triad pad
+ high-passed noise "hi-hat" + a wobbling lead), built with ffmpeg.
Separation cost is content-independent — it is a fixed convolution/
attention stack over fixed-length segments — so a synthetic clip is a
valid stand-in **for timing**. It is *not* a valid stand-in for quality,
and no quality claim is made from it here.

All runs: `python -m demucs.separate -n <model> -d cpu -o out clip45.wav`,
defaults otherwise (`--overlap 0.25`, `--shifts 0`), weights already
downloaded, wall-clock and child-process rusage captured by a wrapper.

| Model | Stems | Wall (s) | CPU (s) | RTF (wall/audio) | min per min of audio | Peak RSS |
|---|---|---|---|---|---|---|
| `htdemucs` (default) | 4 | 96.4 | 162.4 | 2.14× | 2.1 | 1334 MB |
| `htdemucs` (repeat) | 4 | 90.9 | 157.6 | 2.02× | 2.0 | 1288 MB |
| `htdemucs_6s` | 6 | 77.4 | 132.3 | **1.72×** | **1.7** | 1435 MB |
| `hdemucs_mmi` (v3) | 4 | 81.6 | 114.9 | 1.81× | 1.8 | 2865 MB |
| `mdx_extra_q` (bag of 4, quantized) | 4 | 274.4 | 400.0 | **6.10×** | **6.1** | **4188 MB** |
| `htdemucs_ft` (bag of 4, fine-tuned) | 4 | 350.9 | 616.7 | **7.80×** | **7.8** | 1861 MB |

**All measured here.** A first, cold `htdemucs` run including the weight
download took 94.2 s wall / 166.5 s CPU, which sits within the spread of
the two warm runs — the weights are small enough that the download barely
registers (see §7).

Reading the table:

- **CPU-seconds are ~1.7× wall-clock** for `htdemucs`, i.e. the workload
  keeps roughly 1.7 of the 2 physical cores busy. It parallelises, but not
  perfectly; there is no headroom story here where more cores sit free.
- **Demucs' own README claims "roughly equal to 1.5 times the duration of
  the track"** on CPU
  ([README, adefossez/demucs](https://github.com/adefossez/demucs)),
  with no hardware named. On this machine the default model measures
  **2.02–2.14×**. The README figure is optimistic for a 2017-era ultrabook
  CPU but the right order of magnitude — which is itself the useful
  finding: the published number can be trusted for planning within ~40%,
  **for the default model only**. It is badly wrong for `htdemucs_ft`
  (7.80×) and for `mdx_extra_q` (6.10×), both of which are bags of four
  models and therefore cost about four runs each.
- **Extrapolated to whole songs** at 2.08× (mean of the two warm
  `htdemucs` runs): a 3-minute track ≈ **6.2 min**, 4 minutes ≈
  **8.3 min**, 6 minutes ≈ **12.5 min**. Against the map's stated
  ~1-hour-per-song top-setting budget, default separation costs **≈14% of
  the budget** for a typical song. That is cheap enough that separation is
  not the pass to optimise. At `htdemucs_ft`'s 7.80×, the same 4-minute
  song costs **≈31 min — 52% of the budget** — which is the single most
  consequential number in this table, because it means the top dial
  position must be *chosen*, not defaulted to.
- **`htdemucs_6s` was *faster* than `htdemucs` here** (1.72× vs 2.02–2.14×),
  not slower, despite producing two more stems. Both are single models (not
  bags), so the extra output channels are cheap; the gap is larger than the
  6% run-to-run variance the two `htdemucs` rows bound, so it looks real
  rather than noise, but it was not investigated. Either way the safe
  reading is **the two cost the same to within a few percent, and six stems
  are certainly not more expensive than four.**
- **Memory is not a constraint for the hybrid-transformer models**
  (1.3–1.9 GB peak against 15 GB) but **is worth noticing for the
  frequency-domain ones**: `hdemucs_mmi` peaked at 2.9 GB and
  `mdx_extra_q` at **4.2 GB** — the *quantized* model is by a wide margin
  the most memory-hungry thing measured here, because quantization shrinks
  the stored weights while its bag-of-four inference still allocates four
  models' worth of activations. On a 15 GB machine none of this bites; on
  a smaller one, `--segment` exists for exactly this and would be needed.

Nobody publishes CPU figures for the rest of the field. **Spleeter's only
published speed claim is "100× faster than real-time … on a GPU"** — no
CPU figure ([Spleeter README](https://github.com/deezer/spleeter)).
Open-Unmix, BS-RoFormer, Mel-Band RoFormer and SCNet publish **none**.
A widely-repeated Open-Unmix RTF of 0.80 on an i5-10500 could not be
traced to a primary source and is **unverified**.

## 3. Licences — code and weights are not the same question

This section exists because #125 already found that code and weight
licences diverge, and because this repo still has no LICENSE file.

**Demucs code: MIT.** Verified three ways — the repo LICENSE (Meta
Platforms, Inc.), the wheel's `METADATA` (`License: MIT License`), and
the LICENSE file vendored inside the installed distribution
(read here at `demucs-4.1.0.dist-info/licenses/LICENSE`). The README says
plainly: *"Demucs is released under the MIT license as found in the
LICENSE file."*

**Demucs weights: not MIT, and effectively unlicensed at the point of
download.** The maintainer's own statement, fetched verbatim from the
GitHub API
([facebookresearch/demucs#327](https://github.com/facebookresearch/demucs/issues/327),
adefossez, 2022-05-23):

> The model weights are not covered by the MIT license, and are provided
> only for scientific purposes.

A later commenter on the same thread gives the cause
(CarlGao4, 2024-06-09):

> The models are trained using MusDB dataset, which requires the result
> model can only be used for research purpose.

That is consistent with MUSDB18's own terms: it is assembled from
MedleyDB (CC BY-NC-SA 4.0) and DSD100 material and is distributed for
academic use ([SigSep](https://sigsep.github.io/datasets/musdb.html)).
A 2024 follow-up question on that same issue — asking whether a Linux
distribution may package the weights, and noting that Intel had
relabelled converted weights as MIT on Hugging Face — **received no
answer**. It is still open.

And **the actual distribution point declares nothing**: `demucs` 4.1.0
downloads from Hugging Face repos under `adefossez/*`, and
`huggingface.co/api/models/adefossez/HTDemucs` returns **no `license`
field at all** (fetched here; tags are only `audio, music,
music-source-separation, demucs`). So a consumer who installs `demucs`
today and reads only what ships with it will see MIT and nothing else.
The restriction lives in a four-year-old issue comment.

**Open-Unmix**: code MIT (Inria); the README states `umxl`'s weights are
**CC BY-NC-SA 4.0**, i.e. explicitly non-commercial
([open-unmix-pytorch README](https://github.com/sigsep/open-unmix-pytorch)).
`umx`/`umxhq` carry no separate statement but are MUSDB18-trained, so the
same shadow falls on them. **Unverified** whether anyone treats
`umxhq` as freely usable.

**Spleeter**: code MIT (LICENSE fetched). No restriction found on the
checkpoints — but "no restriction found" is not "confirmed unrestricted",
and Spleeter is ruled out on Python 3.14 anyway.

**RoFormer-family community checkpoints**: hosted informally on Hugging
Face by third parties with inconsistent or absent licence tags. Treat as
**licence-unclear**.

### What this means for this repo

Every high-quality separator available today traces its weights to
MUSDB18 and inherits a research-only restriction that is stated with
varying degrees of clarity and *never* in the package you install.
Consequences:

1. **Do not vendor or redistribute separation weights.** Download at
   first use, into the user's own cache — which is exactly what
   `demucs` 4.1.0 already does via `huggingface_hub`, so the default
   behaviour is the correct one. This is the same posture #125 reached
   about weights generally, arrived at independently.
2. **The separation extra should say so.** Whatever `[project.
   optional-dependencies]` group carries this (following the
   `librosa`/`music21`/`fluidsynth` isolation convention) should carry a
   note that the downloaded models are research-use, because nothing in
   the install path says it.
3. **This does not block the map**, since the project is not commercial —
   but it does make the missing LICENSE file matter more, not less. It is
   now the second research ticket to land on that fact.

## 4. Does separation help or hurt downstream transcription?

**There is no published apples-to-apples comparison of an off-the-shelf
separator feeding an off-the-shelf transcriber, measured with and without
the separation step, on a shared metric.** I searched for it directly and
did not find it. That sentence is the finding.

What does exist, and what it is worth:

- **The one measured downstream win is already in this map.** #127 found
  a separated drum stem used as an extra beat-tracker input channel
  lifting **downbeat F1 from 0.699 to 0.775** on drum-heavy material.
  That is a real number for a real downstream task, and it is enough on
  its own to justify running a separator at least for the drum stem.
- **The one primary source that speaks to artifacts declines to quantify
  them.** An audio-to-analysis piano pipeline separates with BS-RoFormer
  before transcription, but states
  ([arXiv:2605.06685](https://arxiv.org/html/2605.06685v1), fetched
  here):

  > For recordings known to be piano solo (the entirety of the MAESTRO
  > corpus), separation is bypassed via a `--piano-solo` flag to avoid
  > introducing spectral artifacts on signals that contain only the
  > target instrument.

  So a working system treats separation as **harmful when unnecessary** —
  but its paper reports **no with/without comparison**, only 0.9791 F1 on
  the (unseparated) MAESTRO set and a *qualitative* validation on the
  separated commercial material. The concern is real and documented; its
  magnitude is not.
- **Jointly-trained separate-and-transcribe systems report gains**
  (e.g. [arXiv:2608.01093](https://arxiv.org/html/2608.01093), drum
  transcription via latent-diffusion stem generation), but a jointly
  trained system is not the cascade this map is building, so it does not
  transfer. **Not independently verified beyond the abstract.**
- [arXiv:2412.06703](https://arxiv.org/abs/2412.06703) ("Source
  Separation & Automatic Transcription for Music") builds exactly this
  cascade — separate, transcribe each stem, engrave — but I could not
  extract any ablation numbers from it. **Unverified.**

### Consequence for the map

This is the same shape of gap #124 found for band-mix ground truth, and
it has the same answer: **measure it here.** #124's harness already
produces the baseline — **37.87% onset F1** on real band audio,
transcribing the mix directly. The separation question is then a single
A/B in that harness:

> transcribe(mix) vs. Σ transcribe(stem) — same corpus, same metric.

That experiment is cheap now that install and cost are settled, and it is
the only way this map gets a defensible answer. Until it is run,
**"separation improves band transcription" is an assumption, not a
finding** — the map's separation decision currently rests on #127's
downbeat number alone, which is about *timing*, not *notes*.

One structural argument survives without measurement, and should be
stated because it is not about accuracy at all: **the map's output is a
multi-track score.** Per-instrument tracks need per-instrument audio.
Even if separation were downstream-neutral for note accuracy, it is what
makes the deliverable's shape possible.

## 5. How many stems

Demucs offers 4 (`vocals`, `drums`, `bass`, `other`) and 6
(`+ guitar`, `+ piano`, via `htdemucs_6s`). Both produced correct output
here (**measured**: `htdemucs_6s` wrote six wavs, `htdemucs` four).

The model's authors state directly, in the README:

> Quick testing seems to show okay quality for `guitar`, but a lot of
> bleeding and artifacts for the `piano` source.

No numeric per-stem SDR table isolating guitar/piano was found from a
first-party source; the qualitative caveat above is the authors' own and
outweighs unverified community numbers. Published overall SDR on
MUSDB18-HQ is **9.0 dB for all three of `htdemucs`, `htdemucs_ft` and
`htdemucs_6s`**, and **7.7 dB for `hdemucs_mmi`** (v3, README table).

**Recommendation: default to 4 stems; expose 6 as an option.** Reasons:

1. The piano stem is disclaimed by its own authors, and piano is the one
   instrument where this project has an *excellent* alternative —
   #125 found `transkun` at **0.95 onset F1**, MIT, CPU-default. Feeding
   that a bleeding piano stem would be the weakest link in an otherwise
   strong chain.
2. **6 stems cost nothing extra here** — measured 77.4 s vs 90.9–96.4 s
   wall, the 6-stem model being the *faster* of the two — so the option is
   free to offer.
3. The 4 stems map cleanly onto what the rest of the map needs: `drums`
   is #127's timing oracle *and* the drum-notation input; `bass` and
   `vocals` are monophonic-ish and therefore the two stems this repo's
   existing YIN path could plausibly handle unaided; `other` is the
   polyphonic residue that needs the neural transcriber.
4. More stems means more separation artifacts distributed across more
   files, and §4 establishes that nobody has measured what that costs.

## 6. The fast end of the dial

The map wants speed vs. accuracy as a load-time user choice. On the
evidence:

- **Top of the dial: `htdemucs_ft`.** Same 9.0 dB SDR headline, "a bit
  better" per the README, a bag of 4 per-source models — **measured at
  7.80× real time, 3.7× the default's cost**, which is exactly what a bag
  of four predicts. ≈31 min for a 4-minute song. Worth offering; not worth
  defaulting to, because the README's own description of the quality gain
  is "a bit better" and this map's downstream consumer is a transcriber,
  not a listener.
- **Default: `htdemucs`.** 2.02–2.14× real time, 9.0 dB, one model.
- **Fast end: `htdemucs` with reduced `--overlap`, not a different
  model.** This is the recommendation and it is a change from the obvious
  answer. Reasons: Spleeter is uninstallable; Open-Unmix is a genuinely
  smaller network but its SDR is **far** lower (`umxhq`: vocals 6.25,
  drums 6.04, bass 5.07, other 4.28 — [SigSep results](https://sigsep.github.io/open-unmix/results.html))
  and it carries the *same* non-commercial weight shadow, so it trades a
  lot of quality for an unmeasured speed win; and the quantized
  `mdx_extra_q` turns out to be **the opposite of a fast option — measured
  at 6.10× and 4.2 GB peak RSS, three times slower and three times
  hungrier than the default.** It is a bag of four models whose
  quantization shrinks the *download* (49 MB, the smallest weight set
  here) and nothing else. That is worth stating plainly because "quantized"
  reads as "the light one" and here it is the heaviest. Meanwhile
  `--overlap` is a pure time/quality dial on the model already chosen,
  with no second weight set, no second licence and no second install.
- **Not worth a dial position: a second model family.** Every additional
  separator adds an install surface, a weight download and a licence
  question, for a quality axis the user cannot hear the difference on
  once the output is a *transcription*.

`--segment` shortens the processing window and cuts memory; on this
machine memory was never the binding constraint (1.3 GB peak for the
recommended model, against 15 GB), so it is not a useful dial here — but
it is the flag to reach for if this ever runs somewhere smaller, and
`mdx_extra_q`'s 4.2 GB shows the ceiling is not purely theoretical.

## 7. Disk footprint

**Measured here.**

| Item | Size |
|---|---|
| Scratch venv, CPU torch 2.14 + torchaudio + demucs 4.1.0 + numpy + diffq | **1.1 GB** |
| `htdemucs` weights | 81 MB |
| `htdemucs_6s` weights | 53 MB |
| `htdemucs_ft` weights (4 models) | 321 MB |
| `hdemucs_mmi` weights | 160 MB |
| `mdx_extra_q` weights (quantized) | 49 MB |
| All five model sets cached together | **662 MB** |

The weights are small; **the dependency is `torch` itself**, ~1 GB
installed. That is the real footprint cost of putting separation behind an
optional extra, and it is the same cost every other neural component in
this map will share — so it is paid once, not per feature.

## Recommendation

1. **Use Demucs `htdemucs` via PyPI `demucs` >= 4.1.0**, CPU device,
   4 stems by default. It installs on Python 3.14 with CPU-only torch,
   costs **≈2.1 minutes per minute of audio measured on the target
   machine** (≈8.3 min for a 4-minute song), peaks at 1.3 GB RSS, and is
   the only model in the field
   with any published CPU figure — which this measurement broadly
   confirms.
2. **Offer `htdemucs_6s` as an option, not a default**, and do not build
   the piano path on its piano stem; route piano to `transkun` (#125)
   instead.
3. **Offer `htdemucs_ft` as the top dial position** — measured 7.80×,
   ≈31 min for a 4-minute song, over half the map's hourly budget — and
   `--overlap` on the default model as the fast one. Do **not** reach for
   `mdx_extra_q` as the light option: measured here it is the slowest and
   most memory-hungry of the four-stem models despite the smallest weight
   file. Do not add a second model family.
4. **Download weights at first use; never vendor them.** Document that
   they are research-use-only, because nothing in the install path says
   so. The missing LICENSE file is now blocking two research tickets.
5. **Run the with/without A/B in #124's harness before treating
   separation as an accuracy win.** #127's downbeat number justifies the
   drum stem today; the note-accuracy case is unmeasured everywhere, by
   anyone.

## What this document does not settle

- **Whether separation actually improves note transcription.** Nobody has
  published it and it was not measured here. §4.
- **Real separation quality on real band audio**, on this machine. Timing
  was measured on a synthetic clip because timing is content-independent;
  quality was not measured at all, and MUSDB18 is access-gated.
- **The RoFormer family on CPU.** Higher SDR, no CPU figures, no
  first-party distribution, heavier architecture. Left as the re-check
  if hardware changes.
- **Why `mdx_extra_q` is *slower and hungrier* than the unquantized
  hybrid models.** The bag-of-four structure explains a factor of ~4 in
  time; the 4.2 GB peak is attributed here to four models' activations
  plus the frequency-domain branch, which is reasoning, not a measurement
  of where the bytes went. No published statement was found either way.
- **Why `htdemucs_6s` beat `htdemucs` on wall-clock.** Reproduced only
  once, and larger than the measured run-to-run variance, but not chased.
- **Whether `torch`'s 2-thread default is leaving performance on the
  table.** `torch.get_num_threads()` returns 2 on this 2-core/4-thread
  CPU. `OMP_NUM_THREADS=4` was not tried.

## Sources

**Read directly (primary)**

- `adefossez/demucs` README and repo metadata; `facebookresearch/demucs`
  repo metadata (archived, last push 2024-04-24) — GitHub API.
- `facebookresearch/demucs` issue
  [#327](https://github.com/facebookresearch/demucs/issues/327) comments,
  fetched verbatim via the GitHub API — the weights-licence statement.
- `huggingface.co/api/models/adefossez/HTDemucs` — no licence field.
- PyPI JSON API for `demucs`, `spleeter`, `openunmix` — versions,
  `Requires-Python`, dependency pins, upload dates.
- The installed `demucs-4.1.0.dist-info` (`METADATA`, vendored `LICENSE`).
- [sigsep/open-unmix-pytorch](https://github.com/sigsep/open-unmix-pytorch)
  README; [SigSep open-unmix results](https://sigsep.github.io/open-unmix/results.html).
- [SigSep MUSDB18](https://sigsep.github.io/datasets/musdb.html).
- [deezer/spleeter](https://github.com/deezer/spleeter) README.
- [arXiv:2309.02612](https://arxiv.org/abs/2309.02612) (BS-RoFormer);
  [arXiv:2310.01809](https://arxiv.org/abs/2310.01809) (Mel-Band
  RoFormer); [arXiv:2605.06685](https://arxiv.org/html/2605.06685v1)
  (piano audio-to-analysis pipeline, the separation-bypass quote).
- [ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training).

**Cited but not independently verified**

- [arXiv:2412.06703](https://arxiv.org/abs/2412.06703) — cascade pipeline,
  no extractable ablation.
- [arXiv:2608.01093](https://arxiv.org/html/2608.01093) — joint
  separate-and-detect drum transcription, abstract only.
- SCNet's 9.0 dB claim — secondary sources only.

**This repo, read directly**

`docs/research/pretrained-transcription-weights.md`,
`docs/research/eval-harnesses-and-corpora.md`,
`docs/research/rhythm-meter-and-drum-transcription.md`, `CLAUDE.md`
(optional-extra isolation convention).
