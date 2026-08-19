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

## Notehead render styles (`N`) and merged legend column (issue #13/#20/#21)

`terminal_tab_display.py`'s `TabDisplay.render()` now takes `notehead_style`
("symbol" or "name") and `legend_on` (bool), both owned as render-thread-
local state in `main.py`'s `run_terminal_tab` (same pattern as `chord_mode`
for `P` — no shared state, no `TabDisplay`-side toggle method). `TabEntry`
still carries each note's raw `pitch_class`/`octave` (not just a
precomputed label), so `render()` recomputes on-screen text fresh every
frame via `_cell_text()` — a live `N` press restyles columns already on
screen, not just future ones. `dump_ansi()` is untouched: it reads the
`label` field of `TabEntry.notes` directly (letter+octave), which
`render()` no longer uses at all.

**G-clef bottom-clipping investigation (issue #20, fix 1).** #19's
prototype (`prototype/clef-and-legend-toggle`) theorized the clipping was
a horizontal-overhang problem — an astral-plane glyph rendering wider than
Python's `.center()` assumes — and tried isolating the clef in its own
buffered, bold column. #20 reported that didn't fix it, and asked for
real investigation into whether it's actually a font-coverage or
cell-height problem instead. Checked with Pillow (`ImageFont.getbbox()`)
against the fonts this machine's fontconfig resolves for these codepoints
(`NotoSansSymbols2-Regular.ttf` has no real glyphs for any of them — its
`getbbox()` returns the same box as an uncovered `.notdef`; the actual
covering font is `NotoMusic-Regular.ttf`). Measured ink bounding boxes at
a 1000-unit em, font's own ascent/descent alongside:

| glyph | above baseline | below baseline | font's own descent |
|---|---|---|---|
| G-clef `\U0001D11E` | 1334 | **398** | 398 |
| F-clef `\U0001D122` | 900 | 0 | 398 |
| notehead `\U0001D157` | 269 | 1 | 398 |

The G-clef's design uses its font's *entire* descent allocation (398 of
398) — no other glyph checked comes close. That's real evidence for the
cell-height theory #20 asked about, not the horizontal one #19 assumed:
when a terminal falls back to a rarely-used symbol font for one glyph but
sizes/positions it inside the *primary* (monospace) font's cell box, any
glyph whose design needs more descent than that primary font's own
metrics allocate gets its bottom cut off by the cell's fixed pixel
height — independent of any horizontal buffering, and it explains, glyph
by glyph, exactly which one clips (only the G-clef needs its font's full
descent; the F-clef needs none at all, matching that only the G-clef was
ever reported clipped). No ANSI escape sequence gives an application
control over a fallback glyph's vertical placement/scale inside a
terminal's cell grid, so this isn't fixable from the app layer the way
#19's horizontal buffer was attempted — it's a terminal/font-stack
property. Shipped mitigation: kept the real glyph (the user preferred it live over
#19's variant C plain-text fallback), rendered plain rather than carrying
over the bold styling #19's variant B prototype added — bold synthesis
for a glyph the primary font doesn't cover is another plausible source of
extra vertical overhang, so not adding it is a legitimate, low-risk
choice, not a proven fix. Documenting
this as a known, terminal-dependent limitation rather than a fully
resolved bug — same treatment as the other environment-dependent
limitations already listed in this section — since there's no further
lever available inside the app to pull.

**Legend merge (issue #20, fix 2).** The dedicated-clef-column prototype
(variant B, two side-by-side regions: a clef-only sub-column plus a
letter-only sub-column, mostly empty on any given row) was *not* carried
into the shipped code. The single shared `legend_width`-wide region
already in `terminal_tab_display.py` before this change — clef glyph on
its anchor row, letter name on every other staff-line row, blank
otherwise — already was the "merge into one column" outcome #20 asked
for; it needed no restructuring, just the octave-digit drop (`staff_map.
line_note_name()`) and the `L` toggle wired through.

## Per-column dimming (`Space`-independent fade) and freeze-frame (issue #13/#22/#23)

Both landed directly against the shipped app (not left in a throwaway
prototype) based on a finished reference implementation the user already
iterated to convergence in `prototype_notehead_toggle.py` on
`prototype/notehead-toggle-in-context` (throwaway, never merged) —
porting that algorithm into `terminal_tab_display.py`/`main.py`'s real
threaded architecture rather than re-deriving it.

**Dimming curve (#22).** Two rounds of live reaction rejected the first
cut's approach entirely: round 1 dimmed instantly (no fade) and pushed the
*newest* column toward `config.BASE_LIGHTNESS_RANGE`'s high end — borrowed
from `terminal_wheel_display.py`'s active-note *pulse* convention, which
reads as a washed-out highlight rather than tab's normal, always-fully-
saturated note color. Final shape: the newest visible column renders at
plain `config.TAB_NOTE_LIGHTNESS` (tab's ordinary note lightness, nothing
special); every older column fades *down* from there, linearly, to
`config.DIM_LIGHTNESS` over `config.FADE_COLUMNS` columns of age, held at
that floor beyond it. `FADE_COLUMNS` was doubled twice via live reaction
(4 → 8 → 16) — 16 is the shipped value, not a placeholder to revisit
without new feedback. Only lightness moves; hue and saturation are
untouched, using the exact same `note_to_hsl(..., scheme="fifths")` +
`hsl_to_rgb255()` pipeline the unfaded code already used, just with
`config.TAB_NOTE_LIGHTNESS` replaced by an age-derived value
(`terminal_tab_display._aged_lightness()`).

**Shared `DIM_LIGHTNESS` (#22).** Rather than let `tab` define its own
copy of `terminal_wheel_display.py`'s `DIM_LIGHTNESS = 0.16` (its inactive-
wedge floor), the constant was promoted to `config.DIM_LIGHTNESS` and both
modules import it from there — `terminal_wheel_display.py` keeps its own
`DIM_LIGHTNESS` name as a same-value alias for any external callers/tests
that reference it directly, but the literal `0.16` now exists exactly
once. Same rationale as the existing `NOTE_NAMES_FIFTHS`/`diatonic_step()`
shared-source fix: two independently-hand-copied constants for the same
visual convention are one accidental edit away from silently drifting
apart between the two views.

**Age computation.** Baking a note's color in at push time (as `push()`/
`push_notes()` did before this) can't work once color depends on age,
because a column's age changes every single frame as newer columns scroll
in from the right — the same column is age 0 the instant it's pushed and
age 1 the moment a newer one supersedes it. `TabDisplay.render()` now
recomputes every visible note's color fresh each call from its raw
`pitch_class` (already stored, from issue #21's notehead-restyling work)
and `age = last_index - index` within the *visible* window (0 = newest
visible column, not "newest ever pushed" — an off-screen column scrolled
out of the visible range doesn't matter). The `rgb` field `TabEntry.notes`
still carries from push time is now read only by `dump_ansi()`, which
keeps rendering at fixed brightness (untouched, per the map's standing
decision).

**Freeze-frame (#23).** `Space` is a third render-thread-local flag in
`main.py`'s `run_terminal_tab`, same pattern as `notehead_style`/
`legend_on` — no `TabDisplay`-side state. Two effects, both driven from
one boolean: (1) while frozen, `run_terminal_tab` skips
`result_queue.get_nowait()` entirely, so no new column is ever pushed and
the status line's note/freq/confidence/rms fields hold their last value —
the analysis thread keeps running and overwriting the single-slot
`result_queue` in the background the whole time, per this app's existing
drop-oldest/overwrite architecture, so there's no backlog to build up and
nothing extra to tear down or restart (unlike `M`'s `AudioCapture.
restart()`); (2) `TabDisplay.render(frozen=True)` forces every visible
column's `age` to 0 regardless of its real position, which resolves
through the exact same `_aged_lightness()` formula to full
`TAB_NOTE_LIGHTNESS` — no separate freeze-specific lightness branch was
needed, `render()` just "lies" about age. Un-freezing resumes live
immediately (the very next queue read picks up whatever's current) with
no catch-up/replay of anything that happened while frozen, matching how
`M`'s source-switch and every other toggle in this app already behaves.
