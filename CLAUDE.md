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
acoustic round-trip test (playing known tones through the speakers and
checking the app reports the correct note/color back). Pitch-tracking
accuracy on real audio varies run-to-run with room/mic conditions — see
"Known limitations" below; this is inherent to monophonic pitch tracking,
not a bug to chase further without a concrete symptom.

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
stage can ever stall another: the real-time audio callback drops old audio
rather than blocking; the analysis thread always works on the freshest
window; the render loop always shows the freshest computed color.

Target end-to-end latency: comfortably under 150ms.

## Files

| File | Responsibility |
|---|---|
| `config.py` | Every tunable constant (sample rate, buffer/window sizes, thresholds, color-wheel params, animation timing, FPS). Check here first before touching logic. |
| `audio_capture.py` | `AudioCapture` — wraps `sounddevice.InputStream` in callback mode, feeds a bounded drop-oldest queue. |
| `pitch_detect.py` | `detect_pitch()` — hand-rolled YIN pitch detection in pure NumPy (FFT-based autocorrelation, cumulative mean normalization, parabolic interpolation). No `aubio`/`librosa` dependency, deliberately. |
| `note_smoother.py` | `NoteSmoother` — turns noisy per-hop pitch estimates into a stable displayed note: silence/confidence gate, median filter in semitone space (kills octave-error outliers), debounce/hysteresis before switching the displayed note, onset detection. |
| `color_map.py` | Pure functions: `note_to_hsl(pitch_class, octave, scheme="chromatic"|"fifths")` → HSL, `hsl_to_rgb255()` → RGB. `fifths_index(pitch_class)` maps a chromatic pitch class to its position on the circle of fifths — `(pitch_class * 7) % 12` (7 is its own modular inverse mod 12). |
| `animation.py` | `ColorAnimator` — exponential crossfade toward the target color plus a decaying onset "pulse" brightness boost. Used by the GUI and terminal-fill views (not the wheel view, which does its own simpler per-cell pulse). |
| `display.py` | `Display` — pygame GUI window (fullscreen toggle, solid fill + debug text overlay). |
| `terminal_display.py` | `TerminalDisplay` — ANSI truecolor full-terminal fill, no GUI/display-server dependency. |
| `terminal_wheel_display.py` | `WheelDisplay` — draws all 12 notes as a ring in circle-of-fifths order (C at top, clockwise), highlighting the currently-detected note. Always uses the fifths color mapping regardless of `--color-scheme`, since that's what the diagram is visualizing. |
| `main.py` | Wires it all together: starts capture + analysis thread, dispatches to GUI (`run_gui`) or one of the terminal views (`run_terminal_fill` / `run_terminal_wheel`) based on CLI flags. `pygame` is only imported inside `run_gui`, so terminal modes have zero GUI dependency at runtime and work over SSH/headless. |
| `tests/` | `test_pitch_detect.py` (YIN accuracy on synthetic tones), `test_note_smoother.py` (scripted sequences: stable note, octave blip, silence gap, genuine note change), `test_color_map.py` (both color schemes, including the confirmed circle-of-fifths hue table). |

## Running it

```
cd ~/note-color
.venv/bin/python main.py                              # GUI window (chromatic scheme)
.venv/bin/python main.py --terminal --view fill        # terminal fill
.venv/bin/python main.py --terminal --view wheel        # terminal circle-of-fifths ring
.venv/bin/python main.py --color-scheme fifths           # any mode, fifths hue mapping instead of chromatic
.venv/bin/python -m pytest tests/                          # run the test suite
```

Shorter launchers on PATH (`~/.local/bin/colorize`, added to `~/.zshrc`'s
PATH): `colorize fill` and `colorize circle` — thin wrappers around
`main.py --terminal --view fill|wheel` that forward any extra flags.

GUI controls: `Esc`/close window to quit, `F` fullscreen, `D` debug overlay.
Terminal modes: `Ctrl+C` to quit.

## Key design decisions and why

- **Python + NumPy**, not a compiled language — DSP at these buffer sizes
  (1024–2048 samples, ~20-90ms windows) is cheap enough that Python's
  overhead doesn't matter, while Python runs unmodified across
  Linux/Mac/Windows/Raspberry Pi with no build toolchain.
- **Hand-rolled YIN instead of `aubio` or `librosa`** — `aubio` has
  unreliable PyPI wheels (especially ARM/Raspberry Pi, newer CPython) and
  often needs compiling `libaubio` from source; `librosa` drags in a heavy
  dependency tree (numba etc.) that's a poor fit for Pi-class installs.
  Plain YIN is ~80 lines of NumPy with no exotic dependencies.
- **Microphone input**, not system-audio loopback — portable across OSes
  with no OS-specific plumbing (loopback setup differs completely between
  WASAPI/PulseAudio/CoreAudio).
- **Single dominant pitch only** (monophonic, tuner-style), not full
  polyphonic/chord detection — far simpler, stays real-time, reads well on
  melodies/vocals/lead instruments. True polyphonic transcription is a much
  harder, slower problem.
- **`pygame-ce`** for the GUI — a solid-fill + crossfade is one of the
  simplest possible rendering workloads; pygame has reliable prebuilt
  wheels across target platforms (including 64-bit Raspberry Pi OS) with
  far less install risk than `glfw`+OpenGL or Kivy.
- **Circle-of-fifths color scheme is additive, not a replacement** —
  chromatic (semitone-order hue) stays the default; `fifths` is opt-in via
  `--color-scheme`. The wheel diagram view always shows fifths layout
  regardless of this flag, since that's inherent to what it's visualizing.

## Known limitations / things learned

- **Octave-error blips during note decay.** YIN can briefly lock onto a
  sub-harmonic as a note's amplitude fades out (harmonics get ambiguous),
  causing a short (~100ms) false reading before self-correcting. Observed
  live during acoustic testing. Not currently worth fixing without a
  concrete complaint — `NoteSmoother`'s median filter/debounce already
  suppresses most single-frame blips; only sustained sub-harmonic locks
  during a fade slip through.
- **Live pitch-tracking quality varies run-to-run** with room acoustics,
  mic gain/AGC settling, and speaker/mic coupling — this is inherent to
  acoustic pitch detection, not a code regression, when comparing two live
  test runs that behave differently.
- **32-bit Raspberry Pi OS (`armv7l`) is a wheel-availability risk** for
  both `sounddevice` and `pygame-ce` — target **64-bit** Raspberry Pi OS
  (Bookworm+) instead.
- **macOS/Windows gate microphone access per-app.** A denied/unhandled
  permission prompt on first run delivers silent zeros from the input
  stream rather than an error — if a fresh install "detects nothing,"
  check OS mic permissions before debugging the DSP.
- `~/.local/bin` was not on PATH before this project; it was created and
  added via a new line appended to `~/.zshrc` to support the `colorize`
  launcher.

## Reference

Full original build plan and rationale (pitch detection algorithm choice,
audio pipeline design, build order) is at
`/home/pelle/.claude/plans/i-want-to-make-graceful-stallman.md`.
