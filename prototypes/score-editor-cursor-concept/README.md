# Prototype: score-editor cursor and view-mode interaction concepts

Throwaway prototype for wayfinder ticket
[#87](https://github.com/pellepang/note-color/issues/87) (map
[#85](https://github.com/pellepang/note-color/issues/85), the score
editor). Same convention as `prototypes/tracker-grid-view/` and the
sibling `prototypes/piano-roll-view/`: self-contained, imports real app
modules for the rendering math it reuses, modifies nothing in the real
source tree, no real mutation (cursor movement and view-mode toggles
only).

## The question

`terminal_tab_display.py`'s `TabDisplay` is a real, working terminal
staff renderer — but it's a *scrolling, append-only, read-only* live
view: new columns arrive on the right as notes are detected, nothing is
ever addressed by position or mutated. The score editor (map #85) needs
the opposite shape: a *loaded-once, random-access, editable* buffer with
a cursor. This prototype raises the fidelity of that discussion with
three structurally different concrete concepts, switchable live.

## What it demonstrates

Three radically different answers to "how does an editable score look
and behave in the terminal", all built on the same 8-column synthesized
sample score (a short melody sliding into a C major triad, then an A
minor triad) held as plain per-note dicts (pitch_class/octave), not
music21 objects — matching #86's just-resolved finding that the editor's
live in-memory model should be a simple intermediate structure.

- **Variant A — staff buffer + cursor overlay.** Extends
  `terminal_tab_display.py`'s real rendering primitives (`NOTEHEAD_GLYPH`,
  `SYMBOL_ACCIDENTALS`, `staff_map.staff_row()`, fifths-order coloring)
  into a *fixed* grand-staff grid instead of a scrolling one, with an
  inverse-video cursor over the current note (Left/Right moves columns,
  higher pitch = up on screen). `c` collapses the whole staff into a
  one-line lead-sheet (chord names / single-note letters only) — the
  "chords-only" density mode. `+`/`-` step through four zoom levels (bare
  notehead → +letter → +octave → +duration abbreviation). This is the
  "reuse the existing renderer, just make it addressable" answer, and the
  one real feedback converged on.

  **`m` cycles three view modes** (real feedback: editing a note's pitch
  used to require drilling into a separate column view just to nudge it;
  wanted that inline, plus a cursor that isn't tied to existing notes at
  all):
  - `select` (default) — Up/Down browses which of the column's existing
    tones is highlighted. No mutation.
  - `transpose` — Up/Down moves the *highlighted* tone's own pitch a
    semitone, live, right in the main melody view — no drill-in needed
    for a simple pitch nudge.
  - `freeview` — the cursor decouples from existing notes entirely:
    Up/Down reaches *any* staff row, empty or not (shown as a `+` marker
    when nothing's there), Left/Right still moves between columns without
    resetting the row (so you can scan across the melody at a fixed pitch
    height). **Space** places a note at the highlighted row if it's
    empty, or removes the one that's there if not — refuses to empty a
    column down to zero notes.

  **Column drill-in editor.** `Enter` on any column opens a dedicated
  small-staff editor for just that column:
  - `m` switches focus between a single tone (Left/Right picks which one,
    Up/Down transposes it a semitone) and the whole chord (Up/Down cycles
    **inversions** — the lowest note jumps an octave to become the new
    highest, or vice versa; the chord's identity is re-derived from its
    pitch-class set after every move, so "still recognized as: C" survives
    however many inversions you cycle through).
  - `t`/`s` open the **chord builder** — five slot-machine-style reels
    (real feedback: "a type ability and an arrow-key controlled slot
    machine thing... more columns assigned to the different parts of the
    chord"). Left/Right picks which reel has focus, Up/Down spins it, and
    every spin applies live — no confirm step, the column's notes update
    as you turn the reel.
    - **ROOT** is ordered around the circle of fifths (C, G, D, A, E, B,
      F#, Db, Ab, Eb, Bb, F — this app's own `color_map.fifths_index()`,
      the same order the wheel view uses), not chromatically.
    - **QUALITY** is a fast preset shortcut, grouped by family — triads
      (major, minor, diminished, augmented), then sevenths (dominant,
      major7, minor7, diminished7), then sus chords. Spinning or typing
      it fills the three reels below it in one move.
    - **3RD / 5TH / 7TH** are the chord's individual tones, each its own
      reel (`(none)`, then the real interval options — 3RD also covers
      sus2/sus4 in the third's "slot", since a sus chord replaces rather
      than adds to it) — build a chord tone-by-tone, including shapes no
      preset covers (a bare root+5th power chord, a `Cm#5`, ...). Moving
      one of these doesn't touch the quality reel's own position, since
      it's just a "last preset used" shortcut, not a live readout.

    Typing works on every reel: on ROOT, a natural letter (`F`) jumps
    straight there and `#`/`b` right after nudges it a semitone; on the
    other four, typing a token (`m7`, `dim`, `b5`, `sus4`, ...) jumps that
    reel, auto-committing the instant the buffer can't mean anything else
    (`7` alone commits immediately on QUALITY; `m` waits, since it could
    still become `maj`/`min`/`m7`/`min7` — Enter force-commits whatever's
    currently an exact match if you don't want to keep typing). `b` leaves
    the builder back to the single-column editor.
  - `b` (from the single-column editor, not the builder) commits the
    column's edits back into the live score buffer and returns to the
    main staff view.

  This is a small, illustrative chord-quality table
  (`CHORD_QUALITIES`/`guess_chord_label()`), not this app's real
  ~360-template `chord_templates.py` dictionary — that module only ever
  goes chroma → name (for *recognizing* a chord from live audio); nothing
  in the real codebase does name → notes (for *constructing* one by hand)
  yet, which is what an editor actually needs here.
- **Variant B — pitch/time grid + staff preview strip.** Splits editing
  from display: a tracker-style grid (rows = pitches actually used, high
  to low; columns = time) is the *editing* surface — cursor is a cell
  (Left/Right = time, Up/Down = pitch row), simpler random-access
  addressing than staff coordinates. A one-line read-only notehead strip
  underneath is the *display* surface, always showing how the current
  state maps back to real notation. `c` collapses the grid to one
  chord-name-per-column row; `+`/`-` change how many pitch rows are
  visible (a literal zoom, not just a cosmetic one).
- **Variant C — numbered list buffer, staff preview on demand.** No
  spatial glyph cursor at all by default — the score is a plain numbered
  text buffer (`1: C4 -- quarter`, `4: C4 E4 G4 (C) -- half`, ...),
  cursor is just "which line", moved with Up/Down. `c` collapses a chord
  line to its chord name. `p` toggles a small read-only staff-position
  readout for the current line on demand, rather than keeping notation
  on screen at all times. This is the "command/list-driven editor"
  answer — closer to a modal text editor than a spatial score view.

Global: `v` cycles between the three variants live; `q`/Ctrl+C quits.

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/score-editor-cursor-concept/demo.py
```

Needs a real interactive TTY (prints a message and exits cleanly if run
under piped/non-interactive input, same convention `main.RawKeys` already
follows elsewhere in this app).
