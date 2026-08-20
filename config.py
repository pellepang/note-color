"""All tunable constants for the note-color pipeline in one place."""

# --- Audio capture ---
SAMPLE_RATE = 22050
BLOCK_SIZE = 512          # ~23ms hop at 22050Hz
WINDOW_SIZE = 2048        # ~93ms analysis window for YIN
QUEUE_SIZE = 8            # bounded raw-block queue (drop-oldest on full)

# --- Pitch detection (YIN) ---
FMIN = 65.0               # ~C2
FMAX = 1000.0             # ~B5
YIN_THRESHOLD = 0.12

# --- Note smoothing ---
# RMS_SILENCE_THRESHOLD and CONFIDENCE_THRESHOLD are the base values at
# sensitivity=1.0. Both scale down (more permissive) as sensitivity rises;
# see NoteSmoother.set_sensitivity(). Adjustable at launch via --sensitivity
# and live via the [ / ] hotkeys in every display mode.
RMS_SILENCE_THRESHOLD = 0.01
CONFIDENCE_THRESHOLD = 0.5
DEFAULT_SENSITIVITY = 1.0
MEDIAN_WINDOW = 5
DEBOUNCE_HOPS = 3
SILENCE_HOPS = 3
ONSET_RMS_JUMP_DB = 6.0

# --- Chord mode (chroma-vector chord recognition) ---
# All provisional pending empirical tuning during real playing, same
# spirit as MEDIAN_WINDOW/DEBOUNCE_HOPS above.
CHORD_MATCH_THRESHOLD = 0.80     # cosine similarity; below this, no-match
CHORD_MEDIAN_WINDOW = 5          # rolling-average window on chroma vectors, pre-match
CHORD_DEBOUNCE_HOPS = 3          # consecutive-candidate hops before the displayed chord name changes
NOTE_STACK_ATTACK_HOPS = 2       # consecutive detections before a note-stack entry turns on
NOTE_STACK_RELEASE_HOPS = 4      # consecutive misses before a note-stack entry turns off
CHORD_MAX_NOTES = 6              # cap on simultaneously-detected notes
CHORD_PEAK_MIN_MAG_RATIO = 0.05  # spectral peak-picking: ignore peaks below this fraction of the strongest peak
CHORD_HARMONIC_TOLERANCE_CENTS = 35.0   # spectral peak-picking: harmonic-consistency pruning window
CHORD_MAX_PEAK_CANDIDATES = 20   # spectral peak-picking: cap on candidates considered before pruning

# --- Color mapping ---
HUE_OFFSET_DEG = 0
MIN_OCTAVE = 2
MAX_OCTAVE = 6
BASE_LIGHTNESS_RANGE = (0.18, 0.82)
BASE_SATURATION = 0.75
# Shared "dim/inactive" lightness floor -- originally terminal_wheel_display.py's
# own module constant (inactive pitch-class wedges), promoted here so
# terminal_tab_display.py's per-column age-based fade (issue #22) can reuse
# the exact same floor without a second copy the two views could drift
# apart from (same rationale as the NOTE_NAMES_FIFTHS/diatonic_step() fix --
# see docs/DECISIONS.md).
DIM_LIGHTNESS = 0.16
IDLE_RGB = (6, 6, 12)
DEFAULT_COLOR_SCHEME = "chromatic"  # or "fifths"

# --- Animation ---
CROSSFADE_TAU_MS = 100
PULSE_DECAY_MS = 200
ONSET_PULSE_BOOST = 0.15

# --- Display ---
WINDOW_SIZE_PX = (800, 600)
FPS = 30
TERMINAL_FPS = 20
WHEEL_FPS = 12

# --- Tab / staff view ---
DEFAULT_SCROLL_MODE = "onset"  # or "fix"
TAB_DEFAULT_NOTEHEAD_STYLE = "symbol"  # or "name" -- live-togglable with N; symbol
                                        # picked as more visually distinctive (issue #13/#21)
TAB_DEFAULT_LEGEND_ON = True           # live-togglable with L
TAB_NOTE_LIGHTNESS = 0.5        # fixed, octave-independent -- 0.5 is where a given
                                 # hue/saturation looks most vivid; BASE_LIGHTNESS_RANGE's
                                 # top end (used by fill/GUI) is much closer to white
TAB_FPS = 20
TAB_FIX_HOPS_PER_SEC = 4        # 'fix' mode: new column every 0.25s
TAB_COLUMN_WIDTH = 3            # terminal characters per history column/glyph cell
TAB_COLUMN_WIDTH_CHORD = 9       # chord mode: wide enough for names like "C#13b9/F#"
TAB_CLEF_WIDTH = 3               # left-hand legend sub-column: clef glyph on its anchor
                                 # row, blank elsewhere (issue #36: its own column, not
                                 # merged with the letter column)
TAB_LETTER_WIDTH = 2            # right-hand legend sub-column: staff-row note letter,
                                 # one per row (every line AND space, issue #36)
TAB_LEGEND_WIDTH = TAB_CLEF_WIDTH + TAB_LETTER_WIDTH  # total width the L toggle
                                                        # reserves from/returns to note columns
TAB_VISIBLE_MAXLEN = 300        # on-screen deque cap
TAB_SESSION_HISTORY_MAX = 5000  # cap on entries retained for the end-of-session dump
FADE_COLUMNS = 16               # issue #22: columns of age to fade a scrolled-past column
                                 # linearly from TAB_NOTE_LIGHTNESS down to DIM_LIGHTNESS,
                                 # held at that floor beyond this age. Reached via three
                                 # rounds of live user reaction (4 -> 8 -> 16) -- not a
                                 # value to second-guess without new live feedback.

# --- Config store (issue #41) ---
# Defaults for every terminal-mode hotkey, keyed by action name -- the
# source of truth `config_store.ConfigStore.keybind()` falls back to when
# a user's config.toml doesn't remap that action. Single characters,
# case-insensitive, except FREEZE_TOGGLE which is literally Space.
DEFAULT_KEYBINDS = {
    "source_toggle": "m",
    "chord_mode_toggle": "p",
    "notehead_style_toggle": "n",
    "legend_toggle": "l",
    "freeze_toggle": " ",
}

# --- Credits & donation (issue #44) ---
# Author callout shown on the menu screen itself and, in full, on the
# separate Credits screen (credits_display.py). DONATION_URL is a
# placeholder -- #44's standing decision was to ship the screen now rather
# than block on a Patreon account existing; swapping in the real URL once
# one does is a one-line fill-in, not a reason this was withheld.
AUTHOR_NAME = "Pelle"
DONATION_PLATFORM = "Patreon"
DONATION_URL = "https://patreon.com/notecolor"

# --- Menu animation (issues #42/#51) ---
# Spinning ASCII donut re-skinned with the circle-of-fifths palette, on
# virtualnote's bare menu screen. Full mode's spacings/fps are the "show
# off the app" default (#39); perf mode (half raster + coarser sampling +
# no letters + half fps) is the auto-detected fallback for weaker
# hardware -- see menu_animation.py.
MENU_DONUT_FULL_THETA_SPACING = 0.07
MENU_DONUT_FULL_PHI_SPACING = 0.02
MENU_DONUT_PERF_THETA_SPACING = 0.10
MENU_DONUT_PERF_PHI_SPACING = 0.03
MENU_DONUT_PERF_BLOCK_WIDTH = 2   # perf mode: each computed cell prints this many terminal columns wide
MENU_DONUT_SPIN_A_STEP = 0.04     # per-frame rotation increment, x-axis
MENU_DONUT_SPIN_B_STEP = 0.02     # per-frame rotation increment, z-axis
MENU_FPS_FULL = 30
MENU_FPS_PERF = 15
# Right-hand text overlay pane (title/donation/tool list/hints/status)
# beside the donut -- issues #43/#44 added Settings/Credits/the donation
# callout after #42's design was decided, so the animated screen needs a
# legible home for all of it, not just the bare tool list #42 prototyped
# against. Below MENU_MIN_DONUT_COLS of leftover width, the donut is
# dropped entirely (text pane re-centers) rather than squeezed unreadably
# small.
MENU_TEXT_PANE_WIDTH = 46
MENU_MIN_DONUT_COLS = 30
# Auto-detect heuristic (issue #46's decision): a core-count floor skips
# the startup probe outright on weak hardware; otherwise a few real,
# off-screen render_frame() calls at the terminal's actual size are timed
# against full mode's own frame budget. "auto" is overridable via
# config.toml's [preferences].menu_perf_mode ("auto"/"full"/"perf") or
# virtualnote's --menu-perf-mode flag, per #42's "config/CLI override for
# cases the heuristic gets wrong."
MENU_AUTODETECT_CPU_FLOOR = 2
MENU_AUTODETECT_PROBE_FRAMES = 3
MENU_AUTODETECT_FRAME_BUDGET = 1.0 / MENU_FPS_FULL
