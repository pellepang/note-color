# The piano roll editing surface reversed into its own panel (issue #155 reopened by hands-on use)

Issue #155 decided the piano roll would zoom in on one track's row of the
same `QGraphicsView`/`QGraphicsScene` arrange canvas, expanding that row's
height in place, rather than a separate dockable pane. That decision is
reversed. The trigger was the first real hands-on session with milestone 2's
keyboard-driven editing (ghost cursor, arrow-key nudge, Space add/delete,
Enter mode-switch): the project owner tried it and reported the key bindings
felt like they overlapped with something else and asked for the piano roll
to live in its own resizable bottom panel with buttons mirroring the
shortcuts, rather than editing in place on the main track view.

### What #155 got wrong in practice

The inline approach routed every piano-roll shortcut through
`StudioWindow.keyPressEvent()` — the same method that already owned the main
window's always-live transport/track-select bindings (Up/Down cycles the
selected track, Left/Right seeks the playhead, Space plays/stops). Whether a
piano roll happened to be "open" was tracked as scene state
(`ArrangeScene.piano_roll_row`/`selected_note`/`cursor`), and the window's
one `keyPressEvent()` branched on that state to decide what an arrow key or
Space meant *this time*. That is fragile by construction: there was no
widget boundary enforcing which meaning won, only `if`/`elif` ordering in one
large method, and — this is the part actual use surfaced that reasoning
about the code did not — Qt's own shortcut system compounds it. The
transport menu registers `Space` as a `QAction` shortcut (Play/Stop) with the
default `Qt.WindowShortcut` context. A `QAction` shortcut is checked via a
`ShortcutOverride` event delivered to the focused widget *before* that
widget's normal `keyPressEvent()` runs; a widget that does not explicitly
accept that event loses the key to the action outright. Nothing in the
inline implementation touched `ShortcutOverride` at all, so the actual
behavior of Space while editing depended on focus-widget details nobody had
reasoned through, not on the `elif` chain in `keyPressEvent()` that appeared
to own it. That mismatch between "what the code appears to decide" and "what
Qt actually delivers" is the concrete shape of "the key bindings feel
overlapped."

### The fix: a separate widget, so Qt's own focus/shortcut routing does the scoping

`gui/piano_roll_panel.py` is a new module: `PianoRollScene` (one track's
notes, at a fixed pitch scale, over its own vertical space — no row-height
geometry shared with `ArrangeScene`), `PianoRollView` (a `QGraphicsView`
subclass owning all of the panel's mouse and keyboard interaction), and
`PianoRollPanel` (a toolbar over that view: a Cursor/Note mode button, an
Add/Delete button mirroring Space, a Delete button mirroring the Delete key,
and a Close button).

The panel sits in a vertical `QSplitter` below the main arrange canvas
(`StudioWindow._build()`), not a `QDockWidget` — the ask was for something
anchored at the bottom that stretches taller/shorter in place, not something
that floats or detaches, and `theme.py`'s stylesheet already had a
`QSplitter::handle` rule waiting for exactly this. Opening a track's piano
roll (still the same double-click-a-track-header gesture #155 chose) now
shows/focuses this panel instead of growing that track's row on the main
canvas; the main canvas goes back to always rendering every track at its
normal collapsed height, with `_clip_thumbnail_notes()`'s always-visible
mini view as the only note rendering there.

The scoping fix has two parts, both load-bearing on their own:

1. **Explicit focus policies.** The main arrange view
   (`self.view.setFocusPolicy(QtCore.Qt.NoFocus)`) can never become the
   keyboard-focus widget — `QGraphicsView` defaults to `StrongFocus`, which
   was silently doing nothing wrong only because nothing had ever clicked
   into it in a way that mattered before. `StudioWindow` itself is
   `StrongFocus` and is where keyboard focus lives by default. `PianoRollView`
   is `StrongFocus` and claims focus on open (`panel.open_track()` calls
   `self.view.setFocus()`) and whenever the user clicks into it; clicking
   back on the main canvas (`StudioWindow.eventFilter()`'s mouse-press
   branch) calls `self.setFocus()` to reclaim it explicitly, rather than
   relying on whichever widget Qt happens to leave focused.
2. **`PianoRollView.event()` intercepts `ShortcutOverride`** for the keys it
   claims (arrows, Space, Enter/Return, Delete/Backspace) whenever a track is
   open, accepting the event so `QShortcutMap` never dispatches the matching
   `QAction` in the first place. This is the piece "just give the panel its
   own `keyPressEvent()`" would have missed — a widget's `keyPressEvent()`
   never even runs for a key a `QAction` shortcut claims first.

One console of truth per key: while the panel has focus, its own
`keyPressEvent()` and `event()` decide what a key means and nothing in
`StudioWindow` runs; the moment focus leaves it (by any means — clicking the
canvas, closing the panel), `StudioWindow`'s original, now much simpler
`keyPressEvent()` is exactly what it was before this feature existed, since
every piano-roll branch has been deleted from it rather than merely
guarded. `tests/test_studio_piano_roll.py`'s focus-scoping tests are the one
place in this file that abandon the "direct method call" discipline the rest
of the suite uses, in favor of `QTest.keyClick()` — a direct call cannot
exercise Qt's real event-delivery/shortcut-override path at all, and that
path is the actual thing being fixed.

### What moved, what didn't

`AddNote`/`DeleteNote`/`MoveNote`/`ResizeNote` (the command objects, #155's
undo design) are unchanged and still the only way `PianoRollView` mutates a
project — `_apply()` still calls the host's `StudioWindow.run()`, so an edit
made in the panel still updates the undo stack, the status line, and (via
the main scene's `rebuild()`) the main canvas's own thumbnail rendering of
the same notes. The ghost-cursor/note-mode keyboard model from milestone
2's first pass — free cursor movement by default, Space adds-or-deletes
at the cursor, Enter toggles into clamped note-nudging only when the cursor
sits on an existing note — is unchanged in behavior; only its location and
enforcement mechanism moved. `ArrangeScene.piano_roll_row` survives as a
bare marker so `TrackHeaders` can still show "piano roll (dbl-click to
close)"; every other piece of piano-roll state that used to live on
`ArrangeScene` (`piano_roll_bounds`, `cursor`, `selected_note`, and the
`_piano_roll_notes()`/`_piano_roll_cursor()`/`pitch_to_y()`/`y_to_pitch()`
methods) moved into `PianoRollScene` and does not exist on `ArrangeScene`
any more.
