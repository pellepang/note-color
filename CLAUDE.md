# note-color

Real-time audio-to-color visualizer. Listens to the microphone, detects the
musical note currently being played, and displays a corresponding color —
fast enough to feel live during actual music.

## Goal / constraints (as given by the user)

- Not a web app.
- Must run across a range of hardware, from small devices (Raspberry Pi
  class) up to full desktops — portability mattered more than raw speed.
- User deferred most technical tradeoffs to "best-performing + easiest to
  build" judgment rather than specifying them.

## Licence

MIT, `Copyright (c) 2026 Pelle Ørevik Evensen` (`LICENSE`, `pyproject.toml`
via PEP 639, and the Credits screen). Settled by issue
[#139](https://github.com/pellepang/note-color/issues/139) because map
[#123](https://github.com/pellepang/note-color/issues/123)'s offline
converter downloads third-party neural weights whose terms could not be
evaluated against a repo that had no licence of its own — until then a
public repo with no LICENSE was *all rights reserved*, the opposite of the
intent.

Third-party material is judged in four categories, not one (full rules and
rationale in `docs/DECISIONS.md`):

| Category | Rule |
|---|---|
| Code this repo ships | Permissive or weak copyleft. **Full GPL refused outright**, even behind an optional extra. LGPL is fine (`pygame-ce` already is). |
| Weights fetched at runtime | Never bundled, never fetched silently — **downloaded only after an explicit prompt naming the terms**, pre-answerable via `[preferences].accept_model_terms` for batch use. Same bundle-nothing posture `sf2_playback.py` takes with soundfonts. |
| Training data | Never distributed; judged like evaluation corpora. |
| Evaluation corpora | **Non-commercial is acceptable** — private evaluation is not distribution. Audio never enters the repo; derived numbers are facts and are publishable. |

The load-bearing line: **non-commercial and unlicensed material may inform
a decision, never ship inside one.** An absent licence is forbidden for
anything shipped and permitted for private evaluation; a contradictory one
takes the most restrictive reading unless upstream clarifies. Permissively
licensed weights may be committed under a **1 MB ceiling**; anything larger
downloads. Contributions are inbound = outbound MIT, no CLA, and one
licence covers the whole repo including `docs/research/`.

## Status

Working end-to-end and verified live: unit tests pass (`pytest tests/`,
1391 tests), and detection has been confirmed with a real speaker→mic
acoustic round-trip test — both the original monophonic pipeline and
chord mode (see below). Pitch-tracking accuracy on real audio varies
run-to-run with room/mic conditions — inherent to monophonic pitch
tracking, not a bug to chase without a concrete symptom.

The terminal score editor (map [#85](https://github.com/pellepang/note-color/issues/85),
implementation spec at [issue #98](https://github.com/pellepang/note-color/issues/98))
is implemented end to end: the data model/persistence layer
(`score_editor_state.py`) and the interaction/CLI/menu-wiring layer
(`score_editor_display.py`, `chord_builder_display.py`,
`score_properties_display.py`, `score_editor_picker.py`,
`main.run_score_editor()`) both landed, covering everything map #85's
own scope includes (loading/creating, navigating, editing pitch/
duration/rests/chords, inserting/deleting columns, time signature/key/
tempo, saving back to MusicXML, both `virtualnote edit <path>` and a
live-menu entry) — map #85 is shippable as of this feature, modulo the
four things its own scope explicitly excluded from the start: foreign
(non-`score_writer.py`-produced) MusicXML import fidelity, a print/
engraving-quality formatter, music-theory analysis, and live audio
playback from inside the editor -- the last of which has since been
delivered anyway by map [#99](https://github.com/pellepang/note-color/issues/99)'s
ticket [#120](https://github.com/pellepang/note-color/issues/120)
(audition, piano-mode entry, play-from-cursor with a playhead, metronome
and a loop region), so only the first three remain out of scope. Verified via the full unit suite plus
manual smoke-testing of the interactive loop (a real TTY isn't available
in this environment — see the implementation PR/commit for what was and
wasn't verified that way); not yet used for a real multi-session editing
workflow.

The standalone synth tool (map
[#99](https://github.com/pellepang/note-color/issues/99), build ticket
[#119](https://github.com/pellepang/note-color/issues/119), decision
[#107](https://github.com/pellepang/note-color/issues/107)) is
implemented end to end — four Tab-cycled layouts, an arrow-driven
parameter panel over an always-visible lit input layer, inline patch
load/save and sample-import overlays, custom layouts in their own files,
and its own `shell.py` dispatch branch plus a `virtualnote synth`
subcommand. Its pure logic is unit-tested directly and its interactive
loop is driven headlessly by scripted key streams (ticket #120's
pattern). It has since been **played and heard** by the project owner on
a real TTY with working audio — the audio path, `render()`, and the
tool's ergonomics all confirmed working, alongside the `tab` view's
frozen playback and the score editor's audition/piano mode. Its
by-ear constants (see Known limitations) are still provisional starting
values rather than tuned ones.

Chord mode (chroma-vector chord recognition, toggled via `P` in terminal
views — opt-in/off-by-default in `fill`/`wheel`, opt-out/on-by-default in
`tab`) is implemented per the spec at
[issue #12](https://github.com/pellepang/note-color/issues/12), itself
synthesized from wayfinder map
[#1](https://github.com/pellepang/note-color/issues/1) and its ten
resolved child tickets (#2–#11).

Rhythm/onset/duration/tempo detection (live + batch), and `tab`-view
rhythm notation (duration glyphs, barlines, `tempo=`/`time=` status
fields), is implemented per the spec at
[issue #55](https://github.com/pellepang/note-color/issues/55), itself
synthesized from wayfinder map
[#47](https://github.com/pellepang/note-color/issues/47) and its seven
resolved child tickets (#48–#54). Verified against synthesized
sine-wave test signals (single sustained tone, multi-note melody) through
both the live pipeline's unit tests and an actual `virtualnote transcribe`
run; not yet verified against real (non-synthetic) playing beyond that.

## Architecture

```
mic -> AudioCapture (PortAudio callback thread, never blocks)
    -> bounded queue.Queue (drop-oldest on full)
    -> analysis thread: rolling 2048-sample ring buffer
                         -> pitch_detect.compute_spectrum()      (shared FFT)
                         -> pitch_detect.detect_pitch()          (YIN, monophonic)
                         -> note_smoother.NoteSmoother            (stabilize, onset-gated)
                         -> color_map.note_to_hsl()               (note -> color)
                         -> chroma.fold() / fold_bass()           (chord mode)
                         -> multipitch.detect()                   (chord mode, up to 6 notes)
                         -> chord_smoother.ChordSmoother           (stabilize chord+notes)
                         -> chord_templates.match()                (chord mode, chroma -> name)
                         -> onset_detect.chroma_flux()              (rhythm mode, novelty signal)
                         -> tempo_tracker.TempoTracker               (rhythm mode, live bpm estimate)
                         -> duration_tracker.DurationTracker          (rhythm mode, mono + chord, per-hop)
    -> single-slot queue.Queue (always overwritten with latest RenderItem)
    -> render loop (GUI window, or one of three terminal views, or the menu)

batch_transcribe.transcribe() (issue #55, offline, `virtualnote transcribe`)
    -> the same per-hop pipeline above, driven by array slicing instead of
       AudioCapture, no live queues/threads involved
    -> duration_tracker.DurationTracker.finalize_noncausal() (per note-slot,
       non-causal refinement) + librosa.beat.beat_track() (tempo)
    -> TabDisplay.dump_ansi() (same on-quit dump format the live `tab` view uses)

any note source (`virtualnote replay --play`, later the synth tool/editor/MIDI)
    -> sound_engine.NoteOn (pitch, velocity, channel, patch) -- note-on/note-off only
    -> sound_engine.SoundEngine.note_on()  (one process-wide engine, lazily started)
        -> Engine.note_on() -> Voice        (tone_engine.ToneEngine today; #113 synth next)
        -> VoiceManager                      (hard polyphony cap, oldest-released-first stealing)
    -> OutputStream callback: Voice.render() summed -> np.tanh -> device
```

The chord-mode pipeline (chroma/multipitch/chord_smoother/chord_templates)
and the rhythm pipeline (onset_detect/tempo_tracker/duration_tracker) both
always run every hop regardless of whether any view has `P` toggled on or
is even `tab` — cheap enough that gating either wasn't worth the extra
shared state (see Key design decisions). `P` is a pure render-thread-local
flag; rhythm detection has no toggle at all, live or in `tab`'s render.

Three threads, connected by non-blocking queues at every boundary, so no
stage can ever stall another. Target end-to-end latency: comfortably under
150ms.

Audio *output* (map #99, decision #105) is a separate, independently lazy
path: `SessionState.ensure_sound_engine()` opens one process-wide
`sound_engine.SoundEngine` on first use and keeps it for the process's
life, exactly as `ensure_started()` does for input — so switching tools
never drops or reopens the output device, and a tool that only plays
(the score editor) never opens the mic, nor vice versa. `virtualnote
transcribe --play` bypasses all of it (offline pre-render, still
`playback.render_offline()`); `virtualnote replay --play` builds its own
`SoundEngine`, since that entry point constructs no `SessionState`.

**Process/session lifecycle (issue #40).** `AudioCapture`, the analysis
thread, `Sensitivity`, and `SourceState` are bundled in `main.SessionState`
and created lazily — on first tool entry, not at process start — via
`SessionState.ensure_started()`, an idempotent call both `main()` (eager,
called once) and `virtualnote`'s menu shell (lazy, called before every tool
entry) can make freely. Once created, a `SessionState` persists for the
rest of the process's life: `virtualnote`'s menu loop (`shell.py`) reuses
the same one across every "pick a tool -> run it -> `|` back to menu ->
pick another tool" round-trip, so switching tools never tears down or
reopens the mic (unlike `M`'s deliberate `AudioCapture.restart()`, a real
source *change*, not a tool switch) — that persistence is what makes `|`
an instant transition rather than a relaunch with startup latency.
`main.run_session(view, ..., session)` is the shared dispatch point both
`main()` (standalone, one-shot) and `shell.run_menu_loop()` (repeated)
call into; each terminal run_* function and `run_gui()` return an explicit
sentinel, `"quit"` or `"menu"`, instead of swallowing `KeyboardInterrupt`
into an implicit `None` as before — see Key design decisions.

**Glossary note (map #145, ticket #150).** **Track** now means a DAW timeline
lane; the notation sense this project used to call a Track is a **Part** —
MusicXML's own word. `ConversionResult.parts` and the project manifest's
`parts` key carry the new name, and `read_project_manifest()` still accepts a
version 1 manifest's `tracks`. Note the words that were *not* renamed:
`BeatTracker.track()`, `DurationTracker` and `tempo_tracker` are the verb
sense — to track something — and are untouched. See `CONTEXT.md`.


## VisualNote Studio (the GUI front-end)

`visualnote` opens the windowed front-end (wayfinder map
[#145](https://github.com/pellepang/note-color/issues/145)), installed as its
own console script plus a `.desktop` entry so it starts from the application
menu with no terminal. It is a **second front-end over the same core**, not a
replacement: `virtualnote` keeps every terminal tool, and parity is promised on
the core rather than on the pixels.

Milestone 1 is done: import a MusicXML file (one **Part** per **Track**), hear
it through the existing `SoundEngine`, watch the playhead move. The load-bearing
detail is that **the playhead is not driven by a Qt timer** -- `SoundEngine`'s
audio callback advances `audio/transport.py`'s `Transport` via
`set_block_listener()`, and the window reads a published snapshot. The Qt timer
only decides how often to repaint, so a slow frame looks choppy but is never
wrong. Measured on a real device: 259 blocks in 3.01s at 44100/512, zero xruns.

Audio is optional -- no output device or no `[synth]` extra means the window
still opens and shows the project, with the reason in the transport bar, the
same posture the score editor's `sound=unavailable` already takes.

| Module | Responsibility |
|---|---|
| `gui/theme.py` | The visual language: the **Copper** palette (taken from the theme's own JSON), JetBrains Mono Nerd Font, translucent surfaces, and the standing rule that **saturation means pitch and nothing else** -- chrome is muted, only notes are vivid, and Copper's own orange is confined to hairline scale (playhead, record letter). Owns the Qt stylesheet too, so no widget names a raw hex value. |
| `gui/studio.py` | The arrange window -- ruler, track headers, `QGraphicsView` canvas (per #147), transport bar. Owns no clock; reads the transport's snapshot. |
| `gui/app.py` | `visualnote`'s entry point: load a `.ncproj` bundle or a MusicXML file, start audio, show the window. |
| `audio/player.py` | `ProjectPlayer` -- turns the transport's half-open beat window into note-ons, with note lengths scheduled against the callback's frame clock. Honours mute/solo. |
| `notation/musicxml_import.py` | MusicXML -> `Project`, one Part per Track. The third module permitted to import `music21`. Deliberately not `score_editor_state.load_score()`, which refuses multi-part files -- exactly the limit this lifts. |

## Package layout

The code lives in a `src/notecolor/` package (wayfinder map
[#145](https://github.com/pellepang/note-color/issues/145), ticket
[#146](https://github.com/pellepang/note-color/issues/146)), not as flat
modules at the repo root as it did until then. **The Files table below names
modules by their bare name; each one lives in the package shown here.**

| Package | Holds |
|---|---|
| `notecolor/analysis/` | Detection and music/colour theory: `pitch_detect`, `chroma`, `multipitch`, `note_smoother`, `chord_smoother`, `chord_templates`, `onset_detect`, `tempo_tracker`, `duration_tracker`, `detection_backends`, `staff_map`, `color_map`, `animation`, `batch_transcribe`, `rhythm_reanalysis` |
| `notecolor/audio/` | `audio_capture`, `sound_engine`, `synth_engine`, `tone_engine`, `sampler`, `sf2_playback`, `effects`, `wav_io`, `playback`, and `session` (the live-capture bundle) |
| `notecolor/notation/` | `score_writer`, `score_editor_state`, `score_audition`, `abc_export`, `log_import`, `session_player`, `session_recorder` |
| `notecolor/convert/` | `convert`, `transcribe_backends`, `evaluate`, `synth_corpus`, and the vendored `vendor/basic_pitch/` graph (which sits beside `transcribe_backends.py` because that module resolves it relative to its own file) |
| `notecolor/project/` | The DAW document -- Project, Track, Clip, tempo map. Empty until ticket [#149](https://github.com/pellepang/note-color/issues/149) settles the `.ncproj` schema. |
| `notecolor/settings/` | `config`, `config_store`, `patch_format` |
| `notecolor/tui/` | The terminal front-end: every `*_display` module, `kitty_keys`, `shell`, `menu_animation`, the synth tool's own modules, `tab_playback`, plus `app` (what `main.py` was) and `cli` (what `virtualnote.py` was) |
| `notecolor/gui/` | The windowed front-ends: `display` (the legacy pygame visualiser) today, VisualNote Studio next |

**The one rule, enforced by `tests/test_package_boundary.py`: no module
outside `tui/` and `gui/` may import a UI toolkit (`blessed`, `pygame`,
`PySide6`) or import a front-end package.** The test walks the real import
graph with `ast`, so it catches lazy in-function imports too -- which matters
here, since heavy dependencies are deliberately imported inside the functions
that need them. It caught two genuine violations on its first run.

This is what makes the two front-ends possible: **parity is promised on the
core, not on the pixels.** A feature is built in a core package, surfaced in
one front-end first, and reaches the other when it has an honest
representation there.

`main.py` is gone as a runnable script. `SessionState`, `analysis_loop`,
`RenderItem`, `Sensitivity`, `SourceState` and the reanalysis buffer moved to
`notecolor/audio/session.py` -- they are core, and leaving them inside the
terminal app would have forced `gui/` to import `tui/` to reach them. The
`run_*` view drivers stayed behind in `notecolor/tui/app.py`, which still
re-exports the moved names so existing callers and tests are unaffected. Run
the terminal app via the `virtualnote` console script, or
`python -m notecolor.tui.cli`.

## Files

Per-module responsibility reference — moved to `docs/FILES.md` to keep this always-loaded file small (same convention as `docs/DECISIONS.md` for full design rationale). One row per file; check there first.

## Running it

```
cd ~/note-color
virtualnote                                            # menu -- pick a tool live
virtualnote fill                                       # straight to terminal fill
virtualnote wheel                                       # straight to terminal circle-of-fifths ring
virtualnote tab onset                                    # straight to scrolling staff, new column per note-attack
virtualnote tab fix                                       # straight to scrolling staff, new column every tick
virtualnote gui                                            # straight to the GUI window
virtualnote fill --color-scheme fifths                      # any tool, fifths hue mapping instead of chromatic
virtualnote fill --source loopback                           # listen to system audio output, not mic
virtualnote tab onset --time-signature 3/4                    # barlines placed for 3/4 instead of the default 4/4
virtualnote transcribe song.wav                                 # offline rhythm/tempo transcription, no live audio
virtualnote transcribe song.wav --export-abc out.abc               # same, plus a hand-rolled ABC notation text file
virtualnote transcribe song.wav --play                            # same, plus oscillator+ADSR playback afterward
virtualnote replay session_log_20260101_120000.jsonl              # replay a recorded session through the tab view
virtualnote replay session.jsonl --speed 2                         # same, at 2x the original pacing
virtualnote replay session.jsonl --play                             # same, plus live audio playback per note
virtualnote edit song.musicxml                                        # terminal score editor -- loads it, or creates it blank
virtualnote edit session_log_20260101_120000.jsonl                     # quantize a recording into a score and open it
virtualnote edit session.jsonl --grid eighth --tempo 120 --out my.musicxml  # same, at a chosen grid/tempo/target path
virtualnote synth                                                      # standalone synth -- play the QWERTY keyboard and pads
virtualnote convert song.mp3                                            # offline band-mix -> multi-track score (map #123)
virtualnote convert song.mp3 --mode piano                                # solo piano: no separation, straight to transkun
virtualnote convert song.mp3 --no-notes                                   # chords only -- needs no model installed
visualnote                                                                # VisualNote Studio (GUI), empty project
visualnote song.musicxml                                                   # ... importing a score, one Part per Track
visualnote MySong.ncproj                                                    # ... opening a saved project bundle
.venv/bin/python -m pytest tests/                              # run the test suite
pip install -e .                                                 # src layout; needed after a fresh clone
```

`virtualnote` (on PATH via `~/.local/bin/virtualnote`, added to `~/.zshrc`'s
PATH) is the one entry point for every tool this project offers (issue
#40), retiring the old per-tool `colorize` bash dispatcher. `pyproject.toml`
(architecture-modernization-plan.md §5) declares the same entry point as a
standard `[project.scripts]` console script, so `pip install -e .` also
works for local development instead of relying solely on the hand-written
`~/.local/bin/virtualnote` shim's hardcoded absolute paths; `librosa`/
`music21` (batch-only, never on the live path — see Key design decisions)
live in an optional `[project.optional-dependencies] batch` extra,
`pip install -e .[batch]` for `virtualnote transcribe`/`--write-score`.
SF2 soundfont playback (map #99, ticket #117) lives behind its own third
extra, `pip install -e .[sf2]` — kept separate from `[synth]` because
`pyfluidsynth` additionally needs the **system** `libfluidsynth` (Arch
`fluidsynth`, Debian `libfluidsynth3`, Homebrew `fluid-synth`) and so can
fail in a way no pure-Python extra can; `sf2_playback.py` degrades to
"unavailable" with a reason rather than crashing when either half is
missing, and no soundfont is bundled (one is discovered — see below).
Bare
`virtualnote` opens an animated ANSI menu (`menu_display.py`, issue #42's
design, built in #51) to pick a tool live — a spinning ASCII donut
re-skinned with the circle-of-fifths palette (rim letters in full mode)
beside the tool list: the four audio tools above, a `Settings` entry
(issue #43) for editing keybind remaps and per-note color overrides live
(see the Config file section below), a `Credits` entry (issue #44)
with full attribution, a `Prototypes` entry for running or reading
every `prototypes/*/` entry from inside the app (see `prototypes_display.py`
in the Files table above) — Enter runs the selected prototype's own
demo/harness script live, right in the terminal, so it can actually be
watched working instead of only read about; `i` opens its README for
context — and a `Stats` entry (Feature 4 in
`docs/research/notation-and-feature-ideas.md`) that aggregates every
`session_log_*.jsonl` the `S` session-recording keybind has ever written
into a summary screen: total logged practice time, most-played notes, and
a sessions-by-date breakdown (see `stats_display.py` in the Files table
above) — any key returns to the menu, same convention as Credits; the menu screen itself also names the author and a
clickable donation link (`config.AUTHOR_NAME`/`DONATION_URL`) right below
the title, regardless of which entry is selected. A performance-mode
fallback (half raster, coarser sampling, no letters, half framerate) is
auto-detected at startup for weaker hardware; `--menu-perf-mode
{auto,full,perf}` overrides it (as does `config.toml`'s
`[preferences].menu_perf_mode`, checked when the flag is omitted). Narrow
terminals drop the donut entirely and show a centered text-only list.
`virtualnote <view> [flags]` goes straight to an audio tool instead,
replicating every flag `colorize` used to forward (`settings`/`credits`/
`prototypes`/`stats` have no direct-launch form, menu-only). Both paths run through the same
long-lived process (`shell.py`), not a relaunch per tool — see
Architecture.
`main.py` itself is still directly runnable exactly as before
(`.venv/bin/python main.py --terminal --view fill`, etc.) for anyone who
wants the original single-tool-per-process entry point; it just has no menu
to fall back to (see below).

The `tab` view writes an ANSI-colored note-history dump to a timestamped
file next to `main.py` on quit (override with `--dump-file PATH`).

GUI controls: `Esc`/close window to quit, `F` fullscreen, `D` debug overlay,
`Up`/`Down` decrease/increase pitch sensitivity. Terminal modes: `Ctrl+C` to
quit, `Up`/`Down` sensitivity, `M` toggle audio source live, `P` toggle
chord mode live (needs a real TTY; no-op otherwise, e.g. piped input) —
`fill`/`wheel` start monophonic and `P` opts up into chord mode, `tab`
starts polyphonic (chord mode on) and `P` opts down to monophonic instead.
`tab` view only: `N` toggle notehead render style live, `L` toggle the
clef+note-letter legend column live, `Space` freeze/un-freeze the view
(see below); while frozen, `R` triggers a non-causal rhythm re-analysis,
`Left`/`Right` scroll back/forward through retained history (issue
#77, see below), and `Enter` plays back what's on screen (map #99,
ticket #121, see below).

`S` toggles opt-in live session recording, available in every terminal
view (fill/wheel/tab; GUI has no live-hotkey mechanism, same established
out-of-scope precedent as chord mode's `P`) — and, as `Shift`+S, in the
standalone synth tool (map #99, ticket #122; plain `S` plays a note
there). Same field, same off-by-default posture, same file. Off by default — pressing
`S` opens a plain-text `session_log_<timestamp>.jsonl` file next to
`main.py` and appends one JSON line per finalized note (mono or
chord-tone) from then on; pressing it again closes the file. Current
state shown in the status line (`rec=on/off`). Hooked directly into
`analysis_loop()` (`session_recorder.py`'s `SessionRecorder`), the same
per-hop placement `ReanalysisBuffer` uses (issue #77) rather than the
render thread — `result_queue` is single-slot and overwrite-on-full, so a
render-thread-side recorder would silently miss a finalized note whenever
two hops complete between two polls. Barlines aren't captured in v1 (that
bookkeeping is `tab`-view-only, render-thread-side); each note's `bpm_
estimate` is still logged, so approximate bar boundaries are
reconstructable later if needed. `SessionState` owns one `SessionRecorder`
for the process's whole life (like `sensitivity`/`source_state`), so
recording state and its file both survive `|` back-to-menu tool switches
the same way.

**Recording the synth, and importing a recording (map #99, ticket #122,
decision #110).** `Shift`+S in the synth tool arms the *same* recorder
writing the *same* `session_log_*.jsonl`, extended additively with four
optional fields — `source` (`"played"`; **absent means `"detected"`**, so
every log written before this feature stays valid), `velocity` (MIDI
0-127), `patch`, `pad`. `virtualnote replay` therefore plays a synth
recording back with no changes at all. Timing is wall-clock
(`time.perf_counter()` at note-on, measured at note-off), `t` runs from
the take's first played note, and `duration_hops`/`bpm_estimate` are
written `null` — they mean nothing without a hop-driven pipeline, and a
synthetic hop clock would put a fictional number in a file people read by
hand. A no-key-release terminal's fixed-duration notes are recorded
exactly as played.

Nothing is quantized at capture: a rounded log cannot be un-rounded.
Rounding happens **on import into the editor**, at a selectable grid
(`log_import.py`) — from the menu, the score editor's picker lists
recordings alongside score files and prompts for a grid; from the CLI,
`virtualnote edit <log>.jsonl [--grid N] [--tempo BPM] [--out PATH]`. The
imported score opens *unsaved* at a `.musicxml` sibling path, so a grid
that turned out wrong costs one quit without saving and can simply be
redone against the untouched log. **Not** in scope (map #99 fog):
recording directly into an open score, and always-on recording with an
explicit save.

`virtualnote replay <file> [--speed N] [--dump-file PATH]` plays a
recorded `.jsonl` session log back through a real `TabDisplay` instead of
live audio — the note/chord/barline events `S` recorded reappear on
screen in their original order, paced by their real recorded timestamp
gaps (`--speed 2` replays twice as fast; default `1.0` is real time).
Standalone, offline, no mic/`SessionState` touched at all, same
`transcribe`-shaped CLI entry point (`main.run_replay_session()`); Ctrl+C
stops a replay early and still writes the on-quit ANSI dump
(`--dump-file`, same default-path convention as `tab`/`transcribe`) for
whatever was replayed up to that point. Doesn't replay raw audio (none
was ever recorded) — only whatever pitch/duration/tempo/chord data
`SessionRecorder` logged, so a replay looks like the original session but
can't literally sound like it — unless `--play` (below) is also passed,
which reconstructs an approximation of it via synthesis.

**Score playback (map #24, decision #32).** `virtualnote transcribe
song.wav --play` and `virtualnote replay session.jsonl --play` both add
audio playback through a NumPy oscillator+ADSR synth (`playback.py`) —
no soundfont/sample library, no new dependency (reuses
`sounddevice.OutputStream`, already a dependency via the mic-input side).
`transcribe --play` pre-renders the whole transcription to one buffer and
plays it back after transcription finishes; `replay --play` instead
triggers each note's synthesis live, in step with the note's own column
appearing on screen (so it speeds up/slows down together with
`--speed`). Playback is a genuine approximation, not the original
recording — a plain harmonic-stack tone, not the instrument that was
actually played — reflecting exactly (and only) whatever pitch/duration
data the detection pipeline itself captured, same honesty caveat as the
visual replay above. Never touches the live mic/`SessionState` path
either way.
`--sensitivity FLOAT` sets the starting value (default 1.0); raises it to
register quieter/softer playing more readily. Current value shown in the
status line (`sens=`).

**Global across every tool (issue #40), unaffected by `M`/`P`/`N`/`L`/
`Space` above.** `|` is the always-live back-to-menu keybind — from inside
any terminal view, or the GUI (bound to the unshifted backslash key there,
since pygame reports `|` as backslash+shift rather than a keycode of its
own) — instantly returns to `virtualnote`'s menu with the mic/analysis
thread/sensitivity/source state all still running, no relaunch. Run via
plain `main.py` instead of `virtualnote` (no menu exists there), `|` just
quits cleanly, same as Ctrl+C. `H` toggles a context-sensitive keybind-
legend line shown below the status line (on by default); off, a one-word
`legend(h)`/`helplegend(h)` hint stays in the status line itself so the
toggle stays discoverable either way. Both are session-local state, same
as `P`/`N`/`L`/`Space` — no persistence across runs (that's issue #41's
`[preferences]` table, not yet wired up for `H`).

`P` toggles chord mode (chroma-vector chord recognition, up to 6
simultaneous notes) in any terminal view — GUI-out-of-scope. `fill`/`wheel`
start monophonic (off by default) and `P` opts *up* into chord mode; `tab`
starts polyphonic (chord mode **on** by default) and `P` opts *down* to
monophonic instead — same key, same plain boolean flip, just a different
starting value for `tab` (issue #13's standing decision: the sheet-notation
view is chord-first). Status line swaps `note=`/`freq=`/`conf=`/`rms=` for
one `chord=<name>` field; `sens=`/`src=` unaffected. Per view: `fill`
splits into proportional horizontal bands, one per active note, pitch-sorted
low-to-high bottom-to-top; `wheel` steadily lights active wedges in their
own colors (no pulsing) with the bass wedge bracketed; `tab` stacks up to 6
notes in one scrolling column with the chord name in a header row above it
(shown whenever polyphonic, i.e. by default), and `--scroll onset` advances
on chord-identity change rather than per-note re-attack. Chord names use
jazz symbol notation (`Δ7`, `-7`, `°7`, `ø7`, `+`, ASCII `#`/`b`) with this
project's flat-biased root spelling, and render blank rather than a guess
when nothing in the ~360-template dictionary clears the match threshold.

The `tab` view renders real sheet-music noteheads instead of colored
letter-in-cell blocks (issue #13). `N` toggles between the two live
render styles: *symbol* (default) — an open notehead glyph (U+1D157) with
a real Unicode ♭/♯ accidental marker next to it if needed, no letter or
octave text — and *name* — bare letter + ASCII accidental, no octave
digit (e.g. `Bb`, `F#`; the note's staff row already conveys octave).
Both use `NOTE_NAMES_FIFTHS` spelling and this app's existing per-note HSL
coloring, unaffected by the toggle. Toggling `N` restyles columns already
scrolled onto the screen, not just future ones. `L` toggles the left
legend area (two side-by-side sub-columns, `TAB_LEGEND_WIDTH` wide
together — a narrower clef-glyph column, blank except on its anchor row,
then a letter column labeling every staff row, line AND space alike,
octave-digit-free) on/off live as one unit, reclaiming its full width for
note columns when off. Current state of both shown in the status line
(`notes=`/`legend=`). The on-quit `dump_ansi()` text dump is unaffected by
either toggle — always letter+octave, as before.

Past columns dim as they scroll by (issue #22): the newest visible column
renders at the normal `TAB_NOTE_LIGHTNESS`; every older column's lightness
fades linearly down to `DIM_LIGHTNESS` (the same floor `wheel`'s inactive
wedges use) over `FADE_COLUMNS` (16) columns of age, then holds at that
floor. Hue/saturation are untouched — only lightness moves. `Space`
freezes the view (issue #23): scrolling and the dimming fade both pause,
every currently-visible column jumps to full `TAB_NOTE_LIGHTNESS`
(overriding the fade as if each were the newest column), and the
underlying audio/detection pipeline keeps running in the background
regardless — pressing `Space` again resumes live immediately, with no
catch-up of whatever happened while frozen. Current state shown in the
status line (`frozen=on/off`).

**Rhythm notation (issue #55), `tab`-view-only, no toggle — always on
once a note has a measured duration.** Each note's duration glyph
(*symbol* style: a combining stem, plus a flag per subdivision below a
quarter note and a dot if dotted; *name* style: a short text suffix like
`Bb·8th` instead) reflects however long that note actually sounded,
snapped to the nearest standard note value — computed once its duration
finalizes (necessarily after the column was already pushed, possibly
already scrolled partway off screen) and then fixed for good, unlike
color's continuous per-frame age-fade. Barlines (a distinct, narrower
column type, no notehead) appear at estimated bar boundaries, driven by a
running beat-accumulator against the live tempo estimate and
`--time-signature N/D` (default `4/4`, plain-digit display only — never
auto-detected). Both the tempo estimate and the time signature show in
the status line (`tempo=<bpm|-->`, `time=N/D`); barline placement is an
approximation tied to that live tempo estimate, not exact bar-for-bar
accuracy — expected drift, not a bug (see Known limitations).
`virtualnote transcribe <file> [--dump-file PATH] [--time-signature N/D]
[--write-score [PATH]] [--play]` runs the same rhythm/duration/tempo
detection offline against a pre-recorded audio file instead of live
input — no terminal window, no mic, just a `dump_ansi()`-format text dump
written on completion (same convention/default path as `tab`'s own
on-quit dump). `--play` (map #24's playback engine, see the Score
playback section above) plays the transcription back through an
oscillator+ADSR synth once transcription finishes.

**`R`-key non-causal rhythm re-analysis + Left/Right scrollback (issue
#77), `tab`-view-only, freeze-mode-only.** While frozen (`Space`), `R`
(remappable, `[keybinds].rhythm_reanalysis`, default `"r"`) re-runs the
same non-causal machinery `virtualnote transcribe` already uses offline
(`duration_tracker.DurationTracker.finalize_noncausal()` +
`librosa.beat.beat_track()`) against a rolling live buffer of the last
`rhythm_reanalysis_window_seconds` (Settings-screen numeric field,
default 60s) of already-computed per-hop data — never raw audio, and
never a redo of pitch/chord detection itself, only the rhythm layer on
top of it. On press, a throwaway thread snapshots the buffer and
recomputes off both the render and analysis threads (`rhythm=
recomputing...` shown in the status line meanwhile); once done, already-
displayed duration glyphs are corrected in place
(`TabDisplay.correct_duration()`) and the barline set within the
recomputed window is replaced (`erase_barlines()`/`insert_barline()`) —
the corrected tempo estimate also takes over the `tempo=` status field
until the view is next unfrozen. Independently, `Left`/`Right` scroll
back/forward through `TabDisplay`'s retained history
(`tab_scrollback_seconds`, default 300s) via `render(scroll_offset=N)`;
current offset shown in the status line (`scrollback=-N`) when nonzero.
Both reset the instant `Space` un-freezes (no catch-up of anything that
happened while frozen, same convention freeze itself already follows).
See Key design decisions for the threading approach and Known
limitations for what's out of scope.

**Loop/section markers, `tab`-view-only, freeze-mode-only.** While
frozen, `[`/`]` (remappable, `[keybinds].mark_range_start`/
`mark_range_end`, default `"["`/`"]"`) each mark one end of a range at
"the point in history currently being looked at" — i.e. respecting
whatever `Left`/`Right` scrollback position is active when the key is
pressed, not always the live tail — and, once both ends are set, scope a
subsequent `R`-key reanalysis to just that marked range instead of the
whole rolling buffer. Order-independent (`]` before `[` normalizes the
same as `[` before `]`); the current range (or a single placed end) shows
in the status line (`mark=[1.20s,3.45s]`, or `mark=[1.20s,...]` with only
one end set) whenever at least one mark is placed. Resets on unfreeze,
same "no catch-up" convention scroll_offset/the reanalysis-corrected
tempo already follow.

**Frozen-buffer playback, `tab`-view-only, freeze-mode-only (map #99,
ticket #121, decision #109).** While frozen, `Enter` (hardcoded, not
remappable) plays what is on screen through the sound engine, and a
second `Enter` stops it. Scope is the `[`/`]` marked range if one is set,
otherwise every column currently visible — the same rule the score
editor's own audition uses, so one gesture means one thing app-wide. In
chord mode every note of a column's detected stack sounds, each with its
own tracked duration: the honest playback of what was actually detected,
not a re-voiced chord name (which would sound better precisely by hiding
detection errors there is reason to hear). Column-to-column pacing comes
from the columns' own recorded timestamps (so real gaps, rushes and
pauses survive); each note's *length* comes from its measured
`duration_class` converted against whatever tempo the status line shows
(the `R`-corrected estimate if one exists, else the live one, else
`config.TAB_PLAYBACK_DEFAULT_BPM`), clamped at both ends. Status line
shows `play=<N>notes(enter)` while playing, or
`play=unavailable(<reason>)` if no sound engine could be started (e.g.
SciPy, the optional `[synth]` extra, isn't installed — a missing optional
dependency never takes the view down). Unfreezing stops playback, same
"no catch-up on unfreeze" convention every other frozen-only piece of
state here follows; nothing else about the frozen view (scrollback
position, marks, corrected tempo) is disturbed. **Live-view sonification
does not exist and is not coming**: #109 dropped it on a measured ~163ms
detection-to-sound latency (46ms window fill + 69.7ms `DEBOUNCE_HOPS`
lock-in + 34.8ms output stream + 11.6ms block) that can't be reduced
without damaging detection itself. `fill`/`wheel` keep no note history,
so the feature has no meaning there and they gain no freeze.

**Score editor (map #85, issue #98).** `virtualnote edit <path>` opens a
terminal editor for a MusicXML file — loads it if it exists
(`score_editor_state.load_score()`), otherwise starts a brand-new blank
score to be saved to `<path>` later (`new_blank_score()`). Never touches
the mic/`SessionState`, same as `transcribe`/`replay`; also reachable
live from the menu's `Edit` entry, which shows a picker over existing
`*.musicxml`/`*.xml` files next to `main.py` plus a "New score…" filename
prompt (`score_editor_picker.py`). Cursor = `(column, staff row)` on a
fixed grand staff (`score_editor_display.py`, extending `tab`'s rendering
approach into a random-access buffer instead of a live-scrolling one —
see `CONTEXT.md`'s Score editor glossary):

| Key | Keybind name | Action |
|---|---|---|
| Left/Right | (hardcoded) | move cursor between columns |
| Up/Down | (hardcoded) | move cursor between staff rows |
| Space | `note_toggle` | place/remove a note at the cursor — spelled per the active key signature by default (e.g. G major's F row places F♯); can empty a column all the way to a Rest, same as removing any other note (reversed from an earlier "refuses to remove the last note" rule — direct user feedback, see docs/DECISIONS.md) |
| Shift+Up/Shift+Down | (hardcoded) | shift the note under the cursor a semitone — hardcoded, not remappable, replacing an earlier remappable `+`/`-` (too far from the arrow keys already used for cursor movement — see docs/DECISIONS.md) |
| `,`/`.` | `duration_shorten`/`duration_lengthen` | step the column's duration through the standard note-value set |
| `r` | `clear_to_rest` | empty the column's notes outright in one press (still useful for a multi-note chord column, vs. Space's one-note-at-a-time removal) |
| `i` | `insert_column` | insert an empty (Rest) column before the cursor; cursor follows onto it |
| `x` | `delete_column` | delete the column at the cursor (refuses on the last remaining column) |
| `u`/`U` | `undo`/`redo` | multi-level, bounded (`config.EDITOR_UNDO_MAX_DEPTH`) |
| `z` | `zoom_cycle` | cycle the notehead's render detail: bare glyph → +letter → +octave → +duration text (render-only) |
| `c` | `chords_only_toggle` | swap noteheads for a lead-sheet chord-name-per-column view (render-only) |
| Enter | (hardcoded) | open the **Chord builder** for the column at the cursor |
| `w` | `save` | write to the loaded/target path |
| `t` | `score_properties` | toggle inline editing of the status line's `time=`/`key=`/`tempo=` fields |
| `P` | `piano_mode` | enter/leave **piano mode** (map #99, ticket #120) — letters become the two-octave tracker keyboard; `Esc` also leaves |
| `L` | `play_from_cursor` | play from the cursor to the end, or to the end of a marked `[`/`]` loop region; the view follows the playhead, any key stops |
| `M` | `metronome_toggle` | click along with playback, on the score's own `tempo=`/`time=` |
| `A` | `audition_toggle` | sound notes on cursor movement (default on; placement always sounds regardless) |
| `[`/`]` | `mark_range_start`/`mark_range_end` | mark a loop region at the cursor's column — the `tab` view's own mark keys (issue #77), order-independent |
| `\|`/`H` | (global) | back-to-menu / legend toggle, unaffected |

Status line shows `saved=yes/no` and, at all times (not just while
editing them), the score's `time=`/`key=`/`tempo=` fields — mirroring
`tab`'s own always-visible `tempo=`/`time=` status-field convention —
plus (ticket #120) `mode=edit/piano`, `audition=`, `metro=`, `oct=` (in
piano mode only), `loop=` (once a mark is placed) and `sound=unavailable`
(only when there's no audio engine at all).
Quitting (`|`/Ctrl+C) while `saved=no` needs a second confirming press of
the same key before actually discarding changes — the one editor view in
this app where quitting can lose real work, unlike every other terminal
view's purely ephemeral render state.

The **Chord builder** (Enter on a column, `chord_builder_exit` — default
`b` — to leave) has five independently spinnable/typeahead-able Reels
(root/quality/3rd/5th/7th), drawn as five stacked rows: Up/Down switches
the focused reel, Left/Right spins it (matching the reels' vertical
layout — swapped from an earlier Left/Right-switches/Up/Down-spins
binding inherited unchanged from the prototype this screen was built
from; direct user feedback found that backwards for a vertical list, see
docs/DECISIONS.md), and typing jumps it (a natural letter on ROOT, e.g.
`F`, jumps straight there — `#`/`b` immediately after nudges it a
semitone; a mnemonic like `m7`/`dim`/`b5`/`sus4` on the other reels
auto-commits the instant it's unambiguous). Every spin/typeahead applies
live to the screen's own working chord; `b` commits it into the real
column on exit. Root is ordered around the circle of fifths, same
theming every other fifths-scheme view in this app already uses.

**Inline header editor** (`t`/`score_properties` toggles it — no longer a
separate screen; a post-#98 hands-on-feedback follow-up retired the
original "Score properties" screen, reversing #90's original call,
because leaving the main view for this was unwanted friction, see
docs/DECISIONS.md). Highlights one of the always-visible `time=`/`key=`/
`tempo=` status-line fields at a time: Left/Right moves the highlight
(a *horizontal* strip of fields, opposite the Chord builder's vertical
Up/Down — different widget shapes, not an inconsistency), Up/Down spins
the highlighted field's value exactly as the old screen's reels did
(`score_properties_display.spin_time_signature()`/`spin_key_fifths()`/
`spin_tempo()`, reused unchanged), and typing digits (plus `/` for time
signature) opens a direct-entry buffer on the two typable fields — time
signature as free-form `N/D` (not snapped to the fixed-set spin), tempo
as a plain BPM number — shown in place of the field's normal value while
typing. Enter parses+applies any pending typed buffer and returns to
normal cursor editing; mutations apply directly to the real `EditorScore`
as they happen, same "no separate commit step" convention the old screen
already used.

**Piano mode, audition, playback, metronome, loop region (map #99,
ticket #120, decision #108).** `P`/`piano_mode` switches the editor's
letter keys from commands to a two-octave tracker keyboard
(`zsxdcvgbhnjm` = C..B, `q2w3er5t6y7u` = the octave above); the same key
or `Esc` leaves, and the status line always names the active mode
(`mode=edit`/`mode=piano`). Shift+Up/Shift+Down moves the keyboard's
octave in piano mode (it stays issue #98's transpose in edit mode);
`,`/`.`, the arrows and `[`/`]` all still work there. Keys pressed
**together** land in one column (a chord); keys pressed **in sequence**
fill successive columns, appending to the score when a run passes the
last one and inheriting that column's duration — a distinction only the
kitty protocol's key releases can report, so this is the one view
constructed with `RawKeys(want_kitty=True)`, degrading on any other
terminal to place-without-advancing (`score_audition.PianoEntry`).
Entered notes take the editor's current `,`/`.` duration, never how long
the key was held. Placing a note always auditions it; moving the cursor
auditions too, toggleably (`A`/`audition_toggle`, default on).
`L`/`play_from_cursor` plays to the end of the score, or to the end of a
`[`/`]` marked loop region (the `tab` view's own mark gesture applied to
columns — order-independent, same `_mark_range()`); the view scrolls to
follow the playhead, which renders reverse-video, and any key stops.
`M`/`metronome_toggle` adds a click on the score's own `tempo=`/`time=`,
downbeat a fifth higher. Entering piano mode and playing back are both
strictly non-dirtying — only actually writing a note is an edit. With no
audio engine available (no `[synth]` extra, no output device) the editor
opens and works exactly as before, silently, showing `sound=unavailable`.

**Synth tool (map #99, ticket #119, decision #107).** `virtualnote synth`,
or the menu's `Synth` entry, opens a standalone instrument. Never touches
the mic/`SessionState` (same as `edit`/`transcribe`/`replay`), but does
use this process's one `SoundEngine`, so switching menu -> synth ->
editor -> a live view never tears down and reopens the output device. The
screen is a **parameter panel above an always-visible input layer below**:
the input layer shows exactly the keys the current layout plays, in their
physical keyboard arrangement, tinted by pitch class in the fifths palette
(so a C here is the colour a detected C already is everywhere else) and lit
on press via `animation.ColorAnimator`. Pads tint by their assigned sample
instead — a pad has no pitch to be honest about.

**Every letter and number always plays a note.** There is no state in this
tool in which pressing a key does something other than sound — including
while a load/save/import overlay is open, and including while typing a
patch name into the save overlay (which both types the character and
sounds the note; the overlay says so on screen). That invariant decides
the whole key map by elimination:

| Key | Action |
|---|---|
| letters / numbers | play a note or hit a pad, at **full velocity** — no faked dynamics (real dynamics come from velocity-layered samples and, later, a MIDI controller) |
| `Tab` | cycle layouts — the switch key precisely because nearly every other key plays a note in *some* layout |
| Up/Down | select a parameter |
| Left/Right | sweep the selected parameter's value |
| Shift+Left/Right | coarse sweep (`SYNTH_PARAM_COARSE_STEPS`, ten ordinary presses) |
| Shift+Up/Down | transpose the note keys an octave (pads never transpose — a kick is a kick) |
| `Shift+P` / `Shift+W` / `Shift+I` | patch browser / save patch / import a sample — all **inline overlays** over the panel, never separate screens; the instrument stays visible and playable underneath |
| `Shift+N` / `Shift+B` / `<` `>` / `Shift+L` | start a custom layout by copying the active one / cycle the last-played key's binding kind / nudge its value / save the layout to its own file |
| `Shift+M` | all notes off (the stuck-note escape) |
| `Shift+H` | help legend (`h` itself plays a note here) |
| `\|` | back to the menu, as everywhere else |

None of the `Shift` bindings is remappable through `[keybinds]`, unlike
this app's other 22 — a remap onto a plain letter would silently break the
always-plays invariant. Same hardcoded tier as the score editor's
Shift+Arrow transpose; see `docs/DECISIONS.md`.

**Four layouts, cycled with `Tab`:** (1) two-octave tracker keyboard
(`zsxdcvgbhnjm` / `q2w3er5t6y7u`, black keys where they physically sit — the
same keyboard the score editor's piano mode plays, shared rather than
copied), no pads; (2) one octave plus a row of eight pads — the one layout
that plays a **kit and a synth patch simultaneously**, which is why it
gets its own lower voice budget (`polyphony_synth_dual`, a Settings-screen
field: one cap now feeds two hands, so the risk is a drum hit arriving to
find every slot held by sustained synth notes); (3) the 4x4 pad square
(`1234`/`qwer`/`asdf`/`zxcv`, numbered bottom-row-first like hardware);
(4) any custom layout found in `~/.config/note-color/layouts/*.toml` —
its own file, independent of patches, because a layout describes your
hands, not a sound.

Status line carries `layout=`/`oct=`/`patch=`/`kit=`/`voices=` and, always,
`keys=`: on a terminal that reports key releases it reads `keys=held` and a
held key sustains; on one that doesn't it reads
`keys=fixed 0.35s (no key release)` and every note is that fixed length.
Saying so plainly is deliberate — it is what keeps "why won't notes
sustain?" from becoming a bug report rather than a documented degradation.
The tool still opens and works either way; a drum pad in particular is
perfectly usable with fixed-duration one-shots.

`--source {mic,loopback}` (default `mic`) selects the input: `loopback`
listens to the computer's own audio output instead of the microphone, via
the PipeWire/PulseAudio monitor of the default sink (Linux only — errors
out clearly on other platforms). Useful for testing without playing sound
out loud; confirmed to keep working even while the output sink is muted.
In any terminal mode, `M` toggles live between `mic` and `loopback` without
restarting the process (`AudioCapture.restart()` tears down and reopens the
PortAudio stream on the same queue, so the analysis thread is undisturbed
apart from a ~100ms gap during the switch); current source shown in the
status line (`src=`), with a failed switch (e.g. `pactl` unavailable)
reported inline there instead of crashing.

### Config file

An optional TOML file at `$XDG_CONFIG_HOME/note-color/config.toml`
(falling back to `~/.config/note-color/config.toml`) additively overrides
`config.py`'s defaults — absent, empty, or malformed reproduces today's
exact behavior. Covers three things today, all hot-reloaded live (edit the
file while the app is running, no restart needed):

- `[keybinds]` — remap any of the nine terminal hotkeys (`source_toggle`,
  `chord_mode_toggle`, `notehead_style_toggle`, `legend_toggle`,
  `freeze_toggle`, `rhythm_reanalysis`, `session_record_toggle`,
  `mark_range_start`, `mark_range_end`) plus the score editor's seventeen
  (issue #98 — `note_toggle`,
  `duration_shorten`, `duration_lengthen`, `clear_to_rest`,
  `insert_column`, `delete_column`, `undo`, `redo`, `zoom_cycle`,
  `chords_only_toggle`, `chord_builder_exit`, `save`, `score_properties`;
  ticket #120 — `piano_mode`, `play_from_cursor`, `metronome_toggle`,
  `audition_toggle`, all defaulting to a Shift+letter since plain-letter
  space is nearly exhausted and piano mode claims a two-octave block of
  it, and all four matched **exact-case** like `undo`/`redo` because `m`
  is a note while `M` is the metronome; the editor's loop region reuses
  `mark_range_start`/`mark_range_end` above rather than adding a second
  pair)
  to a different single character, e.g.
  `source_toggle = "x"`. The score editor's transpose (Shift+Up/Shift+Down)
  and every arrow/Enter key are hardcoded instead, never remappable here,
  as is every one of the synth tool's own `Shift`+key commands (ticket
  #119 — a remap onto a plain letter would silently break that tool's
  always-plays invariant), **including its `Shift`+S recording arm**
  (ticket #122, deliberately contradicting decision #110's "remappable
  like every other keybind" for that reason; `rec=`'s meaning is still
  identical across all four views, which is what #110 was asking for —
  see docs/DECISIONS.md) —
  same tier as this app's other hardcoded arrow-key handling (see the
  Score editor section above and docs/DECISIONS.md). `rhythm_reanalysis` (default `"r"`) is the
  tab view's freeze-mode-only non-causal rhythm re-analysis trigger
  (issue #77); `session_record_toggle` (default `"s"`) is the opt-in live
  session-log recorder toggle, available in every terminal view;
  `mark_range_start`/`mark_range_end` (default `"["`/`"]"`) are the tab
  view's freeze-mode-only loop/section markers that scope a subsequent
  `rhythm_reanalysis` press to just the marked range. `undo`/`redo`
  (default `"u"`/`"U"`) are matched case-sensitively by
  `main.run_score_editor()`, unlike every other keybind here (matched
  case-insensitively) — they deliberately share a letter, distinguished
  only by case; see docs/DECISIONS.md. The status line's hotkey hints
  (`(m)`, `(p)`, etc.) reflect the remap. Editable live from the menu's
  Settings screen (below), or by hand.
- `[colors]` — override a note's hue (degrees, 0–360) by name, either
  sharp or flat spelling, e.g. `C = 200` or `"F#" = 45`. Saturation and
  octave-driven lightness are untouched by the override. Also editable
  from the Settings screen.
- `[preferences]` — free-form quality-of-life settings; `menu_perf_mode`
  (hand-edit only, no screen owns it) plus two numeric fields editable
  live from the Settings screen (below):
  `menu_perf_mode = "auto"/"full"/"perf"` (issue #51's menu-donut override
  — see `menu_display._resolve_perf_mode()`);
  `rhythm_reanalysis_window_seconds` (default `60.0`, valid range 5–1800,
  step 5) — how many seconds of recent audio/data the tab view's `R`
  non-causal rhythm re-analysis (issue #77) reaches back over, re-read
  live from `main.ReanalysisBuffer` on every hop so a Settings-screen edit
  takes effect without restarting, see
  `config.RHYTHM_REANALYSIS_WINDOW_SECONDS`; `tab_scrollback_seconds`
  (default `300.0`, valid range 30–3600, step 30) — how far back the tab
  view's freeze-mode Left/Right scrollback can browse, see
  `config.TAB_SCROLLBACK_SECONDS`; `polyphony_standalone` (default `40`)
  and `polyphony_with_detection` (default `24`), both valid range 1–128,
  step 4 — the sound engine's hard voice cap (map #99, decision #105) in
  each of its two contexts, measured rather than guessed (prototype
  #100), see `config.POLYPHONY_STANDALONE`/`POLYPHONY_WITH_DETECTION` and
  `sound_engine.polyphony_for()`; and `polyphony_synth_dual` (default
  `28`, same range and step) — the third context (ticket #119), the synth
  tool's layout 2 playing a kit and a synth patch from one cap, selected
  by `synth_tool.polyphony_for_layout()` through
  `SoundEngine.set_polyphony_override()` rather than a third branch
  inside `polyphony_for()`, so `sound_engine.py` stays ignorant of what a
  layout is. One non-numeric, hand-edit-only entry
  joins `menu_perf_mode`: `soundfont_path` (map #99, ticket #117) — an
  explicit path to an SF2/SF3 soundfont for the SF2 engine, layered over
  `sf2_playback.discover_soundfonts()`'s search of the samples directory,
  the XDG data dir, the standard system locations and a Homebrew prefix.
  Nothing is bundled, so with no soundfont installed anywhere the engine
  reports "no soundfont found" as status. All numeric fields are read/written
  purely through `config_store.py`'s already-generic
  `preference()`/`set_preference()`, no bespoke accessor needed. The rest
  of the table is still reserved for future settings (e.g. #40's
  still-unwired global `H` keybind-legend on/off persistence); see
  `config_store.py`'s docstring for the full schema and
  `docs/DECISIONS.md` for why the schema stops here for now.

**Settings screen (issue #43).** `virtualnote`'s menu has a `Settings`
entry (same tier as any tool) that opens an interactive editor
(`settings_display.py`) for the `[keybinds]`/`[colors]`/numeric-
`[preferences]` overrides above — Up/Down moves between fields, Enter
edits the highlighted one (captures the very next keypress for a keybind
row; opens an inline 0–360 digit entry for a color row; opens an inline
clamped digit entry, bounded to that field's own min/max, for a numeric
row), Backspace/Delete resets a color row straight back to "default" or a
numeric row straight back to its spec default, and `|`/Esc returns to the
menu, same always-live convention every other tool uses. Edits write
straight through `config_store.set_keybind()`/`set_note_hue_override()`/
`set_preference()` and take effect immediately via the store's existing
hot-reload — no restart, same live-UX as `M`/`P`. A remap can't be bound
to `|` or `h`/`H` — both are global keys every terminal loop checks
unconditionally, so binding an action onto either would make that key
double-fire instead of working as a normal remap. Unlike a color field's
hue (which wraps modulo 360, a circular quantity), a numeric field's typed
value is clamped into its `[min, max]` range instead — the correct
behavior for a bounded real-world quantity like a time window.

## Key design decisions and known limitations

One-liner summaries of both — moved to `docs/SUMMARY.md` to keep this always-loaded file small; full rationale for any of them lives in `docs/decisions/` (see `docs/DECISIONS.md`'s index). Skim `docs/SUMMARY.md` first for "why did we do X" — it's one-liners, not the full writeup.

## Working practices

This repo is tracked on GitHub at `github.com/pellepang/note-color`; git is
the system of record for the project's history, not just a backup.

- After each meaningful checkpoint (a fix, a feature, a config/behavior
  change), commit with a message that describes the change and its
  rationale, then push to `origin/main`.
- Keep commits scoped to one logical change rather than batching unrelated
  work together.
- Generated run-time artifacts (e.g. `note_history_*.txt` dumps from the
  `tab` view) are gitignored, not committed.
- When a backlog item is resolved, delete it from this file rather than
  archiving it here — `git log` already preserves that history.
- New design rationale goes into `docs/DECISIONS.md`, not inlined here —
  keep this file to orientation only.

## Reference

- Full design rationale: `docs/DECISIONS.md`.
- Full original build plan and rationale (pitch detection algorithm choice,
  audio pipeline design, build order):
  `/home/pelle/.claude/plans/i-want-to-make-graceful-stallman.md`.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `pellepang/note-color`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (neither exists yet — created lazily by `/domain-modeling`). See `docs/agents/domain.md`.
