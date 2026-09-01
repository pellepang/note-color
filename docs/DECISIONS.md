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

## YIN's unprincipled "loose global fallback" removed: it was accepting noise-driven false locks at high confidence (issue #71)

`scripts/acoustic_pipeline_test.py`'s `noise` suite (new this session: a
reduced chromatic+chord sweep with additive broadband Gaussian noise at a
few levels, layered on top of the otherwise effectively-silent-room
conditions every other suite runs under) found `pitch_detect.detect_pitch()`
doing something categorically worse than "losing confidence under noise":
at `noise` suite's `moderate` level (0.15 relative amplitude), it
**confidently** (0.6-0.9 confidence — comfortably above
`config.CONFIDENCE_THRESHOLD=0.5`) locked onto a pitch near `FMIN` (~octave
2) for *any* note being played, regardless of octave. This isn't #69's
octave-doubling (a shorter-lag harmonic winning) — for higher-register
notes the false pitch has no harmonic/subharmonic relationship to the true
note at all.

**Root cause, confirmed via synthetic reproduction (no audio hardware
needed) and re-confirmed against real `--source loopback` acoustic-test
raw hop logs.** `detect_pitch()`'s tau-selection has two branches: (1) an
ascending scan from `tau_min` accepting the first `tau` whose CMND dips
below `threshold` (config's `YIN_THRESHOLD=0.12`), correctly walking
forward to that dip's local minimum first — this branch is fine, matches
classic YIN, and was untouched by this fix; (2) a fallback, present since
this repo's very first commit, that fired whenever branch (1) found
*nothing* — it took the single global argmin of CMND across the *entire*
`[tau_min, tau_max)` search range and accepted it as a detection as long as
it beat a barely-there cutoff (`< 0.99`), reporting confidence as
`1 - cmnd[tau]` with no regard for *why* that argmin was low.

That fallback is fundamentally unprincipled: branch (1)'s scan already
checks `cmnd[t] < threshold` for *every* `t` in range individually, so if
it found nothing, there is by construction no `tau` anywhere that clears
the real detection threshold — branch (2)'s separate, ~8x-looser cutoff
(0.99 vs. 0.12) has no principled basis to exist at all. Direct inspection
of a synthesized A4 tone (harmonics 1-4 weighted like
`chroma.HARMONIC_WEIGHTS`, the exact profile `scripts/acoustic_pipeline_
test.py`'s `synth_notes()` uses) plus 0.05-0.15 amplitude white noise
showed why this matters in practice: the true fundamental's own CMND dip
(tau≈50 samples) gets shallower under noise and stops clearing 0.12, while
one of that *same period's own integer multiples* — confirmed empirically
at 2x (F#3), 5x (A4), 7x (D#5), all landing well inside `tau_max` for a
short-period upper-register note — occasionally looks deeper. This isn't
coincidence: a periodic signal's difference function has real dips not
just at its true period but at every integer multiple of it, and CMND's
own cumulative-mean normalization is *systematically* biased lower at
large tau even for **pure noise with no tone at all** — 30 trials of pure
white noise (no signal) showed every single trial's global-minimum CMND
landing in the top third of the search range (near `tau_max`, i.e. near
`FMIN`), averaging ~0.87 there vs. ~0.95-1.0 near `tau_min` — because the
difference function's window (`w - tau` samples) shrinks as tau grows, so
fewer samples back each large-tau CMND estimate, adding variance and (via
the cumulative-mean denominator lagging behind a downward-trending
numerator) a real downward bias. The two effects compound: a real note's
own large-multiple-of-tau dip, already assisted by that same near-tau_max
bias, can end up looking confidently periodic even while the true,
short-tau period is too noise-degraded to clear threshold at all.

**The fix**: delete the fallback branch entirely. When the ascending scan
finds no `tau` clearing `threshold`, `detect_pitch()` now returns
`(None, 0.0)` — the same "unvoiced frame" behavior classic YIN specifies,
and the only behavior actually justified once branch (1) has already ruled
out every candidate under the real threshold. No new constant, no
`config.py` change — the loose `0.99` cutoff this replaces was never a
named, principled constant to begin with.

**A threshold recalibration (raising `YIN_THRESHOLD` itself) was
investigated and rejected, not blindly skipped.** A sweep from 0.12 to 0.30
against the exact reproducing synthetic signals found: raising it recovers
some `noise_amp=0.05` cases via the *principled* ascending scan (since the
true, short tau is always found before the scan ever reaches a near-
`tau_max` artifact — a real, safe mechanism) — but this repo's own `light`
noise level (0.05) was *already* 100% recall/accuracy before this fix (the
real captured audio's downsampling from `PLAYBACK_SR=44100` to
`SAMPLE_RATE=22050` attenuates injected wideband noise more than the
tonal harmonics, an effective SNR gain the isolated synthetic sweep above
doesn't get), so there was no headroom to improve there. At `moderate`
(0.15), *no* threshold up to 0.30 recovered a single correct detection in
the sweep — the true fundamental's CMND is genuinely too degraded within
one ~93ms window at that SNR, a real statistical limit, not a threshold-
calibration problem. Worse, raising `YIN_THRESHOLD` also loosens issue
#69's subharmonic-check gate (`cmnd[cand_tau] < threshold` reuses the same
parameter), and reintroduced occasional wrong-confident detections at C2
(octave 2) even at `noise_amp=0.05` — i.e. it would have reopened #69's
exact failure mode at low octaves for zero measured benefit at the levels
this app's own noise suite actually tests. `YIN_THRESHOLD` was left at
0.12.

**Before/after, from real `--source loopback` acoustic hop logs** (not
just the synthetic repro — `acoustic_test_results/round2/noise_raw.json`
vs. `acoustic_test_results/yin_fallback_fix/noise_raw.json`, same
timeline/expected-note ground truth, only the code differs), classified
per steady-state hop as correct / **wrong-but-confident (>=0.5)** / wrong-
low-confidence / no-detection:

| level | correct (pre→post) | **wrong-confident (pre→post)** | no-detection (pre→post) |
|---|---|---|---|
| clean | 100%→100% | 0%→0% | 0%→0% |
| light | 100%→100% | 0%→0% | 0%→0% |
| moderate | 27.2%→0.0% | **72.8%→0.0%** | 0%→100% |

The crude "note accuracy" number at `moderate` reads the same "0-ish"
grade before conclusion either way, but that's misleading in isolation:
pre-fix, both the 27.2% "correct" and the 72.8% "wrong-confident" hops
came from the *same* unprincipled fallback mechanism landing, by chance,
on the true tau or one of its multiples respectively — neither was earned
by real periodicity evidence, so the 27.2% wasn't a real capability being
traded away. What actually matters — a live color visualizer never
silently displaying a *confidently wrong* note — went from 72.8% of
moderate-noise hops to 0%. `clean` and `light` (the app's own defined
noise levels) are both unaffected, and the `chromatic` suite (clean-signal
regression guard) stayed 100%/100%, matching every prior baseline this
session. Full `pytest tests/`: 320 passed (up from 310; 10 new adversarial
tests in `tests/test_pitch_detect.py`
(`test_broadband_noise_never_confidently_wrong`,
`test_no_subthreshold_tau_anywhere_returns_none_not_loose_fallback`),
including an explicit re-run of every #69 regression test, all still
green — this fix doesn't touch the `tau is not None` branch #69's
subharmonic check lives in at all.

**Known residual limitation, left honest rather than force-fixed.** At
sustained broadband noise around a 0.15-relative-amplitude SNR, a single
~93ms analysis window's periodicity evidence for the true note is
genuinely too degraded for *any* principled per-hop threshold to recover —
confirmed by the threshold sweep above finding zero recoverable margin up
to 0.30. `note_smoother.NoteSmoother`'s existing silence-gating is exactly
the mechanism this now correctly falls through to (same category as this
project's other documented "sometimes silence-gated under real acoustic
conditions" limitations, e.g. issue #69's own writeup) — recovering actual
detection at this SNR would need information beyond a single hop's
magnitude spectrum (e.g. cross-hop periodicity accumulation), out of this
fix's scope. This was validated via synthetic adversarial testing plus a
`--source loopback` round-trip through the real unmodified pipeline, not a
real physical speaker→mic session — this repo has a documented history
(issue #69, twice) of synthetic/loopback fixes not surviving real-mic
verification, so a real-mic re-check is still advisable before treating
this as fully closed in the field.

**Follow-up, found by the orchestrating session immediately after
landing the fix above (not by the fix's own agent, whose regression guard
only re-checked the `chromatic` suite): removing the argmin fallback also
cost real recall at the `tempo` suite's fastest tested speed.** 90/140/
200bpm all stayed 100% (unaffected — plenty of periods per analysis
window at those speeds), but 280bpm (eighth notes, 107ms/note, already
this suite's explicit "how fast can it go" stress case, not a normal-use
guarantee) dropped from a stable 88% (measured twice, both this session's
#67/#68 work and independently before the #71 fix) to a stable 71%
(likewise measured twice, immediately after #71 landed, `--source
loopback --suites tempo`, no other change in between). This is the same
trade-off as the noise case above, at a different stressor: a fast
legato transition's analysis window is briefly contaminated by the
previous/next note bleeding in at the window edges, and the old fallback
would sometimes guess through that ambiguity by luck (right or wrong,
unverifiable from the recall number alone, same as the noise case);
removing it means some of those borderline hops now correctly report no
detection instead of a lucky guess. Left as-is, not chased further: only
the most extreme tested tempo is affected, 90-200bpm are untouched, and
re-introducing any form of "guess when in doubt" would directly undo
issue #71's whole point. Documented here and in CLAUDE.md's Known
limitations rather than silently left for the next person to rediscover.

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

## `multipitch.detect()` had no frequency-range bound: percussion noise peak-picked as phantom notes at nonsensical octaves (issue #74)

### Root cause

A new acoustic-test suite (`percussion`, `scripts/acoustic_pipeline_test.py`'s
`build_percussion()`/`analyze_percussion()`) plays synthesized kick/snare/
hi-hat percussion — broadband/inharmonic, no stable pitch by construction
— through the real live pipeline via `--source loopback`. With **zero
pitched content playing at all**, the polyphonic chord-mode pipeline
(`multipitch.detect()` + `chord_smoother.py` + `chord_templates.match()`)
frequently produced a non-empty `note_stack`, and sometimes even a
confidently-named chord.

`multipitch.detect()` converts *every* spectral peak surviving
`min_mag_ratio` straight to a MIDI pitch class/octave via
`midi = 69 + 12*np.log2(freq/440.0)`, with **no frequency-range bound at
all** — unlike the monophonic path, `pitch_detect.detect_pitch()` (YIN) is
explicitly bounded by `config.FMIN`/`FMAX` (65-1000Hz, ~C2-B5, this app's
whole targeted instrument range; see `tau_min`/`tau_max`'s derivation from
`fmax`/`fmin` in `pitch_detect.py`). A hi-hat's high-passed noise (6-11kHz
in this test's synthesis) is ~3-4 octaves above `FMAX`, but multipitch
happily reported it as notes at octave 8-9 — nonsensical for any real
instrument this app targets. Confirmed directly (no audio hardware
needed) by feeding `multipitch.detect()` a synthetic spectrum with energy
only above 4kHz (band-passed white noise, `np.fft.rfft`/`irfft` with
everything below 4kHz zeroed): it reported 6 phantom "notes", all at
octave 8-9, e.g. `NoteCandidate(pitch_class=10, octave=8, freq=7360.1,
confidence=1.0)`. Root cause is a plain missing bound, not a subtler
peak-picking or pruning bug — no percussion/pitch-plausibility classifier
exists anywhere in this pipeline at all (per CLAUDE.md's architecture,
chroma/multipitch always run every hop regardless of what's actually
playing).

Reproduced on the real acoustic suite twice (independent runs, same
machine/session) before the fix, numbers consistent both times:

| tier (no pitched content at all) | hops | false-chord rate | false note-stack rate |
|---|---|---|---|
| isolated_hits (run 1 / run 2) | 194 / 193 | 0.0206 / 0.0 | 0.2371 / 0.2435 |
| beat_only, sustained 4-bar beat (run 1 / run 2) | 465 / 465 | 0.1376 / 0.1312 | 0.6774 / 0.6688 |

So a sustained drum-only beat with **no pitched content whatsoever**
showed a non-empty (phantom) note_stack on ~67-68% of hops, and a
fully-formed, "confident" chord name (e.g. `B13b9`, `AΔ9`, `Ab°Δ7/D`) on
~13-14% of hops. Per-drum-type breakdown on isolated single hits: kick
and snare produced phantom note_stacks with plausible-*register* pitch
classes (kick: e.g. `(7,2)`/`(10,1)`, near its own low thump; snare:
mixed low+high). Hi-hat's phantom notes were exclusively at octave 8-9
— purely the missing upper-bound artifact — and hi-hat was also the only
drum type among isolated hits that produced a "confident" chord name.

### Fix

`multipitch.detect()` gained `min_freq_hz`/`max_freq_hz` parameters
(defaulting to new module constants `DEFAULT_MIN_FREQ_HZ`/
`DEFAULT_MAX_FREQ_HZ`, 65.0/1000.0 — numerically identical to
`config.FMIN`/`FMAX`, same "own DEFAULT_* mirrors config's real value,
callers pass the config value explicitly" convention this module's
`DEFAULT_MAX_NOTES`/`DEFAULT_MIN_MAG_RATIO`/etc. already use for
`config.CHORD_MAX_NOTES`/`CHORD_PEAK_MIN_MAG_RATIO`/etc.). Filtering
happens in the raw-candidate-building loop, immediately after each peak's
quadratic-interpolated frequency is computed and before either the
`max_peak_candidates` cap or the ascending-frequency harmonic-pruning
walk (issue #67) ever see it:

```python
freq = (i + offset) * sample_rate / fft_size
if freq < min_freq_hz or freq > max_freq_hz:
    continue
raw_candidates.append((freq, magnitude[i]))
```

`main.py`'s `analysis_loop()` and `batch_transcribe.py`'s `transcribe()`
(the only two real call sites) now pass `min_freq_hz=config.FMIN,
max_freq_hz=config.FMAX` explicitly, exactly the same pattern they
already use for `config.CHORD_MAX_NOTES`/`CHORD_PEAK_MIN_MAG_RATIO`/etc.

**Why reuse `config.FMIN`/`FMAX` rather than a separate polyphonic-only
range?** Considered and rejected: multipitch is still detecting notes
from the same real instruments/register this whole app targets, just
more than one at a time — there is no principled reason a chord's
individual notes would plausibly sit outside the range a single melody
note already can't. A polyphonic voicing's upper extensions (9ths,
11ths, 13ths) could in principle push a chord tone's *own* fundamental
higher than a plain melody line would — checked directly against this
app's own acoustic test fixtures (`scripts/acoustic_pipeline_test.py`'s
`voice_chord()`, every quality including `add9`/`sus2`/`sus4`, registers
3-4) and every tested chord tone's fundamental already sits well under
1000Hz; the existing unit-test suite's own dense/high voicings
(`test_dense_six_note_chord_all_survive_when_not_harmonically_colliding()`'s
6-note stack up to C#4, `test_high_order_harmonic_near_miss_does_not_
prune_a_real_independent_note()`'s B5) also all clear comfortably. A
harmonic *overtone* of an in-range fundamental (a real note's 3rd/4th
partial) routinely does land above 1000Hz — e.g. a sus4 voicing's F4/G4
partials land at 1047Hz/1176Hz — but those were already being correctly
discarded via harmonic-consistency pruning before this fix (confirmed by
direct A/B: an isolated synthetic Csus4 chord — C4+F4+G4, harmonics 1-4
weighted like `chroma.HARMONIC_WEIGHTS` — produces the identical 3-note
`NoteCandidate` list whether `min_freq_hz`/`max_freq_hz` are 65/1000 or
0/1e9), so pre-filtering them earlier is a no-op for correctness, just a
few fewer candidates walked through pruning.

### Verification, including a same-day false alarm

Unit tests added to `tests/test_multipitch.py`:
`test_out_of_range_high_frequency_noise_produces_no_phantom_notes()`
(band-passed white noise above 4kHz -> `[]`, the direct repro above),
`test_out_of_range_low_frequency_rumble_produces_no_phantom_notes()`
(a companion low-end case — a kick drum's sub-bass thump can sit below
`FMIN` entirely, not just above `FMAX`), and
`test_frequency_range_bound_does_not_affect_in_range_chords()` (a chord
spanning close to both edges of the default range, C2+B5, still detects
correctly). Every pre-existing `multipitch`/chord test — including the
issue #67/#68 harmonic-pruning and `CHORD_HARMONIC_MAX_NUMBER`-cap tests,
the ones most likely to be accidentally re-broken by touching this
function — passed unchanged. Full suite green throughout.

Re-running the percussion suite after the fix (`--source loopback`):

| tier | false-chord rate (before -> after) | false note-stack rate (before -> after) |
|---|---|---|
| isolated_hits | 0.0-0.0206 -> 0.0 | 0.2371-0.2435 -> 0.0825-0.0928 |
| beat_only (sustained beat) | 0.1312-0.1376 -> 0.0-0.0151 | 0.6688-0.6774 -> 0.2968-0.3032 |

The false-chord rate on sustained drums-only audio dropped to essentially
zero, as expected — that number is driven almost entirely by hi-hat's
octave-8-9 phantom notes clearing `CHORD_MATCH_THRESHOLD`, exactly what
the frequency bound removes. The false note-stack rate dropped
substantially (roughly half) but stayed nonzero: kick/snare's own
broadband energy still has real content *inside* [65, 1000]Hz (a kick's
low thump, a snare's mixed low+high spectrum), which peak-picks into a
plausible-looking but still spurious note — a real, separate gap (no
percussion/pitch classifier exists anywhere in this pipeline) that this
fix doesn't claim to close; see CLAUDE.md's Known limitations.

Tiers 2-4 (real chords/melody mixed with drums) and the `chords`/
`density` suites confirmed no regression to legitimate detection —
`chords` stayed at 100% name accuracy / 0 mean phantom pcs/hop (49/49),
`density` stayed within the existing 0-0.73 missing-pcs/0-0.33 phantom-
pcs baseline range across 1-6 simultaneous notes (see the issue #67/#68
round 2 entry above for that baseline's origin).

**False-alarm digression, recorded because it nearly became a wrong
conclusion.** A first post-fix `chords` suite run measured a startling
77.6% chord-name accuracy (down from the ~100% baseline) with a high
0.437 mean phantom-pcs/hop — on its face, a serious regression. Before
accepting that, the exact failing case (`Csus4`, register 4) was
reproduced in isolation as a static synthesized waveform (matching
`synth_notes()`'s own harmonics-1-4-weighted-like-`HARMONIC_WEIGHTS`
construction): `multipitch.detect()` returned the *identical* 3-note
result with the frequency bound on or off. That ruled out the fix itself
as the mechanism for a single chord's peak-picking. A controlled A/B
against the real live pipeline followed: `min_freq_hz`/`max_freq_hz`
temporarily forced wide open at the real `main.py` call site (`0.0`/
`1e9`, bound effectively disabled) reproduced a clean 100%/0-phantom
result; putting the real fix back (`config.FMIN`/`FMAX`) and re-running
immediately after reproduced the clean 100%/0-phantom result too. The
original bad run did not replicate — concluded to be transient
environmental noise in this particular dev session (this machine had
another long-running `virtualnote.py` process open concurrently for the
whole session, a plausible source of audio-device contention against
`--source loopback`'s own PipeWire capture), not a real code regression.
Recorded here per this codebase's own precedent (issue #69's real-mic
regression-and-recalibration round) that a single suspicious acoustic-
suite number is a lead to chase with a controlled re-run, not a
verdict to act on immediately — in this instance the re-run cleared the
fix rather than confirming a regression, which is exactly why the
re-run step matters.

## Phantom in-register percussion note in chord mode: root-caused, no safe fix found (issue #75)

### Reproduction, and the original hypothesis turned out to be wrong

The same new `percussion` acoustic-test suite as #74's entry above, a
different tier: a genuinely sustained, unchanging chord (Cmaj or Am7, 4
bars, no chord change) with `BASIC_BEAT` (a plain kick/snare/hi-hat rock
beat) playing underneath. Reproduced on a fresh run (`--source loopback
--suites percussion`) post-#74: Cmaj+drums showed 8/8 kick hits
correlating with a spurious `chord_duration_tracker` finalize event
(`(pitch_class=7, octave=3)` — G3, ~196Hz — every time); Am7+drums showed
4/8. Chord-name accuracy stayed 100% throughout in both conditions (the
naming pipeline reads `chroma.fold()` directly, not
`multipitch.detect()`'s note list, so this is purely a phantom-note/
duration bug riding alongside a correctly-named chord).

Issue #75's original write-up attributed this to the kick drum's own
swept-sine decay (`synth_kick()`: 150Hz -> 45Hz exponential sweep, ~130ms)
landing near G3 during its decay tail. **That hypothesis does not survive
a timing check against the raw per-hop log.** Correlating each of the 8
Cmaj phantom-finalize timestamps against the nearest kick vs. the nearest
snare in the same window:

| phantom finalize `t` | nearest snare | offset | nearest kick | offset |
|---|---|---|---|---|
| 31.084 | 30.900 | +0.184 | 31.500 | -0.416 |
| 32.291 | 32.100 | +0.191 | 32.700 | -0.409 |
| 33.503 | 33.300 | +0.203 | 33.900 | -0.397 |
| 34.684 | 34.500 | +0.184 | 35.100 | -0.416 |
| 35.890 | 35.700 | +0.190 | 36.300 | -0.410 |
| 37.104 | 36.900 | +0.204 | 37.500 | -0.396 |
| 38.286 | 38.100 | +0.186 | 38.700 | -0.414 |
| 39.490 | 39.300 | +0.190 | 38.700 | +0.790 |

Every single event sits a tight, consistent ~184-204ms after a **snare**
hit, never near a kick. The Am7 tier's 4 events show the identical
pattern (~264-278ms after the nearest snare — a different offset than
Cmaj's, consistent with Am7's different chord content interacting
differently with harmonic pruning, but still snare-locked, never
kick-locked).

### Corrected root cause

`synth_snare()` (`scripts/acoustic_pipeline_test.py`) synthesizes a real
snare drum's two components deliberately: a broadband noise "body" (the
rattling snares) *and* "a brief low-mid tonal 'poc' component (~200Hz,
the shell/head resonance) at the attack" — a genuine, physically-modeled
feature of real snare drums, not a synthesis artifact to tune away. 200Hz
is only ~35 cents from G3 (196.00Hz) — right at the edge of
`CHORD_HARMONIC_TOLERANCE_CENTS` (35.0). `multipitch.detect()` correctly
finds this as a real spectral peak (it genuinely is one: a real sine
partial, not noise), `chord_smoother`'s `NOTE_STACK_ATTACK_HOPS` (2)
correctly promotes it into `raw_stack` after it's genuinely detected on 2
consecutive hops, and `chord_duration_tracker.update()` (fed from
`raw_stack`, always `is_onset=False` per chord mode's documented design)
correctly tracks and finalizes its ~3-4-hop lifetime as a duration event
once it decays/disappears. Every stage behaves exactly as designed on a
signal that is, from a single hop's magnitude/frequency alone,
indistinguishable from a very short real note. Confirmed directly with a
synthetic repro (no audio hardware): mixing `synth_notes()`'s
Cmaj/Am7 voicing with `synth_snare()` and sliding `multipitch.detect()`
across the snare's onset reproduces the identical `(pitch_class=7,
octave=3, freq≈197-200)` candidate, appearing 3-4 consecutive hops after
the snare's onset and nowhere near any kick.

Cmaj (C-E-G) and Am7 (A-C-E-G) both happen to already contain G as a real
chord tone — not a coincidence of picking these two test chords
specifically to dodge the collision, since the phantom's *detected*
pitch class is driven purely by the snare's own fixed 200Hz frequency,
independent of whatever chord is playing; any chord containing G (or
G#/Ab, the only other pitch class within a few dozen cents of 200Hz)
would collide the same way, and ~200Hz is a genuinely representative
real snare shell-resonance frequency, not a synthesis parameter chosen
adversarially.

### Candidate fixes evaluated, and why each was rejected

**(a) Minimum-persistence gate before chord-mode's `DurationTracker`
opens a new tracked state** (mirroring mono's
`require_onset_for_new_note`, per CLAUDE.md's own suggestion). Rejected:
the phantom's total measured lifetime here (`duration_hops` 2-5, i.e.
~45-115ms) is the same order of magnitude as the shortest *legitimate*
note this pipeline is explicitly designed to track — issue #55 story 3's
280bpm-eighth-note stress case is ~107ms/note. A threshold high enough to
reliably reject this phantom would sit right on top of, or above, that
legitimate case, with no safety margin — exactly the collision CLAUDE.md
flagged before any fix was attempted, now confirmed with the actual
numbers rather than just reasoning about it.

**(b) A magnitude/decay-shape heuristic in `multipitch.detect()` itself**
(distinguish a peak that's actively decaying within one hop's window
from a genuinely sustained partial) — the most promising-looking
candidate, so it was empirically prototyped rather than reasoned about
abstractly: for each candidate peak, split its analysis window in half
and compare FFT magnitude at that peak's frequency in the first half vs.
the second half. The phantom's ratio is indeed never close to 1.0 during
its entire life (0.01, 0.0, 1.25, 4.58, 4.88, 4.34, 5.07 across
consecutive hops — always either onset-rising or decay-falling, never
flat), which looked like a clean signature. **It is not a safe
differentiator**: the exact same experiment run against a real chord's
own genuine onset (the Cmaj control tier's attack, no drums at all) shows
an *identical*-shaped ratio trajectory for the same 3-5 hops (0.0, 0.0,
0.04, 0.66, 0.99, 1.0...) before settling near 1.0 — because a real
note's own attack transient *is* a within-window energy asymmetry, for
almost exactly as long as this phantom's entire lifetime. Any threshold
on this ratio that rejects the phantom would equally reject or delay a
real note's own onset by the same 3-5 hops, directly reopening the onset-
latency concerns issue #40/#55 already balanced carefully, and compounds
with issue #70's already-documented onset-timing fragility. Disproven
empirically, not just suspected.

**(c) Treating a close near-subharmonic match as prunable in
`multipitch._is_harmonic_of()`/pruning** (i.e. catch "196Hz is
suspiciously close to half of the real 392Hz chord tone" the same way
harmonic-consistency pruning already catches overtones). Checked the
actual cents math for this exact case: `_is_harmonic_of(392, 200, ...)`
computes harmonic_number=2, predicted=400, and
`1200*log2(392/400) ≈ -34.9` cents — already *inside* the existing 35.0
cent tolerance, i.e. this candidate sits right at the tolerance's edge by
construction, not comfortably outside it. Whether G4 (the real chord
tone) gets pruned as a harmonic of the phantom G3 or not on any given
hop is therefore already a coin flip driven by sub-Hz frequency-estimate
noise — confirmed by the raw log itself, where G4 and phantom-G3 are
seen coexisting in `note_stack` on some hops. Deliberately tightening
this further (or adding a reverse/subharmonic-direction check) is exactly
the kind of order-dependent, tolerance-boundary fragility issue #67 fixed
once already and the harmonic_number<=4 residual limitation (CLAUDE.md's
Known limitations) already documents for a structurally identical
near-miss case; doing so risks reopening #67/#68's evaluation-order
correctness for a marginal, unproven benefit here.

**(d) An amplitude/confidence threshold** (only trust a candidate loud
enough relative to the chord) was also considered and rejected without a
separate prototype: the raw per-hop confidence values captured during
the same synthetic repro show the phantom hitting `confidence=1.0` (the
single loudest peak that hop) on several hops, while the real chord
tones' confidence *drops* in relative terms during the same window
(0.66-0.99) — a real snare hit is often genuinely louder than a sustained
chord underneath it in absolute terms, so there is no magnitude-based
line to draw either.

### Conclusion: no safe fix found, left open

All three concretely-evaluated fix directions either have no safety
margin against a legitimate, already-documented use case (a) or are
empirically indistinguishable from a real note's own onset (b), or are
already known-fragile tolerance-boundary territory this codebase has
explicitly chosen not to keep tightening (c/d). Per this repo's own
established convention for this situation (see the harmonic_number<=4
residual limitation, and issue #70's onset-timing residual), this is
recorded as a known limitation rather than forced into an unsafe fix:
**a real percussion instrument's own attack-transient tonal content
(a drum shell/head resonance, a cymbal's strike fundamental, etc.) can
coincidentally land close enough to a real pitch class to be
indistinguishable, on a single hop's or even a few consecutive hops'
magnitude/frequency evidence alone, from a very short genuine note.**
Resolving this fully would need information no single hop or short hop
run can supply on its own — e.g. cross-referencing a full onset-detection
transient classifier against the broadband/noise-body component
`synth_snare()`/`synth_kick()` already model separately from their tonal
components, which is a materially bigger feature than this issue's scope,
not a targeted bug fix. Issue #75 is left open with this investigation
recorded rather than closed, so a future, better-scoped attempt (e.g.
alongside a real onset/transient classifier, if one is ever built for
other reasons) has this groundwork rather than starting over.

### Second round: two more angles tried, same conclusion

A follow-up pass looked for a genuinely different signal from the three
already-rejected candidates above -- not a re-attempt of (a)/(b)/(c), but
two new mechanisms neither of which this codebase had tried yet.

**(e) A persistence gate scoped narrowly to "duplicate pitch class,
different octave, while another octave of that pitch class is already an
active `DurationTracker` state"** -- i.e. not (a)'s blanket minimum-
persistence-before-opening-any-new-state gate, but one that only engages
for the exact shape the phantom always has (a new key whose pitch class
duplicates an already-long-sustained different-octave key). This is
purely internal to `DurationTracker` (it already knows every currently
active key's pitch class), so it was cheap to prototype: a pending-state
buffer that accumulates peak/last magnitude across a grace window and,
if the candidate survives `duplicate_pc_grace_hops` consecutive hops,
opens a normal tracked state backdated to when it first appeared (so a
note that does survive the grace window keeps its true measured
duration, not a truncated one) -- otherwise the candidate is dropped with
no finalize event at all.

Prototyped and tested in full isolation from multipitch/chord_smoother
(directly against `DurationTracker.update()` with hand-built per-hop note
sequences, to remove the harmonic-pruning coin-flip noise a full pipeline
run adds -- see below) with two matched scenarios: a "phantom" candidate
present for exactly N consecutive hops then gone, and a "genuine" candidate
present for the *same* N hops then gone (a player releasing a real short
note, not a decaying transient). **Both scenarios produced byte-identical
suppression behavior at every grace value tried (4-8 hops):** whichever
grace threshold reliably suppressed the phantom (grace > its ~5-hop
observed max lifetime) also completely suppressed the genuine same-length
note, with zero finalize event either way -- not delayed, not
under-measured, simply never reported at all. This is the same
information-theoretic wall (a) already hit, just relocated to a narrower
input class: duration/persistence alone cannot distinguish "short because
it's a transient" from "short because the player played it briefly" no
matter how the gate is scoped, because both produce the exact same
`DurationTracker` input shape. Narrowing the gate's blast radius from "any
new note" to "duplicate-pitch-class new notes only" doesn't buy safety --
it only shrinks which real notes get silently dropped, and duplicate-
pitch-class-different-octave notes are not a rare or contrived case to
sacrifice: octave-doubling (playing the same pitch class in two registers
at once, or adding a doubled note shortly after the first) is one of the
most common real voicing techniques on piano and guitar alike, arguably
*more* common than the phantom scenario this would be trading against.
Rejected on the same evidence standard as (b) -- disproven empirically
with a matched control, not just reasoned away. A full-pipeline run of
this same prototype (through real `multipitch.detect()`/`chord_smoother`
output, not hand-built sequences) additionally surfaced a second,
independent confound worth recording: introducing a genuine new
lower-octave note can itself intermittently knock the *existing* higher-
octave note out of `multipitch.detect()`'s harmonic-pruning acceptance
(the note ordering shifts once a second candidate near a harmonic-of-2
relationship is present), reproducing (c)'s already-documented coin-flip
independently of any duration-tracker change -- consistent with, not a
new instance beyond, what (c) already found.

**(f) Spectral breadth of the chroma novelty at the candidate's onset
hop** -- distinct from (b) (which looked at one peak's *temporal* shape
within its own analysis window): this instead asks whether a percussive
transient's broadband energy shows up as a wide spread of simultaneously-
rising `chroma.fold()` bins (many of the 12 pitch classes jumping at
once, since noise has no privileged pitch class) versus a real note's
attack concentrating its novelty into one or two bins (its own pitch
class plus close harmonics). Tested directly: computed `chroma.fold()`
frame-to-frame flux and its per-hop "how many of the 12 bins moved
together" spread across three synthesized scenarios -- the documented
snare-hit phantom, a genuine full chord attack from silence, and a
genuine single new note added to an already-sustained chord. All three
showed the same noisy, overlapping pattern: hops where all 12 bins moved
together happened in *every* scenario, including the two genuine-note
ones (a real chord's own onset, and a real added note's onset, both
routinely lit up most or all 12 chroma bins on at least one hop each).
Root cause is `fold()`'s own already-documented harmonic-summing bleed
(issue #56's docstring in `chord_smoother.py`): a single real note's 2nd-
4th harmonics land across several *other* pitch-class bins by design, so
a genuine attack is already spectrally "broadband" at this 12-bin,
single-hop resolution -- there isn't a clean narrowband/broadband line to
draw here either, for essentially the same reason (b) found no clean
within-window-shape line: a handful of hops of coarse aggregate spectral
evidence just isn't enough resolution to separate "a real note's own
onset mechanics" from "an unrelated transient's broadband onset,"
regardless of which axis (temporal shape, spectral breadth, or now
duration/persistence) is used to look for the difference.

A third angle was considered but not separately prototyped, since the
reasoning is conclusive without needing a run: **scaling any persistence
gate to the *live tempo estimate* instead of a fixed hop count** (i.e.
require some fraction of a beat rather than a fixed ms/hop threshold,
so the gate tightens automatically at faster tempos where legitimate
notes are also shorter). This does not resolve the underlying conflict,
it just relocates where the two curves cross: the phantom's real-world
duration is governed by drum-transient physics (the shell/head
resonance's own decay time, tens of ms, tempo-independent), while a
legitimate short note's duration is tempo-proportional by construction.
At a slow tempo both curves sit comfortably apart (a phantom is short in
absolute terms, a legitimate note is long in absolute terms) and a fixed
gate already works fine there; at a fast tempo -- exactly where a real
song is more likely to have both a driving beat *and* fast passages
played over it -- a tempo-relative gate shrinks in lockstep with
legitimate note length, converging on the same phantom-sized duration
it's trying to reject. A tempo-relative gate is not more discriminating
than a fixed one, it just makes the failure mode tempo-dependent instead
of a flat constant.

Conclusion unchanged from the first round: no signal available at the
`DurationTracker`/`multipitch`/`chroma` layer -- duration, temporal decay
shape, spectral breadth, or tempo-relative scaling of any of the above --
can separate this phantom from a legitimate short note, because both
produce indistinguishable input at every one of those layers. Left open,
same as before; the groundwork above (particularly (e) and (f)'s
prototypes, both matched-control-tested rather than merely reasoned
about) is recorded so a future attempt doesn't re-spend effort
re-discovering that these two don't work either.

## Barline drift, round 2: `_pad_center()` was padding to the wrong cell width for duration-glyph notes (issue #82)

`d7d2ea0`'s fix (`_pad_center()` measuring cell text with `wcwidth.
wcswidth()` instead of naive code-point counting) was believed to fully
close the barline-column-misalignment bug class. `research/terminal-
capture/`'s new `pyte`+Pillow capture tool (built for unrelated visual
bug-catching, see its FINDINGS.md) immediately found a real residual case
by comparing `TabDisplay.render()`'s actual ANSI output, fed through
`pyte`, against what `_pad_center()` assumed: every screen row carrying a
note with at least one duration-glyph combining mark (`STEM_GLYPH`/
`FLAG_GLYPHS`/`DOT_GLYPH`, all Unicode General_Category "Mc") drifted a
following barline column 1 real column left, regardless of whether the
note had 1, 2, or 3 such marks — only the `whole`-note row (no marks at
all) landed correctly.

**Root cause:** `wcwidth.wcswidth()` has a deliberate heuristic that
forces a base+Mc-mark grapheme cluster to measure exactly 2 columns
regardless of how many further Mc marks follow — `d7d2ea0` built
`_pad_center()`'s padding math on that number. But `pyte`'s own
cursor-advance model (`Screen.draw()`) — the standards-conformant one,
per `unicodedata.combining()`-driven zero-advance — treats any codepoint
where `wcwidth(ch) == 0` and `unicodedata.combining(ch) != 0` as truly
consuming 0 columns, merging it into the previous cell. All three
duration-glyph codepoints qualify (confirmed directly: `wcwidth() == 0`,
`combining()` class 216 for stem/flags, 226 for the dot). So a real
terminal advances its cursor only 1 column for a notehead + any number of
these marks, not the 2 `_pad_center()` assumed — a real, reproducible
1-column desync on exactly the rows carrying duration-glyph notes. Prior
research (`docs/research/terminal-rendering-performance.md`, its own
`ucs-detect`-survey citations) had already flagged, in the abstract, that
several real terminals (Windows Terminal, cmd.exe, ConsoleZ, ...) measure
this exact Mc category as narrow (width 1) rather than zero-width — a
third possible drift amount, and independent evidence that `wcswidth()`'s
cluster-forced-to-2 answer is not a safe default to build on for this
codepoint class.

**Fix:** `_pad_center()`/`_clip_to_width()` now measure with a new
`_display_width()` (`terminal_tab_display.py`) — a per-codepoint sum using
the same zero-advance rule `pyte`'s `Screen.draw()` applies (`wcwidth(ch)
== 0 and unicodedata.combining(ch)` → contributes 0), rather than
`wcwidth.wcswidth()`'s grapheme-cluster heuristic. A notehead + any
number of duration-glyph marks now measures as 1 column, matching what
`pyte` (and, per the `ucs-detect` survey, most real terminal emulators)
actually advance the cursor by. Verified via `research/terminal-capture/
capture.py`'s exact repro: before the fix, the barline glyph landed at
x=76 on rows with 1-3 combining marks and x=77 on the `whole`-note row;
after, every row (0/1/2/3 marks) lands at the same x=98 in a fresh
100x30-terminal render, fed through `pyte`. `tests/
test_terminal_tab_display.py` gained two direct regression tests
(`_display_width()` returning the same value for a bare notehead as for
notehead+1/2/3 combining marks; `_pad_center()`'s padded output measuring
the same total width across all four real duration classes) plus updated
its existing barline-alignment/width tests to measure via the new
`_display_width()` instead of `wcwidth.wcswidth()` (which would now
disagree with the app's own corrected padding by construction, since
that's exactly the heuristic being moved away from). Full suite: 372
passed.

**Caveat, stated explicitly per this repo's own track record on this
exact question:** this fix is validated against `pyte`'s standards-
conformant model and the prior `ucs-detect` research survey, not a live
sweep of real terminal emulators — no physical/GUI terminal was available
in this environment (a `kitty`-under-`Xvfb` attempt in the prior research
round was inconclusive for unrelated environment reasons, see FINDINGS.md).
The zero-advance model chosen here matches `pyte` and is the
standards-conformant default most terminals build on, but the `ucs-detect`
survey already found a real minority (Windows Terminal, cmd.exe, ConsoleZ,
ExtraTermQt, zoc) that measure this exact Mc category as width-1 instead
of width-0 — on those, this fix would leave a smaller residual drift than
before (1 column instead of up to 3, since multiple marks no longer
compound), not necessarily zero. Same posture as issue #69's history:
treat this as provisionally fixed and evidence-based, not field-confirmed,
until a live multi-terminal visual check happens.

## `tab` view's beat-accumulator double-counted mono+chord tracker beats, halving barline spacing (issue #76)

Root-caused via a research subagent
(`docs/research/tab-barline-straightness.md`): `run_terminal_tab()`'s
`beats_accumulated` was incremented by **both** the mono
`DurationTracker`'s finalization **and** every `note_stack` (chord/
multipitch) entry's finalization, summed, every hop — unconditionally,
regardless of the view's `P` display toggle, since the chord/multipitch
pipeline always runs (this codebase's documented always-on-pipeline
convention, see CLAUDE.md's Architecture). An ordinary single monophonic
note is routinely finalized independently by both trackers in the same
hop (the mono smoother's own note, and multipitch's one-note "chord" for
the same acoustic event), so summing credited that one note's duration
toward the next bar boundary roughly twice — barlines landing at
~half the correct spacing, independent of tempo-estimate accuracy; a
perfect BPM estimate would not have fixed it. `run_batch_transcribe()`
never had this bug: it already takes `max()` over every simultaneous
note at one onset (`main.py`'s `column_beats = max(column_beats,
note_beats or 0.0)`) rather than summing across mono/chord streams —
proof this was a fixable asymmetry, not something inherent to tracking
mono and chord data in parallel.

Fixed by extracting the per-hop credit into a small pure function,
`main._hop_beats(beats_values)` — takes the max across whatever
durations (mono's, if any, plus one per finalized `note_stack` entry)
finalized this hop, treating a `None` value (bpm_estimate unknown at
finalization time) as 0.0 — and calling it once per hop instead of
accumulating inline sums from two separate code blocks. Mirrors
`run_batch_transcribe()`'s existing pattern exactly, just factored out
into a named, unit-tested helper (`tests/test_main.py`) rather than
inlined per-block max-tracking, following this repo's "pure logic
unit-tested, real render loop smoke-tested" convention (see
`menu_animation.detect_perf_mode()`/`_decide_perf_mode()` for the
precedent this follows). Barline *placement* accuracy tied to live
tempo-estimate quality is unaffected by this fix and remains the
already-documented, accepted approximation (CLAUDE.md's Known
limitations) — this fix corrects the beat-*count* per hop, not the
BPM conversion factor.
## Mono `tab` *name* style's duration suffix was illegible: own wide column added (issue #83)

`_cell_text()`'s *name* style composes `f"{letter}·{suffix}"` (e.g.
"Bb·16th."), up to 8 display columns for the worst case (a 2-char
accidental letter + middle dot + the longest 5-char suffix, "whole"/
"half."/"16th."). Mono columns render at `config.TAB_COLUMN_WIDTH = 3`
— `_pad_center()` correctly clips oversized text to fit, but the
*result* was illegible: "C·whole" -> "C·w", "A·16th." -> "A·1". Chord
mode never had this problem, since `TAB_COLUMN_WIDTH_CHORD = 9` was
already sized for chord names of similar length.

Three options were on the table: widen `TAB_COLUMN_WIDTH` itself (moves
every mono column, symbol style included, denser layout lost for a
problem specific to one toggle state); abbreviate suffixes further to
fit 3 cells (a single-letter code like "w"/"h"/"q"/"e"/"s" loses
real information — dotted vs. undotted collapses onto the same letter
unless a second symbol is added, which is most of the way back to
needing more width anyway); or give mono name-style-with-duration its
own wider column, mirroring `TAB_COLUMN_WIDTH_CHORD`'s existing
precedent for exactly this "this render mode's text is wider than a
default cell" situation. Took the third option:
`config.TAB_COLUMN_WIDTH_NAME = 9` (same value as `TAB_COLUMN_WIDTH_CHORD`
by coincidence — both landed on ~9 cells for unrelated content — kept as
its own constant so the two can move independently later), selected in
`TabDisplay.render()` only when `not chord_mode and notehead_style ==
"name"`. Symbol style (whose duration glyphs are combining marks
composed onto the notehead, not extra text) keeps `TAB_COLUMN_WIDTH`
unchanged, so its already-fine compact layout is untouched. Verified via
`research/terminal-capture/capture.py --scene tab-name`: all four
duration suffixes in that scene ("C·whole", "G·4th", "D·8th", "A·16th.")
now render in full, unclipped.

## Mono/chord dynamics sensitivity gap: `RMS_SILENCE_THRESHOLD` recalibrated, `CONFIDENCE_THRESHOLD` left alone (issue #72)

`scripts/acoustic_pipeline_test.py`'s new `dynamics` suite (loud-to-whisper
`base_amp` sweep, `--source loopback`) found mono note recall cratering
completely between `base_amp=0.05` ("quiet", recall 1.0) and `base_amp=0.02`
("very_quiet", recall 0.0), while chord-name accuracy and phantom/missing
pitch-class rates stayed perfect down to `base_amp=0.008` ("whisper") —
over 6x quieter than where mono went fully silent. Investigated in full
before changing anything, per the issue's own explicit ask for "at
minimum, a deliberate decision."

**Step 1: how much of the 6x gap is the reported chord-synthesis
amplitude artifact, not a real gate difference?** `synth_notes()` sums
each chord tone independently at the same `base_amp` before a
down-only peak-cap, so a chord's *summed* signal is inherently louder in
absolute terms than a single note at the same `base_amp` — the issue
itself flagged this as unverified. Measured directly (no audio hardware
needed — pure NumPy on `synth_notes()`'s own output): at every `base_amp`
below the peak-cap's engagement point (i.e. every level this suite's
`quiet`/`very_quiet`/`whisper` tiers actually exercise), a 3-note major
triad's steady-state RMS is ~1.73x a single note's at the same
`base_amp`, and a 4-note min7's is ~2.0x — both match the textbook
incoherent-sum √N prediction for independently-phased tones almost
exactly. That's real, but nowhere near 6x — most of the reported gap is
not this artifact.

**Step 2: is it the RMS gate or the confidence gate doing the killing?**
A small standalone probe (`main.SessionState` + real `--source loopback`
round trip, `RenderItem.rms`/`.confidence` logged per hop — not part of
the checked-in suite, a throwaway diagnostic) played a single C2 tone at
`quiet`/`very_quiet`/`whisper`/an even quieter `ultra` (0.002) level and
logged both fields hop-by-hop. Result: YIN's confidence measured a flat
**1.000** at every level tested, all the way down to `ultra`
(captured RMS ~0.0008) — the periodicity evidence in this clean digital
loopback signal is not degrading at all as amplitude drops, since
loopback has no physical noise floor (mic self-noise, room rumble) to
erode it. What *does* change is the raw captured RMS crossing
`config.RMS_SILENCE_THRESHOLD` (0.01 at the time): `quiet` measured
~0.020 (passes), `very_quiet` ~0.008 (fails), `whisper` ~0.003 (fails).
The recall cliff lines up almost exactly with that crossing. Confirmed:
**`RMS_SILENCE_THRESHOLD`, not `CONFIDENCE_THRESHOLD`, is the sole
mechanism gating mono out at these levels** — `NoteSmoother.update()`
short-circuits on `rms < self.rms_silence_threshold` before confidence is
even consulted for candidate promotion.

**Step 3: does the `noise` suite (issue #71's regression guard) actually
exercise this threshold at all?** `_add_noise()`'s injected Gaussian
noise is `amplitude` directly (not scaled relative to note volume) — the
suite's own quietest nonzero tier, `light`, uses `amplitude=0.05`, giving
a silence-gap RMS of ~0.05 (for zero-mean Gaussian noise, RMS ≈ std).
That's already 5x the *old* `RMS_SILENCE_THRESHOLD` (0.01) and 10x any
candidate lowered value discussed below — meaning the `noise` suite's
synthetic noise floor clears the RMS gate regardless of where this
constant sits, both before and after any change in this range. Issue
#71's actual regression-guard mechanism is entirely `CONFIDENCE_THRESHOLD`
(YIN correctly returning `(None, 0.0)` on noise-degraded input post-#71's
fallback removal, then `NoteSmoother` gating on confidence, not RMS) —
this constant and that one are orthogonal in the region either suite
actually tests.

**Decision: lowered `RMS_SILENCE_THRESHOLD` 0.01 → 0.005 (2x), left
`CONFIDENCE_THRESHOLD` untouched.** This is a real, evidenced recovery of
real headroom (recovers `very_quiet`, the exact tier the issue's own data
table showed failing) with a change that provably cannot interact with
issue #71's fix (see Step 3) and doesn't touch the confidence gate #71
already spent a full threshold sweep calibrating. Deliberately **not**
chased further to fully close the gap down to chord's own whisper-level
floor (0.008 `base_amp`, RMS ~0.003), for three reasons: (1) chord mode's
insensitivity to amplitude at all is architectural, not a threshold this
project could match on mono's side even in principle — `chord_templates
.match()`'s cosine similarity is scale-invariant by construction, with no
absolute floor anywhere in that path, whereas mono's silence gate exists
specifically to stop `NoteSmoother`/YIN from chasing near-zero-energy
blocks, a deliberate difference already documented in CLAUDE.md's
Architecture section, not a bug to erase; (2) this project has a
documented history (issues #69, #71, both twice) of synthetic/loopback
conclusions *not* surviving real-mic re-verification, specifically
because loopback has no real acoustic noise floor to test against — this
whole investigation is exactly that kind of conclusion (Step 2's `1.000`
confidence at `ultra` is a property of a noiseless digital path, not
evidence about what a real mic's self-noise floor would do to a
correspondingly lower RMS gate), so a large jump felt reckless without
that check even though nothing here contradicts it; (3) `--sensitivity`
(CLI flag, live `[`/`]` hotkeys) already gives a user actually playing
quietly in a real quiet room a bigger, no-code-change lever than this
default ever will — `NoteSmoother.set_sensitivity()` divides both gates
by it directly, so a user who needs `whisper`-level headroom already has
a path to it today.

**Validated, both loopback suites re-run before/after on the same real
`--source loopback` round trip:**

| suite / level | before (0.01) | after (0.005) |
|---|---|---|
| dynamics: very_quiet note recall | 0.0 | **1.0** |
| dynamics: whisper note recall | 0.0 | 0.0 (unchanged, as intended — see reason 2/3 above) |
| dynamics: chord accuracy, all levels | 1.0 | 1.0 (unaffected, no gate to move) |
| noise: light note accuracy | 1.0 | 1.0 (unchanged) |
| noise: moderate note accuracy | 0.0 | 0.0 (unchanged — correctly still silence-gated, not #71's wrong-confident failure mode reopened) |
| noise: moderate chord accuracy | 1.0 | 1.0 (unchanged) |

(`noise`'s `clean`-tier mono accuracy moved 0.731→1.0 between the two
runs; per Step 3 this constant has zero effect on `clean`'s
noiseless/normal-volume audio either way, so that's ordinary
run-to-run loopback jitter — CLAUDE.md's already-documented "quality
varies run-to-run with room/mic conditions," not this change.)

Full `pytest tests/`: 326 passed against every file this change could
plausibly affect (`tests/test_note_smoother.py` reads
`config.RMS_SILENCE_THRESHOLD` symbolically, not a hardcoded literal, so
needed no update). Two unrelated failures in
`tests/test_terminal_tab_display.py` (`NameError: wcwidth`) at the time
of this investigation belong to a concurrent agent's in-progress edit to
that file (issue #82/#83 territory) — confirmed by `git diff --stat`
showing that file already modified before this investigation touched
anything, and by the failure itself (a missing import, nothing to do
with silence gating) — not caused by, or fixed by, this change.

**Still open, honestly:** the real-mic re-verification caveat above is
real, not boilerplate — this conclusion is loopback/synthetic-validated
only, same standing caveat as issues #69 and #71's own entries. If a
future real speaker→mic session finds 0.005 too permissive (a real room's
noise floor doing to this gate what it can't do here), the fix is a
one-line revert, same low blast radius as the change itself.
