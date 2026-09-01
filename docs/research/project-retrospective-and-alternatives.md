# note-color retrospective: what existing code/architecture would have changed, and what actually happened when we prototyped the alternatives

## Status update (2026-09-02)

This doc sat uncommitted for a while after being written, and two of its
three Part 2 recommendations have since actually been executed —
recording that here rather than leaving them looking like open
suggestions:

- **Recommendation 1 (adopt algorithms, not dependencies, via the
  `DetectionBackend` protocol): done.** `detection_backends.py` exists in
  the repo (see `docs/research/architecture-modernization-plan.md`'s own
  status update for the same finding, §3.1 there).
- **Recommendation 3 (ABC as a second data layer, not a rewrite): done.**
  `abc_export.py` exists, wired into `virtualnote transcribe
  --export-abc`, built as an additive export path exactly as this doc's
  Part 2 and the sibling `notation-and-feature-ideas.md` both
  recommended — not a rewrite of `TabDisplay`'s live rendering.
- **Recommendation 2 (make `terminal_screenshot.py` a standing part of
  how rendering changes get checked): not yet actionable in the
  committed repo.** `scripts/terminal_screenshot.py`, which Part 3 below
  describes as "built this session" and cites repeatedly as the tool that
  produced this doc's own screenshot evidence, was itself still sitting
  uncommitted in the working tree as of this status update, alongside
  this doc — i.e. the same "code/docs reference a file that isn't in git
  history" gap the rest of this reconciliation pass exists to catch, just
  in the opposite direction (a doc citing an uncommitted script, rather
  than committed code citing an uncommitted doc). Out of scope for this
  pass to fix (only the four `docs/research/*.md` files described in this
  reconciliation task were committed here) — flagged so a future session
  either commits that script for real or updates this note.
- The six prototypes in Part 3's table (`detection-backend-protocol/`,
  `onset-novelty-hfc/`, `abc-notation-view/`, `piano-roll-view/`,
  `tracker-grid-view/`, `session-log-and-practice-mode/`) are all present
  and committed under `prototypes/` — this doc's own evidence base is
  intact and verifiable in the repo, independent of the doc's own commit
  status.

This is an honest look back at the whole project to date: for each major
thing that got hand-built, what existing code or established architecture
pattern could have been used instead, what we'd have actually gained or
lost by doing that, and — now that six of the resulting ideas have been
built as real, runnable prototypes (see `prototypes/`) — what we actually
learned by trying them, not just theorizing about them.

Not every hand-built piece was a mistake. Some were the right call and
today's prototype work independently confirmed that. The point of this
doc is to be honest about which is which.

## Part 1 — decision-by-decision retrospective

### Pitch detection: hand-rolled YIN vs. an existing library

**What happened:** `pitch_detect.py` is a from-scratch NumPy port of the
YIN algorithm, then patched twice more (issue #69's subharmonic
correction, issue #71's noise-fallback removal), each patch requiring its
own adversarial-synthetic-signal investigation and, in #69's case, a
*second* round after a real-mic regression was found.

**What existing code could have replaced it:** `aubio` (C library, YIN +
4 other pitch methods, Python bindings), `essentia` (C++, broader DSP
suite incl. pitch/chroma/onset), or `librosa.pyin()` (pure-Python
probabilistic YIN).

**What we'd have gained:** a starting implementation already validated
against MIREX-style benchmarks, instead of bootstrapping our own accuracy
baseline from zero via live acoustic testing.

**What we'd have lost — and this is the part today's research actually
verified, not assumed:** `essentia`'s piwheels ARM build is **32-bit
(armhf) only** — a direct, concrete conflict with this project's
64-bit-Pi-only decision, confirmed by the detection-systems-survey this
session, not a generic "C++ is scary" hand-wave. `aubio` has the same
class of risk. `librosa.pyin()` avoids the wheel problem (pure Python +
NumPy/SciPy/numba) but is heavier and, per this session's
`detection-backend-protocol` prototype, isn't obviously *more* correct —
our prototype's pYIN-style backend confidently locked onto the wrong
harmonic on the exact adversarial low-register case YIN's dedicated
subharmonic-correction hack was built to catch. A generic algorithm
without this project's specific empirical tuning is not automatically
better; it's differently wrong.

**Honest verdict:** the caution was justified, not paranoia — but it was
applied as an all-or-nothing dependency decision when a middle path
existed and still exists: adopt the *algorithm* (port pYIN's core loop,
the way YIN itself was already ported) without adopting the *library*.
That's exactly what today's `detection-backend-protocol` prototype did,
and it's the one piece of "should have used existing work" that's both
real and actionable — see Part 2.

### Chord/chroma detection: hand-rolled harmonic pruning vs. prior art

**What happened:** `chroma.py`/`chord_templates.py`/`multipitch.py` are
entirely from-scratch: a hand-built Gaussian log-frequency chroma fold, a
~360-entry template dictionary, and hand-tuned harmonic-consistency
pruning that needed three separate rounds of fixes (issues #67, #68, the
still-open harmonic-collision limitation).

**What existing code could have replaced it:** NNLS chroma (Mauch/Dixon,
well-established in MIR literature), essentia's `ChordsDetection`,
madmom's chord-recognition models.

**What we'd have gained:** a chroma-folding approach with a real
published accuracy track record instead of hand-tuned-by-trial constants
(`CHORD_MATCH_THRESHOLD`, the 0.25 sigma choice, etc.).

**What we'd have lost:** the same Pi-wheel risk as above (essentia,
madmom), plus madmom's PyPI release is now confirmed stale (12+ months,
per this session's detection survey) — a live maintenance-risk signal
that wasn't visible when this project started.

**Honest verdict:** genuinely worth a scoped follow-up (an NNLS-based
chroma-fold prototype, already flagged as near-term in
`detection-systems-survey.md`) — but as an *algorithm* borrowed into this
project's own pure-NumPy code, not a dependency swap. *(Status update,
2026-09-02: this follow-up has since been prototyped —
`prototypes/nnls-chroma/`, issue #81 — see
`detection-systems-survey.md`'s own status update.)*

### Terminal rendering: raw ANSI everywhere vs. a TUI framework

**What happened:** every terminal view is hand-assembled ANSI escape
sequences, cursor positioning, and manual column-width bookkeeping. This
is the one area where the hand-rolled choice has directly, repeatedly
produced real correctness bugs: issue #82's barline drift (a combining
Unicode duration glyph consumed a different number of real terminal
columns than the code assumed), and the project owner's own words — "we
are hitting a wall with this type of displaying faster than I thought I
would" — are what triggered the `terminal-rendering-performance.md`
research pass in the first place.

**What existing code could have replaced it:** `Rich`/`Textual` (mature
Python TUI libraries with real grapheme-width handling built in),
`blessed` (already a dependency — but only for the one deliberately
scoped Settings-screen exception, not the rendering-heavy views).

**What we'd have gained:** the combining-mark width bug class structurally
can't happen if a library that already solved grapheme-width correctness
is doing the column math instead of hand-counted code-point arithmetic.

**What we'd have lost:** every terminal view becomes a framework
dependency instead of raw stdout writes — a real, if smaller, addition to
the app's runtime dependency surface, and a philosophy reversal for a
project whose CLAUDE.md explicitly frames raw ANSI as a deliberate,
repeatedly-reaffirmed choice (see the Settings-screen `blessed` exception
being called out, twice, as *scoped* and not a precedent).

**Honest verdict:** this is the strongest "should have used existing
code" case in the whole project — not because raw ANSI was unreasonable
to *start* with, but because the same bug class (glyph/column-width
miscounting) has now hit twice (issue #82, and the treble-clef-clipping
limitation is a related font/glyph-rendering issue), and a mature library
would have caught it structurally rather than requiring a live-testing
catch each time. This session's `terminal_screenshot.py` (pyte + Pillow,
built just now to actually show you these prototypes — see Part 3) is
itself indirect evidence: it took a *third* piece of infrastructure
(pyte's own correct-by-construction terminal emulation) to reliably
verify the hand-rolled rendering is right, rather than the rendering
layer being self-evidently correct.

### Music notation data model: per-column mutable dicts vs. an existing notation format

**What happened:** `TabDisplay` stores each note as a mutable dict with a
`duration_class` filled in later, built specifically for this app's
scrolling/fading/duration-glyph rendering needs — with no external notion
of "this is a bar of 4/4 with these seven notes in it," just a flat,
append-only column history.

**What existing code could have replaced it:** ABC notation — a decades-old,
plain-text, human-editable standard with an existing parser/writer this
project already has installed (`music21`, already a dependency via
`score_writer.py`).

**What we'd have gained, confirmed by actually building it this
session:** the `abc-notation-view` prototype demonstrates a real,
working hand-edit round-trip — change one note in a plain text string
(`G/2 ^F` → `A/2 ^F`), and the re-rendered preview reflects it correctly.
That is a structurally different, and structurally simpler, editing story
than mutating a live `TabDisplay` column dict.

**What we'd have lost — a real friction point found by actually trying
it, not predicted in advance:** `music21` can *read* ABC but cannot
*write* it (`stream.write("abc")` raises `SubConverterException`) — the
events→ABC direction had to be hand-rolled regardless. And ABC has no
structural bar-duration validation, so a careless hand-edit can silently
desync bar boundaries downstream rather than erroring — a real gotcha,
not a hypothetical one.

**Honest verdict:** worth adopting, but as an *additional* export/data
layer alongside the existing live renderer, not a replacement for it —
see Part 2. *(Status update, 2026-09-02: adopted — `abc_export.py`,
`virtualnote transcribe --export-abc`, exactly in this additive shape.)*

### Offline/batch analysis: uses `librosa` — this one was already right

**What happened:** `batch_transcribe.py` and `rhythm_reanalysis.py` are
the two deliberate, narrowly-scoped places this project *does* reach for
an existing library (`librosa.beat.beat_track()`), specifically because
those code paths are offline/non-causal and never touch the live,
Pi-constrained path.

**Honest verdict:** no retrospective complaint here — this is the
counter-example that shows the project's dependency caution was never
blanket "no libraries ever," it was "no libraries on the *live* path."
That's a real, working instance of the hybrid approach Part 1's other
entries argue should have been applied more often.

### Config: one flat `config.py` vs. a domain-scoped config system

**What happened:** one 302-line file covering 13 unrelated domains
(pitch, chord, rhythm, UI, keybinds, ...), plus a separate additive TOML
overlay (`config_store.py`) layered on top of it.

**What existing code could have replaced it:** a settings library
(pydantic-settings, or even just per-domain plain modules) from day one.

**Honest verdict:** this is the one area where "should have used
something else" doesn't actually hold up under scrutiny — 302 lines
across 13 domains is still one `Ctrl-F` away from anything, and this
session's architecture-modernization prototype work explicitly flagged
splitting it now as premature. Worth revisiting only once it roughly
doubles. *(Status update, 2026-09-02: `config.py` is 345 lines as of
this note — still short of the ~500-600-line trigger
`architecture-modernization-plan.md` sets; this verdict still holds
unchanged.)*

## Part 2 — if we were starting today, knowing all of this

1. **Pitch/chord detection: adopt algorithms, not dependencies.** The
   `DetectionBackend` protocol prototype (Part 3) is the mechanism —
   it lets a better-published algorithm (pYIN's approach, NNLS chroma)
   get ported into this project's own pure-NumPy code and swapped in
   behind a stable interface, without taking on aubio/essentia/madmom's
   Pi-wheel or maintenance risk. This is the single highest-leverage
   "what we'd do differently" — not because the original caution was
   wrong, but because it didn't need to also mean "hand-derive every
   fix ourselves from scratch," and now there's a concrete seam for it.
   *(Status update, 2026-09-02: done — see the Status update block at
   the top of this doc.)*

2. **Terminal rendering: verify structurally, don't just hand-test.**
   Not a framework swap (that reverses a repeatedly-reaffirmed
   philosophy for real reasons) — but the `terminal_screenshot.py` tool
   built this session (pyte + Pillow) should become a standing part of
   how rendering changes get checked, specifically *because* the raw-ANSI
   approach has no structural width-correctness guarantee the way a
   library would. *(Status update, 2026-09-02: the tool itself isn't yet
   committed to the repo — see the Status update block at the top of
   this doc — so "standing part of the process" isn't true yet in
   practice; the recommendation stands, but the prerequisite is still
   outstanding.)*

3. **Notation: add ABC as a second data layer, not a rewrite.** Same
   conclusion the notation research reached independently, now backed by
   an actual working prototype with real, specific friction documented
   (`music21`'s read-only ABC support, the bar-validation gap).
   *(Status update, 2026-09-02: done — see the Status update block at
   the top of this doc.)*

4. **Everything else (threading model, config-as-flat-file, offline-only
   librosa use) was the right call and doesn't need revisiting.** It's
   worth saying plainly: most of this project's architecture held up.
   The retrospective isn't "we should have used a framework for
   everything" — it's specifically pitch/chord algorithm provenance and
   terminal rendering correctness, two areas with a real, repeated cost
   already paid (issue #69's two-round fix, issue #82's rendering bug).

## Part 3 — the six prototypes: ups and downs

All six are real, runnable code under `prototypes/`, not sketches — each
was executed end-to-end this session, and three are shown as actual
rendered screenshots (not just described) via a new
`scripts/terminal_screenshot.py` capture tool (pyte + Pillow — see below).

| Prototype | Up | Down | Verdict |
|---|---|---|---|
| `detection-backend-protocol/` | Mechanical, low-risk seam; current YIN keeps working unmodified underneath it | The alternative backend it enables isn't automatically better — it confidently got the adversarial low-register case *wrong* where hand-tuned YIN gets it right | Build the seam now; don't assume any specific alternative backend is a win without the same adversarial testing YIN itself went through |
| `onset-novelty-hfc/` | Complex-domain novelty beat current spectral-flux on timing precision (14.3ms vs 22.0ms mean error) | HFC alone is noisy on sustained tones without extra smoothing; all three novelty functions still missed the same legato transition | Worth a real complex-domain-novelty trial; don't expect it to fix the onset-detection gaps note_smoother's multi-signal OR gate already exists to cover |
| `abc-notation-view/` | Real, working hand-edit round-trip — genuinely simpler to tweak than mutating column dicts | `music21` can't write ABC (only read); ABC has no bar-duration validation, so bad edits fail silently rather than erroring | Build as an additive export path off `batch_transcribe.NoteEvent`, following `score_writer.py`'s existing isolation pattern |
| `piano-roll-view/` | Runs cleanly, correctly colored, easier to hand-edit than combining-mark glyph math | Loses the grand-staff octave-legibility CLAUDE.md documents as a deliberate win | Not a replacement for `tab`; only worth it as an additional opt-in mode, if at all |
| `tracker-grid-view/` | Same ease-of-edit win, and naturally fits this app's own hop-based internal clock | Same octave-legibility loss, plus a real quantization-drift artifact found by actually running it (16th-note rows don't evenly divide the real hop length at 120bpm) | Same as piano-roll: opt-in extra, not a replacement |
| `session-log-and-practice-mode/` | Cheapest, highest-leverage of the six — unlocks export/practice-mode/stats; caught and fixed a real bug (onset-time vs. finalization-time) mid-build | Disk growth over long sessions; needs to be opt-in, not always-on | Build this first — everything else benefits from it existing |

*(Status update, 2026-09-02: per the Status update block at the top of
this doc, the `detection-backend-protocol` and `abc-notation-view`
"build it" verdicts above have been acted on for real —
`detection_backends.py` and `abc_export.py` respectively. The
`session-log-and-practice-mode` verdict has also been acted on: the
session-log half shipped as `session_recorder.py`/`session_player.py`/
`virtualnote replay`; the practice-mode half of that prototype's name has
not — no play-along scoring feature exists yet, see
`notation-and-feature-ideas.md`'s Feature 3. `piano-roll-view/` and
`tracker-grid-view/` remain unbuilt as shipped view modes, consistent
with their own "opt-in extra, not a replacement" verdicts never being
elevated to "build this.")*

## Screenshots

Three of the above are visual by nature and were captured as real PNG
renders of their actual terminal output (not redrawn or idealized) using
a new tool built this session, `scripts/terminal_screenshot.py`: it feeds
a captured raw-ANSI transcript through `pyte` (a pure-Python terminal
emulator — the same class of tool `docs/research/terminal-visual-capture-for-agents.md`
already recommended for exactly this purpose) and rasterizes the
resolved screen buffer with Pillow. One real bug was caught and fixed
building it: `pyte` requires CRLF line endings to reset cursor column
correctly, and this project's captured output (plain `print()` calls) is
LF-only — without normalizing that first, the images render as garbled,
column-shifted overlap. Sent separately as image attachments.
