# Design decisions and rationale

Full "why" behind choices summarized one-line in `CLAUDE.md`. Read this when
you need the reasoning, not just the outcome.

## Python + NumPy, not a compiled language

DSP at these buffer sizes (1024–2048 samples, ~20-90ms windows) is cheap
enough that Python's overhead doesn't matter, while Python runs unmodified
across Linux/Mac/Windows/Raspberry Pi with no build toolchain.

## Hand-rolled YIN instead of `aubio` or `librosa`

`aubio` has unreliable PyPI wheels (especially ARM/Raspberry Pi, newer
CPython) and often needs compiling `libaubio` from source; `librosa` drags in
a heavy dependency tree (numba etc.) that's a poor fit for Pi-class installs.
Plain YIN is ~80 lines of NumPy with no exotic dependencies.

## Microphone input, not system-audio loopback

Portable across OSes with no OS-specific plumbing (loopback setup differs
completely between WASAPI/PulseAudio/CoreAudio).

## Single dominant pitch only (monophonic, tuner-style)

Not full polyphonic/chord detection — far simpler, stays real-time, reads
well on melodies/vocals/lead instruments. True polyphonic transcription is a
much harder, slower problem.

## `pygame-ce` for the GUI

A solid-fill + crossfade is one of the simplest possible rendering
workloads; pygame has reliable prebuilt wheels across target platforms
(including 64-bit Raspberry Pi OS) with far less install risk than
`glfw`+OpenGL or Kivy.

## Circle-of-fifths color scheme is additive, not a replacement

Chromatic (semitone-order hue) stays the default; `fifths` is opt-in via
`--color-scheme`. The wheel and tab views always show fifths layout
regardless of this flag: `--color-scheme` only affects fill/GUI. Tab
originally followed `--color-scheme` like fill did, but that meant the same
note could render as a different color in `tab` than in `wheel` (e.g. B was
pink under chromatic, green under fifths) — since `tab` and `wheel` are both
meant to show a note's fixed color identity, they now always agree with each
other, matching `wheel`'s existing fifths-only behavior instead of the other
way around.

## `tab` view uses a grand staff (bass + treble), not a single treble staff

The app's usable range is C2–B5 (4 octaves); a single treble staff would
need 8 ledger lines below it to reach C2. A grand staff (how piano music is
notated) caps that at 2 ledger lines below and 1 above, at the cost of
rendering two 5-line staff blocks instead of one.

## `tab` view's on-quit dump is a plain per-line text log, not a rendered staff image

One line per note (elapsed time, ANSI color swatch, note name). Chosen to
stay minimal (nothing fancier was asked for yet) and to stay
structured/parseable for a possible future playback feature, rather than
being a wide unusable ANSI-art block.

## `tab` view's note color ignores octave and uses fixed lightness (`config.TAB_NOTE_LIGHTNESS = 0.5`)

`fill`/GUI intentionally scale lightness by octave (darker = lower, per
`note_to_hsl()`). In `tab`, octave already has a job — it sets the note's row
on the staff — so also using it for lightness made a low C render too dark
to read as red and a high C wash out toward white. The first fix reused
`BASE_LIGHTNESS_RANGE`'s top end (0.82, matching the wheel view's peak pulse
brightness), but that's close enough to white to read as pastel when held
continuously rather than shown as a brief pulse — lightness 0.5 is where a
given hue/saturation looks most vivid in HSL, so that's what
`_tab_note_rgb()` in `main.py` uses instead. Each note letter is still one
fixed, recognizable color (C is always red); only its vertical position
moves with octave.

## All three terminal views clear the screen on a detected size change, not just once at startup

They normally repaint via cursor-addressing (not a full clear) every frame
to avoid flicker, which only overwrites the region the current frame
actually draws. Under a tiling WM the terminal window resizes constantly as
tiles rearrange; without a resize-triggered clear, content from the previous
(larger) size/layout was never overwritten and lingered as ghost/duplicated
elements. Each display class tracks `self._last_size` and clears once when
`shutil.get_terminal_size()` differs from the last frame's.

## Known-limitation detail

- **Octave-error blips during note decay.** YIN can briefly lock onto a
  sub-harmonic as a note's amplitude fades out (harmonics get ambiguous),
  causing a short (~100ms) false reading before self-correcting. Observed
  live during acoustic testing. Not currently worth fixing without a
  concrete complaint — `NoteSmoother`'s median filter/debounce already
  suppresses most single-frame blips; only sustained sub-harmonic locks
  during a fade slip through.
- **Live pitch-tracking quality varies run-to-run** with room acoustics, mic
  gain/AGC settling, and speaker/mic coupling — this is inherent to acoustic
  pitch detection, not a code regression, when comparing two live test runs
  that behave differently.
- **`tab --scroll onset` freezes the display during sustained notes or
  silence, by design** — a new column only appears on a genuine new
  note-attack (`NoteSmoother`'s `is_onset` flag), so a held note or a quiet
  passage simply doesn't advance the scroll. This is expected, not a bug.
- **Very short terminals (fewer than ~22 rows) will clip the outermost
  ledger-line notes** in the `tab` view — the two 5-line staff blocks
  themselves are never shrunk below their minimum size, so on a small
  terminal, notes far above/below the staff just don't draw rather than
  corrupting the staff layout.
