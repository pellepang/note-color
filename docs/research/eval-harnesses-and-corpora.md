# Evaluation harnesses, aligned corpora and transcription metrics: what we can take off the shelf

Research for issue
[#124](https://github.com/pellepang/note-color/issues/124), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"). The map's standing decision is
**evaluation before algorithm**: the harness, corpora and metrics come
first, and every accuracy claim carries numbers plus an error analysis.
This document answers what that harness can be *built out of*.

The sibling research
[`docs/research/pretrained-transcription-weights.md`](pretrained-transcription-weights.md)
(issue [#125](https://github.com/pellepang/note-color/issues/125)) was read
in full first and shapes what this one looks for. It established two
things that decide the evaluation design:

1. **Open multi-instrument transcription tops out around 0.60 note-onset
   F1** — and that figure is on *synthesised* three-instrument audio, with
   no published real-band-mix number at all. So the harness's first job is
   to produce that missing number.
2. **No open model emits the score layer** — barlines, meter, key, note
   values. So a metric that only scores note events is blind to most of
   what this converter is being judged on, and the harness needs a second,
   notation-level metric alongside the first.

Everything below is sourced. Where a claim could not be verified from a
primary source it is marked **unverified** in place. Unlike #125, a
handful of things here *were* actually executed against this repo's own
Python 3.14 venv (`pip install --dry-run --report`), and those are marked
as **measured here**.

## Questions

1. Which aligned corpora genuinely contain **multi-instrument band
   material** — the map's target input — rather than solo piano or
   classical chamber?
2. How is a transcription scored against a human-written score that has
   **no timestamps**? Is there a ready-made tool, or is this ad hoc?
3. Which metrics score **notation** (barlines, meter, note values, key,
   voicing) rather than just note events?
4. What is cloneable/installable **today on Python 3.14**, and under what
   licence?
5. What do published benchmarks say current systems actually achieve, so
   this project has a realistic bar rather than an invented one?

## 1. The corpora, and the blunt answer about band material

### 1.1 The honest summary first

**They mostly do not.** The corpora with excellent note-level ground truth
are solo piano or classical chamber. The corpora with genuine full-band
instrumentation and note-level ground truth are **synthesised**, not
recorded. The corpora with real recorded band audio (MedleyDB, MUSDB18,
RWC-Popular) have either *no* note-level ground truth or only a melody
line / beat-chord layer.

Exactly **one** corpus sits in the intersection the map needs — real
commercial band recordings with aligned multitrack note-level MIDI — and
it was built in 2026 precisely because that intersection was empty. It is
3.5 hours long, is explicitly evaluation-only, and its own paper reports
that the best current model reaches **37.9% onset F1** on it.

### 1.2 The one that matters: MulTTiPop

**MulTTiPop** (Multitrack Transcription Dataset for Pop Music, arXiv
2607.08756, published 9 July 2026; project page `gclef-cmu.org/multtipop`;
data at `huggingface.co/datasets/gclef-cmu/multtipop`).

- **572 segments, 3.5 hours**, from 374 unique songs by 263 artists,
  released 1939–2009 (concentrated 1970s–2000s), spanning 101 genres by
  Every Noise at Once lookup — rock, New Wave, Motown. For contrast the
  paper notes RWC-Popular covers 2 genres.
- **Real commercial recordings.** Aligned multitrack MIDI from the Lakh
  MIDI and TheoryTab datasets, matched by metadata.
- **Splits:** dev 169 segments (~61 min), test 403 (~152 min), stratified
  by artist. There is deliberately **no train split**.
- **Licence: CC BY 4.0** (the aligned MIDI is adapted from Lakh, also
  CC BY 4.0).
- **Obtained** from Hugging Face. **The audio is not distributed** — the
  release is aligned MIDI plus metadata carrying YouTube video IDs and
  in-video timestamps; the user fetches the segments themselves. The
  authors state researchers "should only use MulTTiPop for model
  evaluation, not training."
- **Alignment method**, which matters for how much to trust it: madmom
  RNN beat tracking on the audio, tempo halving/doubling correction, an
  anchor beat linking the first audio beat to the *k*-th MIDI beat, then
  linear interpolation warping with the detected beats as control points.
  Candidate anchors came from chroma similarity + onset correlation,
  melody matching, and YouTube timing constraints; six annotators then
  validated candidates by ear against synthesised MIDI overlays, with a
  hidden control set gating annotator quality.
- **The honest caveat the authors publish: only 49.1% of segments yielded
  satisfactory alignments.** Melody matching lifted the success rate from
  2.3% to 31.7%; adding YouTube timing reached 40.0%. The 572 released
  segments are the survivors.
- **Published benchmark on it** (onset F1, 50 ms tolerance, `mir_eval`
  convention; "exact" = instrument label must match the MIDI program,
  "harm/perc" = reduced to pitched vs. unpitched):

  | Model | P (exact) | R (exact) | **F1 (exact)** | P (h/p) | R (h/p) | **F1 (h/p)** |
  |---|---|---|---|---|---|---|
  | MT3 | 31.03 | 28.18 | **28.42** | 39.55 | 37.10 | **36.83** |
  | YourMT3+ | 29.51 | 24.10 | **25.29** | 43.13 | 36.65 | **37.87** |

  No per-instrument breakdown is reported. The authors attribute the drop
  versus MusicNet/GuitarSet numbers to both models having been trained on
  multitrack pop only via **Slakh2100, a synthetic dataset**.

**This is the single most useful number in this document for map #123.**
It is the first published measurement of open multi-instrument
transcription on *real commercial band recordings*, and it is roughly
**38%, not 60%**. #125 flagged the real-mix figure as the field's missing
number; this is it, and the direction of the error was correctly
predicted.

### 1.3 Everything else, by category

**Real audio + note-level ground truth, but solo piano.**

| Corpus | Size | Score layer? | Real audio | Licence | Obtained |
|---|---|---|---|---|---|
| **ASAP** | 222 scores / 1,068 performances, >92 h; audio for 519 | **Yes** — MusicXML + quantised MIDI score, beats, downbeats, time sig, key sig, note-level score↔performance alignment | Yes (Disklavier, via MAESTRO) | CC BY-NC-SA 4.0 | `github.com/cpjku/asap-dataset` + init script for audio |
| **ACPAS** | superset of ASAP + MAPS-family sources | Yes (inherits) | Mixed | see repo | `github.com/cheriell/ACPAS-dataset` |
| **MAESTRO v3** | ~200 h, ~3 ms audio↔MIDI | **No** — performance MIDI only, no MusicXML | Yes (Disklavier) | CC BY-NC-SA 4.0 | direct, 109 GB |
| **MAPS** | 65 h | No | Mixed (Disklavier + VSTi) | CC BY-NC-SA 2.0 FR | Télécom Paris, ~31 GB |
| **Aria-MIDI** | 1.19 M files, ~100 k h | No | n/a — auto-transcribed, no paired audio distributed | CC BY-NC-SA 4.0 | `github.com/loubbrad/aria-midi` |

ASAP is the only corpus in this whole survey that ships a genuine
**human-written score in MusicXML** aligned to real audio. It is also
entirely solo classical piano.

**Real audio + note-level ground truth, classical chamber/orchestral.**

- **MusicNet** — 330 real classical recordings, ~34 h, 11 instruments in
  small chamber ensembles, >1 M note labels; the refined "MusicNet-EM"
  variant supplies DTW-aligned MIDI (~32 ms onset accuracy). **CC BY 4.0**,
  Zenodo record 5120004, ~11–14 GB. Real audio, but chamber, not band.
- **URMP** — 44 chamber pieces (duets–quintets), video + per-track and
  mixed audio, MIDI scores + PDF, frame-level pitch and note-level
  transcriptions per track. Real audio. **Licence unverified** — the
  Rochester site gates download behind a form with no licence text found.
- **Bach10** — 10 four-part Bach chorales, one instrument per part, MIDI
  scores plus ground-truth audio-score alignment. Tiny. **Licence
  unverified.**
- **PHENICX-Anechoic** — anechoic orchestral recordings with note
  onset/offset annotations. **Access gated: requires separate permission
  from Aalto University**, not an open CC licence.
- **Su dataset** — 10 short passages (piano solo, string quartet, piano
  quintet, violin sonata); ground truth produced by a pianist replaying on
  a silent piano synced to the recording. **Licence/access unverified.**

**Real recorded band audio, but no full note-level ground truth.**

- **MedleyDB v1+v2** — 122 songs, real multitrack recordings, genuinely
  band-shaped (pop/rock/singer-songwriter). But the open annotation is
  **melody f0** (108/122 tracks) plus instrument activations — not a
  polyphonic note transcription of every stem. **CC BY-NC-SA 3.0.**
- **MUSDB18 / MUSDB18-HQ** — 150 real songs, 4 stems
  (vocals/drums/bass/other). **No MIDI, no note annotation at all** — it
  is a source-separation corpus. **CC BY-NC-SA 4.0.** Useful to map #123
  only as separation ground truth (a different ticket's problem), or as
  raw audio to annotate ourselves.
- **RWC Music Database** — Popular (100 songs), Jazz (50), Royalty-Free
  (15), Classical (50), Genre (100), Instrument Sound (50). Real band
  audio, and the AIST annotation packages give beat, chord, melody and
  drum annotations — but for the Popular/Jazz sets those are
  **beat/chord/melody-line level, not full polyphonic multitrack note
  transcription**. A 2026 TISMIR re-release puts it on Zenodo under
  **CC BY-NC 4.0** (record 18656623); historically it was
  physical-media-at-cost.
- **GuitarSet** — 3 h, 6 guitarists, hexaphonic pickup enabling
  near-automatic note annotation, plus chords/beats/downbeats. Real audio,
  note-level, **CC BY 4.0**, Zenodo 3371780 — but **solo guitar**, not a
  mix.
- **FiloBass** / Weimar Jazz Database — real jazz recordings with
  **bass-only** or **solo-line-only** transcriptions.
- **Saraga** (Carnatic/Hindustani) — 168 tracks, real multitrack Indian
  art music with time-aligned melody/rhythm/structure annotations,
  **CC BY-NC-SA 4.0**. Genuinely multi-instrument ensemble audio, but the
  annotations are melody-contour/structural and the genre is off-target.

**Full band + perfect note-level ground truth, but synthesised.**

- **Slakh2100** — 2,100 tracks, 145 h, Lakh MIDI rendered through
  professional sample-based virtual instruments; every mix has at minimum
  piano, guitar, bass and drums; 34 instrument classes. Alignment is exact
  *by construction*. **CC BY 4.0**, Zenodo 4599666, 104 GB compressed
  (~500 GB uncompressed). This is the corpus every multi-instrument model
  in #125's table was trained on, and MulTTiPop's authors name it as the
  reason those models underperform on real recordings.
- **BabySlakh** — 20-track debug subset, Zenodo 4603870.
- **CocoChorales** — 240,000 four-part chorales, ~1,400 h, Coconet
  composition + MIDI-DDSP neural synthesis. **CC BY 4.0.** Chamber
  timbres, not band.
- **AAM (Artificial Audio Multitracks)** — 3,000 algorithmically composed
  multitrack songs rendered from real-instrument samples, with exact
  onset/pitch/instrument/key/tempo/chord/beat/segment annotations by
  construction. **Licence unverified.**
- **TuttiCorpus** (arXiv 2609.00640, `github.com/a-musiclover/TUTTI`) —
  363,610 audio–score pairs in **ABC notation**, generated by NotaGen and
  rendered with sfizz sample libraries at 22.05 kHz. 64.6% solo, ~29%
  duets/trios; top instruments piano, violin, cello, harpsichord, viola.
  It is the largest audio-to-**score** corpus in existence and is
  therefore tempting — but it is classical/chamber and fully synthetic,
  and its licence is not stated in the paper (**unverified**; the repo
  exists and points at Hugging Face `pzzzzz/TuttiCorpus` and a ModelScope
  mirror, with the HF copy noted as incomplete).
- **Groove MIDI (GMD)** / **E-GMD** — 13.6 h / ~444 h of human-performed
  drumming on Roland electronic kits, MIDI-aligned by construction, E-GMD
  re-rendered through 43 kits. **CC BY 4.0.** Drums only, and the audio is
  a kit's voice engine, not a mic'd acoustic kit in a mix — but for map
  #123's **drums-as-timing-oracle** decision this is the best available
  ground truth for a drum transcriber in isolation.

### 1.4 What is *not* available, anywhere

**A corpus of real band recordings with a human-written notated score.**
Nothing in this survey has both. ASAP has real audio + MusicXML but is
solo piano. MulTTiPop has real band audio but its ground truth is
performance MIDI (community-arranged Lakh MIDI, warped to the recording),
which carries a beat grid and tempo but is not an engraved score with
barlines, note values and pitch spelling settled by a human.

So the score layer for band material has **no ground truth corpus at
all**, matching #125's finding that it has no pretrained model either.
That is a coherent, if unwelcome, picture: it is the same gap seen from
two directions.

## 2. Dataset loaders: `mirdata`

`github.com/mir-dataset-loaders/mirdata`, ISMIR 2019 paper. Standardised
Python loaders that handle download, checksum validation and conversion of
each corpus's idiosyncratic annotation files into common objects designed
to feed straight into `mir_eval`. It is the data-loading half; `mir_eval`
is the scoring half; they share maintainers. `soundata` is the same design
applied to environmental audio and is irrelevant here.

- **Licence:** BSD-3-Clause.
- **Version 1.0.0, uploaded 2025-09-23.** `requires_python >=3.8`;
  classifiers stop at 3.11.
- **Measured here:** `pip install --dry-run mirdata` **resolves cleanly on
  this repo's Python 3.14.7 venv**, but pulls a large tree —
  `pandas 3.0.5`, `h5py`, `openpyxl`, `pretty_midi`, `mido`, and via
  `smart_open[all]` the **boto3, azure-storage-blob and google-cloud-storage
  SDKs** plus `cryptography`, `paramiko` and `PyNaCl`. That is a heavy
  dependency for what this project needs from it.
- **65 dataset loaders** (verified by listing `mirdata/datasets/` on
  GitHub). Relevant ones present: `slakh`, `guitarset`, `maestro`,
  `groove_midi`, `medleydb_melody`, `medleydb_pitch`, `phenicx_anechoic`,
  `rwc_classical`, `rwc_jazz`, `rwc_popular`, `saraga_carnatic`,
  `saraga_hindustani`, `haydn_op20`, `cipi`, `filosax`, `jtd`,
  `mdb_stem_synth`.
- **Absent, and these are the ones this project would want:**
  **MusicNet, ASAP/ACPAS, URMP, Bach10, MAPS, MUSDB18, E-GMD, AAM,
  CocoChorales, Aria-MIDI, MulTTiPop**, and full multitrack MedleyDB
  (only the melody/pitch subsets are loadable).

**Verdict: `mirdata` does not remove the corpus-wrangling work for this
map.** It covers Slakh2100 (with genuine automatic download — its
`slakh.py` carries a checksummed Zenodo `REMOTES` entry) and GuitarSet
well, and nothing else on the shortlist. Both MulTTiPop and Slakh are one
`huggingface_hub` call and one Zenodo URL respectively; writing those two
loaders by hand is less work than carrying three cloud SDKs. Adopt
`mirdata` only if the Slakh loader's validation is wanted for free.

## 3. Metrics: what each one actually measures

| Tool | Level | Scores notation? | Language | Licence | Version / py3.14 |
|---|---|---|---|---|---|
| **`mir_eval`** | note events, beats, chords, multipitch | **No** | Python | MIT | 0.8.2 (2025-02-25); **resolves on 3.14, measured here** |
| **MV2H** | score: pitch, voice, **meter**, **note value**, harmony | **Partly** — meter and note value, not literal barlines | Java 8+ (`make`) | MIT | v2.2; runs on the OpenJDK 26 present here |
| **pyMV2H** | as above, community port | as above | Python | Unlicense | 1.1.0 (2024-04-10); **resolves on 3.14, measured here** |
| **`musicdiff`** | **notation**: barlines, signatures, beams, ties, voicing, note values | **Yes, fully** | Python | MIT | 5.2 (2026-02-16), `requires_python >=3.10`; **resolves on 3.14, measured here** |
| **`partitura`** | data model / I/O, not metrics | n/a | Python | Apache-2.0 | 1.9.0 (2026-05-25), `>=3.10`; resolves |
| **MUSTER** | edit-distance note/rhythm error rates | Partly | script download | not stated | reference script at `amtevaluation.github.io` |
| **`madmom.evaluation`** | beats, onsets, notes, chords | No | Python/Cython | BSD code + **CC BY-NC-SA 4.0 models** | 0.16.1 (**2017-11-14**); **fails to build on 3.14, measured here** |
| **`music21.omr.evaluators`** | measure-level edit distance | Partly | Python | BSD-3 | ships in the `music21` already installed here |
| **`amt_evaluation`** | note events + instrument-family partial credit | No | Python | MIT | unmaintained since 2024-12; TF-pinned |
| **AMT-Dual-Eval** | notation (OMR-NED) + playback similarity | Yes | Python | MIT (claimed) | **repo 404s — not released** |

### 3.1 `mir_eval` — the note-event workhorse, and its blind spot

`github.com/craffel/mir_eval`, MIT, 0.8.2 (2025-02-25). `setup.cfg`
declares `python_requires >=3.8` with classifiers to 3.13; PyPI's own
`requires_python` is unset, so nothing gates the install. Dependencies are
`numpy>=1.20.3`, `scipy>=1.4.0`, `decorator` — all already in this repo's
venv. **Measured here: it resolves on Python 3.14.7.** It is pure Python;
there is no build step to fail.

Modules: `alignment`, `beat`, `chord`, `display`, `hierarchy`, `io`,
`key`, `melody`, `multipitch`, `onset`, `pattern`, `segment`,
`separation`, `sonify`, `tempo`, `transcription`,
`transcription_velocity`, `util`.

`transcription.precision_recall_f1_overlap()` defaults:
`onset_tolerance=0.05`, `pitch_tolerance=50.0` (cents),
`offset_ratio=0.2`, `offset_min_tolerance=0.05`. A note is correct when
its onset is within ±50 ms of a reference note and its pitch within a
quarter tone; with offsets enabled, additionally within 20% of the
reference note's duration or 50 ms, whichever is larger. This is the
convention every number in #125's tables and in §5 below was computed
under.

**What it cannot see:** barlines, time signature, key signature, note
values, pitch spelling, beaming, voicing, staff assignment. None of those
concepts appear anywhere in its module list. `mir_eval.beat` (F-measure at
0.07 s, Cemgil, Goto, P-score, CMLc/CMLt/AMLc/AMLt, information gain) is
the closest thing to a meter metric, and it is still purely
timestamp-based — it scores a beat *sequence*, never a notated bar.
`mir_eval.alignment` is unrelated: it scores lyric/karaoke-style
timestamp alignment, not symbolic score alignment.

**So `mir_eval` is necessary and insufficient.** It is the right tool for
the note-event half and it is the tool every published number this project
would compare against was produced with — but a transcription that gets
every note right and every barline wrong scores 1.0 on it.

### 3.2 MV2H — the metric built for exactly this problem

`github.com/apmcleod/MV2H`, **MIT** (Copyright 2018 Andrew McLeod), Java,
built with `make`, run as `java -cp bin mv2h.Main`. Papers: McLeod &
Steedman, "Evaluating Automatic Polyphonic Music Transcription", ISMIR
2018; and the alignment follow-up, arXiv 1906.00566 (ISMIR 2019 LBD).

Five sub-metrics, each reported separately:

- **Multi-pitch** — correct simultaneous pitches.
- **Voice** — correct voice/stream assignment.
- **Meter** — correct metrical structure (beats/sub-beats/tatums and the
  bar hierarchy, including anacrusis).
- **Note Value** — correct rhythmic durations.
- **Harmony** — correct key and chord labelling.

The headline `MV2H` figure is the **unweighted arithmetic mean** of the
five. Confirmed from the README's own worked example: (0.9302325581395349
+ 0.8125 + 0.7368421052631577 + 0.9642857142857143 + 1.0) / 5 =
0.8887720755376813, which is exactly the `MV2H:` line printed. (#124's
brief asked what these things measure; the aggregation formula is often
cited vaguely, so it is pinned here.)

**Input format** is its own line-based text schema, not MIDI or MusicXML
directly — `Note pitch on onVal offVal voice`, `Tatum time`,
`Hierarchy bpb,sbpb tpsb a=al [time]`, `Key tonic maj/min [time]`,
`Chord time chord`, all times in milliseconds. Converters ship in the
repo: `java -cp bin mv2h.tools.Converter -m -i x.mid -o x.txt` for MIDI,
and an `evaluate_xml.bash gt.xml transcription.xml` one-liner for
MusicXML — **which shells out to `musescore3`**, and MuseScore is not
installed on this machine (checked). Chord symbols are not parsed by the
MIDI converter.

**A warning in MV2H's own README that lands directly on this repo:** its
recommended MusicXML→MIDI route is MuseScore 3, and it explicitly says
"other methods may also work, but not all will handle anacrusis (pick-up)
measures correctly (`music21`, for example, did not when I tested it and
will require manual setting during the MIDI conversion with `-a INT`)."
This project's score I/O is entirely `music21`
(`score_writer.py`, `score_editor_state.py`). If MV2H is adopted, either
MuseScore becomes a dev-time dependency of the harness or the anacrusis
offset is set by hand — and this repo's existing
`score_editor_state.py` measure-alignment limitation (documented in
`docs/DECISIONS.md`) is in the same family of problem.

**pyMV2H** (`github.com/lucasmpaim/pyMV2H`, PyPI `pyMV2H` 1.1.0,
2024-04-10, **Unlicense**, deps `docopt`/`pretty-midi`/`mido`/`tqdm`)
**resolves cleanly on Python 3.14 — measured here**. The upstream README
credits it but states plainly: "The java version of MV2H should always be
considered the canonical, 'correct' version, and should be used for final
evaluation of a system," and notes the Python port "does run significantly
slower." OpenJDK 26 is already installed on this machine, so the Java
version is not actually an obstacle.

### 3.3 MUSTER — the other notation-error family

Nakamura's MUSTER metrics (`amtevaluation.github.io`), from Nakamura,
Benetos, Yoshii & Dixon, "Towards Complete Polyphonic Music
Transcription", ICASSP 2018. Edit-distance-based error rates in the spirit
of word error rate — pitch errors, missing notes, extra notes, onset and
offset timing errors, and a rhythm/note-value component, of which five
note-oriented rates enter the reported `MeanER`. A reference evaluation
script is downloadable from that site. Its predecessor is Cogliati & Duan,
"A Metric for Music Notation Transcription Accuracy", ISMIR 2017, which
counts errors over notes, durations, rests, **barlines**, staff assignment
and ties — the earliest metric in this survey to score barlines
explicitly; **no public reference implementation for it was found**.

MUSTER is what the ASAP rhythm-quantisation literature reports (including
the arXiv 2604.22290 work #125 cited as the design target for the score
layer). Its weakness is documented from an unexpected direction — see
§3.5.

### 3.4 `musicdiff` — the one tool that speaks this project's own file format

`github.com/gregchapman-dev/musicdiff`, **MIT**, PyPI **5.2 released
2026-02-16**, `requires_python >=3.10`, dependencies `music21>=9.9.1`,
`numpy`, `converter21>=4.0.1`. **Measured here:
`pip install --dry-run musicdiff` on this repo's 3.14.7 venv resolves,
pulling only `converter21 4.0.1` — `music21 10.5.0` and `numpy 2.5.2` are
already installed.** It is derived from Foscarin's `music-score-diff`
(Foscarin, Jacquemard & Fournier-S'niehotta, "A diff procedure for music
score files", DLfM 2019), with Foscarin's permission and continued
advice.

It compares, per its own `--include`/`--exclude` vocabulary:
`notesandrests`; the note decorations `beams`, `tremolos`, `ornaments`,
`articulations`, `ties`, `slurs`; the other objects `signatures` (key and
time), **`barlines`**, `staffdetails`, `chordsymbols`, `ottavas`,
`arpeggios`, `lyrics`; plus `style`, `metadata`, `notestaffposition` and
`voicing`. `notestaffposition` compares staff position rather than
diatonic pitch specifically to avoid cascading errors from a wrong clef or
key — a thoughtful touch for exactly the failure mode a guessed key
signature produces. `voicing` compares how notes are grouped into voices
and chords.

Three output modes: marked-up PDF (`visual`, the default), diff-like text
(`text`), and **`omrned` — a JSON numeric OMR-NED (Optical Music
Recognition Normalized Edit Distance) score**. It also has a batch mode,
`--ml_training_evaluation --ground_truth_folder … --predicted_folder …`,
built for exactly the "score a whole run against a folder of ground
truth" job a harness needs. Input is "any format music21 or converter21
can parse" — MusicXML, MEI, Humdrum `**kern`, MIDI.

**This is the strongest single find in this document.** It is MIT, it
installs on 3.14 today with one new dependency, it reads the MusicXML this
repo already writes, it scores precisely the layer #125 established that
nothing pretrained provides, and it emits a single number per comparison.

### 3.5 The 2026 result that reframes all of the above

"A Dual Evaluation for Music Transcription" (arXiv 2608.04511) argues that
audio-to-score evaluation is irreducibly **two-dimensional**: a score must
be judged on both written appearance and playback fidelity, and these can
favour different systems. It pairs **OMR-NED** (notation similarity — the
same normalised edit distance over notation symbols `musicdiff` emits,
attributed to Martinez-Sevilla et al. 2025) with playback-similarity
measures (DTW and TWED over 14 audio features; learned embeddings CLEWS,
CLaMP 3, and LLM judges).

Validated against **106 participants and 3,180 ABX judgements** aggregated
by Bradley–Terry, over 24 modular pipelines (8 audio-to-MIDI models ×
3 MIDI-to-score converters: `music21`, MuseScore, MIDI2ScoreTransformer)
plus an end-to-end system. Correlations with human preference: CLEWS
ρ=0.971, Gemini 3.1 Pro ρ=0.970, TWED–MFCC ρ=0.924, DTW–Chroma CENS
ρ=0.891.

Two findings this project should absorb:

- **It criticises MUSTER directly:** its subscores lack consistent
  interpretation — the structure-oriented components track OMR-NED
  (ρ=0.94) while `MeanER` correlates more strongly with *playback*
  preference (ρ=0.79) than with notation similarity (ρ=0.38). Reporting
  MUSTER's headline number as "notation accuracy" is therefore
  misleading.
- **It states that note-level F1 metrics are appropriate only for
  MIDI-like outputs, not sheet music** — an independent confirmation, from
  a human-validated study, of the concern that motivated this ticket.

Its corpus is 230 recordings from ATEPP over 23 works, 6 composers, 30
performers — **solo piano again**, deliberately excluding ASAP-sourced
material to limit training overlap. **The MIT-licensed evaluation code the
paper announces at `github.com/pingw220/AMT-Dual-Eval` returns 404 as of
this research (checked, both the web page and the GitHub API).** Treat it
as a design reference, not a dependency — the same status #125 gave
arXiv 2604.22290.

### 3.6 The rest, briefly

- **`partitura`** (`github.com/CPJKU/partitura`, **Apache-2.0**, 1.9.0
  released 2026-05-25, `>=3.10`) models score *and* performance and reads
  MusicXML, MEI and MIDI into one note-array representation, with a
  first-class `partitura.io.importnakamura` for Nakamura alignment
  match-files. It ships `show_diff()` but is **not a metrics library** —
  it is the substrate. Useful if this project ever needs a
  score/performance data model richer than `music21`'s; not needed to
  score anything.
- **`music21.omr.evaluators`** — already installed here. `OmrGroundTruthPair.getDifferences()`
  returns a minimum edit distance between two `music21` Scores, and
  `evaluateCorrectingModel()` returns original vs. corrected distances.
  Built for OMR-correction evaluation, measure-level. Real, shipped, and
  free — worth a look as a zero-dependency sanity check, though
  `musicdiff` is strictly more informative. (Note: **there is no
  `music21` API called `alignmentScore`** — that name does not appear in
  the library; the real analogues are `music21.omr` and `music21.search`.)
- **`madmom`** — `madmom.evaluation` has beat/onset/note/chord
  submodules, but **PyPI's last release is 0.16.1 from 2017-11-14**, with
  no `requires_python` metadata, dual BSD-code / **CC BY-NC-SA 4.0
  models**. **Measured here:** `pip install --dry-run madmom` on this
  venv fails at `Getting requirements to build wheel` with
  `ModuleNotFoundError: No module named 'Cython'` — the sdist's
  `setup.py` imports Cython at build time without declaring it as a build
  requirement. Even pre-installing Cython leaves the long-running
  numpy-2.x ABI problems reported upstream. **Do not depend on it.**
  (Noted for context: MulTTiPop's own alignment pipeline used madmom's
  RNN beat tracker, so its ground truth carries whatever madmom's beat
  tracking got wrong.)
- **`amt_evaluation`** (`github.com/ojas-chaturvedi/amt_evaluation`, MIT,
  by one of the AMT Challenge organisers) — inspected directly: it is a
  **vendored 38 KB fork of `mir_eval/transcription.py`** plus
  `instrument_similarity.py`/`.yaml`, which add *partial credit* for a
  wrong-but-similar instrument, using a cosine similarity over NSynth
  timbre qualities across 11 instrument families. 17 commits, 0 stars,
  untouched since 2024-12-20, and its `requirements.txt` is a fully
  pinned 2024 snapshot including `tensorflow==2.18.0` and `numpy==2.0.2`.
  **Skip the package; the idea — instrument-family partial credit — is
  worth remembering**, because a band transcriber that calls a Rhodes a
  piano is not as wrong as one that calls it a trombone. Note that the
  AMT Challenge paper itself says plain `mir_eval` was used, not this.
- **`AMPACT`/`pyampact`** (MIT, 0.0.6, 2026-04-01) is a score-to-audio
  alignment and performance-descriptor toolkit, not a transcription
  metric, and its `numpy<2.0` pin makes 3.14 doubtful (**unverified**).
- No maintained tool named **SMAT** or **ScoreSimilarity**, and no
  Verovio-based score diff, surfaced as an established project.

## 4. The alignment problem: scoring against a score with no timestamps

This is the part the ticket predicted might have no clean answer. It has
**two** clean answers and a large amount of ad-hockery around them.

**Answer 1 — MV2H's `-a` flag.** MV2H v2.0 added exactly this capability,
documented in arXiv 1906.00566 ("Evaluating Non-aligned Musical Score
Transcriptions with MV2H"). Invocation:
`java -cp bin mv2h.Main -g gt.txt -t transcription.txt -a [-p DOUBLE] [-v]`.
It performs a **DTW alignment** between the transcription and the ground
truth before scoring, so a ground-truth score with its own independent
timeline can be used directly. `-A` prints note-by-note alignment details.
`-p` (added in v2.2) tunes the DTW insertion/deletion penalty: higher is
faster and forces more notes to align even when they match poorly; lower
is slower and aligns only near-exact matches; 0.5 and 1.0 are inflection
points, and the README insists the same value be used throughout an
evaluation for fairness. The paper's stated motivation is precisely
that "non-aligned musical scores are significantly more widely available
than aligned ones." v2.2 also "sped up the DTW process significantly."

**Answer 2 — Nakamura's Symbolic Music Alignment Tool**
(`midialignment.github.io`, from Nakamura, Yoshii & Katayose,
"Performance Error Detection and Post-Processing for Fast and Accurate
Symbolic Music Alignment"). **MIT-licensed**, distributed as a
downloadable tool with an alignment-visualising GUI. The strongest signal
that this is the community default rather than a one-off is that
`partitura` ships a dedicated importer for its output format
(`partitura.io.importnakamura`). It aligns symbolic to symbolic — a score
to a performance MIDI — which is the right shape when the transcription is
already MIDI-like and the reference is a notated score.

**And the ad-hockery.** Outside those two, most papers do this per-paper:
either alignment falls out of the pipeline for free (the transcriber
already tracks a shared clock or a DTW path, so there is nothing to align
afterwards) or a bespoke DTW over pitch-class/onset sequences is written
and never published as a library. **There is no "`mir_eval` but for
aligning two untimed scores" as a general community package.** Related but
distinct: `music21.omr` is measure-level edit distance, not a general
aligner; `music21.search` does approximate melodic-segment matching;
`AMPACT`/`partitura` handle score-to-*audio* alignment, which is a
different problem (and needs audio synthesised from the reference).

**Practical consequence for map #123.** The alignment question mostly
dissolves for this project's *first* harness, because the two corpora
worth using — MulTTiPop and Slakh2100 — both ship ground truth that is
already time-aligned to the audio. Alignment becomes load-bearing only at
the second stage, when a **notated** reference is compared, and there the
answer is MV2H `-a` (for its five sub-metrics) or `musicdiff` (which is
edit-distance over notation structure and needs no timeline at all — an
underrated property: a bar-tree diff is intrinsically timestamp-free).

## 5. Published numbers: the realistic bar

Ordered from most to least relevant to a band mix. All onset F1 unless
noted; all use the `mir_eval` ±50 ms convention.

| Corpus | Audio | Material | Best published | Source |
|---|---|---|---|---|
| **MulTTiPop** | **real commercial** | **pop/rock band** | **37.87** (YourMT3+, harm/perc) · 36.83 (MT3) · 28.42 / 25.29 exact-instrument | arXiv 2607.08756 |
| AMT Challenge 2025 test set | synthetic (FluidSynth + FluidR3 GM) | ≤3 classical instruments | 59.98 (MIROS) · 59.38 (YourMT3-YPTF-MoE-M) · 55.81 · 39.47 (YourMT3-P) · 39.32 (MT3 baseline) | arXiv 2603.27528 |
| MuScriptor's own 372-track set | mixed | multi-instrument | 60.4 onset / 47.8 multi (1.3B, +RL) | arXiv 2607.08168 |
| Slakh2100 | synthetic | full band | 84.56 instrument-agnostic onset; 74.84 multi onset-offset | arXiv 2407.04822 |
| URMP | real | chamber | 67.98 multi F1 | arXiv 2407.04822 |
| MIR-ST500 | real | pop vocals | 71.07 multi F1 | arXiv 2407.04822 |
| MAESTRO v3 | real | solo piano | 96.52–96.98 onset (YourMT3+) · 95.05 onset / 93.14 onset+offset (Transkun v2) | arXiv 2407.04822; Transkun README |

Rank 4 of the AMT Challenge (YourMT3-P, 0.3947) is filled in here; #125's
table flagged it as missing from that extraction. Runtimes reported in the
same table are 12.60–22.05 ms, but the paper does not state the hardware,
so they are **not** comparable to the CPU budget #125 needs.

The AMT Challenge adopts the `mir_eval` convention verbatim ("the
precision and recall of the reference and estimated MIDI are computed
using the `mir_eval` library"), with **Multi Onset F1** correct only if
program, pitch and onset (±50 ms) all match, and an **Overlap** figure
computed as onset/offset intersection-over-union. Its "instrument leakage"
is discussed as a failure mode (hallucinating nonexistent instruments),
**not defined as a formal metric in the paper** despite being described as
such elsewhere. Its 76-piece evaluation set (~20 s each, eight
instruments, five professional composers) carries **no stated licence or
availability terms**, and no leaderboard or evaluation-code URL appears in
the paper.

**The bar, stated plainly.** On real band audio, the state of the art is
**under 40% onset F1**, and *below 30%* if the instrument label has to be
right. Anything this project builds is being measured against that, not
against the 95% piano figure or the 60% synthetic figure. That is a
significant recalibration of expectations from what #125 was able to
establish, and it is good news in one respect: the bar is low enough that
a careful pipeline has room to be interesting.

## 6. Shortlist

**Tier 1 — adopt.**

1. **`mir_eval` 0.8.2** (MIT, installs on 3.14 with zero new
   dependencies beyond what is already here). The note-event metric,
   non-negotiable, because it is what every number in §5 was computed
   with. Use `transcription.precision_recall_f1_overlap()` at its
   documented defaults and report the defaults alongside the number.
2. **`musicdiff` 5.2** (MIT, `>=3.10`, one new dependency
   `converter21` on top of the `music21` this repo already ships). The
   notation metric. It reads the MusicXML `score_writer.py` already
   writes, scores barlines/signatures/note values/voicing/beaming, emits
   a JSON OMR-NED number, needs no timeline, and has a folder-vs-folder
   batch mode. This is the direct answer to the ticket's third priority.
3. **MulTTiPop** (CC BY 4.0). The only real-band evaluation corpus that
   exists. 3.5 h, aligned MIDI + YouTube pointers from Hugging Face; the
   audio has to be fetched separately, and the authors ask that it be used
   for evaluation only.
4. **Slakh2100** (CC BY 4.0, Zenodo 4599666, 104 GB). The large synthetic
   proxy for iteration and ablation — with the standing caveat, which
   MulTTiPop's authors state and this document endorses, that **good
   Slakh numbers do not transfer to real recordings**.

**Tier 2 — adopt when the score layer exists to be measured.**

5. **MV2H v2.2** (MIT, Java; OpenJDK 26 already present). Its Meter and
   Note Value sub-metrics are the only published, citable numbers for the
   two things map #123 commits to guessing (barlines and time signature),
   and its `-a` DTW flag is the ready-made answer to the unaligned-score
   problem. Costs: a Java build step, a text-format conversion, and either
   MuseScore or a hand-set anacrusis offset because of its documented
   `music21` caveat. **pyMV2H** (Unlicense, installs on 3.14) is the
   quick path for exploration; upstream says the Java version is canonical
   for published numbers.
6. **GMD / E-GMD** (CC BY 4.0) for evaluating the drum transcriber that
   map #123's drums-as-timing-oracle decision depends on, plus
   `mir_eval.beat`'s CMLt/AMLt for the beat grid it produces.

**Tier 3 — situational.**

7. **`mirdata` 1.0.0** (BSD-3) — only for its Slakh and GuitarSet
   loaders, and only if its `smart_open[all]` cloud-SDK dependency tree is
   acceptable. It has no loader for MulTTiPop, MusicNet, ASAP, URMP or
   MUSDB18.
8. **ASAP** (CC BY-NC-SA 4.0) — the only corpus with real audio *and* a
   human-written MusicXML score. Solo piano, so wrong genre, but it is the
   only place a `musicdiff`/MV2H score-layer harness can be *validated
   against a real human score at all* before it is pointed at band
   material. Note the **NC** term.
9. **`music21.omr.evaluators`** — free, already installed, measure-level
   edit distance. A cheap smoke test while `musicdiff` is being wired up.

**Explicitly skip.**

- **`madmom`** — verified here to fail to build on Python 3.14, last
  released 2017, NC-licensed models. Its beat metrics are covered by
  `mir_eval.beat`.
- **`amt_evaluation`** — an unmaintained fork of `mir_eval.transcription`
  with a TensorFlow-pinned requirements file. Keep the instrument-family
  partial-credit *idea*; take none of the code.
- **MUSTER as a headline number** — arXiv 2608.04511 shows its `MeanER`
  correlates with *playback* preference (ρ=0.79) far more than with
  notation similarity (ρ=0.38), so reporting it as "notation accuracy"
  would be wrong. Read the papers that use it; do not adopt it as this
  project's metric.
- **AMT-Dual-Eval** — the repo does not exist yet. Design reference only.
- **TuttiCorpus / CocoChorales / AAM** — synthetic and classical; Slakh
  already covers the synthetic-proxy role with band instrumentation and a
  clear licence.
- **`partitura`, `AMPACT`** — good libraries, wrong job. Neither scores a
  transcription.

## 7. What has to be built regardless

1. **Corpus fetchers for MulTTiPop and Slakh2100.** `mirdata` covers the
   second and not the first, and MulTTiPop needs a YouTube-segment fetch
   step that no loader library will ever ship.
2. **A model-output → `mir_eval` intervals/pitches adapter**, per
   pipeline stage. Trivial, but it is the thing that makes the numbers
   comparable to §5.
3. **A MusicXML reference for band material.** This does not exist
   anywhere (§1.4). Producing even a handful of hand-notated bars over
   MulTTiPop segments — or accepting `musicdiff`-against-our-own-earlier-output
   as a regression metric rather than an accuracy metric — is a decision
   this map has to make, and it is the evaluation-side twin of #125's
   "no open model emits the score layer".
4. **A per-stem evaluation convention.** Every metric here scores one
   reference against one estimate; a multi-track output needs a stated
   rule for how stems are matched to reference parts before scoring, and
   MulTTiPop's own "exact vs. harmonic-percussive" split (a 10-point F1
   difference for YourMT3+) shows how much that choice moves the number.

## 8. What this means for map #123

- **The map's evaluation-before-algorithm decision is vindicated and
  cheaper than expected.** The note-event half is two `pip install`s that
  resolve on this machine's Python 3.14 today; the notation half is a
  third that reads the MusicXML this repo already writes.
- **The realistic bar is ~38% onset F1 on real band audio, not 60%.**
  MulTTiPop supplies the number #125 said no source provided. Any accuracy
  target in this map should be set against that, and any Slakh number
  quoted in this project's voice needs the synthetic caveat attached.
- **Evaluating the score layer on band material is not fully solvable
  today.** There is no notated ground truth for a band mix anywhere.
  `musicdiff` and MV2H give a *mechanism*; the *reference* has to be made,
  bought, or scoped down to piano (ASAP) for validation and then trusted
  by analogy. That is an honest limit, and it should be recorded as one
  rather than designed around silently.
- **The drums-as-timing-oracle decision has usable ground truth**
  (GMD/E-GMD, CC BY 4.0) even though the rest of the score layer does not
  — which strengthens the case for leaning on it.
- **Licence pressure again.** MulTTiPop, Slakh, GuitarSet, MusicNet, GMD
  and CocoChorales are CC BY 4.0 — clean. MAESTRO, ASAP, MAPS, MedleyDB,
  MUSDB18, Saraga and RWC are all **NC**, and `madmom`'s models are
  NC-SA. Combined with #125's finding that this repo has **no LICENSE
  file**, the set of corpora a *distributable* product could quote numbers
  from is narrower than the set available to a hobby project. Evaluation
  data is one more thing the licence decision reaches into.

## 9. Unverified / open

- **Nothing was installed and nothing was run against real data.** The
  only things actually executed were `pip install --dry-run --report`
  resolutions on this repo's `.venv` (Python 3.14.7): `mir_eval` →
  0.8.2 ok; `mirdata` → 1.0.0 ok (heavy tree); `partitura` → 1.9.0 ok;
  `musicdiff` → 5.2 ok (+`converter21` 4.0.1); `pretty_midi` → 0.2.11
  ok; `soundata` → 1.0.1 ok; `miditok` → 3.0.6 ok; `pyMV2H` → 1.1.0 ok;
  **`madmom` → build failure (`ModuleNotFoundError: No module named
  'Cython'`)**. A dry-run resolution is not proof that a package imports
  and runs correctly on 3.14.
- **MV2H was not compiled or executed here**, and its
  `evaluate_xml.bash` path was not exercised — MuseScore is not installed
  on this machine (checked). The `music21`-mishandles-anacrusis warning is
  quoted from MV2H's README, not reproduced.
- **MV2H's aggregation formula** was derived by arithmetic from its
  README's own worked example, which matches an unweighted mean of the
  five sub-scores exactly. The ISMIR 2018 paper was not fetched to
  confirm the formula is *stated* that way.
- **`musicdiff`'s OMR-NED output was not inspected** — its existence,
  name and JSON form are read off the README's `-o omrned` documentation.
  Whether the number is comparable to the OMR-NED figures in arXiv
  2608.04511 (attributed there to Martinez-Sevilla et al. 2025) is
  **unverified**.
- **URMP's, Bach10's, the Su dataset's and AAM's licences were not
  found.** Absence of a licence statement is not permission.
- **TuttiCorpus's licence is not stated** in arXiv 2609.00640; the repo
  exists and points at mirrors, one of which it describes as incomplete.
- **The AMT Challenge's 76-piece evaluation set has no stated licence or
  availability**, and no evaluation-code or leaderboard URL appears in
  arXiv 2603.27528. The "instrument leakage ratio" and "instrument
  detection F1" attributed to the challenge in secondary summaries are
  **not formally defined in the paper**.
- **`AMT-Dual-Eval` is announced but not published** —
  `github.com/pingw220/AMT-Dual-Eval` returns 404 from both the web and
  the GitHub API as of this research.
- **MulTTiPop's alignment quality was not independently assessed.** The
  49.1% satisfactory-alignment rate and the six-annotator validation
  process are the authors' own reporting; the released 572 segments are
  the ones that passed, so the released set should be better than that
  figure suggests, but by how much is unknown.
- **`mirdata`'s per-dataset download behaviour was verified only for
  Slakh** (a checksummed Zenodo `REMOTES` entry). Which of its other 64
  loaders genuinely download versus index-only was not exhaustively
  checked.
- **`partitura`'s Humdrum `**kern` support** was not confirmed from a
  primary module listing — likely present, **unverified**.
- No number in §5 was reproduced. Every figure is as published by its
  source, and the MulTTiPop table and the AMT Challenge table are **not**
  comparable to each other (different corpora, different instrument
  vocabularies, different matching schemas).

## Sources

Primary sources, fetched September 2026.

**Corpora.** MulTTiPop: arXiv 2607.08756 (`arxiv.org/html/2607.08756v1`),
`gclef-cmu.org/multtipop`, `huggingface.co/datasets/gclef-cmu/multtipop`
(licence tag `cc-by-4.0`, 572 rows / dev 169 / test 403). ASAP:
`github.com/cpjku/asap-dataset`. ACPAS:
`github.com/cheriell/ACPAS-dataset`. MAESTRO v3:
`magenta.withgoogle.com/datasets/maestro`. MAPS:
`adasp.telecom-paris.fr/resources/2010-07-08-maps-database/`. Aria-MIDI:
`github.com/loubbrad/aria-midi`, arXiv 2504.15071. MusicNet:
`zenodo.org/records/5120004`, `johnthickstun.com/musicnet.html`. URMP:
`labsites.rochester.edu/air/projects/URMP.html`, arXiv 1612.08727.
Bach10: `github.com/flippy-fyp/Bach10_v1.1`. PHENICX-Anechoic:
`upf.edu/web/mtg/phenicx-anechoic`, Zenodo DOI 10.5281/zenodo.840024.
MedleyDB: `medleydb.readthedocs.io`, `github.com/marl/medleydb`, Bittner
et al. ISMIR 2014. MUSDB18: `sigsep.github.io/datasets/musdb.html`,
`github.com/sigsep/website`. RWC: `staff.aist.go.jp/m.goto/RWC-MDB`,
`zenodo.org/records/18656623`, `transactions.ismir.net/articles/10.5334/tismir.326`.
GuitarSet: `zenodo.org/records/3371780`. Saraga:
`compmusic.upf.edu/datasets`, Zenodo DOI 10.5281/zenodo.4301737.
FiloBass: arXiv 2311.02023. Slakh2100: `zenodo.org/records/4599666`;
BabySlakh `zenodo.org/records/4603870`. CocoChorales:
`github.com/lukewys/chamber-ensemble-generator`, arXiv 2209.14458. AAM:
`github.com/fabianostermann/ArtificialSongGenerator`, EURASIP
`link.springer.com/article/10.1186/s13636-023-00278-7`. TUTTI/TuttiCorpus:
arXiv 2609.00640, `github.com/a-musiclover/TUTTI` (README fetched).
GMD: `magenta.tensorflow.org/datasets/groove`; E-GMD via Kaggle.

**Loaders.** `mirdata`: `github.com/mir-dataset-loaders/mirdata`, ISMIR
2019 paper `archives.ismir.net/ismir2019/paper/000009.pdf`, PyPI JSON for
`mirdata` 1.0.0 (2025-09-23, BSD-3-Clause, `requires_python >=3.8`,
classifiers 3.8–3.11), GitHub contents API listing of
`mirdata/datasets/` (65 loaders). `soundata`:
`github.com/soundata/soundata`.

**Metrics.** `mir_eval`: `github.com/craffel/mir_eval` (MIT),
`setup.cfg`, `mir_eval/transcription.py` docstring and signature
defaults, `mir_eval/beat.py`, PyPI JSON for 0.8.2 (2025-02-25). MV2H:
`github.com/apmcleod/MV2H` README and LICENSE (MIT), McLeod & Steedman
ISMIR 2018 `ismir2018.ircam.fr/doc/pdfs/148_Paper.pdf`, arXiv 1906.00566
for the DTW alignment extension. pyMV2H:
`github.com/lucasmpaim/pyMV2H`, PyPI JSON for 1.1.0 (2024-04-10,
Unlicense). `musicdiff`: `github.com/gregchapman-dev/musicdiff` README
and LICENSE (MIT), PyPI JSON for 5.2 (2026-02-16, `>=3.10`), docs at
`gregchapman-dev.github.io/musicdiff`, derived from
`github.com/fosfrancesco/music-score-diff` (Foscarin et al., DLfM 2019,
`dl.acm.org/doi/10.1145/3358664.3358671`). `partitura`:
`github.com/CPJKU/partitura`, PyPI JSON for 1.9.0 (2026-05-25,
Apache-2.0), `partitura.readthedocs.io` `importnakamura` module page.
MUSTER: `amtevaluation.github.io`; Nakamura et al. ICASSP 2018; Cogliati
& Duan ISMIR 2017 `archives.ismir.net/ismir2017/paper/000131.pdf`.
`madmom`: `github.com/CPJKU/madmom`, PyPI JSON for 0.16.1 (2017-11-14),
issue 463 on the Cython build requirement. `music21.omr.evaluators`:
`music21.org/music21docs/moduleReference/moduleOmrEvaluators.html`.
`amt_evaluation`: `github.com/ojas-chaturvedi/amt_evaluation` (MIT,
GitHub API metadata, tree listing, `compare_midi.py`,
`instrument_similarity.py`, `instrument_similarity.yaml`,
`requirements.txt`, last push 2024-12-20). `pyampact`:
`github.com/pyampact/pyampact`, arXiv 2412.05436.

**Alignment.** arXiv 1906.00566 ("Evaluating Non-aligned Musical Score
Transcriptions with MV2H"); Nakamura Symbolic Music Alignment Tool at
`midialignment.github.io`; `partitura.io.importnakamura`.

**Benchmarks.** arXiv 2603.27528 (2025 AMT Challenge — metric
definitions, ranks 1–5 incl. rank 4, `mir_eval` statement);
arXiv 2407.04822 (YourMT3+ per-dataset table); arXiv 2607.08168
(MuScriptor); arXiv 2607.08756 (MulTTiPop benchmark table);
`github.com/Yujia-Yan/Transkun` README.

**Reframing.** arXiv 2608.04511 ("A Dual Evaluation for Music
Transcription" — OMR-NED, CLEWS/TWED/DTW correlations, the MUSTER
critique, the note-level-F1 caveat, the announced-but-absent
`github.com/pingw220/AMT-Dual-Eval`).

**Measured locally**, on this repo's `.venv` (Python 3.14.7, pip 26.1.2):
`pip install --dry-run --report -` resolutions for `mir_eval`, `mirdata`,
`partitura`, `musicdiff`, `madmom`, `pretty_midi`, `soundata`, `miditok`,
`pyMV2H`; `java -version` (OpenJDK 26.0.2.1); absence of
`musescore3`/`mscore`; HTTP status checks for
`github.com/pingw220/AMT-Dual-Eval` (404),
`github.com/apmcleod/MV2H` (200), `github.com/a-musiclover/TUTTI` (200),
`huggingface.co/datasets/gclef-cmu/multtipop` (200).

In-repo cross-references:
`docs/research/pretrained-transcription-weights.md`,
`docs/research/oss-landscape-transcription-and-prior-art.md`,
`docs/research/detection-systems-survey.md`, map
[#123](https://github.com/pellepang/note-color/issues/123), ticket
[#124](https://github.com/pellepang/note-color/issues/124).
