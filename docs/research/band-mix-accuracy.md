# Maximising band-mix accuracy: task reframing, instrument identity, per-stem transcription and specialist models

Research for issue
[#142](https://github.com/pellepang/note-color/issues/142), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"), opened by the project owner on reading
[#129](https://github.com/pellepang/note-color/issues/129)'s numbers:

> 38% accuracy is bad. Are there better ways of doing this?

Five sibling documents were read in full first and are **not** re-derived
here. They establish the state of knowledge this one builds on:

- [`pretrained-transcription-weights.md`](pretrained-transcription-weights.md)
  (#125) — the open weight landscape, licences, and the fact that **no open
  model emits the score layer**.
- [`eval-harnesses-and-corpora.md`](eval-harnesses-and-corpora.md)
  (#124) — the corpora, the metrics, and the load-bearing number:
  **37.87% onset F1 on real commercial band audio**.
- [`oss-landscape-chord-multipitch.md`](oss-landscape-chord-multipitch.md)
  — MIREX note-level multi-F0 tops out at **0.40–0.48 F-measure**;
  chord estimation on real pop/rock has sat at **75–80% MajMin for a
  decade**.
- [`cpu-source-separation.md`](cpu-source-separation.md) (#126) — Demucs
  measured on this machine at **2.1× real time**, weights not MIT, and
  **no published study** measures a separator feeding a transcriber
  with and without separation.
- [`rhythm-meter-and-drum-transcription.md`](rhythm-meter-and-drum-transcription.md)
  (#127) — Beat This! as the beat/downbeat default, drum-stem timing
  oracle (downbeat F1 0.699 → 0.775), and the ADT licence landscape.

Licence judgements throughout apply `docs/DECISIONS.md`'s **"MIT, and the
rules for third-party models and weights (issue #139)"** section: full GPL
refused outright even behind an extra; NC/unlicensed weights only via an
explicit user-prompted download, never bundled; anything needing a Hugging
Face account is expert-only.

Everything below is sourced. Claims that could not be verified from a
primary source are marked **unverified** in place. Things actually
executed on this machine are marked **measured here**, following #124's
convention.

## Questions

1. What is actually achievable end to end for a **melody + chord symbols**
   target on real commercial recordings, with which models, at what
   licence and CPU cost — and what can that target *not* represent?
2. Does dropping instrument identification recover the ~12 points
   MulTTiPop shows? Do the candidate models **expose** instrument-agnostic
   inference, or is instrument identity baked into the output vocabulary?
3. Has anyone published a **separated-stem vs. whole-mix** transcription
   comparison? If not, what exactly is the experiment?
4. Do **specialist per-instrument** models beat a generalist on their own
   instrument, and are they licence- and CPU-viable here?
5. Anything newer than #125's sweep, especially evaluated on **real** band
   audio?
6. Does **ensembling** earn its cost for note transcription?
7. What should an **editing-effort metric** look like for #132?

## Summary of the answer, before the evidence

- **Question 2's premise does not survive contact with the code, and this
  is the sharpest finding in the document.** The ~12 points between
  MulTTiPop's 25.29 and 37.87 are **already banked**: both figures come
  from *the same model output*, scored twice under two different matching
  rules. Opening the `mt3-infer` 0.2.0 wheel (**measured here**) shows
  YourMT3's instrument identity is not a discardable label but a
  structural property of decoding — the `Multi` variant maps every program
  class onto **its own decoder channel** (`program2channel_vocab`,
  `num_decoding_channels`), and the only instrument-related knob the
  toolkit exposes is a *post-hoc MIDI-level* drum-leakage filter
  (`auto_filter`). **There is no instrument-agnostic inference mode to
  switch on, and nothing further to recover.** 37.87 is the
  instrument-agnostic number.
- **Task reframing is real, and it is the largest available win — but it
  is a different product, not a better transcription.** A melody line plus
  chord symbols is served by two mature, separately-benchmarked tasks:
  vocal-melody note transcription at **COnP 0.798 / COnPOff 0.625** on
  real pop (Mel-RoFormer on MIR-ST500) and chord estimation at
  **75.52% triads / 81.59% MajMin / 67.44% tetrads** WCSR over 485 real
  pop/rock songs (BTC, APSIPA 2025 re-run). Those are roughly double the
  full-mix note figure on the same kind of audio.
- **A clean, permissive, CPU-plausible chord estimator exists and is not
  in #129's stack.** `BTC-ISMIR19` is **MIT code with the pretrained
  weights committed inside the MIT repo** (`btc_model.pt` 12.2 MB,
  `btc_model_large_voca.pt` 12.2 MB — **verified here** via the GitHub
  contents API). Its two obvious alternatives are licence traps:
  NNLS-Chroma/Chordino is **GPL-2.0** (verified), and `autochord`, despite
  its own Apache-2.0 grant, **runs on the NNLS-Chroma Vamp plugin**, so it
  inherits the refusal.
- **Nobody has published the per-stem vs. whole-mix comparison, still.**
  The two nearest misses are both single-task and neither is a controlled
  A/B: drum transcription off a separated drum stem reaches F 0.84 MDB /
  0.76 ENST against ADTOF's *published* 0.72 / 0.65 (arXiv 2509.24853 —
  the authors state these are quoted, not re-run, so it is **not** a
  controlled baseline), and chord recognition through a Demucs
  separate-amplify-remix front end moved WCSR triads **75.52 → 75.72**,
  i.e. **+0.20 points** (APSIPA 2025). H1 remains unmeasured, and the one
  number that exists for a *harmonic* task is approximately zero.
- **Specialist models are a weaker angle than the ticket hoped.** On the
  one instrument where a generalist and specialists have published numbers
  on a shared corpus, **the generalist wins**: YourMT3+ scores 91.65 on
  GuitarSet against TART's 0.838 F50 and GAPS' 88.1 zero-shot. And the
  specialist field is licence- and release-poor: the best vocal models
  (Mel-RoFormer, T3MS) and the best guitar models (TART, GAPS, N2N for
  drums) publish **no weights at all** — GAPS' authors say explicitly they
  are "carefully considering" whether to release them. What *is* released
  and permissive (ROSVOT, VOCANO, STARS, FretNet — all MIT) is either
  older or unbenchmarked against the generalists.
- **One genuinely new, adoptable candidate turned up: `timbreAMT`**
  (Apache-2.0, verified) — an explicitly two-stage design that transcribes
  notes *without* timbre first and clusters instruments after, at
  **18,978–26,620 parameters**, ONNX-exported, beating `basic-pitch` on
  every ensemble set it reports (URMP ensemble .723 vs .681, PHENICX
  ensemble .595 vs .503). It is the same shape and licence tier as the
  `nmp.onnx` #129 already chose, and it is a drop-in A/B for it.
- **Ensembling has no evidence to earn its cost.** In the 2025 AMT
  Challenge — 21 teams, 14 submissions, 8 valid — **no submission was an
  ensemble**, and the winner beat the #2 by 0.0060 F1.
- **The metric point is right and there is prior art for it.** F1 is the
  wrong objective here; the literature that studies error salience agrees
  (Ycart et al., TISMIR 2020) and the notation-side metrics this repo has
  already adopted (`musicdiff`'s OMR-NED) are *already* edit distances,
  i.e. already editing-effort-shaped. §7 recommends a concrete triple:
  **F0.5** as the comparable headline, **ghost notes per bar** and
  **missed notes per bar** reported separately and never averaged, and an
  **editor-operation count** computed in this repo's own action vocabulary.

---

## 1. Task reframing: a melody-and-chords target

### 1.1 The two tasks, and their real-audio numbers

A Real Book chart (#141) is a melody line plus chord symbols. That is not
a harder version of multi-instrument transcription; it is **two different,
older, better-studied tasks**, each with its own real-audio benchmark
history, and neither of them is scored by "every note of every
instrument".

| Ask | Corpus | Audio | Best published | Source |
|---|---|---|---|---|
| Every note of every instrument, instrument label must match | MulTTiPop | real pop/rock | **25.29** onset F1 | arXiv 2607.08756 (via #124) |
| Every note of every instrument, instrument-agnostic | MulTTiPop | real pop/rock | **37.87** onset F1 | arXiv 2607.08756 (via #124) |
| **Vocal melody line only, onset + pitch** | MIR-ST500 | **real pop** | **0.798 (COnP)** · 0.819 COn · 0.625 COnPOff | arXiv 2409.04702 |
| **Vocal melody line only, multi-F1** | MIR-ST500 | real pop | 71.07 | arXiv 2407.04822 (via #124) |
| **Chord symbols, MajMin** | Isophonics + RobbieWilliams + uspop2002 (485 songs) | **real pop/rock** | **81.59% WCSR** | APSIPA 2025 (BTC re-run) |
| **Chord symbols, triads** | same 485 songs | real pop/rock | **75.52% WCSR** | APSIPA 2025 |
| **Chord symbols, tetrads** | same 485 songs | real pop/rock | **67.44% WCSR** | APSIPA 2025 |

Two things about that table matter more than the individual figures.

**The melody and chord numbers are on real commercial recordings by
famous artists** — The Beatles, Queen, Robbie Williams — not synthetic
renders. This is the one place in map #123's whole evidence base where the
literature's headline numbers were *already* measured on the target input,
rather than needing to be re-measured downward.

**They are also computed under a different scoring convention.** WCSR is a
frame-weighted symbol recall, not `mir_eval`'s ±50 ms note-onset F1. A
75.52% WCSR and a 37.87% onset F1 are **not comparable numbers**, and this
document does not claim they are — what is comparable is the *shape* of
the two problems: a chord label is a slowly-changing, heavily
context-constrained symbol over a whole beat or bar, and a melody is one
monophonic voice. Both are enormously more constrained than "every
simultaneous note in a four-piece band", which is why both are much
further along.

### 1.2 Melody: what serves it, and at what licence

The route is not in doubt architecturally — Demucs already produces a
`vocals` stem (#126, measured at 2.1× real time here), and the task on
that stem is *singing voice transcription*, which has its own corpus
(MIR-ST500, 500 songs, ~30 h) and its own decade of work. What is in
doubt is which model.

| Model | Year | Best number | Code licence | Weights | Py3.14 / CPU |
|---|---|---|---|---|---|
| **Mel-RoFormer** (vocal melody transcription) | 2024 | **COnP 0.798, COnPOff 0.625** on MIR-ST500 | paper CC BY 4.0 | **not released by the authors** | — |
| **T3MS** | 2025 | Note-level F1 0.610, onset F1 0.806 on ST500 | — | **no release mentioned** | — |
| **YourMT3+** (singing subtask) | 2024 | 71.07 multi F1 on MIR-ST500 | **GPL-3.0** repo, Apache-2.0 per-file headers | Apache-2.0 on HF | refused by #139 (contradiction → most restrictive) |
| **ROSVOT** | ACL 2024 | "state of the art ... with clean or noisy inputs" (**abstract only; numbers not extracted — unverified**) | **MIT** (verified) | weights provided per README | **unverified** |
| **STARS** (transcription + alignment + style) | 2026, pushed 2026-08-18 | not extracted (**unverified**) | **MIT** (verified) | — | **unverified** |
| **VOCANO** | ISMIR 2021 | — (**unverified**) | **MIT** (verified) | — | last push 2021-08 |
| `basic-pitch` `nmp.onnx` on the vocals stem | 2022 | 0.709 note F1 MAESTRO; no MIR-ST500 figure | Apache-2.0 | Apache-2.0, 230 KB | vendored per #129 |
| `torchcrepe` / `penn` f0 → this repo's own note segmenter | — | f0 estimation only, not note-level | **MIT** both | — | plain torch |

**The honest reading**: the two best vocal-melody numbers in the
literature belong to models nobody can run, and the strongest one anybody
*can* run under #139's rules is a licence question away (YourMT3+) or an
unmeasured one (ROSVOT, STARS). The permissive, definitely-installable
fallback is the `nmp.onnx` #129 already chose, run on the vocals stem —
which is exactly what #129's routing already does, without anyone having
noticed that this is the melody-extraction path.

A fifth option deserves naming because it is cheap and this repo is
unusually well placed for it: **`torchcrepe` or `penn` (both MIT) produce
a monophonic f0 contour**, and a vocals stem is close to monophonic by
construction. This repo already owns a note segmenter that turns a pitch
track plus an onset signal into note events with measured durations
(`note_smoother.py`, `duration_tracker.py`, `onset_detect.py`), and
`duration_tracker.finalize_noncausal()` already does the non-causal
version. That is a melody transcriber assembled almost entirely out of
parts that already exist and are already MIT. It is very unlikely to reach
0.798 COnP; it is very likely to reach *something*, and it costs no new
licence exposure at all. Worth an A/B, not an assumption.

### 1.3 Chords: one clean answer, and two traps

**The clean answer: `BTC-ISMIR19`.** Bi-directional Transformer for
Musical Chord Recognition, Park et al., ISMIR 2019.

- **Licence: MIT** (`Copyright (c) 2019 Jonggwon Park`, LICENSE fetched
  verbatim), and — the part that matters — **the pretrained weights are
  committed inside that same MIT repository**: `test/btc_model.pt`
  (12,154,754 bytes) and `test/btc_model_large_voca.pt` (12,229,576 bytes),
  **verified here** through the GitHub contents API. There is no separate
  weights-licence statement, so the repo's single MIT grant is the only
  statement that exists; that is the *favourable* reading of an ambiguity,
  and it is flagged as such in §10.
- **Independently re-run on real audio in 2025**: Mitoma & Furuya (APSIPA
  ASC 2025) evaluate the released BTC checkpoint on 485 real pop/rock
  songs (225 Isophonics, 65 Robbie Williams, 195 uspop2002) with a
  **large 170-chord vocabulary**, scoring **WCSR triads 75.52, root 82.51,
  maj-min 81.59, tetrads 67.44**. This is a third-party reproduction of a
  released checkpoint on real commercial recordings, which is a
  substantially stronger form of evidence than a self-reported table.
- Dependencies (README): `pytorch`, `numpy`, `pandas`, `librosa`,
  `pyyaml`, `mir_eval`, `pretty_midi`, plus `pyrubberband` for training
  only. No PyPI package — it is a clone-or-vendor, not a `pip install`.
  Inference is a CQT plus a small transformer over ~12 MB of weights, so
  it is *plausibly* cheap on this CPU; **no CPU figure is published and
  none was measured here — unverified.**
- 12 MB is over #139's 1 MB vendoring ceiling, so the weights would
  download rather than be committed. Because they are MIT, that download
  needs **no terms prompt** — unlike Demucs.

**Trap 1: NNLS-Chroma / Chordino is GPL-2.0.**
`github.com/c4dm/nnls-chroma`'s licence is **GPL-2.0** (verified via the
GitHub API). `oss-landscape-chord-multipitch.md` recommends borrowing its
*technique* — approximate NNLS note transcription before chroma folding,
worth +6 points overall and +12 on harmonically ambiguous chords per Mauch
& Dixon, ISMIR 2010 — and that recommendation is unaffected, because an
idea is not code. But **the plugin itself cannot ship here** under #139's
"full GPL refused outright, even behind an optional extra".

**Trap 2: `autochord` is Apache-2.0 on top of that GPL-2.0 plugin.**
`cjbayron/autochord` (Apache-2.0, verified) is superficially the perfect
fit — a pip-installable chord recogniser with auto-downloading weights.
But its README states it "uses the NNLS-Chroma VAMP plugin to extract
chroma features", so a shipped dependency chain reaches GPL-2.0 code and
the refusal applies transitively. Independently it would fail anyway:
PyPI 0.1.4 (2021-10-07) requires `tensorflow>=2.6` plus the `vamp` host
bindings, and its reported test accuracy is **67.33%** — below BTC.

**Trap 3 (already known): madmom's Deep Chroma.** `oss-landscape-chord-
multipitch.md` measured ~80.4% MajMin for madmom's CNN/CRF recogniser.
#127 established that madmom's BSD code is fine and its **downbeat DBN
loads no model**, so the CC BY-NC-SA `.npy` term never reaches that path.
**Chord recognition is the opposite case** — `DeepChromaChordRecognition`
is precisely a pretrained-model path, so the NC-SA term *does* apply to
it. And #124 measured madmom failing to build on Python 3.14 anyway.

**The in-house option, which should not be dismissed.** This repo already
has `chord_templates.py` (~360 templates, 30 qualities × 12 roots, with
bass-gated slash naming) and `chroma.fold()`/`fold_bass()`. Beat This!
(#127, MIT code *and* weights) already supplies a beat grid. A
**beat-synchronous chroma fed to the existing template matcher** is a
complete chord estimator built from parts already in this repository,
under this repository's own licence, at essentially zero marginal CPU
cost. Its expected accuracy is the classic-chroma-plus-templates tier —
which `oss-landscape-chord-multipitch.md` puts *below* NNLS-chroma's 80%,
so call it the ~70% tier, **unverified** — versus BTC's 81.59% MajMin.
That is a real gap, and BTC is the better default. But the in-house path
is the correct **no-extra-installed** tier for chords, exactly as today's
DSP path is for notes, and it costs nothing to have.

### 1.4 What a melody-and-chords target cannot represent

Stated plainly, because a reframing that quietly drops the user's actual
request is not a win:

- **The bass line.** A Real Book chart implies it through the chord
  symbol and the slash bass; it does not notate it. A bass player reading
  the chart invents a line. If the brief is "print off the bass part",
  a lead sheet does not deliver it.
- **Inner voicings, comping rhythm, riffs and hooks.** The four-note
  guitar figure that *is* the song is not a chord symbol. `Am7` says
  nothing about whether it was arpeggiated, strummed on the offbeats, or
  a single sustained pad.
- **Drums**, entirely — a lead sheet has no drum staff.
- **Anything polyphonic in the melody staff.** Double-stops, a harmonised
  chorus vocal, a two-guitar unison — a melody line is one voice by
  definition.
- **Instrumental sections with no melody instrument foregrounded.** The
  melody staff has nothing to carry during a drum break or a pad-only
  intro.
- **Chord quality beyond what estimation reaches.** Tetrads score 67.44%
  and the richer vocabularies (sevenths with bass, inversions) score
  **55–65%** per the MIREX history in `oss-landscape-chord-multipitch.md`
  — so exactly the extensions and slash chords that make a Real Book chart
  a Real Book chart are the part that is least reliable.

**And it does not remove the score layer problem.** #125 found no open
model emits barlines, meter, key or note values; #124 found no notated
band ground truth exists. A lead sheet needs *all* of those — a melody
line without note values and barlines is not a chart. Reframing to
melody-plus-chords narrows what has to be *transcribed*; it narrows
nothing about what has to be *notated*.

The right conclusion is the map's own: #140 and #141 already ticket two
output styles. **Melody-plus-chords is a second output mode, not a
replacement for the multi-track one** — and it is the mode whose
underlying tasks the field is best at. It should be built because it is
achievable, not because it makes the other number go away.

---

## 2. Dropping instrument identification: the crux, resolved

The ticket's hypothesis: naming the instrument costs ~12 points on
MulTTiPop (37.87 → 25.29) and ~10 on Slakh (84.56 → 74.84); separation
already tells us which stem is which; so instrument identity is free and
those points should be recoverable.

**The hypothesis is already spent, and the evidence is in the wheel.**

### 2.1 The two numbers are one model output scored twice

MulTTiPop's paper defines the instrument-agnostic figure as reducing "the
instrument space ... to harmonic (pitched) and percussive (unpitched)
instruments" (fetched here). Both columns of its benchmark table come from
**the same YourMT3+ inference run**; only the matching rule changes.
Nothing about the model, its decoding, or its compute differed between
25.29 and 37.87.

So **37.87 is the instrument-agnostic score**, and it is the number #124
and #129 already carry as the bar. There is no second, larger number
hiding behind a flag.

### 2.2 Instrument identity is structural in YourMT3, not a label

**Measured here.** Downloaded `mt3_infer-0.2.0-py3-none-any.whl`
(212,983 bytes) from PyPI and read the vendored YourMT3 sources:

- `mt3_infer/models/yourmt3/config/vocabulary.py` defines the program
  vocabularies (`GM_INSTR_CLASS`, `GM_INSTR_CLASS_PLUS`, `MT3_FULL`,
  `GM_DRUM_NOTES`, …). Its docstring states the two roles explicitly:
  as `train_vocab` a class maps to a program number the model is trained
  to emit; as `eval_vocab`, "**any program number in the instrument class
  is considered as correct**". The second is a *scoring* concession —
  which is precisely the mechanism §2.1 describes.
- `mt3_infer/adapters/vocab_utils.py` and the event codec show `program`
  as a first-class **event type in the output token stream**, alongside
  `shift`, `pitch` and `velocity`. A note is emitted *as* a
  program-plus-pitch pair.
- `mt3_infer/models/yourmt3/utils/task_manager.py` goes further for the
  `Multi` variant this repo would actually use: `num_decoding_channels`
  and `program2channel_vocab` mean **each instrument class gets its own
  decoder channel**. The checkpoint `mt3-infer` registers is
  `YPTF.MoE+Multi (noPS)` with `num_stems: 8`. Instrument identity is not
  a token you can ignore; it is which decoder wrote the note.

The one genuinely inference-time instrument lever that exists in the
codebase is `task.py`'s **subtask tokens** — `transcribe_singing`,
`transcribe_drum`, `transcribe_all`, with an `eval_subtask_prefix` that
can be set to `singing-only`. That is real instrument *conditioning*, and
it is the shape §2.4 says is the future — but it only applies to
checkpoints trained under the `singing_v1` / `singing_drum_v1` task
configs, and `mt3-infer`'s adapter defaults to `mt3_full_plus` with no
API to change it (`_resolve_task_name()` reads a `-tk` argument that is
not exposed through `api.py` or `cli.py`).

### 2.3 What `mt3-infer` actually exposes

**Measured here**, by grepping the whole wheel: the *only* instrument-
related option in the public API is `auto_filter` (default `True`), and
its docstring says what it is — "MT3-PyTorch has a known issue where it
incorrectly assigns drum sounds to melodic instruments ... automatically
detects and filters such cases". It is a **post-hoc heuristic over the
emitted MIDI file**, counting notes per program family, not an inference
mode.

**Conclusion for the ticket's question 2: no candidate exposes
instrument-agnostic inference, because for the leading candidates
instrument identity is not separable from decoding. What it recovers is
zero, because it was already recovered by the scoring convention that
produced 37.87.**

### 2.4 What *is* genuinely instrument-agnostic, and it is worth having

Two models actually decouple the two decisions, and both are permissive:

**`basic-pitch`** (Apache-2.0 code and weights, 16,782 parameters) emits
one undifferentiated stream of notes and no program at all. That is a
feature after separation and a disaster before it — the 2025 AMT Challenge
scored it **0.0634 F1**, last of everything evaluated, precisely because
its metric requires the program to match and basic-pitch emits none. That
number should never be quoted as basic-pitch being bad at transcription;
it is basic-pitch being scored on a task it declines to attempt. It is,
however, a sharp warning about #129's routing: **the moment a per-stem
note stream is merged into a multi-track score, this project supplies the
instrument label from the stem name, and every metric it reports must say
which convention it used.**

**`timbreAMT`** (`madderscientist/timbreAMT`, **Apache-2.0**, verified;
last push 2026-04-17; arXiv 2509.12712) is the more interesting find and
is new since #125. It is explicitly the architecture the ticket was
reaching for: a **two-branch** design that first transcribes notes with no
timbre information at all, then assigns instruments by *note-level
contrastive clustering* — the two decisions genuinely separated.

| Corpus | timbreAMT F1 | basic-pitch F1 | BPraw F1 |
|---|---|---|---|
| BACH10 Ensemble | .803 | .787 | .809 |
| BACH10 All | .861 | **.879** | .832 |
| PHENICX Ensemble | **.595** | .503 | .418 |
| URMP Ensemble | **.723** | .681 | .517 |
| URMP Solo | **.806** | .796 | .555 |

It wins on every *ensemble* set except BACH10-All, at **18,978 trainable
parameters** (26,620 excluding the learnable CQT), roughly **half
basic-pitch's 56,517** by the paper's own accounting, with **ONNX
exports** shipped and a browser deployment as proof they run cheaply.

And it supplies a second number directly relevant to §2.1: its own
**timbre-separated** results *degrade* to .563 F1 on a 2-instrument mix
and .407 on a 3-instrument mix, versus the .72–.80 its timbre-agnostic
branch reaches. That is an independent replication, from a completely
different architecture, of the same finding — **asking for instrument
identity is expensive, and the expense is in the instrument decision, not
the note decision**.

The direction of the field agrees. The 2025 AMT Challenge winner, MIROS,
is YourMT3+ with a "recurrent adapter that conditions the temporally
downsampled encoder outputs on **learned instrument group embeddings**",
one T5 decoder per instrument group — i.e. more instrument conditioning,
not less. It bought **0.5998 vs 0.5938**, a **0.0060** margin over
unmodified YourMT3+.

---

## 3. Per-stem versus whole-mix transcription (H1)

### 3.1 The finding is still "nobody has published this"

#126 searched for it and did not find it. This document searched again,
with different queries, and **also did not find it**. That sentence stands
as the finding. What turned up instead are three near misses, each worth
recording because each is one axis away from the experiment and none is a
substitute.

**Near miss 1 — drums, and the closest thing that exists.**
"Enhanced Automatic Drum Transcription via Drum Stem Source Separation"
(arXiv 2509.24853) puts **Demucs v4** in front of an ADTOF-class drum
transcriber and reports 8-class F-measure **0.84 MDB / 0.76 ENST /
0.56 RBMA** against ADTOF's **0.72 / 0.65 / 0.58**. Read carefully, this
is *not* a controlled A/B: fetched here, the paper's own framing is that
"Original ADTOF results are shown in parentheses" — the comparison values
are **quoted from the ADTOF paper, not re-run by these authors on mixed
audio**. So it is suggestive of a large per-stem gain on drums (+12/+10
points) and **it is not evidence of one**, and RBMA goes the other way
(−2), which the authors attribute to electronic drum timbres.

**Near miss 2 — chords, and the number is approximately zero.**
Mitoma & Furuya (APSIPA ASC 2025) run **exactly this shape** of pipeline
for chord recognition: HTDemucs into four stems, amplify the pitched ones,
remix, feed BTC. Over 485 real songs, WCSR triads went **75.52 → 75.72**;
root 82.51 → 82.72; maj-min 81.59 → 81.84; tetrads 67.44 → 67.59. That is
**+0.20 points**, statistically significant by a sign test over 1,136,742
frames (13,675 frames fixed, 11,390 frames broken) and musically
negligible. Their error analysis is instructive: amplifying the harmonic
stem made *isolated single notes* louder too, so an A major with a passing
D in the guitar riff started reading as A:sus4. **Separation moved
harmonic-content accuracy by nothing.**

**Near miss 3 — the cascade with no ablation.** arXiv 2412.06703 builds
separate-then-transcribe and, fetched here, reports **no with/without
comparison at all**; the authors themselves flag a mismatch (vocal
separator, piano-trained transcriber).

### 3.2 What this does to H1

#129 recorded H1 ("separation improves note accuracy") as unmeasured.
It is still unmeasured, but it is no longer *unevidenced*: the only
harmonic-task datapoint in existence says the gain is **+0.2 points**,
and the only percussive-task datapoint says something like **+10**, from a
non-controlled comparison. Those are consistent with a plausible
mechanism — separation removes the *masking* that hurts a broadband
percussive onset detector far more than it hurts a harmonic model that
was trained on mixtures anyway — but a mechanism is not a measurement.

**H1 should be sharpened, not answered.** Split it:

- **H1a — separation improves *pitched* note accuracy.** Weakly
  contra-indicated by the one adjacent number. Cheap to test.
- **H1b — separation improves *percussive* accuracy and timing.** Two
  supporting numbers (this one, plus #127's downbeat 0.699 → 0.775) and
  no contrary one.

### 3.3 The experiment, defined precisely

For #132's harness, on MulTTiPop's test split (403 segments, ~152 min,
CC BY 4.0), with `mir_eval.transcription.precision_recall_f1_overlap()`
at documented defaults (`onset_tolerance=0.05`, `pitch_tolerance=50.0`,
offsets disabled), reporting P, R and F1 separately:

**Arm A (baseline).** Transcribe the mix once. Score instrument-agnostic
(MulTTiPop's harm/perc reduction). Expected ≈ 37.87 for YourMT3+; this
project's own permissive stack will differ and that difference is itself a
finding.

**Arm B (per-stem).** `htdemucs` → 4 stems → transcribe each stem with the
same transcriber → concatenate all note events into one flat list →
**score with the identical instrument-agnostic rule as Arm A**. The union
must be scored, not the per-stem scores averaged; averaging rewards a stem
that produced nothing.

**Arm C (per-stem, instrument-aware).** As B, but each stem's notes carry
the label the stem name implies, scored under MulTTiPop's exact-instrument
rule. Expected ≈ 25.29 for the mix baseline; **the delta C − (mix,
exact) is the only place the "instrument identity is free after
separation" claim can actually be cashed**, and unlike §2's dead end this
one is real: the label genuinely does come free, it just does not make the
*notes* any better.

**Controls that must be held fixed**, or the result means nothing:
identical transcriber and weights across arms; identical audio
normalisation (the APSIPA paper used ReplayGain; Demucs output is not
loudness-matched to its input); identical `mir_eval` parameters; and the
same `--overlap` for every Demucs run, since #126 identified overlap
rather than model choice as the speed dial.

**Cost.** MulTTiPop test is ~152 min of audio. Demucs at the 2.1× real
time measured in #126 is **≈5.3 hours of wall clock for Arm B's separation
alone**, once, cacheable per the map's standing decision. That is an
overnight run, not an interactive one, and it is the single most valuable
overnight run available to this map.

---

## 4. Specialist per-instrument models

The ticket flags this as the least-explored and possibly biggest angle.
It is the least explored. It is not the biggest, and the reason is
uncomfortable: **the specialists that beat the generalists mostly do not
release weights, and the ones that release weights mostly do not beat the
generalists.**

### 4.1 The survey

| Instrument | System | Best number (corpus) | Code licence | Weights | Py3.14 / CPU |
|---|---|---|---|---|---|
| **Piano** | **transkun v2** | **0.9505 onset F1** (MAESTRO) | **MIT** | **MIT, in the wheel** | CPU by default; already #129's choice |
| Piano | ByteDance | 0.9672 onset (MAESTRO) | Apache-2.0 | **not stated** | py3.7-era declaration |
| Piano | hFT-Transformer | SOTA ISMIR 2023 (MAPS/MAESTRO) | MIT | **not stated** | — |
| **Vocals** | Mel-RoFormer | **COnP 0.798 / COnPOff 0.625** (MIR-ST500, real pop) | paper CC BY 4.0 | **not released** | — |
| Vocals | T3MS | note-level F1 0.610 (ST500) | — | **not released** | — |
| Vocals | ROSVOT | not extracted — **unverified** | **MIT** | provided per README | **unverified** |
| Vocals | STARS | not extracted — **unverified** | **MIT** | — | pushed 2026-08 |
| Vocals | VOCANO | not extracted — **unverified** | **MIT** | — | 2021 |
| **Guitar** | *generalist* **YourMT3+** | **91.65** (GuitarSet) | GPL-3.0 (refused) | Apache-2.0 | — |
| Guitar | GAPS benchmark CRNN | 88.1 zero-shot GuitarSet; 94.3 GAPS | — | **authors "carefully considering" release** | — |
| Guitar | TART | 0.838 F50 GuitarSet; 0.779 EGDB | — | **not released; "ongoing work"** | — |
| Guitar | CRNN (trimplexx) | 0.8736 MPE F1 GuitarSet | **no LICENSE file** | — | — |
| Guitar | FretNet | continuous-pitch tablature | **MIT** | — | 2023 |
| **Bass** | — | **nothing found** | — | — | — |
| **Drums** | ADTOF | 0.78–0.79 MDB 5-class | **CC BY-NC-SA 4.0** | NC-SA | evaluation-only per #139 |
| Drums | STAR Drums | 0.79 MDB 5-class, 0.67 18-class | dataset+code released | **CC BY 4.0 data, no pretrained weights** | training required |
| Drums | N2N | **89.68 E-GMD / 87.86 MDB / 94.90 IDMT** | — | **no code or weights** | — |
| **Any pitched stem** | **timbreAMT** | .723 URMP ens. / .595 PHENICX ens. | **Apache-2.0** | ONNX exported | ~19–27 k params, browser-deployable |
| Any pitched stem | basic-pitch | .681 URMP ens. / .503 PHENICX ens. | Apache-2.0 | Apache-2.0, 230 KB | vendored per #129 |

### 4.2 What the table says

**The one head-to-head goes to the generalist.** GuitarSet is the only
corpus where a generalist and specialists both publish. YourMT3+ scores
**91.65**; TART 0.838; GAPS 88.1 zero-shot / 91.2 fine-tuned on
GuitarSet's own train split; the CRNN 0.8736 on a *frame-level* MPE
metric, which is not the same metric. So the specialists do not clear the
generalist even before the licence filter is applied, and the closest one
(GAPS at 91.2) got there by training on the test corpus's own split.

**Bass — the ticket's specific example — has nothing.** Repeated searches
surfaced jazz walking-bass work from 2017–2021 and the FiloBass corpus
(#124 already records it as bass-only transcription ground truth), but no
released, benchmarked, modern electric-bass transcription model. Bass is
also the stem where a generalist should be *least* bad — monophonic,
low-register, harmonically simple — and where this repo's own existing
monophonic YIN path plus a non-causal duration pass is least out of its
depth. **Unverified**, but it is the cheapest hypothesis on this list to
test, because the machinery already exists in this repository.

**Vocals is the one place a specialist genuinely could win**, because a
vocals stem is close to monophonic and the specialist corpus (MIR-ST500)
is real pop. The blocker is release status, not accuracy: the two best
(Mel-RoFormer, T3MS) publish nothing. The three MIT ones (ROSVOT, STARS,
VOCANO) are unmeasured here and should be, in that order — STARS first
because it is the only one still being pushed to (2026-08-18) and it
bundles alignment, which is what the score layer needs.

**Drums is settled and #127 settled it**: as an *oracle* no ADT model is
needed at all; as *notation* the only permissive route is training on STAR
Drums, which is a project, not an adoption.

### 4.3 The structural argument against a bank of specialists

Even if every specialist existed and were permissive, a per-instrument
model bank has costs the ticket's framing does not price:

- **Every model is another set of weights to download, another install
  path to fail, another licence to check.** "Easy install for anyone, not
  just computer geeks" is a stated product goal, and it argues for the
  *smallest* number of models that clears the bar, not the best model per
  stem.
- **Specialists are trained on clean solo recordings of their instrument**
  — GuitarSet is hexaphonic-pickup solo guitar, MIR-ST500 is vocal-line
  annotation, MAESTRO is a Disklavier. A Demucs stem is none of those: it
  is a separated, artefact-carrying, bleed-carrying approximation. arXiv
  2512.14602 measures a **20-point F1 drop from recording conditions alone
  and 14 points from genre shift** across state-of-the-art AMT systems.
  A specialist's advantage is exactly the kind of thing that domain shift
  eats first, and **no specialist in the table above has been evaluated on
  a separated stem by anybody**.
- **#129's seam already makes this a config question.** `NoteTranscriber`
  is a Protocol; routing a stem to a different adapter is one dictionary
  entry. So none of this needs deciding now — it needs *measuring*, which
  is what the seam was for.

---

## 5. 2026 SOTA sweep

Newer than #125's survey, and specifically things evaluated on real audio:

- **MIROS** (2025 AMT Challenge winner, 0.5998 F1) — YourMT3+ framework
  with **MusicFM**, a conformer self-supervised foundation model
  pretrained with BEST-RQ, plus a recurrent adapter conditioning on
  learned instrument-group embeddings and per-group T5 decoders with
  rotary embeddings and FlashAttention. Reported at **0.83
  multi-instrument F-measure on Slakh2100**. Beat unmodified YourMT3+ by
  0.0060. **No open release located — unverified.** The interesting part
  is the recipe: a *music foundation model encoder* is where the field's
  headroom currently is.
- **MuScriptor** (arXiv 2607.08168, MIT code, **CC BY-NC 4.0 gated
  weights**) — the only model trained on 11,000+ hours of *real* audio,
  and #129 already records it as expert-only for that reason. Fetching its
  README here confirms it exposes **no instrument-agnostic or
  instrument-conditioned inference option** — the CLI is
  `muscriptor transcribe file.wav` with no instrument flags at all.
- **timbreAMT** (arXiv 2509.12712, Apache-2.0) — §2.4. New, tiny,
  permissive, instrument-agnostic by construction, ONNX. The single most
  actionable new find in this document.
- **PF2N** (Periodicity–Frequency Fusion Network, Mathematics 2025) —
  reports consistent gains as a *component* dropped into existing SOTA
  models on Slakh2100/MusicNet/MAESTRO. A module, not a system; **no
  release checked — unverified**.
- **MDS / "Sound and Music Biases in Deep Music Transcription Models"**
  (arXiv 2512.14602; Springer JASMP 2025) — the most decision-relevant new
  paper for this map that is not about a model at all. It measures
  **20 percentage points of F1 lost to recording conditions** and
  **14 points to genre shift**, and finds dynamics estimation more fragile
  than onset prediction. This is the quantified version of why Slakh
  numbers do not transfer, and it should be cited whenever a synthetic
  figure is quoted in this project's voice.
- **"A Dual Evaluation for Music Transcription"** (arXiv 2608.04511) —
  already in #124; restated here because §7 leans on it: it states that
  **note-level F1 metrics are appropriate only for MIDI-like outputs, not
  sheet music**, validated against 3,180 human ABX judgements.

Nothing found beats MulTTiPop's 37.87 on real band audio. **The bar has
not moved since #124 set it.**

---

## 6. Ensembling (H3)

**No evidence that it earns its cost for note transcription.**

- The 2025 AMT Challenge is the natural place for an ensemble to appear —
  a held-out leaderboard where teams optimise for one number and compute
  is not the constraint. **21 teams registered, 14 submitted, 8 valid,
  and the paper describes no ensemble submission at all** (fetched here).
  If ensembling were a reliable win, a challenge is where it would show.
- The margin an ensemble would have to beat is tiny anyway: rank 1 to
  rank 2 is **0.5998 vs 0.5938**, and rank 2 to rank 3 is 0.5938 vs
  0.5581. Meanwhile the gap to MulTTiPop's real-audio 37.87 is ~22 points.
  **Ensembling is optimising the wrong axis by an order of magnitude.**
- The one paper found that *combines* models —
  "Separate-and-Detect" (arXiv 2608.01093) — is a **jointly trained**
  separation-plus-transcription system, not a post-hoc reconciliation of
  independent outputs. #126 already recorded that a jointly trained system
  does not transfer to a cascade.
- There is a real argument *against* it that §7 sharpens: an ensemble by
  union raises recall and lowers precision, and this product wants the
  opposite. An ensemble by **intersection** — keep only notes every model
  agrees on — would raise precision, and is the one ensembling variant
  worth a line in the harness, precisely because it is the cheap version
  of "tune toward precision". It is also the one variant nobody publishes,
  because it scores worse on F1.

**Recommendation: leave H3 rejected-by-default.** Spend the same effort on
Arm B/C of §3.3, which addresses a 22-point gap rather than a 0.6-point
one.

---

## 7. An editing-effort metric for #132

The ticket's observation is correct and is supported by the literature:
F1 weights a missed note and a hallucinated note equally, and for a
human-corrected output they are not equal. YourMT3+ on MulTTiPop already
sits at **P 43.13 / R 36.65** (harm/perc) — more precise than complete —
and a model tuned further toward precision would score *worse* on F1 and
*better* here.

### 7.1 What the literature already says

- **`musicdiff`'s OMR-NED is already an editing-effort metric.** #124
  adopted it as Tier 1 for the notation layer. A normalised edit distance
  over notation symbols is, definitionally, "how many operations to turn
  the output into the reference" — which is the thing being asked for.
  Half of #132's answer is already chosen; it just was not framed this way.
- **The OMR literature frames it explicitly.** Symbol Error Rate is
  standard there precisely because it "reflects the manual correction
  effort required by users". Cogliati & Duan (ISMIR 2017) counted errors
  over notes, durations, rests, **barlines**, staff assignment and ties —
  the earliest metric in #124's survey to score barlines, and an edit
  count by construction (**no public implementation found**, per #124).
- **Error salience is asymmetric and has been measured.** Ycart, Liu,
  Benetos & Plumbley (TISMIR 2020, 4,501 ratings from 186 listeners over
  1,552 excerpts) found standard F-measure agreed with listeners 89–91%
  on easy comparisons but **disagreed nearly 40% of the time when two
  transcriptions were within 10% F1** — i.e. exactly in the regime where
  this project would be choosing between backends. Their features include
  **out-of-key false positives** and **loudness of false negatives** as
  separate terms, and their headline finding is that *rhythm* descriptors
  mattered most.
- **arXiv 2608.04511** states outright that note-level F1 is appropriate
  only for MIDI-like outputs, not sheet music.

### 7.2 The recommendation

**Do not report one number. Report a triple, and never average it.**

**(1) Headline, comparable: F0.5.** The van Rijsbergen Fβ with β=0.5
weights precision twice as heavily as recall. It is one line of arithmetic
over the P and R `mir_eval` already returns, it stays legible to anyone
who knows F1, and it is directly comparable across backends. β=0.5 is a
**stated editorial choice, not a measurement** — the honest statement is
"this project holds a hallucinated note to be about twice as expensive as
a missed one", and if a real editing session says otherwise, β moves.
Report P and R beside it always, so nobody has to reverse-engineer them.

**(2) The two error classes, per bar, separately and never merged.**

- **Ghost rate** — false positives per bar. The expensive class, because
  a wrong note must first be *found*, which means reading every bar of
  every track against the recording.
- **Miss rate** — false negatives per bar. The cheap class: the editor
  already places notes at the cursor in one keystroke, and a missing note
  is discovered by the same listening pass that would have happened
  anyway.

Per **bar**, not per note, because that is the unit a human scans. A
2-notes-per-bar ballad and a 16-notes-per-bar riff with the same F1 are
not the same amount of work.

**(3) Editor operations to correct — the metric only this project can
compute.** `score_editor_display.py` already exposes a closed, countable
set of mutations: `note_toggle`, `transpose_note_at_cursor`,
`cycle_duration`, `clear_to_rest`, `insert_column_at`, `delete_column_at`.
Given a transcribed `EditorScore` and a reference `EditorScore`, the
minimum number of those operations that transforms one into the other is a
literal, unarguable measure of editing effort, in the units the user
actually spends. It is an alignment plus an edit distance over this repo's
own action vocabulary, and it prices things F1 cannot see: **a note at the
right pitch with the wrong duration is one `cycle_duration` press, while a
note at the wrong pitch entirely is a delete plus a place**. It also
naturally scores the score layer — a wrong time signature is one
operation, not a hundred wrong barlines.

This is the metric worth building, and it is small: the alignment is the
same shape as `musicdiff`'s, and the reference side is `EditorScore`,
which `score_editor_state.load_score()` already produces from MusicXML.

**(4) One weighting refinement, once the basics work.** Ycart et al. found
out-of-key false positives are far more noticeable than in-harmony ones.
For *listening* that means they hurt more. For **editing** the sign flips:
an out-of-key ghost is easy to *spot* and therefore cheap, while a ghost
that sits comfortably inside the chord is the one that survives review and
ends up in the printed part. So the search-cost weight should be **higher
for diatonic, in-chord false positives** — the opposite of the perceptual
weighting, for a well-understood reason. Recorded as a hypothesis, not a
formula; it needs one real editing session to calibrate, and #132 should
not block on it.

**(5) What to drop.** Do not report a headline MUSTER `MeanER` as
"notation accuracy" — #124 already established via arXiv 2608.04511 that
it correlates with *playback* preference (ρ=0.79) far more than with
notation similarity (ρ=0.38).

### 7.3 The consequence for model selection

Once F0.5 and ghost-rate are the objective, several choices change sign:

- A **higher decoding threshold** is free accuracy by this metric and a
  loss by F1. Every model in #125's table has one.
- **Ensembling by intersection** becomes the interesting variant (§6).
- **basic-pitch's and timbreAMT's** precision/recall balance becomes a
  first-class selection criterion rather than a footnote.
- And **MIROS vs. YourMT3+** stops being a 0.006 F1 question and becomes a
  P 0.6558 vs 0.6010 question — a 5.5-point precision difference that F1
  almost entirely hid.

---

## 8. Recommendation

**1. Close out the instrument-identity hypothesis. It is spent.** §2 is
the answer to the ticket's biggest apparent lever, and the answer is that
the 12 points were already collected. Record it in `docs/DECISIONS.md` so
it is not re-opened: *instrument-agnostic scoring is a metric convention,
not an inference mode; 37.87 is the instrument-agnostic number.*

**2. Add a chord estimator to the stack, and make BTC the default.**
MIT code with MIT-repo weights, independently re-run on 485 real pop/rock
songs at 81.59% MajMin / 75.52% triads, ~12 MB, no account, no terms
prompt. It is the only permissive chord model found that clears the field's
plateau, and #141's Real Book output currently has **nothing** behind it.
Second tier, no-extra-installed: this repo's own `chord_templates.py`
against a Beat This!-synchronous chroma. Explicitly refused: Chordino
(GPL-2.0), `autochord` (transitively GPL-2.0), madmom Deep Chroma (NC-SA
models, and does not build on 3.14).

**3. Add `timbreAMT` as a measured alternative to `nmp.onnx`, not a
replacement.** Same licence tier (Apache-2.0), same size class, ONNX
already exported, and it beats basic-pitch on every ensemble benchmark it
reports. #129's seam exists precisely so this is one adapter and an A/B.

**4. Build the melody-plus-chords output as a second mode, and be honest
about what it drops.** §1.4 lists what it cannot represent — bass lines,
comping, riffs, drums, inner voices. It is the achievable mode, not the
complete one, and #141 should carry that list verbatim.

**5. Run §3.3's three-arm experiment before anything else in #132.** It is
one overnight Demucs pass plus three cheap scoring runs, and it settles
H1a/H1b, which currently rest on a +0.2-point chord result and a
non-controlled drum result.

**6. Adopt §7's metric triple in #132, and set the accuracy bar in those
units.** F0.5 headline with P and R beside it; ghost and miss rates per
bar, separately; editor-operation count as the project-specific ground
truth. Not F1.

**7. Do not build a bank of specialist models.** The one head-to-head goes
to the generalist, the best specialists ship no weights, none has ever
been evaluated on a separated stem, and the install cost fights a stated
product goal. The two exceptions worth *measuring*, cheaply, are:
**STARS/ROSVOT (both MIT) on the vocals stem**, and **this repo's own
existing monophonic pipeline on the bass stem**, where no specialist model
exists at all and the material is closest to what that pipeline was
designed for.

**8. Do not ensemble.** §6.

### 8.1 Does any of this change #129?

**Nothing reverses.** #129's architecture — `virtualnote convert`, refuse
rather than degrade, ask the input mode, `transcribe_backends.py` as the
seam, transkun for piano, Beat This! for timing, permissive-by-default —
is unaffected and is what made these findings actionable.

**Three additions:**

- **A fourth Protocol.** `ChordEstimator.estimate(audio, sr, beats) ->
  list[ChordSpan]`, with `BTCEstimator` and a `TemplateChordEstimator`
  built on this repo's existing `chroma.py`/`chord_templates.py` behind it.
  #129's model-routing table has no row for chords, and #141 needs one.
- **A second `NoteTranscriber` contender.** `TimbreAMTBackend` alongside
  `BasicPitchBackend`, both Apache-2.0, decided by #132 rather than by
  this document.
- **One deflation.** Any implicit expectation that instrument-agnostic
  inference would lift band-mix accuracy should be struck; §2 settles it.

---

## 9. What this document does not settle

- **Whether melody-plus-chords is actually good enough.** §1 establishes
  that its component tasks score far better on real audio than
  multi-instrument note transcription does. It does not establish that
  chaining a separator, a vocal transcriber, a chord estimator, a beat
  tracker and a notation layer yields a *usable chart*, because errors
  compound and nobody has published the end-to-end number.
- **Whether BTC runs acceptably on this CPU.** No published figure, none
  measured here.
- **Whether any melody transcriber survives a Demucs vocals stem.** Every
  MIR-ST500 number in §1.1 is on the corpus's own audio, not on separated
  stems.
- **Whether F0.5 with β=0.5 is the right weight.** It is a stated
  editorial choice awaiting one real editing session.
- **The score layer.** Untouched here, and still the sharpest open
  question on the map — no model emits it (#125), no band ground truth
  exists for it (#124), and reframing to a lead sheet does not remove the
  need for it (§1.4).
- **Whether this project should quote WCSR at all.** It is the chord
  literature's metric and this project's harness is built around
  `mir_eval` and `musicdiff`. A conversion convention is needed and is not
  proposed here.

## 10. Unverified / open

- **No model was installed and none was run.** The only things executed
  were: downloading and unpacking the `mt3_infer` 0.2.0 wheel and reading
  its sources; GitHub REST API licence/contents queries; PyPI JSON
  metadata queries; and `pdftotext` over one fetched PDF. No accuracy
  figure in this document was reproduced.
- **BTC's weights carry no separate licence statement.** The repository's
  single MIT LICENSE is the only grant present, and the `.pt` files are
  committed inside it. That is the favourable reading of an absence. #139's
  rule for an *absent* licence is "forbidden for anything shipped" — but
  here the licence is not absent, it is repository-wide, and the weights
  are inside the repository. Worth one confirming question upstream before
  shipping, on the same reversible footing #129 gave YourMT3.
- **BTC's CPU cost is unpublished and unmeasured.** So is transkun's,
  `timbreAMT`'s, `nmp.onnx`'s under `onnxruntime` 1.29, and every vocal
  model's. #125 flagged this gap; it is still the largest one.
- **BTC's Python 3.14 viability is unverified.** No PyPI package exists;
  the dependency list is plain (`torch`, `numpy`, `pandas`, `librosa`,
  `pyyaml`, `mir_eval`, `pretty_midi`) but `pandas`-on-3.14 and the
  vendoring effort were not checked.
- **ROSVOT's, STARS' and VOCANO's accuracy numbers were not extracted.**
  Their MIT licences were verified via the GitHub API; nothing else about
  them was.
- **arXiv 2509.24853's ADTOF comparison is explicitly not a controlled
  baseline** — the authors state the parenthesised figures are quoted from
  the ADTOF paper. Treated throughout as suggestive only.
- **The 2025 AMT Challenge's per-system runtimes** are reported as
  "12.60–22.05 ms" and basic-pitch at "3.91 ms" per piece, on ~20-second
  excerpts, with **no hardware stated**. The units as stated are not
  physically plausible for a 45.8M-parameter encoder-decoder on 20 s of
  audio; they are quoted here as published and should not be used for any
  cost estimate. **Unverified.**
- **basic-pitch's 0.0634 F1** in that challenge is under a metric that
  requires an instrument program match, which basic-pitch does not emit.
  It is quoted as an illustration of metric mismatch, not of transcription
  quality.
- **`timbreAMT`'s table was extracted from the arXiv HTML** and mixes
  frame-level and note-level reporting in places; the paper "reports
  frame-level metrics combining note and onset predictions but doesn't
  isolate onset F1 separately". So its comparison with basic-pitch is
  **not** on the same footing as MulTTiPop's onset-F1 numbers, and the two
  tables must not be merged.
- **MIROS's 0.83 Slakh2100 figure** came from a secondary summary of the
  challenge paper, not from the paper's own table as fetched.
  **Unverified.**
- **Nobody has evaluated any transcriber on a Demucs stem**, for any
  instrument, anywhere this search reached. Every per-instrument number in
  §4.1 is on that instrument's own clean corpus.
- **No bass transcription model was found at all.** Absence of a search
  result is not proof of absence.
- **`mt3-infer`'s subtask-token path was read, not run.** Whether the
  registered `YPTF.MoE+Multi (noPS)` checkpoint was trained under a task
  config that would honour `transcribe_singing` was not determined; the
  adapter defaults to `mt3_full_plus` and does not expose `-tk`.
- **`autochord`'s NNLS-Chroma dependency was read from its README**, not
  from its source; whether the dependency is load-bearing at inference or
  only at training was not confirmed. Treated as load-bearing, the more
  restrictive reading, per #139.
- The APSIPA 2025 chord paper's `+0.20` gain is on a **specific**
  amplification setting selected by a preliminary experiment on the same
  data, so it is an optimistic estimate of what separation buys chord
  recognition, not a conservative one.

## Sources

Primary sources, fetched September 2026.

**Instrument identity and the vocabulary question.** PyPI JSON for
`mt3-infer` 0.2.0 (2026-07-11, MIT); the wheel
`mt3_infer-0.2.0-py3-none-any.whl` (212,983 B) downloaded and unpacked
here — `mt3_infer/models/yourmt3/config/vocabulary.py`,
`config/task.py`, `utils/task_manager.py`, `adapters/vocab_utils.py`,
`adapters/yourmt3.py`, `adapters/mt3_pytorch.py`, `config/checkpoints.yaml`,
`api.py`, `cli.py`, `dist-info/licenses/LICENSE`.
`github.com/mimbres/YourMT3` (repo licence GPL-3.0, GitHub API).
arXiv 2407.04822 (YourMT3+ per-dataset table, via #124/#125).
arXiv 2607.08756 (MulTTiPop; harm/perc definition, P/R values, fetched).

**Task reframing — melody.** arXiv 2409.04702 (Mel-RoFormer: MIR-ST500
COn 0.819 / COnP 0.798 / COnPOff 0.625; SpecTNT 8.4M params COnPOff 0.550;
MERT 324M COnPOff 0.530; paper CC BY 4.0). arXiv 2502.12438 (T3MS:
ST500 note-level F1 0.610, onset 0.806, offset 0.759, onset+pitch 0.771;
MusicYOLO-I 0.586, Note-level Transformer 0.591, CE+CTC 0.574; no release).
arXiv 2405.09940 / `github.com/RickyL-2000/ROSVOT` (MIT, GitHub API).
`github.com/gwx314/STARS` (MIT, pushed 2026-08-18, GitHub API).
`github.com/B05901022/VOCANO` (MIT, GitHub API); ISMIR 2021 paper
`archives.ismir.net/ismir2021/paper/000036.pdf`.
PyPI JSON for `crepe` 0.0.16, `torchcrepe` 0.0.24, `penn` 1.0.0 (all MIT).

**Task reframing — chords.** `github.com/jayg996/BTC-ISMIR19` — LICENSE
(MIT, `Copyright (c) 2019 Jonggwon Park`, fetched verbatim), README
(dependency list, `test.py` CLI, `--voca` large-vocabulary flag), and the
GitHub contents API listing showing `test/btc_model.pt` (12,154,754 B) and
`test/btc_model_large_voca.pt` (12,229,576 B). arXiv 1907.02698 (BTC,
ISMIR 2019). Mitoma & Furuya, "Accuracy Improvement of Automatic Chord
Recognition with Source Separation Preprocessing", APSIPA ASC 2025,
`apsipa.org/proceedings/2025/papers/APSIPA2025_P307.pdf` (fetched and text-
extracted here: 485 songs, 170-chord large vocabulary, WCSR via `mir_eval`,
Table I/II/III, HTDemucs, sign test over 1,136,742 frames).
`github.com/c4dm/nnls-chroma` (**GPL-2.0**, GitHub API).
`github.com/cjbayron/autochord` (Apache-2.0, GitHub API; README's
NNLS-Chroma Vamp dependency; 67.33% test accuracy); PyPI JSON for
`autochord` 0.1.4 (2021-10-07, `tensorflow>=2.6`, `vamp`, `gdown`).
`madmom.features.chords` docs. MIREX 2020 ACE results and Mauch & Dixon
ISMIR 2010, both via `oss-landscape-chord-multipitch.md`.

**Per-stem vs. whole mix.** arXiv 2509.24853 (drum-stem ADT: Demucs v4,
8-class F 0.84 MDB / 0.76 ENST / 0.56 RBMA vs ADTOF 0.72 / 0.65 / 0.58;
"Original ADTOF results are shown in parentheses"). arXiv 2412.06703
(`github.com/Lucas-Dunker/Stem-Separator-AMT`; no ablation).
arXiv 2608.01093 (Separate-and-Detect, jointly trained). arXiv 2605.06685
(`--piano-solo` bypass, via #126).

**Specialist models.** `github.com/CWitkowitz/guitar-transcription-continuous`
(FretNet, MIT, GitHub API). arXiv 2510.02597 (TART: GuitarSet F50 0.838,
EGDB 0.779; "ongoing work", no release). arXiv 2408.08653 (GAPS: 88.1
zero-shot GuitarSet, 91.2 GAPS+GuitarSet, 84.8 FrançoisLeduc, 94.3 GAPS;
CRNN over log-mel; authors "carefully considering whether to make our
model weights freely available"). `github.com/trimplexx/music-transcription`
(0.8736 MPE F1 GuitarSet; **no LICENSE file**, GitHub API).
arXiv 2311.02023 (FiloBass, via #124). ADTOF / STAR Drums / N2N figures
carried from `rhythm-meter-and-drum-transcription.md` (#127).
`github.com/Yujia-Yan/Transkun` (MIT, GitHub API).

**Instrument-agnostic models.** arXiv 2509.12712 and
`github.com/madderscientist/timbreAMT` (**Apache-2.0**, GitHub API, pushed
2026-04-17): two-branch architecture, Table 3 (BACH10 Ensemble .803 /
basic-pitch .787 / BPraw .809; BACH10 All .861/.879/.832; PHENICX Ensemble
.595/.503/.418; URMP Ensemble .723/.681/.517; URMP Solo .806/.796/.555),
Table 4 (timbre-separated 2-instrument .563, 3-instrument .407),
18,978 trainable parameters / 26,620 excluding CQT vs basic-pitch's 56,517,
ONNX export, `madderscientist.github.io/noteDigger/`.
`github.com/spotify/basic-pitch` (Apache-2.0, GitHub API); PyPI JSON for
`basic-pitch` 0.4.0.

**2026 sweep.** arXiv 2603.27528 (2025 AMT Challenge, HTML: MIROS's
MusicFM/BEST-RQ encoder, recurrent adapter over learned instrument-group
embeddings, per-group T5 decoders; 0.5998 P 0.6558 R 0.5724 vs
YourMT3-YPTF-MoE-M 0.5938 P 0.6010 R 0.5888; basic-pitch 0.0634;
"instrument leakage, hallucinating nonexistent instruments"; F-measure
falling 0.28 from solo to three instruments; 21 teams / 14 submissions /
8 valid; **no ensemble submission described**). arXiv 2607.08168 and
`github.com/muscriptor/muscriptor` (MIT code, **CC BY-NC 4.0 weights**,
103M/307M/1.4B, "small is the practical choice on CPU-only machines",
no instrument flags in the CLI). arXiv 2512.14602 / Springer JASMP
`link.springer.com/article/10.1186/s13636-025-00428-z` (MDS corpus:
20-point F1 drop from recording conditions, 14 from genre; dynamics more
fragile than onsets). `mdpi.com/2227-7390/13/11/1708` (PF2N).

**Metrics.** Ycart, Liu, Benetos & Plumbley, "Investigating the Perceptual
Validity of Evaluation Metrics for Automatic Piano Music Transcription",
TISMIR 2020, `transactions.ismir.net/articles/10.5334/tismir.57`
(4,501 ratings, 186 participants, 1,552 excerpts; 89–91% agreement on easy
comparisons, ~40% disagreement within 10% F1; out-of-key false positives
and false-negative loudness as features; final metric 89.1% vs 88%;
rhythm descriptors most important). arXiv 2608.04511 ("A Dual Evaluation
for Music Transcription": note-level F1 appropriate only for MIDI-like
outputs; MUSTER MeanER ρ=0.79 playback vs ρ=0.38 notation) — via #124.
Cogliati & Duan, ISMIR 2017,
`archives.ismir.net/ismir2017/paper/000131.pdf`. McLeod & Steedman, MV2H,
ISMIR 2018 — via #124. `musicdiff` OMR-NED — via #124.
arXiv 2311.04091 (Reading Music Systems workshop proceedings: Symbol Error
Rate as a proxy for manual correction effort).

In-repo cross-references:
`docs/research/pretrained-transcription-weights.md`,
`docs/research/eval-harnesses-and-corpora.md`,
`docs/research/oss-landscape-chord-multipitch.md`,
`docs/research/cpu-source-separation.md`,
`docs/research/rhythm-meter-and-drum-transcription.md`,
`docs/DECISIONS.md` ("MIT, and the rules for third-party models and
weights (issue #139)"; "Transcription stack and pipeline shape (issue
#129)"), map [#123](https://github.com/pellepang/note-color/issues/123),
tickets [#124](https://github.com/pellepang/note-color/issues/124),
[#125](https://github.com/pellepang/note-color/issues/125),
[#126](https://github.com/pellepang/note-color/issues/126),
[#127](https://github.com/pellepang/note-color/issues/127),
[#129](https://github.com/pellepang/note-color/issues/129),
[#132](https://github.com/pellepang/note-color/issues/132),
[#139](https://github.com/pellepang/note-color/issues/139),
[#141](https://github.com/pellepang/note-color/issues/141),
[#142](https://github.com/pellepang/note-color/issues/142).
