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

**Legend merge (issue #20, fix 2) — superseded by issue #36.** The
dedicated-clef-column prototype (variant B, two side-by-side regions: a
clef-only sub-column plus a letter-only sub-column, mostly empty on any
given row) was *not* carried into the shipped code at this point. The
single shared `legend_width`-wide region already in
`terminal_tab_display.py` before this change — clef glyph on its anchor
row, letter name on every other staff-line row, blank otherwise —
already was the "merge into one column" outcome #20 asked for; it needed
no restructuring, just the octave-digit drop (`staff_map.
line_note_name()`) and the `L` toggle wired through. **This call was
reversed by #36** (below) after a live user reaction to the shipped
branch said the merged layout wasn't actually what was wanted — variant
B's two-column split is now what's shipped.

## Two-column legend split and every-row labeling (issue #36)

Live reaction to the shipped `feature/tab-sheet-notation` branch: two
concrete, fully-specified fixes to the legend, on top of everything else
on that branch (confirmed good otherwise).

**Fix 1: label every staff row, not just line rows.** `staff_map.
line_note_name()` — despite its name and docstring, which claimed the
input "must be in `STAFF_LINE_ROWS`" — was already pure `(row +
GRAND_STAFF_REF_STEP) % 7` diatonic-step math with no branch on line-vs-
space; it was already correct for every row, line or space, and simply
undocumented/underused as such. Renamed to `row_note_name()` and its
docstring corrected to state it's general over every row (line, space, or
ledger-line territory beyond the staff) — no logic change, since none was
needed. `terminal_tab_display.py`'s legend-building loop now calls it
unconditionally for every `screen_row` in the render loop's visible range
(which is always exactly the rows actually drawn, so no extra bounds
check is needed), instead of only inside a `screen_row in
STAFF_LINE_ROWS` branch. `tests/test_staff_map.py` gained direct coverage
of space rows (bass clef space mnemonic "All Cows Eat Grass", treble
"FACE"), ledger-line-territory rows (middle C, the range extremes), and a
cross-check against `staff_row()`/`diatonic_step()` for every natural
pitch class/octave in range.

**Fix 2: clef and letter in separate columns.** `config.py` splits the
old single `TAB_LEGEND_WIDTH` into `TAB_CLEF_WIDTH` (3) and
`TAB_LETTER_WIDTH` (2), with `TAB_LEGEND_WIDTH` now derived as their sum
— so the total width the `L` toggle reserves from/returns to the note
columns is unchanged, only how that width is split internally.
`terminal_tab_display.py`'s per-row legend cell is now built as two
concatenated sub-cells: a `TAB_CLEF_WIDTH`-wide clef cell (blank except
on `BASS_CLEF_ROW`/`TREBLE_CLEF_ROW`) followed by a `TAB_LETTER_WIDTH`-
wide letter cell (always populated, per fix 1). The `L` keybind still
toggles the whole `legend_width` region as one unit (`legend_width =
config.TAB_LEGEND_WIDTH if legend_on else 0`, unchanged) — not
fragmented into two independently-toggleable halves, per the ticket's
explicit instruction.

**G-clef clipping, revisited.** #36 asked to retry the G-clef (𝄞)
bottom-clipping investigation now that the clef has genuine dedicated
column space rather than shared cells, in case that happened to help.
It doesn't, and per #20's original investigation (above) there was never
reason to expect it would: the clipping is a *vertical* cell-height
problem (the glyph's covering font, `NotoMusic-Regular.ttf`, draws it
using that font's entire descent allocation, and no ANSI-level control
exists over a fallback glyph's vertical placement inside a terminal's
cell grid) — giving the clef more *horizontal* room via its own column
doesn't touch that axis at all. Not re-verified pixel-for-pixel against a
real terminal/font stack as part of this fix (this environment's smoke
test only inspects the raw ANSI text stream, which can't show font
rendering) — left documented as a known, terminal-dependent limitation in
`CLAUDE.md`, per #36's own instruction to note it again rather than block
on it or re-run the full #20 investigation from scratch.

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

## Config/theming store (issue #41)

Map #37 (unified tool shell) settled format/location/layering by grilling
in #39; this ticket only had to fill in the parts #39 left open: exact
schema and the inventory of what's covered beyond keybinds/colors.

**Schema, scoped down on purpose.** `[keybinds]` covers exactly the five
existing single-key terminal hotkeys (`source_toggle`/`chord_mode_toggle`/
`notehead_style_toggle`/`legend_toggle`/`freeze_toggle`) — `Up`/`Down`
sensitivity and `Ctrl+C` quit were left out of the remap surface, since
they're not single printable characters in the same sense (arrow-key
semantics, and quit-via-signal isn't really a "binding"). `[colors]`
overrides a note's *hue* only, by name (`C`, `F#`, etc., either sharp or
flat spelling on read — canonical write-back is always sharp), leaving
saturation and octave-driven lightness computed as normal; a full
HSL/RGB override was considered and rejected for v1 as scope creep beyond
what #37 actually asked for ("per-note color overrides", read as "the hue
identity of a note", not "arbitrary color replacement"). `[preferences]`
is a deliberately generic bucket for anything else (e.g. #40's future
global `H` keybind-legend on/off state, once #40 exists to read/write it)
— #41 only owns the load/persist mechanics, not any particular
preference's UI or behavior.

**Hot-reload via mtime-stat, not a file watcher.** Every accessor
(`keybind()`/`note_hue_override()`/`preference()`) calls `os.stat()` and
only re-parses the TOML if the mtime changed since last read — cheap
enough to call on every hop/frame (same design constraint that already
justified running chord mode's pipeline unconditionally every hop, and
the same "zero shared state, plain read every frame" shape as `P`/`M`/`N`/
`L`). No `inotify`/`watchdog` dependency, no background thread, no
explicit "reload" call the render loop has to remember to make.

**Absent/malformed file reproduces today's behavior exactly**, per #41's
destination text: `ConfigStore._refresh()` catches `OSError` (file
missing) and `tomllib.TOMLDecodeError`/`UnicodeDecodeError` (malformed)
alike, falling back to an empty `{}` overlay in both cases — every
accessor then falls through to `config.py`'s existing default. Verified
in `tests/test_config_store.py` (missing file, empty file, and garbage
content all resolve to unmodified defaults).

**Wired into the existing views now, not deferred to #40's shell.** #41
is independent of #40 per #37's dependency ordering, and nothing about
remapping `main.py`'s existing hotkeys or overriding `color_map.
note_to_hsl()`'s hue requires the future `virtualnote` entry point to
exist first — so `fill`/`wheel`/`tab` all read live through
`config_store.store` today, including the status-line hint text (e.g.
`src=mic (m)`), which resolves the *actual* bound key rather than a
hardcoded literal, so a remap doesn't leave a stale/wrong hotkey hint on
screen.

**Write path only serializes the shapes this app's schema needs** — three
known top-level tables, string-keyed leaves that are bool/int/float/str —
rather than pulling in a third-party TOML writer (`tomllib` is read-only;
stdlib has no `tomllib`-equivalent writer). `set_preference()` is the only
caller today, and nothing in this app invokes it yet (no settings-screen
UI exists until #43); it exists now so #43 has something to call against
without also needing to design the persistence layer at the same time.

## Unified shell entry point: `virtualnote`, lazy session state, sentinel returns (issue #40)

Map #37's grilling (#39) had already settled the shape (one new command,
in-process not subprocess-per-tool, `|`/`H` as the global keybinds) — #40's
actual work was making `main.py`'s existing per-invocation architecture
support running any tool, any number of times, in one process, without
disturbing anything the three terminal views/GUI already did.

**One `SessionState`, not `run_session()`'s originally-sketched loose
params.** The ticket's own sketch suggested a signature like
`run_session(view, ..., sensitivity, source_state, capture_holder)` — five-
plus loose parameters threaded through. Bundling `capture`/`result_queue`/
`stop_event`/`analysis_thread`/`sensitivity`/`source_state`/`color_scheme`
into one `main.SessionState` object instead keeps `run_session()`'s own
signature to `(view, scroll_mode, dump_file, fullscreen, debug, session)`
and gives the lazy-start/idempotent-restart logic (`ensure_started()`) a
natural home next to the state it manages, rather than as free-floating
module-level logic that would have to reach into five separate variables.
Nothing about this changes the *behavior* the ticket asked for, only how
the plumbing is packaged.

**Lazy creation, not eager-at-process-start.** `virtualnote`'s bare menu
must not open the mic just from being displayed — a real, user-visible
side effect (OS "listening" indicators, an OS mic-permission prompt on
macOS/Windows) that has no business firing before a tool is actually
picked. `SessionState.ensure_started()` is a no-op once already started,
so both call sites — `main()`'s single eager call (preserving today's
exact "capture opens immediately" behavior for anyone still running
`main.py` directly) and `shell.py`'s menu loop calling it fresh before
every tool entry — share one code path with no special-casing for which
caller it is.

**Sentinel return value, not an exception or a shared flag.** The ticket
considered (and rejected) two alternatives for signaling "the user pressed
`|`, go back to the menu": (1) a custom exception type unwound by the
caller, which would have meant every terminal `run_*` function's existing
`try/except KeyboardInterrupt/finally` shape needed a second parallel
exception-handling path; (2) a shared mutable flag object threaded through
like `Sensitivity`/`SourceState`, which adds shared state for something
that's really just "what did this call return," the textbook case for a
plain return value. `"quit"`/`"menu"` as plain strings (not an enum — this
app has no other enums, and two well-named string constants read fine at
every call site) slot into the exact same `finally` blocks that already
ran `keys.restore()`/`display.quit()`/`dump_ansi()` before this ticket,
with `return "menu"` fired from inside the existing `try` and Python's
normal "execute `finally` before actually returning" semantics doing the
rest — no restructuring of any run_* function's control flow beyond
adding the one new keycheck and swapping `pass`/implicit-`None` for an
explicit `return "quit"` in the `except KeyboardInterrupt` clause.

**GUI's back-to-menu key is `K_BACKSLASH`, wired directly in `run_gui`,
not shared with the terminal views' `_handle_back_to_menu_key`.** Pygame
reports the physical backslash key as `K_BACKSLASH` regardless of the
shift modifier — there's no separate keycode for the shifted `|` glyph the
way a raw terminal byte stream just hands over the literal character. Since
`_handle_back_to_menu_key(key)` compares against the literal string `"|"`,
it can't be reused as-is for a pygame `KEYDOWN` event; `run_gui` checks
`event.key == pygame.K_BACKSLASH` inline instead, same as it already does
for `K_ESCAPE`/`K_f`/`K_d`. `H` similarly gets its own inline
`pygame.K_h` check in `run_gui` rather than going through
`_handle_help_legend_key` (which expects a single-character string, not a
pygame keycode) — both are still the same conceptual toggle, just two
small platform-appropriate call sites instead of one shared function
that would need an awkward key-representation-normalizing shim in the
middle for no real benefit (each call site is one line).

**H legend line reserves a second trailing terminal row only when
populated.** Every terminal view's `render()`/`render_bands()`/
`render_chord()` grew an optional `legend=""` (`help_legend=""` on tab's
`TabDisplay.render()`, to avoid colliding with its existing, unrelated
`legend_on` staff-legend-column parameter) trailing-row parameter, sized
into each view's existing "reserve N rows for text, fill the rest with the
view's actual content" row-budget math. Passing `""` (H off) reproduces
the exact single-status-row layout every view always had; a non-empty
string reserves one more row and draws it right below the status line.
Kept as a plain string built by `main._legend_line()`, not a structured
type or its own rendering primitive — same "informational text line, not
a UI framework" scope call #40's ticket asked for; #42 owns the shell's
actual visual design and can restyle this without touching the row-budget
mechanics underneath it.

**Tab's on-quit `dump_ansi()` still fires on a `"menu"` return, not just
`"quit"`.** `run_terminal_tab`'s `finally` block was already
`keys.restore()` wrapping a nested `try: dump_ansi() finally: display.quit()`
before this ticket; `return "menu"` executes through that exact same chain
(Python runs `finally` blocks on every `return`, not only on an exception),
so leaving `tab` via `|` writes the session's note history same as leaving
it via Ctrl+C always did — there was no reason to special-case "the history
dump only matters if you're quitting the whole process," a session that
moves on to another tool via the menu still benefits from that file.

## Bass-register chord garbling fixed with a second, gated ring buffer (issue #63)

Issue #63's own root-cause hypothesis (FFT bin density too coarse at low
absolute frequencies) turned out to be a red herring: reproducing the bug
and inspecting the raw magnitude spectrum directly showed that zero-padding
the same 2048-sample window up to 32x denser bins converged on the exact
same wrong peak locations. The real cause is that a 2048-sample (~93ms)
Hann window's mainlobe half-width (~21.5Hz at 22050Hz) is physically wider
than the gap between an ordinary low triad's fundamentals (C2→E2 is only
~17Hz) — the two peaks' mainlobes overlap and merge into one
wrong-frequency peak no amount of interpolation can separate, since the
information needed to tell them apart was never captured by that short a
window in the first place.

Confirmed empirically that a longer window resolves this correctly: 2x
`WINDOW_SIZE` (4096 samples, ~186ms) was already enough to detect both
test chords from the issue's repro perfectly; 4x added no further
improvement. Given the choice between three tradeoffs (widen harmonic
pruning tolerance at low frequencies — cheap but leaves the merged
fundamental itself wrong; grow the live window unconditionally — fixes it
but adds ~93ms of latency to every hop, including ordinary mid/treble
playing that never needed it; or a dedicated low-band estimator — most
surgical but real new-module work), the user chose the middle path: keep
`WINDOW_SIZE` as the default and add a second, longer ring buffer
(`config.MULTIPITCH_LOW_WINDOW_SIZE = 4096`) maintained alongside it in
`analysis_loop()`/`batch_transcribe.transcribe()`, and swap
`multipitch.detect()` onto it only for hops that actually have low content
to resolve.

That gate (`multipitch.select_window()`) reuses the same confidence-ratio
check `chord_templates.match()` already applies to `chroma.fold_bass()`'s
output for slash-chord bass detection (bass-chroma peak ≥
`MULTIPITCH_BASS_GATE_RATIO` (0.25) × main-chroma peak) rather than
inventing a new signal — `fold_bass()` already isolates the <~250Hz band
this problem lives in, and its existing gate already distinguishes real
low content from spectral-leakage noise. `select_window()` lives in
`multipitch.py` (not `main.py`/`batch_transcribe.py`) and takes plain
chroma arrays as arguments rather than importing `chroma.py` itself, so it
stays a pure, cheaply-unit-testable function — same "pure logic
unit-tested, real I/O smoke-tested" convention as the rest of this
codebase — while both call sites keep computing `main_chroma`/
`bass_chroma` themselves (they already did, for the chord-mode pipeline).

## Monophonic YIN octave-doubling in the low register fixed with a sub-harmonic sanity check (issue #69)

Issue #69's acoustic round-trip test found the monophonic YIN tracker
(`pitch_detect.detect_pitch()`) frequently locking onto a note's own 2nd
or 4th harmonic instead of the true fundamental, specifically in octave 2
(~65-123Hz) — note-specific (A2/C2/F#2 badly wrong or silence-gated;
D#2/E2/F2/A#2/B2 rock solid), not a uniform "bass is hard" story.

**Root cause, confirmed empirically (not the window-size hypothesis the
issue's own repro guidance suggested).** `detect_pitch()`'s tau-selection
scans lags ascending from `tau_min` and locks onto the *first* one whose
CMND (cumulative mean normalized difference function) dips below
`YIN_THRESHOLD`. Reproduced with synthesized additive tones (harmonics
1-4, weighted like `chroma.HARMONIC_WEIGHTS`) fed straight to
`detect_pitch()`: when a note's fundamental is weak relative to its own
harmonics (empirically representative of real playing — bass rolloff in
small speakers/mics, or just a harmonic-rich low tone whose energy skews
upper-partial), a strong harmonic produces its *own* confident
sub-threshold CMND dip at an exact submultiple of the true fundamental's
period — and because that shorter lag is scanned first, it wins,
regardless of how much deeper the true (longer) fundamental's own dip
would have been. `config.WINDOW_SIZE` turned out fine (octave 2 still
gets 6-10 full periods per window) — the bug is purely in which
sub-threshold dip the scan accepts, not a resolution problem.

Confirmed via direct inspection of the CMND curve (e.g. a weak-fundamental
A2 tone): the true fundamental's dip (tau≈200 samples) was ~10x deeper
(more confident) than the harmonic-submultiple dip the scan locked onto
first (tau≈50, a 4th-harmonic-driven false lock reading as A4). This
directly matches the issue's own "search further for a deeper/more
confident local minimum" and "check smaller integer-multiple lags, prefer
the strongest" suggestions — but a naive version of either (just compare
raw CMND depth among `tau`, `2*tau`, `3*tau`, `4*tau`) turned out to
*regress* plenty of already-correct octave 3-5 detections: integer-sample
rounding of the true (non-integer-sample) period means a coincidental
multiple can land closer to an exact grid sample than the true tau does,
making its raw CMND value spuriously lower with no real periodicity
advantage — verified directly (e.g. C3/F5/G#5 test tones flipped to a
wrong lower octave under the naive version, including *pure sine* tones
with no harmonic ambiguity at all, which should never regress).

**The fix**, entirely inside `detect_pitch()` (`pitch_detect.py`):
1. After the ascending scan finds a sub-threshold candidate `tau`, check
   small integer multiples (`2*tau` through `4*tau`, matching
   `chroma.HARMONIC_WEIGHTS`' own harmonics-1-4 convention —
   `config.YIN_SUBHARMONIC_MAX_MULTIPLE`) for a *parabolically-refined*
   CMND value (not the raw grid value — the refined, sub-sample-accurate
   vertex value from the same 3-point parabola `detect_pitch()` already
   uses for frequency refinement) that both clears the threshold and beats
   the candidate's own refined value by a real margin
   (`config.YIN_SUBHARMONIC_MARGIN`, 0.5 — i.e. at least half as deep).
   Using the *refined* value rather than the raw grid sample is what
   defeats the integer-rounding false-positive above: it estimates the
   true continuous-domain minimum regardless of which exact grid point
   happened to be the nearest integer lag.
2. Skip the check entirely when the original candidate is *already* very
   confident (refined CMND below `config.YIN_SUBHARMONIC_SKIP_CMND`,
   0.01) — an already-correct detection is *also* trivially periodic at
   its own integer multiples (any period-T signal repeats at 2T, 3T, ...
   by definition), so even the refined-value comparison could otherwise
   occasionally flip a genuinely-correct, high-confidence detection. This
   gate is what keeps octave 3-5 (and plain single-sine tones, which have
   no harmonic content to be ambiguous about at all) untouched.

Verified: all 6 of the issue's reported failing octave-2 frequencies now
land within a semitone of the true fundamental across a range of
harmonic-weight profiles (flat, bass-rolloff-weighted, near-fundamental-
silent), a broad sweep across all 12 pitch classes × octaves 2-5 × several
harmonic profiles shows zero regressions, and the fix is robust to
additive noise up to a 0.2-amplitude floor against the reported
frequencies' harmonic tones. A handful of maximally-degenerate synthetic
weight combinations (e.g. two of four harmonics reduced to near-zero,
leaving almost all energy on a single upper harmonic) remain uncorrected
— a genuine physical ambiguity when the fundamental is essentially
inaudible, not a realistic harmonic profile, and out of this fix's scope.
`tests/test_pitch_detect.py::test_octave2_harmonic_rich_tone_not_octave_doubled`
is the regression test for the 6 originally-reported frequencies.

### Follow-up: the fix itself regressed real-mic accuracy, root-caused and recalibrated (issue #69, round 2)

A second real speaker→mic re-verification round found the fix above made
things *worse*, not better: octave-2 recall dropped from 11/12 to 8/12
notes ever detected (four notes, including three that were previously
100% accurate, went completely undetected), overall chromatic recall fell
from 97.9% to 91.7%, and several notes that were 100% accurate *before*
this fix — D#2, E2, G2 — became unstable (15-34% steady-state accuracy)
afterward, alongside a new regression on octave-3's C3. This wasn't "not
fully fixed" — it was the fix actively breaking previously-correct
detections, so the issue was reopened rather than left closed.

**First finding: the existing regression test doesn't actually exercise
the fix.** `test_octave2_harmonic_rich_tone_not_octave_doubled`'s profile
(`harmonics=(1.0, 0.5, 1/3, 0.25)`, fundamental dominant, matching
`chroma.HARMONIC_WEIGHTS`) turns out to already detect correctly with the
subharmonic check fully disabled (`subharmonic_max_multiple=0`) — the
scan's own ascending-threshold-then-walk-to-local-minimum behavior was
already enough for this profile, so the test was validating "detection
still works," not "the correction still fires." A genuinely adversarial
profile was needed to reproduce the *original* bug reliably at all:
`harmonics=(0.0, 0.1, 1.0, 0.2)` (fundamental fully silent, 3rd harmonic
dominant) reliably octave-doubles (or worse — lands on the 3rd harmonic,
~1902 cents off) across all 12 octave-2 pitch classes with the check
disabled, and is what
`test_octave2_silent_fundamental_dominant_3rd_harmonic_not_octave_doubled`
now formalizes.

**Root cause of the regression, found by reproducing it synthetically.**
Sweeping a dominant-fundamental octave-3 tone (the same
`chroma.HARMONIC_WEIGHTS`-shaped profile, fundamental strongest) plus
additive white noise (0.05 amplitude) and a 60Hz+120Hz "mains hum"
component (standing in for the mic self-noise/room rumble/electrical hum
a real recording — but not a clean synthesized test tone — actually
contains) reproduced the regression directly: a C3 tone's true tau (≈169
samples at `SAMPLE_RATE=22050`) was found correctly by the ascending scan,
but at only a middling confidence (refined CMND ≈0.115, close to
`YIN_THRESHOLD=0.12` — a real, noisy signal, not the near-zero CMND a
clean synthetic tone produces). The subharmonic check then examined 2×
that tau (≈337 samples, landing just inside `tau_max≈339` — the boundary
implied by `FMIN=65Hz` for `SAMPLE_RATE=22050`/`WINDOW_SIZE=2048`) and
found a *deeper* dip there (refined CMND ≈0.017) — not because of any
real periodicity at that lag, but because broadband low-frequency content
sitting near the fmin edge produces its own coincidentally-deep dip
there, independent of what note is actually playing. `YIN_SUBHARMONIC_
MARGIN=0.5` (a switch needs the candidate to be only ~2x deeper) accepted
that dip immediately, misreading a genuinely-correct C3 as C2 — an octave
error inflicted *by the correction itself*, not the original bug. This
mechanism is structural, not a one-off coincidence: it only affects
octaves whose true tau's 2x/3x/4x multiple still lands inside `tau_max`
(octave-2 notes' own tau already sits close to `tau_max`, so their own
multiples always exceed it and the subharmonic check is a no-op *for an
already-correct octave-2 candidate* regardless of margin — matching why
the real-world regression report's directly-broken notes clustered at the
octave-2/octave-3 boundary specifically). It's also, structurally, exactly
the classic YIN "subharmonic error" pitfall the original paper's
ascending-scan-plus-first-sub-threshold-dip heuristic exists to avoid in
the first place (CMND is known to trend toward spuriously low values at
larger lags, independent of real periodicity) — the original #69 fix
reintroduced a scoped version of that same pitfall by deliberately
searching larger lags for a "deeper" dip.

**The fix: recalibrate `YIN_SUBHARMONIC_MARGIN` from 0.5 to 0.1** (a
switch now needs the multiple's dip to be ~10x deeper, not ~2x), backed
by adversarial-testing data that separates the two failure modes cleanly:
sweeping fundamental weight from 0 up to the point genuine octave-doubling
stops occurring at all (across three single-extra-harmonic profiles —
2nd, 3rd, and 4th harmonic each tested alone) found genuine subharmonic-
lock ratios (best-multiple-CMND / candidate-CMND) never exceeding ~0.08;
stress-testing the mains-hum/noise regression mechanism above across all
12 octave-3 notes, hum amplitudes 0.2-1.2, both 50Hz and 60Hz mains
frequencies, and 8+ noise seeds each found the false-positive ratio never
dropped below ~0.14. A margin of 0.1 sits with real headroom inside that
gap on both sides, and — not a coincidence — lands almost exactly on this
fix's own original "~10x+ deeper in the reported failure cases" empirical
observation above, which the 0.5 value never actually reflected.
`YIN_SUBHARMONIC_SKIP_CMND` was checked and left unchanged (0.01): it
doesn't discriminate this regression at all, since the false-positive
candidate's own CMND (≈0.115 in the C3 case) sits nowhere near that
threshold — it's a genuinely correct but noise-degraded detection, not an
already-ultra-confident one the skip gate was ever meant to guard.

Re-validated after recalibration: the adversarial silent-fundamental
octave-2 sweep above still corrects all 12 pitch classes (unaffected —
margin tightening only makes the check *more* conservative, never
prevents a genuine ≥10x-deeper correction); a 20-seeds-per-note stability
sweep across all 12 octave-3 notes under the hum+noise regression
mechanism holds 239/240 trials within 50 cents (the one residual failure,
C#3 seed 1, reproduces identically with the subharmonic check fully
disabled — confirmed unrelated to this fix, an ordinary base-YIN noise
robustness limit, not something introduced or fixable here). Full
`pytest tests/` suite (302 tests, including both new adversarial
parametrized tests above plus every pre-existing #69 regression test)
green throughout.
`tests/test_pitch_detect.py::test_octave3_hum_and_noise_does_not_flip_already_correct_detection`
is the regression test for this round.

**Known residual risk, explicitly not closed out by this round.** All of
the above — both the original bug's reproduction and this round's
regression reproduction — is synthetic. This repo's `--source loopback`
acoustic pipeline test (`scripts/acoustic_pipeline_test.py`) was re-run as
a guard and stayed at 100% recall/100% steady-state accuracy on the
`chromatic` suite, same as before this round's changes — but loopback
audio has no physical mic frequency-response coloration or real room
noise at all, so (as already noted when that test infrastructure was
built) it cannot reproduce the real regression this round investigates,
and passing it is not evidence the real-mic regression is fixed. The
synthetic mains-hum/noise profile here is a plausible, reasoned proxy for
what a real mic's self-noise/room rumble/electrical hum could look like
to YIN's CMND curve — not a measurement of an actual mic. A real
speaker→mic re-verification, the same kind that caught this regression in
the first place, is the only way to confirm this recalibration holds up
in the field; issue #69 is being left open pending that, not closed on
synthetic evidence alone given this exact issue's own two-round history
of "looked fixed synthetically, broke for real."

## Harmonic-pruning evaluation order fixed, not tolerance (issues #67/#68)

Issue #67's own hypothesis — that a fixed `harmonic_tolerance_cents`
doesn't scale correctly with harmonic number, so real-world frequency
jitter on the fundamental defeats pruning at higher harmonics — was
checked directly and largely ruled out: cents are already a relative/
logarithmic unit, so a fixed cents tolerance applied to
`predicted = accepted_freq * n` is mathematically invariant to `n` for a
proportional (relative) frequency error, and a synthetic sweep detuning a
note's own 3rd harmonic by up to 30 cents (fundamental still the loudest
peak) never broke the existing 35-cent tolerance — well past any
plausible real jitter at this app's bin resolution (~5.4Hz at
`config.WINDOW_SIZE`/2x-zero-padded FFT).

Instrumenting `multipitch.detect()`'s raw candidate list directly (per
both issues' own guidance) found the actual bug instead: the pruning loop
walked candidates in magnitude-descending order, and `_is_harmonic_of()`
only prunes a candidate that's a harmonic *of* an already-accepted one —
it has no reverse check for "is this already-accepted candidate itself a
harmonic of a not-yet-accepted, lower note." Reproduced deterministically
(no jitter needed) by synthesizing a single E4 whose 3rd harmonic partial
carries more amplitude than its own fundamental (plausible under real
mic/speaker frequency response or room-reflection comb filtering, both of
which can null a fundamental's bin or boost an overtone's): the louder
harmonic got accepted first (nothing to compare against yet), and the
true fundamental, evaluated afterward, isn't a harmonic of anything
*higher* in frequency, so it got accepted too — reported as two notes
(E4 real + B5 phantom) instead of one. This exactly reproduces issue #67's
measured symptom, including the "single isolated note shows a phantom
2nd/3rd-harmonic note" observation that ruled out cross-note interaction.

Fix: walk candidates ascending by frequency instead of descending by
magnitude for the pruning pass (capping to `max_notes` by magnitude
afterward, once pruning is done, to preserve the existing "keep the
loudest surviving notes" behavior). This guarantees a real fundamental
always gets first claim on an accepted slot, so its own harmonics
reliably prune against it regardless of which partial the FFT happened to
weight louder that hop — verified against the loud-3rd-harmonic
repro above, and against a 30-cent-detuned-harmonic variant, with zero
regressions across the existing `test_multipitch.py`/`test_chord_
templates.py`/`test_chord_smoother.py` suites (including issue #56's
original over-calling fix).

**Issue #68's residual gap after the ordering fix.** Re-running the
ordering-fixed `detect()` against `scripts/acoustic_pipeline_test.py`'s
own `DENSITY_VOICINGS` synthetic fixtures (no live audio, just the same
note lists run through additive synthesis) showed several of those
specific voicings still drop a real note — always the same pattern: one
note's frequency sits within a couple of cents of another note's own
harmonic (e.g. A2 and E4 — a root and the fifth an octave above it, a 3:1
ratio; 12-TET's fifth+octave is only ~2 cents from the true 3rd harmonic).
This is a *different* bug from #67's: it's not order- or
magnitude-dependent, and it isn't fixed by the ordering change, because
it isn't really a bug in the pruning logic at all — it's an inherent
ambiguity. A single hop's magnitude spectrum cannot distinguish "this
peak is note X's own 3rd harmonic" from "this peak is a real,
independently-sounding note that happens to land within a couple of
cents of note X's 3rd harmonic": the two scenarios are spectrally
identical events (one peak at 3x another's frequency).

Two more targeted fixes were tried and rejected before settling on
"document as a known limitation":

- *Narrowing `harmonic_tolerance_cents`* (tried down to 5 cents): fixes
  none of the near-exact collisions (the coincidence itself is ~2 cents,
  well inside any tolerance still wide enough to catch real acoustic
  jitter) and introduces new phantom notes elsewhere (a legitimate
  overtone's own jitter starts escaping a too-narrow tolerance) — a wash,
  not a fix, exactly the "don't blindly loosen/tighten the threshold"
  risk both issues warned against.
- *A magnitude-consistency requirement* (only prune a harmonic candidate
  if its magnitude doesn't exceed what a decaying overtone series would
  predict from the accepted fundamental's own magnitude): directly
  reopens issue #67. #67's real acoustic failure mode *is* a genuine
  overtone measuring louder than its own fundamental (that's the whole
  bug) — a magnitude-consistency check would refuse to prune exactly the
  case #67 needs pruned, at exactly the rate it would fix #68's
  collision cases. Confirmed by re-running the loud-3rd-harmonic repro
  with this rule added: the phantom note returns.
- A candidate's own corroborating harmonic series (does 2x/3x of *this*
  peak also show up as a separate peak, as evidence it's a real,
  independent fundamental rather than just another note's overtone) was
  also considered and rejected: a single real note's own natural overtone
  series produces exactly the same self-corroborating pattern, so this
  discriminator can't tell "real independent note" from "the accepted
  note's own next overtone" either — confirmed by checking the accepted
  fundamental's own lower harmonics in the same failing test cases.

`chord_smoother._update_note_stack()`'s max_notes trimming — the issue's
second named hypothesis — was checked directly and is *not* buggy:
seeded with 6 constant, always-detected synthetic candidates it retains
all 6 indefinitely, and seeded with a worst-case transient (a full
6-note chord change, 12 candidates briefly competing for 6 slots) it
fully converges onto the new chord's correct 6 notes within a handful of
hops (see `tests/test_chord_smoother.py`'s
`test_real_pipeline_retains_all_six_notes_of_a_dense_non_colliding_chord`/
`test_note_stack_trimming_converges_to_new_chord_after_a_few_hops`). The
residual #68 gap lives entirely in `multipitch.detect()`'s pruning layer,
not the smoother.

Given no scoped fix resolves the collision case without reopening #67,
and resolving it properly would need information beyond a single hop's
magnitude spectrum (e.g. per-pitch-class onset/persistence tracked across
hops — a materially bigger change than tuning existing pruning logic),
this is recorded as a known, inherent limitation (see CLAUDE.md's Known
limitations) rather than force-fit with a change that trades one bug for
another. Chord voicings without such coincidental intervals are fully
fixed by the ordering change alone — confirmed with a dense 6-note chord
built with a >=60-cent safety margin from any small-integer frequency
ratio (`tests/test_multipitch.py`'s
`test_dense_six_note_chord_all_survive_when_not_harmonically_colliding`).

## Capping harmonic_number + lowest-note tiebreak (issues #67/#68, round 2)

The evaluation-order fix above (`3e49499`) helped clean up small phantom
counts but real-mic re-verification found it wasn't enough and was a mixed
bag: chord-name accuracy barely moved (14.3%→16.3%), phantom rate got
*worse* overall, and some previously-correct plain chords (a C major
triad, C/E slash, Csus4) started returning no match at all. Both issues
were reopened with that data. This round re-validated against
`scripts/acoustic_pipeline_test.py --source loopback` (a real
PortAudio/PipeWire round trip, no room acoustics/mic coloration — the only
live-pipeline validation available in this sandboxed environment; a real
physical speaker→mic re-check is still advisable before fully trusting
these numbers in the field) instead of live mic, since no physical
mic/speaker exists here. Baseline on that signal was already much
healthier than the real-mic numbers in the issue threads (87.8% chord-name
accuracy, 0 mean phantom pcs/hop) but still showed two distinct, unresolved
problems once broken down by root cause:

**Bug 1 — `_is_harmonic_of()` had no upper bound on harmonic number
(#68's real remaining recall gap).** The pruning loop computes
`harmonic_number = round(freq / accepted_freq)` and prunes the candidate
if the predicted `accepted_freq * harmonic_number` lands within
`harmonic_tolerance_cents` — for *any* integer `harmonic_number`, however
large. That's fine for the low harmonics a real instrument's overtone
series and this app's own `chroma.HARMONIC_WEIGHTS`/
`YIN_SUBHARMONIC_MAX_MULTIPLE` conventions already treat as "the harmonics
that matter" (1-4), but at high multiples it starts pruning real,
independent notes purely because they land near *some* large integer
multiple of an already-accepted note. The more integers there are to try
(8x, 9x, 12x...), the more likely an accidental near-miss becomes — and
that likelihood grows with chord density and pitch spread, exactly #68's
"recall collapses under density" symptom. Confirmed directly against
`acoustic_pipeline_test.py`'s own `DENSITY_VOICINGS` fixtures: a 6-note
voicing (C2 D#2 F#3 A3 D4 G5) lost G5 because it sits ~2 cents from C2's
*12th* harmonic (65.41×12=784.9 vs G5's 783.99Hz); a 5-note voicing (F2 A2
C3 E4 G5) lost G5 to C3's *6th* harmonic the same way, on top of losing E4
to A2's genuine 3rd-harmonic collision (the already-documented, still-open
residual below).

Fix: capped `harmonic_number` at `CHORD_HARMONIC_MAX_NUMBER = 4`
(`multipitch._is_harmonic_of`, wired through `detect()`, `main.py`, and
`batch_transcribe.py`) — the same "1-4 is what matters" line this codebase
already draws elsewhere. Verified safe against every existing
`test_multipitch.py` fixture (none synthesizes harmonics above the 4th —
matches `scripts/acoustic_pipeline_test.py`'s own synth, which also only
generates harmonics 1-4 per note); new regression tests
`test_high_order_harmonic_near_miss_does_not_prune_a_real_independent_note`
and `test_own_low_order_harmonics_still_pruned_after_capping_harmonic_number`
confirm the cap fixes the former without weakening the latter. Real-world
tradeoff: capping means a genuinely high-order overtone (a distortion
artifact from the real mic/speaker/room, or an instrument with unusually
rich high-partial content) is no longer pruned as "obviously" belonging to
its fundamental, so it can now register as a low-confidence phantom
instead of being silently absorbed — measured on the loopback density
suite as a small phantom-rate uptick (0 → 0.0-0.33/hop at 3-5 note
density) traded for a much larger recall gain (missing pcs/hop: 0.67-1.51
→ 0-0.73 across the same density range). A clear net win on this signal;
worth re-checking against real mic/room content since analog distortion
there is likely richer than loopback's.

**Bug 2 — symmetric-chord root tiebreak had no signal at all when no note
was in the true bass register (#67's remaining real misnaming).** Once
Bug 1's phantom/missing problem was otherwise fixed, the loopback chord
suite still misnamed 6/49 chords — all with phantom_rate and missing_rate
already at 0 (the *pitch-class set* detected was exactly right). Every one
was a rotationally-symmetric quality (augmented, dim7, half-dim7/min6)
voiced entirely above `chroma.DEFAULT_BASS_CUTOFF_HZ` (~250Hz) — e.g. an
F#-A#-D augmented triad, voiced upward from F#4, consistently named
"D+". `chord_templates._resolve_tie()`'s only real disambiguation signal
was `bass_chroma`, gated to require genuine sub-250Hz content (the right
gate for genuine slash-chord naming, so as not to misread ordinary
mid-register chords as having a bass note) — but that gate meant *any*
symmetric chord voiced in the mid/treble register got zero disambiguating
signal at all, falling to the old fallback: `min(candidates, key=root)`,
a fixed but musically arbitrary "always answer with whichever root index
sorts lowest."

Fix: added `lowest_pc` — the pitch class of whichever detected note is
lowest in frequency *this hop*, unconditionally (no bass-register
requirement) — as a last-resort tiebreak in `_resolve_tie()`, tried only
after the genuine `bass_chroma` signal fails to disambiguate.
`chord_smoother._update_chord_name()` computes it as
`min(note_candidates, key=freq).pitch_class` from the same already
harmonic-pruned `multipitch.detect()` output chord-name matching already
uses (issue #56), so it costs nothing extra to compute. Rationale: absent
any other signal, "the lowest note actually played is probably the root"
is a far better default than an arbitrary index — root-position,
lowest-note-in-the-bass voicing is both this app's own acoustic-test
convention and ordinary real playing's common case. It only ever fires
when multiple templates are tied on cosine similarity (i.e. genuinely
pitch-class-set-identical, like aug/dim7/min7-vs-maj6), so it can't
override a real, better-scoring match. New tests:
`test_symmetric_augmented_triad_resolved_by_lowest_detected_note`,
`test_symmetric_dim7_resolved_by_lowest_detected_note_without_bass_chroma`,
`test_lowest_pc_tiebreak_yields_to_a_confident_bass_chroma` (confirms
`bass_chroma` still wins when both signals disagree), and
`chord_smoother`'s own
`test_symmetric_chord_name_uses_lowest_note_candidate_as_tiebreak` for the
wiring itself.

**Combined result on the loopback suite** (`--source loopback`, real
PortAudio/PipeWire round trip, this session's only available live-pipeline
validation — see caveat above): chord-name accuracy 87.8%→**100%**
(49/49); density-suite missing pcs/hop dropped to ~0 through 5-note chords
(previously 0.67-1.44) and to 0.73 at 6 notes (previously 1.51), at the
cost of a small phantom-rate uptick (0→0.0-0.33/hop) explained above.
Chromatic (100%/100%) and tempo (90/140/200bpm 100%, 280bpm 88%) suites
unaffected — re-run after both fixes landed, identical to their
pre-existing baseline. Full test suite green (278 passed, from 272).

**Residual limitation, unchanged by this round.** The near-exact (~2
cent) small-integer-ratio collision documented above (e.g. A2's 3rd
harmonic landing almost exactly on E4) is still not resolved, and isn't
expected to be — `harmonic_number` capping only stops checking multiples
*above* 4; it changes nothing for a collision at harmonic_number 2, 3, or
4, which is exactly where the genuinely inherent ambiguity lives (a fixed,
small integer ratio a real instrument's own overtone series would
produce, at exactly the harmonic numbers this codebase already treats as
"real"). The F2-A2-C3-E4-G5 voicing referenced above is a good example of
the two bugs' actual boundary: E4 collides with A2 at harmonic_number=3
(still pruned, still the documented inherent limitation) while G5 collided
with C3 at harmonic_number=6 (now fixed by the cap, since 6 is above the
new limit and gets a real chance to survive as its own note). The
genuinely inherent, harmonic_number≤4 collisions remain exactly as
described above: unresolvable from a single hop's magnitude spectrum
alone, still the documented known limitation.

## Mono duration-class snapping undercounted short notes: three compounding causes fixed (issue #70)

`scripts/acoustic_pipeline_test.py`'s new `rhythm` suite (its first-ever
live round trip against issue #55's rhythm pipeline — previously verified
only via synthetic array-slicing unit tests and one batch `transcribe`
run) found `duration_class_for_beats()` snapping short notes to the wrong
standard note value: a played quarter note measured as `dotted-sixteenth`
(58% short), an eighth as `sixteenth` (40% short), and so on, while notes
>=1.5 beats classified correctly with only a small, fairly constant
absolute deficit (~50-90ms). Reproduced cheaply offline first — no audio
hardware needed — by driving the real mono pipeline (`compute_spectrum`
-> `detect_pitch` -> `NoteSmoother` -> `DurationTracker`) hop by hop over
synthesized notes (harmonics 1-4, 20ms linear fade in/out, matching
`scripts/acoustic_pipeline_test.py`'s own `synth_notes()`), which
surfaced two independent, compounding bugs; a third was only visible on
real recorded audio and found by re-running the actual acoustic suite.

**Bug 1: a spurious "ghost" duration-1 note after every note that decays
into silence.** `NoteSmoother` deliberately keeps echoing a just-ended
note's `(pitch_class, octave)` with `is_onset=False` for
`SILENCE_HOPS - 1` more hops after its magnitude crosses the silence
floor — a grace period against display flicker, unrelated to duration
tracking. But `DurationTracker.update()`'s `state is None` branch opened
a brand-new tracked state for *any* reappearance of a key with no
existing state, regardless of `is_onset` — so the very next hop after a
real note's decay-ratio finalize (which deletes its state), the smoother's
echo re-opened a new state with near-zero magnitude, which then finalized
again as a spurious ~1-hop "ghost" note two hops later once the echo
itself went silent. Every mono note that ended by decaying into silence
(as opposed to being cut off by a new note's attack) produced this ghost
immediately after its real finalize event.

Fix: `DurationTracker.__init__` gained `require_onset_for_new_note`
(default `False`, preserving chord mode's existing behavior — chord notes
have no reliable per-note onset signal at all, see below). Mono's tracker
sets it `True` (`main.py`): a key with no existing state only opens one
when `is_onset` is actually `True` for it. Mono's `NoteSmoother` output
guarantees this is always true for a genuine new note (`note_changed or
was_silent` is exactly `is_onset`'s trigger for a fresh attack), so the
echo hops — always `is_onset=False` — are silently ignored instead of
opening a ghost state. Chord mode's `chord_duration_tracker` keeps the
default off, since `main.py` intentionally hardcodes chord notes'
`is_onset=False` (no persistent per-note identity across
`multipitch.detect()`'s independent per-hop peak-picking — see the
existing "Chord-mode duration tracking always passes `is_onset=False`"
design decision above) and relies entirely on appear/absence for its
lifecycle.

**Bug 2: `NoteSmoother`'s debounce lock-in delay baked directly into
`onset_hop`.** A note-change promotion (`note_changed`) only fires once
`candidate_count` reaches `config.DEBOUNCE_HOPS` (3) *consecutive* hops
agreeing on the same candidate — meaning the true attack precedes the
promotion by exactly `DEBOUNCE_HOPS - 1` hops (~46ms at this app's hop
size), every single time, since a promotion by definition only ever lands
on the hop count first reaches that threshold. `DurationTracker` stamped
`onset_hop` at the hop `is_onset` actually fired, silently absorbing that
fixed, entirely predictable delay into every measured duration — invisible
for long notes, but a large fraction of a short one's total length.

Fix: `NoteSmoother` now exposes `onset_backdate_hops` (0 normally,
`DEBOUNCE_HOPS - 1` exactly on the hop a genuine note-change promotion
fires — computed directly from `note_changed`, so it's zero for an
RMS-jump/spectral-flux re-attack of an *already-current* note, which never
had to rebuild `candidate_count` and so has no such delay to correct for).
`DurationTracker.update()` gained an `onset_backdate` parameter (default
0, chord mode unaffected) that subtracts straight off `hop_index` when
stamping a new state's `onset_hop`; `main.py` passes
`smoother.onset_backdate_hops` for the mono tracker. Deliberately scoped
to the after-silence case only (a fresh `NoteSmoother`'s `history` deque
is empty, so the raw pitch estimate itself is already correct as soon as
SNR allows — the only remaining delay is `DEBOUNCE_HOPS`'s own buildup).
A same-key legato transition straight from one already-sounding note to
another, with no intervening silence, pays additional `MEDIAN_WINDOW`-
driven delay on top (the history deque isn't cleared, so stale old-note
samples still influence the median for a few more hops) — out of scope
here; `onset_backdate_hops` only ever claims to correct the debounce
portion, and is verified `0` in that scenario via
`test_onset_backdate_hops_set_on_genuine_note_change`'s own docstring.

With bugs 1+2 fixed, an offline synthetic sweep of all ten standard
duration classes (whole down to thirtysecond, silence-separated, 100bpm)
classified 10/10 correctly with exactly one finalize event per note — up
from a mix of double-counted ghosts and systematically undercounted
durations before.

**Bug 3, found only by re-running the real acoustic suite after 1+2:**
even with `duration_hops` itself now accurate, the reported *duration
class* for short notes was still frequently wrong — because
`duration_class_for_beats()`'s beats conversion (both in `main.py`'s
`tab` view and the acoustic test's own analysis) divides by whatever
`TempoTracker.update()`'s live `bpm_estimate` happens to be *at the exact
hop a note finalizes*, and that estimate was swinging wildly during the
rhythm suite's short-note phase: 99.38 -> 41.02 -> 47.85 -> 76.00 ->
48.75 bpm across consecutive re-estimates, confirmed reproducible offline
by feeding the suite's exact synthesized audio through the real
`chroma.fold()`/`chroma_flux()`/`TempoTracker` pipeline directly (no
hardware needed once the mechanism was suspected). Root cause:
`TempoTracker._estimate()`'s autocorrelation window
(`config.TEMPO_HISTORY_SECONDS`, 8s) still held the tail of the suite's
earlier isochronous 100bpm pulse train when the duration-class notes
began, giving a strong, confident periodicity match — but as that
periodic content scrolled out of the rolling window and was replaced by
genuinely non-periodic content (isolated single notes at irregular,
shrinking intervals, no consistent beat for autocorrelation to find at
all), the best-lag `argmax` degenerated into picking whichever lag was
marginally tallest among what was essentially noise, with no stable
answer to converge on.

Measured directly: the autocorrelation peak normalized against zero-lag
energy (`peak / acf[0]`) sat at ~0.85-0.90 throughout the genuinely
periodic pulse-train phase, and collapsed to ~0.09-0.19 once the window
was dominated by non-periodic content — a clean, wide margin. Fix:
`TempoTracker._estimate()` now computes this same ratio and, below
`config.TEMPO_MIN_CONFIDENCE` (0.3, comfortably inside that empirical
margin), returns `self._last_estimate` unchanged instead of re-locking
onto the best-of-a-bad-lot candidate — holding the last confident
estimate through a stretch of non-periodic input rather than chasing
noise. Re-running the exact same offline reproduction after this fix
holds a steady 99.4bpm through the entire duration-note phase instead of
wobbling.

**Combined result**, measured via
`scripts/acoustic_pipeline_test.py --suites rhythm --source loopback`
(a real speaker-absent loopback round trip through the actual PortAudio/
PipeWire stack, not a physical mic — see the caveat below):

| expected class | expected beats | before (detected / correct) | after (detected / correct) |
|---|---|---|---|
| whole | 4.0 | whole / True | whole / True |
| dotted-half | 3.0 | dotted-half / True | dotted-half / True |
| half | 2.0 | half / True | half / True |
| dotted-quarter | 1.5 | dotted-quarter / True | dotted-quarter / True |
| quarter | 1.0 | dotted-sixteenth / **False** | quarter / True |
| dotted-eighth | 0.75 | eighth / **False** | eighth / **False** |
| eighth | 0.5 | sixteenth / **False** | eighth / True |
| dotted-sixteenth | 0.375 | sixteenth / **False** | sixteenth / **False** |
| sixteenth | 0.25 | thirtysecond / **False** | thirtysecond / **False** |
| thirtysecond | 0.125 | thirtysecond / True (coincidence) | thirtysecond / True |

Duration-class accuracy rose from 50% (5/10) to 70% (7/10) on this run,
tempo-convergence accuracy was unaffected (99.4bpm measured, 0.6% error,
identical to before), and every note >=1 beat is now exactly correct.
Traced the three remaining short-note misses (dotted-eighth,
dotted-sixteenth, sixteenth) to a *fourth*, distinct and narrower
mechanism: on real recorded audio (not present in the idealized offline
reproduction above, which used exactly block-aligned synthetic slicing),
these notes' own 20ms linear attack fade occasionally straddles a hop
boundary awkwardly enough that the block-to-block RMS ratio during the
ramp-up itself clears `ONSET_RMS_JUMP_DB`, firing a spurious same-key
re-onset within 1-2 hops of the note's own genuine attack and splitting
one note into two duration-tracker events — the later of which (the one
`scripts/acoustic_pipeline_test.py`'s own nearest-to-segment-end picking
logic selects) is missing its own true beginning. This is real-audio-
timing-jitter-sensitive rather than a clean deterministic bug the way the
three fixed mechanisms were (the exact same idealized synthetic sweep
that found bugs 1/2 does not reproduce it), and tightening the RMS-jump/
spectral-flux onset heuristics to suppress it risks the opposite failure
mode (missing a genuine fast repeated note, issue #55 story 3's explicit
scope) without further empirical tuning against real playing — already
flagged as provisional in this file's "Rhythm mode's thresholds/constants"
known-limitation entry above. Documented here rather than chased further
within this fix's budget; a good candidate for a future issue if it
proves to matter against real (non-synthetic) playing specifically, not
just this acoustic test's own construction.

Verified via `--source loopback` (a real audio-stack round trip: real
PortAudio buffering/timing, real device resampling, real OS-level
capture — but no physical air gap, so no room acoustics/reflections/mic
frequency response), not a physical speaker->mic re-verification — this
codebase has a documented history (issue #69's round 2, above) of a
loopback/synthetic pass not surviving real-mic testing, so this result
should be read with that same caveat until a real-mic round confirms it.
Regression-tested: `tests/test_duration_tracker.py` (the ghost-suppression
and `onset_backdate` mechanisms), `tests/test_note_smoother.py`
(`onset_backdate_hops`' note-change-only trigger condition), and
`tests/test_tempo_tracker.py` (the confidence gate, both that it holds
under non-periodic input and that it still tracks a genuine periodicity
change) — full suite green throughout
(`.venv/bin/python -m pytest tests/`), and a `chromatic`/`sustain`
acoustic-suite spot check confirmed no regression to steady-state pitch
accuracy or the existing `is_onset` misfire-rate baseline.
