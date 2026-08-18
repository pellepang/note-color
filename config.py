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
RMS_SILENCE_THRESHOLD = 0.01
CONFIDENCE_THRESHOLD = 0.5
MEDIAN_WINDOW = 5
DEBOUNCE_HOPS = 3
SILENCE_HOPS = 3
ONSET_RMS_JUMP_DB = 6.0

# --- Color mapping ---
HUE_OFFSET_DEG = 0
MIN_OCTAVE = 2
MAX_OCTAVE = 6
BASE_LIGHTNESS_RANGE = (0.18, 0.82)
BASE_SATURATION = 0.75
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
TAB_NOTE_LIGHTNESS = 0.5        # fixed, octave-independent -- 0.5 is where a given
                                 # hue/saturation looks most vivid; BASE_LIGHTNESS_RANGE's
                                 # top end (used by fill/GUI) is much closer to white
TAB_FPS = 20
TAB_FIX_HOPS_PER_SEC = 4        # 'fix' mode: new column every 0.25s
TAB_COLUMN_WIDTH = 3            # terminal characters per history column/glyph cell
TAB_VISIBLE_MAXLEN = 300        # on-screen deque cap
TAB_SESSION_HISTORY_MAX = 5000  # cap on entries retained for the end-of-session dump
