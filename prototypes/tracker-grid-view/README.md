# Prototype: tracker-style step-sequencer grid (Concept C)

Throwaway prototype for `docs/research/notation-and-feature-ideas.md`'s
Concept C (MOD/XM/IT-style tracker grid: rows = fixed time steps, columns
= voices/channels). Same convention as
`prototypes/issue-42-menu-animation/` and the sibling
`prototypes/piano-roll-view/`: self-contained, imports real app modules
for the math it reuses, modifies nothing in the real source tree.

## What it demonstrates

- Rows are fixed time steps quantized to a sixteenth-note subdivision of
  the tempo (`ROW_SUBDIVISION = 4` rows per beat), snapped to the nearest
  whole multiple of this app's own real per-hop clock
  (`config.BLOCK_SIZE / config.SAMPLE_RATE`, ~23.2ms) via
  `_quantize_row_hops()` — a row's duration is always an integer number of
  real analysis hops, since this app has no sub-hop time resolution to
  place a note-attack at anyway.
- Columns are up to `config.CHORD_MAX_NOTES` (6) simultaneous
  voices/channels — mirroring chord mode's own `note_stack` cap exactly.
  `_assign_channels()` does simple greedy interval-graph packing (walk
  events by onset time, place each into the first channel whose previous
  occupant has already ended), the same manual-authoring convention real
  tracker software leaves to the user, generalized into an algorithm here
  since this app's events come from live detection, not hand authoring.
- Each cell is a compact ASCII token in this app's own
  `color_map.NOTE_NAMES_FIFTHS` spelling (e.g. `C-4`, `Bb4`, `F#4`) plus an
  abbreviated duration code (`4` = quarter, `8.` = dotted-eighth, etc. —
  `_DURATION_CODES`, derived from the exact same
  `duration_tracker.duration_class_for_beats()` snap the live/batch
  pipeline already uses). An empty cell prints `...` — tracker convention:
  no explicit sustain marker, a note is understood to hold until the next
  non-blank cell in that channel.
- A distinct barline row (`|--- bar`) marks measure boundaries, mirroring
  `terminal_tab_display.py`'s own separate `BarlineEntry` column type as
  a structurally different row/column kind, not a variant note cell.
- Cell text is colored with this app's real per-note fifths-order HSL
  coloring (`color_map.note_to_hsl(..., scheme="fifths")` +
  `hsl_to_rgb255()`), same as the piano-roll prototype.
- Input is the same `NoteEvent`-compatible tuple shape as the piano-roll
  prototype, fed the same synthesized C-major-scale-then-chord sequence
  (see that prototype's README for why it's a local redefinition, not an
  import from `batch_transcribe.py`).

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/tracker-grid-view/tracker_grid.py
```

Confirmed working: prints a colored, row-numbered grid with each scale
note landing in channel 1 (reused as each prior note ends before the next
begins), the C-E-G chord visibly landing on the *same* row across three
separate channels (2 more channels than the scale needed, correctly
packed), and bar-divider rows at the expected row intervals. One real,
informative wrinkle surfaced by actually running this against real
tempo/hop numbers: at 120bpm the ideal row length (125ms, a sixteenth
note) doesn't evenly divide this app's ~23.2ms hop (125/23.2 = 5.39,
rounds to 5 hops = 116.1ms/row) — so successive quarter-note rows drift
slightly off the "every 4th row" grid you'd expect from pure beat math
(e.g. landing on rows 0, 4, 9, 13, 17, 22, 26, 30 instead of a clean 0, 4,
8, 12...). This is not a bug in the prototype; it's the same class of
approximation-under-quantization this app's own barline placement already
documents as expected drift (CLAUDE.md: "barline placement is an
approximation tied to that live tempo estimate, not exact bar-for-bar
accuracy").

## System architecture reasoning

**How this would integrate with / replace `terminal_tab_display.py`.**
Also not a drop-in — like the piano-roll prototype, this needs a
different underlying data shape, but arguably a *smaller* structural
delta than the piano roll's does. `TabDisplay`'s current model (one
`TabEntry` per onset event, holding up to `CHORD_MAX_NOTES` notes) maps
fairly directly onto "one grid row per onset, up to `CHORD_MAX_NOTES`
notes across channels" — the real new pieces are (1) the channel/voice
*assignment* problem (`_assign_channels()` here), which `TabDisplay` has
never needed because it always stacks a chord's notes into one column
rather than distributing them across persistent per-voice lanes, and (2)
translating "note re-attacks this row, then keeps sounding" into
tracker-style "blank means still-sustaining" semantics for every row in
between, rather than `TabDisplay`'s current per-column independent
render. `finalize_duration()`'s "mutate the note dict already in
history" pattern *does* carry over cleanly here (a cell's duration code
is exactly the kind of thing that gets filled in once a note's duration
finalizes, same timing as today), which the piano roll's continuously-
extending-sustain model can't reuse as directly. Realistically a new
`TrackerGridDisplay` class, reusing `duration_tracker`/`DurationTracker`
untouched, with `main.py` needing new logic only for the channel
assignment (not present anywhere in today's pipeline).

**Fit against this app's real internal hop-based sampling model.**
Stronger than the piano roll, and arguably the best fit of the two
prototypes here — a tracker row *is*, by construction, an integer
multiple of this app's actual hop clock (see `_quantize_row_hops()`), so
"one row" is a real, meaningful unit in this pipeline's own terms, not an
arbitrary wall-clock bucket the way the piano roll's 100ms columns are.
The tradeoff, also surfaced directly by actually running this prototype
(see "How to run" above): row spacing is a subdivision of the *tempo*
estimate, and tempo doesn't evenly divide the hop clock in general, so
grid rows drift against true beat position by up to half a hop per row
unless the estimated bpm happens to make `beat_seconds / ROW_SUBDIVISION`
an exact hop multiple. That's a real, inherent cost of insisting on a
fixed row grid at all — a cost the current `tab` view avoids by pushing
columns on real onsets rather than a fixed subdivision, and one the piano
roll avoids by keying off wall-clock seconds rather than tempo-relative
rows.

**Fit against visual/notation-standard fidelity.** The weakest of the two
prototypes, and explicitly weaker than the current staff, matching the
research doc's own assessment: this is a real, 40-year-old convention,
but not staff notation — no key signature, no enharmonic-spelling
context, duration is implicit in row-count rather than encoded (unlike
even a duration glyph or a piano-roll run-length, a reader has to count
rows and know the tempo/subdivision to recover a note's actual length).
Its real audience is trackers' own hobbyist/musician-programmer
community, which plausibly does overlap this app's target users more than
sheet-music readers — but that's a different kind of "true," not a
music-notation one.

**Effort/risk to build for real.** Comparable to the piano roll overall,
with the risk concentrated in a different place: the rendering itself
(fixed-width ASCII cells, no Unicode/combining-mark concerns at all — see
`CELL_WIDTH`/`_cell_token()`) is close to zero-risk and this prototype
already proves it out directly. The real risk is `_assign_channels()`'s
channel-packing algorithm scaling to *live*, causal, hop-by-hop data
rather than this prototype's whole-event-list-known-up-front batch
version — a live tracker view needs to commit a note to a channel the
moment it's detected, without knowing yet how long it (or a
still-to-come simultaneous note) will last, which is a strictly harder
online-scheduling version of the same problem this prototype solves
offline. The row-quantization drift documented above is also a real
design decision a live version would need to make explicitly (re-snap
every row to the *current* live tempo estimate as it updates, accepting
visible row-width jitter — or fix row width once and accept beat drift,
the same tradeoff `TempoTracker`'s own re-estimate-every-N-hops cadence
already lives with elsewhere in this app).

**What's gained vs. the current `tab` view.** Plain ASCII throughout —
genuinely the most trivially hand-editable of every concept surveyed in
the research doc (`_DURATION_CODES`, `_cell_token()`'s format string,
`CELL_WIDTH` are all one-line tweaks, no Unicode/`wcwidth`/combining-mark
reasoning required anywhere, the issue #82 bug class simply doesn't
exist here either). Multi-voice simultaneity is explicit and structural
(each voice gets its own persistent channel/column across the whole grid)
rather than `TabDisplay`'s current "however many notes happened to be in
this one chord-mode column" — closer to how a real polyphonic
transcription (independent melodic lines, not just stacked simultaneous
notes) would actually want to be represented.

**What's lost.** Same core loss as the piano roll: the current view's
grand-staff octave-legibility (CLAUDE.md: "`tab` uses a grand staff, not
single treble — manageable ledger lines across the app's 4-octave
range") is gone. Here the loss is different in *kind*, not just degree —
this concept has no vertical pitch axis at all; pitch is encoded purely
as text inside a cell (`C-4`, `Bb4`, ...), meaning there is zero
positional/geometric pitch information on screen the way both the current
staff *and* the piano roll give for free from vertical placement. A
reader has to read every cell's text to know what pitch is sounding,
which is a bigger readability regression than the piano roll's "needs a
row-label lookup" cost. Duration's own doubly-implicit encoding (a
cell's own code, *and* separately the row-count until the next non-blank
cell in that channel) is also less immediately legible than either the
staff's duration glyphs or the piano roll's run-length — a reader must
mentally track "which channel is this note still occupying" across
several blank rows, exactly the kind of bookkeeping a tracker's own
target audience is trained to do but a casual/sheet-music-literate reader
is not.

## Honest comparison: piano roll vs. tracker grid vs. current `tab` view

| | Current `tab` (staff) | Piano roll (this doc's Concept B) | Tracker grid (this doc's Concept C) |
|---|---|---|---|
| Pitch legibility | Best — vertical position + clef, standard notation | Good — vertical position, but needs full row-label lookup, no clef/key-signature context | Worst — pitch is text-only, no positional encoding at all |
| Duration legibility | Good — real duration glyphs (symbol style) or a text suffix (name style) | Best — run-length *is* the duration, visually unambiguous | Weakest — implicit in row-count across possibly-blank cells; needs tempo/subdivision context to read at all |
| Simultaneity/chords | Good — stacked in one column, chord name shown in a header row | Best — literally simultaneous columns across separate pitch lanes | Good — separate channels, but only as legible as the channel-assignment happens to be that moment |
| Timing honesty | Approximate — barlines drift with the live tempo estimate (documented, accepted limitation) | Best — keyed to real elapsed seconds, no tempo-estimate dependency at all | Weakest of the three — row grid itself drifts against true beat position when tempo doesn't evenly divide the hop clock (see above) |
| Editability (hand-tweak the code) | Hardest — combining-mark composition, `_display_width()`/`wcwidth` reverse-engineering (issue #82) | Easy — three literal characters (`●`/`─`/` `), a `config.toml`-style knob would suffice | Easiest — plain ASCII string templates throughout, zero Unicode concerns |
| Fit to this app's real hop clock | Onset-driven, already matches the app's own event granularity | Weakest — wall-clock columns are coarser/finer than the hop clock by an arbitrary factor | Strongest — a row *is* an integer multiple of the real hop clock, by construction |
| Build effort/risk for a live version | Already built and hardened | Medium — new architecture needed for continuously-extending live sustain and unbounded vertical range | Medium — new architecture needed for live/causal channel assignment and a tempo-vs-hop-clock rounding decision |

**Overall honest take:** neither prototype is a clear win over the
current `tab` view for *this app's stated goals* (CLAUDE.md: "more true
to music notation," the user already likes the current aesthetic).
Both are more hand-editable, which is exactly the problem the research
doc's Part 1 was asked to address — but both give up the one thing
CLAUDE.md explicitly calls out as a deliberate, hard-won design win (the
grand staff's octave-legibility within a bounded ~21 rows), and neither
is meaningfully closer to *standard music notation* than what's already
shipped. The research doc's own recommendation — adopt Concept E (a
JSONL session log) as infrastructure, then Concept A (ABC notation) as an
*additional* export/import path rather than replacing the live staff
renderer — still looks like the right call after actually building and
running these two: it gets the "true to a real, standard notation
format" win without giving up the staff's pitch legibility, and without
this doc's implied cost of picking either the piano roll's unbounded
vertical range problem or the tracker grid's beat-vs-hop rounding
problem. If either prototype here were to ship, it should be as an
*additional* opt-in view mode (a new `virtualnote tab --style pianoroll`/
`--style tracker`, alongside the existing staff), not a replacement —
same framing the research doc itself already lands on for Concepts B/C.
