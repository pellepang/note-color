# note-color

Real-time audio-to-color visualizer. Listens to the microphone, detects the
musical note currently being played, and displays a corresponding color —
fast enough to feel live during actual music.

## Goal / constraints (as given by the user)

- Not a web app.
- Must run across a range of hardware, from small devices (Raspberry Pi
  class) up to full desktops — portability mattered more than raw speed.
- User deferred most technical tradeoffs to "best-performing + easiest to
  build" judgment rather than specifying them.

## Status

Working end-to-end and verified live: unit tests pass (`pytest tests/`,
245 tests), and detection has been confirmed with a real speaker→mic
acoustic round-trip test — both the original monophonic pipeline and
chord mode (see below). Pitch-tracking accuracy on real audio varies
run-to-run with room/mic conditions — inherent to monophonic pitch
tracking, not a bug to chase without a concrete symptom.

Chord mode (chroma-vector chord recognition, toggled via `P` in terminal
views — opt-in/off-by-default in `fill`/`wheel`, opt-out/on-by-default in
`tab`) is implemented per the spec at
[issue #12](https://github.com/pellepang/note-color/issues/12), itself
synthesized from wayfinder map
[#1](https://github.com/pellepang/note-color/issues/1) and its ten
resolved child tickets (#2–#11).

Rhythm/onset/duration/tempo detection (live + batch), and `tab`-view
rhythm notation (duration glyphs, barlines, `tempo=`/`time=` status
fields), is implemented per the spec at
[issue #55](https://github.com/pellepang/note-color/issues/55), itself
synthesized from wayfinder map
[#47](https://github.com/pellepang/note-color/issues/47) and its seven
resolved child tickets (#48–#54). Verified against synthesized
sine-wave test signals (single sustained tone, multi-note melody) through
both the live pipeline's unit tests and an actual `virtualnote transcribe`
run; not yet verified against real (non-synthetic) playing beyond that.

## Architecture

```
mic -> AudioCapture (PortAudio callback thread, never blocks)
    -> bounded queue.Queue (drop-oldest on full)
    -> analysis thread: rolling 2048-sample ring buffer
                         -> pitch_detect.compute_spectrum()      (shared FFT)
                         -> pitch_detect.detect_pitch()          (YIN, monophonic)
                         -> note_smoother.NoteSmoother            (stabilize, onset-gated)
                         -> color_map.note_to_hsl()               (note -> color)
                         -> chroma.fold() / fold_bass()           (chord mode)
                         -> multipitch.detect()                   (chord mode, up to 6 notes)
                         -> chord_smoother.ChordSmoother           (stabilize chord+notes)
                         -> chord_templates.match()                (chord mode, chroma -> name)
                         -> onset_detect.chroma_flux()              (rhythm mode, novelty signal)
                         -> tempo_tracker.TempoTracker               (rhythm mode, live bpm estimate)
                         -> duration_tracker.DurationTracker          (rhythm mode, mono + chord, per-hop)
    -> single-slot queue.Queue (always overwritten with latest RenderItem)
    -> render loop (GUI window, or one of three terminal views, or the menu)

batch_transcribe.transcribe() (issue #55, offline, `virtualnote transcribe`)
    -> the same per-hop pipeline above, driven by array slicing instead of
       AudioCapture, no live queues/threads involved
    -> duration_tracker.DurationTracker.finalize_noncausal() (per note-slot,
       non-causal refinement) + librosa.beat.beat_track() (tempo)
    -> TabDisplay.dump_ansi() (same on-quit dump format the live `tab` view uses)
```

The chord-mode pipeline (chroma/multipitch/chord_smoother/chord_templates)
and the rhythm pipeline (onset_detect/tempo_tracker/duration_tracker) both
always run every hop regardless of whether any view has `P` toggled on or
is even `tab` — cheap enough that gating either wasn't worth the extra
shared state (see Key design decisions). `P` is a pure render-thread-local
flag; rhythm detection has no toggle at all, live or in `tab`'s render.

Three threads, connected by non-blocking queues at every boundary, so no
stage can ever stall another. Target end-to-end latency: comfortably under
150ms.

**Process/session lifecycle (issue #40).** `AudioCapture`, the analysis
thread, `Sensitivity`, and `SourceState` are bundled in `main.SessionState`
and created lazily — on first tool entry, not at process start — via
`SessionState.ensure_started()`, an idempotent call both `main()` (eager,
called once) and `virtualnote`'s menu shell (lazy, called before every tool
entry) can make freely. Once created, a `SessionState` persists for the
rest of the process's life: `virtualnote`'s menu loop (`shell.py`) reuses
the same one across every "pick a tool -> run it -> `|` back to menu ->
pick another tool" round-trip, so switching tools never tears down or
reopens the mic (unlike `M`'s deliberate `AudioCapture.restart()`, a real
source *change*, not a tool switch) — that persistence is what makes `|`
an instant transition rather than a relaunch with startup latency.
`main.run_session(view, ..., session)` is the shared dispatch point both
`main()` (standalone, one-shot) and `shell.run_menu_loop()` (repeated)
call into; each terminal run_* function and `run_gui()` return an explicit
sentinel, `"quit"` or `"menu"`, instead of swallowing `KeyboardInterrupt`
into an implicit `None` as before — see Key design decisions.

## Files

| File | Responsibility |
|---|---|
| `config.py` | All tunable constants (sample rate, buffer sizes, thresholds, color/animation/chord-mode params). Check here first. |
| `audio_capture.py` | `AudioCapture` — `sounddevice.InputStream` callback → bounded drop-oldest queue. `resolve_loopback_device()` finds the system-output monitor for `--source loopback`. |
| `pitch_detect.py` | `compute_spectrum()` — shared FFT reused by YIN, chroma, and (via its own Hann-windowed variant) multipitch. `detect_pitch()` — hand-rolled YIN (pure NumPy, FFT autocorrelation + parabolic interpolation) over a precomputed spectrum. |
| `note_smoother.py` | `NoteSmoother` — silence/confidence gate, median filter, debounce, onset detection (monophonic path): note-change, an RMS jump, or (issue #55) `onset_detect.spectral_flux()` clearing `ONSET_FLUX_THRESHOLD` against the hop-over-hop spectrum. `onset_backdate_hops` (issue #70) — 0 normally, `DEBOUNCE_HOPS - 1` on the exact hop a genuine note-change promotion fires (never on an RMS-jump/flux re-attack of an already-current note) — the fixed, known lock-in delay `main.py` backdates `DurationTracker`'s `onset_hop` by so a note's measured duration isn't systematically shortened by the smoother's own debounce buildup. |
| `chroma.py` | `fold()`/`fold_bass()` — 12-bin chroma vector via a precomputed Gaussian log-frequency weighting matrix summing 1st–4th harmonics per pitch class; `fold_bass()` restricts to <~250Hz for bass/inversion detection. |
| `chord_templates.py` | ~360-template dictionary (30 qualities × 12 roots) + `match()` — cosine-similarity chord recognition, bass-chroma-gated slash/inversion naming and rotational-tie-breaking. |
| `multipitch.py` | `detect()` — spectral peak-picking (own Hann-windowed FFT, not the shared one — see Key design decisions) + harmonic-consistency pruning, up to 6 simultaneous notes with confidence. `select_window()` (issue #63) picks which ring buffer a hop's `detect()` call should analyze — the app's normal live `config.WINDOW_SIZE` window, or a longer `config.MULTIPITCH_LOW_WINDOW_SIZE` one the caller (`main.py`/`batch_transcribe.py`) also maintains, gated on `chroma.fold_bass()` showing real low-frequency content — see Key design decisions for why the short window alone garbles close-together bass notes. |
| `chord_smoother.py` | `ChordSmoother` — mirrors `NoteSmoother`'s shape for chord mode: chroma rolling-average + chord-name debounce, plus asymmetric attack/release hysteresis per note-stack slot. |
| `onset_detect.py` | (issue #55) `spectral_flux()`/`chroma_flux()` — pure, `None`-safe half-wave-rectified positive-magnitude-difference novelty measures between two consecutive `pitch_detect.compute_spectrum()`/`chroma.fold()` frames. `spectral_flux()` feeds `note_smoother.py`'s onset gate; `chroma_flux()` feeds `tempo_tracker.py`. |
| `duration_tracker.py` | (issue #55) `DurationTracker` — mirrors `ChordSmoother.note_states`' dict-of-state shape, but for *measuring* how long a note sounded rather than debouncing its display. `.update()` (live, causal, keyed by `(pitch_class, octave)`, `is_onset`-aware re-attack preemption) and `.finalize_noncausal()` (batch, centered-smoothed envelope, static method) share one off-threshold definition (`DURATION_DECAY_RATIO`). `duration_class_for_beats()`/`DEFAULT_DURATION_CLASS` — nearest-standard-note-value snapping (incl. dotted), used by both live and batch. `require_onset_for_new_note` (constructor, issue #70) — mono's tracker sets this `True` so a key with no existing state only opens one when `is_onset` is genuinely `True` for it, since `NoteSmoother` otherwise echoes a just-finalized note's key with `is_onset=False` for a couple more hops (its own silence grace period) that would otherwise misread as a spurious new note; chord mode keeps the default `False` since it has no reliable per-note onset signal at all and relies on appear/absence alone. `.update()`'s `onset_backdate` parameter (issue #70) backdates a freshly-opened state's `onset_hop`, fed from `NoteSmoother.onset_backdate_hops` for mono. |
| `tempo_tracker.py` | (issue #55) `TempoTracker` — live-only causal BPM estimation via FFT autocorrelation over a rolling `chroma_flux()` novelty-history window (same autocorrelation approach `pitch_detect.py`'s YIN already uses, applied to novelty instead of raw audio); re-estimates every `TEMPO_UPDATE_INTERVAL_HOPS` hops, not every hop. `_estimate()`'s best-lag candidate (issue #70) is gated on a confidence ratio (autocorrelation peak over zero-lag energy, `config.TEMPO_MIN_CONFIDENCE`) — below it, the estimate holds at its last value rather than re-locking onto what's essentially noise once the rolling window's content stops being periodic (e.g. a stretch of isolated, irregularly-spaced notes with no consistent beat). Batch tempo uses `librosa.beat.beat_track()` directly instead (`batch_transcribe.py`) — this module is never imported there. |
| `batch_transcribe.py` | (issue #55) The only module permitted to import `librosa` for *offline* transcription. `load_audio()` + `transcribe()` — runs the same per-hop pipeline `analysis_loop()` drives live (mono via `NoteSmoother`, polyphonic via `multipitch.detect()`+`ChordSmoother`), accumulates full-recording-length per-`(pitch_class, octave)` magnitude/onset arrays, then calls `DurationTracker.finalize_noncausal()` per key and `librosa.beat.beat_track()` for tempo. Returns a `TranscriptionResult` (`notes` polyphonic, `mono_notes` monophonic, `bpm`, `hop_seconds`) that `main.run_batch_transcribe()` turns into `TabDisplay` columns. |
| `rhythm_reanalysis.py` | (issue #77) The other module permitted to import `librosa`, for the `tab` view's live `R`-key non-causal rhythm re-analysis — see Key design decisions for why this is an accepted second exception to "librosa lives only in `batch_transcribe.py`", not a reopening of that rule. `HopRecord` (namedtuple: `hop_index`, `mono`, `chord_notes`, `chroma_novelty`) is the buffered-per-hop shape `main.ReanalysisBuffer` accumulates and `recompute()` (the pure, unit-tested engine) consumes — a snapshot list of these, plus `hop_seconds`/`beats_per_bar`, in; a `RecomputeResult` (`corrected_notes`, `barline_times`, `bpm_estimate`, `window_start_time`, `window_end_time`) out, or `None` if the buffer was empty. Reconstructs per-key magnitude/onset arrays from the flat `HopRecord` sequence mirroring `batch_transcribe.transcribe()`'s own per-hop loop almost exactly, then calls the same `DurationTracker.finalize_noncausal()`/`librosa.beat.beat_track()` batch already uses — the one structural difference is mapping local buffer positions back to real hop timestamps via each `HopRecord`'s own `hop_index`, since a rolling window's contents aren't 0-based/contiguous the way batch's whole-recording arrays are. |
| `color_map.py` | `note_to_hsl()`, `hsl_to_rgb255()`, `fifths_index()`, `hue_for_step()` (the shared 30-degrees-per-step hue formula `note_to_hsl()` and `menu_animation.band_color()` both build on), `NOTE_NAMES`, `NOTE_NAMES_FIFTHS`. |
| `staff_map.py` | `staff_row()`, `ledger_rows()`, `row_note_name()` (general row→letter, every line/space row) — grand-staff placement, used only by `tab` view. |
| `animation.py` | `ColorAnimator` — crossfade + onset pulse. Used by GUI, terminal-fill, and (per-note-keyed) chord-mode fill bands. |
| `display.py` | `Display` — pygame GUI window (fullscreen, debug overlay). Chord mode is out of scope for the GUI (no live-hotkey mechanism). |
| `terminal_display.py` | `TerminalDisplay` — ANSI truecolor full-terminal fill; `render_bands()` for chord mode's proportional per-note bands. |
| `terminal_wheel_display.py` | `WheelDisplay` — 12-note fifths ring, always fifths color regardless of `--color-scheme`; `render_chord()` for chord mode's multi-wedge steady-lit display. |
| `terminal_tab_display.py` | `TabDisplay` — scrolling grand-staff note history rendered as sheet-music noteheads; `push()`/`push_notes()` (each note stored as a mutable dict, not a tuple — a `duration_class` field starts `None` and is filled in later by `finalize_duration()`, since a note's duration is only known after it decays, well after the column carrying it was pushed; optional `t=` override lets `main.run_batch_transcribe()` stamp a column with the recording's real onset time instead of wall-clock), `push_barline()` (issue #55: a second, distinct column type — no notes, just a divider glyph spanning the staff height at `TAB_BARLINE_WIDTH`, aged/dimmed the same way note columns are but with no hue), `render()` (takes live `notehead_style`/`legend_on`/`frozen`/`scroll_offset`, age-fades each column's lightness per issue #22, and composes duration glyphs/suffixes onto each note per its `duration_class`), `dump_ansi()` on quit (always letter+octave, unaffected by any toggle). `self.entries` retains history by timestamp window (`self.scrollback_seconds`, defaulting to `config.TAB_SCROLLBACK_SECONDS`, overridable via the constructor) rather than the old fixed column count, giving the `R`/Left-Right-arrow scrollback feature real reach to scroll within; `render(..., scroll_offset=N)` renders the view as it looked `N` history entries ago, historical age-fade included (freeze's usual pin-to-full-brightness only applies at `scroll_offset=0`). `correct_duration()` retroactively overwrites a specific already-finalized note's `duration_class` (disambiguated from repeat notes at the same key by closest column timestamp — `finalize_duration()` itself can only reach the currently-open note at a key); `erase_barlines()`/`insert_barline()` replace a stale barline set within a time range with recomputed ones (the latter inserts in sorted position rather than just appending). All three are the `TabDisplay`-side API issue #77's `R`-key non-causal rhythm re-analysis calls into (`main.py`'s `_apply_reanalysis_result()`) — this module owns only the data/render capability, not the recompute engine (`rhythm_reanalysis.py`) or keybind wiring (`main.py`). |
| `config_store.py` | `ConfigStore`/module-level `store` — additive TOML overlay over `config.py` from `$XDG_CONFIG_HOME/note-color/config.toml` (fallback `~/.config/note-color/config.toml`); `keybind()`/`note_hue_override()`/`preference()` (mtime-checked hot-reload), `set_preference()`/`set_keybind()`/`set_note_hue_override()` (persist + write back to the TOML file — all three back issue #43's settings screen, `set_preference()`/`preference()` generically covering the numeric `rhythm_reanalysis_window_seconds`/`tab_scrollback_seconds` fields alongside the earlier hand-edit-only `menu_perf_mode`, no bespoke accessor needed for any of the three). |
| `main.py` | Wires threads together; `SessionState` (lazy-created capture/analysis-thread/sensitivity/source bundle) + `run_session()` (dispatch-and-return-sentinel, reusable across tool switches, issue #40) sit alongside the original per-view CLI entry point. `RenderItem` NamedTuple is the render-queue shape — `duration_hops`/`bpm_estimate` (issue #55) are its newest two fields. `run_terminal_tab()` drives rhythm notation: per-hop `finalize_duration()` calls (mono via the previous hop's `pitch_class`/`octave`, chord via each `note_stack` entry's own `duration_hops`) and a beat-accumulator triggering `push_barline()`. `run_batch_transcribe()` (issue #55, `virtualnote transcribe`) never touches `SessionState`/audio at all — offline, one-shot, builds `TabDisplay` columns from `batch_transcribe.transcribe()`'s output and calls `dump_ansi()` directly, no render loop. `pygame` imported only inside `run_gui`; `librosa` never imported here directly (only via `rhythm_reanalysis.py`, see that module and `batch_transcribe.py`). Issue #77 additions: `ReanalysisBuffer` (owned by `SessionState`, appended to every hop by `analysis_loop()` with `rhythm_reanalysis.HopRecord`s, bounded/hot-reloaded against `rhythm_reanalysis_window_seconds`) and `ReanalysisState` (a plain `.in_progress` flag shared between the render thread and the throwaway recompute thread `_handle_reanalysis_key()` spawns on `R`). `run_terminal_tab()` polls a second, local single-slot `reanalysis_result_queue` once per iteration and applies a ready result via `_apply_reanalysis_result()`; `_handle_scroll_keys()` maintains `scroll_offset` (reset to 0 on every unfreeze) fed straight to `TabDisplay.render(scroll_offset=...)`. |
| `menu_display.py` | `MenuDisplay` — `virtualnote`'s tool-picker screen (issue #40); `render()` draws issue #42's decided animated design (built in #51): `menu_animation`'s spinning donut fills a left-hand pane, with the title/donation-callout/tool-list/hints/status text overlaid in a fixed-width right-hand pane (`_layout()`, `_text_lines()`) — narrow terminals drop the donut and fall back to a centered text-only screen, same shape as the original #40 placeholder. `move()`/`move_to()`/`current_view()` selection plumbing is unchanged by any of this. `TOOLS` (the four run_session-launchable views) vs. `MENU_ITEMS` (`TOOLS` plus non-audio screens: `settings`, `credits`) — selection/render operate on `MENU_ITEMS`; `shell.py` special-cases the extra entries instead of sending them through `main.run_session()`. `osc8_link()`/`_donation_line()` (issue #44) build the main screen's clickable author/donation callout. `_resolve_perf_mode()` picks full vs. perf donut rendering: an explicit override (virtualnote's `--menu-perf-mode` flag) beats `config.toml`'s `[preferences].menu_perf_mode` beats `menu_animation.detect_perf_mode()`'s real startup probe. |
| `menu_animation.py` | Animation math for the menu screen's donut (issues #42/#51), ported from the throwaway prototype at `prototype/issue-42-menu-animation/{donut_fifths.py,autodetect.py}`: `render_frame()` — NumPy-vectorized torus point-projection (`_project()`) + a painter's-algorithm z-buffer via ascending-depth-sort fancy-indexing (no per-point Python loop) — re-skinned with the circle-of-fifths palette (`band_color()`/`FIFTHS_LABELS`), full mode shaded/lettered, perf mode flat/letterless/half-raster. `detect_perf_mode()`/`_decide_perf_mode()` — issue #46's auto-detect heuristic (core-count floor, then a real self-timed `render_frame()` probe against the full-mode frame budget), split into a real-timing wrapper and a pure decision function for testability. |
| `settings_display.py` | `run_settings_screen()` — `virtualnote`'s interactive Settings screen (issue #43): edits `config_store`'s keybind remaps, per-note hue overrides, and generic numeric preferences live, using `blessed` for field navigation and "press a key to capture this remap"/"type a clamped number" input (the one deliberate exception to raw-ANSI chrome elsewhere in the shell, per #37/#39). `FIELDS` (three kinds: `"keybind"`/`"color"`/`"numeric"`) / `NUMERIC_FIELDS` (spec list: key, label, min, max, step, default — today covers `rhythm_reanalysis_window_seconds` and `tab_scrollback_seconds`) / `move()` / `keybind_value()`/`color_value()`/`numeric_value()` / `is_valid_remap_key()` / `parse_hue_input()` (wraps modulo 360) / `parse_numeric_input()` (clamps into `[min, max]`, the correct behavior for a bounded quantity unlike hue's circular wrap) / `apply_field_edit()` / `clear_field()` are the pure, unit-tested logic; `run_settings_screen()`'s render/edit-capture loop itself (including `_capture_numeric()`, modeled on `_capture_hue()`) is smoke-tested manually, same convention as every `run_terminal_*` loop. |
| `credits_display.py` | `run_credits_screen()` — `virtualnote`'s static Credits screen (issue #44): author, Claude/AI-assistance credit, and third-party library attribution (`THIRD_PARTY_LIBRARIES`), raw ANSI (no editable state, so no need for `settings_display`'s `blessed` exception). `credits_lines()` is the pure, unit-tested text builder; the render/wait-for-any-keypress loop itself is smoke-tested manually. |
| `shell.py` | `run_menu_loop(session)` — `virtualnote`'s unified in-process orchestrator (issue #40): shows the menu, dispatches a pick to `main.run_session()`, loops back to the menu on a `"menu"` sentinel, exits the process on `"quit"`. `_handle_menu_key()` is the pure keypress-to-selection logic. `"settings"`/`"credits"` picks are special-cased via `_NON_SESSION_SCREENS` straight to `settings_display.run_settings_screen()`/`credits_display.run_credits_screen()` instead of `run_session()` (issues #43, #44) — neither touches audio, so both always return straight back to the menu. |
| `virtualnote.py` | CLI entry point for the unified shell (issue #40): `build_parser()` (bare menu vs. `<view> [flags]`, replicating every flag the retired `colorize` dispatcher forwarded; `--menu-perf-mode {auto,full,perf}`, top-level-only, issue #51's CLI override for the menu donut; `tab`'s `--time-signature` and the standalone `transcribe <file> [--dump-file] [--time-signature]` subcommand, issue #55) + `main()`, which builds one `main.SessionState` and hands off to `shell.run_menu_loop()` or `main.run_session()` directly — except `transcribe`, handled and returned before `SessionState` is even constructed, since batch never touches live audio. |
| `tests/` | `test_pitch_detect.py`, `test_note_smoother.py`, `test_color_map.py`, `test_staff_map.py`, `test_chroma.py`, `test_chord_templates.py`, `test_multipitch.py`, `test_chord_smoother.py`, `test_terminal_tab_display.py`, `test_config_store.py`, `test_shell.py` (the new global key handlers/legend builder, `MenuDisplay` selection state, `shell._handle_menu_key`, `virtualnote.build_parser()` — not the threaded/interactive loops themselves, per this repo's existing test convention), `test_settings_display.py` (field layout/formatting/parsing/edit helpers, each test isolated onto its own `tmp_path` config file via a monkeypatched `settings_display.store` — never the real `~/.config/note-color/config.toml`), `test_credits_display.py` (`credits_lines()` text content), `test_menu_animation.py` (projection/shading helpers, `render_frame()` shape/smoke checks, the auto-detect decision function), `test_menu_display.py` (`_layout()`'s donut/text-pane column split, `_resolve_perf_mode()`'s override precedence, `_text_lines()`'s content), `test_onset_detect.py`/`test_duration_tracker.py`/`test_tempo_tracker.py`/`test_batch_transcribe.py` (issue #55: synthetic spectra/chroma/magnitude-envelope/periodic-impulse fixtures, same "synthesize the signal, no binary fixtures" convention `test_chroma.py`'s `make_tone()` set), `test_rhythm_reanalysis.py` (issue #77: synthesized `HopRecord` sequences exercising `rhythm_reanalysis.recompute()` directly — corrected durations, chord-onset-on-reappearance, tempo recovery from a periodic novelty signal, barline placement, the empty-buffer `None` case — same convention, not `main.py`'s threaded `R`-key wiring itself, which is smoke-tested manually per this repo's existing `run_terminal_*` convention). |

## Running it

```
cd ~/note-color
virtualnote                                            # menu -- pick a tool live
virtualnote fill                                       # straight to terminal fill
virtualnote wheel                                       # straight to terminal circle-of-fifths ring
virtualnote tab onset                                    # straight to scrolling staff, new column per note-attack
virtualnote tab fix                                       # straight to scrolling staff, new column every tick
virtualnote gui                                            # straight to the GUI window
virtualnote fill --color-scheme fifths                      # any tool, fifths hue mapping instead of chromatic
virtualnote fill --source loopback                           # listen to system audio output, not mic
virtualnote tab onset --time-signature 3/4                    # barlines placed for 3/4 instead of the default 4/4
virtualnote transcribe song.wav                                 # offline rhythm/tempo transcription, no live audio
.venv/bin/python -m pytest tests/                              # run the test suite
```

`virtualnote` (on PATH via `~/.local/bin/virtualnote`, added to `~/.zshrc`'s
PATH) is the one entry point for every tool this project offers (issue
#40), retiring the old per-tool `colorize` bash dispatcher. Bare
`virtualnote` opens an animated ANSI menu (`menu_display.py`, issue #42's
design, built in #51) to pick a tool live — a spinning ASCII donut
re-skinned with the circle-of-fifths palette (rim letters in full mode)
beside the tool list: the four audio tools above, a `Settings` entry
(issue #43) for editing keybind remaps and per-note color overrides live
(see the Config file section below), and a `Credits` entry (issue #44)
with full attribution; the menu screen itself also names the author and a
clickable donation link (`config.AUTHOR_NAME`/`DONATION_URL`) right below
the title, regardless of which entry is selected. A performance-mode
fallback (half raster, coarser sampling, no letters, half framerate) is
auto-detected at startup for weaker hardware; `--menu-perf-mode
{auto,full,perf}` overrides it (as does `config.toml`'s
`[preferences].menu_perf_mode`, checked when the flag is omitted). Narrow
terminals drop the donut entirely and show a centered text-only list.
`virtualnote <view> [flags]` goes straight to an audio tool instead,
replicating every flag `colorize` used to forward (`settings`/`credits`
have no direct-launch form, menu-only). Both paths run through the same
long-lived process (`shell.py`), not a relaunch per tool — see
Architecture.
`main.py` itself is still directly runnable exactly as before
(`.venv/bin/python main.py --terminal --view fill`, etc.) for anyone who
wants the original single-tool-per-process entry point; it just has no menu
to fall back to (see below).

The `tab` view writes an ANSI-colored note-history dump to a timestamped
file next to `main.py` on quit (override with `--dump-file PATH`).

GUI controls: `Esc`/close window to quit, `F` fullscreen, `D` debug overlay,
`Up`/`Down` decrease/increase pitch sensitivity. Terminal modes: `Ctrl+C` to
quit, `Up`/`Down` sensitivity, `M` toggle audio source live, `P` toggle
chord mode live (needs a real TTY; no-op otherwise, e.g. piped input) —
`fill`/`wheel` start monophonic and `P` opts up into chord mode, `tab`
starts polyphonic (chord mode on) and `P` opts down to monophonic instead.
`tab` view only: `N` toggle notehead render style live, `L` toggle the
clef+note-letter legend column live, `Space` freeze/un-freeze the view
(see below); while frozen, `R` triggers a non-causal rhythm re-analysis
and `Left`/`Right` scroll back/forward through retained history (issue
#77, see below).
`--sensitivity FLOAT` sets the starting value (default 1.0); raises it to
register quieter/softer playing more readily. Current value shown in the
status line (`sens=`).

**Global across every tool (issue #40), unaffected by `M`/`P`/`N`/`L`/
`Space` above.** `|` is the always-live back-to-menu keybind — from inside
any terminal view, or the GUI (bound to the unshifted backslash key there,
since pygame reports `|` as backslash+shift rather than a keycode of its
own) — instantly returns to `virtualnote`'s menu with the mic/analysis
thread/sensitivity/source state all still running, no relaunch. Run via
plain `main.py` instead of `virtualnote` (no menu exists there), `|` just
quits cleanly, same as Ctrl+C. `H` toggles a context-sensitive keybind-
legend line shown below the status line (on by default); off, a one-word
`legend(h)`/`helplegend(h)` hint stays in the status line itself so the
toggle stays discoverable either way. Both are session-local state, same
as `P`/`N`/`L`/`Space` — no persistence across runs (that's issue #41's
`[preferences]` table, not yet wired up for `H`).

`P` toggles chord mode (chroma-vector chord recognition, up to 6
simultaneous notes) in any terminal view — GUI-out-of-scope. `fill`/`wheel`
start monophonic (off by default) and `P` opts *up* into chord mode; `tab`
starts polyphonic (chord mode **on** by default) and `P` opts *down* to
monophonic instead — same key, same plain boolean flip, just a different
starting value for `tab` (issue #13's standing decision: the sheet-notation
view is chord-first). Status line swaps `note=`/`freq=`/`conf=`/`rms=` for
one `chord=<name>` field; `sens=`/`src=` unaffected. Per view: `fill`
splits into proportional horizontal bands, one per active note, pitch-sorted
low-to-high bottom-to-top; `wheel` steadily lights active wedges in their
own colors (no pulsing) with the bass wedge bracketed; `tab` stacks up to 6
notes in one scrolling column with the chord name in a header row above it
(shown whenever polyphonic, i.e. by default), and `--scroll onset` advances
on chord-identity change rather than per-note re-attack. Chord names use
jazz symbol notation (`Δ7`, `-7`, `°7`, `ø7`, `+`, ASCII `#`/`b`) with this
project's flat-biased root spelling, and render blank rather than a guess
when nothing in the ~360-template dictionary clears the match threshold.

The `tab` view renders real sheet-music noteheads instead of colored
letter-in-cell blocks (issue #13). `N` toggles between the two live
render styles: *symbol* (default) — an open notehead glyph (U+1D157) with
a real Unicode ♭/♯ accidental marker next to it if needed, no letter or
octave text — and *name* — bare letter + ASCII accidental, no octave
digit (e.g. `Bb`, `F#`; the note's staff row already conveys octave).
Both use `NOTE_NAMES_FIFTHS` spelling and this app's existing per-note HSL
coloring, unaffected by the toggle. Toggling `N` restyles columns already
scrolled onto the screen, not just future ones. `L` toggles the left
legend area (two side-by-side sub-columns, `TAB_LEGEND_WIDTH` wide
together — a narrower clef-glyph column, blank except on its anchor row,
then a letter column labeling every staff row, line AND space alike,
octave-digit-free) on/off live as one unit, reclaiming its full width for
note columns when off. Current state of both shown in the status line
(`notes=`/`legend=`). The on-quit `dump_ansi()` text dump is unaffected by
either toggle — always letter+octave, as before.

Past columns dim as they scroll by (issue #22): the newest visible column
renders at the normal `TAB_NOTE_LIGHTNESS`; every older column's lightness
fades linearly down to `DIM_LIGHTNESS` (the same floor `wheel`'s inactive
wedges use) over `FADE_COLUMNS` (16) columns of age, then holds at that
floor. Hue/saturation are untouched — only lightness moves. `Space`
freezes the view (issue #23): scrolling and the dimming fade both pause,
every currently-visible column jumps to full `TAB_NOTE_LIGHTNESS`
(overriding the fade as if each were the newest column), and the
underlying audio/detection pipeline keeps running in the background
regardless — pressing `Space` again resumes live immediately, with no
catch-up of whatever happened while frozen. Current state shown in the
status line (`frozen=on/off`).

**Rhythm notation (issue #55), `tab`-view-only, no toggle — always on
once a note has a measured duration.** Each note's duration glyph
(*symbol* style: a combining stem, plus a flag per subdivision below a
quarter note and a dot if dotted; *name* style: a short text suffix like
`Bb·8th` instead) reflects however long that note actually sounded,
snapped to the nearest standard note value — computed once its duration
finalizes (necessarily after the column was already pushed, possibly
already scrolled partway off screen) and then fixed for good, unlike
color's continuous per-frame age-fade. Barlines (a distinct, narrower
column type, no notehead) appear at estimated bar boundaries, driven by a
running beat-accumulator against the live tempo estimate and
`--time-signature N/D` (default `4/4`, plain-digit display only — never
auto-detected). Both the tempo estimate and the time signature show in
the status line (`tempo=<bpm|-->`, `time=N/D`); barline placement is an
approximation tied to that live tempo estimate, not exact bar-for-bar
accuracy — expected drift, not a bug (see Known limitations).
`virtualnote transcribe <file> [--dump-file PATH] [--time-signature N/D]`
runs the same rhythm/duration/tempo detection offline against a
pre-recorded audio file instead of live input — no terminal window, no
mic, just a `dump_ansi()`-format text dump written on completion (same
convention/default path as `tab`'s own on-quit dump).

**`R`-key non-causal rhythm re-analysis + Left/Right scrollback (issue
#77), `tab`-view-only, freeze-mode-only.** While frozen (`Space`), `R`
(remappable, `[keybinds].rhythm_reanalysis`, default `"r"`) re-runs the
same non-causal machinery `virtualnote transcribe` already uses offline
(`duration_tracker.DurationTracker.finalize_noncausal()` +
`librosa.beat.beat_track()`) against a rolling live buffer of the last
`rhythm_reanalysis_window_seconds` (Settings-screen numeric field,
default 60s) of already-computed per-hop data — never raw audio, and
never a redo of pitch/chord detection itself, only the rhythm layer on
top of it. On press, a throwaway thread snapshots the buffer and
recomputes off both the render and analysis threads (`rhythm=
recomputing...` shown in the status line meanwhile); once done, already-
displayed duration glyphs are corrected in place
(`TabDisplay.correct_duration()`) and the barline set within the
recomputed window is replaced (`erase_barlines()`/`insert_barline()`) —
the corrected tempo estimate also takes over the `tempo=` status field
until the view is next unfrozen. Independently, `Left`/`Right` scroll
back/forward through `TabDisplay`'s retained history
(`tab_scrollback_seconds`, default 300s) via `render(scroll_offset=N)`;
current offset shown in the status line (`scrollback=-N`) when nonzero.
Both reset the instant `Space` un-freezes (no catch-up of anything that
happened while frozen, same convention freeze itself already follows).
See Key design decisions for the threading approach and Known
limitations for what's out of scope.

`--source {mic,loopback}` (default `mic`) selects the input: `loopback`
listens to the computer's own audio output instead of the microphone, via
the PipeWire/PulseAudio monitor of the default sink (Linux only — errors
out clearly on other platforms). Useful for testing without playing sound
out loud; confirmed to keep working even while the output sink is muted.
In any terminal mode, `M` toggles live between `mic` and `loopback` without
restarting the process (`AudioCapture.restart()` tears down and reopens the
PortAudio stream on the same queue, so the analysis thread is undisturbed
apart from a ~100ms gap during the switch); current source shown in the
status line (`src=`), with a failed switch (e.g. `pactl` unavailable)
reported inline there instead of crashing.

### Config file

An optional TOML file at `$XDG_CONFIG_HOME/note-color/config.toml`
(falling back to `~/.config/note-color/config.toml`) additively overrides
`config.py`'s defaults — absent, empty, or malformed reproduces today's
exact behavior. Covers three things today, all hot-reloaded live (edit the
file while the app is running, no restart needed):

- `[keybinds]` — remap any of the six terminal hotkeys (`source_toggle`,
  `chord_mode_toggle`, `notehead_style_toggle`, `legend_toggle`,
  `freeze_toggle`, `rhythm_reanalysis`) to a different single character,
  e.g. `source_toggle = "x"`. `rhythm_reanalysis` (default `"r"`) is the
  tab view's freeze-mode-only non-causal rhythm re-analysis trigger (issue
  #77). The status line's hotkey hints (`(m)`, `(p)`, etc.) reflect the
  remap. Editable live from the menu's Settings screen (below), or by
  hand.
- `[colors]` — override a note's hue (degrees, 0–360) by name, either
  sharp or flat spelling, e.g. `C = 200` or `"F#" = 45`. Saturation and
  octave-driven lightness are untouched by the override. Also editable
  from the Settings screen.
- `[preferences]` — free-form quality-of-life settings; `menu_perf_mode`
  (hand-edit only, no screen owns it) plus two numeric fields editable
  live from the Settings screen (below):
  `menu_perf_mode = "auto"/"full"/"perf"` (issue #51's menu-donut override
  — see `menu_display._resolve_perf_mode()`);
  `rhythm_reanalysis_window_seconds` (default `60.0`, valid range 5–1800,
  step 5) — how many seconds of recent audio/data the tab view's `R`
  non-causal rhythm re-analysis (issue #77) reaches back over, re-read
  live from `main.ReanalysisBuffer` on every hop so a Settings-screen edit
  takes effect without restarting, see
  `config.RHYTHM_REANALYSIS_WINDOW_SECONDS`; `tab_scrollback_seconds`
  (default `300.0`, valid range 30–3600, step 30) — how far back the tab
  view's freeze-mode Left/Right scrollback can browse, see
  `config.TAB_SCROLLBACK_SECONDS`. Both numeric fields are read/written
  purely through `config_store.py`'s already-generic
  `preference()`/`set_preference()`, no bespoke accessor needed. The rest
  of the table is still reserved for future settings (e.g. #40's
  still-unwired global `H` keybind-legend on/off persistence); see
  `config_store.py`'s docstring for the full schema and
  `docs/DECISIONS.md` for why the schema stops here for now.

**Settings screen (issue #43).** `virtualnote`'s menu has a `Settings`
entry (same tier as any tool) that opens an interactive editor
(`settings_display.py`) for the `[keybinds]`/`[colors]`/numeric-
`[preferences]` overrides above — Up/Down moves between fields, Enter
edits the highlighted one (captures the very next keypress for a keybind
row; opens an inline 0–360 digit entry for a color row; opens an inline
clamped digit entry, bounded to that field's own min/max, for a numeric
row), Backspace/Delete resets a color row straight back to "default" or a
numeric row straight back to its spec default, and `|`/Esc returns to the
menu, same always-live convention every other tool uses. Edits write
straight through `config_store.set_keybind()`/`set_note_hue_override()`/
`set_preference()` and take effect immediately via the store's existing
hot-reload — no restart, same live-UX as `M`/`P`. A remap can't be bound
to `|` or `h`/`H` — both are global keys every terminal loop checks
unconditionally, so binding an action onto either would make that key
double-fire instead of working as a normal remap. Unlike a color field's
hue (which wraps modulo 360, a circular quantity), a numeric field's typed
value is clamped into its `[min, max]` range instead — the correct
behavior for a bounded real-world quantity like a time window.

## Key design decisions

One-liners; full rationale in `docs/DECISIONS.md`.

- Python + NumPy — cheap enough at these buffer sizes, no build toolchain.
- Hand-rolled YIN, not `aubio`/`librosa` — wheel/dependency risk on Pi.
- `pitch_detect.detect_pitch()` corrects octave-doubling in the low
  register (issue #69, real acoustic testing found ~65-123Hz notes
  frequently locking onto their own 2nd/4th harmonic) with a sub-harmonic
  sanity check: after the ascending threshold scan finds a candidate
  `tau`, small integer multiples of it (`config.YIN_SUBHARMONIC_MAX_MULTIPLE`)
  are checked for a *parabolically-refined* (not raw-grid) CMND value that
  both clears threshold and beats the candidate by a real margin
  (`config.YIN_SUBHARMONIC_MARGIN`); skipped whenever the candidate is
  already very confident (`config.YIN_SUBHARMONIC_SKIP_CMND`), which is
  what keeps octave 3-5 (and plain sine tones) from regressing — see
  docs/DECISIONS.md for the full empirical root-cause writeup, including
  why a naive "just compare raw CMND depth" version of this same idea
  regressed already-correct detections. A subsequent real-mic
  re-verification round found the fix's original `YIN_SUBHARMONIC_MARGIN`
  (0.5, i.e. only ~2x deeper) far too loose: ordinary broadband
  low-frequency content in a real recording (mic self-noise, room rumble,
  mains hum) can produce its own coincidentally-deep CMND dip near
  `tau_max` (the fmin edge), which a 2x margin accepted readily —
  misreading already-correct octave-3 detections down an octave.
  Recalibrated to 0.1 (~10x deeper), backed by adversarial synthetic
  testing that separates genuine subharmonic-lock ratios (<=0.08) from
  mains-hum/noise false-positive ratios (floor ~0.14) with real headroom
  on both sides — see docs/DECISIONS.md's follow-up entry. Only confirmed
  synthetically; a real-mic re-verification is still pending (see Known
  limitations).
- `pitch_detect.detect_pitch()` no longer falls back to the global-argmin
  CMND candidate when its ascending threshold scan finds no `tau` clearing
  `YIN_THRESHOLD` (issue #71) — that fallback, present since the initial
  commit, accepted almost anything short of a near-1.0 CMND cutoff and
  reported confidence as `1 - cmnd[tau]` regardless of *why* the value was
  low. A noise-adversarial acoustic test (a new `noise` suite in
  `scripts/acoustic_pipeline_test.py`) found it confidently (0.6-0.9)
  locking onto a pitch near `FMIN` for any note under moderate broadband
  noise, unrelated to what was actually playing — root-caused to an
  integer multiple (2x/5x/7x, confirmed empirically) of the true,
  noise-degraded period looking deeper than the true dip itself, aided by
  a real, confirmed-on-pure-noise-alone bias: CMND trends systematically
  lower near `tau_max` because the difference function's window shrinks as
  `tau` grows. Since the ascending scan already checks every `tau` for the
  real threshold, finding none means no fallback is principled — it now
  returns `(None, 0.0)`, the same "unvoiced frame" classic YIN specifies.
  A `YIN_THRESHOLD` recalibration was investigated and rejected (evidenced,
  not skipped): a 0.12-0.30 sweep found zero recoverable margin at this
  app's own `moderate` noise level and would have reopened issue #69's
  low-octave subharmonic-check regression at `light` noise for no measured
  benefit. See docs/DECISIONS.md for the full root-cause writeup and
  before/after real-loopback-hop-log numbers (moderate-noise
  wrong-confident rate: 72.8% → 0%).
- Microphone is the default input; `--source loopback` is opt-in and
  Linux-only (PipeWire/PulseAudio monitor), so portability of the default
  path is unaffected.
- Monophonic only — simpler, real-time, fits the use case.
- `pygame-ce` for the GUI — reliable wheels across target platforms.
- `--color-scheme fifths` is additive; `wheel`/`tab` always use fifths so a
  note's color stays consistent between views.
- `tab` uses a grand staff, not single treble — manageable ledger lines
  across the app's 4-octave range.
- `tab`'s left legend area is two side-by-side sub-columns, not one merged
  region (issue #36, reversing #20's earlier "merge into one column"
  call after live user reaction): a clef-glyph column (blank except on
  each staff's anchor line, G4/F3) and, to its right, a letter column
  labeling every staff row — lines *and* spaces alike, via
  `staff_map.row_note_name()`'s general diatonic-step math, not just the
  5 line rows per staff — so the grand staff is legible without already
  knowing note positions by heart, especially in the bass register.
- `tab`'s on-quit dump is plain text, not a rendered image.
- `tab`'s note color ignores octave, fixed lightness
  (`TAB_NOTE_LIGHTNESS = 0.5`) — octave already encodes as staff row.
- Terminal views clear on detected resize — avoids ghosting under tiling WMs.
- Chord mode's pipeline always runs every hop, on by-flag or not — cheap
  enough (measured ~3ms/hop worst case on Pi Zero 2 W) that `P` can stay a
  pure render-thread-local flag with zero shared state, unlike `M`'s
  `AudioCapture.restart()` (which changes what's captured, not just shown).
- `multipitch.detect()` computes its own Hann-windowed FFT from the ring
  buffer rather than reusing `pitch_detect.compute_spectrum()`'s unwindowed
  one — an unwindowed FFT's spectral-leakage sidelobes are strong enough,
  a semitone or more from a real peak, to register as spurious extra notes
  in peak-picking (verified empirically). YIN's own shared spectrum is
  left untouched so its calibrated behavior is unaffected; the extra FFT
  is well inside the latency budget.
- Chroma folding's Gaussian log-frequency weighting uses a narrow 0.25
  semitone sigma, not the wider 0.5 semitones the design docs first
  proposed — 0.5 let each candidate pitch class's Gaussian tail pick up
  enough neighboring energy that a large chord template (more active
  pitch classes) could out-score the correct, sparser template on cosine
  similarity even for an unambiguous root-position triad. Verified against
  a synthesized C-E-G triad, both directly and through a live speaker→mic
  round trip.
- Bass-note detection from `chroma.fold_bass()` is gated on a confidence
  ratio (peak bass-chroma value vs. peak main-chroma value, threshold
  0.25) rather than trusted whenever nonzero — a chord voiced entirely
  above the ~250Hz bass cutoff has no real bass note, and `fold_bass()`'s
  output there is just spectral-leakage noise (empirically ~0.15x the
  main peak) that would otherwise get misread as a slash-chord bass note.
  A genuine sounding bass note measured ~0.35x+.
- `multipitch.detect()` runs against a second, longer ring buffer
  (`config.MULTIPITCH_LOW_WINDOW_SIZE`) instead of the normal live window
  whenever `multipitch.select_window()`'s bass-chroma gate clears (issue
  #63) — the normal ~93ms window's Hann mainlobe is physically wider than
  the gap between an ordinary low triad's fundamentals (e.g. C2→E2, only
  ~17Hz), so their peaks merge into one wrong-frequency peak that no
  amount of interpolation/pruning tuning can separate; a longer window
  actually has the resolution to tell them apart. Gated rather than
  unconditional so the extra ~93ms of latency is paid only on hops with
  real low content, not every hop.
- `multipitch.detect()`'s harmonic-consistency pruning walks candidate
  peaks ascending by frequency, not descending by magnitude (issue #67)
  — the magnitude-first order let a note's own higher harmonic jump the
  queue ahead of its fundamental whenever the FFT happened to weight
  that harmonic louder that hop (routine under real acoustic capture:
  mic/speaker frequency response, room-reflection comb filtering can
  both null a fundamental's bin and boost an overtone's), and
  `_is_harmonic_of()` has no reverse-direction check for "is this
  already-accepted candidate itself a harmonic of a not-yet-accepted
  lower note" — so once the harmonic was accepted first, the true
  fundamental arriving later (not itself a harmonic of anything higher)
  got accepted too, alongside the phantom. Walking low-to-high instead
  means a real fundamental always gets first claim on a slot, so its own
  harmonics reliably prune against it regardless of which partial
  carried more raw magnitude that hop — confirmed against a synthesized
  note whose 3rd harmonic outmagnitudes its own fundamental, and does
  *not* need any widening of `harmonic_tolerance_cents` (a synthetic
  sweep up to 30 cents of pure frequency detuning, fundamental still
  loudest, never broke the existing 35-cent tolerance — the real-world
  failure was evaluation order, not tolerance width). This fix does not
  resolve every note-density recall gap issue #68 also reported — see
  Known limitations for the residual, inherent harmonic-collision
  ambiguity in some chord voicings, and `docs/DECISIONS.md` for the
  full investigation (including why a magnitude-consistency check was
  tried and rejected for the residual gap: it would reopen this exact
  fix).
- `multipitch._is_harmonic_of()` caps the harmonic multiple it will check
  at `config.CHORD_HARMONIC_MAX_NUMBER` (4, issues #67/#68 round 2) —
  without a cap, a real, independently-sounding note could get pruned
  just for accidentally landing near a *large* integer multiple (8x, 9x,
  12x...) of some other already-accepted note that hop; more such
  multiples exist to accidentally collide as chord density/pitch spread
  grows, which is exactly #68's density-recall symptom. 4 matches the one
  convention this codebase already treats as "the harmonics that matter"
  (`chroma.HARMONIC_WEIGHTS`, `YIN_SUBHARMONIC_MAX_MULTIPLE`) and is safe
  against every harmonic content this app's own tests/acoustic-test synth
  produce (never above the 4th). Measured on the `--source loopback`
  acoustic density suite: missing pcs/hop dropped from 0.67-1.51 to
  0-0.73 across 3-6 simultaneous notes, at the cost of a small phantom-
  rate uptick (0 → 0-0.33/hop) from genuinely high-order overtones (real
  mic/speaker/room distortion) no longer being silently absorbed — a net
  win on this signal. Does not touch the harmonic_number≤4 near-exact
  collision case below, which is unrelated and still open.
- `chord_templates._resolve_tie()` falls back to `lowest_pc` — the pitch
  class of whichever detected note is lowest in frequency this hop, no
  bass-register requirement — before falling back further to an arbitrary
  lowest-root-index pick (issue #67 round 2). A rotationally-symmetric
  chord quality (aug, dim7, half-dim7/min6, ...) voiced entirely above
  `chroma.DEFAULT_BASS_CUTOFF_HZ` has no genuine `bass_chroma` signal at
  all (that gate is correct for genuine slash-chord naming, but leaves
  these chords with zero disambiguation) — real acoustic testing found
  this made an F#-A#-D augmented triad, voiced upward from F#4, name
  consistently as "D+" instead of "F#+". `lowest_pc` is computed for free
  from `multipitch.detect()`'s already harmonic-pruned note candidates
  (the same ones issue #56 already routed chord-name matching through),
  and only ever fires when multiple templates are genuinely tied on
  cosine similarity, so it can't override a real, better-scoring match.
- `tab`'s notehead style (`N`), legend visibility (`L`), and freeze-frame
  (`Space`) are pure render-thread-local state in `main.py`, same as `P` —
  `TabDisplay` itself owns no toggle state, just renders whatever
  style/visibility/frozen-ness `render()` is called with each frame.
- `tab`'s notehead rendering keeps each note's raw pitch_class/octave (not
  a precomputed label) so a live `N` toggle restyles columns already on
  screen; `dump_ansi()` keeps its own precomputed letter+octave label
  independently, unaffected by either notehead toggle.
- `tab`'s per-column dimming (issue #22) recomputes each note's color fresh
  every `render()` call from its raw `pitch_class` and the column's age
  (distance from the newest *visible* column), rather than reusing the
  rgb baked in at push time — a column's age changes every frame as newer
  columns scroll in, so it can't be fixed once at push time. The
  precomputed push-time rgb survives only for `dump_ansi()`, which stays
  letter+octave/full-brightness as before, same reasoning as the notehead
  style toggle above.
- `DIM_LIGHTNESS` (0.16) is a single constant in `config.py`, imported by
  both `terminal_wheel_display.py` (inactive wedges) and
  `terminal_tab_display.py` (dimmed columns) — promoted there specifically
  so `tab`'s dimming floor and `wheel`'s inactive-wedge lightness can never
  drift apart, same rationale as the existing `NOTE_NAMES_FIFTHS`/
  `diatonic_step()` shared-source fix.
- `tab`'s freeze-frame (`Space`, issue #23) is a view-only pause: while
  frozen, `main.py`'s `run_terminal_tab` simply stops calling
  `result_queue.get_nowait()`, so no new columns get pushed and the status
  line's note/freq/etc. fields hold their last value — the analysis thread
  keeps overwriting the single-slot queue in the background regardless (no
  backlog risk, per this app's threaded architecture). `TabDisplay.render()`
  doesn't know why nothing new is arriving; it's just told `frozen=True`
  and pins every visible column's age to 0.
- `config_store.ConfigStore` hot-reloads by `os.stat()`-checking the TOML
  file's mtime on every accessor call rather than a file-watcher thread —
  cheap enough to call every hop/frame, same zero-shared-state spirit as
  `P`/`M`/`N`/`L`, and it's what lets `[keybinds]`/`[colors]` overrides
  apply live with no restart and no explicit reload call anywhere in the
  render loop.
- Per-note `[colors]` overrides replace hue only, not saturation or
  lightness — read as "override this note's color identity," not "hand it
  an arbitrary RGB," so octave-driven lightness (fill/GUI) and tab's fixed
  `TAB_NOTE_LIGHTNESS` both keep working unmodified underneath an override.
- `SessionState`'s capture/analysis thread are created lazily on first
  tool entry, not at `virtualnote` process start — sitting at the bare
  menu never opens the mic, a real, user-visible side effect (an OS-level
  "listening" indicator, a mic-access permission prompt) that shouldn't
  fire just from looking at a menu. `ensure_started()` is idempotent
  specifically so both `main()`'s eager one-shot call and `shell.py`'s
  repeated per-tool-entry call can use the exact same code path.
- Terminal `run_*` functions return an explicit `"quit"`/`"menu"` sentinel
  string instead of the previous implicit `None` (via a swallowed
  `KeyboardInterrupt`) — a plain return value, not an exception or a piece
  of shared mutable state, is the simplest way for `shell.py`'s menu loop
  to tell "the user pressed `|`, go back to the menu" apart from "the user
  quit for real," and it composes cleanly with the existing `finally`
  blocks (`keys.restore()`/`display.quit()`/`dump_ansi()` all still run
  before the sentinel is returned, same as they always ran before a plain
  `return`/loop-exit).
- The Settings screen (#43) is a `blessed` app, not raw ANSI — the one
  deliberate, scoped exception to this project's raw-ANSI-everywhere
  convention, settled by #37/#39's grilling specifically for this screen's
  form controls (field navigation, "press a key to capture this remap").
  It's reached from `shell.py`'s menu loop by name (`selection ==
  "settings"`) rather than through `main.run_session()` — it doesn't touch
  `SessionState` at all, so opening it never triggers the lazy
  mic-open `ensure_started()` every real tool does.
- A keybind can't be remapped onto `|` or `h`/`H` in the Settings screen
  (`settings_display.is_valid_remap_key`) — both are global keys checked
  unconditionally by every `run_terminal_*` loop, ahead of or independent
  from any `store.keybind()` lookup, so binding an action onto either would
  make that key double-fire (the action, then instantly back to the menu,
  or flip the help legend) instead of working as a normal remap.
- The Credits screen (#44) is static content with no user-editable state,
  so it stays raw ANSI rather than reaching for the Settings screen's
  `blessed` exception — consistent with #37/#39's "scoped exception, not a
  wholesale framework adoption" framing. It waits for *any* keypress to
  return to the menu, not specifically `|` — there's no other state on a
  static info screen a stray key could disturb, so being lenient there is
  strictly more usable than requiring the exact global back-to-menu key.
- The main menu screen's donation callout uses an OSC 8 terminal hyperlink
  escape sequence (`menu_display.osc8_link()`), not a plain printed URL —
  genuinely clickable in terminals that support it (kitty, iTerm2, wezterm,
  gnome-terminal, etc.) and silently degrades to plain text everywhere
  else, since an unsupported terminal just ignores the escape bytes. No
  separate fallback branch needed. `DONATION_URL` in `config.py` is a
  placeholder Patreon URL — shipping now rather than blocking on a real
  account existing was #44's explicit call; swapping in the real URL later
  is a one-line change.
- The menu donut's point-projection (issue #42's flagged, #51's fixed
  problem) is vectorized with NumPy rather than kept as the prototype's
  plain-Python double loop over theta/phi: the whole grid's trig/torus
  algebra runs as array ops (`menu_animation._project()`), and the
  painter's-algorithm z-buffer is built via an ascending-depth `argsort`
  followed by fancy-index assignment (NumPy keeps the *last* write to a
  repeated index, so sorting by depth ascending makes the nearest point
  per cell win) instead of a per-point `if ooz > zbuffer[idx]` comparison
  — same "push the hot loop into NumPy" approach `pitch_detect.py`/
  `chroma.py`/`multipitch.py` already use for this codebase's FFT math.
  Measured on this dev machine: ~80ms/frame (prototype, matching the
  order of magnitude of #42's own ~149ms desktop measurement) down to
  ~12ms/frame (vectorized, 80x40 terminal) — a ~7x speedup, comfortably
  under the 33ms/30fps full-mode budget; ~9ms/frame measured even at a
  much larger 200x50 terminal, since raster size (not the fixed
  theta/phi sampling grid) barely moves the cost. Only the row/column
  string-assembly loop (bounded by terminal size, already small) stays
  plain Python — it wasn't the measured bottleneck.
- The animated menu screen's layout (issue #51) puts the donut and the
  title/donation/tool-list/hints/status text in two side-by-side panes
  (`menu_display._layout()`) rather than overlaying text on top of the
  donut raster or replacing it outright — simplest to get right with this
  project's raw-ANSI/no-terminal-graphics-library constraint (no alpha
  blending, so "overlay" would mean either fully clobbering donut cells
  under the text or fiddly per-glyph transparency tracking), and keeps
  the two independently redrawable: the donut pane is always fully
  explicit content (every cell is a glyph or a space, no diffing needed)
  written first, and the text pane's `\033[K`-then-write per line is
  addressed strictly to its own columns, so neither redraw can clobber
  the other. Below `config.MENU_MIN_DONUT_COLS` of leftover width the
  donut is dropped rather than shrunk further — a corner of a donut too
  small to read is worse than no donut, and the resulting text-only
  fallback is exactly this screen's original #40 placeholder shape, not
  a new code path to maintain.
- The auto-detect heuristic's real-timing probe (`detect_perf_mode()`)
  and its actual decision (`_decide_perf_mode()`) are two separate
  functions, not one — mirrors this repo's existing "pure logic unit-
  tested, real I/O/timing smoke-tested" convention (see `tests/`'s Files
  entry): `_decide_perf_mode()` takes a cpu count and a probe average (or
  `None`) as plain arguments and is fully deterministic, so the floor/
  budget branches are unit-tested without spending real wall-clock time
  or depending on the test machine's actual core count.
- The menu's perf-mode override resolution order (issue #42's "config/CLI
  override" requirement) is explicit CLI flag > `config.toml`'s
  `[preferences].menu_perf_mode` > the real auto-detect probe
  (`menu_display._resolve_perf_mode()`) — CLI wins because it's the most
  explicit, most temporary signal (a one-off `--menu-perf-mode perf` to
  work around a bad autodetect on unfamiliar hardware shouldn't require
  editing a config file); `--menu-perf-mode auto` still exists specifically
  so a CLI invocation can force auto-detection even when a config.toml
  preference has pinned a mode, rather than "auto" only ever meaning "no
  flag was passed."
- `_resolve_perf_mode()`'s auto-probe result is cached per `(cols, rows)`
  in a module-level dict (`menu_display._perf_probe_cache`) — without it,
  `shell.py`'s `run_menu_loop()` building a fresh `MenuDisplay` on every
  `|` back-to-menu round trip would re-run `detect_perf_mode()`'s real
  frame-timing probe every single time, quietly working against the
  "instant transition, no relaunch latency" reason `|` exists at all (see
  Architecture). A resize still gets a fresh probe at the new size
  (different cache key) — only a repeat visit at an already-measured size
  is free. An explicit override (CLI flag or config.toml) never touches
  the probe or the cache, since it's already free.
- Settings/Credits (issues #43/#44) don't return a `"menu"`/`"quit"`
  sentinel the way every `run_terminal_*` view does — `shell.py`'s
  `_NON_SESSION_SCREENS` dispatch always loops back to the menu regardless
  of their return value, since neither has any other state to distinguish.
  Ctrl+C during either still needs to quit the whole app like everywhere
  else, though: both screens' raw-keyboard mode (blessed's `cbreak()` for
  Settings, `main.RawKeys` for Credits) leaves SIGINT enabled exactly like
  every other terminal view, so a bare `KeyboardInterrupt` does reach
  `shell.py` — just outside the menu-polling loop's own `try/except`,
  which only wraps the menu screen's poll loop, not this dispatch. Caught
  with its own explicit `try/except KeyboardInterrupt: return` around the
  `_NON_SESSION_SCREENS` call instead.
- `RenderItem.duration_hops`/`bpm_estimate` (issue #55) are exactly two
  new fields, chosen to match the shape a future score-file/playback
  consumer (map #24) will want — deliberately *not* a list of every note
  that finalized this hop, even though chord mode can finalize more than
  one note in a single hop. Mono's field pairs unambiguously with the
  *previous* hop's `pitch_class`/`octave` (that's the note `DurationTracker`
  was actually tracking); chord mode's per-note duration instead rides
  along inside each `note_stack` entry's own `duration_hops` key (that
  list already existed, so this isn't a new top-level field) rather than
  forcing multiple simultaneous finalizations through one flat int.
- Chord-mode duration tracking always passes `is_onset=False` to
  `DurationTracker.update()` — `multipitch.detect()` has no persistent
  per-note identity across hops (independent spectral peak-picking every
  hop), so there's no reliable signal for "this is a genuine re-attack of
  an already-sounding pitch" the way `NoteSmoother`'s monophonic onset gate
  (note-change / RMS jump / spectral flux) has. The ordinary appear/
  sustain/disappear lifecycle still tracks correctly via absence-based
  finalization; a same-pitch re-attack mid-sustain with no gap just won't
  split into two chord-mode notes. A deliberate, bounded scope-narrowing
  versus the mono path, not an oversight.
- `TabDisplay.push()`/`.push_notes()`/`.push_barline()` take an optional
  `t=` timestamp override (issue #55) — live callers omit it and get the
  original wall-clock time-since-construction; `main.run_batch_
  transcribe()` passes the note's real onset time from the recording
  instead, since a batch sweep pushes every column within milliseconds of
  real time regardless of where the notes actually fall in the file —
  without the override, `dump_ansi()`'s `t` column would read ~0.00s for
  an entire transcription.
- `librosa` is isolated to `batch_transcribe.py` and `rhythm_reanalysis.py`
  — never imported by `main.py` or `analysis_loop()`'s own per-hop path
  directly. `rhythm_reanalysis.py` is a deliberate second, narrowly-scoped
  exception to "librosa only in batch_transcribe.py" (issue #77): its
  `recompute()` reuses the exact same non-causal machinery
  (`DurationTracker.finalize_noncausal()` + `librosa.beat.beat_track()`)
  batch already uses, triggered live by the `R` key but never running on
  the live per-hop path itself — only on a throwaway thread, at explicit
  user request, while the view is frozen. Extending batch's own already-
  accepted offline use of librosa to this one additional on-demand-replay
  trigger was judged simpler and more consistent than either duplicating
  `finalize_noncausal()`'s non-librosa half into a third place or routing
  the live `R` press through `batch_transcribe.py` itself (which assumes
  a whole preloaded file, not a rolling live buffer — see
  docs/research/live-noncausal-rhythm-reanalysis.md's Q3).
- Issue #77's `R`-key recompute runs on a throwaway `threading.Thread`
  spawned the instant `R` is pressed, not routed through the analysis
  thread via a request/response queue (the other option
  docs/research/live-noncausal-rhythm-reanalysis.md's Q5 considered and
  rejected) — the analysis thread's own per-hop cadence must never stall
  on a recompute that can take up to ~1.3s at the largest configured
  window (benchmarked in that research doc), and the render loop has
  nothing else to do while frozen anyway. The rolling buffer itself
  (`main.ReanalysisBuffer`) still lives on and is appended to only by the
  analysis thread, right alongside its other per-hop trackers -- that's
  the one place that already computes every value the recompute needs
  (mono/chord magnitude+onset signals, chroma-flux novelty) each hop. The
  render thread's throwaway thread reads a plain `list(deque)` snapshot of
  it directly rather than through a second queue -- safe against
  corruption from a concurrent append under CPython's GIL (deque
  operations are individually atomic) but not a guaranteed fixed-point-
  in-time read; acceptable because `R` only ever fires while frozen, so a
  slightly stale snapshot is a low-stakes imprecision, not a correctness
  bug.
- `rhythm_reanalysis.recompute()` is a pure function (`HopRecord`s +
  `hop_seconds`/`beats_per_bar` in, a `RecomputeResult` or `None` out) with
  no thread/queue awareness of its own, following this codebase's existing
  "pure logic unit-tested, real I/O/threading smoke-tested" convention
  (see `menu_animation.detect_perf_mode()`/`_decide_perf_mode()` for the
  precedent) -- `main.py`'s `_handle_reanalysis_key()`/
  `_apply_reanalysis_result()` own all the threading/queue/`TabDisplay`
  side effects instead.
- The `R`-key recompute's corrected tempo estimate does take over the
  `tab` view's `tempo=` status field (not just duration glyphs/barlines)
  until the next unfreeze -- while frozen, the live `bpm_estimate` isn't
  advancing anyway (the view has stopped draining `result_queue`), so
  there's no live value it could be conflicting with or masking.
- Barline reconciliation (`erase_barlines()`+`insert_barline()`) only
  happens when the recompute actually produced a `bpm_estimate` -- with
  none (e.g. a near-silent buffered window), `recompute()` can't place any
  corrected barlines either, and erasing the window's existing
  (live-estimated, imperfect but non-empty) barlines with nothing to
  replace them would be strictly worse than leaving them alone. Corrected
  note durations apply regardless of whether a bpm estimate came back,
  since they already fall back to the same `DEFAULT_DURATION_CLASS` the
  live path uses when no bpm is available -- applying them is never worse
  than what's already displayed.

## Known limitations / things learned

One-liners; full detail in `docs/DECISIONS.md`.

- Octave-error blips (~100ms) can occur during note decay; not worth fixing
  without a concrete complaint.
- Live pitch-tracking quality varies run-to-run with room/mic conditions —
  not a regression. (One concrete, non-room-dependent low-register
  instance of this *was* found and fixed, though: issue #69's octave-2
  YIN octave-doubling — see Key design decisions. "Varies with room/mic
  conditions" still covers everything else, e.g. C#2/D2/G#2 sometimes
  going silence-gated in the same acoustic test, which is an amplitude/
  sensitivity-threshold question, not a YIN algorithm bug.)
- Issue #69's octave-doubling fix has round-tripped through real-mic
  verification twice: it fixed the originally-reported failures, a
  follow-up real-mic check found it had regressed other, previously-
  correct octave-2/3 detections, and that regression was root-caused and
  fixed via a margin recalibration (`YIN_SUBHARMONIC_MARGIN` 0.5 → 0.1 —
  see Key design decisions and docs/DECISIONS.md). That recalibration is
  validated only against adversarial *synthetic* signals (deliberately
  constructed to approximate real mic self-noise/room rumble/mains hum)
  plus `--source loopback` (which cannot reproduce this failure mode at
  all — no physical mic coloration). A real speaker→mic re-verification —
  the same kind that caught the regression the first time — has not yet
  been done for this round; treat the current constants as provisionally
  fixed, not field-confirmed, until that happens.
- Under sustained broadband noise around the acoustic test suite's
  `moderate` level (issue #71), monophonic detection genuinely goes silent
  rather than reporting a note — a single ~93ms analysis window's
  periodicity evidence is too degraded at that noise level for any
  per-hop threshold to recover (confirmed via a 0.12-0.30 threshold
  sweep finding zero recoverable margin), an honest statistical limit, not
  a bug to chase further without new evidence (e.g. cross-hop periodicity
  accumulation, out of scope for this fix). What was fixed is the far
  worse failure mode this replaced: confidently reporting a *wrong* note
  near `FMIN` regardless of octave (72.8% of moderate-noise hops,
  measured on real `--source loopback` audio, pre-fix) — see Key design
  decisions and docs/DECISIONS.md. Validated via synthetic adversarial
  testing plus `--source loopback`, not a real physical speaker→mic
  session — same "synthetic/loopback fixes haven't always survived
  real-mic testing" caveat as issue #69 above; a real-mic re-check is
  still advisable.
- Same issue #71 fix also cost recall at the `tempo` suite's fastest
  tested speed only: 280bpm eighth-note legato (107ms/note, already this
  suite's explicit stress case, not a normal-use guarantee) dropped from
  a stable 88% to a stable 71% (each measured twice via `--source
  loopback --suites tempo`), while 90/140/200bpm stayed 100% throughout.
  Same trade-off as the noise case above at a different stressor (a fast
  transition's analysis window briefly straddling two notes used to get
  rescued by the removed fallback's lucky guess); not chased further for
  the same reason — see docs/DECISIONS.md's #71 entry.
- Target 64-bit Raspberry Pi OS (Bookworm+) — 32-bit is a wheel risk.
- macOS/Windows gate mic access per-app; a denied prompt gives silent zeros,
  not an error.
- `~/.local/bin` is on PATH via `~/.zshrc`, for `virtualnote` (formerly `colorize`, retired by issue #40).
- `tab --scroll onset` freezes on sustained notes/silence, by design.
- Terminals <~22 rows clip outermost `tab`-view ledger-line notes.
- A minor-7th chord and its relative-major 6th chord share the exact same
  pitch-class set (e.g. Am7 = A-C-E-G, C6 = C-E-G-A) — an inherent
  music-theory ambiguity, not a bug. Without a confident bass note to
  disambiguate the root, `chord_templates.match()` deterministically picks
  the lower root-index template; this is correct behavior, not a wrong
  answer, when no bass is actually present.
- A chord voiced so that one note's frequency nearly coincides with
  another note's own harmonic (e.g. a root and a fifth an octave+fifth
  above it, a 3:1 ratio — 12-TET's fifth+octave lands only ~2 cents from
  the true 3rd harmonic) can still lose the higher note in
  `multipitch.detect()`'s harmonic-consistency pruning, even after issue
  #67's evaluation-order fix (below) — investigated for issue #68.
  Confirmed by direct experiment: no combination of pruning order or
  magnitude-based reasoning can distinguish "this peak is note X's own
  3rd harmonic" from "this peak is a real, independent note that happens
  to sit within a couple of cents of note X's 3rd harmonic" from a single
  hop's magnitude spectrum alone — the two cases are spectrally
  identical. A magnitude-consistency check (only prune a harmonic
  candidate if it's quieter than its accepted fundamental, scaled by
  typical overtone decay) was tried and rejected: it reopens issue #67
  (whose real acoustic failure was a genuine overtone measuring *louder*
  than its own fundamental, via mic/speaker frequency response) exactly
  as often as it would fix #68. Narrowing `harmonic_tolerance_cents`
  doesn't help either — the coincidence itself is only ~2 cents in exact
  math, well inside any tolerance wide enough to still catch real
  acoustic jitter. Resolving this fully would need information beyond a
  single hop's magnitude spectrum (e.g. per-pitch-class onset/persistence
  tracked across hops) — out of scope for #67/#68's pruning-logic tuning.
  Chord voicings without such coincidental intervals are unaffected (see
  `tests/test_multipitch.py`'s dense-chord test, deliberately built with
  a >=60-cent safety margin from any small-integer frequency ratio). Issue
  #67/#68 round 2's `CHORD_HARMONIC_MAX_NUMBER` cap (above) fixed the
  higher-order variant of this same collision class (a note landing near
  some *large* multiple — 6x, 9x, 12x — of another note, not itself a
  plausible overtone relationship); it deliberately does not touch this
  harmonic_number≤4 case, since 3 (and 4, 6=2×3, etc. once octave-folded)
  are exactly the multiples a real instrument's own overtone series
  legitimately produces, so capping lower would just reopen issue #67.
- Low bass notes with no harmonic content (a pure sine, no overtones) can
  be a semitone off in `chroma.fold_bass()`'s bass-note detection — real
  bass instruments' overtones resolve this fine (see the harmonic-summing
  rationale above); a pure low tone is an edge case, not representative of
  real playing, so not chased further without a concrete complaint.
- Chord mode's thresholds/constants (`CHORD_MATCH_THRESHOLD`,
  `CHORD_MEDIAN_WINDOW`, `CHORD_DEBOUNCE_HOPS`, `NOTE_STACK_ATTACK_HOPS`/
  `RELEASE_HOPS`, the multipitch peak-picking constants) are provisional
  starting values per the spec, not yet tuned against extended real
  playing beyond the smoke tests already run live.
- Rhythm mode's thresholds/constants (`ONSET_FLUX_THRESHOLD`,
  `DURATION_DECAY_RATIO`, `TEMPO_HISTORY_SECONDS`/`MIN_BPM`/`MAX_BPM`/
  `UPDATE_INTERVAL_HOPS`) are likewise provisional, same convention as
  chord mode's — verified against synthesized test signals (known-BPM
  impulse trains, synthetic decay envelopes) and one real
  `virtualnote transcribe` run against a synthesized melody, not yet
  tuned against extended real playing.
- Barline placement (issue #55) is explicitly approximate, tied to the
  live/estimated tempo rather than exact bar-for-bar accuracy — drift
  under an imperfect tempo estimate is accepted, not a bug to chase (same
  posture as chord mode's provisional thresholds above).
- A short mono note's own 20ms attack fade can, on real recorded audio
  (not reproduced by idealized block-aligned synthetic test signals —
  found via a live `--source loopback` re-run, issue #70), straddle a hop
  boundary awkwardly enough that the block-to-block RMS ratio during the
  ramp-up itself clears `ONSET_RMS_JUMP_DB`, firing a spurious same-key
  re-onset within a hop or two of the note's own genuine attack and
  splitting it into two duration-tracker events instead of one. Real-
  audio-timing-jitter-sensitive rather than a clean deterministic bug (see
  issue #70's writeup in `docs/DECISIONS.md` for the two other, *fixed*
  mechanisms this was found alongside); tightening the RMS-jump/
  spectral-flux onset heuristics risks the opposite failure (missing a
  genuine fast repeated note, issue #55 story 3's explicit scope) without
  further tuning against real playing, so left as a known limitation
  rather than chased further for now.
- The treble clef glyph (𝄞) can still render with its bottom clipped off
  in some terminal/font combinations — investigated for issue #20;
  measured (Pillow `ImageFont.getbbox()`) that its covering font
  (`NotoMusic-Regular.ttf` on this machine) draws it using that font's
  *entire* descent allocation, unlike the bass clef or notehead glyphs,
  which explains why only the treble clef is ever reported clipped. No
  ANSI-level control exists over a fallback glyph's vertical placement
  inside a terminal's cell grid, so this is a terminal/font-stack property,
  not something fixable from the app layer — see `docs/DECISIONS.md` for
  the full investigation.
- Issue #77's `R`-key recompute is scoped strictly to whatever's currently
  sitting in `main.ReanalysisBuffer` — it never redoes pitch/chord
  detection itself, only re-runs the rhythm layer (durations/tempo/
  barlines) against already-detected note events. A wrong pitch/chord
  detection upstream (e.g. one of this project's already-documented
  octave-doubling or harmonic-collision limitations above) stays wrong;
  `R` can only correct *when* a note started/stopped and how the beat grid
  falls, not *what* note it was.
- A note still genuinely sounding at the exact moment `R` is pressed has
  no true decay boundary inside the buffered window and gets truncated to
  whatever's currently visible — the same class of edge case
  `duration_tracker.DEFAULT_DURATION_CLASS`'s "still sounding at quit"
  fallback already documents, not a new failure mode. Mitigated in the
  common case (freezing usually happens after playing has already paused)
  but not eliminated (see docs/research/live-noncausal-rhythm-
  reanalysis.md's Q3).
- A mono or chord note whose true onset happened *before* the buffered
  window started (i.e., it's already sounding on the very first buffered
  hop, with no onset event inside the window at all) can't be corrected —
  `rhythm_reanalysis.recompute()` has no onset to anchor a duration
  measurement to for it, so it's silently skipped rather than guessed at.
  Widening `rhythm_reanalysis_window_seconds` reduces how often this
  happens but can't eliminate it outright (there's always some true start
  of the buffer).
- `chroma_flux()`'s coarse 12-bin chroma-difference novelty signal, fed
  directly to `librosa.beat.beat_track()` as `onset_envelope=` (rather
  than librosa's own full mel-spectrogram-based `onset_strength()`, which
  needs raw audio this feature deliberately never buffers — see
  docs/research/live-noncausal-rhythm-reanalysis.md's Q1), is a confirmed
  free win on frame-rate alignment (same `hop_length`/`sr` by
  construction) but an open empirical question on tempo-tracking
  *accuracy* specifically — not yet measured against real playing beyond
  this feature's own synthetic unit tests (`tests/test_rhythm_
  reanalysis.py`'s periodic-impulse convergence check).
- No "quality/time-budget" dial beyond `rhythm_reanalysis_window_seconds`
  itself ships with this feature — investigated and rejected during design
  (docs/research/live-noncausal-rhythm-reanalysis.md's Q4): neither
  `DurationTracker.finalize_noncausal()` nor `librosa.beat.beat_track()`
  has a genuine internal speed/accuracy tradeoff to expose once given a
  fixed input; window length (already user-facing) is the one real lever
  confirmed by direct benchmarking. A multi-hypothesis ensemble pass
  (several `beat_track()` calls at different `start_bpm` priors,
  reconciled by vote) was identified as a real, affordable future
  accuracy improvement but isn't built — out of this ticket's scope.

## Working practices

This repo is tracked on GitHub at `github.com/pellepang/note-color`; git is
the system of record for the project's history, not just a backup.

- After each meaningful checkpoint (a fix, a feature, a config/behavior
  change), commit with a message that describes the change and its
  rationale, then push to `origin/main`.
- Keep commits scoped to one logical change rather than batching unrelated
  work together.
- Generated run-time artifacts (e.g. `note_history_*.txt` dumps from the
  `tab` view) are gitignored, not committed.
- When a backlog item is resolved, delete it from this file rather than
  archiving it here — `git log` already preserves that history.
- New design rationale goes into `docs/DECISIONS.md`, not inlined here —
  keep this file to orientation only.

## Reference

- Full design rationale: `docs/DECISIONS.md`.
- Full original build plan and rationale (pitch detection algorithm choice,
  audio pipeline design, build order):
  `/home/pelle/.claude/plans/i-want-to-make-graceful-stallman.md`.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `pellepang/note-color`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (neither exists yet — created lazily by `/domain-modeling`). See `docs/agents/domain.md`.
