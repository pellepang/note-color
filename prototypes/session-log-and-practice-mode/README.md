# Session log + practice mode (prototype)

Standalone prototype for Concept E and Feature 3 of
`docs/research/notation-and-feature-ideas.md` -- a JSON-Lines session log
of everything the live pipeline finalizes, a reader that replays it, and
a minimal practice-mode scorer that grades a recorded attempt against a
hand-written target melody.

Follows this repo's `prototypes/` convention (see
`prototypes/issue-42-menu-animation/`): self-contained, run directly with
`.venv/bin/python`, imports the real note-color modules read-only
(`config`, `color_map`, `duration_tracker`), and **does not modify any
existing source file**. If this direction is adopted for real, it would
be ported into the app proper (a `SessionRecorder` instantiated in
`main.SessionState`, wired into `analysis_loop()` -- see "System
architecture reasoning" below) the same way the menu-donut prototype was.

## What's here

| File | What |
|---|---|
| `session_recorder.py` | `SessionRecorder` -- appends one JSON object per finalized note/chord-note/barline to a `.jsonl` file, from a stream of `RenderItem`-shaped hop data. |
| `session_player.py` | `SessionPlayer` -- reads a `.jsonl` log back and replays it as a terminal printout, instantly or paced by real elapsed time (`realtime=True`). |
| `practice_scorer.py` | `score_session()`/`print_report()` -- naive nearest-expected-time alignment of a target melody against a recorded log; pitch accuracy + rhythm timing deviation. |
| `demo.py` | Fabricates a short imperfect "performance," records it, replays it, scores it against `target_melody.json`. Run this to see everything work. |
| `target_melody.json` | A tiny 5-note target fixture (`[{"label": "C4", "t": 0.0}, ...]`). |
| `_repo_paths.py` | `sys.path` bootstrap so the scripts above can `import config`/`color_map`/`duration_tracker` from the real app. |

## How to run it

```
cd ~/note-color
.venv/bin/python prototypes/session-log-and-practice-mode/demo.py
```

That's the full pipeline end to end: record -> dump raw JSONL -> replay
-> score. No arguments needed. `session_player.py` is also directly
runnable against any `.jsonl` log it (or a hand-edited file in the same
shape) produced: `.venv/bin/python session_player.py demo_session.jsonl`.

### What the demo actually produced (real output, this run)

The fabricated "performance" deliberately diverges from `target_melody.json`
in four different ways -- one per note, chosen to exercise every branch
of the scorer:

| target | expected t | played | played t | result |
|---|---|---|---|---|
| C4 | 0.00 | C4 | 0.00 | hit, on time |
| D4 | 0.50 | D4 | 0.65 | hit, +150ms late |
| E4 | 1.00 | **F4** | 1.16 | wrong pitch |
| G4 | 2.00 | *(not played)* | -- | missed |
| A4 | 2.50 | A4 | 2.46 | hit, 39ms early |

```
Pitch accuracy: 3/5 (60%)
Rhythm accuracy: mean timing deviation 63ms (over 3 correctly-pitched, matched notes)

target  expected_t  played  played_t    dev_ms    result
C4      0.00        C4      0.00        +0        hit
D4      0.50        D4      0.65        +150      hit
E4      1.00        F4      1.16        +161      wrong_pitch
G4      2.00        -       -           -         missed
A4      2.50        A4      2.46        -39       hit
```

The recorded `.jsonl` log itself (also printed by `demo.py`):

```json
{"t": 0.0, "kind": "note", "pc": 0, "octave": 4, "label": "C4", "duration_hops": 20, "duration_seconds": 0.464, "duration_class": "quarter", "bpm_estimate": 120.0, "chord_name": null}
{"t": 0.65, "kind": "note", "pc": 2, "octave": 4, "label": "D4", "duration_hops": 20, "duration_seconds": 0.464, "duration_class": "quarter", "bpm_estimate": 120.0, "chord_name": null}
{"t": 1.161, "kind": "note", "pc": 5, "octave": 4, "label": "F4", "duration_hops": 39, "duration_seconds": 0.906, "duration_class": "half", "bpm_estimate": 120.0, "chord_name": null}
{"t": 2.461, "kind": "note", "pc": 9, "octave": 4, "label": "A4", "duration_hops": 19, "duration_seconds": 0.441, "duration_class": "quarter", "bpm_estimate": 120.0, "chord_name": null}
{"t": 1.2, "kind": "barline"}
```

`SessionRecorder` was also verified directly (not just via the demo)
against a real `main.RenderItem` namedtuple instance (not a dict) and
against a fabricated chord-mode hop (`note_stack` with 3 entries,
`chord_name="C"`) -- both produce correctly-shaped events; see the
"Testing" section below for the exact commands.

## JSON schema

One JSON object per line (JSON Lines -- no wrapping array, no trailing
comma bookkeeping, trivially `tail -f`-able while a session is live, and
directly `jq`-able). Two event kinds today:

```jsonc
// kind: "note" -- a monophonic or single chord-tone finalization
{
  "t": 0.65,                  // onset time, seconds since session start
  "kind": "note",
  "pc": 2,                    // pitch_class, 0-11 (same convention as RenderItem)
  "octave": 4,
  "label": "D4",              // convenience -- color_map.NOTE_NAMES[pc] + octave
  "duration_hops": 20,        // raw hop count the note was tracked for (DurationTracker's own unit)
  "duration_seconds": 0.464,  // duration_hops * (config.BLOCK_SIZE / config.SAMPLE_RATE)
  "duration_class": "quarter",// nearest standard note value -- duration_tracker.duration_class_for_beats()
  "bpm_estimate": 120.0,      // live tempo estimate at finalization, or null if none yet
  "chord_name": null          // recognized chord name if this note was part of one, else null
}

// kind: "barline" -- an estimated bar boundary
{ "t": 1.2, "kind": "barline" }
```

Design choices, and why:

- **JSON Lines, additive, no envelope.** This is deliberately the same
  shape `config_store.py`'s own module docstring establishes as this
  project's precedent for "plain-text, hand-editable, tool-agnostic
  format": `config.toml` is an additive TOML overlay a user can open in
  any editor and partially fill in; a session log is an additive,
  append-only *stream* a user (or another tool) can open in any editor,
  `jq`, or `tail -f`, with every line independently valid JSON -- no
  cross-line schema coupling, no migration story needed if a future field
  gets added (an old reader just ignores keys it doesn't know, same as
  `config_store.py` tolerating unknown TOML keys "reserved for future
  settings").
- **Field names mirror `RenderItem`/`NoteEvent`, not invented fresh.**
  `pc`/`octave`/`bpm_estimate`/`chord_name`/`duration_hops` are the exact
  names `main.RenderItem` and `batch_transcribe.NoteEvent` already use for
  the same concepts (see `main.py` lines ~276-296,
  `batch_transcribe.py`'s `NoteEvent` namedtuple) -- a session log
  "describes what note sounded when for how long," the same thing
  `NoteEvent` already describes for offline transcription, so reusing its
  vocabulary means anything that already knows how to read a
  `NoteEvent`/`RenderItem` needs zero new mental model to read this log.
  `pc` (not `pitch_class`) is the one deliberate shortening, matching
  Concept E's own schema mockup in the research doc.
- **`t` is the note's onset time, not the finalization time.** A note's
  full information (pitch + duration + the tempo estimate at that moment)
  only becomes knowable on the hop its duration *finalizes* -- but the
  timestamp a reader actually wants is when the note *started*.
  `SessionRecorder._write_note()` derives it by subtracting the measured
  `duration_seconds` back off the finalization hop's timestamp, matching
  `batch_transcribe.NoteEvent.onset_time`'s convention and what a target-
  melody file's own `t` values mean (see `practice_scorer.py`) -- this
  was a real bug caught while building this prototype: an earlier version
  logged the finalization timestamp directly, which silently broke every
  alignment in the scorer (everything came back near-0% until fixed; see
  git-blame-equivalent history in this prototype's own commit if this
  ships for real -- worth calling out here since it's an easy mistake to
  reintroduce).
- **Both `duration_hops` (the raw measurement) and `duration_class` (the
  snapped standard note value) are logged, not just one.** `duration_hops`
  is what `DurationTracker` actually measured, useful for anyone wanting
  the un-lossy raw number (e.g. a future stats feature computing genuine
  timing tightness); `duration_class` is what a human or a notation
  renderer wants directly, already computed via the same
  `duration_tracker.duration_class_for_beats()` the live `tab` view's
  duration glyphs use -- no duplicate logic, no risk of the log's
  "quarter"/"eighth" vocabulary drifting from the app's own.
- **Two event kinds only, for now.** `"note"` covers both monophonic and
  chord-tone events (a chord's three notes each log as their own `"note"`
  line, sharing one `t` and one `chord_name` -- see the C-major-chord
  example under Testing below) rather than inventing a separate `"chord"`
  kind that duplicates most of the same fields; `"barline"` is its own
  minimal kind since it carries none of a note's fields at all. This
  matches Concept E's own mockup in the research doc, trimmed to what
  this prototype could actually demonstrate meaningfully rather than
  speculatively adding kinds nothing here exercises.

## System architecture reasoning

**Where a real `SessionRecorder` would hook into `analysis_loop()`.**
`main.py`'s `analysis_loop()` constructs and pushes one `RenderItem` per
hop at its very end:

```python
item = RenderItem(target_rgb, is_onset, label, freq, confidence, rms, fifths_idx, pitch_class, octave,
                   note_stack, chord_name, duration_hops, bpm_estimate)
hop_index += 1
_overwrite(result_queue, item)
```

(`main.py`, inside `analysis_loop()`, immediately before the
`_overwrite(result_queue, item)` call -- see the `RenderItem` NamedTuple
definition a few dozen lines above it, and `CLAUDE.md`'s Architecture
section for the three-thread pipeline this sits in.) A real integration
adds exactly one line right there:

```python
if session_recorder is not None:
    session_recorder.record_hop(item, hop_index * hop_seconds)
```

`session_recorder` would live on `SessionState` (the same lazily-created,
process-lifetime bundle that already owns `AudioCapture`/the analysis
thread/`Sensitivity`/`SourceState`, per `CLAUDE.md`'s "Process/session
lifecycle" section) so it persists across `|`-key tool switches exactly
like everything else `SessionState` owns, opened once via
`ensure_started()` rather than per-view. `record_barline()`'s call site is
equally direct: right next to `run_terminal_tab()`'s existing
`push_barline()` call in its beat-accumulator logic.

**Cost on the live path.** This is genuinely cheap, in the same spirit as
this project's other "always-on, no gating needed" pipeline stages (chord
mode, rhythm tracking -- see `CLAUDE.md`'s Architecture note on why those
run every hop regardless of which view is active):

- `record_hop()` itself does no I/O at all on the overwhelming majority of
  hops -- it only writes when `duration_hops`/a `note_stack` entry's own
  `duration_hops` is non-`None`, which is precisely the "notable hop"
  policy documented in `session_recorder.py`'s module docstring: a note
  finalizes roughly once per note played, not once per hop (~43
  hops/second at this app's `config.BLOCK_SIZE`/`config.SAMPLE_RATE`).
  A real practice session playing, say, 200 notes over a few minutes
  means ~200 file writes total, not tens of thousands.
- Each write is a small `json.dumps()` (a handful of scalar fields, no
  nested structure beyond `note_stack`'s already-small per-entry dict)
  plus an `fh.write()`/`fh.flush()` of well under 200 bytes. On Pi-class
  hardware this is comfortably inside the same latency budget the chord/
  rhythm pipelines already established as fine to run unconditionally
  every hop -- this is *less* frequent and *cheaper per event* than
  those.
- The one real cost worth calling out honestly: `fh.flush()` after every
  write (chosen here so `tail -f` against a live log always shows the
  latest event, and so a crash never loses the last-written line) means
  every note-finalization hop pays one `write()` syscall's worth of
  latency, not just a buffered in-memory append. Still small relative to
  a hop's own detection cost, but a production integration might
  reasonably relax this to a periodic flush (e.g. every N events or every
  few seconds) if profiling on real Pi hardware ever shows it mattering --
  not chased further here since this prototype's job was proving the
  mechanism, not micro-optimizing an unmeasured cost.

**What this unlocks**, concretely referencing
`docs/research/notation-and-feature-ideas.md`:

- **Feature 1 (session recording + playback,
  `virtualnote replay session.jsonl`)** -- `SessionPlayer` here is exactly
  the reader half; a real `replay` subcommand would extend it to re-drive
  `TabDisplay.push`/`push_notes`/`push_barline`/`finalize_duration` from
  these same timestamps instead of just printing them, per that feature's
  own description.
- **Feature 2 (export to ABC/MIDI/MusicXML)** -- an exporter becomes "read
  the JSONL log, emit tokens in the target format," fully decoupled from
  the live render path, exactly as that feature idea describes ("an ABC
  exporter is just... no coupling to the live render path required").
- **Feature 3 (practice mode)** -- `practice_scorer.py` here is a first,
  intentionally naive cut at exactly what that feature idea calls for: "a
  naive 'nearest-expected-beat' match, no full dynamic-time-warping,
  would cover a first version." A real practice-mode integration would
  run this scoring *live*, hop-by-hop against an already-loaded target
  (not just post-hoc against a finished log the way this prototype does),
  but the alignment/scoring core is the same.
- **Feature 4 (historical stats)** -- a stats module reading a directory
  of these `.jsonl` files (one per session) is pure aggregation over
  already-logged data, no new detection work, per that feature's own
  scoping.

**Honest downsides / risks:**

- **Disk usage over long sessions.** ~200 bytes/event, ~1 event per note
  played (not per hop) keeps this small in practice (a busy hour of
  playing might be a few hundred KB, not megabytes) -- but there is no
  built-in rotation, size cap, or expiry here. A real integration
  practicing daily for months would want *some* retention policy (e.g. a
  `config.toml`-style `[preferences]` knob, same pattern
  `rhythm_reanalysis_window_seconds`/`tab_scrollback_seconds` already
  established) before this ships unattended.
- **Should recording be opt-in?** Yes, unambiguously, and this prototype
  takes no position otherwise -- it never runs unless a caller explicitly
  constructs a `SessionRecorder` and calls `record_hop()`. A real
  integration should keep it that way: off by default, an explicit flag
  or Settings-screen toggle to turn on, mirroring this project's existing
  "no side effect without an explicit action" posture (`CLAUDE.md`'s own
  reasoning for why `SessionState`'s mic isn't opened just from sitting at
  the menu screen applies just as much here -- writing a persistent record
  of what someone played is a bigger, more deliberate side effect than a
  transient in-memory render item, and shouldn't happen silently).
- **Privacy.** A session log is, in effect, a permanent transcript of what
  someone played, written to plain disk with no encryption and (unlike
  the existing `tab`-view `dump_ansi()` text dump, which is also
  unencrypted plain text, so this isn't a new category of exposure) no
  access control beyond the filesystem's own. Anyone building this for
  real should document that plainly to the user (same spirit as this
  being "a personal real-time audio-to-color visualizer" per
  `CLAUDE.md`'s own framing -- a single-user local tool, not multi-tenant
  software with a real threat model, but still worth being explicit that
  "everything I've ever played" is recoverable from a directory of these
  logs once the feature exists).

## Testing

Beyond `demo.py`'s own printed output above, two extra checks were run
directly (not saved as files here, since this prototype's own scope note
says "don't over-build" the testing -- these are the two properties worth
calling out explicitly, both confirmed working):

1. **A real `main.RenderItem` namedtuple** (not a dict) fed straight into
   `SessionRecorder.record_hop()` produces a correctly-shaped `"note"`
   event -- confirms the "accept real RenderItem-like objects or plain
   dicts with the same field names" requirement actually holds, not just
   for the dict path `demo.py` happens to exercise.
2. **A fabricated chord-mode hop** (`note_stack` with 3 entries all
   sharing `duration_hops=40`, `chord_name="C"`) produces three separate
   `"note"` lines, one per chord tone, each carrying `chord_name: "C"` and
   its own correct `pc`/`octave`/`label` -- confirms the chord path
   (`note_stack` iteration in `record_hop()`) works independently of the
   mono `duration_hops`-pairs-with-the-previous-hop logic.
