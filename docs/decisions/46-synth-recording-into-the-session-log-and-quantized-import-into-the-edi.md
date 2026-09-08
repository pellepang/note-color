# Synth recording into the session log, and quantized import into the editor (map #99, ticket #122, decision #110)

The last of map #99's build tickets: what you play on the synth becomes a
score you can open in the editor. Decision #110 settled the shape — one
log format extended additively, wall-clock timing, capture raw and
quantize at import — and this is what implementing it actually cost.

### One schema, four optional fields, and why nothing else could move

`session_recorder.py` grew `note_on()`/`note_off()` alongside the existing
`record_hop()`, writing the same file with four new optional keys:
`source` (`"played"`, absent meaning `"detected"`), `velocity` (MIDI
0-127), `patch`, and `pad`. The constraint that made this cheap is that
`source`'s default is the *absent* case, so no pre-existing log needed
rewriting and no reader needed a version check — the same
defaults-and-degradation posture `config_store.py` takes toward
`config.toml` and #106 took toward patch files.

`virtualnote replay` then reads a synth recording with **no changes at
all**, which was verified end to end rather than reasoned about: a
recorded four-note take was replayed through the real
`main.run_replay_session()` and its `dump_ansi()` output checked for the
right pitches at the right times with the right duration glyphs.
`session_player.group_columns()` needed nothing either — a chord played
with three keys down lands as three lines sharing one `t`, which is
exactly the shape it already groups.

`velocity` is written as a MIDI 0-127 integer rather than the engine's own
0..1 float. The log is a hand-readable file and the map's standing rule is
that the event model is MIDI-shaped; `127` is what a musician reading the
line expects "full velocity" to say.

### `t` starts at the first note, not at arming

Decision #110 said `perf_counter()` at note-on. What it did not say is
what zero means. Measuring from the moment `Shift`+S was pressed would put
however long it took to reach for the keyboard at the front of every
recording, and a replay would sit on an empty staff through it. Measuring
from the take's first played note removes that and has a second benefit
that turned out to matter more: the clock origin is then whatever the
caller passed in, so the whole played-note path is testable with injected
times and no real elapsed time anywhere.

The one wrinkle is that a file mixing detected and played notes would
carry two time origins. Nothing in this app can produce one — the synth
tool and the live views are never running simultaneously — so it is
documented in `session_recorder.py`'s schema rather than defended against.

A real bug lived here for one commit and is worth recording: the origin
was read as `self._started_at or pending["started_at"]`, and a take whose
first note landed at clock 0.0 took the falsy branch, collapsing every
later note's `t` to zero. Caught by a test with injected times starting at
0.0 — the kind of thing real wall-clock values would have hidden forever.

### `duration_class` is derived; `duration_seconds` is measured

`bpm_estimate` and `duration_hops` are written `null` per #110 — they are
meaningless without a hop-driven pipeline. But `duration_class` is what
`virtualnote replay` draws its duration glyphs from, and writing every
played note as `DEFAULT_DURATION_CLASS` would replay a fast passage as a
row of identical quarter notes.

So it is derived against `config.PLAYED_NOTE_REFERENCE_BPM` (90.0, the
same figure `score_editor_state.DEFAULT_TEMPO_BPM` uses), with the
unrounded `duration_seconds` written right beside it. That is the one
place a played note's line is inferred rather than measured, and the
measurement it was inferred from is in the same line — so `log_import.py`
recomputes it at whatever tempo the import actually uses, and no
information was lost to make the glyph appear.

### Recording is armed from the same two functions that make sound

`_note_on()`/`_note_off()` inside `run_synth_tool()` call the recorder
directly, rather than a separate hook watching the key stream. That makes
the log a record of what was *heard*, not of what was *typed*, which
matters in three real cases: a note ended by a layout switch or an octave
shift (both go through `policy.release_all()`), a note still held when the
tool is left (the `finally` block's release writes its line), and a
fixed-duration note on a terminal with no key releases, whose note-off
arrives from `policy.expire()` at the moment it actually stopped sounding.
That last one is #110's "recorded exactly as played" made literal: the
fallback's fixed length is what the log says, because it is what happened.

Arming and disarming both release everything first, for the same reason an
octave shift does — a note whose note-on landed on one side of the switch
and whose note-off lands on the other has no honest line to write.

The recorder itself is the process-wide `SessionState.session_recorder`
when there is a session, so arming in the synth and arming in `fill` are
the same switch on the same file and `|` back to the menu never severs a
take. The standalone `virtualnote synth` path (no session) owns one for
its own lifetime and closes it on the way out, since nothing else in that
process would.

### `Shift`+S is not remappable, and that contradicts #110 on purpose

#110 said the recording arm would be remappable "like every other
keybind". #119 had already ruled the synth tool's `Shift` bindings
non-remappable, because a remap onto a plain letter would silently break
the always-plays invariant the whole tool is built on and
`settings_display.is_valid_remap_key()` has no way to express "any letter,
ever".

Following #110 to the letter would put that invariant at the mercy of one
line of TOML. Following #119 keeps `rec=`'s *meaning* identical across all
four views — same field, same on/off, same off-by-default posture — which
is what #110 was actually asking for when it said "same as everywhere".
`Shift`+S it is, hardcoded, same tier as the score editor's Shift+Arrow
transpose.

### Quantization happens at import, and the editor's model shapes the result

`log_import.py` is where the rounding finally happens. Its pure half
(`quantize_columns()` plus the grid helpers) takes plain event dicts and
returns plain `(notes, duration_class)` columns with no music21 anywhere;
`score_from_events()`/`import_log()` wrap that into a real `EditorScore`
behind a local import.

This is emphatically not the same thing as `score_writer.py`'s existing
32nd-note offset quantization, which is not a musical judgement at all —
it exists because a real onset produces a rest music21 cannot express in
MusicXML. This one rounds *rhythm*, deliberately, where the user picks the
grid and can see the result.

Two properties of `EditorScore` decided the output shape:

1. **A column's duration is when the next column starts.** There are no
   independent onsets in the editor's model, so a note that decayed before
   the next one arrived becomes a note column followed by a **Rest
   column** — that is how a gap survives the trip. Without it, every
   staccato passage would import as legato.
2. **Only standard note values exist.** A quantized span of five
   sixteenths has no name in `DURATION_CLASS_ORDER`, so it snaps to the
   nearest one and the sequence drifts slightly against the grid. The
   alternative is tie-split columns, a notation feature this editor does
   not have.

Notes landing on the same grid step become one column, which is
`session_player.group_columns()`'s own "same `t` is one column" rule
applied to a grid instead of to exact equality — and is what makes a
hand-played chord, whose keys are never struck at the identical
microsecond, arrive as a chord.

No triplet grids are offered. `DURATION_CLASS_ORDER` has no tuplet values
to write the result as and `score_writer.py` has never emitted one (issue
#62 deferred tuplet detection); a grid whose output cannot be notated
would be a worse lie than not offering it.

### Two ways in, both leaving the log untouched

From the menu, the score editor's picker now lists session recordings
alongside score files (suffixed `[recording]`, so a log is visibly not a
score before Enter is pressed) and shows a grid prompt when one is picked.
From the CLI, `virtualnote edit <log>.jsonl [--grid] [--tempo] [--out]`
does the same thing without a prompt.

Both hand the editor an already-built score through a new `score=`
parameter on `run_score_editor()`, at a `.musicxml` sibling path that
**nothing has written yet**. A grid that turned out wrong therefore costs
one quit-without-saving, not a file to clean up — which is the practical
form of #110's "quantizing at import is reversible". The alternative,
teaching `run_score_editor()` about `import_log=`/`grid=`, was rejected:
that function's job is to edit a score, not to know what a session log is.

`edit` keeping one `file` argument that accepts either kind, rather than
gaining an `import` subcommand, is the same reasoning — from the user's
side it is still "open this in the editor".

### Verified without a TTY or ears

Nothing here was heard. What is verified: the played-note schema, pairing,
time origin, truncation-on-close and velocity conversion (unit tests with
injected clocks); every quantization behaviour above (pure-function tests
with hand-built events); the picker's row layout; and the synth loop's own
recording dispatch, driven headlessly through the real
`run_synth_tool()` by #119/#120's scripted-`RawKeys` pattern — including
that nothing played before arming is recorded, that a still-held note is
finalized when the tool is left, and that a session-owned recorder
survives it. `virtualnote replay` on a synth recording was run end to end
against its ANSI dump.

Untested by anything here: the picker's and grid prompt's interactive
loops, and `virtualnote synth`'s standalone recorder-closing path, which
would need a real audio device. Unknown until someone plays it: whether
`config.IMPORT_DEFAULT_GRID`'s sixteenth is the right default for actual
human timing, and whether `PLAYED_NOTE_REFERENCE_BPM`'s 90 makes a replay's
glyphs read sensibly for a performance played at some other tempo. Both
are provisional in the same spirit as chord mode's thresholds.
