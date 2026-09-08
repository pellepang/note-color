# Score editor data layer: a second `music21` importer, and promoting `score_writer.py`'s per-note helpers (issue #98)

**What.** `score_editor_state.py` (issue #98's data-layer half, synthesized
from wayfinder map #85 and children #86/#87/#88/#90) is a new module
implementing `EditorNote`/`EditorColumn`/`EditorScore` (a plain mutable
score representation), `new_blank_score()`, `load_score()`/`save_score()`
(MusicXML round trip via `music21`), and `EditHistory` (bounded undo/redo).
Two things about it are worth a real rationale entry, not just a Files-
table pointer: it becomes a second module permitted to import `music21`,
and it reuses rather than duplicates three pieces of `score_writer.py`'s
own private logic.

**Why a second `music21` importer, not a third copy of the isolation
rule.** `CLAUDE.md` documented `score_writer.py` as *the* module allowed
to import `music21`, mirroring `batch_transcribe.py`'s sole-`librosa`
rule. Issue #98's editor genuinely needs `music21` too — round-tripping a
MusicXML file is the whole point of `load_score()`/`save_score()` — so
the only real choices were (a) route the editor's persistence through
`score_writer.py` itself, forcing that module to grow a second,
differently-shaped responsibility (batch, sparse `onset_hop`-keyed export
vs. interactive, dense fixed-column-sequence load/save), or (b) accept a
second, narrowly-scoped importer. `rhythm_reanalysis.py` already
established the precedent for (b) with `librosa` in issue #77 — a second
exception is judged simpler and more honest than bending one module's
shape to serve two structurally different callers. The actual constraint
this isolation rule protects (`music21`'s real, one-time import cost has
no business on the live/Pi-constrained analysis path) is unaffected
either way: neither `score_writer.py` nor `score_editor_state.py` is ever
imported by `analysis_loop()`/`SessionState`/any live-path module — both
are strictly opt-in, invoked only when a caller explicitly wants a
written/loaded/edited score file, same as `write_score()` always was.

**Why promote `QUARTER_LENGTHS`/`note_hex_color()`/`pitch_for()` instead
of duplicating them.** All three encode real, non-obvious project
conventions that must stay identical between "batch-exported score" and
"editor-saved score" for a user to trust either: `QUARTER_LENGTHS` is the
one place `duration_class` names map to music21's `quarterLength` unit;
`note_hex_color()` is the fixed-lightness fifths-hue mapping that makes a
note's color consistent between every live view *and* any exported/edited
score; `pitch_for()` is this project's flat-biased `NOTE_NAMES_FIFTHS`
spelling convention. Hand-copying any of the three into
`score_editor_state.py` would create exactly the kind of drift risk this
codebase has already promoted shared constants specifically to prevent
(`DIM_LIGHTNESS`, `NOTE_NAMES_FIFTHS`/`diatonic_step()`) — a future tweak
to, say, `TAB_NOTE_LIGHTNESS` or the fifths spelling table would silently
apply to freshly-exported scores but not to freshly-edited ones (or vice
versa) unless a maintainer remembered to update both copies by hand. The
fix is a plain rename (`_note_hex_color`→`note_hex_color`,
`_pitch_for`→`pitch_for`, `_QUARTER_LENGTHS`→`QUARTER_LENGTHS`), not a
restructuring — `score_writer.py`'s own internal call sites and
`tests/test_score_writer.py`'s import were updated to match, with zero
behavior change (confirmed by the full suite passing unchanged).
`score_writer.py`'s `_staff_for()` (the middle-C-row-10 treble/bass split)
was deliberately *not* promoted alongside the other three — it's a
one-line threshold over the already-public `staff_map.staff_row()`, cheap
enough to re-derive locally in `score_editor_state.py` with a comment
pointing back to the canonical version, rather than growing the promoted
surface beyond what #98's spec actually asked for.

**Editor-specific behavior new relative to `write_score()`.**
`save_score()` writes an explicit `music21.tempo.MetronomeMark` and a
real `music21.key.KeySignature(score.key_fifths)` — `write_score()` never
wrote a tempo marking at all, and only ever wrote a *guessed* key
signature when confident. The editor has no such uncertainty (`tempo_bpm`/
`key_fifths` are fields the user directly set via the Score Properties
screen, a different agent's layer built on top of this one), so writing
them unconditionally is correct here in a way it wasn't for
`write_score()`'s best-effort transcription output. `load_score()`
defaults `tempo_bpm` to 90.0 when no `MetronomeMark` is present (true of
every file `write_score()` has ever produced, since it never wrote one)
and `key_fifths` to 0 when no `KeySignature` is present (true whenever
`guess_key_signature()` wasn't confident) — both matching
`new_blank_score()`'s own defaults, so loading an old batch-exported file
into the editor doesn't surprise the user with an arbitrary tempo/key.

**Known limitation: measure-alignment round-trip fidelity.** MusicXML is
fundamentally measure-based; `save_score()`'s `stream.write()` call
invokes music21's own `makeMeasures()` internally, which pads an
incomplete final measure with an extra rest, and — found empirically
while writing this module's round-trip tests — *splits a note into a tied
pair* if its duration would otherwise straddle a barline. Either case
means a score whose total column duration doesn't land on a whole number
of measures can come back from `load_score()` with a different column
count/duration split than what was saved, even though nothing was lost
musically (a tied pair still sounds identical to the original note). This
is a genuine MusicXML-format constraint, not a bug in this module's
merge-by-offset logic — confirmed by reproducing it directly (a (3,4)-time
score with columns summing to 5 quarter-beats, not a multiple of 3, came
back with an extra split column) and then removing it by aligning the
same fixture to two full measures (6 beats), which round-tripped byte-for-
dataclass-equal. `tests/test_score_editor_state.py`'s round-trip fixtures
are deliberately measure-aligned for this reason; a future UI layer that
lets a user save a mid-measure-aligned score should expect this rather
than treating it as new breakage. Not worked around here (e.g. by forcing
every save to pad to a full measure itself) since #98's spec scoped this
module to data/persistence only — a padding policy is presentation-layer,
not persistence-layer.
