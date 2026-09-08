# Score editor: corrections after the project owner's first hands-on session (issue #98 follow-up)

Issue #98 shipped the score editor end to end (both sections above); this
section covers a corrective pass fixing four pieces of real usability
feedback from the project owner's *first actual use* of the finished
feature, plus one bundled follow-on capability. Three of the four reverse
calls #98's own spec or its two child tickets (#88, #90) had already
settled — recorded honestly here as reversals, and *why* they're
reversals: direct feedback from using the thing, not a new abstract
argument found on paper. None of the four required touching the data
layer (`score_editor_state.py`) — all four are interaction-layer
corrections.

**`note_toggle` (Space) can now empty a column to zero notes itself —
reversing the original "refuses to remove the last note" rule.**
`toggle_note_at_cursor()` used to special-case a column's very last note:
removing it required a separate `clear_to_rest` (`r`) press instead. The
stated reason at the time (see CONTEXT.md's original Rest entry, now
reworded) was keeping "empty" unambiguous with "rest." In practice, using
the editor for real surfaced this as unwanted two-step friction, not a
protection anyone wanted: the same key that places a note should be able
to take it away, all the way to zero, with no special case. The fix is a
straight deletion of the guard (`if len(column.notes) <= 1: return
False`) — `toggle_note_at_cursor()` now always pops the note at the
cursor's row if one's there. `clear_to_rest` keeps its own real value
independent of this fix: for a multi-note chord column, it clears
everything in one press instead of one note at a time, so it stays as a
distinct, still-useful action.

**The Chord builder's Up/Down and Left/Right roles are swapped — Up/Down
now switches the focused reel, Left/Right spins it.** The original
binding (Left/Right switches reel, Up/Down spins) was inherited unchanged
from `prototypes/score-editor-cursor-concept/`'s own scheme when #98's
spec built this screen — #87/#88's grilling of that prototype rejected
its keybind *vocabulary* (which literal keys did what) but never
re-examined this particular direction mapping against the screen's actual
rendered shape. `chord_builder_display.render()` draws the five reels
(root/quality/3rd/5th/7th) as five stacked *rows*, one per line — a
vertical list, for which Up/Down is the natural navigation axis and
Left/Right the natural "adjust this row's value" axis, not the reverse.
Direct user feedback after hands-on use called the original binding
backwards for this reason. The fix is purely in `main.py`'s
`_run_chord_builder()`'s key dispatch (its `if key == "LEFT": ... elif
key == "RIGHT": ...` reel-switch branch and its `elif key in ("UP",
"DOWN"): ...` spin branch swapped keys, bodies unchanged) plus the two
on-screen help strings (`chord_builder_display.render()`'s own hardcoded
line, and `_run_chord_builder()`'s `status` argument) — none of the pure
stepping functions (`move_slot()`, `spin_root()`, `spin_degree()`,
`spin_quality()`, `apply_quality_preset()`) changed at all, since they
already took a direction-agnostic `delta`. Typing-to-jump behavior
(`step_root_typeahead()`/`step_alias_typeahead()`) is likewise untouched
— only which physical key means "switch" vs. "spin" changed.

*General principle, also governing the inline header editor below:* an
arrow-key binding should match the widget's actual visual orientation —
Up/Down navigates a vertical list, Left/Right navigates a horizontal
strip — not whatever an earlier prototype happened to bind. This is why
the Chord builder (vertical reels) and the inline header editor
(horizontal fields, see below) end up with *opposite* Up/Down-vs-Left/
Right roles from each other: they're genuinely different widget shapes,
not an inconsistency to reconcile.

**Transpose moved off a remappable `+`/`-` onto hardcoded Shift+Up/
Shift+Down.** `transpose_up`/`transpose_down` were originally ordinary
`[keybinds]`-table entries (config.DEFAULT_KEYBINDS, default `"+"`/`"-"`),
remappable through the Settings screen like every other score-editor
action except the arrows/Enter. Direct user feedback: `+`/`-` sit far
enough from the arrow keys already used for cursor movement that
reaching for them broke the editing flow. The fix makes transpose a
hardcoded Shift+Up/Shift+Down instead — the same tier as Left/Right/Up/
Down/Enter, never remappable — rather than picking a different single
remappable character: a modifier-arrow combo is a natural extension of
this app's existing "arrows are never remapped anywhere" convention, and
`settings_display.is_valid_remap_key()`'s single-character validation
couldn't represent a modifier combo as a remap target anyway.
`transpose_up`/`transpose_down` are removed from
`config.DEFAULT_KEYBINDS`, `config_store.py`'s schema docstring, and
`settings_display.py`'s `KEYBIND_ACTIONS`/`_KEYBIND_LABELS`.

Making this work required teaching `main.RawKeys.poll()` to recognize the
*modified* arrow-key escape sequence, not just the bare one it already
handled. A bare arrow sends `ESC [ <letter>` (no parameter bytes); a
Shift-held arrow sends the CSI-parameterized form `ESC [ 1 ; 2 <letter>`
(`1;2` being xterm's standard "modifier code 2 = Shift" encoding, part of
a small fixed vocabulary that also covers Alt/Ctrl/combinations).
`poll()`'s byte-reading loop, after confirming the `ESC [` prefix, now
keeps reading bytes (each still gated by the same
`config.ESCAPE_SEQUENCE_TIMEOUT`-bounded `select()` pattern the original
bare-arrow path already used) for as long as they're parameter bytes
(ASCII digits or `;`), accumulating them into a string; the first
non-parameter byte is the sequence's final letter. That parameter string
plus the final letter feed a new pure function, `_parse_csi_params(
param_bytes, final_byte)` — factored out specifically so it's
unit-testable without a real TTY/fd, following this repo's "pure logic
unit-tested, real I/O smoke-tested" convention
(`menu_animation.detect_perf_mode()`/`_decide_perf_mode()`'s precedent).
Three cases: `param_bytes == ""` (bare arrow) maps straight through the
existing `_ARROW_BY_FINAL_BYTE` table, unchanged; `param_bytes == "1;2"`
with `final_byte` `"A"`/`"B"` returns the new `"SHIFT_UP"`/`"SHIFT_DOWN"`
tokens; anything else recognized-but-not-this-app's-concern (another
modifier code, or Shift+Left/Right which nothing here consumes yet) falls
back to the plain direction for `final_byte` rather than returning `None`
and dropping the keystroke — same graceful-degradation posture `poll()`'s
own existing docstring already documents for a laggy/multiplexed pty
(a modifier this app doesn't have a use for is still "the user pressed an
arrow key," not "no key was pressed"). `resolve_editor_action()` gained
two more hardcoded cases (`"SHIFT_UP"` -> `"transpose_up"`, `"SHIFT_DOWN"`
-> `"transpose_down"`), at the same tier as its existing hardcoded
`"LEFT"`/`"RIGHT"`/`"UP"`/`"DOWN"`/Enter handling, ahead of the
`_EDITOR_ACTIONS`/keybind-store loop. Every *other* `RawKeys` consumer in
the app (fill/wheel/tab's sensitivity Up/Down, menu navigation, Settings'
field navigation, etc.) is unaffected: a bare, unmodified arrow press
still produces exactly the same `"UP"`/`"DOWN"`/`"LEFT"`/`"RIGHT"` string
it always did, confirmed by the full test suite passing unchanged and by
`_parse_csi_params("", <letter>)`'s own direct test coverage.

Honest caveat: this depends on the terminal/multiplexer actually sending
the standard xterm CSI encoding for a Shift-held arrow. A terminal that
encodes it some genuinely different way would silently fall through to
"not an arrow key I recognize as modified" and just not transpose,
rather than erroring — graceful degradation, not a hard cross-terminal
guarantee, same posture as the existing arrow-burst-under-lag handling.
Verified via synthetic byte-sequence unit tests (`tests/test_main.py`)
only; a real TTY wasn't available to confirm end-to-end against an actual
terminal emulator during this pass (see Known limitations).

**The separate Score properties screen is retired — score-level
properties (time signature/key/tempo) are now edited inline in the main
editor view's own status line.** This reverses #90's original call ("a
second, separate reel-based screen ... same shape as the Chord builder"),
for the same "found out by actually using it" reason as the other three
items here: the project owner didn't want to leave the main view just to
change the tempo or key signature. `score_properties_display.py`'s
`render()` and `main.py`'s `_run_score_properties()` interactive loop
(the standalone screen's whole render/dispatch shape) are deleted
outright; the module keeps only its pure logic
(`PROPERTY_SLOTS`/`spin_time_signature()`/`spin_key_fifths()`/
`spin_tempo()`/`key_fifths_label()`/`TIME_SIGNATURE_OPTIONS`/etc.), which
the inline editor reuses unchanged — the actual stepping/labeling math
never needed to change, only the screen wrapped around it.

`main.run_score_editor()`'s status line now always shows `time=`/`key=`/
`tempo=` fields (`_property_field_texts(score)`, a pure dict-builder),
mirroring `tab`'s own always-visible `tempo=`/`time=` status-field
convention — so there's something to look at even outside edit mode, per
the spec for this fix. Pressing `score_properties` (`t`, same key as
before) sets a `properties_editing` flag; while it's set,
`run_score_editor()`'s main key-dispatch branch is bypassed entirely in
favor of a new pure function, `_handle_property_key(key, score, slot,
buffer)`, which mutates `score` in place exactly the way every other
score-editor mutation function in this codebase does (see
`score_editor_display.py`'s module docstring for that convention) and
returns `(new_slot, new_buffer, still_editing)`. Left/Right moves the
highlighted field (`spd.move_slot()`, reused verbatim) — a *horizontal*
strip of three fields, so Left/Right navigates here per the general
orientation principle above, the opposite mapping from the Chord
builder's now-vertical Up/Down for the same underlying reason (different
widget shape, not an inconsistency). Up/Down spins the highlighted
field's value via `spin_time_signature()`/`spin_key_fifths()`/
`spin_tempo()`, reused unchanged. A digit (or `/` for time signature)
accumulates into a per-field typed buffer on the two fields with a
natural typed form (`_PROPERTY_TYPABLE_SLOTS = ("time_signature",
"tempo")` — key signature has none, so digits typed while it's
highlighted are simply ignored, spin-only); Backspace trims the buffer;
Enter parses+applies any pending buffer via a new pure function,
`_parse_property_input(slot_name, text)` (tempo: a plain BPM number,
clamped into `spd.TEMPO_MIN_BPM`/`MAX_BPM`; time signature: free-form
`N/D` text, deliberately *not* snapped to `spin_time_signature()`'s fixed
`TIME_SIGNATURE_OPTIONS` set, since typing a value directly — e.g. an
uncommon `11/8` — is exactly the point of a free-form entry path
alongside the fixed-set spin), swallowing an unparseable buffer rather
than crashing (same "leave the field unchanged, don't crash" posture
`settings_display._capture_numeric()` already follows on a bad parse),
and always exits edit mode whether or not there was a pending buffer to
apply. `_handle_property_key()`'s buffer-accumulation/backspace mechanics
mirror `settings_display._capture_numeric()`'s capture-buffer pattern,
just inline (no modal sub-loop) rather than a dedicated capture function.
Mutations apply directly to the real `EditorScore` as they happen, same
"no separate commit step" convention the original screen already used
(per its own module docstring's reasoning: three independent scalar
fields have no "which notes should this become" staging ambiguity the
way the Chord builder's notes do) — "commit" here is really just "exit
edit mode back to normal cursor editing," not a distinct persistence
step.

The highlighted field renders reverse-video (`\033[7m...\033[0m`) in the
status line, same visual convention the Chord builder/old properties
screen used for their own highlighted rows; while a typed buffer is
non-empty, the highlighted field shows the raw buffer text instead of the
field's real current value (e.g. `tempo=120` while typing, reverting to
the real value the instant the buffer clears on Enter or Backspace-to-
empty). `score_properties_exit` (the old screen's dedicated close
keybind, default `"b"`) is removed along with the screen it closed — for
config.py's `DEFAULT_KEYBINDS`, `config_store.py`'s schema docstring, and
`settings_display.py`'s `KEYBIND_ACTIONS`/`_KEYBIND_LABELS` alike, there's
no separate screen left to exit from.

**Bundled follow-on (not a reversal — a new capability requested
alongside the same feedback): `note_toggle`'s default placement and the
main editor's left legend are now key-signature-aware.** Previously,
`pitch_at_row()` always returned the bare natural for whatever row the
cursor sat on, and the legend (`staff_map.row_note_name()`) always showed
that bare natural letter too, regardless of `EditorScore.key_fifths`. In
a key with sharps or flats, this meant every fresh note placement needed
a manual Shift+Up nudge immediately after Space just to match the key —
real friction the owner called out directly. The fix is a small new
helper, `staff_map.key_signature_accidental(key_fifths, letter_idx) ->
"sharp"|"flat"|"natural"`, built on the standard order-of-sharps (F, C,
G, D, A, E, B) / order-of-flats (B, E, A, D, G, C, F) tables — keyed by
*letter index* (`staff_map.LETTER_NAMES`' own 0=C..6=B order), not pitch
class, since a key signature accidental applies to a letter name across
every octave, not one specific pitch. `key_fifths > 0` sharps the first
`key_fifths` entries of the sharp order; `key_fifths < 0` flats the first
`abs(key_fifths)` entries of the flat order; everything else stays
natural. `score_editor_display.pitch_at_row(row, key_fifths=0)` (default
argument keeps every existing all-natural call/test working unchanged)
folds the looked-up accidental's semitone delta into the same
`octave*12+pitch_class` arithmetic `transpose_note_at_cursor()` already
uses, so it wraps octaves correctly at the rare edge case of a sharped B
or flatted C (e.g. a 7-sharp key's B row correctly resolves to the pitch
class of C, one octave up — musically B♯, enharmonically identical to the
next C). `toggle_note_at_cursor()` takes the same `key_fifths=0` default
parameter and threads it through to `pitch_at_row()`;
`main.run_score_editor()`'s call site passes `score.key_fifths`
explicitly, same convention as `config.FMIN`/`FMAX` being passed
explicitly at their own real call sites elsewhere in this codebase. A new
`score_editor_display._legend_letter(row, key_fifths)` applies the same
lookup to the legend's display text, appending a real Unicode ♯/♭ marker
to `row_note_name()`'s bare letter (`config.TAB_LETTER_WIDTH` (2) already
fits a letter plus one accidental mark exactly, so no width change was
needed). Only the *default* a fresh placement gets changed — Shift+Up/
Shift+Down still freely retune any already-placed note afterward,
unaffected.
