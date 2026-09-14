# Measured CPU cost of every `convert` pass (issue #134)

Measurements taken while settling #134's speed/accuracy dial. Everything here
was **run on the target machine**; nothing is cited. Cost, determinism and model
size are content-independent facts about the tools, so they need neither #132's
harness nor #133's corpora. Every *accuracy* claim in this document is either
against synthetic ground truth and flagged as such, or absent — map #123's
evaluation-before-algorithm rule keeps real accuracy numbers behind #133.

Machine: Intel Core i5-7300U @ 2.60 GHz, 2 cores / 4 threads, no GPU, Linux,
Python 3.14.7 — the same box #126 used. One venv holding the whole `[convert]`
stack: `torch 2.14.0+cpu`, `torchaudio 2.11.0+cpu`, `demucs 4.1.0`,
`transkun 2.0.1`, `beat-this 1.1.0`, `onnxruntime 1.30.0`, `numpy 2.5.3`.
Wall / child-CPU / peak RSS captured by a `resource.getrusage(RUSAGE_CHILDREN)`
wrapper, same shape as #126's. RTF = wall ÷ 45 s of audio.

Two 45 s 44.1 kHz stereo clips:

- `clip45.wav` — synthetic mix (bass + triad pad + hi-hat noise + wobble lead),
  #126's recipe. transkun finds **zero notes** in it, so it measures the network
  only.
- `piano45.wav` — a 495-note dense piano performance (chords + running 16ths)
  rendered from MIDI through fluidsynth + TimGM6mb.sf2. `piano.mid` is exact
  ground truth for it.

## Per-pass cost

| Pass | Setting | Wall (s) | CPU (s) | RTF | Peak RSS | min per min of audio |
|---|---|---|---|---|---|---|
| `htdemucs` separation | default `--overlap 0.25` | 63.3 / 57.8 | 118.7 / 108.3 | **1.28–1.41×** | 1300 MB | 1.3–1.4 |
| `htdemucs` separation | `--overlap 0.05` | 47.7 | 90.5 | **1.06×** | 1319 MB | 1.1 |
| Beat This! beats+downbeats | CPU, default | 7.7 | 12.9 | **0.17×** | 528 MB | 0.17 |
| `nmp.onnx` (basic-pitch graph) | onnxruntime, per stem | 1.6 | 2.8 | **0.04×** | 108 MB | 0.04 |
| `transkun` v2 | hop 16 s (no overlap) | 28.7 | 50.4 | **0.64×** | 1472 MB | 0.6 |
| `transkun` v2 | hop 8 s (**default**) | 68.6 | 125.4 | **1.52×** | 1482 MB | 1.5 |
| `transkun` v2 | hop 4 s | 137.8 | 260.7 | **3.06×** | 1485 MB | 3.1 |

transkun rows are on `piano45.wav`. On the note-free `clip45.wav` the same
settings cost 0.44× / 1.04× / 3.12× — i.e. **transkun's cost is
content-dependent** (+46 % at the default hop once there are notes to decode),
unlike separation, which is not. #126's "timing is content-independent" holds
for the separator and does not generalise to the CRF decoder.

Cold vs warm: transkun's first run cost 63.3 s against 46.9 s warm on the same
file — **≈16 s of fixed per-process startup** (torch import + 56 MB weight
load). That is per *pass process*, and it is the floor under any
resume-from-cache design.

## What the extra transkun compute actually buys

Onset F1/F0.5 (`mir_eval`, ±50 ms, offset ignored) against `piano.mid`:

| hop | RTF | est. notes | P | R | F1 | F0.5 | near-duplicate notes |
|---|---|---|---|---|---|---|---|
| 16 s | 0.64× | 482 | 1.0000 | 0.9737 | 0.9867 | 0.9946 | 0 |
| **8 s (default)** | 1.52× | 495 | 1.0000 | 1.0000 | **1.0000** | **1.0000** | 0 |
| 4 s | 3.06× | 617 | 0.8023 | 1.0000 | 0.8903 | 0.8353 | **120** |

**More overlap is worse, not slower-but-better.** At hop 4 s transkun emits 120
duplicate notes — same pitch, within 100 ms — from double-emitting the overlapped
region. Twice the compute for a score full of doubled notes, which under decision
49's precision-weighted F0.5 is the most expensive error class there is.

Caveat, stated so it is not over-read: synthetic GM-piano audio, one seed,
MIDI-exact ground truth — an easy best case, which is why F1 reaches 1.000.
The claim here is the **ordering and the duplicate mechanism**, not the absolute
numbers. Real-corpus numbers wait on #132/#133.

## Pipeline budget for a 4-minute song

| Mode | Passes | Total |
|---|---|---|
| Piano, hop 16 s | transkun 2.6 min + Beat This! 0.7 min | **≈3.3 min** |
| Piano, hop 8 s (default) | transkun 6.1 min + Beat This! 0.7 min | **≈6.8 min** |
| Band, `--overlap 0.05` | sep 4.2 + beats 0.7 + 4 × nmp 0.6 | **≈5.5 min** |
| Band, default overlap | sep 5.1–5.6 + beats 0.7 + 4 × nmp 0.6 | **≈6.4–6.9 min** |
| Band, `htdemucs_ft` | sep ≈31 min (#126, not re-measured) + 1.3 | **≈32 min** |

**The hour budget is nowhere near spent.** Everything except `htdemucs_ft` lands
between 3 and 7 minutes for a 4-minute song.

Separation dominates band mode; transcription of the non-piano stems is free
(0.04× each — four stems cost 36 s of a 4-minute song). Peak RSS stays at
1.3–1.5 GB per pass on a 15 GB box, so passes must stay sequential but nothing
here is memory-bound.

## Determinism: every model in the stack is bit-exact across runs

The project owner asked whether running the same file twice produces the same
output. Measured on the same clip, same settings:

| Model | Two runs |
|---|---|
| `transkun` (hop 8 s) | **byte-identical MIDI** — md5 `23a53e61…` both times |
| `nmp.onnx` via onnxruntime | **bit-identical posteriorgrams** — digest `6bb1ad7f…` both times |
| Beat This! | identical beat and downbeat arrays |

This settles the shape of any consensus scheme (#215): repeated runs of an
unchanged input are **unanimous by construction and carry no information**. Only
a *perturbed* input produces passes that can disagree, which is why
`multi-pass-consensus.md` (#143) builds TTA on rate-based resampling rather than
on repetition.

## Model size: the band path runs a 391× smaller model than the piano path

Parameter counts read out of the shipped weights:

| Model | Params | On disk | Cost |
|---|---|---|---|
| `transkun` v2 (solo piano) | **14,087,028** | 56 MB, in the wheel | 1.52× RT |
| `nmp.onnx` (**every non-piano stem**) | **36,037** | 230 KB, vendored | **0.04× RT** |

`nmp.onnx` is on the default path because of #139 — the only permissive,
token-free, 3.14-installable option #129 could find — not because it is
accurate; #142 already found `timbreAMT` beating it on every ensemble set. The
0.04× figure is what 36 K parameters buys: **four stems of a 4-minute song in 36
seconds**. Stated plainly for #215: the band path currently runs the lightest
model in the field while roughly 97 % of the hour budget goes unspent.

## Install findings (all new, all on Python 3.14.7)

The whole stack resolves in **one venv with no conflicts** — 1.6 GB installed,
plus 132 MB fetched at first use (`htdemucs` 50.8 MB + Beat This! 81 MB, both
into `~/.cache/torch/hub/checkpoints/`). transkun's 56 MB weights ship inside
the wheel; `nmp.onnx` is 230 KB. But three things break out of the box:

1. **`torchaudio` from PyPI is linked against the CUDA torch build.** With
   `torch 2.14.0+cpu` installed, `import torchaudio` dies with
   `OSError: Could not load this library: …/_torchaudio.abi3.so`, and transkun
   with it. Fix: install `torchaudio` from `download.pytorch.org/whl/cpu` too,
   not just `torch`.
2. **transkun → pydub → `audioop`, removed from the stdlib in 3.13.**
   `ModuleNotFoundError: No module named 'pyaudioop'`. Fix: depend on
   `audioop-lts`.
3. **Beat This! 1.1.0 loads audio via `torchaudio.load`, which no longer works
   on torchaudio 2.11**, and its fallback chain ends in
   `RuntimeError: Could not load audio from "…"`. Fix: `soundfile` must be in
   the extra — the fallback only works if it is installed.

None are upstream bugs this repo can wait on; all three are pins in the
`[convert]` extra.

## One correction to #126

`htdemucs` measured **1.28–1.41× here, against #126's 2.02–2.14×** on the same
machine — roughly 35 % faster, presumably a torch/demucs version change since
that ticket. `htdemucs_ft`'s 7.80× was not re-measured and may have moved the
same way; the ≈31 min figure above is #126's and is now an upper bound.
