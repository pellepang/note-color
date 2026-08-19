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

## Microphone is the default input; loopback is opt-in and Linux-only

Portable across OSes with no OS-specific plumbing (loopback setup differs
completely between WASAPI/PulseAudio/CoreAudio), so mic stays the default
and works everywhere unchanged.

`--source loopback` was added 2026-08-18 so the app can be tested by
playing audio through the computer instead of needing a mic to physically
hear a speaker (and, per a live test, keeps working even with the sink
muted, since PipeWire's monitor tap sits upstream of the mute applied on
the path to the physical output on this machine). Implementation:
`audio_capture.resolve_loopback_device()` shells out to
`pactl get-default-sink`, sets `PULSE_SOURCE` to `<sink>.monitor`, and
opens PortAudio's `pulse` device — no new dependency, but PipeWire/
PulseAudio-only (fails with a clear error elsewhere, e.g. macOS/Windows,
which would need a virtual-audio-driver device instead, not implemented).

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

## Chord mode always runs, `P` is a pure render-thread-local flag

Resolved via live grilling during spec #12's implementation (see wayfinder
map #1, issue #11's resolution comment). The latency budget research
(#8/#10) measured the full chord-mode addition at ~3ms/hop worst case on
a Pi Zero 2 W — a large margin against the 23ms hop budget — so running it
unconditionally every hop, regardless of whether any view has `P` toggled
on, was cheaper than building a mechanism to turn it on/off. Consequence:
`P` needs no shared state at all, unlike `M`'s `SourceState`/
`AudioCapture.restart()` (which changes what's captured); it's exactly the
same shape as `show_debug` in `run_gui`.

## `multipitch.py` computes its own Hann-windowed FFT, not the shared spectrum

The original plan (per #10's resolution) was for `multipitch.detect()` to
reuse `pitch_detect.compute_spectrum()`'s spectrum, same as chroma folding
and YIN, for zero added FFT cost. Implementing spectral peak-picking
against that spectrum directly, though, produced spurious extra "notes":
a single pure tone (no other notes playing) registered 2-3 phantom peaks
a semitone or more away from the real one. Root cause: this pipeline
applies no window function anywhere (matches YIN's existing design), and
an unwindowed (rectangular-window) FFT's spectral leakage decays so slowly
that sidelobes a semitone away still carried 20-30% of the true peak's
magnitude — well above any reasonable peak-picking threshold, and
persisting even after adding both harmonic-consistency pruning and a
minimum-peak-separation check.

Verified empirically (synthesizing a single 440Hz tone and inspecting the
actual magnitude spectrum) that a Hann window suppresses this ringing to a
few bins within a couple of percent, letting simple local-maximum
peak-picking work correctly. `multipitch.detect()` therefore takes the raw
ring-buffer window (not a precomputed spectrum) and computes its own
windowed FFT internally — one extra same-size FFT per hop, still trivially
inside the latency budget's measured margin. `pitch_detect.compute_spectrum()`
and YIN's calibrated (already-tested) behavior are untouched.

## Chroma Gaussian sigma narrowed to 0.25 semitones; bass detection gated on a confidence ratio

Two bugs found via a live speaker→mic round-trip test with a real C major
triad (C-E-G), after all four new modules' own unit tests (built from
hand-constructed pitch-class vectors, not real audio) already passed:

1. **Wrong chord entirely.** `chroma.fold()`'s Gaussian log-frequency
   weighting matrix originally used a 0.5-semitone sigma (per #2's
   resolution). At that width, each candidate pitch class's Gaussian tail
   picked up enough of its neighbors' energy that the resulting chroma
   vector had a non-trivial "noise floor" across most of the 12 bins, not
   just the 3 real notes. A large chord template (e.g. a 7-note `min13`)
   accumulates more of that spread noise floor than a small, correct
   3-note `maj` template loses by comparison, so cosine similarity
   sometimes favored the wrong, larger template outright (`D-13/B` instead
   of `C` for a plain C major triad). Narrowing to 0.25 semitones (still
   wide enough to pass the low-frequency-discrimination test in
   `test_chroma.py`) fixed this on both the synthetic and live-audio case.
2. **Spurious slash chords.** Even after fix 1, a triad voiced entirely
   above `fold_bass()`'s ~250Hz cutoff (nothing below it) still got
   labeled with a wrong bass (e.g. `C/B` for a plain C-E-G triad) — because
   `fold_bass()`'s output in that case is pure spectral-leakage noise, and
   `chord_templates.match()` unconditionally trusted `argmax(bass_chroma)`
   whenever it was nonzero. Measured that noise floor's peak sits around
   0.15x the main chroma's peak, while a genuine sounding bass note sits
   at 0.35x+; added `DEFAULT_BASS_CONFIDENCE_RATIO = 0.25` in
   `chord_templates.py` as the gate between the two.

Both fixes are implementation-level corrections to #2/#4's originally
resolved constants, not new architectural decisions — the mechanism
(Gaussian-weighted harmonic summing, bass-chroma-driven slash naming) is
unchanged.

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
  themselves are never shrunk below their 21-row minimum (top=20, bottom=0),
  so on a small terminal, notes far above/below the staff just don't draw
  rather than corrupting the staff layout. Below that 21-row floor (terminal
  height under ~22 rows including the status line), `render()` additionally
  caps how many staff rows it emits at `usable_rows`, cropping off the
  bottom (bass) rows first, rather than writing ANSI cursor-addressing past
  the terminal's actual height — the latter used to scroll/corrupt the
  fixed-position rendering instead of just cropping. Fixed 2026-08-18: two
  related bugs in `TabDisplay.render()` — out-of-range notes were clamped
  onto the boundary staff row instead of dropped (silently misplacing them),
  and the row-emission loop didn't cap at `usable_rows`, so terminals under
  22 rows always wrote past their real height. See
  `tests/test_terminal_tab_display.py`.
- **A minor-7th chord and its relative-major 6th chord are pitch-class-set
  identical** (Am7 = A-C-E-G, C6 = C-E-G-A, always a minor third apart) —
  inherent to the chords themselves, not a matching bug. Without a
  confident bass note, `chord_templates.match()`'s tie-break deterministically
  picks the lower-root-index template; this is the correct behavior for
  "no distinguishing information available," not a wrong answer.
- **A pure (harmonic-free) low bass tone can be misdetected a semitone off**
  by `chroma.fold_bass()` — real bass instruments' overtones resolve this
  correctly (that's the whole point of the harmonic-summing chroma design);
  a synthesized pure sine with no harmonics at all is an edge case not
  representative of real playing, observed during implementation testing
  but not chased further absent a concrete complaint.
