# Transcription stack and pipeline shape (issue #129)

Map [#123](https://github.com/pellepang/note-color/issues/123)'s converter,
decided after #139 settled the licence rules it was blocked on. What is
settled here is the **architecture, the seam and the default stack**; what
is deliberately *not* settled is anything the map's own
evaluation-before-algorithm rule says must be measured first (below).

### A finding that reopens #125's "unresolved" licence discrepancy

#125 recorded YourMT3's GPL-3.0-repo vs. Apache-2.0-redistribution split
as unresolved. Opening the `mt3-infer` 0.2.0 wheel here settles half of
it: every vendored YourMT3 source file carries a per-file header reading
`Copyright 2024 The YourMT3 Authors. Licensed under the Apache License,
Version 2.0` — and then, in the same header, `Please see the details in
the LICENSE file`, which is the GPL-3.0 one. So the contradiction is
*inside the upstream grant itself*, not an error introduced by the
repackager. `mt3-infer` itself is genuinely MIT.

Under #139's rule (contradictory licence → most restrictive reading unless
upstream clarifies) **YourMT3+ is excluded, pending one question to the
maintainer** — the same route by which #126 got Demucs' answer. Recorded
as reversible rather than final.

### Two capabilities, not one command

`virtualnote transcribe` is untouched. The converter is a **new
`virtualnote convert`** plus a menu entry, because the two are different
products wearing one name otherwise: `transcribe` returns an ANSI dump or
a one-part MusicXML from a recording in seconds, while `convert` eats a
band mix for up to an hour and emits a multi-track project. Bolting the
second onto the first's flag surface would give one command whose runtime
varies by three orders of magnitude and whose output shape changes
underneath the user. It also gives the refusal behaviour below a clean
home: `convert` refuses without its extra; `transcribe` keeps working for
everyone already using it.

### Refuse, don't degrade

With no neural extra installed, `convert` **refuses and names the install
line** rather than silently falling back to the live DSP pipeline. #124
measured the ceiling: the best *neural* systems reach ~37.87% onset F1 on
real band audio, and this repo's monophonic live detector is far below
that — handing someone a wrong four-track score of their favourite song is
worse than a message saying what to install. This is `synth_engine.py`'s
posture exactly (#111: refuse to open rather than open filterless, since a
silently degraded instrument is what makes the closest prior art read as a
toy), and `ConversionUnavailable` mirrors `SynthUnavailable` — it carries
the install line.

### Input mode is asked, not guessed

Two paths, because the quality gradient between them is enormous:

- **Solo piano** — no separation at all, straight to `transkun` (MIT code,
  MIT weights bundled in the wheel, CPU by default, **0.9505 onset F1**).
- **Band mix** — separate, then transcribe per stem (~0.38 F1 territory,
  per #124).

The mode is **asked once at load, beside #134's quality dial**, defaulting
to band. Not auto-detected: the honest detector for "is this solo piano"
would be to run separation and look at the stems, which is both the
expensive step and — per arXiv 2605.06685, the one shipping system in this
space, which bypasses separation behind a `--piano-solo` flag precisely to
avoid spectral artifacts on already-clean signals — the step that *harms*
solo piano. A question costs the user one keystroke and cannot be wrong in
a way they can't see. `--piano`/`--band` override it for scripting.

The consequence worth stating plainly: **on solo piano this converter can
be excellent; on a band mix the whole field is currently poor.** That is a
property of September 2026, not of this design.

### Model routing

| Input | Model | Licence |
|---|---|---|
| Solo piano (whole file) | `transkun` v2 | MIT code, MIT weights in the wheel |
| `drums` stem | Beat This! as an extra tracker channel (timing oracle, #127: downbeat F1 0.699 → 0.775) | MIT code *and* weights |
| `bass`, `vocals`, `other` stems | `basic-pitch`'s `nmp.onnx` under `onnxruntime` | Apache-2.0 code *and* weights |
| Beats/downbeats (both modes) | Beat This! | MIT |

`basic-pitch`'s 230 KB ONNX graph is **vendored into the repo**, under
#139's 1 MB ceiling — #125 found the pip package will not install on
Python 3.14, so vendoring the graph and reimplementing its (also
Apache-2.0, also small) pre/post-processing is the only route to it. That
makes the entire default stack permissive, with nothing gated and nothing
non-commercial.

**Upgrades, not prerequisites:** YourMT3+ (0.5938 F1) if the maintainer
clarifies the licence; MuScriptor (likely the accuracy leader — 11,000+
hours of *real* audio, the only such model) as an expert-only backend with
user-supplied weights, since its CC BY-NC 4.0 weights sit behind a Hugging
Face account plus a licence click and therefore can never auto-download.
Both are deliberately outside the default path: "easy install for anyone,
not just computer geeks" is a stated product goal, and a backend requiring
an HF token is the opposite of it.

**A stem no model covers well is still transcribed, and its track marked
low-confidence** — never dropped. The output is editable by design, so an
approximate track a human corrects beats a missing one, and a confidence
marker is the honest way to say which is which.

### The seam

`transcribe_backends.py`, modelled directly on `detection_backends.py`
(the seam `docs/research/architecture-modernization-plan.md` §3.1 already
established for the live path, and the reason swapping a live detector is a
config change rather than surgery). Three `typing.Protocol`s, each mirroring
the return shape its callers already depend on, with nothing
algorithm-specific in the Protocol itself:

- `Separator.separate(audio, sr) -> dict[str, ndarray]`
- `NoteTranscriber.transcribe(audio, sr) -> list[NoteEvent]`
- `BeatTracker.track(audio, sr, drum_stem=None) -> Beats`

Adapters (`TranskunBackend`, `BasicPitchBackend`, `DemucsSeparator`,
`BeatThisTracker`) capture all model-specific config at `__init__` rather
than threading it through every call, and import their heavy dependency
**lazily inside the method**, never at module scope — `synth_engine.py`'s
`_signal()` convention, so importing the module costs nothing on an install
that never converts anything. `convert.py` drives the passes.

The seam is the load-bearing decision here, more than any model choice:
it is what lets #132's harness A/B two backends against each other, which
is the mechanism by which every model choice above gets replaced by a
measured one.

### What is deliberately NOT decided: three hypotheses, not architecture

The ticket asked whether the pipeline is genuinely multi-pass and whether
ensembling earns its cost. Neither can be answered honestly yet — #132
(protocol) and #133 (corpora) are not done, so there is nothing to measure
against, and the map's own standing rule is **evaluation before
algorithm**. Deciding them now would be precisely the "comfortable story
that measurement does not support" the ticket warns against. They are
recorded as falsifiable hypotheses for the harness:

- **H1 — separation improves note accuracy.** #126 found *no published
  study* measures a separator feeding a transcriber with and without
  separation on a shared metric. The A/B is `transcribe(mix)` vs.
  `Σ transcribe(stem)` on one corpus. Note that separation survives H1's
  rejection regardless: #127's drum-stem downbeat gain is measured, and a
  multi-track deliverable structurally needs per-instrument audio.
- **H2 — transcription conditioned on a locked beat grid beats
  independent nearest-value snapping** (what this repo does today).
- **H3 — ensembling several models beats one model plus better
  post-processing.** Nothing ensembled is built; the seam makes it
  expressible if H3 survives.

### Extras

`[piano]` (transkun + torch) and `[convert]` (the full band stack:
demucs, beat-this, onnxruntime, transkun), the latter a superset. Split
because solo-piano and band-mix are genuinely two capabilities with
different quality stories and very different install costs — #126 measured
the full stack at a **1.1 GB venv** plus 81 MB of Demucs weights fetched on
first use (behind #139's terms prompt), where transkun alone is a far
smaller ask. Same granular-extra habit as `[batch]`/`[synth]`/`[sf2]`.
