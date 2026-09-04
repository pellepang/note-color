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
KITTY_NEGOTIATION_TIMEOUT = 0.25  # seconds RawKeys waits for a terminal to answer the kitty
                                  # keyboard-protocol probe (map #99, ticket #118) before assuming no
                                  # support. Generous next to a local pty round trip (sub-millisecond)
                                  # but still a blink to a human, and paid once per view that opts in
                                  # -- never process-wide. A terminal that answers *anything* settles
                                  # the question long before this expires, via the DA1 sentinel in
                                  # kitty_keys.PROBE_SEQUENCE; this only bounds a pty with nothing on
                                  # the far end at all

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
    # Score editor (issue #98) -- main editor view + Chord builder screen,
    # `virtualnote edit <path>`. Defaults per #98's spec; none collide
    # with '|'/'h' (settings_display.is_valid_remap_key already enforces
    # this generically for a remap, same as every existing keybind
    # above). transpose_up/transpose_down were here too (default '+'/'-')
    # until a post-#98 hands-on-feedback follow-up replaced them with
    # hardcoded Shift+Up/Shift+Down -- see main.py's resolve_editor_action()
    # and docs/DECISIONS.md; score_properties_exit was here until that same
    # follow-up retired the separate Score properties screen it belonged
    # to in favor of an inline header editor (score_properties, 't',
    # still opens it -- there's just no separate screen left to exit from).
    "note_toggle": " ",
    "duration_shorten": ",",
    "duration_lengthen": ".",
    "clear_to_rest": "r",
    "insert_column": "i",
    "delete_column": "x",
    "undo": "u",
    "redo": "U",
    "zoom_cycle": "z",
    "chords_only_toggle": "c",
    "chord_builder_exit": "b",
    "save": "w",
    "score_properties": "t",
    # Score editor audition/piano/playback (map #99, ticket #120,
    # decision #108). All four default to a *Shift*ed letter, because
    # plain-letter space in the editor is nearly exhausted and piano mode
    # now claims a two-octave block of it (`zsxdcvgbhnjm`/`q2w3er5t6y7u`
    # -- see score_audition.py). They are matched exact-case by
    # main._EDITOR_CASE_SENSITIVE_ACTIONS for the same reason undo/redo
    # are: 'm' is a note (B) in piano mode while 'M' is the metronome, so
    # a case-insensitive match would make the two indistinguishable.
    # mark_range_start/mark_range_end aren't repeated here -- the editor's
    # loop region deliberately reuses the tab view's existing '['/']'
    # binding above rather than inventing a second mark vocabulary (#108).
    "piano_mode": "P",
    "play_from_cursor": "L",
    "metronome_toggle": "M",
    "audition_toggle": "A",
}

# --- Score editor audition/playback (map #99, ticket #120) ---
# The octave the piano keyboard's lower row (`z` = C) sounds in; the upper
# row is one octave above. 3 puts the two octaves either side of middle C,
# i.e. centred on the grand staff. Shift+Up/Shift+Down moves it live.
EDITOR_PIANO_BASE_OCTAVE = 3
EDITOR_AUDITION_VELOCITY = 0.8
# The metronome is synthesised through the same SoundEngine as everything
# else (there is no click sample in this repo): a short high tone, a fifth
# higher on the downbeat so bar one is audibly distinct. MIDI pitches --
# 96 is C7, 89 is F6.
EDITOR_METRONOME_PITCH = 89
EDITOR_METRONOME_DOWNBEAT_PITCH = 96
EDITOR_METRONOME_VELOCITY = 0.5
EDITOR_METRONOME_CLICK_SECONDS = 0.05

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

# --- Score editor (issue #98, score_editor_state.EditHistory) ---
EDITOR_UNDO_MAX_DEPTH = 50  # how many EditHistory.record() snapshots are retained before the oldest
                             # is dropped -- plain EditorScore dataclass snapshots are cheap (no music21
                             # graph involved, see score_editor_state.py), so this is a generous, not a
                             # tightly-tuned, bound; per #88's "multi-level, bounded" call.

# --- Playback synthesis (map #24, decision #32: oscillator+ADSR synth) ---
PLAYBACK_SAMPLE_RATE = 44100     # independent of the live pipeline's SAMPLE_RATE (22050) -- playback is
                                  # output-only, no FFT-window/latency-budget constraint drives this choice,
                                  # so it defaults to a standard audio-output rate instead of piggybacking
                                  # on a value picked for a different purpose.
PLAYBACK_BLOCK_SIZE = 512        # OutputStream callback block size -- 512/44100 =~ 11.6ms upper bound on
                                  # note_on()-to-audible latency (sound_engine.SoundEngine). Kept at 512
                                  # deliberately: prototype #100 measured PipeWire reporting an identical
                                  # 34.8ms stream latency at 128/256/512, so a smaller block buys no latency
                                  # at all and only tightens the callback's deadline.
PLAYBACK_ATTACK_SECONDS = 0.01
PLAYBACK_DECAY_SECONDS = 0.08
PLAYBACK_SUSTAIN_LEVEL = 0.65    # 0..1, fraction of peak amplitude held during a note's sustain segment
PLAYBACK_RELEASE_SECONDS = 0.15  # also the length of the audible tail appended after a note's own
                                  # detected/notated duration, so it fades out rather than cutting off
PLAYBACK_HARMONIC_WEIGHTS = (1.0, 0.4, 0.15)  # fundamental + 2nd + 3rd partial weights (descending) --
                                                # a small fixed harmonic stack instead of a bare sine, per
                                                # issue #28's "a few adjustable waveforms is the ceiling
                                                # without real modelling work" framing. Picked by ear, not
                                                # measured -- revisit freely, not a tuned/load-bearing constant.

# --- tab-view frozen playback (map #99, ticket #121, decision #109) ---
TAB_PLAYBACK_DEFAULT_BPM = 90.0   # tempo used to turn a note's duration_class back into real seconds when
                                   # no live/reanalysis bpm estimate is available (silence, or too little
                                   # material to lock a tempo). Same value score_editor_state.
                                   # new_blank_score() defaults a new score's tempo to -- a plain, neutral
                                   # walking tempo, not a measurement.
TAB_PLAYBACK_MIN_NOTE_SECONDS = 0.05   # floor on one played note's length, so a thirtysecond at a fast
                                        # estimated tempo is still audible rather than a click
TAB_PLAYBACK_MAX_NOTE_SECONDS = 8.0    # ceiling, so an absurd (very low) bpm estimate can't leave a note
                                        # ringing for most of a minute
TAB_PLAYBACK_VELOCITY = 0.8       # fixed velocity for every played-back note: nothing in this app's
                                   # detection pipeline produces a real per-note velocity yet (rms is a
                                   # whole-frame measure, not a note's own attack strength), so inventing
                                   # a dynamic from it would be dressing up data that was never captured.

# --- Sound engine (map #99, decision #105; figures measured by prototype #100) ---
POLYPHONY_STANDALONE = 40        # hard voice cap when nothing else in the process is doing heavy work (the
                                  # standalone synth tool, the score editor, `virtualnote replay`). #100
                                  # measured 40 voices sustained with zero driver xruns inside a real
                                  # sounddevice callback, 48 marginal, 64 failing -- a measured ceiling, not
                                  # a guessed one. Overridable live via [preferences].polyphony_standalone
                                  # (Settings screen): the binding constraint is what else the process is
                                  # doing, which no startup hardware probe can see (#100).
POLYPHONY_WITH_DETECTION = 24    # the same cap while this app's live detection is running: one thread doing
                                  # the real 2048-point FFT at ~86 hops/s costs ~1.3ms/block mean and ~7ms
                                  # p99 of the callback's budget (GIL contention, not CPU headroom -- #100),
                                  # dropping the safe figure by nearly half. Overridable via
                                  # [preferences].polyphony_with_detection.

# --- Sampler engine (map #99, build ticket #116) ---
SAMPLER_ATTACK_SECONDS = 0.002   # click-suppression fade-in at the very start of a sample. Not a musical
                                  # envelope (a sample already carries its own attack -- that is the point of
                                  # sampling); this exists only so a recording that happens to start on a
                                  # non-zero sample doesn't begin with a step discontinuity. ~2ms is below
                                  # the ~10ms where a fade starts being heard as a fade rather than as a
                                  # clean start.
SAMPLER_RELEASE_SECONDS = 0.08   # fade-out on note-off, applied ONLY to a looping zone -- a one-shot (a
                                  # drum) ignores note-off and plays to its natural end, which is what makes
                                  # a pad work with a fixed-duration key tap (see sampler.py).
SAMPLER_CHOKE_SECONDS = 0.006    # fade-out when a zone is cut by another in the same choke_group (closed
                                  # hi-hat over open). Deliberately much shorter than the release: a choke
                                  # must read as a cutoff, not as a fade, while still being long enough to
                                  # avoid the click of dropping a waveform mid-cycle to zero.

# --- Effects chain (map #99, ticket #114, implementing research #104; effects.py) ---
# Delay. Defaults are pedalboard/JUCE's own (#104 §2): a quarter-second slap with a
# third of it fed back, mixed 30% wet. Range ceilings bound the circular buffer.
EFFECT_DELAY_TIME_SECONDS = 0.25
EFFECT_DELAY_MIN_SECONDS = 0.001
EFFECT_DELAY_MAX_SECONDS = 2.0
EFFECT_DELAY_FEEDBACK = 0.35
EFFECT_DELAY_MAX_FEEDBACK = 0.95  # |g| < 1 is the stability condition; 0.95 =~ 90 repeats to -60dB,
                                   # already "forever" musically, so nothing is lost by stopping short of 1
EFFECT_DELAY_MIX = 0.3
EFFECT_DELAY_DAMPING = 0.0        # feedback-path high-end rolloff, 0..1 -- OFF by default: #104 measured
                                   # nothing about damping (it named a one-pole as *convention*), so this is
                                   # a by-ear knob in PLAYBACK_HARMONIC_WEIGHTS' provisional spirit, not tuned
# Chorus. Ranges from juce::dsp::Chorus (#104 §3); defaults =~ +-22 cents peak detune.
EFFECT_CHORUS_RATE_HZ = 1.0
EFFECT_CHORUS_MAX_RATE_HZ = 100.0
EFFECT_CHORUS_DEPTH_MS = 2.0
EFFECT_CHORUS_CENTRE_DELAY_MS = 7.0   # "around 7-8 ms" is the classic chorus; lower + feedback = flanger
EFFECT_CHORUS_MIN_DELAY_MS = 1.0
EFFECT_CHORUS_MAX_DELAY_MS = 100.0    # JUCE's own delay-line ceiling; the buffer is ~17KB at 44100Hz
EFFECT_CHORUS_MIX = 0.5
EFFECT_CHORUS_FEEDBACK = 0.0          # -1..1 in JUCE (negative is a real variant); clamped to
                                       # +-EFFECT_DELAY_MAX_FEEDBACK here for the same stability reason
EFFECT_CHORUS_VOICES = 3              # taps at spread LFO phases sharing one buffer; #104 measured 3 voices
                                       # at 1.6% of the block budget, i.e. nearly free
EFFECT_CHORUS_MAX_VOICES = 8
# Offline-render tail (effects.tail_seconds): how long a delay's repeats are allowed to ring past
# the last note before a pre-rendered buffer is cut, bounded so a near-1.0 feedback can't ask
# for minutes of silence-plus-echo.
EFFECT_TAIL_FLOOR = 0.001             # -60dB: a repeat this quiet counts as gone
EFFECT_MAX_TAIL_SECONDS = 10.0

# --- SF2 soundfont engine (map #99, ticket #117; figures measured by research #102) ---
SF2_GAIN = 0.2                   # FluidSynth's `synth.gain` -- its own default, and what #102 measured at.
                                  # sound_engine's callback tanh-soft-clips the mix, so a hot bank can't
                                  # hard-clip; raise this only if a bank is consistently too quiet.
SF2_POLYPHONY = 64               # FluidSynth's *internal* `synth.polyphony` cap. Counts FluidSynth VOICES,
                                  # not notes: a stereo or layered preset spends 2+ per key (#102). This is
                                  # deliberately NOT the voice manager's POLYPHONY_* note budget above --
                                  # both apply at once, neither knows about the other (see sf2_playback.py).
                                  # 64 is the figure #102's 0.951 ms/block headroom was measured at.
SF2_REVERB = True                # FluidSynth's built-in reverb/chorus, on by default -- #102's headroom
SF2_CHORUS = True                #   figure was measured with both ON, so leaving them on costs nothing
                                  #   the budget hasn't already absorbed. Off = a few percent of CPU back.
SF2_RELEASE_TAIL_SECONDS = 1.0   # how long a released SF2Voice keeps its voice-manager slot: FluidSynth
                                  # exposes no per-note "finished" signal, so the slot is reclaimed on a
                                  # timer. Long enough for a typical piano/string release to have decayed
                                  # while another note is still likely sounding; the shared block is only
                                  # cut early when this was the LAST live voice (see sf2_playback.py).

# --- Subtractive synth (map #99, research #103, decision #111, build ticket #113: synth_engine.py) ---
SYNTH_CONTROL_SUB_BLOCK = 64     # samples per control sub-block: the filter's coefficients (filter envelope,
                                  # LFO, key tracking, velocity) are recomputed once per sub-block and held
                                  # fixed across it, since scipy.signal.lfilter's contract is constant
                                  # coefficients per call. #103's measured price knee: 1.24ms/block at 16
                                  # voices (10.7% of the 11.61ms budget) vs 2.32ms at 32 samples; matches
                                  # FluidSynth's own FLUID_BUFSIZE = 64. Amp envelope is audio-rate regardless.
SYNTH_TABLE_SIZE = 4096          # samples per wavetable band. Read cost is a gather, independent of size;
                                  # size only bounds how many partials a band can hold before linear
                                  # interpolation error dominates (capped at SYNTH_TABLE_SIZE // 4 partials,
                                  # i.e. >= 4 table samples per cycle of the highest partial), so 4096 keeps
                                  # even the lowest MIDI octave's band full out to ~16.7kHz.
SYNTH_MIP_BANDS = 12             # one band-limited table per octave from MIDI note 0 (8.18Hz) up; 12 bands
                                  # reach 33.5kHz, past MIDI 127 (12.5kHz) with room for a pitch LFO on top.
SYNTH_CUTOFF_MIN_HZ = 20.0       # filter cutoff floor after modulation (the patch field's own minimum)
SYNTH_DAMPING_MAX = 1.4142       # SVF damping k = 1/Q at resonance 0: sqrt(2), i.e. Butterworth -- the
                                  # flattest passband, no peak, which is what "no resonance" should mean.
SYNTH_DAMPING_MIN = 0.1          # k at resonance 1.0: Q = 10, a +20dB peak at cutoff. Self-oscillation
                                  # (k -> 0) is deliberately unreachable: a linear SVF at k = 0 is a
                                  # marginally-stable oscillator with unbounded output, not a musical one.
SYNTH_FILTER_ENV_OCTAVES = 6.0   # cutoff swing (octaves) at filter.env_amount = +/-1.0 and envelope 1.0
SYNTH_LFO_FILTER_OCTAVES = 3.0   # cutoff swing (octaves) at lfo.depth 1.0 with destination "filter"
SYNTH_LFO_PITCH_SEMITONES = 2.0  # vibrato swing (semitones, +/-) at lfo.depth 1.0 with destination "pitch"
SYNTH_VELOCITY_FILTER_OCTAVES = 4.0  # how far below the patch cutoff velocity 0 lands at velocity_to_filter 1.0
SYNTH_PINK_GAIN = 11.5           # make-up gain after the 1/f pinking filter so pink and white noise sit at
                                  # the same RMS (measured 0.087x on uniform white noise, see synth_engine.py)
                                  # -- the `colour` knob changes spectrum, not loudness.

# --- Synth tool (map #99, ticket #119, decision #107) ---
# The standalone `synth` menu tool: which key plays what, how a parameter
# sweeps, and how the input layer is lit. Provisional by-ear values in the
# same spirit as PLAYBACK_HARMONIC_WEIGHTS -- none is load-bearing.
SYNTH_BASE_OCTAVE = 3            # octave of the *lower* row of the two-octave layout (z = C3), so the two
                                  # built-in keyboard octaves span C3..B4 -- centred on middle C, the register
                                  # a bare QWERTY keyboard is most often played in.
SYNTH_OCTAVE_SHIFT_MAX = 3       # how far Shift+Up/Shift+Down can transpose the note keys, +/- octaves. Bounded
                                  # so a shift can never walk the whole layout off the bottom or top of MIDI.
SYNTH_FIXED_NOTE_SECONDS = 0.35  # note length on a terminal with no key-release reporting (decision #107 point
                                  # 7). Long enough to hear a filter envelope open, short enough that a fast
                                  # passage doesn't smear -- and the status line says plainly that this is what
                                  # is happening, rather than leaving "why won't notes sustain?" a mystery.
SYNTH_PARAM_COARSE_STEPS = 10    # Shift+Left/Right multiplier: one coarse press == ten ordinary ones. The
                                  # `Shift`-is-the-escape-hatch rule (#107 point 5) applied to a sweep that
                                  # would otherwise take a hundred presses to cross the filter's range.
SYNTH_PARAM_CUTOFF_RATIO = 1.059463094359295  # 2**(1/12): one Left/Right press moves the cutoff a semitone,
                                  # so a filter sweep is the same musical distance per press everywhere in
                                  # the 20Hz-20kHz range instead of crawling low and leaping high.
SYNTH_PARAM_LOG_FLOOR = 0.001    # smallest value a log-scaled parameter steps *up* to from its minimum -- a
                                  # ratio step can never leave zero on its own, so the first press has to jump.
POLYPHONY_SYNTH_DUAL = 28        # voice cap while the synth tool's layout 2 has a kit and a synth patch playable
                                  # at once (#107's implementation note). Lower than POLYPHONY_STANDALONE
                                  # because the same budget is now shared by two engines whose per-voice costs
                                  # differ, and a drum hit arriving to find every slot held by sustained synth
                                  # notes is the audible failure this margin buys off.
SYNTH_KEY_DIM_LIGHTNESS = 0.20   # lightness of an idle key in the input layer -- the same "visible but plainly
                                  # off" floor DIM_LIGHTNESS gives the wheel view's inactive wedges.
SYNTH_KEY_LIT_LIGHTNESS = 0.62   # lightness of a key while its note is sounding.
SYNTH_KEY_TAU_MS = 45            # ColorAnimator crossfade for one key. Much faster than the fill view's, since
                                  # a key press is a discrete event to be seen landing, not a colour to dwell in.
SYNTH_KEY_PULSE_DECAY_MS = 220
SYNTH_KEY_PULSE_BOOST = 0.5

# --- Synth recording + quantized import (map #99, ticket #122, decision #110) ---
PLAYED_NOTE_REFERENCE_BPM = 90.0  # the tempo `session_recorder.py` snaps a *played* note's measured
                                  # `duration_seconds` against to fill the log's `duration_class` field. A synth
                                  # performance has no tempo estimate at all -- nothing in the synth tool tracks a
                                  # beat -- but `duration_class` is what `virtualnote replay` draws its duration
                                  # glyphs from, so writing every played note as the same DEFAULT_DURATION_CLASS
                                  # would make a fast passage replay as a row of identical quarter notes. The raw
                                  # `duration_seconds` is written unrounded alongside it, so this derived field can
                                  # always be recomputed against a different tempo later (which is exactly what
                                  # log_import.py does on the way into the editor). Matches
                                  # score_editor_state.DEFAULT_TEMPO_BPM, so an imported score's own default tempo
                                  # and the glyphs a replay drew agree by construction.
IMPORT_DEFAULT_GRID = "sixteenth"  # log_import.py's starting quantization grid (decision #110 point 3: capture raw,
                                  # quantize at import, with a selectable grid). A sixteenth is fine enough to keep
                                  # ordinary played rhythm intact and coarse enough that small human timing spread
                                  # doesn't fragment a phrase into unreadable dotted values.
