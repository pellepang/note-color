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

## Backlog (open problems, not yet fixed)

- **Visual bugs in the `tab` view.** User-reported (2026-08-18), symptoms
  not yet captured in detail — get a concrete repro/description (what's
  drawn wrong, and under what terminal size/conditions) before starting a
  fix. Candidate causes to check first given the recent resize-clear and
  glyph-color changes: interaction between the per-frame cursor-addressed
  redraw in `terminal_tab_display.py`'s `render()` and the two grand-staff
  blocks' row math (`staff_map.py`), and edge cases at small terminal
  sizes (see the existing "clip the outermost ledger-line notes" note
  under Known limitations).
- **Pitch detection isn't sensitive enough.** User wants quieter/softer
  playing to register more reliably, e.g. a runtime-adjustable
  sensitivity control rather than a fixed value. Relevant knobs today are
  all fixed constants in `config.py`: `RMS_SILENCE_THRESHOLD` (0.01, gates
  whether a hop counts as silence) and `CONFIDENCE_THRESHOLD` (0.5, gates
  whether a YIN pitch estimate is trusted) — both in `note_smoother.py`'s
  gating logic. Worth exploring: a CLI flag / hotkey / on-screen slider to
  adjust one or both of these live instead of editing `config.py` and
  restarting.

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
| `color_map.py` | Pure functions: `note_to_hsl(pitch_class, octave, scheme="chromatic"|"fifths")` → HSL, `hsl_to_rgb255()` → RGB. `fifths_index(pitch_class)` maps a chromatic pitch class to its position on the circle of fifths — `(pitch_class * 7) % 12` (7 is its own modular inverse mod 12). Also holds the shared `NOTE_NAMES` sharps-only spelling table. |
| `staff_map.py` | Pure functions: `staff_row(pitch_class, octave)` → a note's row on a grand staff (bass + treble, row 0 = G2, row 12 = E4), `ledger_rows(row)` → which ledger lines a given row needs. Used only by the `tab` view. |
| `animation.py` | `ColorAnimator` — exponential crossfade toward the target color plus a decaying onset "pulse" brightness boost. Used by the GUI and terminal-fill views (not the wheel or tab views, which do their own simpler per-cell pulse/no crossfade). |
| `display.py` | `Display` — pygame GUI window (fullscreen toggle, solid fill + debug text overlay). |
| `terminal_display.py` | `TerminalDisplay` — ANSI truecolor full-terminal fill, no GUI/display-server dependency. |
| `terminal_wheel_display.py` | `WheelDisplay` — draws all 12 notes as a ring in circle-of-fifths order (C at top, clockwise), highlighting the currently-detected note. Always uses the fifths color mapping regardless of `--color-scheme`, since that's what the diagram is visualizing. |
| `terminal_tab_display.py` | `TabDisplay` — scrolling note-history view: notes enter on the right and scroll left, each placed at its correct grand-staff row (`staff_map.py`) and colored. Owns the on-screen entry deque plus a capped full-session history used to write an ANSI text dump on quit (`dump_ansi()`). |
| `main.py` | Wires it all together: starts capture + analysis thread, dispatches to GUI (`run_gui`) or one of the terminal views (`run_terminal_fill` / `run_terminal_wheel` / `run_terminal_tab`) based on CLI flags. `pygame` is only imported inside `run_gui`, so terminal modes have zero GUI dependency at runtime and work over SSH/headless. |
| `tests/` | `test_pitch_detect.py` (YIN accuracy on synthetic tones), `test_note_smoother.py` (scripted sequences: stable note, octave blip, silence gap, genuine note change), `test_color_map.py` (both color schemes, including the confirmed circle-of-fifths hue table), `test_staff_map.py` (grand-staff row/ledger-line placement). |

## Running it

```
cd ~/note-color
.venv/bin/python main.py                              # GUI window (chromatic scheme)
.venv/bin/python main.py --terminal --view fill        # terminal fill
.venv/bin/python main.py --terminal --view wheel        # terminal circle-of-fifths ring
.venv/bin/python main.py --terminal --view tab --scroll onset   # scrolling staff, new column per note-attack
.venv/bin/python main.py --terminal --view tab --scroll fix     # scrolling staff, new column every tick
.venv/bin/python main.py --color-scheme fifths           # any mode, fifths hue mapping instead of chromatic
.venv/bin/python -m pytest tests/                          # run the test suite
```

Shorter launchers on PATH (`~/.local/bin/colorize`, added to `~/.zshrc`'s
PATH): `colorize fill`, `colorize circle`, `colorize tab fix`, and
`colorize tab onset` — thin wrappers around `main.py --terminal --view
fill|wheel|tab` that forward any extra flags (e.g. `colorize tab onset
--color-scheme fifths`).

The `tab` view writes an ANSI-colored note-history dump to a timestamped
file next to `main.py` when it quits (override with `--dump-file PATH`) —
one line per note (elapsed time, color swatch, note name), intended as a
minimal first step toward possibly replaying/displaying the history later.

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
  `--color-scheme`. The wheel and tab views always show fifths layout
  regardless of this flag: `--color-scheme` only affects fill/GUI. Tab
  originally followed `--color-scheme` like fill did, but that meant the
  same note could render as a different color in `tab` than in `wheel`
  (e.g. B was pink under chromatic, green under fifths) -- since `tab` and
  `wheel` are both meant to show a note's fixed color identity, they now
  always agree with each other, matching `wheel`'s existing fifths-only
  behavior instead of the other way around.
- **`tab` view uses a grand staff (bass + treble), not a single treble
  staff.** The app's usable range is C2–B5 (4 octaves); a single treble
  staff would need 8 ledger lines below it to reach C2. A grand staff
  (how piano music is notated) caps that at 2 ledger lines below and 1
  above, at the cost of rendering two 5-line staff blocks instead of one.
- **`tab` view's on-quit dump is a plain per-line text log, not a rendered
  staff image** — one line per note (elapsed time, ANSI color swatch, note
  name). Chosen to stay minimal (nothing fancier was asked for yet) and to
  stay structured/parseable for a possible future playback feature, rather
  than being a wide unusable ANSI-art block.
- **`tab` view's note color ignores octave, unlike `fill`/GUI, and uses its
  own fixed lightness (`config.TAB_NOTE_LIGHTNESS = 0.5`), not
  `BASE_LIGHTNESS_RANGE`.** `fill`/GUI intentionally scale lightness by
  octave (darker = lower, per `note_to_hsl()`). In `tab`, octave already
  has a job — it sets the note's row on the staff — so also using it for
  lightness made a low C render too dark to read as red and a high C wash
  out toward white. The first fix reused `BASE_LIGHTNESS_RANGE`'s top end
  (0.82, matching the wheel view's peak pulse brightness), but that's
  close enough to white to read as pastel when held continuously rather
  than shown as a brief pulse — lightness 0.5 is where a given
  hue/saturation looks most vivid in HSL, so that's what `_tab_note_rgb()`
  in `main.py` uses instead. Each note letter is still one fixed,
  recognizable color (C is always red); only its vertical position moves
  with octave.
- **All three terminal views clear the screen on a detected size change,
  not just once at startup.** They normally repaint via cursor-addressing
  (not a full clear) every frame to avoid flicker, which only overwrites
  the region the current frame actually draws. Under a tiling WM the
  terminal window resizes constantly as tiles rearrange; without a
  resize-triggered clear, content from the previous (larger) size/layout
  was never overwritten and lingered as ghost/duplicated elements. Each
  display class tracks `self._last_size` and clears once when
  `shutil.get_terminal_size()` differs from the last frame's.

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
- **`tab --scroll onset` freezes the display during sustained notes or
  silence, by design** — a new column only appears on a genuine new
  note-attack (`NoteSmoother`'s `is_onset` flag), so a held note or a quiet
  passage simply doesn't advance the scroll. This is expected, not a bug.
- **Very short terminals (fewer than ~22 rows) will clip the outermost
  ledger-line notes** in the `tab` view — the two 5-line staff blocks
  themselves are never shrunk below their minimum size, so on a small
  terminal, notes far above/below the staff just don't draw rather than
  corrupting the staff layout.

## Working practices

This repo is tracked on GitHub at `github.com/pellepang/note-color`; git is
the system of record for the project's history, not just a backup.

- After each meaningful checkpoint (a fix, a feature, a config/behavior
  change), commit with a message that describes the change and its
  rationale, then push to `origin/main`. That way `git log`/GitHub history
  doubles as the changelog, and no work done in a session gets silently
  lost or forgotten by the next one.
- Keep commits scoped to one logical change rather than batching unrelated
  work together, so the history stays legible and each commit is easy to
  reason about (or revert) on its own.
- Generated run-time artifacts (e.g. `note_history_*.txt` dumps from the
  `tab` view) are gitignored, not committed — they're per-session output,
  not project state.

## Reference

Full original build plan and rationale (pitch detection algorithm choice,
audio pipeline design, build order) is at
`/home/pelle/.claude/plans/i-want-to-make-graceful-stallman.md`.
