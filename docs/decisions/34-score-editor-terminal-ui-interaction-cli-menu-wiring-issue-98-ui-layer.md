# Score editor: terminal UI + interaction + CLI/menu wiring (issue #98, UI layer)

This section covers the layer built on top of `score_editor_state.py`
(the data model/persistence layer, documented above): the interactive
terminal editor itself (`score_editor_display.py`, `main.run_score_editor()`),
the Chord builder (`chord_builder_display.py`) and Score properties
(`score_properties_display.py`) screens, and the live-menu picker
(`score_editor_picker.py`). Real judgment calls not already pinned by
issue #98's spec are recorded here; `CLAUDE.md` stays pointers.

**`chord_name_for_column()` doesn't literally call `chroma.fold()`.** The
spec's own wording ("using `chroma.fold()` + `chord_templates.match()`
against each column's notes") describes the live detection pipeline's
shape, but `chroma.fold()` takes an FFT magnitude spectrum — a loaded
score's columns have no audio to compute one from, only exact pitch-class
sets. `chord_name_for_column()` instead builds the *equivalent* synthetic
chroma vector directly (1.0 at each pitch class actually present, 0
elsewhere) and feeds that straight to `chord_templates.match()` — the
same recognizer, skipping only the "recover pitch-class energy from a raw
spectrum" step that `chroma.fold()` exists for and that isn't needed when
the pitch classes are already known exactly. Bass-chroma is approximated
the same way, gated on the column's lowest note's octave (<=3, roughly
below the ~250Hz cutoff `chroma.fold_bass()` uses) rather than a real
measured energy ratio — a coarser gate than the live pipeline's, but
directionally correct for the same purpose (distinguishing "there's a
real bass note here" from "nothing in the bass register"). Confirmed
against a synthesized C major triad (`tests/test_score_editor_display.py`)
recognizing correctly.

**`undo`/`redo` are matched case-sensitively, breaking this codebase's
established case-insensitive keybind convention.** Every other
remappable action in this app (`_handle_chord_mode_key`, etc.) matches
`key.lower() != bound.lower()`, so e.g. Shift+M also toggles the audio
source the same as plain 'm'. `undo`/`redo`'s defaults ('u'/'U')
deliberately share a letter, distinguished only by case — a
case-insensitive match would make pressing either lowercase or uppercase
fire *both* actions' bound-key check simultaneously (whichever the
dispatch loop checks first would always win), collapsing them into one
key. `resolve_editor_action()`'s `_EDITOR_CASE_SENSITIVE_ACTIONS` carves
out just these two; every other score-editor action keeps the codebase's
normal case-insensitive convention. A user who remaps `undo`/`redo` onto
two keys that happen to differ only by case elsewhere isn't specially
protected against the same collision, but that's true of any keybind
collision a remap can create — not something Settings' generic remap UI
guards against for the app's *other* nine keybinds either.

**`chord_builder_exit`/`score_properties_exit` ('b' by default) are also
matched case-sensitively (exact-case, not merely "case-sensitive same as
undo/redo") — a narrower fix for a related but different conflict.**
Unlike undo/redo (two actions sharing one letter), this is one action
whose default key collides with a real *piece of typed content*: the
Chord builder's ROOT reel needs to type the note letter 'B', and 'b' is
also `chord_builder_exit`'s default. Exact-case matching (`key ==
store.keybind(...)`, not `.lower()`) means only literal lowercase 'b'
exits the screen; uppercase 'B' (Shift+B on a real keyboard) falls
through to the ROOT reel's own typeahead instead. This only resolves the
conflict for the *root* reel, though — see the next entry for the
narrower, unresolved edge this leaves on the third/fifth/seventh degree
reels' own typed tokens (Known limitation below).

**`step_root_typeahead()`'s letter matching is exact-case (uppercase
only), not merely inheriting case-sensitivity from the exit-key fix
above.** A natural letter jump (e.g. typing 'F' to jump the root reel
straight to F) only fires on the literal uppercase character; a
lowercase letter keystroke is a no-op rather than also jumping (see
`test_root_typeahead_lowercase_letter_does_not_jump`). This is what makes
the previous entry's fix actually work end-to-end: if lowercase 'f' also
jumped the reel to F, then lowercase 'b' would need to unambiguously mean
either "jump to the letter B" or "flatten what I just picked" — an
irresolvable ambiguity for that one letter. Requiring the shifted
(uppercase) form for every letter, uniformly, keeps the whole reel's
typing rule simple and consistent rather than special-casing only B.

**Known limitation: the third/fifth/seventh degree reels' own typed
tokens ('b3', 'b5', 'b7') can't be typed while `chord_builder_exit` is
bound to its default 'b'.** The ROOT reel's ambiguity above was
resolvable via letter case (a real, existing distinction — 'B' vs 'b').
The degree reels' tokens have no equivalent case variant to exploit:
'b3'/'b5'/'b7' are conventionally lowercase, and pressing 'b' at all
(regardless of which reel has focus) always exits the builder first,
before any reel-specific typeahead logic ever sees the keystroke. Spinning
Up/Down still reaches every degree option, including the flat ones, so
this is a typing-shortcut gap, not a missing feature — but it's a real,
user-visible one under the spec's own chosen default keybind, not fixed
here (remapping `chord_builder_exit` to a different key via the Settings
screen sidesteps it entirely).

**`main.run_score_editor()`'s quit-confirm treats undo/redo as always
dirtying the score, even when they return it to exactly its last-saved
content.** Precisely tracking "does the live score's content differ from
what's actually on disk" across arbitrary undo/redo traversal would mean
either comparing full `EditorScore` snapshots by value on every action
(cheap here, since they're plain dataclasses, but still extra bookkeeping
for a purely cosmetic accuracy improvement) or tracking a "distance from
last save" counter that undo/redo would need to move in the opposite
direction from every other mutating action. Simpler and safer: `dirty`
becomes `True` on any action that *could* have changed the score,
including undo/redo, and only `False` again on an explicit `save`. This
can produce a false "unsaved changes" quit-confirm (e.g. toggle a note,
undo it, try to quit) — a harmless extra confirmation keypress, not a
risk of silently losing real edits, which is the failure mode the
confirm exists to prevent in the first place.

**Filename capture for the live-menu "New score..." picker action
(`score_editor_picker.capture_filename()`) is a small raw-ANSI keystroke
buffer built on `main.RawKeys`, not `settings_display.py`'s `blessed`
exception.** `blessed` in this codebase is a single, deliberately scoped
carve-out (settled by #37/#39's grilling specifically for the Settings
screen's field-navigation/remap-capture UI — see that module's own
docstring). Reusing it here would either mean threading a second,
unrelated screen through the same exception (turning a one-screen
carve-out into "blessed wherever a screen wants line-editing," exactly
the scope creep #37/#39 ruled out) or duplicating `blessed`'s setup/
teardown a second, inconsistent way. The actual need here — a single-line,
backspace-editable buffer, Enter confirms, Esc cancels — is precisely the
shape `settings_display._capture_hue()`/`_capture_numeric()` already poll
one keystroke at a time for against `blessed.Terminal.inkey()`; porting
that same poll-a-key/backspace-editable-buffer/Enter-confirms shape onto
`main.RawKeys.poll()` instead needed no new mechanism, just the existing
primitive this repo's raw-ANSI screens already use everywhere else.

**The score editor's "Edit" live-menu entry doesn't go through
`shell.py`'s `_NON_SESSION_SCREENS` dict the way Settings/Credits/
Prototypes/Stats do.** Those four screens share one real property this
new entry doesn't: none of them ever needs to distinguish "back to the
menu" from "quit the whole app" via their own return value — Ctrl+C
during any of them is caught and always exits, and otherwise they always
loop back to the menu. `main.run_score_editor()` can't share that shape:
it's independently reachable as `virtualnote edit <path>` too, so it
follows every other `run_session`-launchable tool's `"menu"`/`"quit"`
sentinel convention (the spec says so explicitly) — a `|` press inside
the editor must return to the live menu specifically, not exit the
process, exactly like `fill`/`wheel`/`tab`/`gui`. At the same time, it
can't be routed through `main.run_session()` either, since it never
touches `SessionState`/audio — `run_session()`'s dispatch always calls
`session.ensure_started()` first, which would needlessly open the mic
for a tool that has nothing to do with audio. `shell.py` therefore gives
`"edit"` its own small branch: show the file picker, then (if a path was
chosen) call `run_score_editor()` and handle its sentinel exactly the way
the `run_session()` branch below it already does.
