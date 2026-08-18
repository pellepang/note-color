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

## Status

Working end-to-end and verified live: unit tests pass (`pytest tests/`,
20 tests), and detection has been confirmed with a real speaker→mic
acoustic round-trip test. Pitch-tracking accuracy on real audio varies
run-to-run with room/mic conditions — inherent to monophonic pitch
tracking, not a bug to chase without a concrete symptom.

## Backlog (open problems, not yet fixed)

None currently open.

## Architecture

```
mic -> AudioCapture (PortAudio callback thread, never blocks)
    -> bounded queue.Queue (drop-oldest on full)
    -> analysis thread: rolling 2048-sample ring buffer
                         -> pitch_detect.detect_pitch()   (YIN)
                         -> note_smoother.NoteSmoother     (stabilize)
                         -> color_map.note_to_hsl()        (note -> color)
    -> single-slot queue.Queue (always overwritten with latest result)
    -> render loop (GUI window, or one of two terminal views)
```

Three threads, connected by non-blocking queues at every boundary, so no
stage can ever stall another. Target end-to-end latency: comfortably under
150ms.

## Files

| File | Responsibility |
|---|---|
| `config.py` | All tunable constants (sample rate, buffer sizes, thresholds, color/animation params). Check here first. |
| `audio_capture.py` | `AudioCapture` — `sounddevice.InputStream` callback → bounded drop-oldest queue. `resolve_loopback_device()` finds the system-output monitor for `--source loopback`. |
| `pitch_detect.py` | `detect_pitch()` — hand-rolled YIN (pure NumPy, FFT autocorrelation + parabolic interpolation). |
| `note_smoother.py` | `NoteSmoother` — silence/confidence gate, median filter, debounce, onset detection. |
| `color_map.py` | `note_to_hsl()`, `hsl_to_rgb255()`, `fifths_index()`, `NOTE_NAMES`. |
| `staff_map.py` | `staff_row()`, `ledger_rows()` — grand-staff placement, used only by `tab` view. |
| `animation.py` | `ColorAnimator` — crossfade + onset pulse. Used by GUI and terminal-fill views. |
| `display.py` | `Display` — pygame GUI window (fullscreen, debug overlay). |
| `terminal_display.py` | `TerminalDisplay` — ANSI truecolor full-terminal fill. |
| `terminal_wheel_display.py` | `WheelDisplay` — 12-note fifths ring, always fifths color regardless of `--color-scheme`. |
| `terminal_tab_display.py` | `TabDisplay` — scrolling grand-staff note history; `dump_ansi()` on quit. |
| `main.py` | Wires threads together, dispatches GUI/terminal views by CLI flag. `pygame` imported only inside `run_gui`. |
| `tests/` | `test_pitch_detect.py`, `test_note_smoother.py`, `test_color_map.py`, `test_staff_map.py`. |

## Running it

```
cd ~/note-color
.venv/bin/python main.py                              # GUI window (chromatic scheme)
.venv/bin/python main.py --terminal --view fill        # terminal fill
.venv/bin/python main.py --terminal --view wheel        # terminal circle-of-fifths ring
.venv/bin/python main.py --terminal --view tab --scroll onset   # scrolling staff, new column per note-attack
.venv/bin/python main.py --terminal --view tab --scroll fix     # scrolling staff, new column every tick
.venv/bin/python main.py --color-scheme fifths           # any mode, fifths hue mapping instead of chromatic
.venv/bin/python main.py --terminal --view fill --source loopback  # listen to system audio output, not mic
.venv/bin/python -m pytest tests/                          # run the test suite
```

Shorter launchers on PATH (`~/.local/bin/colorize`, added to `~/.zshrc`'s
PATH): `colorize fill`, `colorize circle`, `colorize tab fix`, and
`colorize tab onset` — thin wrappers forwarding extra flags (e.g.
`colorize tab onset --color-scheme fifths`).

The `tab` view writes an ANSI-colored note-history dump to a timestamped
file next to `main.py` on quit (override with `--dump-file PATH`).

GUI controls: `Esc`/close window to quit, `F` fullscreen, `D` debug overlay,
`Up`/`Down` decrease/increase pitch sensitivity. Terminal modes: `Ctrl+C` to
quit, `Up`/`Down` sensitivity, `M` toggle audio source live (needs a real
TTY; no-op otherwise, e.g. piped input). `--sensitivity FLOAT` sets the
starting value (default 1.0); raises it to register quieter/softer playing
more readily. Current value shown in the status line (`sens=`).

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

## Key design decisions

One-liners; full rationale in `docs/DECISIONS.md`.

- Python + NumPy — cheap enough at these buffer sizes, no build toolchain.
- Hand-rolled YIN, not `aubio`/`librosa` — wheel/dependency risk on Pi.
- Microphone is the default input; `--source loopback` is opt-in and
  Linux-only (PipeWire/PulseAudio monitor), so portability of the default
  path is unaffected.
- Monophonic only — simpler, real-time, fits the use case.
- `pygame-ce` for the GUI — reliable wheels across target platforms.
- `--color-scheme fifths` is additive; `wheel`/`tab` always use fifths so a
  note's color stays consistent between views.
- `tab` uses a grand staff, not single treble — manageable ledger lines
  across the app's 4-octave range.
- `tab`'s left legend column shows a treble/bass clef glyph on each staff's
  anchor line (G4/F3) and the natural-note name on every other staff line
  — added so the grand staff is legible without already knowing note
  positions by heart, especially in the bass register.
- `tab`'s on-quit dump is plain text, not a rendered image.
- `tab`'s note color ignores octave, fixed lightness
  (`TAB_NOTE_LIGHTNESS = 0.5`) — octave already encodes as staff row.
- Terminal views clear on detected resize — avoids ghosting under tiling WMs.

## Known limitations / things learned

One-liners; full detail in `docs/DECISIONS.md`.

- Octave-error blips (~100ms) can occur during note decay; not worth fixing
  without a concrete complaint.
- Live pitch-tracking quality varies run-to-run with room/mic conditions —
  not a regression.
- Target 64-bit Raspberry Pi OS (Bookworm+) — 32-bit is a wheel risk.
- macOS/Windows gate mic access per-app; a denied prompt gives silent zeros,
  not an error.
- `~/.local/bin` is on PATH via `~/.zshrc`, for `colorize`.
- `tab --scroll onset` freezes on sustained notes/silence, by design.
- Terminals <~22 rows clip outermost `tab`-view ledger-line notes.

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
