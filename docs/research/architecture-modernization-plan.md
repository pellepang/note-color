# Architecture modernization: pluggability, config structure, packaging

Research/assessment doc, no ticket assigned yet. Written against the
codebase as of commit `a913d2e` (docs: record issue #75 second-round
investigation). Scope: (1) how hard would it be, *today*, to swap in an
alternative pitch/chord/onset backend, add a 5th display, or keep growing
`config.py`; (2) concrete staged refactors, sized and risk-rated; (3) a
prioritized roadmap that's honest about where a proposal would be
over-engineering for a single-user hobby project; (4) packaging.

This is a companion to the sibling algorithm-landscape docs already in
this directory (`oss-landscape-pitch-detection.md`,
`oss-landscape-chord-multipitch.md`, `oss-landscape-rhythm-tempo.md`) —
those evaluate *which* alternative algorithms might be worth adopting;
this doc only asks whether the codebase's *shape* would let such a choice
be made without a rewrite. No algorithm evaluation happens here.

## Status update (2026-09-02)

This doc sat uncommitted for a while after being written, and real work
landed against its own §3.1 and §5 proposals in the meantime — noting
that here rather than silently rewriting the sections below as if they'd
predicted their own completion:

- **§3.1 (`DetectionBackend` protocol): done.** `detection_backends.py`
  exists in the repo (`MonoPitchBackend`/`PolyphonicBackend` Protocols,
  `YinBackend`/`SpectralPeakBackend` adapters, `default_pitch_backend()`/
  `default_poly_backend()`), and `analysis_loop()`/`SessionState` call
  through it — essentially exactly as proposed below, including the
  explicit call to leave `multipitch.select_window()` outside the
  Protocol. See CLAUDE.md's Files table entry for `detection_backends.py`
  and its Key design decisions entry.
- **§3.2 (config package split): correctly still not done.** `config.py`
  is 345 lines as of this note (up from the 302 this doc was written
  against) — short of the "roughly 500-600 lines" trigger §4's roadmap
  table sets for when this split would actually pay for itself. The
  recommendation to defer still holds; nothing below needs revising on
  this point yet.
- **§3.3 (display dispatch table): not done.** `main.run_session()`
  still dispatches via a plain `if view == "gui": ... if view == "wheel":
  ... if view == "tab": ...` chain — no `VIEWS` dict exists in `main.py`.
  This proposal is still live/actionable as written.
- **§5 (packaging): done.** `pyproject.toml` exists at the repo root with
  a `[project.scripts] virtualnote = ...` entry point, and `librosa`/
  `music21` are split into a `[project.optional-dependencies] batch`
  extra — matching this section's recommendation closely. CLAUDE.md's
  "Running it" section now documents `pip install -e .`/`pip install
  -e .[batch]` accordingly.

The rest of this document is left as originally written (including the
now-partially-stale "current state" framing in §1.1 and §5) so the
reasoning that led to the §3.1/§5 proposals stays intact and legible —
treat any claim below of the shape "there is no X in the codebase" as
accurate *as of the commit this doc was written against*, not as of
today, wherever this status block says otherwise.

## 0. Method

Read `CLAUDE.md` and `docs/DECISIONS.md` in full first, since both
document a lot of deliberate, empirically-reasoned architectural choices
(why chord mode's pipeline always runs unconditionally, why `librosa` is
isolated to exactly two files, why the `R`-key recompute uses a throwaway
thread instead of a request/response queue into the analysis thread, why
terminal views are raw ANSI except one scoped `blessed` exception, etc.).
Then read the actual source: `main.py` (all 1393 lines), `config.py` (all
302 lines), `audio_capture.py`, `shell.py`, `virtualnote.py`,
`config_store.py`, `pitch_detect.py`, `multipitch.py`, `chroma.py`,
`chord_templates.py`, `note_smoother.py`, `chord_smoother.py`,
`duration_tracker.py`, `tempo_tracker.py`, `onset_detect.py`, `display.py`,
`terminal_display.py`, `terminal_wheel_display.py`, and the structural
shape (not every line) of `terminal_tab_display.py` (809 lines). External
research covered hexagonal/ports-and-adapters architecture in Python,
Essentia's `Algorithm`/`AlgorithmFactory` registry and standard-vs-streaming
dual API, and Python plugin-discovery conventions (in-repo registry vs.
`setuptools` entry points) — cited inline where used.

No source file was modified to produce this doc; only this file was
created.

## 1. Grounded assessment of current coupling

### 1.1 Swapping the detection backend

**The good news first: the stabilization layer is already backend-agnostic.**
`NoteSmoother` (`note_smoother.py`), `ChordSmoother` (`chord_smoother.py`),
and `DurationTracker` (`duration_tracker.py`) don't know anything about
YIN or spectral peak-picking specifically — they consume `(freq_hz,
confidence, rms, spectrum)` for mono and a list of
`multipitch.NoteCandidate(pitch_class, octave, freq, confidence)` for
poly. Any algorithm that can produce those same shapes slots in without
touching the smoothing/debounce/duration-tracking code at all. That's a
real seam, and it's the one a `DetectionBackend` proposal should build on,
not replace.

**The bad news: nothing calls through that seam via an interface — it's
direct function calls from inside one large function.**
`main.analysis_loop()` (`main.py:299-461`) is a single ~160-line function
that, every hop, does (paraphrasing the actual call sites):

```python
freq, confidence = detect_pitch(
    ring, config.SAMPLE_RATE, spectrum, config.FMIN, config.FMAX, config.YIN_THRESHOLD,
    config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
)                                                                          # main.py:329-332
...
raw_notes = multipitch.detect(
    multipitch_window, config.SAMPLE_RATE, max_notes=config.CHORD_MAX_NOTES,
    min_mag_ratio=config.CHORD_PEAK_MIN_MAG_RATIO,
    harmonic_tolerance_cents=config.CHORD_HARMONIC_TOLERANCE_CENTS,
    max_peak_candidates=config.CHORD_MAX_PEAK_CANDIDATES,
    harmonic_max_number=config.CHORD_HARMONIC_MAX_NUMBER,
    min_freq_hz=config.FMIN, max_freq_hz=config.FMAX,
)                                                                          # main.py:376-386
```

Both calls are free functions imported directly (`from pitch_detect import
... detect_pitch`, `import multipitch` — `main.py:54,50`), invoked with
9 and 7 positional/keyword arguments respectively, every one of them an
algorithm-specific tuning knob pulled straight out of the flat `config`
module. To try pYIN, CREPE-lite, or a Basic-Pitch-style neural polyphonic
tracker today, you'd edit `analysis_loop()` in place — change which
function is called, work out which of `config.YIN_*`/`config.CHORD_*`
constants even apply to the new algorithm (most wouldn't:
`harmonic_tolerance_cents` and `min_mag_ratio` are meaningless for a
neural model that outputs note probabilities directly), and there is no
way to keep both implementations selectable side by side without an
if/else inside the hop loop itself. `multipitch.select_window()`
(`main.py:373-375`, `multipitch.py:105-128`) adds a second axis of
algorithm-specific coupling: it's YIN/spectral-peak-picking-specific
window-size logic (issue #63's fix for two close low fundamentals'
mainlobes merging) invoked unconditionally in the same function, with no
way for an alternative backend to opt out of paying for a second ring
buffer it doesn't need, or opt into its own different pre-processing.

There is also no `DetectionBackend`-shaped *type* anywhere in the
codebase to target — `pitch_detect.py` and `multipitch.py` are modules of
free functions, not classes implementing a shared protocol, so "the
interface a pluggable backend must satisfy" exists only implicitly, as
"whatever `analysis_loop()` happens to call positionally today."

### 1.2 Adding a 5th display backend

Four `run_*` functions live in `main.py`: `run_terminal_fill` (673-722),
`run_terminal_wheel` (725-781), `run_terminal_tab` (834-1074, the largest
by far — rhythm notation, freeze, scrollback, non-causal reanalysis all
live here), and `run_gui` (1077-1145). `run_session()` (`main.py:1215-1237`)
dispatches to one of them via a plain `if/elif` chain keyed on a string
literal (`"gui"`, `"wheel"`, `"tab"`, else fill).

**What's already de-duplicated (this is worth crediting, not just
critiquing):** the truly cross-cutting keypress handlers —
`_handle_sensitivity_key`, `_handle_source_key`, `_handle_chord_mode_key`,
`_handle_help_legend_key`, `_handle_back_to_menu_key`
(`main.py:244-534`) — are already standalone, shared, unit-testable
functions called identically from every terminal `run_*` loop. That's the
right level of factoring for logic that's genuinely identical everywhere.

**What isn't de-duplicated:** the outer loop skeleton itself —
`RawKeys()` construction, the `try: while True: ... time.sleep(dt) except
KeyboardInterrupt: return "quit" finally: keys.restore(); display.quit()`
shape — is hand-copied into all three terminal `run_*` functions (fill:
687-722, wheel: 741-781, tab: 891-1074). Each display module's own
`render()` has a genuinely different signature reflecting genuinely
different content — `TerminalDisplay.render(rgb, status, legend)`
(`terminal_display.py:16`), `WheelDisplay.render(active_index, pulse,
status, legend)` (`terminal_wheel_display.py:31`), and
`TabDisplay.render(status, chord_mode=..., notehead_style=..., legend_on=...,
frozen=..., help_legend=..., scroll_offset=...)`
(`terminal_tab_display.py:404`) — there is no common `Display` protocol
today, and (see 3.3) there probably *shouldn't* be one covering `render()`
itself, given how different tab's state really is from fill's.

Concretely, adding a 5th terminal view today means: write a new
`run_terminal_X` function (copy-pasting the loop skeleton from an existing
one, as `tab` clearly did from `fill`/`wheel`'s shape), add a branch to
`run_session()`'s if/elif, add a subparser in `virtualnote.build_parser()`
(`virtualnote.py:66-109`), and add an entry to `menu_display.py`'s `TOOLS`
list (referenced in CLAUDE.md's Files table, not modified here). Four
touch points for one new view, three of which are boilerplate wiring
rather than anything specific to the new view's actual content.

### 1.3 Config surface growth

`config.py` is 302 lines, comment-delimited into 13 unrelated domains:
audio capture, YIN pitch detection, note smoothing, chord mode, color
mapping, animation, display/terminal, tab/staff view, config-store
keybind defaults, credits/donation, menu animation, rhythm/onset/
duration/tempo, and score-writer (`config.py:3,9,41,54,92,108,113,125,
182,196,206,241,296` mark each section). The module docstring — and
CLAUDE.md's own Files table entry ("All tunable constants... in one
place. Check here first.") — treats this flatness as a *feature*: one
`Ctrl-F` finds any constant, no import-path guessing.

Two things make this genuinely harder to grow further, though not yet
unmanageable:

- **It's not just flat, it's also the *only* place most of these
  constants live.** `config_store.py`'s additive TOML overlay
  (`config_store.py:1-50`'s docstring) only covers three tables —
  `[keybinds]` (5 named actions), `[colors]` (hue-by-note-name), and
  `[preferences]` (a free-form bucket, of which only `menu_perf_mode`,
  `rhythm_reanalysis_window_seconds`, and `tab_scrollback_seconds` are
  actually read anywhere). That's roughly 8 of `config.py`'s ~100
  constants that are live-overridable; the other ~90 (every `YIN_*`,
  every `CHORD_*`, `DURATION_DECAY_RATIO`, `TEMPO_*`, animation timings,
  `TAB_COLUMN_WIDTH*`, `FADE_COLUMNS`...) are compile-time-only, editable
  exclusively by hand-editing `config.py` and restarting. This is a
  reasonable, deliberate split (per docs/DECISIONS.md's #41 entry:
  "`[preferences]` is a deliberately generic bucket... #41 only owns the
  load/persist mechanics, not any particular preference's UI or
  behavior") — but it means any future config-organization change has to
  preserve *two* tiers (hard constants vs. `config_store`-backed live
  overrides), not just tidy up one flat namespace.
- **Every new feature ticket has appended its own section rather than
  reusing an existing one** — rhythm/tempo (#55/#77), menu animation
  (#42/#51), score writer (#65) each got a fresh `# --- Foo ---` block.
  At 302 lines this is still skimmable; it is the kind of growth pattern
  that becomes a problem in the 600-1000+ line range, not before.

## 2. What NOT to adopt, and why

The task brief asked me to specifically weigh hexagonal/ports-and-adapters,
DI, and event-driven/pub-sub against the current design. Two of the three
are, on the evidence, not worth adopting here — said plainly, not buried:

**Full hexagonal/ports-and-adapters (a `domain/`, `ports/`, `adapters/`
directory layout with the pipeline as "core" and everything else as
driven/driving adapters).** This pattern earns its keep when an
application has many external actors (multiple UIs, multiple persistence
backends, a real "business core" that outlives any one of them) and a
team that needs the seams enforced structurally. note-color has one core
loop (capture → analysis → render), a fixed and small set of adapters
(one mic API, N terminal renderers + one GUI, one optional TOML config
file), and one maintainer. Reorganizing the whole tree into hexagonal
layers would multiply the number of files and import indirections for
every existing piece of logic, for a benefit (formal enforcement of a
boundary this codebase already respects informally and consistently —
`pitch_detect.py`/`multipitch.py`/`chroma.py` already don't import
`main.py` or any display module) that's mostly about *preventing*
violations a single disciplined maintainer working alone isn't
structurally prone to making in the first place. The narrower
`DetectionBackend`/display-table proposals below get the concrete,
actionable slice of this idea (a real interface at the one seam that
actually needs to flex) without the wholesale reorg.

**A dependency-injection framework** (e.g. a container library). The
codebase already does the *idea* of DI by hand and has for a while:
`SessionState` (`main.py:1148-1212`) bundles `capture`/`sensitivity`/
`source_state`/`reanalysis_buffer` and gets passed into `run_session()`
and every `run_terminal_*` function as a parameter — nothing reaches for
a global. `Sensitivity`/`SourceState`/`ReanalysisState` are all plain
objects constructed once and threaded through, not module-level globals.
This is "manual constructor injection," and it's the right amount of DI
for this codebase's size — a framework would add a dependency, a
learning curve, and a layer of indirection (container wiring, lifecycle
scopes) to formalize something four lines of Python already do clearly.
The `DetectionBackend` proposal below is itself just one more instance of
this same existing convention (construct the backend object once, pass
it in) — no new pattern, no new library.

**Event-driven pub-sub in place of `queue.Queue`.** The current design's
queue choices are not generic "get data from A to B" plumbing that
happens to use `queue.Queue` — each one is a deliberately different
policy, chosen and documented against this app's actual real-time
constraints: `AudioCapture`'s raw-block queue is bounded and
drop-oldest (`audio_capture.py:56-66`, callback thread must never block);
the analysis→render handoff is a single-slot always-overwritten queue
(`main._overwrite()`, `main.py:464-472`, "latest wins, no backlog");
issue #77's `R`-key recompute deliberately runs on a *throwaway thread*
reading a plain snapshot rather than through a request/response queue
into the analysis thread, for reasons argued explicitly in
`docs/DECISIONS.md`/CLAUDE.md's Key design decisions ("the analysis
thread's own per-hop cadence must never stall on a recompute that can
take up to ~1.3s"). A generic pub-sub/event-bus layer would either (a)
have to expose exactly this same per-topic configurability (bounded vs.
single-slot vs. "just read a snapshot, no queue at all"), at which point
it's `queue.Queue` with an extra dependency and a translation layer, or
(b) flatten these three deliberately-different policies into one generic
"publish an event, subscribers get notified" model and lose the
distinctions that make each queue correct for its own boundary. This
app's actual topology is also fixed and small (exactly one producer and
one consumer at each of the three thread boundaries) — the "N producers,
M unknown subscribers" scenario pub-sub is built for doesn't exist here.
Not recommended.

## 3. Concrete staged proposals

### 3.1 `DetectionBackend` protocol (small-medium)

Introduce two `typing.Protocol`s — not a full plugin framework, not
`setuptools` entry points (see §5 for why entry-point-style discovery is
the wrong tool at this scale; per Python's own packaging guide, "start
simple with interface-based plugins if you control all the plugin code,"
which is exactly this repo's situation) — capturing the two detection
entry points `analysis_loop()` actually calls:

```python
# detection_backends.py (new file)
class MonoPitchBackend(Protocol):
    def detect(self, ring: np.ndarray, spectrum: np.ndarray, sample_rate: int) -> tuple[Optional[float], float]:
        """-> (freq_hz or None, confidence 0..1). Mirrors pitch_detect.detect_pitch()'s return shape."""

class PolyphonicBackend(Protocol):
    def detect(self, window: np.ndarray, sample_rate: int) -> list[multipitch.NoteCandidate]:
        """Mirrors multipitch.detect()'s return shape -- NoteSmoother/DurationTracker/
        ChordSmoother already only depend on this shape, not on how it was produced."""
```

Wrap today's implementations as adapter classes that take their
algorithm-specific config in `__init__`, not per-call — this is the part
that actually buys pluggability, since it moves "which config constants
this algorithm needs" out of `analysis_loop()` and into the backend
object itself:

```python
class YinBackend:
    def __init__(self, fmin, fmax, threshold, subharmonic_max_multiple, subharmonic_margin, subharmonic_skip_cmnd):
        ...  # store config.FMIN etc., captured once at construction
    def detect(self, ring, spectrum, sample_rate):
        return pitch_detect.detect_pitch(ring, sample_rate, spectrum, self.fmin, self.fmax, ...)

class SpectralPeakBackend:
    def __init__(self, max_notes, min_mag_ratio, harmonic_tolerance_cents, ...):
        ...
    def detect(self, window, sample_rate):
        return multipitch.detect(window, sample_rate, max_notes=self.max_notes, ...)
```

`SessionState.__init__` (`main.py:1166`) gains two optional constructor
params, `pitch_backend=None`/`poly_backend=None`, defaulting to
`YinBackend(...)`/`SpectralPeakBackend(...)` built from `config.*` exactly
as today — so nothing about default behavior changes. `analysis_loop()`'s
signature gains `pitch_backend, poly_backend` params and its two call
sites (`main.py:329-332`, `376-386`) become `pitch_backend.detect(ring,
spectrum, config.SAMPLE_RATE)` / `poly_backend.detect(multipitch_window,
config.SAMPLE_RATE)`. `multipitch.select_window()`'s bass-gated
long-window logic (issue #63) is spectral-peak-picking-specific — leave
it as a `SpectralPeakBackend`-only concern (called from inside that
backend's own `detect()`, or left in `analysis_loop()` gated on
`isinstance`/a `backend.needs_long_window` flag if a future neural
backend genuinely has no equivalent need) rather than trying to
generalize it prematurely for an algorithm that doesn't exist in this
codebase yet.

**Files touched:** new `detection_backends.py` (~60-80 lines, all of it
adapter classes with no new logic — `pitch_detect.py`/`multipitch.py`
themselves don't change); `main.py` — `analysis_loop()`'s signature and
its two call sites, `SessionState.__init__`. No change to
`NoteSmoother`/`ChordSmoother`/`DurationTracker`/any display module/config
schema. **Effort: 1-2 focused sessions.** **Payoff:** this is the one
change the sibling algorithm-research docs actually need to land any of
their findings without a second surgery through `analysis_loop()` — right
now, trying e.g. pYIN would mean editing this same 160-line function by
hand, with the risk of breaking the chord/rhythm pipeline that shares
it. **Risk:** low. It's a pure extraction with defaults reproducing
current behavior exactly; the main way to get it wrong is over-generalizing
the Protocol to anticipate needs no real second backend has yet (e.g. adding
a `confidence_threshold` param to the Protocol itself because YIN happens
to have one) — keep the Protocol to exactly the two shapes
`NoteSmoother`/`multipitch`-consumers already require, nothing algorithm-specific.

### 3.2 Config package split, config_store-compatible (medium, but see roadmap for timing)

Mechanism: turn `config.py` into a `config/` package whose `__init__.py`
re-exports every name from per-domain submodules, so **every existing
importer keeps working unchanged**:

```
config/
  __init__.py       # from .audio import *; from .pitch import *; from .chord import *; ...
  audio.py          # SAMPLE_RATE, BLOCK_SIZE, WINDOW_SIZE, QUEUE_SIZE
  pitch.py          # FMIN, FMAX, YIN_*
  chord.py          # CHORD_*, MULTIPITCH_*
  color.py          # HUE_OFFSET_DEG, MIN/MAX_OCTAVE, BASE_*, DIM_LIGHTNESS, IDLE_RGB, DEFAULT_COLOR_SCHEME
  animation.py       # CROSSFADE_TAU_MS, PULSE_DECAY_MS, ONSET_PULSE_BOOST
  display.py         # WINDOW_SIZE_PX, FPS, TERMINAL_FPS, WHEEL_FPS, ESCAPE_SEQUENCE_TIMEOUT
  tab.py             # TAB_*, DEFAULT_SCROLL_MODE, FADE_COLUMNS
  keybinds.py        # DEFAULT_KEYBINDS
  credits.py         # AUTHOR_NAME, DONATION_*
  menu.py            # MENU_*
  rhythm.py          # ONSET_FLUX_THRESHOLD, DURATION_DECAY_RATIO, TEMPO_*, RHYTHM_REANALYSIS_WINDOW_SECONDS, DEFAULT_TIME_SIGNATURE, TAB_BARLINE_WIDTH
  score.py           # KEY_GUESS_CONFIDENCE_THRESHOLD
```

Because `config/__init__.py` re-exports everything at the top level,
every existing call site — `config.SAMPLE_RATE` in `main.py`,
`config.DEFAULT_KEYBINDS[action]` in `config_store.py:103`, `config.FMIN`
passed into `detect_pitch()`, all ~150 other references across the
codebase — needs **zero changes**. `config_store.py` needs no changes
either: it only ever reads `config.DEFAULT_KEYBINDS`, and that's still
`config.DEFAULT_KEYBINDS` after the split, just physically sourced from
`config/keybinds.py`. This is the "config_store-compatible transition
path" the split needs to have, and it's mechanical, not a redesign of the
override layering itself.

**Files touched:** `config.py` deleted, ~12 new small files created
(mostly cut-and-paste of existing constants + their existing comments,
verbatim), `config/__init__.py` new (~15 import lines). No other file
changes required, though a search for `import config` vs. `from config
import X` patterns should confirm nothing does a wildcard/`dir(config)`
introspection that would behave differently against a package
(a quick grep found none — every call site is `config.NAME` or a
specific `from config import NAME`). **Effort: half a day**, almost all
of it mechanical relocation, plus a full test-suite run to catch any
missed re-export. **Payoff, honestly assessed:** real but *not yet
large* — see the roadmap below for why this is staged as "do when it
actually hurts," not "do now."

### 3.3 Display dispatch as a table, not a shared `Display` Protocol (small)

Given §1.2's finding that the shared *handler* logic is already factored
out and only the outer loop-skeleton + `run_session()`'s if/elif +
`virtualnote.py`'s subparser list are duplicated/scattered, the
proportionate fix is narrower than a full `DisplayBackend` class
hierarchy: replace the string-keyed if/elif chain with one dict, built
once, that both `run_session()` and `virtualnote.build_parser()` read
from instead of hand-writing each branch:

```python
# main.py, near the run_* functions
VIEWS = {
    "fill":  {"run": run_terminal_fill,  "help": "full-terminal color fill"},
    "wheel": {"run": run_terminal_wheel, "help": "circle-of-fifths ring diagram", "aliases": ["circle"]},
    "tab":   {"run": run_terminal_tab,   "help": "scrolling grand-staff note history", "extra_args": _tab_subparser_args},
    "gui":   {"run": run_gui,            "help": "native pygame color window", "extra_args": _gui_subparser_args},
}
```

`run_session()` becomes `VIEWS[view]["run"](...)` (with each `run_*`
function's differing parameter list still handled by keeping their
current, different signatures — this table is about *dispatch*, not
about forcing a uniform call shape onto four functions whose real
argument needs differ, which is exactly the over-abstraction risk to
avoid here). Adding a 5th view becomes "write `run_terminal_foo` +
add one `VIEWS` entry" instead of touching `run_session()`'s body,
`virtualnote.py`'s subparser construction, and `menu_display.py`'s
`TOOLS` list as three separate hand-edits (though `menu_display.TOOLS`
would still need to iterate this same table rather than maintain its own
literal list, a small follow-on edit to that module).

**Deliberately not proposed:** a common `render()` Protocol across
`TerminalDisplay`/`WheelDisplay`/`TabDisplay`/`Display`. Their `render()`
signatures differ because their actual state differs by a wide margin —
`TabDisplay.render()` takes 7 parameters covering rhythm notation,
freeze-frame, scrollback, and legend toggles that have no fill/wheel
equivalent at all (`terminal_tab_display.py:404`); forcing a shared
signature would mean either a bloated Protocol most views ignore most of,
or a `**kwargs`/`RenderContext` object that just re-hides the same
differences one level down. This would be the highest-risk item in this
whole document if pushed further than the dispatch-table level — see the
roadmap's explicit downside callout.

**Effort: 1 session.** **Payoff:** removes the "touch 3 files" tax on
adding a display; keeps CLAUDE.md's Files table and the menu's tool list
as one source of truth instead of two. **Risk:** low, as scoped above;
the risk is entirely in scope creep toward a full shared-render Protocol,
which this proposal explicitly declines to do.

## 4. Prioritized roadmap

| # | Item | Effort | Payoff | Downside / over-engineering risk |
|---|---|---|---|---|
| 1 | **`DetectionBackend` protocol** (§3.1) | 1-2 sessions | High, and time-sensitive: it's the direct prerequisite for the sibling algorithm-evaluation docs to land any finding without hand-surgery on `analysis_loop()`. Zero behavior change on its own. | Low, if kept to exactly the two shapes already implied by `NoteSmoother`/multipitch consumers. Risk grows only if the Protocol is padded with speculative parameters for algorithms not yet chosen — resist that until a second real backend exists to design against. |
| 2 | **Display dispatch table** (§3.3) | 1 session | Medium — real, immediate reduction in "touch N files to add a view," at very low cost. | Low as scoped (dispatch only). Would become the roadmap's biggest risk if scope-crept into a shared `render()` Protocol across 4 structurally different views — don't. |
| 3 | **Config package split** (§3.2) | ~half a day | Medium *eventually*, low *right now*. At 302 lines with clear `# ---` section headers, `config.py` is still genuinely one-`Ctrl-F`-away navigable — CLAUDE.md itself calls this out as a feature ("Check here first"). Splitting into 12 files trades that for "which of 12 files is `FMIN` in," a real regression in solo-maintainer ergonomics unless the file has actually grown past the point a flat file stays skimmable. | **This is the roadmap's clearest over-engineering trap.** Recommend: don't do this now. Revisit once `config.py` crosses roughly 500-600 lines or a 4th unrelated feature domain gets added on top of today's 13 — at that point the split pays for itself; today it mostly adds import-path indirection for a problem that hasn't arrived yet. |
| 4 | **`config_store` schema widened to cover more of `config.py`** (not detailed above — flagged here since it's adjacent) | Small per-constant, but compounding | Low priority unless a concrete constant becomes something the user wants to tune live without restarting (a real, recurring request would justify it). | Widening `[preferences]`'s live-overridable surface constant-by-constant, on demand, is strictly better than pre-emptively exposing all ~90 hand-edit-only constants through the settings screen "just in case" — most of them (YIN subharmonic margins, harmonic tolerance cents) are exactly the kind of empirically-calibrated, footgun-prone values `docs/DECISIONS.md` spent real investigation tuning; exposing them for casual live editing risks silently reintroducing bugs those investigations fixed. |
| 5 | **Full hexagonal/ports-and-adapters reorg** | Large (multi-week, touches nearly every file) | Speculative — the boundaries this pattern would formalize are already respected informally (detection modules don't import display modules, etc.), so the marginal benefit over the narrower proposals above is small. | **Explicitly not recommended for this project.** This is the single clearest instance in this whole report of a pattern that would make the codebase harder for a solo hobbyist to keep hacking on, for a payoff (structural enforcement of boundaries) that a one-person project doesn't need enforced structurally — self-discipline plus the existing import-graph convention already does the job. |
| 6 | **Event-bus/pub-sub replacing `queue.Queue`** | Large, and would touch `docs/DECISIONS.md`-documented, empirically-justified threading choices | None identified — see §2. | **Not recommended.** Each current queue's policy (bounded/drop-oldest, single-slot/overwrite, throwaway-thread-plus-snapshot) is a deliberate answer to a specific real-time constraint; genericizing them loses the distinction that makes each one correct. |
| 7 | **DI framework/container library** | Medium to adopt, ongoing tax to maintain | None identified — see §2; manual constructor injection (already this codebase's convention via `SessionState`) already gets the benefit. | **Not recommended.** Would add a dependency and indirection to formalize something 4 lines of plain Python already do clearly. |

**Suggested order:** #1 first (unblocks the sibling algorithm research,
which is presumably the actual near-term motivation for this whole
inquiry), #2 next (cheap, real, no dependency on #1), #3 deferred until
its stated trigger, #4 done opportunistically/on-demand rather than as a
project, #5-7 not done at all absent a concrete, currently-nonexistent
need (e.g. #5 might become worth reconsidering only if this ever stopped
being a single-maintainer hobby project — worth flagging, not worth
planning for).

## 5. Packaging & distribution

**Current state:** no `pyproject.toml`/`setup.py`/`setup.cfg` anywhere in
the repo. Dependencies live in a flat `requirements.txt`
(`numpy>=1.24`, `sounddevice>=0.4.6`, `pygame-ce>=2.4.1`, `blessed>=1.20`,
`wcwidth>=0.2`, `librosa>=0.10`). `virtualnote` on `PATH` is a
hand-written 4-line bash shim (`~/.local/bin/virtualnote`) that hardcodes
the absolute paths to the repo's `.venv/bin/python` and `virtualnote.py`
— not a console-script entry point, just a wrapper invoking a script by
absolute path. One small, concrete gap found in the process: `score_writer.py`
imports `music21` (`score_writer.py:3-5`, and referenced in
`main.py:1329-1333`'s local-import comment) but `music21` is **not**
listed in `requirements.txt` at all — an undeclared dependency for the
opt-in `--write-score` feature (issue #65). Worth a one-line fix
(`music21>=...` added to `requirements.txt`, or split into a
`requirements-optional.txt`/extras group alongside `librosa`, which is
at least commented as batch-only) regardless of anything else in this
report.

*(Status update: this "current state" is as of the commit this doc was
written against — `pyproject.toml` now exists, see the Status update
block at the top of this file.)*

**Is a real `pyproject.toml` + `pip install -e .` warranted? Yes, this
one's a genuinely low-cost, real win, unlike most of §4's larger items:**
a minimal `pyproject.toml` (build-backend `setuptools` or `hatchling`,
`[project.scripts] virtualnote = "virtualnote:main"`) replaces the
hand-maintained bash shim with a standard, portable entry point, makes
`pip install -e .` work for local development (no more remembering which
absolute paths the shim hardcodes), and is the natural place to declare
the `music21` gap away and split `librosa`/`music21` into an optional
`[project.optional-dependencies] batch = [...]` extra, since neither is
needed for the live/Pi-constrained path (both already documented as
opt-in-feature-only in CLAUDE.md/module docstrings). This is maybe a
1-2 hour change, self-contained, and doesn't interact with anything in
§§1-4.

**Is PyPI publishing or a prebuilt Raspberry Pi image warranted? No, not
yet, and it's worth saying why explicitly rather than leaving it
unaddressed:** this is a personal project with one user. Publishing to
PyPI buys discoverability and `pip install note-color`-from-anywhere for
an audience that doesn't currently exist, at the ongoing cost of version
bookkeeping, a release process, and namespace/naming considerations
(`note-color` as a PyPI name may well be taken or ambiguous) — pure
overhead against today's actual use case, which is "clone the repo on
whatever machine, `pip install -e .`, run `virtualnote`." A prebuilt Pi
image (a `.img` with the venv, dependencies, and a systemd unit baked in)
is an even larger, more speculative investment — it would freeze a
dependency snapshot the project's own `requirements.txt` still actively
moves against (numpy/sounddevice/pygame-ce version floors, not pins), and
this project doesn't yet have the kind of "flash and forget" deployment
story (no auto-start service, no headless-boot-to-running-view mode
documented anywhere in CLAUDE.md) that a prebuilt image would actually
serve. If a second real Pi ever enters the picture (per CLAUDE.md's own
target-hardware framing, a genuine goal, just not yet an *acted-on* one
beyond "test on it"), a documented `pyproject.toml`-based install plus a
short "install on a fresh Pi" runbook section in CLAUDE.md would cover
that need at a fraction of an image's cost — revisit the image question
only if that runbook itself becomes a repeated point of friction.

## 6. Summary

The codebase's real weak point for pluggability is narrow and specific:
`main.analysis_loop()` calls two detection functions directly by name
with algorithm-specific arguments threaded straight from a flat config
module, and `run_session()`/`virtualnote.py` hand-wire each display view
into three separate places. Both are fixable with small, low-risk,
mechanical extractions (§3.1, §3.3) that don't touch the parts of this
codebase that are already well-factored (the smoothing/debounce layer,
the shared keypress handlers, the additive TOML config-override
mechanism) or the parts that are already correct on their own terms and
well-documented as deliberate (the queue topology, the thread model). The
config-file-split and the larger architectural patterns considered in
this report (hexagonal reorg, event bus, DI container) are, on the
evidence actually read, either premature (config split — real payoff,
wrong time) or not warranted at all for a single-maintainer hobby project
at this scale (the other three) — recommending them anyway would trade
this project's actual, working "keep hacking on this himself" property
for structure that serves a team or a plugin ecosystem this project
doesn't have and, per its own stated goals, isn't trying to become.

*(§3.1 and §5 of this doc are now implemented — see the Status update
block at the top of this file. §3.3 remains open/actionable as written.)*
