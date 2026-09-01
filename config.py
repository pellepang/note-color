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

# Issue #69: octave-doubling correction. In the low register a note's
# fundamental can be naturally weaker than its overtones, and a strong
# harmonic then wins YIN's ascending-from-tau_min threshold scan before
# the true (longer-period) fundamental is ever reached. After the scan
# finds a candidate, detect_pitch() checks small integer multiples of it
# (candidate positions for the true fundamental) and adopts the deepest
# one that's both still sub-threshold and meaningfully deeper than the
# candidate -- see pitch_detect.detect_pitch()'s docstring/comment and
# docs/DECISIONS.md for the full empirical rationale.
#
# YIN_SUBHARMONIC_MARGIN was originally 0.5 (a multiple needed to be only
# 2x deeper) and a real-mic re-verification round found that far too loose
# -- it re-triggered on already-correct detections whenever ordinary
# broadband low-frequency content (mic self-noise, room rumble, mains hum)
# produced its own coincidentally-deep CMND dip near tau_max (the fmin
# edge), since ANY dip there beat an already-correct-but-noise-degraded
# candidate by more than 2x. Recalibrated to 0.1 (a multiple must now be
# ~10x deeper) against adversarial synthetic data bracketing both failure
# modes: genuine octave-doubling cases clear this with wide margin (ratios
# empirically <=0.08 across a weak-fundamental sweep), while synthesized
# mains-hum/noise false-positive cases never exceeded a floor of ~0.14 --
# see docs/DECISIONS.md's "regression" follow-up entry for the full
# methodology.
YIN_SUBHARMONIC_MAX_MULTIPLE = 4   # matches chroma.HARMONIC_WEIGHTS' harmonics 1-4
YIN_SUBHARMONIC_MARGIN = 0.1       # a multiple must beat the candidate's CMND by at least this factor
YIN_SUBHARMONIC_SKIP_CMND = 0.01   # skip the check when the candidate is already this confident

# --- Note smoothing ---
# RMS_SILENCE_THRESHOLD and CONFIDENCE_THRESHOLD are the base values at
# sensitivity=1.0. Both scale down (more permissive) as sensitivity rises;
# see NoteSmoother.set_sensitivity(). Adjustable at launch via --sensitivity
# and live via the [ / ] hotkeys in every display mode.
RMS_SILENCE_THRESHOLD = 0.005
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
# Issue #68 residual: caps how high a harmonic multiple _is_harmonic_of()
# will check when deciding a candidate peak is "just" an already-accepted
# note's overtone. Matches chroma.py's HARMONIC_WEIGHTS (harmonics 1-4) and
# YIN_SUBHARMONIC_MAX_MULTIPLE above -- the one convention this codebase
# already treats as "the harmonics that matter" for a note's own identity.
# Without a cap, a real, independently-sounding note in a dense chord could
# get pruned just for accidentally landing near a large integer multiple
# (8x, 9x, 12x...) of some other note already accepted that hop -- more
# such multiples exist to accidentally collide as chord density/pitch
# spread grows, which is exactly #68's "recall collapses under density"
# symptom. See docs/DECISIONS.md for the empirical repro.
CHORD_HARMONIC_MAX_NUMBER = 4

# multipitch.detect()'s live window (WINDOW_SIZE, ~93ms) can't resolve
# fundamentals of closely-spaced low notes (e.g. C2+E2, ~17Hz apart) --
# their mainlobes physically overlap and merge into one wrong-frequency
# peak (issue #63). A longer window resolves them correctly (verified:
# 2x WINDOW_SIZE is already enough for ordinary low triads), so
# multipitch.select_window() swaps to MULTIPITCH_LOW_WINDOW_SIZE whenever
# bass_chroma carries real signal (gated by MULTIPITCH_BASS_GATE_RATIO,
# same 0.25 confidence-ratio convention chord_templates.match() already
# uses for slash-chord bass detection) -- paying the extra ~93ms latency
# only for hops that actually have low content, not every hop.
MULTIPITCH_LOW_WINDOW_SIZE = 4096
MULTIPITCH_BASS_GATE_RATIO = 0.25

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
ESCAPE_SEQUENCE_TIMEOUT = 0.05  # seconds RawKeys.poll() waits for an arrow key's ESC [ <letter>
                                # continuation bytes before treating ESC as a lone keypress -- covers
                                # tmux/laggy-pty setups that split the burst across reads. Matches
                                # vim's common ttimeoutlen fix for the same class of problem; only
                                # caps the *worst case* wait -- a genuine arrow burst resolves as soon
                                # as its bytes are ready, almost always well under this

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
TAB_COLUMN_WIDTH_NAME = 9        # mono *name* notehead style (issue #83): wide enough for
                                  # a duration suffix like "Bb·16th."/"C·whole" (up to 8
                                  # cells: 2-char letter + middle dot + 5-char suffix) --
                                  # TAB_COLUMN_WIDTH (3) clips every suffix longer than 1
                                  # char down to an unreadable stub ("C·whole" -> "C·w").
                                  # Same value as TAB_COLUMN_WIDTH_CHORD by coincidence of
                                  # both needing ~9 cells, not because they're the same
                                  # concept -- kept as its own constant so the two can move
                                  # independently later.
TAB_CLEF_WIDTH = 3               # left-hand legend sub-column: clef glyph on its anchor
                                 # row, blank elsewhere (issue #36: its own column, not
                                 # merged with the letter column)
TAB_LETTER_WIDTH = 2            # right-hand legend sub-column: staff-row note letter,
                                 # one per row (every line AND space, issue #36)
TAB_LEGEND_WIDTH = TAB_CLEF_WIDTH + TAB_LETTER_WIDTH  # total width the L toggle
                                                        # reserves from/returns to note columns
TAB_SCROLLBACK_SECONDS = 300.0  # how far back (in the column timestamps pushed via
                                 # push()/push_notes()/push_barline()'s `t=`, not a
                                 # fixed column count) TabDisplay retains history for
                                 # in self.entries -- the live-audio-timed window the
                                 # `R`/Left-Right-arrow scrollback feature scrolls
                                 # within. Overridable per-instance via TabDisplay's
                                 # own `scrollback_seconds=` constructor arg (read from
                                 # config_store.store.preference("tab_scrollback_seconds", ...)
                                 # by main.py's caller); this is only the fallback
                                 # default. Replaces the older, now-removed
                                 # TAB_VISIBLE_MAXLEN count-based cap -- notes arrive at
                                 # irregular, onset-driven intervals (mono) or a fixed
                                 # rate that varies by scroll mode ('fix'), so a count
                                 # never corresponded to a real time window the way this
                                 # constant does.
TAB_SESSION_HISTORY_MAX = 5000  # cap on entries retained for the end-of-session dump
FADE_COLUMNS = 16               # issue #22: columns of age to fade a scrolled-past column
                                 # linearly from TAB_NOTE_LIGHTNESS down to DIM_LIGHTNESS,
                                 # held at that floor beyond this age. Reached via three
                                 # rounds of live user reaction (4 -> 8 -> 16) -- not a
                                 # value to second-guess without new live feedback.
TAB_SCROLLBACK_SECONDS = 300.0   # how far back (in seconds) tab view's freeze-mode
                                 # Left/Right scrollback can browse -- editable live via
                                 # the Settings screen's numeric fields (issue #43),
                                 # default backing config.toml's [preferences]
                                 # "tab_scrollback_seconds" key. Valid range 30-3600
                                 # (1 hour), step 30, enforced by settings_display.py's
                                 # NUMERIC_FIELDS clamp, not by this constant itself.

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
    "rhythm_reanalysis": "r",
    "session_record_toggle": "s",
    "mark_range_start": "[",
    "mark_range_end": "]",
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

# --- Rhythm/onset/duration/tempo detection (issue #55) ---
# All provisional pending empirical tuning against real playing, same
# convention as chord mode's own constants above.
ONSET_FLUX_THRESHOLD = 0.3       # spectral_flux() OR-condition added to NoteSmoother's is_onset test --
                                  # a *relative* threshold (fraction of the previous frame's total
                                  # spectral magnitude, see onset_detect.spectral_flux(), issue #66) --
                                  # empirically, a sustained tone's worst-case hop-to-hop wobble tops
                                  # out around ~0.3 across this app's pitch/amplitude range, while even
                                  # a quiet genuine attack clears ~0.33 and normal playing volume clears
                                  # it several times over. Not the same units as the pre-#66 raw-sum
                                  # value this replaces -- don't compare the two numbers directly.
DURATION_DECAY_RATIO = 0.25      # current/peak magnitude ratio below which a note counts as "off" --
                                  # deliberately reuses chroma.fold_bass()'s bass-confidence ratio
                                  # value for consistency, not because the two thresholds must mean
                                  # the same thing
TEMPO_HISTORY_SECONDS = 8.0      # rolling novelty-history window TempoTracker autocorrelates over
TEMPO_MIN_BPM = 40
TEMPO_MAX_BPM = 240
TEMPO_UPDATE_INTERVAL_HOPS = 20  # ~0.46s at BLOCK_SIZE=512/SAMPLE_RATE=22050 -- re-estimate tempo
                                  # this often rather than every hop, amortizing the autocorrelation cost
# Issue #70: TempoTracker._estimate()'s autocorrelation peak, normalized
# against zero-lag energy (acf[0]) -- how much of the novelty history's
# total energy the best periodic lag actually explains. A real periodic
# passage (e.g. this suite's own isochronous pulse train) measured
# ~0.85-0.90 throughout; once that periodic content scrolls out of the
# rolling TEMPO_HISTORY_SECONDS window and is replaced by non-periodic
# content (isolated single notes at irregular intervals -- no consistent
# beat for autocorrelation to find), confidence collapsed to ~0.09-0.19 and
# the estimate started swinging wildly (99bpm -> 41bpm -> 76bpm -> 49bpm
# across consecutive re-estimates on real recorded audio). Below this
# threshold, TempoTracker holds its last estimate rather than re-locking
# onto what's essentially autocorrelation noise -- see docs/DECISIONS.md
# for the full empirical calibration (clean margin: <=0.19 for genuinely
# non-periodic content, >=0.41 for real periodic content anywhere in the
# tested data).
TEMPO_MIN_CONFIDENCE = 0.3
# Issue #79: TempoTracker._resolve_octave_lock()'s threshold for the
# noise-adjusted "excess" measure of whether acf[2*best_lag] shows real
# alternating structure beyond what a plain, non-alternating periodic
# signal's linear lag-decay would predict (see that function's docstring
# for the derivation). Calibrated on synthetic novelty signals (delta-
# impulse trains, both plain and genuinely alternating at various tempos,
# with additive Gaussian noise at several SNRs): a plain, non-alternating
# signal's adjusted excess stayed <= ~0.05 across every tested tempo
# (90-200bpm) and noise level (sigma 0-0.25 against unit-amplitude
# impulses); a genuinely alternating signal (a real, sounding beat every
# other subdivision, >=30% amplitude difference between the two) measured
# >= ~0.03-0.19 depending on how pronounced the alternation was. 0.08 sits
# with real margin above the plain-signal ceiling while still catching
# clearly alternating structure -- a subtle/mild alternation (a "ghost"
# note only ~10% quieter than the true beat) is deliberately NOT corrected
# (adjusted excess ~-0.06 in that case), since that's musically closer to
# a genuinely ambiguous case than a clear octave-lock error. Only
# validated against synthetic signals so far -- a real-mic/loopback
# re-verification via scripts/rhythm_accuracy_test.py is still pending
# (same provisional posture as issue #69/#71's synthetic-first fixes; see
# docs/DECISIONS.md).
TEMPO_OCTAVE_LOCK_MARGIN = 0.08
RHYTHM_REANALYSIS_WINDOW_SECONDS = 60.0  # how many seconds of recent audio/data the tab
                                  # view's 'R' offline-style rhythm re-analysis reaches
                                  # back over -- editable live via the Settings screen's
                                  # numeric fields (issue #43), default backing
                                  # config.toml's [preferences]
                                  # "rhythm_reanalysis_window_seconds" key. Valid range
                                  # 5-1800 (30 min), step 5, enforced by
                                  # settings_display.py's NUMERIC_FIELDS clamp, not by
                                  # this constant itself.
DEFAULT_TIME_SIGNATURE = (4, 4)  # (numerator, denominator) -- never auto-detected. A tuple, not a
                                  # display string: argparse only re-parses a --time-signature default
                                  # via _parse_time_signature when the default is itself a string, and
                                  # every non-CLI caller (shell.py's menu path, run_session/
                                  # run_terminal_tab's own defaults, batch_transcribe.transcribe's)
                                  # uses this value directly as the pre-validated (int, int) pair the
                                  # rest of the pipeline expects -- see main.py's run_terminal_tab.
TAB_BARLINE_WIDTH = 1             # terminal characters per barline column -- narrower than a note
                                   # column (TAB_COLUMN_WIDTH), so it reads as a divider, not data

# --- Score writer (issue #65, batch-only MusicXML export via music21) ---
KEY_GUESS_CONFIDENCE_THRESHOLD = 0.65  # Krumhansl-Schmuckler correlation (-1..1 range, but a genuine
                                        # tonal recording typically scores 0.6-0.9 against its true key);
                                        # below this, score_writer.guess_key_signature() returns None and
                                        # the written score falls back to C major/no key signature rather
                                        # than a confidently-wrong guess -- provisional starting value,
                                        # same "retune later" convention as CHORD_MATCH_THRESHOLD.
