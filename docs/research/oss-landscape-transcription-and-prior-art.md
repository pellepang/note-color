# OSS landscape survey: music transcription and audio-to-visual prior art

Research requested to answer a standing question this project has never
formally checked: how does note-color's from-scratch approach (hand-rolled
YIN, chroma-template chord matching, spectral multi-pitch peak-picking,
terminal ASCII notation) compare to what already exists in the wider
automatic-music-transcription (AMT) and audio-to-visual space, and is
there anything specific worth borrowing — a dependency exception, a
technique, or a validation that the current scope is already reasonable.
No ticket number yet assigned; this is landscape research, not a design
doc for one feature.

## Questions

1. How good are the best open, published full AMT systems (Spotify
   basic-pitch, Magenta Onsets-and-Frames/MT3, ByteDance's high-resolution
   piano transcription, others) at note-level transcription, and are any
   of them realistically usable as a dependency — live or offline?
2. What real-time audio-pitch-to-color/light projects already exist, and
   are any more sophisticated than note-color's own hue-by-note (or
   hue-by-fifths) mapping?
3. Does anything comparable to `tab` view's live scrolling ASCII
   sheet-music notation already exist?
4. What does the standard, well-supported approach for programmatic
   MusicXML/score export look like, given note-color's stated future
   direction toward a score-file/playback consumer (map #24)?
5. What performance ceilings/pitfalls do other Python-on-Raspberry-Pi
   real-time audio DSP projects report, and does anything apply to
   keeping note-color's <150ms latency budget healthy as features grow?

## Answers

### 1. Full AMT systems: strong on curated piano data, not built for note-color's shape of problem

**Spotify basic-pitch** (`github.com/spotify/basic-pitch`, ICASSP 2022,
arXiv 2203.09893, "A Lightweight Instrument-Agnostic Model for Polyphonic
Note Transcription and Multipitch Estimation"). Concrete, verifiable
numbers found:

- **~17,000 parameters, <20MB peak memory** — genuinely tiny by AMT
  standards (Spotify's own engineering blog post, confirmed independently
  by multiple secondary sources).
- Jointly predicts frame-wise onsets, multipitch, and note activations
  (multi-task, same general shape as note-color's own
  onset/pitch/duration split across separate modules, just learned
  jointly rather than hand-designed per-signal).
- Explicitly **instrument-agnostic** — trades some piano-specific accuracy
  for generalizing across instrument types (vocals, guitar, etc.), unlike
  the piano-only systems below.
- Ships as **TensorFlow (recommended), CoreML (macOS default), TensorFlow
  Lite (Linux default), or ONNX (Windows default)** — i.e., it is a
  neural-network runtime dependency, not a NumPy-only algorithm.
- Spotify's own claim: "faster than real time on most modern computers."
  **No published number for Raspberry Pi-class hardware was found** —
  every benchmark reference located was desktop/cloud-class. This matters:
  basic-pitch is benchmarked and shipped as a **batch, file-in/file-out
  tool** (feed it an audio file, get MIDI back), not as a streaming/
  frame-by-frame online model — there is no evidence it has ever been
  validated for genuine low-latency live use, on any hardware.
- Could not independently verify a specific F1 table (MAESTRO/MAPS/
  GuitarSet) from primary sources within this research pass — Spotify's
  own materials describe results qualitatively ("frame-level accuracy
  only marginally below specialized SOTA systems," "substantially better
  than baseline") rather than in a single obviously-fetchable table. Note
  this as unverified-to-a-number, not fabricated.

**ByteDance high-resolution piano transcription**
(`github.com/bytedance/piano_transcription`, Kong et al. 2020,
arXiv 2010.01815) — a **verified, sourced number**: on the MAESTRO test
set, **onset F1 = 96.72%**, beating the prior Onsets-and-Frames baseline's
**94.80%**; **onset+offset F1 = 82.47%**; **onset+offset+velocity F1 =
80.92%**; pedal-onset F1 = 91.86% (first published pedal benchmark on
MAESTRO). This is piano-only, trained/evaluated on MAESTRO's Disklavier
recordings (studio-quality, single instrument, ~172 hours) — a much
narrower and cleaner target than note-color's live-mic-in-a-room,
any-instrument, chord+rhythm-aware scope.

**Google Magenta Onsets-and-Frames / MT3.** Onsets-and-Frames (2018) is
the piano-transcription baseline the ByteDance numbers above beat. MT3
(2021, arXiv 2111.03017) generalizes to **multi-instrument** transcription
via a T5-style sequence-to-sequence transformer, and "consistently
outperforms baseline systems... across all metrics and datasets" in its
own paper — but multiple community comparisons note it is not clearly
*better* than Onsets-and-Frames specifically on piano. More importantly
for note-color: **MT3 has no shipped real-time/streaming inference path.**
A 2026-era community inference toolkit for the MT3 family
(`openmirlab/mt3-infer`) still lists "ONNX export and streaming inference"
as a *future roadmap item*, not a current capability — confirming MT3 is
architecturally a batch/offline transformer, unsuitable for a live pipeline
regardless of hardware.

**Omnizart** was surfaced in one secondary source (an AI-summarized search
result, not independently confirmed against Omnizart's own repo/paper in
this pass) citing **79.57% piano note-level F1** — included here for
completeness but flagged as the weakest-sourced number in this survey;
treat as approximate, not authoritative.

**Bottom line for Q1:** state-of-the-art neural AMT (ByteDance,
Onsets-and-Frames, MT3) is piano-focused, trained/measured on
studio-quality single-instrument data, and — with the partial exception
of basic-pitch — architecturally batch-oriented. None of them are proven,
published, low-latency, streaming, Pi-plausible tools. basic-pitch is the
only one that's small and portable enough to even consider, but it is
still shipped/benchmarked as file-in/file-out, requires a neural-runtime
dependency (TF/TFLite/CoreML/ONNX) note-color has deliberately avoided so
far, and has no demonstrated real-time streaming numbers on any hardware,
let alone Pi-class. See Synthesis for where (if anywhere) this fits.

### 2. Real-time audio-to-color/light prior art: niche, mostly simpler than note-color, with one striking direct precedent

Concrete projects found:

- **`chromesthesia`** (`github.com/fredriklindberg/chromesthesia`) — a
  real Python/NumPy/PyAudio real-time sound visualizer with multiple
  output backends (on-screen, DMX lighting, Shadertoy). Maps frequency
  content to color/light live, but has **no chord awareness, no note
  smoothing/debounce, no rhythm tracking** — closer to a spectrum analyzer
  with color output than a pitch-to-note-to-color pipeline.
- **Synesthesia Lens**, **synesthesia-audio-visualizer**, **VR
  music-visualizer** projects (P5.js/ML5.js or MIDI-to-HSL) — hobbyist/art
  projects doing direct frequency-or-MIDI-to-HSL mapping, generally
  simpler than note-color's per-note debounced color state, and none
  found with chord-template matching or a jazz-symbol chord vocabulary.
- **Chord Colourizer: A Near Real-Time System for Visualizing Musical Key**
  (arXiv 2510.10173, 2025) — the closest *academic* peer found. Uses
  Constant-Q Transform chroma features + threshold filtering to estimate
  chords, colors a GUI keyboard and an Arduino/LED physical display via
  **Newton's historical color wheel**, and explicitly flags "slight
  latency" as an accepted limitation. Critically, **it can only detect
  triads — no sevenths, no augmented/diminished chords** — a materially
  smaller vocabulary than note-color's ~360-template (30 qualities × 12
  roots) chord dictionary with jazz notation (Δ7, ø7, °7, +, slash
  chords). This is a genuine, recent, peer-reviewed data point that
  note-color's chord-mode scope (up to 6 simultaneous notes, full jazz
  quality vocabulary, bass-aware slash naming) is **more sophisticated
  than published 2025 academic prior art** in this specific niche, not
  less.
- **Stephen Malinowski's Music Animation Machine** (since 1974, first
  software version 1985, `musanim.com`) — not open-source, not a live
  microphone tool (it animates pre-existing MIDI/scores), but directly
  relevant prior art for the *color choice itself*: Malinowski
  independently converged on **shifting hue by the circle of fifths** as
  his color scheme for tonality/harmony — the exact technique note-color
  already ships as `--color-scheme fifths` and always uses for `wheel`/
  `tab`. Alexander Scriabin's *clavier à lumières* (1910, `Prometheus:
  Poem of Fire`) is the same idea a century earlier, itself explicitly
  built on the circle of fifths plus Newton's color-wheel ordering (not
  true involuntary chromesthesia, but a deliberate designed system).
  **This is a real validation, not just a nice historical footnote**:
  note-color's fifths-based hue mapping has ~115 years of independent
  precedent converging on the same idea, from a completely different
  motivation (visualizing harmonic relationships) than "just picking
  pretty colors."

**Bottom line for Q2:** this is a genuinely niche space. Nothing found
combines live mic input + debounced note smoothing + chord-template
naming + multiple simultaneous output views (fill/wheel/GUI/tab) the way
note-color does. The one close 2025 academic peer (Chord Colourizer) is
narrower in chord vocabulary and explicitly latency-apologetic. Malinowski/
Scriabin validate the fifths color scheme's theoretical grounding, not its
implementation.

### 3. Live ASCII terminal music notation: no comparable prior art found

Searched for curses/terminal-based live sheet-music or tab rendering.
Found:

- **ASCII tab** (Wikipedia) — a decades-old *static text file format*
  convention for guitar tab (numbers on string-lines), the format
  countless tab websites use — but it's a file format, not a live
  rendering tool, and it's tablature (fret positions), not staff notation.
- **gtrsnipe**, **NotaGen**, **FATpick** — guitar-transcription/practice
  tools; FATpick gives real-time pitch/rhythm feedback while playing
  along, but as a note-hit/miss overlay, not scrolling staff notation.
- General curses/asciimatics documentation — confirms the terminal-UI
  tooling note-color already uses (raw ANSI, not curses) is a reasonable,
  standard choice, but turned up **no existing project rendering live,
  scrolling, grand-staff sheet-music notation (noteheads, ledger lines,
  duration glyphs, barlines) in a terminal**, from live or offline audio.

**Bottom line for Q3:** this appears to be a genuinely uncommon feature —
not because it's technically exotic (it's ANSI escape codes and Unicode
notehead glyphs), but because essentially nobody in the OSS/hobbyist space
building audio-analysis or visualization tools has chosen the terminal as
the notation-display surface. `tab`'s scrolling grand-staff view is one of
note-color's most distinctive, least-precedented features, not a
reinvention of something well-trodden.

### 4. MusicXML/score export tooling: `music21` is the clear standard-library answer for note-color's stated future direction

Four tools surveyed, each with a different role:

- **`music21`** (MIT-licensed, pure Python, MIT's own long-maintained
  toolkit) — reads/writes MusicXML natively, plus MIDI, Humdrum,
  Lilypond, ABC, braille, and more; designed exactly for "generate/
  manipulate symbolic music data programmatically." This is the
  general-purpose Python library for exactly the shape of problem map
  #24 describes (a score-file/playback consumer downstream of
  note-color's own detected notes+durations+chords). No native
  audio-rendering/build-toolchain dependency required just to emit
  MusicXML.
- **Abjad** (`github.com/Abjad/abjad`) — a Python API specifically for
  *formalized score control*, built on top of LilyPond as its rendering
  backend. More powerful for fine-grained engraving control
  (voice-leading, tuplets, cross-staff notation) but requires a working
  LilyPond installation to actually render anything visual — a real
  external-binary dependency, working against note-color's
  "no build toolchain" constraint if visual rendering (not just
  MusicXML text export) were ever wanted.
- **LilyPond** — the underlying text-based engraving *language/engine*
  both Abjad and (optionally) music21 can target; not a Python library
  itself, a separate compiled program.
- **Verovio** — a C++/WebAssembly toolkit for rendering MEI/MusicXML to
  SVG, aimed squarely at **web** display. Explicitly the wrong tool given
  note-color's standing "Not a web app" constraint.

**Bottom line for Q4:** if/when map #24's score-file/playback direction is
picked up, **`music21` is the standard, well-supported, pure-Python choice
for MusicXML generation** — it doesn't require LilyPond or a web renderer
just to produce a valid `.musicxml` file that any external notation
program (MuseScore, etc.) can open. Abjad/LilyPond only become relevant if
note-color ever wants to *render* engraved notation itself (out of scope
today — `tab`'s own ANSI rendering already covers the "look at notation"
need live).

### 5. Raspberry-Pi-class Python real-time audio DSP: numpy vectorization is already the standard first move; numba/cython are the next lever, not yet needed here

- Multiple sources (academic — MDPI's "Programming Real-Time Sound in
  Python," and hobbyist Pi-audio-DSP projects) converge on the same
  pattern note-color already follows: **push the hot per-hop loop into
  vectorized NumPy/SciPy first**; only reach for **Numba (JIT) or Cython**
  when a specific loop genuinely can't be vectorized. No project surveyed
  reported needing anything more exotic than that pair for real-time
  audio DSP on a Pi.
- Concrete Pi-class real-time audio latency figures found: a Raspberry Pi
  4/5 comfortably runs lightweight real-time pitch-shifting/effects at
  **20-40ms latency**, well inside note-color's own <150ms end-to-end
  target — a useful sanity-check data point that the *hardware* isn't the
  limiting factor for this class of DSP, consistent with note-color's own
  measured ~3ms/hop chord-pipeline cost on Pi Zero 2 W (per this repo's
  own `CLAUDE.md`).
- By contrast, **neural-network inference** on Pi-class hardware is
  reported as meaningfully more constrained: pure CPU inference on a Pi 5
  tops out around 5-13 FPS for lightweight vision models in the sources
  found — no equivalent number exists for basic-pitch specifically, but
  this is a real, general signal that swapping any of note-color's
  hand-rolled NumPy DSP for a neural model would reintroduce exactly the
  kind of hardware-portability risk this project has consistently avoided
  (see `CLAUDE.md`'s existing "hand-rolled YIN, not aubio/librosa — wheel/
  dependency risk on Pi" rationale, and the librosa-isolation rule).
- **`aubio`** (`aubio.org`, C library with a Python/NumPy-array binding)
  is worth naming explicitly here even though it wasn't re-benchmarked:
  it is explicitly designed as a **causal, real-time-safe** pitch/onset/
  tempo library — the same shape of tool note-color's own hand-rolled YIN
  fills — and is the most direct "why not just use an existing library"
  alternative to the from-scratch DSP stack. `CLAUDE.md` already recorded
  a decision against it ("wheel/dependency risk on Pi"); this research
  did not re-verify whether aubio's current PyPI wheels cover arm64/Pi
  today (that would need a fresh, hardware-specific check, not a web
  search), so treat the original rejection as still the operative
  decision, not overturned by anything found here.
- **Essentia** (`essentia.upf.edu`) — a much heavier C++ MIR toolkit
  (spectral/tonal/rhythm/high-level descriptors, deep-learning inference
  hooks) than aubio; more plausible as a research/offline-analysis
  dependency than anything to add to the live per-hop path.

**Bottom line for Q5:** nothing found suggests note-color's Python+NumPy,
vectorize-the-hot-loop approach is behind best practice for this class of
embedded real-time audio DSP — it *is* the commonly reported best practice
at this scale. Numba/Cython are the well-established next lever if a
future feature's hot loop can't be vectorized, not something missing
today.

## Synthesis and recommendation

**Where note-color stands, honestly:**

- On raw note-level pitch accuracy against curated single-instrument data
  (MAESTRO-style piano), note-color's hand-rolled YIN is *not* competitive
  with SOTA neural AMT (ByteDance's 96.7% onset F1 on studio piano) — but
  that comparison is apples-to-oranges: those systems are trained/
  evaluated offline on clean single-instrument recordings, not real-time,
  any-instrument, live-mic, room-acoustics input. This project's own
  documented real-mic re-verification cycles (issues #69/#71) already
  show the honest, harder-won accuracy story a from-scratch live pipeline
  actually has — that's expected, not a gap to close by importing a
  neural model.
- In its *actual* niche — live audio → chord-aware color/notation output —
  note-color is **more feature-complete than anything found**, including
  a 2025 peer-reviewed academic system (Chord Colourizer): richer chord
  vocabulary (360 templates vs. triads-only), more simultaneous notes (6
  vs. unstated/lower), more output surfaces (fill/wheel/GUI/tab vs. one
  GUI+LED), plus rhythm/duration/tempo tracking and a live scrolling
  ASCII-notation view nothing else in this survey attempts at all. This
  is the single most important finding to relay: **note-color is
  unusually ambitious for its space, not behind**, in the sub-area it
  actually targets.
- The fifths-based color mapping isn't an arbitrary aesthetic choice —
  it has genuine, independently-derived precedent (Malinowski's Music
  Animation Machine, Scriabin's clavier à lumières), which is a real
  validation of that design decision, worth knowing if it's ever
  questioned.

**Concrete recommendations:**

1. **Do not adopt basic-pitch, MT3, or any neural AMT model for the live
   pipeline.** They're batch-oriented (MT3 has no shipped streaming
   inference at all), require a neural-runtime dependency (TF/ONNX/
   TFLite/CoreML) this project has deliberately avoided (same rationale
   already on record against aubio/librosa for the live path), and have
   no published low-latency numbers on Pi-class hardware. This isn't a
   gap — it's the correct call given this project's stated portability
   constraint.
2. **basic-pitch is worth a narrow, scoped look for `batch_transcribe.py`
   specifically, but only if polyphonic transcription accuracy on real
   recordings ever becomes a concrete, reported complaint** — not
   proactively. It's small (17k params, <20MB) and instrument-agnostic,
   and `batch_transcribe.py` already has an accepted precedent for a
   scoped exception (librosa, isolated to exactly one module) that a
   second offline-only, ONNX-runtime dependency could follow the same
   shape as. This is explicitly a "future option if needed," not a
   recommendation to add it now — no current issue reports batch
   transcription accuracy as a problem.
3. **When map #24 (score-file/playback) is picked up, use `music21` for
   MusicXML export.** It's the standard, mature, pure-Python tool for
   exactly this — no LilyPond binary or web-rendering toolkit required
   just to emit a valid `.musicxml` file, consistent with the "no build
   toolchain" constraint already governing every other dependency choice
   in this project. Abjad/LilyPond only become relevant if note-color
   ever wants to *render* engraved notation itself, which `tab`'s ANSI
   view already covers for live use.
4. **No DSP-technique gap to close.** The numpy-vectorize-first,
   numba/cython-if-needed pattern this survey found as the field's common
   approach is exactly what note-color already does throughout
   (`pitch_detect.py`, `chroma.py`, `multipitch.py`, `menu_animation.py`'s
   own documented ~7x vectorization speedup). Nothing suggests reaching
   for Numba/Cython is overdue — no current hot loop in this codebase is
   flagged as a latency problem, and Pi-class hardware measured elsewhere
   in this survey (20-40ms for comparable live audio DSP) has headroom
   under note-color's own <150ms budget.
5. **aubio remains a "revisit if" option, not a switch to make now.** If
   the hand-rolled YIN's ongoing calibration cost (the repeated
   subharmonic-margin/threshold recalibration cycles already documented
   in `CLAUDE.md`'s Key design decisions) keeps growing, aubio's mature,
   purpose-built causal pitch/onset/tempo algorithms are the most direct
   existing alternative — but this research did not re-verify aubio's
   current Pi/arm64 wheel situation (a hardware-specific check, not a web
   search), so the original wheel-risk rejection stands unless someone
   does that concrete check.

## What's unusually ambitious vs. unusually behind

- **Unusually ambitious:** chord-mode's 360-template jazz-symbol
  vocabulary and 6-note polyphony (vs. the one comparable 2025 academic
  system's triads-only scope); the live scrolling ASCII grand-staff
  notation view (no comparable prior art found at all); running the full
  rhythm/duration/tempo/chord pipeline every hop on Pi Zero 2 W-class
  hardware.
- **Honestly behind, but for a defensible reason:** raw note-level pitch
  accuracy against the best neural AMT systems on clean single-instrument
  audio — an expected, already-acknowledged tradeoff (see `CLAUDE.md`'s
  own extensive "provisional, verified only synthetically/on this dev
  machine" caveats), not a surprise this research uncovered.

## Key risks/unknowns flagged

- basic-pitch's specific F1 numbers (MAESTRO/MAPS/GuitarSet table) could
  not be pinned to a primary-source table within this research pass —
  only qualitative claims ("marginally below SOTA," "substantially better
  than baseline") were found. Anyone citing a specific basic-pitch F1
  number later should verify against the actual ICASSP 2022 paper PDF,
  not this doc.
- The Omnizart 79.57% figure came from a single AI-summarized search
  result, not an independently confirmed primary source — weakest-sourced
  number in this survey, included for completeness only.
- aubio's current PyPI wheel coverage for arm64/Raspberry Pi was not
  re-checked (would require an actual `pip install` test on target
  hardware, not a web search) — the existing `CLAUDE.md` rejection is
  presented as still-operative, not re-validated.
- No Pi-class (or any embedded-hardware) benchmark for basic-pitch's
  actual inference latency was found anywhere — its "faster than
  real-time" claim is a desktop-class claim; extrapolating it to Pi-class
  hardware would be a guess, not a finding, and this doc does not make
  that guess.

## Sources

- Spotify basic-pitch: `github.com/spotify/basic-pitch`,
  `engineering.atspotify.com/2022/06/meet-basic-pitch`, arXiv 2203.09893
  abstract.
- ByteDance high-resolution piano transcription: arXiv 2010.01815,
  `github.com/bytedance/piano_transcription`, Synced/SyncedReview
  coverage.
- Google Magenta: `github.com/magenta/magenta` (onsets_frames_transcription
  README), `github.com/magenta/mt3`, arXiv 2111.03017 (MT3),
  `github.com/openmirlab/mt3-infer` (streaming-inference roadmap status).
- Chord Colourizer: arXiv 2510.10173.
- Chromesthesia and related hobbyist projects:
  `github.com/fredriklindberg/chromesthesia`, GitHub `synesthesia` topic
  search results (jacquelinegli/synesthesia-audio-visualizer,
  jcckg/synesthesia, celia96/music-visualizer, stefanvodita/
  synesthesia-simulator).
- Music Animation Machine: `musanim.com/Background`,
  `themarginalian.org/2010/11/09/stephen-malinowski-music-animation-machine`,
  Wikipedia "Stephen Malinowski."
- Scriabin/Newton color-wheel tradition: Wikipedia "Alexander Scriabin,"
  `warrenmars.com` "Mr Mars' Musical Colour Wheel."
- ASCII tab / terminal notation: Wikipedia "ASCII tab,"
  `github.com/scottvr/gtrsnipe`, `commandlinefanatic.com` guitar-tablature
  generator series, `fatpick.com`, Python `curses`/`asciimatics` docs.
- MusicXML/score tooling: `music21.org` docs, Abjad
  (`github.com/Abjad/abjad` via search), Verovio project page, Wikipedia
  "Comparison of scorewriters."
- Raspberry Pi / embedded Python DSP: MDPI "Programming Real-Time Sound in
  Python" (2020), MDPI "Real-Time Granular Audio Processing Using
  Raspberry Pi," `github.com/dddomin3/DSPi`, VoxBooster Pi voice-changer
  writeup, general Pi 4/5 CPU-inference benchmark writeups (Medium, for
  general ML-on-Pi context, not audio-specific).
- aubio: `aubio.org`, `github.com/aubio/aubio`.
- Essentia: `essentia.upf.edu`, ISMIR/ACM MM Essentia papers (titles only,
  not fetched in full).

All searches/fetches performed via WebSearch/WebFetch in this session
(August 2026); no numbers in this document were taken from model memory
without a corresponding source above.
