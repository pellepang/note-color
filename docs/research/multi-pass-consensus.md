# Multi-pass transcription and consensus: test-time augmentation, ensembling, and disagreement as a confidence signal

Research for issue
[#143](https://github.com/pellepang/note-color/issues/143), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"), and the deep version of #129's hypothesis
**H3**. The proposal, in the project owner's words:

> "transcribing the music multiple times then comparing them and removing
> artifacts, to get higher accuracy scores."

The intuition is precise and falsifiable: **a hallucinated note should be
unstable under perturbation, a real note stable.** Perturb the input,
transcribe again, keep what recurs. That removes false positives — it
raises **precision** and costs **recall**, which scores worse on F1 and
better on the metric #142 argues actually matters here (editing effort in
this repo's own score editor).

Four sibling documents were read first and are not re-derived:

- [`pretrained-transcription-weights.md`](pretrained-transcription-weights.md)
  (#125) — the candidate models and their split code/weight licences.
- [`cpu-source-separation.md`](cpu-source-separation.md) (#126) — Demucs
  `htdemucs` **measured at 2.02–2.14× real time on this machine**, ≈8.3
  minutes for a 4-minute song. That is the cost anchor for everything below.
- [`eval-harnesses-and-corpora.md`](eval-harnesses-and-corpora.md) (#124) —
  **37.87 onset F1** is the best published number on a real pop/rock band
  mix (MulTTiPop), with **precision 43.13 above recall 36.65**;
  `mir_eval`'s ±50 ms convention.
- `docs/DECISIONS.md` §§ "MIT, and the rules for third-party models and
  weights (issue #139)" and "Transcription stack and pipeline shape
  (issue #129)" — the settled stack.

A concurrent document, [`band-mix-accuracy.md`](band-mix-accuracy.md)
(#142), owns the broad accuracy sweep. This one is narrow: multi-pass,
TTA, reconciliation, confidence.

Claims not verified from a primary source are marked **unverified** in
place. Things established by reading actual shipped source code here are
marked **read here**.

## Questions

1. Does anyone do test-time augmentation (TTA) in automatic music
   transcription, and what was measured?
2. Can we ensemble at the **frame-level posteriorgram** (the strong
   version) or only at the **note-event level** (the weak, black-box
   version)? What does each model in #129's stack actually expose?
3. What is the reconciliation algorithm for note-event voting — prior art,
   or do we design it?
4. Do model soups / weight averaging apply here?
5. What does N passes cost against the ~1 hour/song budget?
6. Is ensemble disagreement a usable per-note confidence signal, and how
   does it compare to the free alternatives?

---

## Summary of the answer, before the evidence

- **Nobody has published test-time augmentation for automatic music
  transcription.** Every hit for "pitch shift" and "augmentation" in AMT
  is *training*-time. Not one of the **eight submissions to the 2025 AMT
  Challenge** — the one recent apples-to-apples multi-instrument
  evaluation — is described as using ensembling, model averaging, TTA,
  multiple inference passes, or output voting. In a competitive setting
  where ensembling is the standard cheap win, nobody reached for it. That
  is a real negative signal, not merely an absence of literature.
- **The strong version of the idea is available, and only for one model.**
  **Read here** in the shipped wheel: `basic-pitch`'s `nmp.onnx` returns
  three raw posteriorgrams — `note`, `onset`, `contour` — *before* any
  note creation, on a **fixed 86.13 fps × 88-semitone grid anchored at
  A0 = 27.5 Hz**, and its Apache-2.0 decoder is a plain function,
  `output_to_notes_polyphonic(frames, onsets, ...)`, taking those arrays
  as arguments. Averaging K posteriorgrams and decoding **once** is a
  ~20-line change with no new dependency. Because the pitch axis is
  exactly semitone-aligned, an integer-semitone pitch shift is an
  **exact array roll** — the inverse is lossless on the frequency axis.
  This is as clean a substrate for frame-level TTA as exists anywhere in
  MIR.
- **`transkun` exposes no posteriorgram** and **YourMT3/MT3 structurally
  cannot have one.** `transkun` is a neural semi-CRF over *intervals*:
  **read here**, `transcribeFrames()` calls `crfBatch.decode()` on a
  pairwise interval score tensor; there is no frame×pitch probability
  matrix at any point. YourMT3 is seq2seq — **read here**, its decoder
  computes logits and immediately discards them (`pred_ids =
  logits.argmax(-1)`), emitting token IDs. Frame-level averaging is
  impossible for both. They can only be voted on as black boxes.
- **The reconciliation algorithm is a known algorithm from an adjacent
  field, not a research problem.** Note events are one-dimensional
  bounding boxes with pitch as the class label, so **Weighted Boxes
  Fusion** (arXiv 1910.13302) transfers directly — cluster by overlap,
  confidence-weight the averaged coordinates, then rescale confidence by
  `C · min(T,N)/N` where T is the cluster size. That final rescale *is* the
  k-of-n dial and the per-note confidence, in one formula. The matching
  primitive is already a library call: `mir_eval.transcription.match_notes`
  (MIT, maximal bipartite matching at ±50 ms / ±50 cents), already a
  planned #124 dependency. ASR's **ROVER** (Fiscus, NIST 1997) is the
  older precedent for the same shape.
- **Model soups are inapplicable by construction.** Weight averaging
  requires several fine-tunes *from a shared initialisation*. This project
  trains nothing (#125's stated goal), each candidate ships **one**
  checkpoint, and the candidates are architecturally unrelated. There is
  nothing to average. Dead end, cleanly.
- **Cost splits the proposal in two, and the split is decisive.**
  Re-running the **whole pipeline** N times is unaffordable: separation
  alone is 8.3 min/song measured, and Demucs' own README says of its
  built-in shift-trick TTA — *"This makes prediction SHIFTS times slower.
  **Don't use it unless you have a GPU.**"* Five passes = ~41.5 min of a
  ~60 min budget, for the least useful perturbation. But **TTA over
  transcription only, reusing one separation pass, is nearly free**:
  `basic-pitch` is a **16,782-parameter** model, and five passes over
  three stems is estimated at **≈3–13 minutes**, against 8.3 minutes of
  separation that is paid once either way. That asymmetry — roughly
  **13× cheaper** — is the single most important number in this document.
- **The honest expectation is a small gain, because TTA passes of one
  model have correlated errors.** basic-pitch's systematic failures
  (octave ghosts, harmonic partials read as notes) are *stable* under a
  semitone shift: the ghost reappears in every pass and survives the vote.
  Consensus removes threshold-boundary noise, not systematic error — and
  the AMT Challenge's own finding that MIROS' precision "plummets from
  0.9067 on solo tracks to 0.4643 on three-instrument tracks" points at
  systematic polyphony confusion as the dominant error mode on exactly the
  material this map targets. In object detection, where both are well
  measured, single-model TTA gained **0.5262 mAP against 0.5344 for a
  two-model ensemble** over a 0.5269 NMS baseline: TTA took the *smaller*
  share.
- **There is a free precision knob that must be beaten first.** **Read
  here**: `basic_pitch.inference.predict()` exposes
  `onset_threshold=0.5` and `frame_threshold=0.3` as plain decode
  parameters. Raising the onset threshold trades recall for precision at
  **zero** extra compute. Any multi-pass scheme must beat
  *threshold-tuned single-pass at matched precision* — not beat the
  default. This is the comparison that decides the ticket, and it is
  cheap to run in #132's harness.
- **The confidence angle is the part worth building, and even there the
  cheap baseline may win.** **Read here**: basic-pitch's decoder already
  returns a per-note `amplitude = np.mean(frames[start:end, freq])` — the
  mean frame posterior over the note's own span, in [0,1] — which it then
  throws away into MIDI velocity. That is a per-note confidence for free,
  today, no extra passes. Meanwhile the one recent MIR-specific
  confidence paper (arXiv 2608.20326, Aug 2026) measured MC-Dropout —
  a stochastic ensemble, 50 forward passes — at **AUROC 84.70 vs. plain
  softmax's 83.60**, concluding disagreement-based uncertainty "offers no
  noticeable improvement despite requiring 50 forward passes", while a
  learned confidence head reached 99.46. Ensemble disagreement is not the
  free lunch it looks like.
- **Verdict: build the small version, and only after the free knob is
  measured.** Frame-averaged TTA over `basic-pitch` alone, 3–5 passes, one
  separation, reusing the existing decoder — cheap enough to be a
  `[preferences]` toggle rather than an architecture. Do **not** re-run
  separation. Do **not** build note-event voting across `transkun` and
  `basic-pitch` (different input modes, they never see the same audio).
  Do **not** treat this as an accuracy strategy until the harness says it
  beats a tuned threshold. **Nothing here changes #129's settled stack** —
  the `transcribe_backends.py` seam already expresses everything this
  document recommends.

---

## 1. What the literature actually says

### 1.1 Test-time augmentation in AMT: a plain negative result

Five searches across arXiv, ISMIR archives and DCASE returned **no primary
source applying augmentation at inference time and reconciling the results
into one transcription**, for any music transcription task. Every hit is
training-time augmentation:

- **YourMT3+** (arXiv 2407.04822) pitch-shifts with a GPU phase vocoder
  during training, assigning batch elements to five groups shifted by
  −2, −1, 0, +1, +2 semitones. At *test* time it does none of this.
  Its "MoE" is **not** an ensemble: verified from the paper, the mixture
  of experts is inside a single model, "replacing the FFNs in latent and
  temporal transformer blocks", routing to two of eight experts, worth
  **+1.5 onset F1 / +1.3 offset F1** on Slakh. That is an architectural
  choice, not multi-pass inference.
- **Robust piano transcription** (Edwards et al., IEEE SPL 2024, arXiv
  2402.01424) reports pitch-shift and reverb augmentation worth **+3.1%**
  and **+2.8%** out-of-distribution F1 — again, training-time.
  **Unverified**: the search index surfaced a phrase suggesting they also
  apply augmentations "independently at test time to demonstrate the
  sensitivity of a model trained without augmentation", but neither the
  abstract page nor the PDF extraction confirmed it, and the PDF did not
  render text cleanly. If real, it is a *diagnostic*, not a reconciled
  multi-pass transcription — the two are different things.
- **The one piece of test-time pitch shifting that exists in this stack's
  own code is dead.** **Read here**, in `mt3-infer` 0.2.0's vendored
  YourMT3: `YourMT3.inference()` has a `test_pitch_shift_layer` hook that
  shifts the input and returns `(pred_ids, x_ps)`. It **never shifts the
  output back**, and the repackager's own comment calls it "a debug-only
  constructor arg never set to non-None by any current caller … this was
  unreachable dead code". The authors built the shift and did not build
  the inverse. That is what "nobody does TTA here" looks like from inside
  the source.

### 1.2 The strongest negative signal: the 2025 AMT Challenge

The challenge paper (arXiv 2603.27528, #125's central citation) describes
eight valid submissions plus an MT3 baseline. Verified by fetching the
paper: **no submitted system is described as using ensembling, model
averaging, test-time augmentation, multiple inference passes, or output
fusion/voting.** The top entry, MIROS, is characterised purely
architecturally — MusicFM encoder, modernised decoders, a recurrent
instrument-conditioned adapter.

This matters more than a literature gap. Challenge entrants optimise for
leaderboard position under a deadline, and in most ML competitions
ensembling and TTA are the first things reached for precisely because they
are cheap and reliable. In this task, eight teams did not. Either it does
not help enough here, or its cost against seq2seq autoregressive decoding
is prohibitive — both are informative for us.

**Caveat, stated plainly:** the paper describes submissions in a paragraph
each. Absence of a description is not proof of absence of the technique.

### 1.3 But frame-level activation averaging *is* standard practice in MIR

The counterweight, and it is a strong one. **Read here**, from madmom's
shipped source (`madmom/features/beats.py`, BSD-3):

```python
def __init__(self, post_processor=average_predictions, online=False,
             nn_files=None, **kwargs):
    ...
    nn = NeuralNetworkEnsemble.load(nn_files, ensemble_fn=post_processor, ...)
```

`RNNBeatProcessor`'s **default** configuration is an ensemble of RNNs whose
**activation functions are averaged frame-by-frame** before the DBN
decoder runs. `average_predictions` lives in `madmom/ml/nn/__init__.py`
and averages elementwise, handling multi-task tuples separately. The same
pattern is the default for `RNNDownBeatProcessor` and the TCN variants.

This is exactly the operation #143 asks about — average posteriorgrams,
decode once — and it is the shipped default of the most-used library in
MIR. It has been for a decade. **Unverified**: the commonly-cited count of
8 networks (the 8-fold cross-validation nets) could not be confirmed,
because `madmom/models` is a git submodule and was not fetched.

Two things follow. First, the technique is not exotic and the reviewer
should not treat it as speculative. Second — and this cuts the other way —
**Beat This!** (Foscarin, Schlüter, Widmer, ISMIR 2024, arXiv 2407.21658),
which #129 already selected as this stack's beat tracker, beats madmom's
ensemble with **one model and no DBN**. Its README confirms inference uses
a single checkpoint (`final0`) by default. That is the H3 counter-argument
in its cleanest empirical form: in the one MIR task where multi-model
frame averaging was standard, a better single model retired it.

---

## 2. Where the ensembling can happen — the crux

Everything depends on what each model exposes *before* its decoder. All
three findings below come from reading the actual shipped code.

| Model | What inference returns | Pre-decode activations? | Frame-level averaging possible? | Licence (code / weights) |
|---|---|---|---|---|
| **`basic-pitch` `nmp.onnx`** | `{"note", "onset", "contour"}` — three float matrices | **Yes, directly.** They *are* the model output; note creation is separate Python | **Yes.** Fixed 86.13 fps × 88 semitone grid, decoder is a pure function of the arrays | Apache-2.0 / Apache-2.0 |
| **`transkun` v2** | `list[Note]` (start, end, pitch, velocity, hasOnset, hasOffset) | Semi-CRF interval score tensor + `computeLogZ`/`forward_backward`, reachable only by forking `transcribe()` | **No posteriorgram exists.** Score-tensor averaging is conceivable but is not the same operation | MIT / MIT (in wheel) |
| **YourMT3+ / MT3 via `mt3-infer`** | token IDs | Logits are computed then discarded at `argmax` | **Structurally impossible** — seq2seq, no frame grid | GPL-3.0 vs Apache-2.0 contradiction; excluded by #129 |
| **Demucs `htdemucs`** | waveform stems | n/a — output is already the "activation" | Yes, and it ships this as `--shifts` | MIT / "scientific purposes only" |
| **Beat This!** | beat/downbeat frame activations | Yes | Yes, but the authors' own result argues against needing it | MIT / MIT |

### 2.1 `basic-pitch` — the one clean case

From the wheel (`basic_pitch/inference.py`, **read here**), the ONNX path:

```python
return {k: v for k, v in zip(
    ["note", "onset", "contour"],
    self.model.run(["StatefulPartitionedCall:1",
                    "StatefulPartitionedCall:2",
                    "StatefulPartitionedCall:0"],
                   {"serving_default_input_2:0": x}))}
```

The graph takes **raw audio** (`(1, 43844, 1)` — 2 s at 22050 Hz minus one
hop) and returns three posteriorgrams. From `constants.py`:

- `AUDIO_SAMPLE_RATE = 22050`, `FFT_HOP = 256` → **86.13 frames/s**,
  **11.61 ms** per frame.
- `note` / `onset`: **88 bins, 1 per semitone**, base 27.5 Hz (A0).
- `contour`: **264 bins, 3 per semitone**, same base.

And from `note_creation.py`, the decoder's signature is a plain function
of arrays:

```python
def output_to_notes_polyphonic(frames, onsets, onset_thresh, frame_thresh,
                               min_note_len, infer_onsets, max_freq,
                               min_freq, melodia_trick=True, energy_tol=11)
```

Its body uses only `numpy` and `scipy.signal.argrelmax`; the module's
`librosa`/`mir_eval`/`resampy`/`pretty_midi` imports serve sonification,
MIDI writing and two trivial Hz↔MIDI conversions. Under #129's plan the
pre/post-processing is reimplemented in this repo anyway (the pip package
will not install on Python 3.14), so **posteriorgram averaging is a
change to code we are already writing**, not a fork of someone else's.

Three consequences worth stating:

1. **The pitch axis is exactly semitone-aligned.** An integer-semitone
   pitch shift of the input maps to an exact ±1 bin roll of `note`/`onset`
   and ±3 bins of `contour`. The inverse is a lossless array roll; only
   the edge bins are lost. No interpolation, no error introduced by the
   realignment itself. Very few models make this so easy.
2. **The windowing is deterministic.** `run_inference` pads by
   `overlap_len/2 = 3840` samples, then windows with
   `hop_size = AUDIO_N_SAMPLES − overlap_len = 36164` samples, trimming 15
   frames from each end of each window's output. Shifting the input by an
   integer multiple of `FFT_HOP` changes *which 2-second window* each
   frame is analysed in — a genuine context perturbation — while keeping
   realignment an **exact integer frame roll**.
3. **The decode thresholds are already parameters.** `predict()` defaults
   to `onset_threshold=0.5`, `frame_threshold=0.3`,
   `minimum_note_length=127.70` ms. See §6.

### 2.2 `transkun` — no posteriorgram, by architecture

**Read here**, `transkun/ModelTransformer.py`:

```python
def transcribeFrames(self, framesBatch, forcedStartPos=None, ...):
    crfBatch, ctxBatch = self.processFramesBatch(framesBatch)
    ...
    path = crfBatch.decode(forcedStartPos=forcedStartPos, forward=False)
```

`NeuralSemiCRFInterval` (in `transkun/CRF/`) exposes `decode`,
`computeLogZ`, `evalPath`, `logProb`, and a `forward_backward`. The model
scores **(start, end) intervals per pitch channel** — 88 pitch channels
(MIDI 21–108) plus two pedal channels (−64 sustain, −67 soft) — and
Viterbi-decodes a path. There is no frame×pitch probability matrix
anywhere in the forward pass, so "average the posteriorgrams" has no
referent.

Two things *are* available to a fork:

- **`logProb(intervals)`** — the model's own log-probability of any
  proposed note set. That is a principled way to score a candidate note
  under the model, and it is the natural confidence signal for the piano
  path (§7).
- **Averaging the interval score tensors** across passes. Legal only when
  the time grids coincide, i.e. for time shifts that are integer multiples
  of `hopSize = 1024` at 44100 Hz (23.2 ms). Not possible for pitch
  shifts, whose mel front-end (`n_mels=229`, `f_min=30`) is not
  semitone-aligned.

Both require reimplementing `transcribe()`'s segment loop, which is
**stateful**: it carries `forcedStartPos` from each 16-second segment to
the next (8-second hop). That state chaining is what makes segment-offset
TTA awkward for `transkun` specifically — a different offset produces a
different chain, not just a shifted one.

Note also, **read here**: `transkun`'s `Note` carries `start`, `end`,
`pitch`, `velocity`, `hasOnset`, `hasOffset` — and **no probability**.
The velocity softmax `pVelocity` is computed and collapsed by
`argmax`; its entropy would be a confidence proxy and is discarded.

### 2.3 YourMT3 / MT3 — structurally impossible

**Read here**, `mt3_infer/models/yourmt3/model/t5mod_helper.py`:

```python
logits = lm_head(dec_hs)     # (b, 1, vocab_size)
_pred_ids = logits.argmax(-1)
...
return pred_ids
```

Greedy autoregressive decoding over a MIDI-like token vocabulary. There is
no frame grid to average on, and no beam search whose scores could be
pooled. Per-token probabilities exist for one step at a time and are
thrown away; exposing them would require a fork, and mapping token
probabilities to *note* confidence is non-trivial because one note spans
several tokens (time-shift, program, on/off, pitch).

Confirmed: **frame-level ensembling is impossible for seq2seq
transcribers.** They can participate only in note-event voting (§4). Since
#129 excludes YourMT3+ on the licence contradiction anyway, this is
currently moot — but it is the finding that matters if the maintainer
clarifies and the model returns.

---

## 3. Test-time augmentation: which perturbations, and which are invertible

The design question is not "which augmentation" but "**which augmentation
has an exact inverse on the output grid**". An inverse that smears the
output destroys precisely the onset precision the exercise is trying to
buy. Ranked for `basic-pitch`, best first:

| Perturbation | How to invert | Exactness | Perturbation strength | Cost |
|---|---|---|---|---|
| **Window-phase shift** — pad input by `k · 256` samples, `k` not a multiple of 141 | roll frame axis by `k` | **Exact** (integer roll) | Weak–moderate: changes which 2 s window each frame sits in, and the overlap trim | 1 extra forward pass |
| **Rate-based pitch shift** — resample by `2^(±n/12)`, changing pitch *and* tempo together | roll pitch axis by `∓n` bins (`∓3n` for contour); rescale the time axis by `2^(∓n/12)` | **Exact on pitch**, one linear interpolation on time (≤ ½ frame ≈ 5.8 ms error, vs. the ±50 ms tolerance) | **Strong** — the whole spectrum moves | 1 pass + a resample |
| **Sub-frame time shift** — pad by a non-multiple of 256 | fractional roll, interpolate frame axis | Approximate | Moderate: genuinely re-aligns the STFT frames | 1 pass |
| **Phase-vocoder pitch shift** (`librosa.effects.pitch_shift`) | roll pitch axis | Exact on pitch | Strong, **but smears transients** | 1 pass + an expensive STFT round trip |
| **Separation variation** (`--shifts`, `--overlap`, seeds) | none needed — stems realign themselves | n/a | Strong (different stems entirely) | **N × 8.3 min** |

Four design notes:

- **Prefer rate-based shift over phase-vocoder shift.** The phase vocoder
  is the standard choice in *training* augmentation, where transient
  smearing is a feature (it teaches robustness). At test time it is a bug:
  it moves onsets, which is the quantity being measured. Rate-based
  shifting is artifact-free apart from resampling, and its inverse is an
  exact affine map on both axes. The cost is that tempo and formants move
  too — acceptable here because `basic-pitch` is instrument-agnostic and
  has no tempo model. **Unverified**: no published source compares the two
  for TTA in any audio task; this is reasoning from the mechanism.
- **Keep shifts inside ±2 semitones.** YourMT3+ trains over exactly
  −2…+2; beyond that the input goes off-distribution and the pass
  contributes noise rather than evidence. **Unverified**: basic-pitch's
  own training-augmentation range was not established from its paper.
  {−1, 0, +1} for N = 3, {−2, −1, 0, +1, +2} for N = 5.
- **Separation-stage TTA is settled and the answer is no.** Demucs already
  ships the shift trick, and its README (**read here**) says: *"The
  `--shifts=SHIFTS` performs multiple predictions with random shifts (a.k.a
  the shift trick) of the input and average them. This makes prediction
  `SHIFTS` times slower. **Don't use it unless you have a GPU.**"* The
  upstream author has answered this question for our hardware. `--overlap`
  (default 0.25, "probably fine", reducible to 0.1 for speed) is a speed
  knob, not a diversity knob.
- **The mechanism's own ceiling.** Passes of one model have **correlated
  errors**. basic-pitch's systematic failures — a partial read as a note,
  an octave ghost — are properties of its learned weights, and a semitone
  shift moves the input without moving the failure. The ghost reappears in
  every pass and *survives* the vote, possibly with raised confidence.
  What consensus removes is the *unstable* error: activations sitting near
  0.5 that fall on either side of the threshold depending on framing.
  The AMT Challenge's own result — MIROS' precision falling from 0.9067 on
  solo to 0.4643 on three-instrument tracks — says the dominant error mode
  on polyphonic band material is systematic confusion, not threshold
  jitter. Expect a small gain, and say so before measuring rather than
  after.

---

## 4. The reconciliation algorithm — prior art exists, in another field

For note-event voting (the only option for `transkun`, and a fallback for
everything), the algorithm does not need designing from scratch. **A note
event is a one-dimensional bounding box** — an interval `[onset, offset]`
with a class label (pitch) and a score. That is exactly the object
detection ensembling problem.

### 4.1 Weighted Boxes Fusion, adapted

**Weighted Boxes Fusion** (Solovyev, Wang, Gabruseva, arXiv 1910.13302),
verified from the paper:

1. Pool all predictions from all N passes into one list, sorted by
   confidence descending.
2. Walk the list; match each box against existing fused clusters by an
   overlap threshold (IoU ≥ 0.55 in vision).
3. No match → start a new cluster. Match → join the cluster.
4. Recompute the fused box: confidence = mean of the cluster's
   confidences; coordinates = confidence-weighted mean,
   `Σᵢ Cᵢ·Xᵢ / Σᵢ Cᵢ`.
5. **Rescale**: `C = C · min(T, N) / N`, where `T` is the cluster size.

Step 5 is the load-bearing one for #143. It is a **continuous k-of-n
threshold**: a note found by 1 of 5 passes keeps a fifth of its
confidence; a note found by all 5 keeps all of it. Applying a single final
confidence cut then becomes the precision/recall dial the ticket asks for,
tunable *after* all passes are complete, with no re-inference. And the
same number is the per-note confidence §7 wants. One formula, both jobs.

Measured, on COCO with two models: WBF **0.5344 mAP** vs. NMS 0.5269,
soft-NMS 0.5239, NMW 0.5285. And the number that calibrates our
expectations: **single-model TTA reached 0.5262** — below the two-model
ensemble's 0.5344, and barely above the 0.5269 NMS baseline. In the domain
where both have been measured properly, TTA is the weaker half.

### 4.2 The translation to note events

| Vision term | Note-event equivalent |
|---|---|
| box coordinates | `[onset, offset]` |
| class | MIDI pitch (or pitch-class + octave) |
| IoU ≥ 0.55 | **onset within ±50 ms and pitch within ±50 cents** — `mir_eval`'s own convention |
| confidence | basic-pitch's `amplitude` (§7); `transkun` has none and must be given a constant, which degrades WBF to plain unweighted voting |
| N models | N TTA passes |

The matching step is a library call, not new code:
`mir_eval.transcription.match_notes(ref_intervals, ref_pitches,
est_intervals, est_pitches, onset_tolerance=0.05, pitch_tolerance=50.0,
offset_ratio=0.2)` returns a **maximal bipartite matching** (via
`util._bipartite_match`), MIT-licensed, and #124 already commits this repo
to `mir_eval` 0.8.2 (confirmed there to resolve on Python 3.14).

Two design decisions `mir_eval` does not make for us:

- **Pairwise matching does not give an N-way clustering.** Pass A may
  match B, B match C, and A not match C. Two workable resolutions:
  (a) **anchor** — match every pass to the unshifted pass, simple and
  deterministic, but a note the anchor missed entirely can never be
  recovered; (b) **greedy agglomerative**, which is what WBF actually
  does — walk confidence-descending, join the first compatible cluster.
  (b) is the better fit and is not more code.
- **Offset disagreement.** Offsets are far less reliable than onsets
  across the whole field — #124's own tables report offset F1 roughly half
  of onset F1 for every model. The right rule is: **cluster on onset and
  pitch only; take the offset as the confidence-weighted mean, or the
  median, and never let offset disagreement break a cluster.** Pitch
  disagreement should break a cluster: two passes claiming different
  pitches at the same instant are reporting two notes, and forcing them
  together fabricates a third.

### 4.3 The older precedent

**ROVER** (Fiscus, *A Post-Processing System to Yield Reduced Word Error
Rates: Recognizer Output Voting Error Reduction*, IEEE ASRU 1997, NIST) is
the canonical output-level voting scheme: align N recognisers' outputs
into one transition network by iterated dynamic programming, then vote per
slot. It is the same shape — black-box outputs, an alignment step, a vote
— and it is the reason the technique is old enough to be uncontroversial.
It is cited here as precedent for the *shape*, not as an implementation:
its DP alignment over a word lattice is a poorer fit for note events than
WBF's clustering, because notes are simultaneous and words are not.

**Answer to the ticket's question:** we do not design this. We adapt WBF,
using `mir_eval.match_notes` as the matcher, and the WBF confidence
rescale as both the k-of-n dial and the confidence output.

---

## 5. Model soups: inapplicable, and cleanly so

**Model soups** (Wortsman et al., ICML 2022, arXiv 2203.05482) average the
*weights* of several models fine-tuned from a **shared initialisation**,
yielding one checkpoint with no extra inference cost. The technique is
real and reaches audio: PSLA reports weight averaging worth **+0.9%** on
audio tagging, and ASR work (arXiv 2210.15282) uses it against
catastrophic forgetting.

It does not apply here, for three independent reasons, any one of which is
sufficient:

1. **This project trains nothing.** #125's framing question is "is there
   an accurate open weight set we can simply take, so this project never
   has to train anything". Soups require fine-tuning runs to average.
   There are none, and creating them would mean training — a different
   project.
2. **Each candidate ships one checkpoint.** `basic-pitch` ships exactly
   `nmp.onnx` / `nmp.tflite` (**read here**, one of each in the wheel).
   `transkun` ships `pretrained/2.0.pt`. The Transkun repo does list three
   variants — V2, V2 Aug (data augmentation), V2 No Ext (no pedal note
   extension) — which *might* share an initialisation and be
   soup-averageable. **Unverified**, and it is a poor bet: "no pedal
   extension" is a different output convention, not a different fine-tune
   of the same objective, and averaging weights across differing
   objectives is the case soups are known to handle worst.
3. **The candidates are architecturally unrelated.** A 16,782-parameter
   CNN, a 6-layer transformer semi-CRF, and a 45.8M-parameter T5 have no
   shared parameter space. There is nothing to add.

Recorded as a dead end so it is not re-proposed. The zero-cost idea in
this space is not weight averaging; it is **using the confidence the
decoder already computes** (§7).

---

## 6. Cost, and the one asymmetry that decides the ticket

Anchors: budget **~1 hour per 4-minute song** (#123). Separation
`htdemucs` **8.3 min for a 4-minute song, measured** on this machine
(#126). Hardware: 2 physical cores / 4 threads, Kaby Lake-U i5-7300U, no
GPU.

| Approach | Separation passes | Transcription passes | Estimated wall-clock, 4-min song | Verdict |
|---|---|---|---|---|
| Baseline (#129 as settled) | 1 (8.3 min) | 1 | **≈9–11 min** | fits easily |
| **TTA over transcription only, N = 3** | 1 (8.3 min) | 3 × 3 stems | **≈10–16 min** | **affordable** |
| **TTA over transcription only, N = 5** | 1 (8.3 min) | 5 × 3 stems | **≈12–21 min** | **affordable** |
| Full-pipeline TTA, N = 3 | 3 (24.9 min) | 3 × 3 stems | ≈27–33 min | half the budget, weakest perturbation |
| Full-pipeline TTA, N = 5 | 5 (41.5 min) | 5 × 3 stems | ≈44–53 min | **at the wall; no** |
| Demucs `--shifts 5` | 5× internal | 1 | ≈42 min separation alone | **author says don't, on CPU** |
| `htdemucs_ft` (better separator) instead | 1 (31 min, measured #126) | 1 | ≈32 min | the alternative use of the same budget |

Transcription-pass cost is **estimated, not measured**. The anchor is
`basic-pitch`'s paper figure of **24 s for a 7:45 file** (≈0.05× real
time), whose CPU-vs-GPU provenance #125 could not establish. For a 4-minute
song that is ~12 s per stem, ~37 s for three stems. Allowing a 4× penalty
for an unfavourable CPU and the ONNX runtime gives **≈0.6–2.5 min per
full 3-stem pass**, hence the ranges above. The model is **16,782
parameters** — three orders of magnitude smaller than anything else in the
pipeline — so being wrong by 4× does not change the conclusion.

**The asymmetry, stated once, plainly:** five TTA passes over
transcription cost **≈3–13 minutes**; five passes over the full pipeline
cost **≈44–53 minutes**. The separation stage is ~13× the price of the
transcription stage and buys the *least* diverse perturbation, because two
Demucs runs on the same audio differ far less than the same audio pitched
a semitone apart. **Reuse the stems.**

For the **solo-piano path**, cost is **entirely unmeasured** — #125 lists
transkun CPU throughput as its largest evidence gap, and this pass did not
close it (no `torch` in the project venv; running models was out of
scope). Structurally, `transkun` processes every audio second **twice**
(16 s segments at 8 s hop) through a 6-layer transformer plus a semi-CRF
decode, so it is unlikely to be near basic-pitch's cost. Even if it runs
at 1× real time, N = 5 is 20 minutes — affordable, but aimed at a path
that already scores **0.9505 onset F1**, where there is almost no
headroom and the editing-effort argument is weakest. **Do not spend
multi-pass budget on solo piano.**

### 6.1 The free knob that must be beaten first

This is the comparison that decides whether the ticket is worth building,
and it costs nothing to run.

**Read here**, from `basic_pitch/inference.py`:

```python
def predict(..., onset_threshold: float = 0.5,
                 frame_threshold: float = 0.3,
                 minimum_note_length: float = 127.70, ...)
```

Raising `onset_threshold` trades recall for precision, on one pass, at zero
extra compute. Published AMT practice already tunes these — a coarse-to-fine
search maximising frame F1 then note F1 is the documented method for
evaluating basic-pitch.

So the honest experiment for #132's harness is **not** "multi-pass vs.
default single-pass". It is:

> Sweep `onset_threshold` on a single pass to trace the precision/recall
> curve. Then run N-pass consensus and trace *its* curve. **Does the
> consensus curve sit above the single-pass curve at matched precision?**

If it does not, the whole idea reduces to an expensive way of turning a
knob that is already free — and it should be recorded as rejected, not
shipped as a feature. The same question applies to the WBF confidence cut,
which is *also* just a threshold, only one computed after N× the compute.

---

## 7. Disagreement as a per-note confidence signal

#143 suspects this is the real prize: even if consensus does not raise
accuracy, per-note variance would let the score editor highlight suspect
notes for review, attacking editing effort directly, and would give #129's
standing decision — *"a stem no model covers well is still transcribed,
and its track marked low-confidence"* — something principled to compute.

The intuition is right. The economics may not be, because **the cheap
baselines are strong and largely unexamined**.

### 7.1 What is already free, today, and being thrown away

**basic-pitch.** **Read here**, `output_to_notes_polyphonic`:

```python
amplitude = np.mean(frames[note_start_idx:i, freq_idx])
note_events.append((note_start_idx, i, freq_idx + MIDI_OFFSET, amplitude))
```

Every note already carries the **mean frame posterior over its own span**,
in [0, 1]. It is then discarded into MIDI velocity —
`velocity=int(np.round(127 * amplitude))`. Under #129, this repo
reimplements the post-processing anyway, so **keeping that number instead
of quantising it into velocity is a one-line change with zero inference
cost**, and it yields a per-note confidence for the entire band path
immediately.

It is **not calibrated** — a mean sigmoid activation is not a probability
of correctness, and it will be systematically over-confident in the way
neural classifiers generally are. But calibration is not what the editor
needs. The editor needs a **ranking**: show me the twenty least-trustworthy
notes first. Ranking is a much weaker requirement than calibration, and the
existing signal may satisfy it outright.

**transkun.** No confidence is exposed, but `NeuralSemiCRFInterval.logProb(intervals)`
computes the model's own log-probability of any proposed note set, and
`forward_backward` gives marginals. A per-note marginal from a semi-CRF is
a genuinely principled confidence — better-founded than a mean sigmoid.
It requires forking `transcribe()`; MIT licence, so permitted.

### 7.2 What the literature says about disagreement as a proxy

The most relevant primary source is recent and MIR-specific:
**TCP_α: Margin-Controlled Confidence estimation for reliable Music
Information Retrieval** (Singh, Singh, Kumar, Arora, IIT Kanpur, arXiv
2608.20326, 20 Aug 2026). It covers rāga identification and frame-wise
ornamentation detection — **not** transcription — and it frames the
problem exactly as this repo needs it: *"our goal is therefore not to
estimate calibrated probabilities, but to learn a confidence score that
reliably distinguishes correct predictions from incorrect ones."* That is
failure prediction, which is what "highlight the suspect notes" means.

Its measured failure-prediction results:

| Method | FPR@95%TPR ↓ | AUPR-Error ↑ | AUROC ↑ |
|---|---|---|---|
| Max Class Probability (free) | 42.25% | 50.59% | 83.60% |
| Energy Score (free) | 37.50% | 56.87% | 86.25% |
| **MC Dropout, 50 forward passes** | **40.75%** | **52.37%** | **84.70%** |
| TCP_α (learned confidence head) | 1.60% | 95.96% | 99.46% |

Two readings, both uncomfortable for #143:

- **MC Dropout — a 50-pass stochastic ensemble — barely beat the free
  softmax number** (84.70 vs. 83.60 AUROC) and *lost* to a free energy
  score (86.25). The authors' own summary: disagreement-based uncertainty
  "offers no noticeable improvement despite requiring 50 forward passes".
- The enormous win came from **a learned confidence head** — which needs
  training, and is therefore outside this project's stated constraints.

**Caveats, stated rather than smoothed:** these are 12-class and 7-class
frame classification tasks in Indian art music, not note-event
transcription on band mixes. MC Dropout is a *stochastic* ensemble
(dropout masks), which is generally accepted as a weaker uncertainty
estimator than a **deep ensemble** of independently-initialised models
(Lakshminarayanan et al., arXiv 1612.01474) — and TTA over one checkpoint
sits somewhere between the two, arguably closer to MC Dropout. So this
table is evidence, not proof, that TTA disagreement will underperform the
free signal here. It is, however, the only MIR-domain measurement of the
comparison that exists.

The one arguable counterweight: arXiv 2304.05104 (Conde et al., 2023/24)
proposes **adaptively weighted** TTA (M-ATTA / V-ATTA) and reports improved
calibration without accuracy loss on CIFAR-10/100 and an aerial imagery
set. That is vision, and its methods need a validation set to fit the
adaptive weights — which this project would have (MulTTiPop, per #124),
but which is another moving part.

### 7.3 What this means for the editor

The design that follows from the evidence, cheapest first:

1. **Ship the free signal.** Keep basic-pitch's `amplitude` as a per-note
   confidence. Render low-confidence notes distinctly in the score editor.
   Cost: zero. This is worth doing whether or not #143 is ever built.
2. **Aggregate it per track** to compute #129's "mark a low-confidence
   track" decision — mean or a low percentile of per-note confidence over
   the stem. Cost: zero.
3. **Only then** measure whether N-pass agreement counts rank suspect
   notes *better* than the free amplitude does. That is a concrete,
   cheap experiment on #132's harness: for each note, compute both scores,
   and compare AUROC against ground-truth correctness on MulTTiPop. If
   agreement count does not clearly beat amplitude, the confidence
   argument for multi-pass collapses along with the accuracy argument.
4. If it does win, WBF's `C · min(T,N)/N` already produces a single number
   combining both — mean confidence *and* agreement count — which is the
   natural thing to ship.

**A caution about combining them:** WBF's confidence-weighted averaging
assumes the input confidences are comparable across passes. They are, for
TTA over one model. They would **not** be across `transkun` and
`basic-pitch` — and in any case those two never see the same audio, since
#129 routes solo piano to one and band stems to the other. **Cross-model
note voting has no home in the settled pipeline.** That is worth saying
explicitly, because "ensemble several models" is the form the idea usually
takes and it is the form that does not fit here.

---

## Recommendation

**Build the small version, gated behind a measurement, and only for the
band path.**

1. **First, and independent of #143: keep the confidence that already
   exists.** Preserve basic-pitch's per-note `amplitude` through this
   repo's reimplemented post-processing instead of collapsing it into MIDI
   velocity, surface it in the score editor, and aggregate it for #129's
   low-confidence track marker. Zero cost, immediate benefit, and it
   creates the baseline every later claim must beat.

2. **Second, run the free-knob experiment in #132's harness.** Sweep
   `onset_threshold` on a single pass and trace the precision/recall
   curve on MulTTiPop. This is the cheapest available answer to #142's
   editing-effort framing, and it may deliver the precision the owner
   wants for nothing. **If it does, #143 is largely moot** and should be
   recorded as such.

3. **Then, if and only if step 2 leaves headroom: frame-averaged TTA over
   `basic-pitch` only.** N = 3 (rate-based pitch shift {−1, 0, +1}) or
   N = 5 with the window-phase shift added. Average the `note`/`onset`/
   `contour` matrices after exact inverse realignment, and run the
   existing decoder **once**. This is the technically strong version, it
   is ~20 lines inside code #129 already commits to writing, it adds no
   dependency, and it costs **≈3–13 minutes** on top of a separation pass
   that is paid anyway. Success criterion: **the consensus curve sits
   above the tuned single-pass curve at matched precision.** Not "beats
   the default".

4. **Do not** re-run separation. Demucs' own README rules it out on CPU,
   and #126's measured 8.3 min/song makes N ≥ 3 unaffordable for the
   weakest perturbation available. The same budget spent on `htdemucs_ft`
   (31 min, measured) is a better-motivated gamble.

5. **Do not** build note-event voting between `transkun` and
   `basic-pitch`. #129 routes them to disjoint inputs; they never
   transcribe the same audio. If note-event voting is ever built, build it
   as WBF over TTA passes of one model, with `mir_eval.match_notes` as the
   matcher and `C · min(T,N)/N` as both the k-of-n dial and the confidence
   output.

6. **Do not** pursue model soups. Nothing to average.

7. **Do not** spend multi-pass budget on the solo-piano path. 0.9505 onset
   F1 leaves almost no headroom, `transkun` exposes no posteriorgram, and
   its CPU cost is unmeasured.

**Expose it as a `[preferences]` setting, not an architecture.** Something
like `transcription_passes` (default 1) is the right shape: it sits behind
the same `transcribe_backends.py` seam #129 already established, it is a
config change rather than surgery, and if the measurement disappoints, the
default of 1 means nothing was lost.

**Nothing here changes #129's settled stack.** The seam already expresses
every recommendation above. What #143 changes, at most, is one default and
one preserved field.

**Is it worth building?** The confidence half — yes, but the *free* version
first, and multi-pass only if it demonstrably ranks suspect notes better.
The accuracy half — probably not, and the reason is mechanical rather than
economic: TTA passes of one model share their systematic errors, the field
has an entire challenge's worth of entrants who did not reach for this,
and the free threshold knob is the honest competitor. The cost is low
enough that measuring it is cheap; the prior is that it will not clear the
bar. **A negative result here is a good outcome, and it should be recorded
rather than quietly dropped.**

---

## What this document does not settle

- **Whether any of this actually helps.** No accuracy claim in this
  document is measured on this stack. Nothing was run — no models were
  executed, per the ticket's constraint. Every number is either cited or
  arithmetic on #126's measured separation cost.
- **The cost of one `basic-pitch` pass on this machine.** Estimated from
  the paper's 24 s / 7:45 figure, whose CPU-vs-GPU provenance #125 already
  flagged as unestablished. The 0.6–2.5 min/pass range is an
  order-of-magnitude estimate with a 4× safety factor, not a measurement.
- **The cost of one `transkun` pass on this machine.** Entirely unmeasured;
  #125 named this its largest gap and it is still open.
- **Whether basic-pitch's `amplitude` ranks errors usefully.** It exists
  and is free; whether it correlates with correctness on real band audio
  is exactly the experiment §7.3 proposes and nobody has published.
- **Whether the AMT Challenge entrants truly used no ensembling.** The
  paper devotes a paragraph per submission; absence of description is not
  proof of absence.
- **Whether Edwards et al. (arXiv 2402.01424) applied augmentations at
  test time.** A search snippet suggested so; neither the abstract page
  nor the PDF confirmed it, and the PDF did not extract cleanly. If they
  did, it was a sensitivity diagnostic, not a reconciled transcription.
- **madmom's beat-ensemble size.** The mechanism is confirmed from source;
  the commonly-cited count of 8 networks is not, because `madmom/models`
  is a submodule that was not fetched.
- **Whether rate-based pitch shift really beats phase-vocoder shift for
  TTA.** Argued from the mechanism (transient smearing vs. exact affine
  inverse); no source measures the comparison in any audio task.
- **Whether `transkun`'s V2 / V2-Aug / V2-No-Ext checkpoints share an
  initialisation** and could therefore be souped. Judged a poor bet, not
  investigated.
- **Whether Beat This! would benefit from checkpoint ensembling.** Its own
  result — one model beating madmom's ensemble — argues no, but the
  authors do not test the ensemble of their own folds. Out of scope here;
  #127 owns the beat path.
- **Sub-frame time-shift TTA's realignment error in practice.** Bounded
  above by half a frame (5.8 ms) against a ±50 ms tolerance by arithmetic,
  but its effect on measured onset F1 is unquantified.

---

## Sources

**Source code read directly here (primary)**

- `basic_pitch-0.4.0-py2.py3-none-any.whl` (Apache-2.0), downloaded from
  PyPI and unzipped without installing:
  `basic_pitch/inference.py` (the ONNX output names, `Model.predict`,
  `run_inference`, `unwrap_output`, the `predict()` threshold defaults),
  `basic_pitch/constants.py` (86.13 fps, 88/264 bins, 27.5 Hz base,
  window/hop geometry), `basic_pitch/note_creation.py`
  (`output_to_notes_polyphonic`'s signature and body, the per-note
  `amplitude`, the velocity mapping), and the bundled
  `saved_models/icassp_2022/nmp.onnx` (230,444 B) / `nmp.tflite`
  (204,448 B).
- `transkun-2.0.1-py3-none-any.whl` (MIT, LICENSE read here):
  `transkun/ModelTransformer.py` (`transcribeFrames`, `transcribe`,
  `targetMIDIPitch`, the discarded velocity softmax, the `Note` fields),
  `transkun/CRF/NeuralSemiCRFInterval.py` (`decode`, `computeLogZ`,
  `forward_backward`, `logProb`), `transkun/transcribe.py`,
  `transkun/pretrained/2.0.conf` (hopSize 1024, fs 44100, 16 s/8 s
  segments, n_mels 229).
- `mt3_infer-0.2.0-py3-none-any.whl` (MIT):
  `mt3_infer/models/yourmt3/model/ymt3.py` (`test_pitch_shift_layer`, the
  "debug-only … unreachable dead code" comment),
  `mt3_infer/models/yourmt3/model/t5mod_helper.py` (`logits.argmax(-1)`),
  `mt3_infer/models/yourmt3/model/pitchshift_layer.py`, and the wheel
  METADATA's licence block.
- [`CPJKU/madmom`](https://github.com/CPJKU/madmom) —
  `madmom/features/beats.py` (`RNNBeatProcessor`'s
  `post_processor=average_predictions`, `NeuralNetworkEnsemble.load`) and
  `madmom/ml/nn/__init__.py` (`average_predictions`), raw from GitHub.
- [`mir-evaluation/mir_eval`](https://github.com/mir-evaluation/mir_eval)
  — `mir_eval/transcription.py` (`match_notes`, `match_note_onsets`, the
  0.05 s / 50 cent defaults, `util._bipartite_match`), raw from GitHub.
- [`adefossez/demucs`](https://github.com/adefossez/demucs) README — the
  `--shifts` shift-trick description and the *"Don't use it unless you
  have a GPU"* instruction; `--overlap` default 0.25.
- PyPI JSON API for `basic-pitch` 0.4.0, `transkun` 2.0.1, `mt3-infer`
  0.2.0 — versions, file sizes, `Requires-Python`, licence text.

**Papers fetched and read (primary)**

- [arXiv:2603.27528](https://arxiv.org/html/2603.27528v1) — *Advancing
  Multi-Instrument Music Transcription: Results from the 2025 AMT
  Challenge*. No submission described as using ensembling / TTA / voting;
  MIROS P 0.6558 / R 0.5724; precision 0.9067 → 0.4643 from solo to
  three-instrument.
- [arXiv:1910.13302](https://arxiv.org/abs/1910.13302) — Solovyev, Wang,
  Gabruseva, *Weighted Boxes Fusion*. Algorithm steps, the
  `C = C·min(T,N)/N` rescale, WBF 0.5344 vs. NMS 0.5269 / soft-NMS 0.5239
  / NMW 0.5285, and single-model TTA at 0.5262.
- [arXiv:2608.20326](https://arxiv.org/html/2608.20326) — Singh, Singh,
  Kumar, Arora, *TCP_α: Margin-Controlled Confidence estimation for
  reliable Music Information Retrieval* (Aug 2026). The failure-prediction
  table and the MC-Dropout-vs-softmax verdict.
- [arXiv:2407.04822](https://arxiv.org/html/2407.04822v1) — *YourMT3+*.
  MoE is intra-model (top-2 of 8 experts, +1.5 onset F1), pitch-shift
  augmentation is training-time, ±2 semitones in five groups.
- [arXiv:2203.05482](https://arxiv.org/abs/2203.05482) — Wortsman et al.,
  *Model soups*. The shared-initialisation requirement.
- [arXiv:2304.05104](https://arxiv.org/abs/2304.05104) — Conde et al.,
  *Approaching Test Time Augmentation in the Context of Uncertainty
  Calibration*. M-ATTA / V-ATTA; abstract only, no extractable numbers.
- [arXiv:2402.01424](https://arxiv.org/abs/2402.01424) — Edwards, Dixon,
  Benetos, Maezawa, Kusaka, *A Data-Driven Analysis of Robust Automatic
  Piano Transcription*, IEEE SPL 2024. Abstract read; the test-time claim
  could not be confirmed (see "does not settle").

**Cited from secondary indexing, not independently verified**

- Fiscus, *A Post-Processing System to Yield Reduced Word Error Rates:
  Recognizer Output Voting Error Reduction (ROVER)*, IEEE ASRU 1997
  ([NIST publication record](https://www.nist.gov/publications/post-processing-system-yield-reduced-word-error-rates-recognizer-output-voting-error)) —
  cited for the shape of output-level voting only.
- Lakshminarayanan et al., [arXiv:1612.01474](https://arxiv.org/abs/1612.01474),
  *Deep Ensembles* — cited for the deep-ensemble-vs-MC-dropout distinction.
- PSLA ([arXiv:2102.01243](https://arxiv.org/pdf/2102.01243)) weight
  averaging worth +0.9% on audio tagging; ASR weight averaging
  ([arXiv:2210.15282](https://arxiv.org/pdf/2210.15282)).
- [arXiv:2407.21658](https://arxiv.org/abs/2407.21658) — *Beat This!*
  (ISMIR 2024). Single-checkpoint default confirmed from the
  [repo README](https://github.com/CPJKU/beat_this); the
  beats-madmom's-ensemble claim is taken from #127/#129's existing
  reading, not re-verified here.
- The commonly-cited count of 8 networks in madmom's beat ensemble.

**This repo, read directly**

`docs/research/pretrained-transcription-weights.md` (#125),
`docs/research/cpu-source-separation.md` (#126, the 2.02–2.14× and
`htdemucs_ft` 7.80× measurements),
`docs/research/eval-harnesses-and-corpora.md` (#124, MulTTiPop 37.87 /
43.13 / 36.65 and the `mir_eval` conventions), `docs/DECISIONS.md`
(#139's four licence categories and 1 MB weight ceiling; #129's model
routing, `transcribe_backends.py` seam, extras, and H1–H3),
`CLAUDE.md` (the lazy-import and optional-extra conventions).
