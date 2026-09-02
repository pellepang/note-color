# Music-theory-analysis bundle: implementation spec

Status: **draft spec, awaiting owner review.** Not yet a GitHub issue, not
yet implemented. Untracked in git on purpose.

Synthesized from wayfinder map [#24](https://github.com/pellepang/note-color/issues/24)
and its resolved children — research [#29](https://github.com/pellepang/note-color/issues/29)
(feasibility), decision [#33](https://github.com/pellepang/note-color/issues/33)
(scope/sequencing), decision [#35](https://github.com/pellepang/note-color/issues/35)
(build order) — following the same "map → research/decide children →
synthesized implementation-spec → implementation" flow #47→#55, #1→#12,
#57→#65 and #85→#98 already followed.

This is the **fifth and final** item on map #24's build sequence. The
first four — rhythm/onset detection (#47/#55), the score writer (#57/#65),
the playback engine (#32/`playback.py`), and the score editor (#85/#98
plus its hands-on follow-up) — are all implemented and merged.

---

## Problem statement

note-color can now hear a note, hear a chord, measure its duration, write
the result to MusicXML, edit that MusicXML in a terminal editor, and play
it back. What it cannot do is say anything *about* the music: what key it
is in, what a chord is doing in that key, or how the individual lines move
against each other.

Every piece of input this needs already exists:

- `chroma.fold()` produces a 12-bin pitch-class energy vector every hop,
  and `batch_transcribe.TranscriptionResult.chroma_histogram` already
  accumulates one over a whole recording.
- `score_writer.guess_key_signature()` already implements a full
  Krumhansl-Schmuckler key guess over that histogram — but it is buried in
  a MusicXML writer, returns a `music21.key.Key`, and is invisible to the
  user (it silently sets a key signature in an exported file, with no
  readout of what it guessed or how confident it was).
- `chord_templates.match()` already turns a chroma vector into a root +
  quality + bass, and `score_editor_display.chord_name_for_column()`
  already runs that recognizer against an editor column's exact pitch-class
  set via a synthetic one-hot chroma vector.
- `score_editor_state.EditorScore` is a clean, non-causal, fully edited
  sequence of columns — the "clean, already-disambiguated note sequence"
  #29 said voice-leading analysis needed and could not get from live
  `multipitch.detect()` output.

So the bundle is mostly **assembly and surfacing**, plus exactly one
genuinely new capability: **voice-identity tracking** (§5).

## Solution, in one paragraph

Three pure analysis modules — `key_detect.py`, `functional_analysis.py`,
`voice_leading.py` — plus a shared plain-text report builder
(`theory_report.py`), all of them music21-free and operating on
duck-typed column sequences rather than on `EditorScore` as a type. They
surface in three places: a render-only overlay in the score editor
(primary), a whole-score text report (`virtualnote analyze <file>`, and
`virtualnote transcribe --analyze`), and — pending the owner's answer to
open question 1 — an optional live `key=` readout in the `tab` view.

---

## Standing decisions this spec implements (not re-litigated)

Per #33, binding:

- All three pieces (key/scale, functional, voice-leading) move as **one
  bundle**, after score-format/editor work lands. That work has landed.
- Key/scale detection is **not** pulled forward as early independent live
  work. (It is no longer "early" — the gate has passed — but this spec
  still treats a live surface as optional and owner-gated, see open
  question 1.)
- Functional analysis **skips the naive live version** and goes straight
  to the score-data/phrase-context-backed version.
- Voice-leading is **confirmed in scope** for the bundle.

Per #35: this is the last subsystem in the build order; there is nothing
sequenced after it on map #24.

## Concerns with the standing decisions

One, recorded and then dropped:

Bundling all three means the "how does the score pipeline coexist with
the existing four render views" question — which map #24 lists as still
open, and which #35 only resolved for rhythm (`tab`-only) — now gets
answered for three features at once, in one ticket, by whoever implements
it. That is a lot of surfacing judgment riding on a single spec. This
document therefore makes the surfacing recommendation explicit and
separable (§7), and puts the genuinely contestable parts of it in the
open-questions list rather than burying them in a module design.

Proceeding as specified.

---

## 1. User stories

### Key/scale detection

1. As a musician with a score open in the editor, I want to see the app's
   best guess at the key the piece is in, so that I have a tonal reference
   for everything else the analysis says.
2. As a musician, I want that guess to come with a confidence signal and
   to go **blank rather than guess** when nothing correlates well, so that
   I never see a confidently wrong key — the same posture
   `chord_templates.match()` already takes below its own threshold.
3. As a musician, I want to see the runner-up keys when the top two are
   close (e.g. a piece that is ambiguous between C major and A minor), so
   that I can tell "confidently C major" from "narrowly C major over A
   minor."
4. As a musician, I want to know which notes in the piece are *outside*
   the detected scale, so that chromaticism is visible rather than
   implied.
5. As a user who ran `virtualnote transcribe`, I want the key that got
   written into the exported MusicXML file to be the same key the analysis
   reports, so that the export and the analysis can never disagree.
6. As a user of the score editor, I want the detected key to be shown
   separately from the score's *written* key signature, so that I can see
   when the two disagree (a wrongly-guessed signature from a batch export
   is exactly the case the editor exists to correct).

### Functional / Roman-numeral analysis

7. As a musician with a score open, I want each chord column labeled with
   its Roman numeral relative to the detected key, so that I can read the
   harmonic function of a progression rather than just its chord names.
8. As a musician, I want inversions reflected in the numeral (figured-bass
   digits), so that a `I` and a `I6` are distinguishable.
9. As a musician, I want chromatic chords labeled with an accidental-
   prefixed numeral (`bVI`, `#iv°`) rather than silently dropped, so that
   a borrowed chord still reads as *something* in the key.
10. As a musician, I want obvious secondary dominants (`V7/V`) recognized
    one level deep, so that the most common chromatic case in tonal music
    isn't mislabeled as a random chromatic chord.
11. As a musician, I want cadences (authentic, plagal, deceptive, half)
    flagged where they occur, so that phrase structure is visible.
12. As a musician, I want columns that don't cleanly match a chord (a
    single passing tone, a two-note dyad, a rest) left **blank** rather
    than force-labeled, so that the numerals I do see are trustworthy.
13. As a musician playing something that modulates, I want an advisory
    marker where the local key estimate stops agreeing with the global
    one, without the app claiming to have done a real modulation analysis.
14. As a developer, I want the numeral labeling to run over the same
    chord recognizer (`chord_templates.match()`) the rest of the app uses,
    so that a chord named `Db7` in `chords_only` view cannot be numbered
    as something else by the analysis.

### Voice-leading / interval analysis

15. As a composer/arranger with a score open, I want the app to work out
    which note in each chord belongs to which continuing *voice*, so that
    "the line" is a thing the app can talk about at all.
16. As a composer, I want each voice's motion between consecutive columns
    classified (step / leap / static, up / down), so that I can see how
    smooth each line is.
17. As a composer, I want each *pair* of voices' relative motion
    classified (parallel, similar, contrary, oblique) at each transition.
18. As a composer, I want parallel fifths and parallel octaves flagged,
    with the exact columns and voices involved, so that the single most
    commonly checked voice-leading rule is checked for me.
19. As a composer, I want direct (hidden) fifths/octaves, voice crossings,
    voice overlaps, and outsized leaps flagged as separate, individually
    identifiable finding types, so that I can tell a hard error from a
    stylistic note.
20. As a composer, I want to know that these findings are advisory — this
    app does not grade counterpoint and does not auto-correct anything.
21. As a musician, I want to know that voices are *inferred* from the
    column grid by minimal-motion matching, not read from real per-voice
    part data, so that I know why a hand-crossing passage might be
    analyzed as two smooth lines rather than two crossing ones.

### Cross-cutting

22. As a user, I want a whole-score analysis report I can read outside the
    editor (`virtualnote analyze <score.musicxml>`), so that analysis is
    usable non-interactively and testable without a TTY.
23. As a user running `virtualnote transcribe`, I want an `--analyze` flag
    following the exact same `None` / bare-flag / explicit-path convention
    `--write-score` and `--export-abc` already use.
24. As a user on Raspberry Pi-class hardware, I want none of this to touch
    the live analysis path or import anything heavy at process start.
25. As a developer, I want every analysis module to be pure, music21-free,
    and testable with hand-built fixtures, so that the analysis can be
    driven from editor data, batch data, or a test without dragging a
    MusicXML parser into the call graph.

---

## 2. Design rules for the whole bundle

These are the constraints every module below is built to. They are the
part of this spec most worth getting right, because they are what keeps
the bundle from quietly widening this project's dependency posture.

**R1 — The analysis core is music21-free.** No module in this bundle
imports `music21`. See §8 for the full argument against a third permitted
importer.

**R2 — The analysis core is `librosa`-free and never touches the live
path.** Nothing here imports `librosa`, `sounddevice`, `pygame`, or any
`run_terminal_*` module. `analysis_loop()`/`SessionState` never import
any of it.

**R3 — Analysis modules take duck-typed column sequences, not
`EditorScore`.** This is load-bearing, not stylistic:
`score_editor_state.py` imports `music21` at module scope, so *importing
`EditorScore` for a type annotation would drag music21 into the analysis
modules* and break R1 and R25 (usable from batch). Every analysis function
therefore accepts an object exposing:

```
column.notes           -> sequence of objects with .pitch_class (0-11) and .octave (int)
column.duration_class  -> str, one of duration_tracker.DURATION_CLASS_ORDER
```

and a score-level object exposing `.columns`, `.time_signature`,
`.key_fifths`, `.tempo_bpm`. `EditorScore`/`EditorColumn`/`EditorNote`
satisfy this by construction; so does a three-line stand-in dataclass in a
test file, and so does a small adapter over a
`batch_transcribe.TranscriptionResult` (§6.3). Document the expected shape
in each module's docstring; do **not** introduce a `typing.Protocol` for
it unless `detection_backends.py`'s precedent genuinely fits (it was
introduced there for a *pluggable seam* between real alternative
implementations — there is only one column shape here, so a Protocol would
be ceremony).

**R4 — Pure logic unit-tested, render loops smoke-tested manually.** The
repo's standing split. Every function in `key_detect.py`,
`functional_analysis.py`, `voice_leading.py`, and `theory_report.py` is
pure and unit-tested. The editor's analysis overlay rendering is
smoke-tested manually, same as `score_editor_display.render()` already is.

**R5 — Blank rather than a guess.** Every analysis output has an explicit
"no confident answer" value (`None`, or an empty cell) and callers render
it as blank. Inherited from `chord_templates.match()`'s threshold posture
and `score_writer.guess_key_signature()`'s `None`-below-threshold return.

**R6 — Every threshold is a `config.py` constant, provisional.** Same
convention as chord mode's and rhythm mode's constants. New constants are
listed in §9.

**R7 — Shared source of truth over duplication.** The Krumhansl-Kessler
profiles and Pearson helper currently living in `score_writer.py` move to
`key_detect.py` and `score_writer.guess_key_signature()` becomes a thin
wrapper over the new core (§3.2). Column-to-`ChordMatch` logic currently
inside `score_editor_display.chord_name_for_column()` moves to
`functional_analysis.py` and that display function becomes a wrapper
(§4.1). Same precedent as `DIM_LIGHTNESS`, `NOTE_NAMES_FIFTHS`, and #98's
promotion of `QUARTER_LENGTHS`/`note_hex_color()`/`pitch_for()`.

**R8 — No new runtime dependency.** In particular, no `scipy` for the
voice-matching assignment problem — see §5.3 for why exhaustive matching
under a small cap is both exact and cheaper than a dependency argument.

---

## 3. Piece 1 — Key/scale detection

### 3.1 New module: `key_detect.py`

Pure, no dependencies beyond NumPy and `color_map`/`config`.

```python
KeyEstimate = namedtuple("KeyEstimate",
    ["tonic_pc", "mode", "correlation", "fifths", "name"])
```

- `tonic_pc` — 0-11.
- `mode` — `"major"` / `"minor"`.
- `correlation` — the winning Pearson `r` (-1..1).
- `fifths` — the key-signature position (-7..7) implied by tonic+mode,
  i.e. exactly what `EditorScore.key_fifths` and
  `music21.key.KeySignature.sharps` hold. Derived from a small fixed table
  keyed on `(tonic_pc, mode)`, not computed — the enharmonic edge cases
  (a tonic of pitch class 6 is F# major at +6 or Gb major at -6) need a
  documented convention, and this project's convention is already fixed:
  spell per `NOTE_NAMES_FIFTHS`' flat bias (so pitch class 6 major → Gb,
  -6), with F# minor (+3) and the rest following from that table.
- `name` — display string, e.g. `"Bb major"`, spelled via
  `NOTE_NAMES_FIFTHS`.

Functions:

| Function | Responsibility |
|---|---|
| `estimate_key(histogram, threshold=config.KEY_GUESS_CONFIDENCE_THRESHOLD)` | Krumhansl-Schmuckler: correlate a 12-element pitch-class histogram against all 24 rotations of the KK major/minor profiles, return the best as a `KeyEstimate`, or `None` below `threshold`. This is `score_writer.guess_key_signature()`'s existing algorithm, moved here verbatim and returning a plain namedtuple instead of a `music21.key.Key`. |
| `rank_keys(histogram, limit=3)` | The same scan, returning the top `limit` `KeyEstimate`s in descending correlation, ignoring the threshold. Backs story 3 (ambiguity readout) and the report's "runners-up" line. |
| `histogram_from_columns(columns)` | Duration-weighted pitch-class histogram from a column sequence: each note contributes `QUARTER_LENGTHS[column.duration_class]` to its pitch class's bin. Duration weighting is what K-S actually assumes (the original profiles are correlated against a *duration*-weighted distribution, not a note count), and it is trivially available here where it was not in the live chroma path. **Do not import `QUARTER_LENGTHS` from `score_writer.py`** — that module imports music21 (R1); import the equivalent from `duration_tracker.py` (`_DURATION_CLASSES`' beat values, already the same numbers, promoted to a public `DURATION_BEATS` dict alongside the existing `DURATION_CLASS_ORDER` if needed). |
| `scale_pitch_classes(estimate)` | The 7 diatonic pitch classes of `estimate`'s key (natural minor; see open question 4 on harmonic/melodic minor). |
| `is_diatonic(estimate, pitch_class)` | Membership test backing story 4 / the report's chromatic-note listing. |
| `scale_degree(estimate, pitch_class)` | `(pitch_class - tonic_pc) % 12` mapped to a `(degree_number, alteration)` pair — `(5, 0)` for a diatonic dominant, `(6, -1)` for a flat submediant. The primitive `functional_analysis.py` builds numerals on. |
| `KeyAccumulator` | **Optional, gated on open question 1.** The long-timescale rolling chroma accumulator #29 named: `.update(chroma_vector) -> KeyEstimate \| None`, an exponentially-decaying sum over `config.KEY_ACCUMULATOR_SECONDS` of chroma, re-estimating every `config.KEY_UPDATE_INTERVAL_HOPS` hops (same amortization pattern `TempoTracker` already uses). Live-capable, no per-hop cost beyond a 12-element add and an occasional 24-template scan. Build this only if the owner wants the live surface. |

### 3.2 Absorbing `score_writer.guess_key_signature()`

**This is the important structural call in Chunk A, and it must be an
absorb-and-wrap, not a duplication.**

Today `score_writer.py` privately owns `_KK_MAJOR_PROFILE`,
`_KK_MINOR_PROFILE`, and `_pearson_correlation()`, and exposes
`guess_key_signature(chroma_histogram) -> music21.key.Key | None`. It is
called from exactly one place (`write_score()`).

After this change:

- The two profiles and the Pearson helper live in `key_detect.py` and are
  deleted from `score_writer.py`.
- `score_writer.guess_key_signature()` stays, with its exact current
  signature and return type (a `music21.key.Key` or `None`) — so
  `write_score()` and `tests/test_score_writer.py` are unchanged — but its
  body becomes:

  ```python
  estimate = key_detect.estimate_key(chroma_histogram)
  if estimate is None:
      return None
  return m21key.Key(NOTE_NAMES_FIFTHS[estimate.tonic_pc], estimate.mode)
  ```

Why wrap rather than move the whole thing or duplicate the profiles:

- Duplicating the profiles is exactly the drift risk this repo has already
  promoted shared constants to prevent (`DIM_LIGHTNESS`,
  `NOTE_NAMES_FIFTHS`, #98's `QUARTER_LENGTHS`). A future recalibration of
  `KEY_GUESS_CONFIDENCE_THRESHOLD` or a switch to Temperley's revised
  profiles must not silently apply to the analysis and not to the export.
  Story 5 makes this a user-visible requirement, not just hygiene.
- Moving the music21-returning function itself into `key_detect.py` is
  ruled out by R1 — that would make the analysis core a music21 importer
  for the sake of one constructor call.
- Keeping `guess_key_signature()`'s music21 return type in
  `score_writer.py` means the music21 boundary stays exactly where the two
  existing permitted importers already put it: at the MusicXML edge.

**Behavior must be bit-identical after the refactor.** `write_score()`'s
existing tests are the regression check; add one test asserting
`key_detect.estimate_key()` and `score_writer.guess_key_signature()` agree
on the same histogram (present-vs-`None` and, when present, tonic+mode).

### 3.3 Where the histogram comes from

Three sources, all producing the same 12-element shape:

- **Score data** (editor, `analyze`): `key_detect.histogram_from_columns()`
  over `EditorScore.columns` — duration-weighted, exact pitch classes, no
  audio involved.
- **Batch audio** (`transcribe --analyze`):
  `TranscriptionResult.chroma_histogram`, which already exists and is
  already what `guess_key_signature()` consumes.
- **Live** (optional): `KeyAccumulator` fed `chroma.fold()`'s per-hop
  output from `analysis_loop()`.

Note the honest asymmetry to document: the score-data histogram is a
*symbolic* distribution (each note counts its written duration once); the
audio histograms are *energy* distributions (a loud low note with strong
harmonics contributes more than a quiet high one). K-S tolerates both —
both are used in the literature — but a score and its own transcription
can legitimately produce slightly different correlations. Say so in the
report rather than pretending they're the same measurement.

---

## 4. Piece 2 — Functional / Roman-numeral analysis

### 4.1 New module: `functional_analysis.py`

Pure; imports `chord_templates`, `key_detect`, `duration_tracker`,
`config`. **Not** `score_editor_state` (R3), **not** `music21` (R1),
**not** `score_editor_display` (that would invert the UI/logic direction).

#### Column → chord, promoted out of the display layer

`score_editor_display.chord_name_for_column()` already does exactly the
right thing (synthetic one-hot chroma → `chord_templates.match()`, with an
octave-≤3 bass approximation and a `<2 distinct pitch classes → None`
rule), but it lives in a UI module and throws away everything except the
display name. Functional analysis needs the root, the quality, and the
bass.

Move its body into `functional_analysis.column_chord_match(column) ->
chord_templates.ChordMatch | None`, and reduce
`score_editor_display.chord_name_for_column()` to:

```python
match = functional_analysis.column_chord_match(column)
return match.name if match else None
```

This keeps story 14's guarantee structurally true (the `chords_only` view
and the numeral row cannot disagree, because they are the same call), and
it moves the documented deviation-from-`chroma.fold()` rationale (already
written up in docs/DECISIONS.md for #98) with the code it explains. Import
direction becomes display → analysis, which is the right way round.

`score_editor_display.py` already imports `chord_templates` directly for
this; that import goes away.

#### Public API

```python
ColumnAnalysis = namedtuple("ColumnAnalysis", [
    "column_index",
    "chord",          # chord_templates.ChordMatch | None
    "numeral",        # str | None  -- e.g. "V7", "i6", "bVI", "V7/V"
    "function",       # "tonic"/"predominant"/"dominant"/"chromatic"/None
    "local_key",      # KeyEstimate | None -- windowed estimate at this column
    "measure",        # int, 1-based
    "beat",           # float, 1-based position within the measure
])

Cadence = namedtuple("Cadence", ["column_index", "kind", "numerals"])
    # kind: "authentic" | "perfect-authentic" | "imperfect-authentic"
    #       | "plagal" | "deceptive" | "half"

ScoreAnalysis = namedtuple("ScoreAnalysis", [
    "key",              # KeyEstimate | None (global)
    "key_runners_up",   # list[KeyEstimate]
    "columns",          # list[ColumnAnalysis]
    "cadences",         # list[Cadence]
    "key_change_hints", # list[(column_index, KeyEstimate)] -- advisory only
    "chromatic_pcs",    # sorted list of pitch classes outside the global scale
])
```

| Function | Responsibility |
|---|---|
| `column_chord_match(column)` | Promoted from `score_editor_display` (above). |
| `measure_positions(score)` | `(measure, beat)` per column, from `time_signature` + a running sum of each column's beats. This is the concrete meaning of "phrase context" #33 required: measure boundaries and whole-sequence availability. Reuses the same `beats_per_bar = numerator * (4/denominator)` formula `main.run_terminal_tab()`/`run_batch_transcribe()` already use — do not invent a second one. |
| `numeral_for(chord_match, key_estimate)` | The core lookup. `(chord.root - key.tonic_pc) % 12` → degree + alteration via `key_detect.scale_degree()`; degree → Roman numeral; quality → case and symbol; `chord.bass` vs. `chord.root` → figured-bass inversion digits. Returns `None` when `chord_match` is `None`. Pure and exhaustively unit-testable — this is the function that must be right. |
| `function_for(numeral, key_estimate)` | Coarse functional bucket (tonic I/vi/iii, predominant ii/IV, dominant V/vii°, else chromatic) — backs an optional coloring of the overlay row and the report's function column. |
| `detect_secondary_dominants(column_analyses, key)` | One level deep, no chains: a major-triad-or-dominant-7th chord whose root is a perfect fifth above the *next* column's chord root, where that next chord is itself diatonic, is relabeled `V/x` or `V7/x` (`x` = the next chord's numeral). Also recognizes the leading-tone variant (`vii°7/x`) under `config.THEORY_SECONDARY_DOMINANTS` (a bool, default `True`) so it can be switched off wholesale if it proves noisy. |
| `detect_cadences(column_analyses, measure_positions)` | Numeral-sequence lookup over adjacent pairs, restricted to pairs that straddle or land on a measure boundary (a mid-measure V→I is a progression, not a cadence). Perfect vs. imperfect authentic distinguished by whether the final I has its root in the bass *and* the tonic in the top voice — the latter needs `voice_leading.py`'s soprano identification, so if Chunk C hasn't landed, emit plain `"authentic"` and refine later. Design the return type for this from the start so Chunk C can fill it in without a signature change. |
| `segment_keys(columns, window_columns, ...)` | Sliding-window `key_detect.estimate_key()` over `window_columns` columns at a time; emit a `key_change_hint` where the windowed winner differs from the global key for at least `config.THEORY_KEY_CHANGE_MIN_WINDOWS` consecutive windows. **Advisory only** — see below. |
| `analyze_score(score)` | The one entry point everything else calls: histogram → global key → per-column chords → numerals → secondary dominants → cadences → key-change hints → `ScoreAnalysis`. |

#### What "not naive" means here, and what it explicitly does not mean

#33 said skip the naive live version and build the version worth
building. #29 said the genuinely correct version is ML territory. This
spec's position on that gap:

**In scope, because they are cheap, deterministic, and the common cases in
tonal music:** whole-score duration-weighted key context; inversions;
chromatic chords labeled with accidental-prefixed numerals rather than
dropped; one-level secondary dominants; cadence detection at measure
boundaries; a windowed local-key *advisory*.

**Explicitly out of scope:** trained models, corpus-derived priors,
tonicization chains more than one level deep, authoritative modulation
analysis with a claimed pivot chord, non-chord-tone (passing/neighbor/
suspension) classification, and figured bass beyond simple triad/seventh
inversion digits. §10 restates these as non-goals.

The windowed key segmentation is the one piece near that line. Ship it
**labeled as advisory** — the report says "local key estimate differs from
the global key here (possible modulation)", never "modulates to X at bar
N" — or defer it (open question 6). It is ~30 lines and reuses
`estimate_key()` unchanged, so the cost of including it is small and the
cost of overclaiming in the wording is the real risk.

#### Honest failure modes to document, not to fix

- A column holding a passing tone against a held chord is a different
  pitch-class set from the underlying harmony, so it gets a different
  numeral or a blank. This is a real consequence of analyzing a
  column grid with no non-chord-tone model, and it is why story 12 makes
  "blank" the correct output rather than a bug.
- `chord_templates.match()`'s existing documented ambiguity (a minor 7th
  and its relative major 6th share a pitch-class set) propagates straight
  into the numerals. Do not add a second disambiguation mechanism here —
  the numeral is exactly as good as the chord name, by design.
- Two-note dyads and single notes get no chord and therefore no numeral.

---

## 5. Piece 3 — Voice-leading, and the voice-identity problem worked out

This is the section with genuinely new work in it. #29 identified voice
identity as the blocker and recommended solving it on score data rather
than live multipitch; #33 put it exactly there. Here is that problem
worked out concretely over `EditorScore` columns.

### 5.1 The problem statement, precisely

`EditorColumn.notes` is an **unordered** list of `EditorNote(pitch_class,
octave)`. Nothing in the data model says which note of column *k* is the
continuation of which note of column *k-1*. Column note counts vary
freely: a 3-note chord can be followed by a 4-note chord, a single note, or
a Rest (zero notes). Voice-leading analysis is meaningless without that
correspondence — "the alto moved up a step" presupposes an alto that
persists.

### 5.2 Why score data makes this tractable where live multipitch does not

Worth stating explicitly in the module docstring, because it is the whole
reason #33 sequenced this piece here:

- **No phantom or missing notes.** The columns are what the user entered
  or corrected in the editor — not `multipitch.detect()`'s peak-picked
  estimate, with its documented harmonic-collision recall gaps and
  in-register percussion phantoms.
- **No octave-error blips.** CLAUDE.md documents ~100ms octave errors
  during note decay on the live path; those would appear to voice-leading
  as a voice leaping an octave and back, i.e. constant spurious motion.
  Score data has none.
- **Discrete, already-segmented events.** There is no hop jitter, no
  attack/release hysteresis, no debounce. A transition is exactly one
  column boundary.
- **Non-causal.** The whole sequence is in hand, so a globally consistent
  assignment is possible and there is no "commit before you know" problem.

### 5.3 The algorithm: minimal-motion bipartite matching, exhaustive

For each adjacent column pair `(prev, cur)`:

1. Convert each note to an absolute pitch `m = octave * 12 + pitch_class`
   (the same arithmetic `score_editor_display.transpose_note_at_cursor()`
   already uses, so octave wrapping is consistent with the editor).
2. Build the cost matrix `C[i][j] = abs(m_prev[i] - m_cur[j])`, plus a
   crossing penalty (below).
3. Find the injective assignment (of the smaller set into the larger)
   minimizing total cost. Unmatched `prev` entries are **voice exits**;
   unmatched `cur` entries are **voice entrances**.

**Solve it exhaustively, not with `scipy.optimize.linear_sum_assignment`.**
Rationale, in this repo's terms: column note counts are bounded by the
same 6 that `config.CHORD_MAX_NOTES` already bounds chord-mode note stacks
to, and 6-note columns are the extreme case, not the norm. The worst-case
search is `P(6,6) = 720` assignments of trivial arithmetic per column
transition — microseconds, in an offline analysis with no latency budget
at all. Reaching for `scipy` would add a real runtime dependency (it is
currently only present transitively via `librosa`, i.e. only under the
`batch` extra) to buy an asymptotic improvement on an input that cannot
get large. That is precisely the wheel-risk/dependency tradeoff this
project already declined for `aubio` and FluidSynth. Guard it anyway:
above `config.VOICE_MATCH_MAX_NOTES` (6) notes in a column, fall back to a
greedy nearest-first pass and record that the transition was matched
greedily, so the report can say so.

**Crossing penalty.** Pure minimal-motion matching happily produces voice
crossings (two voices swapping order) whenever crossing is a semitone
cheaper. Conventional part-writing avoids crossings, so add
`config.VOICE_CROSSING_PENALTY_SEMITONES` (provisional 3.0) to the cost of
any assignment that reverses the pitch order of two matched voices. This
makes the matcher prefer the non-crossing reading unless the crossing
reading is *substantially* smoother — which is also exactly the condition
under which a real crossing is probably intended. Provisional, `config.py`,
tunable (R6).

**Rests.** A Rest column (zero notes) is a hard break: every open voice
exits at the rest, and voices restart after it. Simpler, honest, and
voice-leading rules across a rest are weak anyway. Flagged as open question
5 because it is a genuine musical judgment, not an implementation detail —
the alternative (suspend voices across a rest and resume by nearest pitch)
is a two-line change if the owner prefers it.

**Voice labels.** After matching the whole sequence, order voices by their
pitch at first entrance, highest first. With exactly 4 concurrent voices
throughout, label them `S`/`A`/`T`/`B`; otherwise `V1..Vn`. The label is
cosmetic; findings reference voice ids either way.

### 5.4 New module: `voice_leading.py`

Pure; imports `config` and NumPy only.

```python
VoiceEntry = namedtuple("VoiceEntry", ["column_index", "midi", "pitch_class", "octave"])
Voice      = namedtuple("Voice", ["voice_id", "label", "entries"])

Motion = namedtuple("Motion", [
    "column_index",      # the transition INTO this column
    "voice_id",
    "from_midi", "to_midi",
    "interval",          # signed semitones
    "kind",              # "static" | "step" | "skip" | "leap"
])

PairMotion = namedtuple("PairMotion", [
    "column_index", "upper_voice_id", "lower_voice_id",
    "relation",          # "parallel" | "similar" | "contrary" | "oblique"
    "interval_before", "interval_after",   # harmonic intervals, semitones
])

Finding = namedtuple("Finding", [
    "column_index", "kind", "voice_ids", "detail", "severity",
])
# kind: "parallel-fifths" | "parallel-octaves" | "direct-fifth"
#     | "direct-octave" | "voice-crossing" | "voice-overlap"
#     | "large-leap" | "augmented-leap"
# severity: "error" | "warning" | "note"  -- advisory labels only (story 20)

VoiceAnalysis = namedtuple("VoiceAnalysis",
    ["voices", "motions", "pair_motions", "findings", "greedy_transitions"])
```

| Function | Responsibility |
|---|---|
| `separate_voices(columns)` | §5.3's matcher over the whole column sequence → `list[Voice]`. The one genuinely new capability in this bundle. |
| `motions(voices)` | Per-voice interval classification: 0 = static, 1-2 = step, 3-4 = skip, ≥5 = leap (`config.VOICE_LEAP_SEMITONES` for the "large leap" finding threshold, provisional 12). |
| `pair_motions(voices)` | Per adjacent-column, per voice-pair relative-motion classification. Parallel = same direction, same harmonic interval; similar = same direction, different interval; contrary = opposite directions; oblique = one static. |
| `check_parallels(pair_motions)` | Parallel fifths (harmonic interval 7 before and after, both voices moving, same direction) and parallel octaves (interval 0 mod 12). The single most-asked-for check (story 18). Unisons treated as octaves. |
| `check_direct_intervals(pair_motions, voices)` | Direct/hidden fifths and octaves: similar motion *into* a perfect fifth/octave where the upper voice moves by leap. Outer voices only by default (`config.VOICE_DIRECT_OUTER_ONLY`, default `True`) — the conventional restriction, and it keeps the finding count sane. |
| `check_crossings_and_overlaps(voices)` | Crossing: a voice ends up below one nominally beneath it. Overlap: a voice moves past the *previous* position of its neighbour. Both reported, never auto-corrected. |
| `check_leaps(motions)` | Leaps larger than `VOICE_LEAP_SEMITONES`, plus augmented-interval leaps (a leap of 6 semitones spelled as an augmented 4th, or 3 spelled as an augmented 2nd) — the latter needs spelling, which the pitch-class model only approximates; keep it a `"note"`-severity finding and say so. |
| `analyze_voices(columns)` | The one entry point: separate → motions → pair motions → all checks → `VoiceAnalysis`. |

### 5.5 The limitation to write down, prominently

**These are inferred voices, not read voices.** MusicXML has a real
`<voice>` element; `score_editor_state.EditorColumn` has no representation
for it, and `load_score()` merges every part's notes at a shared offset
into one flat, unordered `notes` list. So even a genuine four-part chorale
exported from MuseScore, loaded into this editor, would arrive as columns
with its real voice assignment already discarded — and this module would
re-infer it by minimal motion, which will usually but not always agree
with the composer's own part-writing (a deliberate hand-crossing passage
is the canonical disagreement).

This connects directly to map #85's own excluded scope ("foreign
MusicXML import fidelity"). Do **not** try to fix it inside this bundle:
plumbing real `<voice>` identity through `EditorColumn`,
`save_score()`/`load_score()`, the editor's cursor model, and the Chord
builder is a score-editor data-model change of comparable size to #98
itself. Record it as a non-goal (§10), a known limitation in CLAUDE.md,
and — if the owner wants it — a future ticket.

---

## 6. Assembly and surfacing

### 6.1 New module: `theory_report.py`

Pure text builder, the analysis bundle's answer to
`credits_display.credits_lines()` / `stats_display.stats_lines()`.

| Function | Responsibility |
|---|---|
| `report_lines(score_analysis, voice_analysis=None, *, title=None)` | A list of plain strings: header (title, time signature, tempo, column/measure counts), key section (detected key + correlation + runners-up + written-vs-detected key-signature disagreement + chromatic pitch classes), harmony section (one line per measure: measure number, numerals with beat positions, chord names), cadence list, key-change advisories, and — when `voice_analysis` is given — a voices section (voice count, per-voice range and total motion) and a findings list grouped by kind. |
| `write_report(lines, path)` | Writes the lines to a text file. Mirrors `abc_export.write_abc()`'s shape exactly. |
| `overlay_cells(score_analysis, start, end, width)` | The per-column numeral strings the editor's overlay row needs, centered/truncated to `width` — the same `_pad_center()`-shaped contract `score_editor_display.render()`'s `chords_only` header row already uses. Kept here rather than in the display module so the cell text itself is unit-testable. |

ANSI coloring: keep the report plain text (no ANSI) so it is diffable and
testable, exactly like `abc_export.py`'s output and unlike `tab`'s
`dump_ansi()`. The editor overlay is where color belongs (§6.2).

### 6.2 Surface 1 (primary): the score editor overlay

A render-only toggle in the main editor view, in the same tier as `z`
(zoom) and `c` (chords_only) — pure render-thread-local state, no
`EditorScore` mutation, mirroring `tab`'s `N`/`L` toggles.

- New keybind `analysis_toggle`, default `"a"` (free: the editor's taken
  letters are `r i x u U z c b w t` plus Space, `,`, `.`, and the global
  `h`/`|`). Added to `config.DEFAULT_KEYBINDS`, `main._EDITOR_ACTIONS`,
  and `settings_display.KEYBIND_ACTIONS` — which, per its own Files-table
  entry, derives its field layout from that list's length, so no layout
  change is needed there.
- Cycles three levels, not a boolean: `off → numerals → numerals+voices`.
  A cycle rather than two separate toggles keeps the editor's key budget
  down and matches `zoom_cycle`'s existing precedent.
  - **`numerals`**: one header row above the staff (reusing the exact
    mechanism `chords_only` already renders its chord-name row with) showing
    each visible column's Roman numeral, blank where there is none.
    Coloring: see open question 10.
  - **`numerals+voices`**: the numeral row, plus a marker row directly
    beneath the staff flagging columns that carry a voice-leading finding
    (e.g. `‖5` for parallel fifths), with the full finding text for the
    column *under the cursor* shown in the help-legend line.
- Status line gains a detected-key field. **Name it something other than
  `key=`** — the editor's status line already uses `key=` for the score's
  written key signature (`score_properties_display.key_fifths_label()`),
  and shadowing it would be actively confusing given story 6 is precisely
  about seeing the two disagree. `est=Bb minor` or `detected=Bb minor`;
  implementer's call, but not `key=` (open question 11 covers whether the
  disagreement should be marked more loudly than just two adjacent
  fields).
- **Recompute policy:** `analyze_score()` over a few hundred columns is
  fast, but it is not free, and the editor's loop runs at
  `config.TERMINAL_FPS`. Cache the `ScoreAnalysis`/`VoiceAnalysis` and
  invalidate on any mutation — the editor already has an exact signal for
  this: the `dirty` flag's every set site, plus undo/redo. Concretely,
  recompute lazily on the next render after a mutation, not inside the
  keypress handler. If profiling during implementation shows even that is
  visible at a few hundred columns, recompute on an explicit refresh
  instead and say so; do **not** thread it (there is no live audio here,
  and #77's throwaway-thread machinery exists for a genuinely different
  problem).

### 6.3 Surface 2: non-interactive reports

Two CLI entry points, both following existing conventions exactly:

- **`virtualnote analyze <file> [--report PATH] [--no-voices]`** — a new
  standalone subcommand in `virtualnote.build_parser()`, handled and
  returned before `SessionState` is constructed, exactly as
  `transcribe`/`replay`/`edit` already are (it never touches audio).
  `main.run_analyze_score(path, ...)` loads the file via
  `score_editor_state.load_score()` (the one place in this flow that
  touches music21, locally imported inside the function, same as
  `run_score_editor()` already does), runs `analyze_score()` +
  `analyze_voices()`, prints `report_lines()` to stdout, and additionally
  writes them to `--report` when given. Non-interactive by design, same as
  `run_batch_transcribe()`.
- **`virtualnote transcribe <audio> --analyze [PATH]`** — bare-flag /
  explicit-path / omitted, the identical `None`/`""`/path convention
  `--write-score` and `--export-abc` use, defaulting to
  `analysis_<timestamp>.txt` next to `main.py`. Needs a small adapter
  turning a `TranscriptionResult` into the duck-typed column shape (R3):
  group `result.notes` by `onset_hop` (`run_batch_transcribe()` already
  does exactly this grouping — factor that grouping out or mirror it),
  each group becoming a column whose `duration_class` is
  `duration_class_for_beats()` of the longest member. Key comes from
  `result.chroma_histogram` via `estimate_key()` directly rather than from
  the reconstructed columns, since the audio histogram is the better
  evidence there.

Both surfaces are what make the whole bundle testable end-to-end without a
TTY — which matters, because this repo has no way to automatically test an
interactive terminal loop.

### 6.4 Surface 3 (optional, owner-gated): live `key=` in `tab`

If open question 1 comes back yes: `KeyAccumulator` on `SessionState`, fed
from `analysis_loop()`'s already-computed per-hop `chroma.fold()` output,
its estimate carried on `RenderItem` as one new appended field
(`key_estimate`, `None` until confident — the same append-don't-insert
convention #55's `duration_hops`/`bpm_estimate` followed), rendered as one
`key=<name|-->` status field in `tab` only. Roughly 60 lines total across
`key_detect.py`, `main.py`, and `terminal_tab_display.py`'s status
passthrough. No new keybind; always on once it has an estimate, same
posture as `tempo=`.

If it comes back no: `KeyAccumulator` is not built at all, and nothing in
`main.py`/`analysis_loop()`/`RenderItem` changes anywhere in this bundle.

### 6.5 Surfaces explicitly rejected

- **A fifth render view.** #35 already settled that rhythm added no new
  views, and a whole view for analysis output would need its own audio
  pipeline relationship, keybind set, and menu entry for something with no
  live component.
- **GUI / `fill` / `wheel`.** Same standing precedent chord mode and
  rhythm both follow: the GUI has no live-hotkey mechanism, and `fill`/
  `wheel` are color surfaces, not notation surfaces.
- **A separate interactive analysis *screen* in the editor** is deliberately
  not the primary recommendation — see open question 2 and the #98
  hands-on follow-up's finding that leaving the main view was unwanted
  friction.

---

## 7. Work split: three chunks

Sized for substantial, independently-landable pieces per the owner's
stated preference (a few subagent-sized chunks, not many small ones).

### Chunk A — Key/scale core, `score_writer` absorption, and the report/CLI spine

- `key_detect.py` (everything except `KeyAccumulator`, which is
  question-1-gated).
- `score_writer.guess_key_signature()` refactored to a wrapper; profiles
  and Pearson helper deleted from that module (§3.2).
- `duration_tracker.DURATION_BEATS` promoted if needed for
  `histogram_from_columns()` (R3/R1: must not import `score_writer`'s
  `QUARTER_LENGTHS`).
- `theory_report.py` with the header + key sections only (the harmony and
  voices sections land in B and C — design `report_lines()`'s signature to
  accept `None` for both from the start).
- `virtualnote analyze <file>` + `main.run_analyze_score()`, key section
  only.
- `virtualnote transcribe --analyze [PATH]` wiring + the
  `TranscriptionResult` → columns adapter.
- Tests: `tests/test_key_detect.py`, `tests/test_theory_report.py`,
  parser coverage in `tests/test_shell.py`.
- Docs: CLAUDE.md Files table + Running-it, `docs/DECISIONS.md` entry for
  the absorb-and-wrap call and the no-third-music21-importer call.

Ships real user value alone (a key readout with confidence, from a score
or an audio file) and touches no editor code.

### Chunk B — Functional analysis and the editor numeral overlay

Depends on A (needs `KeyEstimate`, `scale_degree()`, and the report
spine).

- `functional_analysis.py` in full, including `column_chord_match()`
  promoted out of `score_editor_display.py` and that module reduced to a
  wrapper.
- `theory_report.py`'s harmony/cadence/key-advisory sections.
- Editor: `analysis_toggle` keybind + the numerals overlay level + the
  detected-key status field + `settings_display.KEYBIND_ACTIONS` entry +
  help-legend entry + the recompute/cache policy (§6.2).
- Tests: `tests/test_functional_analysis.py`, extensions to
  `tests/test_theory_report.py`, `resolve_editor_action()` coverage for
  the new keybind in `tests/test_main.py`, and a regression test that
  `chord_name_for_column()` still returns exactly what it did.

### Chunk C — Voice-leading

Depends on A only (report spine). **Independent of B** — B and C can be
implemented in parallel by separate agents, with one coordination point:
`theory_report.report_lines()`'s signature, fixed in A, and
`detect_cadences()`'s perfect-vs-imperfect refinement, which C can hand
back to B's function without changing its shape (§4.1).

- `voice_leading.py` in full, including `separate_voices()`'s matcher.
- `theory_report.py`'s voices/findings sections.
- Editor: the `numerals+voices` overlay level and the under-cursor finding
  text in the help-legend line.
- `virtualnote analyze --no-voices` honored.
- Tests: `tests/test_voice_leading.py`.
- Docs: the inferred-voices limitation (§5.5) in CLAUDE.md's Known
  limitations, and a `docs/DECISIONS.md` entry for the exhaustive-matching-
  over-`scipy` call and the crossing-penalty design.

---

## 8. The music21 question, answered explicitly

music21 10.5.0 is already installed here (under the `batch` extra) and
genuinely does ship theory tooling this bundle could use:
`music21.analysis.discrete.KrumhanslSchmuckler`,
`music21.roman.romanNumeralFromChord()`, and
`music21.voiceLeading.VoiceLeadingQuartet` (which has parallel-fifth/
octave and hidden-interval checks built in). The spec must address whether
to make it a **third permitted importer**, rather than quietly assuming
either answer.

**Recommendation: no. The analysis core stays music21-free (R1).**

The argument, in this repo's own terms:

1. **The two existing exceptions are both about MusicXML I/O, and that is
   the reason they were granted.** `score_writer.py` and
   `score_editor_state.py` import music21 because parsing and emitting
   MusicXML is a thing nothing else in this stack can do, and both are at
   the file edge. #98's decision entry states the constraint the rule
   protects: music21's real one-time import cost has no business on the
   live/Pi-constrained path. Theory analysis has no equivalent uniqueness
   claim — this project already hand-rolls K-S key finding
   (`guess_key_signature()`) and already hand-rolls chord identification
   (a ~360-template matcher). Adding music21 to the analysis core would be
   the first exception granted for *convenience* rather than *capability*.
2. **It would break R3 and cost the batch surface.** Using
   `romanNumeralFromChord()` means constructing `music21.chord.Chord`
   objects per column, which couples the analysis modules to music21 at
   import time — which in turn means `transcribe --analyze` (which has no
   MusicXML in the picture at all) pays a music21 import, and every test
   file for the bundle does too.
3. **The precedent for hand-rolling when the fit is inexact already
   exists.** `abc_export.py` hand-rolls ABC serialization *specifically
   because* music21 can read ABC but not write it — the repo has already
   chosen a small hand-rolled implementation over a partial music21 fit.
4. **The output would need translating anyway.** music21's Roman numerals
   and chord spellings do not follow this project's conventions —
   flat-biased `NOTE_NAMES_FIFTHS` roots, jazz symbols (`Δ7`, `ø7`, `°7`).
   Feeding its analysis back into this app's vocabulary is a translation
   layer, which erodes most of the "we get it for free" benefit.
5. **The actual algorithms are small.** K-S is already written and working
   in this repo. Numeral lookup given key + root + quality + bass is a
   table. A parallel-fifths check is: same direction, harmonic interval 7
   before and after. None of these is where the difficulty in this bundle
   lives — the difficulty is voice identity (§5), which music21 does not
   solve for a flat column grid either.

**One narrow use that is worth allowing, and is not a production import:**
music21 as a **test-only cross-check oracle**. A `tests/` file may import
`music21` (tests already do so transitively via `score_editor_state`) to
assert that `functional_analysis.numeral_for()` agrees with
`romanNumeralFromChord()` on a set of unambiguous diatonic fixtures, and
that `check_parallels()` agrees with `VoiceLeadingQuartet` on a set of
hand-built quartets. That buys real confidence in the hand-rolled tables
at zero cost to the runtime dependency posture. Mark such tests
`pytest.mark.skipif` on music21's absence so the core suite still runs
without the `batch` extra. Open question 3 confirms this.

---

## 9. New `config.py` constants

All provisional, tunable, same convention as chord/rhythm mode's (R6).

| Constant | Suggested | Meaning |
|---|---|---|
| `KEY_GUESS_CONFIDENCE_THRESHOLD` | 0.65 (**existing**) | Reused unchanged as the analysis threshold too — the export and the analysis must agree (story 5). |
| `KEY_RUNNER_UP_MARGIN` | 0.05 | Below this correlation gap between the top two keys, the report calls the key ambiguous and shows both. |
| `THEORY_KEY_WINDOW_COLUMNS` | 16 | Sliding-window size for `segment_keys()`. |
| `THEORY_KEY_CHANGE_MIN_WINDOWS` | 3 | Consecutive disagreeing windows before a key-change advisory is emitted. |
| `THEORY_SECONDARY_DOMINANTS` | `True` | Whether one-level secondary-dominant relabeling runs at all. |
| `VOICE_MATCH_MAX_NOTES` | 6 | Above this column size, the matcher falls back to greedy (matches `CHORD_MAX_NOTES`). |
| `VOICE_CROSSING_PENALTY_SEMITONES` | 3.0 | Cost added to an assignment that crosses two voices. |
| `VOICE_LEAP_SEMITONES` | 12 | Leap size that produces a `large-leap` finding. |
| `VOICE_DIRECT_OUTER_ONLY` | `True` | Restrict direct-fifth/octave checks to the outer voice pair. |
| `KEY_ACCUMULATOR_SECONDS` | 20.0 | *Question-1-gated.* Live chroma accumulation window. |
| `KEY_UPDATE_INTERVAL_HOPS` | (as `TEMPO_UPDATE_INTERVAL_HOPS`) | *Question-1-gated.* Live re-estimation cadence. |

New keybind: `analysis_toggle`, default `"a"`, in `DEFAULT_KEYBINDS` (and
therefore remappable via the Settings screen like every other).

---

## 10. Non-goals

Explicitly not built by this bundle. Each is a real thing someone might
expect; saying no here is the point.

1. **ML / corpus-trained Roman-numeral analysis.** #29 established this is
   where the literature went and why; this project's stack (pure NumPy, no
   ML toolchain, Pi portability) is not set up for it and #33 did not ask
   for it.
2. **Authoritative modulation analysis.** Windowed local-key *advisories*
   only (§4.1), never a claimed pivot chord or a stated modulation.
3. **Non-chord-tone analysis.** No passing/neighbor/suspension/anticipation
   classification. A column is analyzed as the harmony it literally is.
4. **Figured bass beyond triad/seventh inversion digits.** No full
   figured-bass realization, no thoroughbass notation.
5. **Counterpoint grading.** Findings are advisory and unranked beyond a
   three-level severity label. No score, no "you passed species 2."
6. **Auto-correction.** Nothing in this bundle mutates an `EditorScore`.
   Analysis is read-only over the score, always. (The one possible
   exception — applying the detected key to the written key signature — is
   open question 7, and would be a single explicit user action, not an
   automatic rewrite.)
7. **Real per-voice part identity from MusicXML.** §5.5. Voices are
   inferred from the column grid; `<voice>` is not represented in
   `EditorColumn` and this bundle does not add it.
8. **Live functional or voice-leading analysis.** #33 settled both. Only
   key detection has any live pathway here, and only if open question 1
   says yes.
9. **Scales beyond major/minor** unless open question 4 says otherwise —
   K-S's profiles are major/minor by construction.
10. **Analysis surfaces in the GUI, `fill`, or `wheel`.** §6.5.
11. **Rendering analysis into the MusicXML file** (music21 can carry
    Roman-numeral and analysis markup). Out of scope: it would put
    analysis output into the score-of-record, which is a format/schema
    decision (#30's territory), not this bundle's.
12. **Tuplets, print/engraving output, and audio preview of analyzed
    passages** — all already excluded by earlier tickets and unchanged
    here.

---

## 11. Testing strategy

Following R4 and this repo's one-test-file-per-module convention.

**Fixtures.** Hand-built column sequences using a three-line stand-in
dataclass local to the test file (R3 means no `EditorScore` import is
needed, which also keeps these tests music21-free and fast). Same
"synthesize the input, no binary fixtures" convention
`tests/test_chroma.py`'s `make_tone()` established.

- `tests/test_key_detect.py`
  - A histogram built from a C-major scale correlates to C major; an
    A-minor-weighted histogram to A minor.
  - A flat/uniform histogram returns `None` (below threshold), and an
    all-zero histogram returns `None` without dividing by zero — the
    existing `_pearson_correlation()` behavior must survive the move.
  - `fifths` is correct across all 24 keys, including the enharmonic
    tie-break cases (pitch class 6 major, pitch class 3 minor).
  - `histogram_from_columns()` weights by duration: a whole note counts 4x
    a quarter.
  - `rank_keys()` orders descending and includes the relative
    major/minor as a near-runner-up for an ambiguous input.
  - **Agreement test:** `estimate_key()` and
    `score_writer.guess_key_signature()` return the same key (or both
    `None`) for a set of histograms — the regression guard for §3.2.
- `tests/test_functional_analysis.py`
  - `numeral_for()` over an exhaustive diatonic table in a few keys, major
    and minor, with and without sevenths.
  - Inversion figures: root position, first (`6`), second (`6/4`), and the
    seventh-chord figures (`6/5`, `4/3`, `4/2`).
  - Chromatic roots produce accidental-prefixed numerals (`bVI`, `bII`).
  - `column_chord_match()` reproduces `chord_name_for_column()`'s existing
    behavior exactly, including the `<2 distinct pitch classes → None`
    rule and the octave-≤3 bass approximation.
  - `measure_positions()` against a 3/4 and a 6/8 score with mixed
    durations.
  - `detect_cadences()` on a hand-built I-IV-V-I ending at a measure
    boundary; a mid-measure V-I producing no cadence.
  - `detect_secondary_dominants()` on a I-V/V-V-I in C (D7 → G7 → C).
  - `segment_keys()` on a sequence that is unambiguously C major for 20
    columns then unambiguously G major for 20.
  - Blank-not-guess: single-note and Rest columns produce `numeral=None`.
- `tests/test_voice_leading.py`
  - `separate_voices()` on a 4-voice hand-built chorale fragment produces
    exactly 4 voices with the expected pitch sequence per voice.
  - A 3-note column followed by a 4-note column produces one voice
    entrance and no crossing; the reverse produces one exit.
  - A Rest column terminates all voices (or suspends them — assert
    whichever open question 5 settles on).
  - The crossing penalty: a transition where crossing is 1 semitone
    cheaper is matched *without* crossing; a transition where it is much
    cheaper is matched *with* it.
  - `check_parallels()` on a textbook parallel-fifths pair (C-G → D-A) and
    a textbook non-example (C-G → D-B, contrary motion into a sixth).
  - `check_direct_intervals()`, `check_crossings_and_overlaps()`, and
    `check_leaps()` each with one positive and one negative fixture.
  - Determinism: the matcher returns the same assignment for the same
    input across runs (no set-iteration-order dependence).
  - **Optional music21 oracle** (`skipif` on import): `check_parallels()`
    agrees with `music21.voiceLeading.VoiceLeadingQuartet` over a set of
    hand-built quartets. See §8 / open question 3.
- `tests/test_theory_report.py`
  - `report_lines()` content assertions in the style of
    `credits_display.credits_lines()`'s tests: the key line appears, the
    runner-up line appears only when the margin is tight, the findings
    section is absent when `voice_analysis is None`.
  - `overlay_cells()` centers and truncates to the requested width.
  - `write_report()` round-trips to a `tmp_path` file.
- `tests/test_shell.py` — `virtualnote analyze <file>` and
  `transcribe --analyze` parse correctly, including the bare-flag form.
- `tests/test_main.py` — `resolve_editor_action()` maps the new
  `analysis_toggle` keybind, honors a remap, and stays case-insensitive
  like every action except `undo`/`redo`.

**Not unit-tested, smoke-tested manually** (stated per convention, with
whatever was and wasn't verified recorded in the implementation PR):
`score_editor_display.render()`'s new overlay rows, the editor's
recompute/cache timing under real editing, and `run_analyze_score()`'s
terminal output.

**End-to-end sanity check during implementation:** run `virtualnote
analyze` against the repo's own `First Test.musicxml` and against a
freshly `--write-score`-exported transcription, and eyeball the report.
This is the closest thing to real-data validation available without a TTY,
and it is exactly the check that caught #65's inexpressible-duration bug.

---

## 12. Documentation to update

Per this repo's convention — CLAUDE.md stays orientation/pointers,
`docs/DECISIONS.md` carries the rationale.

- **CLAUDE.md**: Files table entries for `key_detect.py`,
  `functional_analysis.py`, `voice_leading.py`, `theory_report.py`, plus
  amendments to `score_writer.py` (no longer owns the K-S profiles),
  `score_editor_display.py` (`chord_name_for_column()` is now a wrapper),
  `main.py` (`run_analyze_score()`), `virtualnote.py` (the `analyze`
  subcommand and `--analyze` flag), and `settings_display.py` (one more
  remappable action). Running-it section gets `virtualnote analyze` and
  `transcribe --analyze` examples. A new "Music-theory analysis" section
  in the same shape as the Score editor section, including the keybind
  table row for `analysis_toggle`. Key design decisions gets one-liners
  for: no third music21 importer, the `guess_key_signature()`
  absorb-and-wrap, exhaustive voice matching over `scipy`, and the
  advisory-only posture on modulation. Known limitations gets: inferred
  (not read) voices, non-chord-tone blindness, the propagated
  minor-7th/relative-6th chord ambiguity, and the symbolic-vs-energy
  histogram asymmetry.
- **CONTEXT.md**: a "Music-theory analysis" glossary section, at minimum:
  **Voice** (an inferred continuous line across columns, vs. MusicXML's
  `<voice>`), **Voice separation**, **Numeral**, **Function**,
  **Finding** (advisory, never auto-corrected), **Detected key** (vs. the
  score's written **key signature** — the two are different things and the
  status line shows both), **Local key advisory**.
- **`docs/DECISIONS.md`**: full rationale entries per chunk.

---

## 13. Open questions requiring the owner's decision

1. **Does this bundle get a live surface at all?** #33 deferred key
   detection until the editor landed — which it now has — but did not say
   whether the live `key=` readout it described is wanted once unblocked.
   §6.4 is a ~60-line addition (`KeyAccumulator` + one `RenderItem` field
   + one `tab` status field) that touches `analysis_loop()`, which nothing
   else in this bundle does. **Recommendation: yes, as a small optional
   tail of Chunk A** — it is the cheapest user-visible piece here and the
   only one that works while actually playing. But it is the one place
   this bundle would touch the live path, so it is the owner's call.

2. **Editor overlay only, or overlay plus a read-only full-report screen?**
   The overlay (§6.2) is cramped: a numeral fits in a column cell, but a
   voice-leading findings list does not. A scrollable read-only report
   screen inside the editor would fix that — but the #98 hands-on
   follow-up explicitly reversed a separate screen (Score properties) back
   into the main view because leaving it was unwanted friction.
   **Recommendation: overlay + the `analyze` CLI report, no in-editor
   screen in v1** — the friction finding was about editing, but the
   cheapest way to honor it is not to add a screen until the overlay
   proves insufficient in real use.

3. **Confirm no third music21 importer, and confirm the test-only oracle.**
   §8 argues no production import, plus optional `skipif`-guarded music21
   cross-check tests. Needs a yes/no, since it sets precedent for anything
   later that wants music21's non-I/O tooling.

4. **Scale vocabulary: major/minor only, or modes too?** K-S's profiles
   are inherently major/minor. Dorian/Mixolydian/pentatonic/blues
   detection would need either extra profiles (available in the
   literature, quality varies) or a different method. "Key/scale
   detection" in #24's wording could mean either. **Recommendation:
   major/minor in v1**, with the report noting when the tonic is confident
   but the mode is close, which is where a modal piece usually shows up.

5. **Does a Rest end a voice, or suspend it?** §5.3 recommends *ends*
   (simpler, and cross-rest voice-leading rules are weak). The alternative
   — voices survive a rest and resume by nearest pitch — is a two-line
   change and is arguably more musical for a short rest. This is a genuine
   musical judgment, not an implementation detail.

6. **Ship the windowed local-key advisory in v1, or defer it?** It is the
   one piece adjacent to the modulation analysis #29 called ML territory.
   It is cheap (reuses `estimate_key()` unchanged) and, worded as an
   advisory, honest. **Recommendation: ship it, advisory-worded.** The
   risk is entirely in the wording, not the code.

7. **Should the editor offer "apply the detected key to the score's key
   signature"?** Story 6 makes the disagreement visible; acting on it is
   one more keypress and would be genuinely useful for correcting a bad
   `--write-score` guess. But it is the only *mutation* anywhere in this
   bundle, which otherwise is strictly read-only over the score (non-goal
   6). Yes/no, and if yes, which key.

8. **`virtualnote analyze`: stdout, a file, or both?** §6.3 proposes
   printing to stdout always and additionally writing `--report PATH` when
   given — which differs from `transcribe`'s convention (always write a
   file, print nothing). Printing suits a short report and a terminal-first
   app; the file suits keeping a record. Confirm the mix.

9. **Blank vs. a visible "no analysis" marker.** R5 says blank, matching
   `chord_templates.match()`. But a page of numerals with silent gaps can
   read as "the analysis is broken" rather than "this column has no chord."
   A faint `·` in unanalyzed columns would distinguish the two.
   **Recommendation: blank in the report, a dim `·` in the editor overlay**
   — but this is a taste call.

10. **Colour the numeral row by harmonic function, or by chord-root
    fifths hue?** Function coloring (tonic/predominant/dominant as three
    hues) is more informative for analysis; root-fifths coloring keeps the
    editor's "a note is always this color" identity intact, which this app
    has been strict about everywhere else. **Recommendation: root-fifths
    hue** (coherence beats a fourth colour language), but the alternative
    is defensible and is the owner's aesthetic call.

11. **Status-line naming when the detected key and the written key
    signature disagree.** §6.2 says the detected key must not be called
    `key=` (that name is taken by the signature). Beyond naming: should a
    disagreement be marked more loudly than two adjacent fields — an
    inline `≠` marker, or a colour change on the field? Story 6 says the
    disagreement is the point; how loud it should be is taste.
