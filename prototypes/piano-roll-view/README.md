# Prototype: piano-roll-as-text (Concept B)

Throwaway prototype for `docs/research/notation-and-feature-ideas.md`'s
Concept B ("ASCII guitar/chord-chart tab style", adapted to a piano-roll:
one text row per pitch lane, time flowing left-to-right). Follows this
repo's existing `prototypes/issue-42-menu-animation/` convention: a
self-contained script, imports real app modules for the math it reuses,
modifies nothing in the real source tree.

## What it demonstrates

- One row per chromatic `(pitch_class, octave)` lane, spanning
  `config.MIN_OCTAVE`..`config.MAX_OCTAVE - 1` (the same range
  `color_map.note_to_hsl()`'s octave-driven lightness gradient already
  assumes) — 48 possible lanes; the demo only prints the lanes actually
  used (plus 2 rows of padding) so the output isn't mostly blank.
- Time flows left→right in fixed 100ms columns (`COLUMN_SECONDS`), keyed
  to real elapsed seconds — not a beat-accumulator guess, matching the
  research doc's own stated advantage of this concept over the current
  `tab` view's onset-driven barline placement.
- A note's onset renders as `●`, its sustain as a run of `─` — the exact
  glyphs the research doc's own mockup used.
- Color is this app's real per-note fifths-order HSL coloring:
  `color_map.note_to_hsl(pitch_class, octave, scheme="fifths")` +
  `hsl_to_rgb255()`, the same math `terminal_tab_display._column_note_rgb()`
  uses (minus the age-fade term, since this is a static one-shot render,
  not a scrolling live loop).
- Duration is snapped to a standard note value via
  `duration_tracker.duration_class_for_beats()` — the same function the
  real batch/live rhythm pipeline already uses — and printed in a summary
  table below the grid.
- Input is a plain list of tuples shaped exactly like
  `batch_transcribe.NoteEvent` (`onset_hop`, `onset_time`, `pitch_class`,
  `octave`, `duration_hops`, `chord_name`) — a local `NoteEvent`
  namedtuple redefinition, not an import from `batch_transcribe.py`
  (which pulls in `librosa` at module scope; this prototype has no reason
  to require that dependency). The synthesized input
  (`synth_melody_and_chord()`) is a one-octave C-major scale followed by a
  held C4-E4-G4 chord — so the render exercises both a monophonic run and
  a genuine simultaneous multi-note (chord) moment on independent lanes.

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/piano-roll-view/piano_roll.py
```

Confirmed working: prints a colored, time-axis-labeled piano roll with
the ascending scale visibly stepping up one lane per note and the C-E-G
chord visibly sounding as three simultaneous colored runs starting at the
same column, followed by a plain-text duration summary.

## System architecture reasoning

**How this would integrate with / replace `terminal_tab_display.py`.**
This is a genuinely different data shape from `TabDisplay`'s current
model, not a drop-in swap. `TabDisplay.entries` is a `deque` of
`TabEntry`/`BarlineEntry` — one entry per *event* (an onset), each holding
up to `CHORD_MAX_NOTES` simultaneous notes, rendered as one narrow
scrolling column per event. A piano-roll needs the opposite: one row per
*pitch lane*, columns addressed by real time, and a note occupies a
variable-width *run* of columns rather than a single column. Concretely,
`push()`/`push_notes()`/`push_barline()`'s "append one column to a deque"
model doesn't fit — you'd instead need a fixed 2D grid (or a sparse
run-list per lane) that a note's *start* writes into and whose *sustain*
extends every render call until the note finalizes, closer in shape to
how this prototype's `render()` builds `grid[lane][col]` from a whole
event list up front. Reusing `finalize_duration()`'s "mutate the dict
already sitting in history" trick doesn't carry over cleanly either,
since here a note's live sustain needs to keep *extending* on-screen
every hop it's still sounding, not just get a `duration_class` filled in
once at the end. This would be a new `TabDisplay`-sibling class (e.g. a
`PianoRollDisplay`), not a small patch to the existing one.

**Fit against this app's real internal hop-based sampling model.** Weaker
than the tracker grid (Concept C). This prototype quantizes to a fixed
100ms wall-clock column, deliberately *not* to `config.BLOCK_SIZE`/
`config.SAMPLE_RATE`'s ~23ms hop, because 1-column-per-hop at a live
scrolling terminal width (~150-200 visible columns) would show under 5
seconds of history — usable for testing on a synthesized burst of notes,
but a real live view would almost certainly want a coarser, human-legible
time granularity, meaning it's *further* from this app's own true sampling
grid than the current `tab` view is (which pushes exactly one column per
detected onset/tick, already granular in the app's own terms). A live
implementation would need continuous non-trivial state (which lanes are
"currently sounding" and how many sustain-columns have been drawn so far
for each) recomputed or incrementally maintained every render — closer in
spirit to `TabDisplay`'s own age-fade recomputation (`_column_note_rgb()`,
recomputed fresh every frame from raw `pitch_class` + age) than to a
one-shot batch render like this prototype does.

**Fit against visual/notation-standard fidelity.** Also weaker than the
current staff view, and weaker than Concept C on directness (though not
by much). A piano roll is a real, recognized convention (this is
literally what every DAW's default note editor looks like), but it isn't
staff notation — it doesn't encode key signature, doesn't distinguish
enharmonic spelling in the same visually-conventional way a staff's
letter/space system does, and (per the research doc's own framing) a
reader who reads sheet music doesn't get pitch *names* for free from
vertical position the way a staff does; they need the row labels this
prototype already includes. What it *is* honest about, more than the
current `tab` view: duration and simultaneity read unambiguously at a
glance (a run's length *is* its duration, a chord's notes visibly start
in the same column), no combining-mark/duration-glyph interpretation
required.

**Effort/risk to build for real.** Medium. The rendering math itself
(lane assignment, run-length fill, color) is genuinely simple and already
proven by this prototype — no new hard problem there. The real cost is
architectural: a live, continuously-updating, scrolling (not fixed-window)
piano roll needs its own state-management design from something closer to
scratch than a small patch to `TabDisplay`, plus a decision about how far
back it keeps history (an analog of `scrollback_seconds`) and how a
sustained note that's still sounding when it scrolls off the left edge of
the visible window gets handled (this prototype's one-shot batch render
sidesteps that entirely — a real live version can't).

**What's gained vs. the current `tab` view.** Duration and multi-note
simultaneity become visually unambiguous without any combining-mark
composition or width-measurement machinery at all (issue #82's whole bug
class — `_char_display_width()`/`_display_width()`/`_pad_center()` in
`terminal_tab_display.py` — simply doesn't exist in this model, since
every cell here is exactly one plain glyph, one column, always). Time
itself is laid out against real elapsed seconds rather than an
onset-driven column-per-event model, so it's honest about tempo variation
and rests in a way the current view (and the tracker grid, see that
prototype's README) aren't.

**What's lost.** The current `tab` view's biggest, most deliberate design
win — the grand staff's octave-legibility (CLAUDE.md's Key design
decisions: "`tab` uses a grand staff, not single treble — manageable
ledger lines across the app's 4-octave range") — is **not** preserved by
this concept as demonstrated. A piano roll spanning the app's full
4-octave range needs up to 48 simultaneous chromatic lanes on screen
(this prototype trims to only the lanes actually used purely so the demo
output isn't mostly blank rows); a real live version showing the app's
full range genuinely would need ~48 rows of vertical terminal space, or a
scrollable/zoomable vertical window over that range — a real, unsolved
UX problem the staff's ledger-line system already handles for free within
~21 rows (`TOP_ROW`/`BOTTOM_ROW`'s existing shrink logic in
`terminal_tab_display.render()`). It also loses the octave's implicit
encoding a staff gives for free (a note's vertical *shape* relative to
the two clefs) in favor of needing an explicit row-label lookup for every
single lane, all the time — more legend-dependent, not less, than the
current staff for a reader who already knows sheet music. Also lost: any
visual encoding of accidental *spelling context* (a staff at least
implies a consistent line/space per letter; a flat chromatic lane list has
no such structure beyond the label text itself).
