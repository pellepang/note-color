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
1391 tests), and detection has been confirmed with a real speaker→mic
acoustic round-trip test — both the original monophonic pipeline and
chord mode (see below). Pitch-tracking accuracy on real audio varies
run-to-run with room/mic conditions — inherent to monophonic pitch
tracking, not a bug to chase without a concrete symptom.

The terminal score editor (map [#85](https://github.com/pellepang/note-color/issues/85),
implementation spec at [issue #98](https://github.com/pellepang/note-color/issues/98))
is implemented end to end: the data model/persistence layer
(`score_editor_state.py`) and the interaction/CLI/menu-wiring layer
(`score_editor_display.py`, `chord_builder_display.py`,
`score_properties_display.py`, `score_editor_picker.py`,
`main.run_score_editor()`) both landed, covering everything map #85's
own scope includes (loading/creating, navigating, editing pitch/
duration/rests/chords, inserting/deleting columns, time signature/key/
tempo, saving back to MusicXML, both `virtualnote edit <path>` and a
live-menu entry) — map #85 is shippable as of this feature, modulo the
four things its own scope explicitly excluded from the start: foreign
(non-`score_writer.py`-produced) MusicXML import fidelity, a print/
engraving-quality formatter, music-theory analysis, and live audio
playback from inside the editor -- the last of which has since been
delivered anyway by map [#99](https://github.com/pellepang/note-color/issues/99)'s
ticket [#120](https://github.com/pellepang/note-color/issues/120)
(audition, piano-mode entry, play-from-cursor with a playhead, metronome
and a loop region), so only the first three remain out of scope. Verified via the full unit suite plus
manual smoke-testing of the interactive loop (a real TTY isn't available
in this environment — see the implementation PR/commit for what was and
wasn't verified that way); not yet used for a real multi-session editing
workflow.

The standalone synth tool (map
[#99](https://github.com/pellepang/note-color/issues/99), build ticket
[#119](https://github.com/pellepang/note-color/issues/119), decision
[#107](https://github.com/pellepang/note-color/issues/107)) is
implemented end to end — four Tab-cycled layouts, an arrow-driven
parameter panel over an always-visible lit input layer, inline patch
load/save and sample-import overlays, custom layouts in their own files,
and its own `shell.py` dispatch branch plus a `virtualnote synth`
subcommand. Its pure logic is unit-tested directly and its interactive
loop is driven headlessly by scripted key streams (ticket #120's
pattern), but **nothing about it has been heard**: it was built on a
muted machine with no interactive TTY, so the audio path, `render()`, and
every timbre/ergonomics judgement are unverified. See Known limitations.

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

any note source (`virtualnote replay --play`, later the synth tool/editor/MIDI)
    -> sound_engine.NoteOn (pitch, velocity, channel, patch) -- note-on/note-off only
    -> sound_engine.SoundEngine.note_on()  (one process-wide engine, lazily started)
        -> Engine.note_on() -> Voice        (tone_engine.ToneEngine today; #113 synth next)
        -> VoiceManager                      (hard polyphony cap, oldest-released-first stealing)
    -> OutputStream callback: Voice.render() summed -> np.tanh -> device
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

Audio *output* (map #99, decision #105) is a separate, independently lazy
path: `SessionState.ensure_sound_engine()` opens one process-wide
`sound_engine.SoundEngine` on first use and keeps it for the process's
life, exactly as `ensure_started()` does for input — so switching tools
never drops or reopens the output device, and a tool that only plays
(the score editor) never opens the mic, nor vice versa. `virtualnote
transcribe --play` bypasses all of it (offline pre-render, still
`playback.render_offline()`); `virtualnote replay --play` builds its own
`SoundEngine`, since that entry point constructs no `SessionState`.

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
| `multipitch.py` | `detect()` — spectral peak-picking (own Hann-windowed FFT, not the shared one — see Key design decisions) + harmonic-consistency pruning, up to 6 simultaneous notes with confidence, candidate peaks bounded to `min_freq_hz`/`max_freq_hz` (issue #74, default `DEFAULT_MIN_FREQ_HZ`/`DEFAULT_MAX_FREQ_HZ` matching `config.FMIN`/`FMAX` — real callers pass those config values explicitly) so out-of-range broadband/percussive noise content can't be peak-picked into a phantom note. `select_window()` (issue #63) picks which ring buffer a hop's `detect()` call should analyze — the app's normal live `config.WINDOW_SIZE` window, or a longer `config.MULTIPITCH_LOW_WINDOW_SIZE` one the caller (`main.py`/`batch_transcribe.py`) also maintains, gated on `chroma.fold_bass()` showing real low-frequency content — see Key design decisions for why the short window alone garbles close-together bass notes. |
| `detection_backends.py` | The pluggable seam between `analysis_loop()` and the two detection functions above (per `docs/research/architecture-modernization-plan.md` §3.1): `MonoPitchBackend`/`PolyphonicBackend` `typing.Protocol`s mirroring `pitch_detect.detect_pitch()`'s `(freq_hz \| None, confidence)` and `multipitch.detect()`'s `list[NoteCandidate]` return shapes exactly — nothing algorithm-specific in the Protocol itself, since `NoteSmoother`/`ChordSmoother`/`DurationTracker` already only depend on those shapes, not on how they were produced. `YinBackend`/`SpectralPeakBackend` wrap today's two functions as adapter classes, capturing all algorithm-specific config (fmin/fmax/threshold/subharmonic params, max_notes/min_mag_ratio/harmonic_tolerance_cents/etc.) once at `__init__` time instead of threading it through every call — the part that actually buys pluggability, since it moves "which config constants this algorithm needs" out of `analysis_loop()`'s body. `default_pitch_backend()`/`default_poly_backend()` build the two default backends from `config.*` exactly as `analysis_loop()` called `detect_pitch()`/`multipitch.detect()` directly before this seam existed — see Key design decisions for why `multipitch.select_window()`'s bass-gated window logic deliberately stays outside this Protocol. |
| `chord_smoother.py` | `ChordSmoother` — mirrors `NoteSmoother`'s shape for chord mode: chroma rolling-average + chord-name debounce, plus asymmetric attack/release hysteresis per note-stack slot. |
| `onset_detect.py` | (issue #55) `spectral_flux()`/`chroma_flux()` — pure, `None`-safe half-wave-rectified positive-magnitude-difference novelty measures between two consecutive `pitch_detect.compute_spectrum()`/`chroma.fold()` frames. `spectral_flux()` feeds `note_smoother.py`'s onset gate; `chroma_flux()` feeds `tempo_tracker.py`. |
| `duration_tracker.py` | (issue #55) `DurationTracker` — mirrors `ChordSmoother.note_states`' dict-of-state shape, but for *measuring* how long a note sounded rather than debouncing its display. `.update()` (live, causal, keyed by `(pitch_class, octave)`, `is_onset`-aware re-attack preemption) and `.finalize_noncausal()` (batch, centered-smoothed envelope, static method) share one off-threshold definition (`DURATION_DECAY_RATIO`). `duration_class_for_beats()`/`DEFAULT_DURATION_CLASS` — nearest-standard-note-value snapping (incl. dotted), used by both live and batch. `require_onset_for_new_note` (constructor, issue #70) — mono's tracker sets this `True` so a key with no existing state only opens one when `is_onset` is genuinely `True` for it, since `NoteSmoother` otherwise echoes a just-finalized note's key with `is_onset=False` for a couple more hops (its own silence grace period) that would otherwise misread as a spurious new note; chord mode keeps the default `False` since it has no reliable per-note onset signal at all and relies on appear/absence alone. `.update()`'s `onset_backdate` parameter (issue #70) backdates a freshly-opened state's `onset_hop`, fed from `NoteSmoother.onset_backdate_hops` for mono. |
| `tempo_tracker.py` | (issue #55) `TempoTracker` — live-only causal BPM estimation via FFT autocorrelation over a rolling `chroma_flux()` novelty-history window (same autocorrelation approach `pitch_detect.py`'s YIN already uses, applied to novelty instead of raw audio); re-estimates every `TEMPO_UPDATE_INTERVAL_HOPS` hops, not every hop. `_estimate()`'s best-lag candidate (issue #70) is gated on a confidence ratio (autocorrelation peak over zero-lag energy, `config.TEMPO_MIN_CONFIDENCE`) — below it, the estimate holds at its last value rather than re-locking onto what's essentially noise once the rolling window's content stops being periodic (e.g. a stretch of isolated, irregularly-spaced notes with no consistent beat). Batch tempo uses `librosa.beat.beat_track()` directly instead (`batch_transcribe.py`) — this module is never imported there. |
| `batch_transcribe.py` | (issue #55) The only module permitted to import `librosa` for *offline* transcription. `load_audio()` + `transcribe()` — runs the same per-hop pipeline `analysis_loop()` drives live (mono via `NoteSmoother`, polyphonic via `multipitch.detect()`+`ChordSmoother`), accumulates full-recording-length per-`(pitch_class, octave)` magnitude/onset arrays, then calls `DurationTracker.finalize_noncausal()` per key and `librosa.beat.beat_track()` for tempo. Returns a `TranscriptionResult` (`notes` polyphonic, `mono_notes` monophonic, `bpm`, `hop_seconds`) that `main.run_batch_transcribe()` turns into `TabDisplay` columns. |
| `score_writer.py` | (issue #65, batch-only v1) One of two modules permitted to import `music21` (the other is `score_editor_state.py`, issue #98's second permitted importer, added by the same precedent `rhythm_reanalysis.py` set for `librosa` in issue #77 — see Key design decisions) — mirrors `batch_transcribe.py`'s sole-`librosa`-importer isolation convention, for the same reason (real, one-time import cost with no business on the live/Pi-constrained path). `write_score(result, path, time_signature=...)` writes a `batch_transcribe.TranscriptionResult` to a two-staff-grand-staff MusicXML file: one `<chord/>` group per `onset_hop` for same-staff simultaneous notes (issue #30's schema), each note's `Note.style.color`/`Chord`-member color set via `note_hex_color()` (the same fixed-lightness fifths-hue mapping `tab`'s own `_tab_note_rgb()` uses, so a score reads the same color live and exported), staff assignment via the existing `staff_map.staff_row()` (middle C and above → treble), and a guessed key signature via `guess_key_signature()` (Krumhansl-Schmuckler correlation against `result.chroma_histogram`, `None`/no-signature below `config.KEY_GUESS_CONFIDENCE_THRESHOLD` — same "blank rather than a wrong guess" posture `chord_templates.match()` already uses). No tuplet detection (issue #62 deferred it) — every duration snaps to `duration_class_for_beats()`'s existing plain/dotted power-of-two set. Each part's note *offsets* (not durations) are quantized to the nearest 32nd-note grid before writing — real (non-quantized) onset times otherwise produce a rest `music21` can't express in MusicXML, reproduced and fixed during issue #65's real (non-synthetic) `--write-score` integration test. Wired into `virtualnote transcribe --write-score [PATH]`, `None` by default (no score written, module not even imported) mirroring `pygame`'s only-imported-inside-`run_gui` convention. `QUARTER_LENGTHS` (duration_class → music21 quarterLength), `note_hex_color()`, and `pitch_for()` are public (promoted from private names for issue #98) so `score_editor_state.py` can reuse them rather than duplicating — see that module's entry and docs/DECISIONS.md. |
| `score_editor_state.py` | (issue #98, score editor data layer) The second module permitted to import `music21`, alongside `score_writer.py` — `EditorNote`/`EditorColumn`/`EditorScore` dataclasses are a plain, simple mutable intermediate structure (per #86), keeping music21 confined to this module's own `load_score()`/`save_score()`; no music21 object escapes either function. `new_blank_score()` — `time_signature=(4,4)`, `key_fifths=0`, `tempo_bpm=90.0`, one starting empty (Rest) column, so the cursor always has somewhere to land. `save_score(score, path)` mirrors `score_writer.write_score()`'s two-staff grand-staff / `<chord/>`-grouping / per-note-color structure (reusing its now-public `QUARTER_LENGTHS`/`note_hex_color()`/`pitch_for()`), but walks `score.columns`' own fixed sequence rather than a sparse `onset_hop` map — a Rest column is written as an explicit `music21.note.Rest` on *both* staves (never skipped) so the two parts' offsets stay in lockstep column-for-column, which `load_score()` relies on to merge them back into one flat list keyed by shared offset. Also writes a real `music21.tempo.MetronomeMark(number=score.tempo_bpm)` and `music21.key.KeySignature(score.key_fifths)` — both new relative to `write_score()`, which wrote neither. `load_score(path)` is the reverse: `music21.converter.parse()`, then merges both parts' notes/chords/rests back into one `columns` list by offset, reconstructing each `duration_class` via `duration_tracker.duration_class_for_beats()` against the parsed quarterLength; a file with no tempo marking (true of every file `write_score()` has ever produced) defaults `tempo_bpm` to 90.0, and no time signature/key signature present similarly falls back to `config.DEFAULT_TIME_SIGNATURE`/0 sharps, rather than crashing or guessing. `EditHistory` — bounded (`config.EDITOR_UNDO_MAX_DEPTH`, 50) multi-level undo/redo over plain `EditorScore` snapshots (`copy.deepcopy`, cheap with no music21 graph attached): `.record(score)` pushes the state *before* a mutation and clears the redo stack; `.undo(current)`/`.redo(current)` return the previous/next snapshot or `None` at either bound. Known limitation: a score whose total duration doesn't land on a whole number of measures gets tie-split/padded by music21's own `makeMeasures()` during `save_score()`'s write, which `load_score()` then sees as extra/split columns — a real MusicXML-format constraint, not a bug in this module (see docs/DECISIONS.md). No interactive terminal UI here — that lives in `score_editor_display.py`/`main.run_score_editor()` below, a separate layer built on top of this one. |
| `score_editor_display.py` | (issue #98, score editor UI layer) The main editor view: renders `EditorScore` as a fixed, cursor-addressable grand staff, extending `terminal_tab_display.py`'s rendering approach (notehead glyph, accidental markers, fifths-order coloring) into a *loaded-once, random-access* buffer instead of a live-scrolling one — see `CONTEXT.md`'s Score editor glossary for the "Column (editor sense)" distinction. Cursor = `(column_index, staff_row)`, reusing `staff_map.py`'s existing row space; `pitch_at_row(row, key_fifths=0)` is this module's one new piece of staff-row math, the inverse of `staff_map.staff_row()` (a row -> the note there) — by default the natural, but (issue #98 follow-up, direct user feedback) spelled per the active key signature when `key_fifths` is passed, via `staff_map.key_signature_accidental()`; needed since the cursor can sit on any row, occupied or not. `_legend_letter()` applies that same key-aware spelling to the left legend's row letters (e.g. G major's F-row legend reads `F♯`). Pure mutation functions `main.run_score_editor()` drives the keybinds through: `toggle_note_at_cursor()` (`note_toggle` — can now empty a column to zero notes, same as removing any other note; the old "refuses to remove the last note" rule was reversed after hands-on feedback, see docs/DECISIONS.md), `transpose_note_at_cursor()` (Shift+Up/Down, hardcoded — see `main.py`'s entry), `cycle_duration()` (`duration_shorten`/`lengthen`, steps `duration_tracker.DURATION_CLASS_ORDER`, clamped not wrapped), `clear_to_rest()` (`clear_to_rest`), `insert_column_at()`/`delete_column_at()` (`insert_column`/`delete_column`, the latter refusing to delete an editor's last remaining column), `cycle_zoom()` (`zoom_cycle`, render-only — `ZOOM_LEVELS` is a 4-step "notehead -> +letter -> +octave -> +duration" progression, each its own column width), `chord_name_for_column()` (`chords_only_toggle`'s lead-sheet view — builds a synthetic one-hot chroma vector directly from the column's own pitch classes and feeds `chord_templates.match()`, since there's no live audio spectrum to run `chroma.fold()` against here; see docs/DECISIONS.md), and `visible_column_range()` (keeps the cursor inside `render()`'s horizontally-scrolled viewport). `render()` itself is smoke-tested manually only, same convention as `terminal_tab_display.TabDisplay.render()`. |
| `chord_builder_display.py` | (issue #98) The Chord builder screen — opened by Enter on a column, closed by `chord_builder_exit`. Five Reels (root/quality/3rd/5th/7th, `BUILDER_SLOTS`), reusing `prototypes/score-editor-cursor-concept/`'s reel *mechanics* (per #87/#88: that prototype's keybind vocabulary was rejected, not its reel math) reimplemented against `EditorNote`/`EditorColumn`. `ROOT_REEL` orders by `color_map.fifths_index()`; `THIRD_OPTIONS`/`FIFTH_OPTIONS`/`SEVENTH_OPTIONS` are small (token, interval, label) tables (index 0 always "(none)"); `QUALITY_PRESETS`/`QUALITY_ALIASES` are the quality reel's fast-preset shortcut (fills the three degree reels in one move, never touches root). `BuilderState` is this screen's own working copy — edits land there, not the real column, until `chord_builder_exit` commits `notes_from_state()`'s result (mirrors the prototype's "`b` always means I'm done, not a discardable draft" convention). `step_root_typeahead()` (a typed uppercase letter jumps the root reel immediately; `b`/`#` right after nudges it a semitone — exact-case letter matching, since lowercase `b` is reserved for the flat accidental, see docs/DECISIONS.md) and `step_alias_typeahead()`/`force_commit_alias()` (the quality/degree reels' auto-commit-when-unambiguous typed-buffer matching) are this module's other pure, unit-tested logic; `render()` is smoke-tested manually only. Not this app's real ~360-template `chord_templates.py` dictionary — that only ever goes chroma -> name; this is the reverse (name -> notes), a small hand-built table same as the prototype's own. |
| `score_properties_display.py` | (issue #98; revised by a post-#98 hands-on-feedback follow-up, reversing #90's original call — see docs/DECISIONS.md) Pure logic for the score-level properties (time signature/key signature/tempo), now edited *inline* in the main editor view's status line (`main.py`'s `_property_field_texts()`/`_handle_property_key()`/`_parse_property_input()`) rather than through a separate screen — this module no longer owns any screen/render loop, only: `PROPERTY_SLOTS` (field order), `spin_time_signature()` (steps `TIME_SIGNATURE_OPTIONS`' small fixed set), `spin_key_fifths()` (+/-1 around the circle of fifths, clamped at +/-7 matching `music21.key.KeySignature`'s valid range), `spin_tempo()` (+/- `TEMPO_STEP_BPM` clamped into `[TEMPO_MIN_BPM, TEMPO_MAX_BPM]`, same clamp-not-wrap convention `settings_display.parse_numeric_input()` uses), and `key_fifths_label()` (the status-line key-signature label). Every function here is pure and unit-tested — there's no render loop left to smoke-test manually. |
| `score_editor_picker.py` | (issue #98) The score editor's live-menu entry point, reached via `shell.py`'s dedicated `"edit"` branch (not `_NON_SESSION_SCREENS`, since `main.run_score_editor()`'s return value has to be handled like a real session tool's — see docs/DECISIONS.md). `score_file_paths()` (flat `*.musicxml`/`*.xml` glob, mirrors `stats_display.session_log_paths()`) + `build_menu_entries()` build the picker list (every existing file, then a fixed trailing "New score..." row); `run_score_editor_picker()` drives the interactive Up/Down/Enter/`|`-cancel loop, returning a `Selection` (`path`, plus a `score` that is non-`None` only for an imported recording) or `None`. (map #99, ticket #122) The list also carries every `session_log_*.jsonl` recording next to `main.py` (`log_file_paths()`, delegating to `stats_display.session_log_paths()`), suffixed `LOG_LABEL_SUFFIX` so a log is visibly not a score before Enter; `entry_kind()` names the three row kinds. Picking one opens `choose_grid()` (Left/Right steps `log_import.GRID_NAMES`, Enter imports, Esc back to the list) and hands the editor an already-quantized `EditorScore` at an unwritten `.musicxml` sibling path — so a wrong grid costs one quit-without-saving, not a file to clean up. Selecting "New score..." opens `capture_filename()` — a small raw-ANSI backspace-editable keystroke buffer built on `main.RawKeys` (not `settings_display.py`'s scoped `blessed` exception — see docs/DECISIONS.md for why a second screen reaching for `blessed` would widen that carve-out); `resolve_new_score_path()` appends `.musicxml` to a bare filename and falls back to `DEFAULT_NEW_SCORE_NAME` for a blank one. |
| `score_audition.py` | (map #99, ticket #120, decision #108) The score editor's audition/piano-mode/playback/metronome logic — pure, side-effect-free, unit-tested end to end, with every terminal/engine side effect left in `main.run_score_editor()` (the same split `rhythm_reanalysis.recompute()` vs. `_handle_reanalysis_key()` already uses). `PIANO_LOWER_ROW`/`PIANO_UPPER_ROW` (`zsxdcvgbhnjm`/`q2w3er5t6y7u`) + `pitch_for_key()`/`clamp_base_octave()` — the two-octave tracker keyboard, and **the one definition of it in this codebase**: `synth_layout.py` (ticket #119) derives the standalone synth tool's note pitches from `PIANO_KEY_SEMITONES` rather than restating the rows, adding only the display geometry the score editor has no use for (see docs/DECISIONS.md for where that seam sits and why it points this direction). `EDIT_MODE`/`PIANO_MODE`/`toggle_mode()`/`is_piano_note_event()` — the mode machine; `is_piano_note_event()` requires *no* modifier held (Caps/Num Lock excepted), which is what keeps `m` (a B) distinguishable from `M` (the metronome). `PianoEntry` — the press-together-means-chord / press-in-sequence-means-successive-columns rule, driven by kitty key releases (#101/#118); `advance_between_groups=False` is #108's degraded path for a terminal reporting none (every press joins the current column, arrows move on). `audition_targets()` — Left/Right sounds the whole column, Up/Down only a note genuinely on the row moved onto. `build_schedule()`/`ScheduledColumn`/`schedule_slice()`/`playhead_index()`/`due_entries()` — playback expressed in *absolute* beats from the score's first column (not from wherever playback began), which is what keeps the metronome's bar grid on the score's real barlines when starting mid-bar; `beats_to_seconds()`/`seconds_to_beats()`/`duration_seconds()` apply the tempo at the edge. `playback_range()` — #121's own "marked range if set, else what follows the cursor" rule, plus the one editor-specific twist (a cursor *inside* a marked region starts there, since picking a marked section up from the middle is a thing an editor is asked for and a scrollback buffer isn't). `beat_grid()`/`metronome_clicks()`/`due_clicks()` — the click grid, on the note value the time signature's denominator names. `sound_notes()`/`sound_metronome_click()` — the only audio-touching functions, thin enough to unit-test against a fake engine, and no-ops on a `None` engine (the whole degradation story for a machine with no audio device or no `[synth]` extra). |
| `rhythm_reanalysis.py` | (issue #77) The other module permitted to import `librosa`, for the `tab` view's live `R`-key non-causal rhythm re-analysis — see Key design decisions for why this is an accepted second exception to "librosa lives only in `batch_transcribe.py`", not a reopening of that rule. `HopRecord` (namedtuple: `hop_index`, `mono`, `chord_notes`, `chroma_novelty`) is the buffered-per-hop shape `main.ReanalysisBuffer` accumulates and `recompute()` (the pure, unit-tested engine) consumes — a snapshot list of these, plus `hop_seconds`/`beats_per_bar`, in; a `RecomputeResult` (`corrected_notes`, `barline_times`, `bpm_estimate`, `window_start_time`, `window_end_time`) out, or `None` if the buffer was empty. Reconstructs per-key magnitude/onset arrays from the flat `HopRecord` sequence mirroring `batch_transcribe.transcribe()`'s own per-hop loop almost exactly, then calls the same `DurationTracker.finalize_noncausal()`/`librosa.beat.beat_track()` batch already uses — the one structural difference is mapping local buffer positions back to real hop timestamps via each `HopRecord`'s own `hop_index`, since a rolling window's contents aren't 0-based/contiguous the way batch's whole-recording arrays are. |
| `tab_playback.py` | (map #99, ticket #121, decision #109) The pure half of the `tab` view's frozen-buffer playback: `select_columns()` (scope — the `[`/`]` marked range if set, else exactly the entries `TabDisplay.visible_entries()` reports, with barlines and silence columns dropped), `playable_notes()`, `note_duration_seconds()` (a note's measured `duration_class` — or `DEFAULT_DURATION_CLASS` if it never finalized, matching what `render()` draws for it — converted against a tempo, clamped into `config.TAB_PLAYBACK_MIN/MAX_NOTE_SECONDS`), `build_schedule()` (columns → a flat, time-ordered list of `PlaybackNote`s: onsets re-based off the first column's own recorded timestamp so real gaps survive, every note of a chord column sharing that onset with its own duration, one fixed `config.TAB_PLAYBACK_VELOCITY` since this pipeline never measured a per-note attack strength) and `schedule_duration()`. No thread, no audio device, no terminal — `main.py`'s `_handle_playback_key()`/`_playback_worker()`/`_wait_until()` own the timing thread and the `sound_engine.SoundEngine` calls, the same "pure logic unit-tested, real I/O and threading smoke-tested" split `rhythm_reanalysis.recompute()` already has against its own `R`-key wiring. |
| `session_recorder.py` | `SessionRecorder` — opt-in live session log, toggled by the `S` keybind (default off, no disk writes unless armed). `.record_hop()` is called unconditionally, every hop, directly from `analysis_loop()` — the same per-hop placement `ReanalysisBuffer.append()` uses (issue #77), not the render thread, since `result_queue` is single-slot/overwrite-on-full and would silently drop finalized notes a render-thread-side recorder tried to watch. Appends one JSON line per finalized note (mono, paired with the *previous* hop's `pitch_class`/`octave` per the same DurationTracker-was-one-hop-behind pairing `run_terminal_tab()` already follows, or chord-tone via each `note_stack` entry's own `duration_hops`) to a plain-text `session_log_<timestamp>.jsonl` file next to `main.py` — `t` is each note's onset time (`onset_hop * hop_seconds`), not its finalization time, matching `batch_transcribe.NoteEvent.onset_time`'s own convention. One instance lives on `SessionState` for the process's whole life, so recording state survives `|` back-to-menu tool switches; `SessionState.stop()` closes it unconditionally on process exit even if still armed. Barlines aren't captured in v1 (that bookkeeping is `tab`-view-only, render-thread-side — see main.py's `_hop_beats()`/beat-accumulator). | (map #99, ticket #122) `.note_on(key, pc, octave, velocity=, patch=, pad=)`/`.note_off(key)` are the *played*-note half, called by `main.run_synth_tool()`'s own `_note_on`/`_note_off` (so the log records what was heard, including a note ended by a layout switch, by leaving the tool, or by the no-key-release fallback's own `expire()`): same file, same schema, four new **optional** fields — `source` (`"played"`; absent means `"detected"`, so every pre-existing log stays valid and `virtualnote replay` reads a synth recording unchanged), `velocity` (MIDI 0-127), `patch`, `pad`. Wall-clock timing (`time.perf_counter()`, injectable via `now=` for testing), `t` measured from the take's first played note, `duration_hops`/`bpm_estimate` written `null` (meaningless with no hop pipeline — decision #110), `duration_class` derived against `config.PLAYED_NOTE_REFERENCE_BPM` with the raw `duration_seconds` beside it. `close()` finalizes a still-held note rather than dropping it.
| `session_player.py` | Pure log-reading/grouping logic behind `virtualnote replay <file>` (session recording + playback, the real port of `prototypes/session-log-and-practice-mode/session_player.py`'s reading half). `load_events()` reads a `.jsonl` session log back, sorted by `t`; `group_columns()` groups it into a time-ordered list of `("notes", t, [event, ...])`/`("barline", t, None)` tuples one `TabDisplay` column each — "note" events sharing the exact same `t` (a chord's tones) become one column, a same-`t` barline sorts just after its note column, not before. No `TabDisplay`/terminal I/O in this module at all — `main.run_replay_session()` owns that side, same "pure logic unit-tested, real I/O smoke-tested" split as `rhythm_reanalysis.recompute()` vs. its own `R`-key wiring. |
| `log_import.py` | (map #99, ticket #122, decision #110) Session log -> score editor, **quantized on the way in**. Decision #110's "capture raw, quantize at import": the log holds real unrounded timing, and this is where the rounding finally happens — against a selectable grid, re-runnable at another resolution against the same untouched log. Distinct from `score_writer.py`'s existing 32nd-note *offset* quantization, which is a MusicXML expressibility constraint, not a musical judgement. Pure half (`GRID_CHOICES`/`grid_beats()`/`cycle_grid()`/`tempo_from_events()`/`quantize_columns()`) takes plain event dicts and returns plain `ImportedColumn`s with no music21; `score_from_events()`/`import_log()` wrap that into a real `score_editor_state.EditorScore` behind a local import. Two `EditorScore` properties shape the output: a column's duration *is* when the next column starts (so a note that decayed early becomes a note column plus a **Rest** column, which is how a gap survives), and only standard note values exist (a five-sixteenth span snaps to the nearest name — no tie-splitting, and deliberately no triplet grids since `DURATION_CLASS_ORDER` has no tuplet values to write them as). Notes on the same grid step become one column, `session_player.group_columns()`'s own same-`t` rule applied to a grid. `default_score_path()` names a `.musicxml` sibling — nothing is written until the editor's own `save`. |
| `playback.py` | (map #24, decision #32) Offline score playback: NumPy oscillator+ADSR synth reusing `sounddevice.OutputStream` the way `audio_capture.py` reuses `InputStream` — no new dependency. `note_frequency()` (standard MIDI tuning, A4=440Hz), `_adsr_envelope()` (linear-segment attack/decay/sustain/release, scaled down proportionally for a note shorter than attack+decay+release so it still reaches zero by its own end rather than clipping mid-release), `synthesize_note()` (a small fixed harmonic-stack waveform — fundamental+2nd+3rd partials, `config.PLAYBACK_HARMONIC_WEIGHTS` — shaped by the envelope), and `render_offline()`/`play_offline()` (whole-buffer pre-render, additively mixed + `tanh` soft-clipped, sample-accurate by construction because timing is a buffer-index computation — used by `virtualnote transcribe --play`, since a whole `TranscriptionResult` already exists before playback starts); `render_offline(effects=...)` (ticket #114) runs an `effects.Effect` once over the summed mix before the clip — the same shared-bus placement `SoundEngine._callback()` uses live — and extends the buffer by `effects.tail_seconds()` so a delay rings out). #32's second mode, the callback-mixing `LiveScheduler`, is **gone** (map #99, decision #105): superseded by `sound_engine.py`'s note-on/note-off voice manager, with its one caller (`virtualnote replay --play`) moved over — two voice-mixing callbacks in one process would compete for the same device and the same GIL prototype #100 measured as the binding constraint, and `trigger_note()`'s duration-carrying shape is exactly the second primitive #105 ruled out. Never imported by `analysis_loop()`/`SessionState` — playback is strictly opt-in offline-time, same isolation convention as `librosa`/`music21`, though `sounddevice` itself is already a live-path dependency so this is about usage, not import cost. |
| `patch_format.py` | (map #99, decision #106 + its #107 velocity-layer addendum; built by issue #115) The sound engine's **Patch** format and model — one hand-editable TOML file per patch under `~/.config/note-color/patches/` (`patches_dir()`/`samples_dir()`, XDG-aware, deliberately *not* inside `config.toml`), declaring `engine = "synth"|"sampler"|"sf2"` so loading a patch stays one code path for all three engines. Dataclass model (`Patch`, `Oscillator`, `Noise`, `Filter`, `Envelope` (DAHDSR), `Lfo`, `VoiceSettings`, `EffectSpec`, `Zone`, `Sf2Selection`) over issue #103's `[osc1]`/`[osc2]`/`[noise]`/`[filter]`/`[amp_env]`/`[filter_env]`/`[lfo]`/`[voice]`/`[[effects]]`/`[[zones]]`/`[sf2]` sections; the module docstring is the schema reference (every field, its default, its range, and the standard MIDI CC number where one exists — `STANDARD_MIDI_CC`, documented but deliberately never *stored* in a patch, since a CC describes the controller, not the sound). `patch_from_toml()`/`patch_to_toml()` are the pure model<->mapping halves; `load_patch()`/`save_patch()`/`patch_paths()` add file I/O (flat sorted `*.toml` glob, mirroring `score_editor_picker.score_file_paths()`). No version field and `config_store.py`'s exact additive-and-degrade posture, three layers deep: `parse_patch_text()` recovers the longest leading prefix of a malformed file that *is* valid TOML (so one typo costs that entry, not the kit), every field coercion degrades to its default rather than raising (out-of-range numbers clamp, unknown enums fall back), and an absent/unreadable file loads as an all-defaults patch named after itself. `select_zone(zones, key, velocity)` is the sampler's mapping lookup — key range **and** velocity band, narrowest match wins, nearest band rather than silence when a velocity lands in an unmapped gap (#107's addendum), deterministic on a tie; `choked_zones()` covers open/closed hi-hat. Samples (and an SF2 soundfont) are referenced by **bare name** and basename'd on both read and write, so a patch stays shareable and can't resolve outside `samples_dir()`; `zone_available()`/`missing_samples()` report an absent one without ever raising. Renders no audio and imports no audio library — #113/#114/#116/#117 each consume a `Patch` produced here. |
| `sound_engine.py` | (map #99, decision #105, ticket #112) The sound engine's core seam — the event model, the two Protocols, the voice manager, and the one process-wide output-stream owner. Nothing here makes a sound: it's the socket map #99's three engines (subtractive synth #113, sampler #116, SF2 #117) and four sources (QWERTY keys, editor audition, frozen-buffer playback, a future MIDI device) all plug into. `NoteOn` (pitch as a MIDI note number, velocity 0..1, channel, patch) is the **only** vocabulary alongside note-off — no duration-carrying primitive, deliberately (#105: every later feature — stealing, sustain pedal, aftertouch — would otherwise need reasoning about twice); `from_pitch_class()`/`midi_pitch()`/`pitch_class_octave()`/`frequency_for()` convert at the edges to this repo's own pitch-class/octave terms, same tuning `playback.note_frequency()` uses. `Engine`/`Voice` `typing.Protocol`s mirror `detection_backends.py`'s seam convention (`Engine`: note-on → voice; `Voice`: render N samples additively, accept a note-off, report `released`/`finished`/`amplitude()`) — a Protocol rather than one concrete class because FluidSynth owns its voices internally (#102). `VoiceManager` enforces a **hard** polyphony cap (#100: the driver's ring buffer hides overruns until it has already xrun, so a load-driven policy's signal arrives too late), stealing via the pure, separately-tested `select_steal_index()` — quietest among the already-released, oldest breaking ties, a still-held note (oldest first) only when every voice is held — and **never** refusing a new note. `polyphony_for()` reads the two `[preferences]` budgets live through `config_store`. `SoundEngine` owns one `sounddevice.OutputStream`, with an idempotent lazy `ensure_started()` mirroring `SessionState`'s own lifecycle (issue #40) for output; `schedule_note_off(voice_id, delay_seconds)` is caller-side sugar for a duration-knowing caller, resolved against the audio callback's own frame clock (no timer thread, accurate to one block) rather than being a second primitive in the voice model. Block size stays `config.PLAYBACK_BLOCK_SIZE` (512): #100 measured PipeWire reporting an identical 34.8ms stream latency at 128/256/512. Everything except `ensure_started()`/`stop()` is unit-tested with no audio device (the callback is called directly with a NumPy buffer); real-device behavior is smoke-tested by `scripts/sound_engine_smoke.py`, which reports PortAudio's own xrun counters. Owns one `effects.EffectsChain` (ticket #114, empty by default) applied in `_callback()` between `render_block()` and the `tanh` clip — `set_effects(chain)` prepares and swaps it in by one attribute assignment (next-block-boundary, lock-free); `stop()` resets it, `all_notes_off()` deliberately leaves its tail ringing. Everything except `ensure_started()`/`stop()` is unit-tested with no audio device (the callback is called directly with a NumPy buffer); real-device behavior is smoke-tested by `scripts/sound_engine_smoke.py`, which reports PortAudio's own xrun counters. |
| `synth_engine.py` | (map #99, ticket #113, implementing research #103's signal path and decision #111's `[synth]` extra) The **subtractive synth engine** — mip-mapped wavetable oscillators ×2 plus noise, a 2-pole state-variable filter run by `scipy.signal.lfilter`, DAHDSR envelopes and one LFO, assembled into a concrete `sound_engine.Engine`/`Voice` pair (`SynthEngine`/`SynthVoice`). It replaces `tone_engine.py` as `sound_engine._default_engine()`'s choice — the `Engine` Protocol is exactly what made that a one-line swap; `ToneEngine` stays available to any caller naming it explicitly. Signal path: osc1 + osc2 + noise → mix → SVF → amp env × velocity × volume, with the filter env, the LFO, key tracking and velocity all modulating cutoff. **Three rates**, as #103 found is conventional: event rate (owned by `VoiceManager`), *control rate* — one value per `config.SYNTH_CONTROL_SUB_BLOCK` (64) samples for everything that becomes a filter coefficient or an oscillator frequency, #103's measured price knee and FluidSynth's own `FLUID_BUFSIZE` — and *audio rate* for the amp envelope and tremolo, which are plain elementwise gains. `build_tables()`/`tables_for()` build one band-limited table per octave by **inverse FFT of an exact harmonic series** (~1ms for a whole band set, cached per (waveform, sample rate) so no voice ever builds one at note-on), deliberately un-normalized; `pulse_from_saw()` makes a square of arbitrary continuous `pulse_width` from two saw reads, which is why a square has no table of its own. `svf_coefficients()` is the bilinear transform of the analog SVF with a `tan` prewarp, yielding lp/hp/bp from one shared denominator (measured -3.010dB exactly at cutoff; the **nonlinear Moog ladder is unavailable** — its saturation is inside the feedback loop, so `lfilter` cannot run it at all). `DahdsrEnvelope` is resumable (a note-off can arrive mid-block) and exposes `block()` (audio rate) and `advance()` (control rate) over one shared segment walk so the two can never drift; `Lfo` carries its phase and per-voice delay across blocks. `SynthVoice._sub_block_lengths()` **carries the control sub-block grid across `render()` calls** rather than restarting it per block — identity at this app's constant 512-sample block, but what keeps a ragged or short final block from re-timing every coefficient update against the block boundary. **SciPy is isolated here** exactly as `librosa` is to `batch_transcribe.py` and `music21` to `score_writer.py`: imported lazily inside `_signal()`, never at module scope, so importing this module costs nothing on an install that never asked for a synth; `require_scipy()` raises `SynthUnavailable` carrying the install line and `SynthEngine()` calls it in its constructor, so the synth **refuses to open rather than opening filterless** (#111 — that is `synthplayer`'s failure mode). `default_patch()` is the init sound, chosen so every stage of the signal path is audible without switching anything on. Verified numerically with the machine muted (see docs/DECISIONS.md); nothing here has been listened to. |
| `tone_engine.py` | (map #99, ticket #112) The interim concrete `Engine`/`Voice` behind `sound_engine.py`'s seam: map #24's existing harmonic-stack+ADSR instrument (`playback.synthesize_note()`, decision #32) reshaped from "synthesize a whole note of known duration" into a block-rendered voice with a resumable envelope — the same relationship `detection_backends.YinBackend` has to `pitch_detect.detect_pitch()`, so the seam ships with a real implementation behind it rather than an abstraction with nothing plugged in. `ToneVoice` carries oscillator phase, envelope stage and level, and velocity across blocks (exactly the per-voice state #103 identified as having to survive between blocks); `note_off()` starts the release from wherever the envelope actually was, so the fade always takes exactly `PLAYBACK_RELEASE_SECONDS` whether the note was cut in attack, decay or sustain, and is idempotent. Identical `config.PLAYBACK_HARMONIC_WEIGHTS`/ADSR constants, so it is the same instrument the offline path renders (asserted spectrally in `tests/test_tone_engine.py`). Ticket #113's subtractive synth replaces it as `sound_engine._default_engine()`'s pick; pure NumPy until then (SciPy only arrives with #113's resonant filter, behind its own `[synth]` extra per #111). |
| `sampler.py` | (map #99, ticket #116) The sampler engine — the second concrete `Engine`/`Voice` pair behind `sound_engine.py`'s seam, and what makes both "import my own samples" and the drum pad real (map #99's standing decision: they are the same feature, since a **kit** is just a sampler patch whose zones are one key wide — there is deliberately no separate kit code path anywhere here). `SamplerEngine.note_on()` picks the zone via `patch_format.select_zone()` (key **and** velocity band, `midi_velocity()` mapping the event's 0..1 to the schema's 0..127 so full velocity reaches a `low_vel = 96` layer), loads its sample through a `SampleCache` (decoded WAVs keyed by bare name, mtime+size-invalidated the same "stat it, don't watch it" way `config_store` hot-reloads), cuts whatever `patch_format.choked_zones()` names, and returns a `SamplerVoice` — or a `SilentVoice` (finished from birth, costs no polyphony) when no zone matches or the sample is missing/undecodable, so a kit with a missing sample loads and plays the rest normally, never a crash (decision #106); `unavailable_samples()` is what a pad UI greys out. `SamplerVoice` pitch-shifts by playback-rate resampling from the zone's `root_key` (`playback_ratio()` — `2**(semitones/12)` times `sample_rate/engine_rate`, so a 22050/48000Hz source folds into the same one linear-interpolated read with no separate resampling pass), honours the WAV's own `smpl`-chunk loop (interpolating *across* the seam), applies a ~2ms click-suppression attack (`SAMPLER_ATTACK_SECONDS`), and treats note-off two ways: a looping zone fades over `SAMPLER_RELEASE_SECONDS`, a one-shot (no loop — every drum) ignores it and plays to its natural end, which is what makes a pad work from a short key tap. `choke()` is a `SAMPLER_CHOKE_SECONDS` (6ms) fade, not a hard cut. Choke groups live in this engine's own active-voice list rather than in `VoiceManager`, which deliberately knows nothing about patches. `velocity_amplitude()`/`gain_to_linear()` (dB) are the pure gain helpers. Verified numerically in `tests/test_sampler.py` — FFT-measured pitch ratio, loop-seam continuity, choke frame counts, RMS gain — with no audio device, since the dev machine is muted. |
| `wav_io.py` | (map #99, ticket #116) The sampler's file side: `read_wav()`/`read_wav_bytes()` decode a WAV into a `Sample` (mono float32 at its **own** rate, plus `smpl`-chunk loop points) via a small hand-rolled RIFF chunk walk (`riff_chunks()`/`parse_fmt()`/`parse_smpl_loop()`) rather than the stdlib `wave` module — `wave` refuses IEEE-float WAVs outright and can't reach the `smpl` chunk at all, both load-bearing for a sampler; still zero new dependencies (`soundfile` is only present transitively via the `[batch]` extra), and cross-checked in tests against files `wave` itself wrote. Handles PCM 8/16/24/32-bit, float 32/64, `WAVE_FORMAT_EXTENSIBLE`, stereo (averaged to mono — the engine is mono today), and truncated files (whole frames only, never raises: a broken sample behaves exactly like a missing one). No resampling on load — `sampler.playback_ratio()` folds the rate mismatch into the pitch-shift read. `import_sample()` is decision #106's copy-into-`samples_dir()`-and-return-a-bare-name; identical bytes under an existing name reuse it, different bytes get `name_1.wav` rather than clobbering a file other patches may reference; raises `SampleImportError` (the one raising path — import is a deliberate user action). `write_wav()` (16-bit PCM, optional `smpl` loop) exists so tests synthesize their fixtures; `imported_samples()` lists the directory for a picker. |
| `synth_layout.py` | (map #99, ticket #119, decision #107) The synth tool's four **layouts**: which physical QWERTY key plays what, and which display cell it sits in. **The tracker keyboard itself is not defined here** — `_semitone_of()` reads `score_audition.PIANO_KEY_SEMITONES` (ticket #120), so the score editor's piano mode and the synth tool cannot drift into "the same keyboard, nearly"; what this module adds is the part `score_audition.py` has no use for, **geometry** (it never draws the keyboard; the synth's whole input layer is a picture of one). Row templates (`" s d   g h j "` above `"z x c v b n m"`) are written out literally so the physical shape is visible in the source, and carry *only* column positions. `KeySlot` (key/kind/value/row/col; `midi_key()` transposes a note with the live octave shift but never a pad — a kick is a kick) / `Layout` (`slot_for()`, `grid()`, `is_dual`, `rebind()` which keeps a slot's row+col so the layer never shifts mid-edit) / the three built-in constructors + `available_layouts()`/`cycle_layout()` (Tab's arithmetic) / `pad_midi_key()` (pad index → the MIDI key a one-key-wide kit zone lives at, 36 upward per General MIDI; pads numbered **bottom row first** like hardware) / custom-layout TOML I/O (`load_layout()`/`save_layout()`/`layout_paths()`, their own `~/.config/note-color/layouts/` directory, independent of patches per #107 point 4) / `slugify()` (conservative enough that a typed name can't escape its directory) / `sample_hue_step()` (a stable byte sum, deliberately not the per-process-randomised `hash()`). |
| `synth_params.py` | (map #99, ticket #119, decision #107 point 5) The synth tool's **parameter panel model**: which of a `patch_format.Patch`'s ~40 scalars are editable, in what order, and what Left/Right does to each. `ParamSpec` reads/writes by one `getattr`/`setattr` pair (the patch dataclasses are already the schema); sections are declared to mirror the TOML file's own `[osc1]`/`[filter]`/`[amp_env]` tables one-for-one, so the panel and a hand-edited patch file read alike. `sections_for()` picks by the patch's own `engine` — a sampler kit gets `[voice]` alone, since its sound lives in its zones (files, changed by importing a sample onto a pad) rather than a bank of knobs. `step_value()` is the interaction: **log-scaled parameters step by ratio, not by amount** (a fixed-Hz cutoff sweep crawls low and leaps high — one press is a semitone everywhere instead), numbers clamp while choice lists wrap (the same split `settings_display.parse_numeric_input()` and the Chord builder's reels already follow), and a log parameter can both leave zero (a ratio can't lift off it, so the first press jumps to `SYNTH_PARAM_LOG_FLOOR`) and land back exactly on it ("no attack at all" is a real setting). `move_selection()`/`visible_range()`/`format_value()`/`note_name()` round it out. All pure, all unit-tested; the panel's screen layout lives in `synth_display.py`. |
| `synth_tool.py` | (map #99, ticket #119, decision #107) The synth tool's non-rendering **runtime**. `resolve_action()` is the key map, decided by elimination from the one invariant the tool rests on — **every plain letter and number plays a note, always** (#107 point 5): arrows drive parameters, `Tab` cycles layouts (nearly every other key plays in *some* layout), and every remaining command is `Shift`+key, including the octave shift on `Shift`+Up/Down. Those `Shift` binds are deliberately **not** remappable via `[keybinds]`, unlike this app's other 22 — a remap onto a plain letter would silently break the always-plays invariant, and `settings_display.is_valid_remap_key()` has no way to express "not any letter, ever" (see docs/DECISIONS.md). `ChannelRouter` — a `sound_engine.Engine` dispatching by MIDI channel (pads on 9, keys on 0), which makes layout 2's "a kit and a synth patch at once" a two-line routing rule rather than a second sound path, and is exactly the shape a MIDI controller plugs into later; a missing engine yields `sampler.SilentVoice`, never `None`. `polyphony_for_layout()` — #107's implementation note made concrete: a dual layout feeds two engines from one cap, so the risk is *starvation* (a drum hit finding every slot held by sustained synth notes), and `polyphony_synth_dual` is the margin. `Overlay` + `patch_entries()`/`sample_entries()` — the inline load/save/import overlays (#107 point 6, never separate screens). `assign_sample_to_pad()`/`zone_for_pad()` — the pad grid as a *view* onto a sampler kit's one-key-wide zones, not a second model. `SynthToolState` — everything the tool remembers between keystrokes, with the audio device and terminal deliberately left out so every transition is testable; `panel_patch()` decides which patch the panel edits, `set_patch()` routes a load by the patch's own engine (#106's "one file kind" paying off: one browser, one Enter, no "which slot?" question). | (ticket #122) `SHIFT_ACTIONS["S"]` is `record_toggle`, the recording arm — non-remappable like every other `Shift` binding here, deliberately contradicting decision #110's "remappable like every other keybind": a remap onto a plain letter would break this tool's always-plays invariant, which `settings_display.is_valid_remap_key()` cannot express. `rec=`'s meaning stays identical across all four views, which is what #110 was actually asking for (see docs/DECISIONS.md).
| `synth_display.py` | (map #99, ticket #119, decision #107 point 1) The synth tool's screen: **parameter panel above, always-visible input layer below**. The input layer shows *exactly the keys the current layout plays*, in their physical arrangement, so a player can see which key is which note without counting — note keys tinted by `color_map.fifths_index()` (a C here is the same colour as a detected C in every other view), pads by their assigned sample instead (`synth_layout.sample_hue_step()`; a pad has no pitch to be honest about). Only lightness moves between rest and sounding, exactly as `tab`'s age-fade treats a note's colour. `KeyLights` gives **each key its own `animation.ColorAnimator`** — several keys sound at once, and one shared animator would smear simultaneous notes into one average colour (the same reason chord mode keys `fill`'s band animators per note); `dt` is injected and no clock is read, so a test steps it deterministically. `slot_hue_step()`/`slot_rgb()`/`slot_caption()`/`input_layer_lines()`/`panel_lines()`/`overlay_lines()` are pure and unit-tested — overlays draw over the *panel* only, never the input layer, which is the whole difference between an inline overlay and the separate screen this project's hands-on feedback already rejected once. `render()` itself is smoke-tested manually, same convention as every `run_terminal_*` view. |

| `effects.py` | (map #99, ticket #114, implementing research #104) The shared effects bus: the `Effect` `typing.Protocol` (`prepare(sample_rate, block_size)`/`process(block)`/`reset()`, mirroring `detection_backends.py`'s seam convention with the algorithm-specific config captured at construction), the two effects that ship first — `Delay` (circular float32 buffer, persistent write index, block-vectorized reads, feedback clamped below 1, linear dry/wet mix, optional one-*zero* feedback-path damping off by default) and `Chorus` (the same buffer read at an LFO-modulated **fractional** index with linear interpolation, `voices` taps at spread phases, LFO phase **derived from an absolute int64 sample counter** so it carries across blocks *and* is chunking-independent) — and `EffectsChain`, itself an `Effect` so chains nest, the identity when empty. Every effect sub-chunks its input to no longer than its own shortest delay, so read-before-write holds by construction even for a feedback delay shorter than one block, and any partitioning gives the same answer: **block-wise processing is bit-identical to one-shot processing** (`np.array_equal`, asserted in `tests/test_effects.py` across block sizes), the ticket's acceptance test. Artifact floor asserted by FFT: a fully-wet chorus over a sine stays below -80 dBc (measures -88.9, #104's own figure). One **shared bus, not per-voice** (#104: both effects are linear, so per-voice routing is the identical signal at N× the cost and loses a delay's tail on release) — attaches in `sound_engine.SoundEngine._callback()` after voice mixing and before the `np.tanh` clip (`set_effects()`), and in `playback.render_offline(effects=...)` offline, which also appends `tail_seconds()` of ring-out. `EFFECT_TYPES` registry + `build_effect()`/`chain_from_specs()`/`chain_from_patch()` turn a patch's `[[effects]]` (`patch_format.EffectSpec`) into a chain; an unknown `type` is `None`/collected into `chain.skipped`, never an error, matching `patch_format.py`'s round-trip preservation. Hand-rolled, not `pedalboard` (GPL-3.0, ~12MB of JUCE, non-interpolating `Delay`, SIGILL on this CPU — #104). Mono; stereo would attach at `Chorus`'s taps. No reverb (#99's fog): one more class + one registry entry when it comes. Defaults/ranges in `config.EFFECT_*`; rationale in docs/DECISIONS.md. |

| `sf2_playback.py` | (map #99, ticket #117, research #102) SF2 soundfont playback — **the only module permitted to import `fluidsynth`** (`pyfluidsynth`), mirroring `batch_transcribe.py`'s `librosa` and `score_writer.py`'s `music21` isolation, except the import here is *lazy* and behind one cached probe (`sf2_availability() -> (available, reason)`), so every decision in the module is importable and unit-testable on a machine with no FluidSynth at all. **Pull model only — `Synth.start()` is never called**: that would open FluidSynth's own audio driver on its own thread, a second device and a second clock competing with this app's `sounddevice` streams. Without it FluidSynth owns no device and `fluid_synth_write_s16()` renders a block on demand, which `downmix()` folds to mono float32 for `sound_engine.SoundEngine`'s existing callback buffer (FluidSynth's own defaults are already `config.PLAYBACK_SAMPLE_RATE`/`PLAYBACK_BLOCK_SIZE`, so nothing resamples). `SF2Engine` satisfies `sound_engine.Engine`; `SF2Voice` is a **handle, not a generator** — FluidSynth mixes every sounding note into one buffer, so exactly one voice (the *primary*, oldest live unfinished) pulls the shared block per callback and the rest are no-ops, which is the case #105 cited for keeping `Voice` a Protocol. The voice registry holds `weakref`s so a voice #112's `VoiceManager` *steals* (dropped outright, no note-off) disappears deterministically, with a `weakref.finalize` sending the missing note-off — generation-checked against a per-voice monotonic stamp so it can never silence a newer note that retook the same `(channel, key)`. `silence_stderr()` (fd-2 redirect) covers construction's ~16 ALSA/SDL lines; `silence_fluidsynth_log()` installs a null log handler once per process for FluidSynth's own runtime WARN/INFO/DBG — the polyphony-exceeded warnings `noteon()` emits mid-performance, found at build time (see docs/DECISIONS.md). **Bundles no soundfont**: `discover_soundfonts()`/`resolve_soundfont()`/`sf2_status()` search the samples dir, XDG data, the standard system locations and a Homebrew prefix, under a `[preferences].soundfont_path` setting and a patch's bare `[sf2].soundfont` name. `config.SF2_POLYPHONY` is FluidSynth's *internal* cap counting **voices** (2+ per key on a layered preset) — deliberately not #112's `[preferences].polyphony_*` **note** budget; both apply at once. `engine_for_patch()` raises exactly one of `SF2Unavailable`/`SoundfontNotFound`/`SF2Error`, so a caller reports "unavailable" as status rather than crashing. Behind the `[sf2]` extra (`pip install -e .[sf2]` plus a system `libfluidsynth`). |
| `color_map.py` | `note_to_hsl()`, `hsl_to_rgb255()`, `fifths_index()`, `hue_for_step()` (the shared 30-degrees-per-step hue formula `note_to_hsl()` and `menu_animation.band_color()` both build on), `NOTE_NAMES`, `NOTE_NAMES_FIFTHS`. |
| `staff_map.py` | `staff_row()`, `ledger_rows()`, `row_note_name()` (general row→letter, every line/space row) — grand-staff placement, used only by `tab` view. |
| `animation.py` | `ColorAnimator` — crossfade + onset pulse. Used by GUI, terminal-fill, and (per-note-keyed) chord-mode fill bands. |
| `display.py` | `Display` — pygame GUI window (fullscreen, debug overlay). Chord mode is out of scope for the GUI (no live-hotkey mechanism). |
| `terminal_display.py` | `TerminalDisplay` — ANSI truecolor full-terminal fill; `render_bands()` for chord mode's proportional per-note bands. |
| `terminal_wheel_display.py` | `WheelDisplay` — 12-note fifths ring, always fifths color regardless of `--color-scheme`; `render_chord()` for chord mode's multi-wedge steady-lit display. |
| `terminal_tab_display.py` | `TabDisplay` — scrolling grand-staff note history rendered as sheet-music noteheads; `push()`/`push_notes()` (each note stored as a mutable dict, not a tuple — a `duration_class` field starts `None` and is filled in later by `finalize_duration()`, since a note's duration is only known after it decays, well after the column carrying it was pushed; optional `t=` override lets `main.run_batch_transcribe()` stamp a column with the recording's real onset time instead of wall-clock), `push_barline()` (issue #55: a second, distinct column type — no notes, just a divider glyph spanning the staff height at `TAB_BARLINE_WIDTH`, aged/dimmed the same way note columns are but with no hue), `render()` (takes live `notehead_style`/`legend_on`/`frozen`/`scroll_offset`, age-fades each column's lightness per issue #22, and composes duration glyphs/suffixes onto each note per its `duration_class`), `dump_ansi()` on quit (always letter+octave, unaffected by any toggle). `self.entries` retains history by timestamp window (`self.scrollback_seconds`, defaulting to `config.TAB_SCROLLBACK_SECONDS`, overridable via the constructor) rather than the old fixed column count, giving the `R`/Left-Right-arrow scrollback feature real reach to scroll within; `render(..., scroll_offset=N)` renders the view as it looked `N` history entries ago, historical age-fade included (freeze's usual pin-to-full-brightness only applies at `scroll_offset=0`). `correct_duration()` retroactively overwrites a specific already-finalized note's `duration_class` (disambiguated from repeat notes at the same key by closest column timestamp — `finalize_duration()` itself can only reach the currently-open note at a key); `erase_barlines()`/`insert_barline()` replace a stale barline set within a time range with recomputed ones (the latter inserts in sorted position rather than just appending). All three are the `TabDisplay`-side API issue #77's `R`-key non-causal rhythm re-analysis calls into (`main.py`'s `_apply_reanalysis_result()`) — this module owns only the data/render capability, not the recompute engine (`rhythm_reanalysis.py`) or keybind wiring (`main.py`). `select_visible_entries()`/`column_width_for()` (pure, module-level) are the width-budget walk deciding which entries are on screen, extracted out of `render()`'s body for ticket #121 and shared with the new `visible_entries()` method — so frozen playback's "every column currently visible" scope is literally the renderer's own answer, immune to disagreeing with it across a resize, an `L` toggle, a notehead-style change or a scrollback offset. |
| `config_store.py` | `ConfigStore`/module-level `store` — additive TOML overlay over `config.py` from `$XDG_CONFIG_HOME/note-color/config.toml` (fallback `~/.config/note-color/config.toml`); `keybind()`/`note_hue_override()`/`preference()` (mtime-checked hot-reload), `set_preference()`/`set_keybind()`/`set_note_hue_override()` (persist + write back to the TOML file — all three back issue #43's settings screen, `set_preference()`/`preference()` generically covering the numeric `rhythm_reanalysis_window_seconds`/`tab_scrollback_seconds` fields alongside the earlier hand-edit-only `menu_perf_mode`, no bespoke accessor needed for any of the three). |
| `kitty_keys.py` | (map #99, ticket #118) Pure logic for the kitty keyboard protocol — the only way a terminal reports key *releases*, and therefore the only way a held key can sustain a note instead of machine-gunning it. No I/O whatsoever; `main.RawKeys` is the thin I/O layer on top and its only caller today. `SYNTH_FLAGS` (27 = `1|2|8|16`) — flag 2 alone is **not** enough (letter keys keep arriving as plain text, which has nowhere to carry an event type; flag 8 forces them through `CSI … u` where a release can exist). `PROBE_SEQUENCE` (`CSI ? u` then a `CSI c` DA1 sentinel) + `CapabilityProbe` — capability detection that settles *immediately* on a non-kitty terminal (every VT-lineage terminal answers DA1) rather than waiting out a timeout, and preserves a keystroke typed during negotiation in `.leftover` rather than eating it. `push_sequence()`/`pop_sequence()`/`FOCUS_TRACKING_ON`/`OFF`. `parse_key_event()` — a strict generalisation of `main._parse_csi_params()` (today's bare and Shift-modified arrows are the degenerate case of the same grammar, cross-checked against the real function in `tests/test_kitty_keys.py`) returning a `KeyEvent` (`key` is the *physical* key so a release matches its own press even if Shift was let go in between; the shifted spelling rides in `text`). `legacy_token()` — the compatibility shim mapping that stream back down to exactly what `poll()` returns today. `HeldKeys` (press → note_on, release → note_off, auto-repeat swallowed, several keys at once, `release_all()`) and `FixedDurationKeys` (the degraded no-releases policy: fixed-length notes, a further press extends rather than retriggers — injected time, never read). Landed from issue #101's prototype, verified live in real kitty by the project owner. |
| `main.py` | Wires threads together; `SessionState` (lazy-created capture/analysis-thread/sensitivity/source bundle) + `run_session()` (dispatch-and-return-sentinel, reusable across tool switches, issue #40) sit alongside the original per-view CLI entry point. `RenderItem` NamedTuple is the render-queue shape — `duration_hops`/`bpm_estimate` (issue #55) are its newest two fields. `run_terminal_tab()` drives rhythm notation: per-hop `finalize_duration()` calls (mono via the previous hop's `pitch_class`/`octave`, chord via each `note_stack` entry's own `duration_hops`) and a beat-accumulator triggering `push_barline()` — `_hop_beats()` (issue #76) credits a hop's beats as the *max* across whatever mono/chord finalizations happened that hop, not a sum, mirroring `run_batch_transcribe()`'s own per-onset `max()` pattern (summing double-counted an ordinary single note, since it's independently finalized by both the always-on mono and chord/multipitch trackers every hop). `run_batch_transcribe()` (issue #55, `virtualnote transcribe`) never touches `SessionState`/audio at all — offline, one-shot, builds `TabDisplay` columns from `batch_transcribe.transcribe()`'s output and calls `dump_ansi()` directly, no render loop. `pygame` imported only inside `run_gui`; `librosa` never imported here directly (only via `rhythm_reanalysis.py`, see that module and `batch_transcribe.py`). Issue #77 additions: `ReanalysisBuffer` (owned by `SessionState`, appended to every hop by `analysis_loop()` with `rhythm_reanalysis.HopRecord`s, bounded/hot-reloaded against `rhythm_reanalysis_window_seconds`) and `ReanalysisState` (a plain `.in_progress` flag shared between the render thread and the throwaway recompute thread `_handle_reanalysis_key()` spawns on `R`). `run_terminal_tab()` polls a second, local single-slot `reanalysis_result_queue` once per iteration and applies a ready result via `_apply_reanalysis_result()`; `_handle_scroll_keys()` maintains `scroll_offset` (reset to 0 on every unfreeze) fed straight to `TabDisplay.render(scroll_offset=...)`. `run_replay_session()` (`virtualnote replay`, session recording + playback) is `run_batch_transcribe()`'s JSONL-log-shaped sibling: also never touches `SessionState`/audio, but *does* render live (unlike batch's silent sweep) — `time.sleep()` between `session_player.group_columns()`'s columns, paced by their real recorded timestamp gaps divided by `--speed`, reproduces the original session's pacing on screen; Ctrl+C stops it early and still dumps via `dump_ansi()` on the way out. Both functions take a `play=False` flag (map #24's playback engine, `virtualnote transcribe`/`replay --play`) — `run_batch_transcribe()` locally imports `playback` and calls `play_offline()` once, after `--write-score`/`--export-abc`, since offline pre-render needs the whole `TranscriptionResult` it already has in hand; `run_replay_session()` locally imports `sound_engine` (map #99, ticket #112 — `playback.LiveScheduler` is retired), opens one `SoundEngine` for the whole run, and per note issues a `note_on()` the instant its column is pushed inside the existing pacing loop plus a matching `schedule_note_off()` at that note's own `duration_seconds` divided by `--speed` (so audio and visuals speed up together), stopping the engine in the same `finally` block that writes the dump. `SessionState` also grows `ensure_sound_engine()` — the idempotent, lazy, process-wide `SoundEngine` owner, separate from `ensure_started()` so a tool can open output without input or input without output — and `stop()` closes it unconditionally on process exit. Map #99 / ticket #121 additions: `PlaybackState` (the render-thread/worker handshake, `ReanalysisState`'s shape), `is_playback_key()` (pure: Enter, and only Enter), `_handle_playback_key()` (frozen-only; builds a `tab_playback` schedule on the render thread, spawns the daemon worker, and treats a second press as stop — an unavailable sound engine becomes a status-line message, never an exception), `_playback_worker()`/`_wait_until()` (real-time note-ons plus `SoundEngine.schedule_note_off()`, sleeping in short slices so a stop lands promptly). `run_terminal_tab()` takes `sound_engine_provider=` (`SessionState.ensure_sound_engine`, passed by `run_session()`), so the output device opens on the first `Enter` and is then kept for the process's life. `run_synth_tool(session=None)` (map #99, ticket #119, `virtualnote synth` / the menu's `Synth` entry) is the standalone instrument's driver — never touches `SessionState`/the mic, opens the keyboard with `want_kitty=True` (the one thing in this app that genuinely needs key *releases*, since a held key must sustain rather than machine-gun) and falls back to `kitty_keys.FixedDurationKeys` otherwise, with `_synth_status()`'s `keys=` field saying which of the two is in force (#107 point 7). `_handle_synth_action()`/`_confirm_synth_overlay()` own the real side effects (file I/O, the audio engine); every *decision* they act on lives in `synth_tool.py` and is unit-tested there. `_synth_legend()` is asserted against the dispatcher by `tests/test_main.py` so the legend can never advertise a keybind that isn't implemented. `run_score_editor(path)` (issue #98, `virtualnote edit <path>`) is the score editor's own `run_terminal_*`-shaped driver — `score_editor_state.load_score()`/`new_blank_score()` picks the starting `EditorScore`, an `EditHistory` backs `undo`/`redo`, and the interactive loop dispatches through `resolve_editor_action()` (the pure keypress-to-action mapping, hardcoded Left/Right/Up/Down/Shift+Up/Shift+Down/Enter plus every remappable score-editor `[keybinds]` action) into `score_editor_display.py`'s pure mutation functions. `RawKeys.poll()`'s CSI parsing (below) is what makes `resolve_editor_action()`'s `SHIFT_UP`/`SHIFT_DOWN` tokens possible — the score editor is the only consumer today, but the parsing itself lives on `RawKeys` since it's a keyboard-input-layer concern, not editor-specific. Opening the Chord builder (Enter) hands the same already-active `RawKeys` instance to `_run_chord_builder()`, a further interactive (smoke-tested-only) loop over `chord_builder_display.py` — Up/Down switches the focused reel, Left/Right spins it (issue #98 follow-up, swapped from the reverse binding to match the reels' actual vertical layout; see docs/DECISIONS.md). Score properties (`t`) no longer opens a second screen (that shape, and `score_properties_display.py`'s old `_run_score_properties()` loop, were retired by the same follow-up) — it toggles an inline header-editing mode over the status line's always-visible `time=`/`key=`/`tempo=` fields instead: `_property_field_texts()` (the three fields' plain display text), `_handle_property_key()` (pure-ish dispatch — Left/Right moves the highlighted field, Up/Down spins its value via `score_properties_display.py`'s existing stepping functions, digits/`/` accumulate into a per-field typed buffer on the two typable fields, Enter parses+applies via `_parse_property_input()` and exits back to normal cursor editing), mutating the real `EditorScore` directly, same "no separate commit step" convention the old screen already used. Quitting (`|`/Ctrl+C) while `dirty` is `True` needs a second confirming press of the same key before actually leaving — the one editor view in this app where quitting can lose real work; any other keypress in between disarms the pending confirm. Like `transcribe`/`replay`/`edit`'s CLI handling, never touches `SessionState`/audio, and imports `score_editor_display`/`score_editor_state`/`score_properties_display` locally (the first two pull in `music21` transitively) rather than at module level, so `music21`'s import cost is paid only when the editor actually opens. `RawKeys.poll()` (below) also grew a small CSI-parameter parser for this — `_parse_csi_params(param_bytes, final_byte)`, a pure function factored out of `poll()`'s byte-reading loop so it's unit-testable without a real TTY, per this repo's "pure logic unit-tested, real I/O smoke-tested" convention. playback`, opens one `LiveScheduler` for the whole run, and calls `trigger_note()` per note the instant its column is pushed inside the existing pacing loop (each note's `duration_seconds` divided by `--speed` too, so audio and visuals speed up together), closing the scheduler in the same `finally` block that writes the dump. `run_synth_tool(session=None)` (map #99, ticket #119, `virtualnote synth` / the menu's `Synth` entry) is the standalone instrument's driver — never touches `SessionState`/the mic, opens the keyboard with `want_kitty=True` (the one thing in this app that genuinely needs key *releases*, since a held key must sustain rather than machine-gun) and falls back to `kitty_keys.FixedDurationKeys` otherwise, with `_synth_status()`'s `keys=` field saying which of the two is in force (#107 point 7). `_handle_synth_action()`/`_confirm_synth_overlay()` own the real side effects (file I/O, the audio engine); every *decision* they act on lives in `synth_tool.py` and is unit-tested there. `_synth_legend()` is asserted against the dispatcher by `tests/test_main.py` so the legend can never advertise a keybind that isn't implemented. `run_score_editor(path)` (issue #98, `virtualnote edit <path>`) is the score editor's own `run_terminal_*`-shaped driver — `score_editor_state.load_score()`/`new_blank_score()` picks the starting `EditorScore`, an `EditHistory` backs `undo`/`redo`, and the interactive loop dispatches through `resolve_editor_action()` (the pure keypress-to-action mapping, hardcoded Left/Right/Up/Down/Shift+Up/Shift+Down/Enter plus every remappable score-editor `[keybinds]` action) into `score_editor_display.py`'s pure mutation functions. `RawKeys.poll()`'s CSI parsing (below) is what makes `resolve_editor_action()`'s `SHIFT_UP`/`SHIFT_DOWN` tokens possible — the score editor is the only consumer today, but the parsing itself lives on `RawKeys` since it's a keyboard-input-layer concern, not editor-specific. Opening the Chord builder (Enter) hands the same already-active `RawKeys` instance to `_run_chord_builder()`, a further interactive (smoke-tested-only) loop over `chord_builder_display.py` — Up/Down switches the focused reel, Left/Right spins it (issue #98 follow-up, swapped from the reverse binding to match the reels' actual vertical layout; see docs/DECISIONS.md). Score properties (`t`) no longer opens a second screen (that shape, and `score_properties_display.py`'s old `_run_score_properties()` loop, were retired by the same follow-up) — it toggles an inline header-editing mode over the status line's always-visible `time=`/`key=`/`tempo=` fields instead: `_property_field_texts()` (the three fields' plain display text), `_handle_property_key()` (pure-ish dispatch — Left/Right moves the highlighted field, Up/Down spins its value via `score_properties_display.py`'s existing stepping functions, digits/`/` accumulate into a per-field typed buffer on the two typable fields, Enter parses+applies via `_parse_property_input()` and exits back to normal cursor editing), mutating the real `EditorScore` directly, same "no separate commit step" convention the old screen already used. Quitting (`|`/Ctrl+C) while `dirty` is `True` needs a second confirming press of the same key before actually leaving — the one editor view in this app where quitting can lose real work; any other keypress in between disarms the pending confirm. Like `transcribe`/`replay`/`edit`'s CLI handling, never touches `SessionState`/audio, and imports `score_editor_display`/`score_editor_state`/`score_properties_display` locally (the first two pull in `music21` transitively) rather than at module level, so `music21`'s import cost is paid only when the editor actually opens. `RawKeys.poll()` (below) also grew a small CSI-parameter parser for this — `_parse_csi_params(param_bytes, final_byte)`, a pure function factored out of `poll()`'s byte-reading loop so it's unit-testable without a real TTY, per this repo's "pure logic unit-tested, real I/O smoke-tested" convention. Map #99 / ticket #120 additions: `run_score_editor(path, session=None)` now takes an optional `SessionState` purely to borrow the process-wide `SoundEngine` (`_editor_sound_engine()`, which falls back to building its own under `virtualnote edit` and to `None` — a silent but fully usable editor — on any audio failure), constructs its `RawKeys` with `want_kitty=True` and drives the loop off `poll_event()` so key *releases* can distinguish a chord from a sequence, dispatches piano-mode presses through `score_audition.py` into `score_editor_display.place_note_at_pitch()`/`append_column()`, and owns `_EditorPlayback` (render-thread-local play-from-cursor state — no thread, unlike `tab`'s #121 playback, since the editor's onsets are already on a beat grid) plus `_editor_audio_status()`/`_editor_loop_status()` for the new status-line fields. Ticket #118 (map #99) extended `RawKeys` itself with optional kitty-keyboard-protocol support (`kitty_keys.py`, below): `RawKeys(fd=..., out_fd=..., want_kitty=..., kitty_flags=..., negotiation_timeout=...)` — `want_kitty=False` by default, so every existing construction site behaves byte-for-byte as before and negotiation is paid **per view, not process-wide** (`|` back-to-menu never pays it). With it on, `poll()` still returns exactly the tokens it always did (via `kitty_keys.legacy_token()`, draining through a new internal queue since one read can now yield several events), and `poll_event()` is the new richer view returning one `kitty_keys.KeyEvent` (press/repeat/release) per call — synthesised as a PRESS on a terminal without the protocol, so an event-shaped caller works everywhere. `restore()` also pops the keyboard mode and disables focus reporting; `release_all()` returns synthetic RELEASE events for every key still held, and is called automatically on focus-out (DECSET 1004) so a note can't hang when the window loses focus mid-hold. `fd`/`out_fd` are constructor parameters purely for testability — it's what lets `tests/test_rawkeys.py` drive every byte path over an `os.pipe()`/`pty.openpty()` pair with no terminal present. See docs/DECISIONS.md for the flag choice, the DA1 sentinel, and the one accepted behaviour change. Ticket #122 additions: `run_synth_tool()`'s `_note_on`/`_note_off` also drive `SessionRecorder.note_on()`/`.note_off()` (so a recording is of what was *heard* — including notes ended by a layout switch, by leaving the tool, or by the no-key-release fallback's `expire()`), `Shift`+S toggles it via `_handle_synth_action`'s `record_toggle` (releasing every held note first, same reason an octave shift does), `_synth_status()` gained a `rec=ON/off(shift+s)` field, and the recorder is `SessionState.session_recorder` when there is a session (so arming here and arming in `fill` are one switch on one file) or a tool-owned one closed on exit when there isn't. `run_score_editor(path, session=None, score=None)`'s new `score=` takes an already-built `EditorScore` instead of loading `path` — how an imported recording arrives, opening unsaved at a path nothing has written. |
| `menu_display.py` | `MenuDisplay` — `virtualnote`'s tool-picker screen (issue #40); `render()` draws issue #42's decided animated design (built in #51): `menu_animation`'s spinning donut fills a left-hand pane, with the title/donation-callout/tool-list/hints/status text overlaid in a fixed-width right-hand pane (`_layout()`, `_text_lines()`) — narrow terminals drop the donut and fall back to a centered text-only screen, same shape as the original #40 placeholder. `move()`/`move_to()`/`current_view()` selection plumbing is unchanged by any of this. `TOOLS` (the four run_session-launchable views) vs. `MENU_ITEMS` (`TOOLS` plus non-audio screens: `settings`, `credits`, `prototypes`, `stats`, (issue #98) `edit`, and (ticket #119) `synth`) — selection/render operate on `MENU_ITEMS`; `shell.py` special-cases the extra entries instead of sending them through `main.run_session()`. `osc8_link()`/`_donation_line()` (issue #44) build the main screen's clickable author/donation callout. `_resolve_perf_mode()` picks full vs. perf donut rendering: an explicit override (virtualnote's `--menu-perf-mode` flag) beats `config.toml`'s `[preferences].menu_perf_mode` beats `menu_animation.detect_perf_mode()`'s real startup probe. |
| `menu_animation.py` | Animation math for the menu screen's donut (issues #42/#51), ported from the throwaway prototype at `prototype/issue-42-menu-animation/{donut_fifths.py,autodetect.py}`: `render_frame()` — NumPy-vectorized torus point-projection (`_project()`) + a painter's-algorithm z-buffer via ascending-depth-sort fancy-indexing (no per-point Python loop) — re-skinned with the circle-of-fifths palette (`band_color()`/`FIFTHS_LABELS`), full mode shaded/lettered, perf mode flat/letterless/half-raster. `detect_perf_mode()`/`_decide_perf_mode()` — issue #46's auto-detect heuristic (core-count floor, then a real self-timed `render_frame()` probe against the full-mode frame budget), split into a real-timing wrapper and a pure decision function for testability. |
| `settings_display.py` | `run_settings_screen()` — `virtualnote`'s interactive Settings screen (issue #43): edits `config_store`'s keybind remaps, per-note hue overrides, and generic numeric preferences live, using `blessed` for field navigation and "press a key to capture this remap"/"type a clamped number" input (the one deliberate exception to raw-ANSI chrome elsewhere in the shell, per #37/#39). `FIELDS` (three kinds: `"keybind"`/`"color"`/`"numeric"`) / `NUMERIC_FIELDS` (spec list: key, label, min, max, step, default — today covers `rhythm_reanalysis_window_seconds` and `tab_scrollback_seconds`) / `move()` / `keybind_value()`/`color_value()`/`numeric_value()` / `is_valid_remap_key()` / `parse_hue_input()` (wraps modulo 360) / `parse_numeric_input()` (clamps into `[min, max]`, the correct behavior for a bounded quantity unlike hue's circular wrap) / `apply_field_edit()` / `clear_field()` are the pure, unit-tested logic; `run_settings_screen()`'s render/edit-capture loop itself (including `_capture_numeric()`, modeled on `_capture_hue()`) is smoke-tested manually, same convention as every `run_terminal_*` loop. The synth tool's own `Shift`+key commands (ticket #119) are deliberately **absent** from `KEYBIND_ACTIONS`: a remap onto a plain letter would silently break that tool's always-plays invariant, and `is_valid_remap_key()`'s fixed denylist has no way to express "not any letter, ever" — same hardcoded tier as the score editor's Shift+Arrow transpose (see docs/DECISIONS.md). `NUMERIC_FIELDS` does gain `polyphony_synth_dual`, the third voice-budget context (#119). `KEYBIND_ACTIONS` also covers the score editor's thirteen remappable actions (issue #98 — `note_toggle` through `score_properties`), remappable here exactly like the app's original nine; `FIELDS`'s length and layout are derived from `KEYBIND_ACTIONS`' own length, so this extension needed no changes to this module's field-layout logic itself. Two score-editor actions that used to be here — `transpose_up`/`transpose_down` and `score_properties_exit` — no longer are: a post-#98 hands-on-feedback follow-up made transpose a hardcoded Shift+Up/Shift+Down (not remappable) and retired the separate Score properties screen `score_properties_exit` used to close; see docs/DECISIONS.md. |
| `credits_display.py` | `run_credits_screen()` — `virtualnote`'s static Credits screen (issue #44): author, Claude/AI-assistance credit, and third-party library attribution (`THIRD_PARTY_LIBRARIES`), raw ANSI (no editable state, so no need for `settings_display`'s `blessed` exception). `credits_lines()` is the pure, unit-tested text builder; the render/wait-for-any-keypress loop itself is smoke-tested manually. |
| `prototypes_display.py` | `run_prototypes_screen()` — `virtualnote`'s Prototypes screen: lets a prototype under `prototypes/` actually be *run*, live, from inside the app — Enter hands the real terminal to the selected prototype's own no-argument demo/harness script as a subprocess (stdio inherited, so its raw ANSI/color output renders exactly as running it by hand would), waits for it to exit, then a keypress returns to the list. `list_prototypes()` (pure, sorted by name, skips a subdirectory with no `README.md`) supplies the list, each entry's `script_path` resolved by `_find_entry_script()` (checks `demo.py`/`run_demo.py`/`harness.py` in order, then falls back to "the one `.py` file in this directory" if unambiguous — see that function's docstring for why a name-derived guess isn't used) — an entry with no resolvable script just isn't offered a `[run]` action. `i`/`RIGHT` always opens a secondary README view (wrapped by `_wrap_readme()`, paginated by `_visible_slice()`, both pure/unit-tested); `LEFT`/Backspace closes it back to the list, `|` returns to the menu from either level. Raw ANSI, no editable state, same `blessed`-free reasoning as `credits_display.py`. |
| `stats_display.py` | `run_stats_screen()` — `virtualnote`'s Play Stats screen (Feature 4 in `docs/research/notation-and-feature-ideas.md`): aggregates every `session_log_*.jsonl` the `S` session-recording keybind has ever written next to `main.py` into a simple summary — total logged practice time, most-played notes (top 10), sessions-by-date. `session_log_paths()` (flat, non-recursive glob) + `load_sessions()` do the real file I/O, reusing `session_player.load_events()` per file rather than reimplementing JSONL parsing; `compute_stats()` (pure, a list of already-loaded `(path, events)` pairs in, a stats dict out) and `stats_lines()` (pure text builder, mirrors `credits_display.credits_lines()`) are the unit-tested logic. `_session_date()` parses a `YYYY-MM-DD` out of `session_recorder.py`'s own `session_log_<timestamp>.jsonl` naming convention for the by-date breakdown, excluding (not crashing on) a hand-renamed or foreign file that doesn't match it. Aggregates fresh on every screen visit (no caching), so a session recorded since the app started shows up without a restart. Raw ANSI, no editable state, same `blessed`-free reasoning as `credits_display.py`. |
| `abc_export.py` | Hand-rolled ABC notation export (Concept A / Feature 2 in `docs/research/notation-and-feature-ideas.md`), additive alongside the existing live `TabDisplay` renderer and MusicXML `score_writer.py` — not a rewrite of either. `music21` (already a `score_writer.py` dependency) can *read* ABC but has no ABC *writer* (confirmed by `prototypes/abc-notation-view/`), so `note_events_to_abc()` hand-rolls the small header/body serialization itself and never imports `music21`. `from_transcription_result()` (a `batch_transcribe.TranscriptionResult` in, grouping by `onset_hop` the same way `main.run_batch_transcribe()` does) and `from_session_log()` (a `session_player.load_events()`-shaped event list in, grouping by shared onset time `t` via `session_player.group_columns()`) both reduce to one shared `columns` list and reconstruct barlines via `_with_barlines()`'s beats-accumulator walk (mirroring `main.run_batch_transcribe()`'s own barline placement) — neither source carries its own barline events. Simultaneous notes at one column (chord mode) render as an ABC chord bracket (`[CEG]`) sharing the longest member's duration. `write_abc()` writes the serialized text to a file. Wired into `virtualnote transcribe --export-abc PATH`, same bare-flag/explicit-path/omit convention as the sibling `--write-score` flag. |
| `shell.py` | `run_menu_loop(session)` — `virtualnote`'s unified in-process orchestrator (issue #40): shows the menu, dispatches a pick to `main.run_session()`, loops back to the menu on a `"menu"` sentinel, exits the process on `"quit"`. `_handle_menu_key()` is the pure keypress-to-selection logic. `"settings"`/`"credits"`/`"prototypes"`/`"stats"` picks are special-cased via `_NON_SESSION_SCREENS` straight to `settings_display.run_settings_screen()`/`credits_display.run_credits_screen()`/`prototypes_display.run_prototypes_screen()`/`stats_display.run_stats_screen()` instead of `run_session()` (issues #43, #44, the Prototypes browser, and Feature 4's Play Stats screen) — none touches audio, so all four always return straight back to the menu. `"synth"` (map #99, ticket #119) gets its own dedicated branch for exactly `edit`'s reason — `main.run_synth_tool()` returns the same `"menu"`/`"quit"` sentinel a real tool does (so its result must be interpreted, unlike the four dict-dispatched screens), while never opening the mic (so it must not go through `run_session()`'s `ensure_started()`); `session` is handed over only for the process-wide `SoundEngine`. `"edit"` (issue #98) gets its own dedicated branch too, alongside (not inside) `_NON_SESSION_SCREENS`: it shows `score_editor_picker.run_score_editor_picker()` first (an existing score file, or "New score..."), and — since a chosen path means actually launching `main.run_score_editor()`, which returns the same `"menu"`/`"quit"` sentinel a real `run_session()` tool does — handles that return value the same way the `run_session()` call below it already does, rather than the "always straight back to the menu" shape the four dict-dispatched screens share (see docs/DECISIONS.md for why `edit` fits neither existing shape cleanly). The `session` is handed to `main.run_score_editor()` (ticket #120) purely so the editor's audition/playback can borrow this process's one `SoundEngine` rather than opening a second output stream — still never its audio *input*. |
| `virtualnote.py` | CLI entry point for the unified shell (issue #40): `build_parser()` (bare menu vs. `<view> [flags]`, replicating every flag the retired `colorize` dispatcher forwarded; `--menu-perf-mode {auto,full,perf}`, top-level-only, issue #51's CLI override for the menu donut; `tab`'s `--time-signature`, the standalone `transcribe <file> [--dump-file] [--time-signature] [--write-score] [--export-abc] [--play]` subcommand (issue #55, `--write-score` from issue #65, `--export-abc` from Feature 2's ABC export, `--play` from map #24's playback engine), and the standalone `replay <file> [--dump-file] [--speed] [--play]` subcommand (session recording + playback, `--play` from map #24's playback engine), the standalone `edit <file>` subcommand (issue #98's score editor), and the standalone, argument-free `synth` subcommand (map #99, ticket #119 — an instrument, not a view of captured audio, so it takes no `--source`/`--color-scheme`)) + `main()`, which builds one `main.SessionState` and hands off to `shell.run_menu_loop()` or `main.run_session()` directly — except `transcribe`/`replay`/`edit`, handled and returned before `SessionState` is even constructed, since none of the three touches live audio. (ticket #122) `edit`'s single `file` argument now also accepts a `session_log_*.jsonl` recording, quantized into a score via `log_import.import_log()` before `run_score_editor(..., score=)` — with `--grid` (choices read from `log_import.GRID_NAMES` itself, never restated), `--tempo` and `--out`. One argument rather than an `import` subcommand: from the user's side it is still "open this in the editor". |
| `tests/` | `test_pitch_detect.py`, `test_note_smoother.py`, `test_color_map.py`, `test_staff_map.py`, `test_chroma.py`, `test_chord_templates.py`, `test_multipitch.py`, `test_chord_smoother.py`, `test_terminal_tab_display.py`, `test_config_store.py`, `test_shell.py` (the new global key handlers/legend builder, `MenuDisplay` selection state, `shell._handle_menu_key`, `virtualnote.build_parser()` — not the threaded/interactive loops themselves, per this repo's existing test convention), `test_settings_display.py` (field layout/formatting/parsing/edit helpers, each test isolated onto its own `tmp_path` config file via a monkeypatched `settings_display.store` — never the real `~/.config/note-color/config.toml`), `test_credits_display.py` (`credits_lines()` text content), `test_menu_animation.py` (projection/shading helpers, `render_frame()` shape/smoke checks, the auto-detect decision function), `test_menu_display.py` (`_layout()`'s donut/text-pane column split, `_resolve_perf_mode()`'s override precedence, `_text_lines()`'s content), `test_onset_detect.py`/`test_duration_tracker.py`/`test_tempo_tracker.py`/`test_batch_transcribe.py` (issue #55: synthetic spectra/chroma/magnitude-envelope/periodic-impulse fixtures, same "synthesize the signal, no binary fixtures" convention `test_chroma.py`'s `make_tone()` set), `test_rhythm_reanalysis.py` (issue #77: synthesized `HopRecord` sequences exercising `rhythm_reanalysis.recompute()` directly — corrected durations, chord-onset-on-reappearance, tempo recovery from a periodic novelty signal, barline placement, the empty-buffer `None` case — same convention, not `main.py`'s threaded `R`-key wiring itself, which is smoke-tested manually per this repo's existing `run_terminal_*` convention), `test_tab_playback.py` (ticket #121: scope selection with and without a marked range, barline/silence-column rejection, duration-class→seconds conversion and its clamps, onset re-basing, full-chord-stack expansion, schedule length), `test_session_recorder.py` (mono-pairs-with-previous-hop/chord-tone-via-note_stack event shape, the not-armed no-op, idempotent close), `test_session_player.py` (`load_events()`'s sort, `group_columns()`'s chord-tone grouping and note-before-barline tie-break), `test_sound_engine.py`/`test_tone_engine.py`/`test_playback_callers.py` (map #99, ticket #112: the `NoteOn` event model's MIDI-pitch/velocity conversions, `select_steal_index()`'s stealing policy across every branch, `VoiceManager`'s hard cap/never-refuse/live-polyphony/note-off-resolution/render-and-retire behavior, `SoundEngine`'s note-off deadline arithmetic and audio callback — all driven with a plain NumPy buffer, no device opened, same "pure logic unit-tested, hardware smoke-tested" split this suite already applies to `audio_capture.py`; `ToneVoice`'s envelope stage timings/levels, release-from-anywhere, cross-block phase continuity and spectral content; and both existing playback callers surviving the change — `transcribe --play`'s still-offline pre-render and `replay --play`'s note-on/scheduled-note-off pairs), `test_playback.py` (map #24: `note_frequency()`'s MIDI-tuning math, `_adsr_envelope()`'s shape incl. the short-note scale-down case, `synthesize_note()`'s length/amplitude/velocity, `render_offline()`'s sample-accurate onset placement and multi-note mixing — the `LiveScheduler` tests went with the class itself in ticket #112), `test_prototypes_display.py` (`list_prototypes()`'s sort/title-from-H1/no-README-skip/`script_path` resolution, `_find_entry_script()`'s candidate-name/sole-.py-file/ambiguous-None cases, `_wrap_readme()`'s wrapping, `_visible_slice()`'s pagination/clamping — not `run_prototypes_screen()` itself, smoke-tested manually per this repo's existing `run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention), `test_stats_display.py` (Feature 4: `compute_stats()`'s duration-summing/most-played-ranking-and-capping/sessions-by-date-grouping against synthesized `(path, events)` pairs, `_session_date()`'s filename parsing, `_format_duration()`, `stats_lines()`'s text content — not `run_stats_screen()` itself, smoke-tested manually per the same convention), `test_abc_export.py` (Concept A / Feature 2: `note_events_to_abc()`'s pitch/accidental/octave-mark/duration-digit tokens and chord-bracket rendering against synthesized column lists, `from_transcription_result()`/`from_session_log()`'s onset-hop/shared-`t` grouping and barline placement, `write_abc()`'s file-write round-trip), `test_score_editor_state.py` (issue #98: `new_blank_score()`'s defaults, `save_score()`/`load_score()` round trips — a chord column, a rest column, non-default time-signature/key/tempo, all built measure-aligned per that file's own docstring so music21's own tie-splitting-across-barlines doesn't confound the assertions — loading a `score_writer.write_score()`-produced file's missing tempo/key defaulting to 90.0/0 sharps, and `EditHistory`'s bound/undo/redo/redo-clears-on-new-edit behavior), `test_score_editor_display.py` (issue #98, UI layer: `pitch_at_row()`'s inverse relationship with `staff_map.staff_row()` and its key-signature-aware accidental defaulting, `_legend_letter()`'s key-aware legend spelling, cursor/column clamping, `toggle_note_at_cursor()`'s mutation behavior (including emptying a column to zero notes, issue #98 follow-up)/`transpose_note_at_cursor()`/`cycle_duration()`/`clear_to_rest()`/`insert_column_at()`/`delete_column_at()`'s refusal behavior, `cycle_zoom()`, `visible_column_range()`'s viewport-centering/edge-clamping, `chord_name_for_column()`'s blank-vs-recognized cases — not `render()` itself, smoke-tested manually per this repo's existing convention), `test_chord_builder_display.py` (`state_from_column()`/`notes_from_state()`'s reverse-lookup-and-reconstruction round trip, every reel's spin-and-wrap stepping, `apply_quality_preset()`, `step_root_typeahead()`'s letter-jump/accidental-nudge/exact-case behavior, `step_alias_typeahead()`/`force_commit_alias()`/`degree_alias_map()`'s buffer-then-auto-commit matching — the Up/Down-switches/Left/Right-spins key *dispatch* itself lives in `main.py`'s `_run_chord_builder()`, smoke-tested manually, not here), `test_score_properties_display.py` (each reel's spin-and-clamp/wrap stepping, `key_fifths_label()`'s pluralization — this module has no render loop left to smoke-test since issue #98's inline-header-editor follow-up), `test_score_audition.py` (map #99, ticket #120: the two-octave keyboard map, the mode machine, `PianoEntry`'s press-together/press-in-sequence grouping, audition targets, the beat schedule/playhead/due-window arithmetic, `playback_range()`'s loop-region rule, the metronome grid, and the two audio helpers against a fake engine — plus, in `test_main.py`, `run_score_editor()`'s own *dispatch* driven headlessly through a scripted `RawKeys` stand-in: which keystroke becomes a note, which advances a column, auto-repeat placing nothing new, and what lands in the undo history), `test_score_editor_picker.py` (`score_file_paths()`'s glob, `build_menu_entries()`'s trailing "New score..." row, `move()`, `is_new_score_row()`, `resolve_new_score_path()`'s extension-appending/blank-name-fallback — not `run_score_editor_picker()`/`capture_filename()` themselves, smoke-tested manually), `test_staff_map.py`'s `key_signature_accidental()` coverage (issue #98 follow-up: order-of-sharps/order-of-flats correctness across `key_fifths` from -7 to 7), `test_synth_layout.py` (ticket #119: the built-in layouts' keys/geometry/pad numbering, the custom-layout TOML round trip, and — the tests that matter most — the **seam with `score_audition.py`**, asserting across three base octaves that every key sounds the pitch `score_audition.pitch_for_key()` would play, which is what keeps the shared tracker keyboard from drifting), `test_synth_params.py` (log-vs-linear stepping, clamp-vs-wrap, the reachable-zero cases, formatting), `test_synth_tool.py` (`resolve_action()` — including a parametrised sweep asserting **no plain letter or number is ever a command**, in any overlay state — plus `ChannelRouter`'s channel dispatch, `polyphony_for_layout()`, the overlays, and every `SynthToolState` transition), `test_synth_display.py` (colour/caption/`KeyLights`/panel/overlay line builders — not `render()`, smoke-tested manually per this repo's convention), `test_main.py`'s `run_synth_tool()` dispatch coverage (ticket #120's scripted-`RawKeys` pattern extended with `_ScriptedSynthKeys`, driving the real loop headlessly against a fake sound engine: which keystroke becomes a note on which MIDI channel, full velocity, auto-repeat swallowing, Tab's layout cycle and voice budget, Shift+Arrow transpose, the fixed-duration status field, and that nothing is left sounding and the process-wide engine is restored on every exit path), and `test_main.py`'s `resolve_editor_action()` coverage (hardcoded arrow/Shift+arrow/Enter keys, every remappable score-editor action's default binding, the case-insensitive-except-undo/redo matching rule, remap honoring, defaulting to the module-level `config_store.store`), `_parse_csi_params()` (bare arrow vs. Shift-modified vs. an unrecognized-modifier fallback), and the inline header editor's `_property_field_texts()`/`_parse_property_input()`/`_handle_property_key()` (field-highlight-cycling, per-field key routing, typed-buffer accumulation/commit/backspace — not `run_score_editor()`'s loop itself, smoke-tested manually per this repo's existing convention). playback.py` (map #24: `note_frequency()`'s MIDI-tuning math, `_adsr_envelope()`'s shape incl. the short-note scale-down case, `synthesize_note()`'s length/amplitude/velocity, `render_offline()`'s sample-accurate onset placement and multi-note mixing, `LiveScheduler`'s `trigger_note()`/callback voice-mixing-and-retirement logic exercised directly without ever opening a real `OutputStream` — same "pure logic unit-tested, no hardware I/O" split this suite already applies to `audio_capture.py`), `test_prototypes_display.py` (`list_prototypes()`'s sort/title-from-H1/no-README-skip/`script_path` resolution, `_find_entry_script()`'s candidate-name/sole-.py-file/ambiguous-None cases, `_wrap_readme()`'s wrapping, `_visible_slice()`'s pagination/clamping — not `run_prototypes_screen()` itself, smoke-tested manually per this repo's existing `run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention), `test_stats_display.py` (Feature 4: `compute_stats()`'s duration-summing/most-played-ranking-and-capping/sessions-by-date-grouping against synthesized `(path, events)` pairs, `_session_date()`'s filename parsing, `_format_duration()`, `stats_lines()`'s text content — not `run_stats_screen()` itself, smoke-tested manually per the same convention), `test_abc_export.py` (Concept A / Feature 2: `note_events_to_abc()`'s pitch/accidental/octave-mark/duration-digit tokens and chord-bracket rendering against synthesized column lists, `from_transcription_result()`/`from_session_log()`'s onset-hop/shared-`t` grouping and barline placement, `write_abc()`'s file-write round-trip), `test_score_editor_state.py` (issue #98: `new_blank_score()`'s defaults, `save_score()`/`load_score()` round trips — a chord column, a rest column, non-default time-signature/key/tempo, all built measure-aligned per that file's own docstring so music21's own tie-splitting-across-barlines doesn't confound the assertions — loading a `score_writer.write_score()`-produced file's missing tempo/key defaulting to 90.0/0 sharps, and `EditHistory`'s bound/undo/redo/redo-clears-on-new-edit behavior), `test_score_editor_display.py` (issue #98, UI layer: `pitch_at_row()`'s inverse relationship with `staff_map.staff_row()` and its key-signature-aware accidental defaulting, `_legend_letter()`'s key-aware legend spelling, cursor/column clamping, `toggle_note_at_cursor()`'s mutation behavior (including emptying a column to zero notes, issue #98 follow-up)/`transpose_note_at_cursor()`/`cycle_duration()`/`clear_to_rest()`/`insert_column_at()`/`delete_column_at()`'s refusal behavior, `cycle_zoom()`, `visible_column_range()`'s viewport-centering/edge-clamping, `chord_name_for_column()`'s blank-vs-recognized cases — not `render()` itself, smoke-tested manually per this repo's existing convention), `test_chord_builder_display.py` (`state_from_column()`/`notes_from_state()`'s reverse-lookup-and-reconstruction round trip, every reel's spin-and-wrap stepping, `apply_quality_preset()`, `step_root_typeahead()`'s letter-jump/accidental-nudge/exact-case behavior, `step_alias_typeahead()`/`force_commit_alias()`/`degree_alias_map()`'s buffer-then-auto-commit matching — the Up/Down-switches/Left/Right-spins key *dispatch* itself lives in `main.py`'s `_run_chord_builder()`, smoke-tested manually, not here), `test_score_properties_display.py` (each reel's spin-and-clamp/wrap stepping, `key_fifths_label()`'s pluralization — this module has no render loop left to smoke-test since issue #98's inline-header-editor follow-up), `test_score_editor_picker.py` (`score_file_paths()`'s glob, `build_menu_entries()`'s trailing "New score..." row, `move()`, `is_new_score_row()`, `resolve_new_score_path()`'s extension-appending/blank-name-fallback — not `run_score_editor_picker()`/`capture_filename()` themselves, smoke-tested manually), `test_staff_map.py`'s `key_signature_accidental()` coverage (issue #98 follow-up: order-of-sharps/order-of-flats correctness across `key_fifths` from -7 to 7), `test_kitty_keys.py`/`test_rawkeys.py` (map #99, ticket #118: the former is pure — negotiation sequences, `CapabilityProbe`'s every fallback path, `parse_key_event()` and its cross-check against `main._parse_csi_params()` itself rather than a copy, `legacy_token()`, both held-note policies; the latter drives `main.RawKeys`' real byte paths over an `os.pipe()` plus one genuine `pty.openpty()` pair — negotiation, the DA1-only and answers-nothing fallbacks with their bounded timeout and typed-ahead-input preservation, the decode queue, arrows/Shift+arrows surviving the pushed mode, focus-loss release synthesis, and `restore()`'s mode pop — so it all runs with no TTY present; real kitty wire behaviour is smoke-tested by hand, per this repo's convention), `test_synth_layout.py` (ticket #119: the built-in layouts' keys/geometry/pad numbering, the custom-layout TOML round trip, and — the tests that matter most — the **seam with `score_audition.py`**, asserting across three base octaves that every key sounds the pitch `score_audition.pitch_for_key()` would play, which is what keeps the shared tracker keyboard from drifting), `test_synth_params.py` (log-vs-linear stepping, clamp-vs-wrap, the reachable-zero cases, formatting), `test_synth_tool.py` (`resolve_action()` — including a parametrised sweep asserting **no plain letter or number is ever a command**, in any overlay state — plus `ChannelRouter`'s channel dispatch, `polyphony_for_layout()`, the overlays, and every `SynthToolState` transition), `test_synth_display.py` (colour/caption/`KeyLights`/panel/overlay line builders — not `render()`, smoke-tested manually per this repo's convention), `test_main.py`'s `run_synth_tool()` dispatch coverage (ticket #120's scripted-`RawKeys` pattern extended with `_ScriptedSynthKeys`, driving the real loop headlessly against a fake sound engine: which keystroke becomes a note on which MIDI channel, full velocity, auto-repeat swallowing, Tab's layout cycle and voice budget, Shift+Arrow transpose, the fixed-duration status field, and that nothing is left sounding and the process-wide engine is restored on every exit path), and `test_main.py`'s `resolve_editor_action()` coverage (hardcoded arrow/Shift+arrow/Enter keys, every remappable score-editor action's default binding, the case-insensitive-except-undo/redo matching rule, remap honoring, defaulting to the module-level `config_store.store`), `_parse_csi_params()` (bare arrow vs. Shift-modified vs. an unrecognized-modifier fallback), and the inline header editor's `_property_field_texts()`/`_parse_property_input()`/`_handle_property_key()` (field-highlight-cycling, per-field key routing, typed-buffer accumulation/commit/backspace — not `run_score_editor()`'s loop itself, smoke-tested manually per this repo's existing convention)., `test_patch_format.py` (map #99/issue #115: per-field defaults and the everything-optional/no-version-field posture, wrong-typed values degrading and out-of-range values clamping, `parse_patch_text()`'s longest-valid-prefix recovery from a malformed file, `select_zone()`'s key-range/velocity-band matching incl. narrowest-wins, the nearest-band-rather-than-silence gap rule and tie determinism, `choked_zones()`, bare-name sample resolution/containment and `missing_samples()`, unknown-effect-type round-trip preservation, and synth/sampler/sf2 save->load round trips — every filesystem test on `tmp_path`, never the real `~/.config/note-color/`)., `test_synth_engine.py` (map #99/ticket #113: **every claim the synth makes is checked numerically, with no audio device and nothing listened to** — oscillator alias floor by FFT against a generous harmonic guard band (a narrow one measures the analysis window's own leakage skirt, not the oscillator: it reports ~-37dBc for a table really at -113dBc) plus an unbandlimited-saw control so the metric can't be vacuous, table partial amplitudes against the ideal harmonic series, `pulse_from_saw()`'s duty cycle across widths, the filter against the analytic response of its own coefficients through `scipy.signal.freqz` (exact -3.010dB at cutoff, 12dB/octave slopes, peak gain `1/k`, pole radii inside the unit circle at every cutoff a modulation can reach), pink noise's -3dB/octave slope and make-up gain, `DahdsrEnvelope`'s stage boundaries sample-by-sample at a 1000Hz rate where one sample is one millisecond, `Lfo` phase persistence and delay gating, the cutoff modulation routing in octaves, and — the test that justifies the resumable-voice design — one block of 1024 versus two of 512, which no voice with a reset oscillator phase/filter `zi`/envelope stage/LFO phase can pass, with a companion test pinning the ragged-split case to the measured carry-over bound rather than to a false exactness). `test_sf2_playback.py` (map #99, ticket #117) is two tiers: everything above its `real library` marker runs anywhere — the availability probe against injected importers, the fd-2 redirect and the one-time log silencing (incl. its missing-symbol degradation), the downmix arithmetic, discovery/resolution on `tmp_path`, and every `SF2Engine`/`SF2Voice` decision (program selection, the velocity edge, the generation-checked note-off, stolen-voice finalization, the one-pull-per-block rule under both of its signals) against a `FakeSynth` with no FluidSynth involved; the real-library tests skip cleanly without `pyfluidsynth`/`libfluidsynth`, and the rendering ones additionally without a soundfont (`NOTE_COLOR_TEST_SOUNDFONT=<path>` points them at one — nothing is bundled), verifying numerically with the machine muted that a block is silent before any note and non-silent the very next block after note-on, decays after note-off, pulls exactly once per `SoundEngine._callback`, writes nothing to fd 2 even when polyphony is overrun, raises no driver status flags, and reproduces #102's per-block render cost at 64 voices. , and ticket #114's `effects=` routing/tail-append), `test_effects.py` (map #99, ticket #114: `Delay`'s exact-D-sample shift, geometric feedback decay, mix crossfade, damping, state persistence/reset and sub-delay chunking; **the acceptance test** — block-wise vs. one-shot `np.array_equal` on float32 across block sizes incl. one sample at a time and a many-times-wrapped buffer, for delays longer and shorter than a block, damped, 3-voice/feedback/flanger-ish choruses and a chain; `Chorus`'s -80 dBc FFT artifact floor plus a deliberately-induced per-block LFO reset shown to breach it, exact integer-centre and half-sample interpolation cases, phase carry, peak-detune arithmetic, depth/voice clamps; effect linearity to float32 epsilon (the shared-bus argument re-measured); `process()`'s never-mutates-input/float32/same-length contract; `EffectsChain` identity/ordering/nesting/propagation; the `EffectSpec`/dict/synonym/degradation/unknown-type registry incl. a real `patch_from_toml()` patch; `tail_seconds()`'s estimate and cap — plus `test_sound_engine.py`'s bus-wiring cases: empty default chain, `set_effects()` preparing to the engine's rate/block, the callback routing the summed mix through a fully-wet delay before the clip, and the tail surviving `all_notes_off()` but not `stop()`), `test_prototypes_display.py` (`list_prototypes()`'s sort/title-from-H1/no-README-skip/`script_path` resolution, `_find_entry_script()`'s candidate-name/sole-.py-file/ambiguous-None cases, `_wrap_readme()`'s wrapping, `_visible_slice()`'s pagination/clamping — not `run_prototypes_screen()` itself, smoke-tested manually per this repo's existing `run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention), `test_stats_display.py` (Feature 4: `compute_stats()`'s duration-summing/most-played-ranking-and-capping/sessions-by-date-grouping against synthesized `(path, events)` pairs, `_session_date()`'s filename parsing, `_format_duration()`, `stats_lines()`'s text content — not `run_stats_screen()` itself, smoke-tested manually per the same convention), `test_abc_export.py` (Concept A / Feature 2: `note_events_to_abc()`'s pitch/accidental/octave-mark/duration-digit tokens and chord-bracket rendering against synthesized column lists, `from_transcription_result()`/`from_session_log()`'s onset-hop/shared-`t` grouping and barline placement, `write_abc()`'s file-write round-trip), `test_score_editor_state.py` (issue #98: `new_blank_score()`'s defaults, `save_score()`/`load_score()` round trips — a chord column, a rest column, non-default time-signature/key/tempo, all built measure-aligned per that file's own docstring so music21's own tie-splitting-across-barlines doesn't confound the assertions — loading a `score_writer.write_score()`-produced file's missing tempo/key defaulting to 90.0/0 sharps, and `EditHistory`'s bound/undo/redo/redo-clears-on-new-edit behavior), `test_score_editor_display.py` (issue #98, UI layer: `pitch_at_row()`'s inverse relationship with `staff_map.staff_row()` and its key-signature-aware accidental defaulting, `_legend_letter()`'s key-aware legend spelling, cursor/column clamping, `toggle_note_at_cursor()`'s mutation behavior (including emptying a column to zero notes, issue #98 follow-up)/`transpose_note_at_cursor()`/`cycle_duration()`/`clear_to_rest()`/`insert_column_at()`/`delete_column_at()`'s refusal behavior, `cycle_zoom()`, `visible_column_range()`'s viewport-centering/edge-clamping, `chord_name_for_column()`'s blank-vs-recognized cases — not `render()` itself, smoke-tested manually per this repo's existing convention), `test_chord_builder_display.py` (`state_from_column()`/`notes_from_state()`'s reverse-lookup-and-reconstruction round trip, every reel's spin-and-wrap stepping, `apply_quality_preset()`, `step_root_typeahead()`'s letter-jump/accidental-nudge/exact-case behavior, `step_alias_typeahead()`/`force_commit_alias()`/`degree_alias_map()`'s buffer-then-auto-commit matching — the Up/Down-switches/Left/Right-spins key *dispatch* itself lives in `main.py`'s `_run_chord_builder()`, smoke-tested manually, not here), `test_score_properties_display.py` (each reel's spin-and-clamp/wrap stepping, `key_fifths_label()`'s pluralization — this module has no render loop left to smoke-test since issue #98's inline-header-editor follow-up), `test_score_editor_picker.py` (`score_file_paths()`'s glob, `build_menu_entries()`'s trailing "New score..." row, `move()`, `is_new_score_row()`, `resolve_new_score_path()`'s extension-appending/blank-name-fallback — not `run_score_editor_picker()`/`capture_filename()` themselves, smoke-tested manually), `test_staff_map.py`'s `key_signature_accidental()` coverage (issue #98 follow-up: order-of-sharps/order-of-flats correctness across `key_fifths` from -7 to 7), `test_synth_layout.py` (ticket #119: the built-in layouts' keys/geometry/pad numbering, the custom-layout TOML round trip, and — the tests that matter most — the **seam with `score_audition.py`**, asserting across three base octaves that every key sounds the pitch `score_audition.pitch_for_key()` would play, which is what keeps the shared tracker keyboard from drifting), `test_synth_params.py` (log-vs-linear stepping, clamp-vs-wrap, the reachable-zero cases, formatting), `test_synth_tool.py` (`resolve_action()` — including a parametrised sweep asserting **no plain letter or number is ever a command**, in any overlay state — plus `ChannelRouter`'s channel dispatch, `polyphony_for_layout()`, the overlays, and every `SynthToolState` transition), `test_synth_display.py` (colour/caption/`KeyLights`/panel/overlay line builders — not `render()`, smoke-tested manually per this repo's convention), `test_main.py`'s `run_synth_tool()` dispatch coverage (ticket #120's scripted-`RawKeys` pattern extended with `_ScriptedSynthKeys`, driving the real loop headlessly against a fake sound engine: which keystroke becomes a note on which MIDI channel, full velocity, auto-repeat swallowing, Tab's layout cycle and voice budget, Shift+Arrow transpose, the fixed-duration status field, and that nothing is left sounding and the process-wide engine is restored on every exit path), and `test_main.py`'s `resolve_editor_action()` coverage (hardcoded arrow/Shift+arrow/Enter keys, every remappable score-editor action's default binding, the case-insensitive-except-undo/redo matching rule, remap honoring, defaulting to the module-level `config_store.store`), `_parse_csi_params()` (bare arrow vs. Shift-modified vs. an unrecognized-modifier fallback), and the inline header editor's `_property_field_texts()`/`_parse_property_input()`/`_handle_property_key()` (field-highlight-cycling, per-field key routing, typed-buffer accumulation/commit/backspace — not `run_score_editor()`'s loop itself, smoke-tested manually per this repo's existing convention). playback.py` (map #24: `note_frequency()`'s MIDI-tuning math, `_adsr_envelope()`'s shape incl. the short-note scale-down case, `synthesize_note()`'s length/amplitude/velocity, `render_offline()`'s sample-accurate onset placement and multi-note mixing, `LiveScheduler`'s `trigger_note()`/callback voice-mixing-and-retirement logic exercised directly without ever opening a real `OutputStream` — same "pure logic unit-tested, no hardware I/O" split this suite already applies to `audio_capture.py`), `test_prototypes_display.py` (`list_prototypes()`'s sort/title-from-H1/no-README-skip/`script_path` resolution, `_find_entry_script()`'s candidate-name/sole-.py-file/ambiguous-None cases, `_wrap_readme()`'s wrapping, `_visible_slice()`'s pagination/clamping — not `run_prototypes_screen()` itself, smoke-tested manually per this repo's existing `run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention), `test_stats_display.py` (Feature 4: `compute_stats()`'s duration-summing/most-played-ranking-and-capping/sessions-by-date-grouping against synthesized `(path, events)` pairs, `_session_date()`'s filename parsing, `_format_duration()`, `stats_lines()`'s text content — not `run_stats_screen()` itself, smoke-tested manually per the same convention), `test_abc_export.py` (Concept A / Feature 2: `note_events_to_abc()`'s pitch/accidental/octave-mark/duration-digit tokens and chord-bracket rendering against synthesized column lists, `from_transcription_result()`/`from_session_log()`'s onset-hop/shared-`t` grouping and barline placement, `write_abc()`'s file-write round-trip), `test_score_editor_state.py` (issue #98: `new_blank_score()`'s defaults, `save_score()`/`load_score()` round trips — a chord column, a rest column, non-default time-signature/key/tempo, all built measure-aligned per that file's own docstring so music21's own tie-splitting-across-barlines doesn't confound the assertions — loading a `score_writer.write_score()`-produced file's missing tempo/key defaulting to 90.0/0 sharps, and `EditHistory`'s bound/undo/redo/redo-clears-on-new-edit behavior), `test_score_editor_display.py` (issue #98, UI layer: `pitch_at_row()`'s inverse relationship with `staff_map.staff_row()` and its key-signature-aware accidental defaulting, `_legend_letter()`'s key-aware legend spelling, cursor/column clamping, `toggle_note_at_cursor()`'s mutation behavior (including emptying a column to zero notes, issue #98 follow-up)/`transpose_note_at_cursor()`/`cycle_duration()`/`clear_to_rest()`/`insert_column_at()`/`delete_column_at()`'s refusal behavior, `cycle_zoom()`, `visible_column_range()`'s viewport-centering/edge-clamping, `chord_name_for_column()`'s blank-vs-recognized cases — not `render()` itself, smoke-tested manually per this repo's existing convention), `test_chord_builder_display.py` (`state_from_column()`/`notes_from_state()`'s reverse-lookup-and-reconstruction round trip, every reel's spin-and-wrap stepping, `apply_quality_preset()`, `step_root_typeahead()`'s letter-jump/accidental-nudge/exact-case behavior, `step_alias_typeahead()`/`force_commit_alias()`/`degree_alias_map()`'s buffer-then-auto-commit matching — the Up/Down-switches/Left/Right-spins key *dispatch* itself lives in `main.py`'s `_run_chord_builder()`, smoke-tested manually, not here), `test_score_properties_display.py` (each reel's spin-and-clamp/wrap stepping, `key_fifths_label()`'s pluralization — this module has no render loop left to smoke-test since issue #98's inline-header-editor follow-up), `test_score_editor_picker.py` (`score_file_paths()`'s glob, `build_menu_entries()`'s trailing "New score..." row, `move()`, `is_new_score_row()`, `resolve_new_score_path()`'s extension-appending/blank-name-fallback — not `run_score_editor_picker()`/`capture_filename()` themselves, smoke-tested manually), `test_staff_map.py`'s `key_signature_accidental()` coverage (issue #98 follow-up: order-of-sharps/order-of-flats correctness across `key_fifths` from -7 to 7), `test_kitty_keys.py`/`test_rawkeys.py` (map #99, ticket #118: the former is pure — negotiation sequences, `CapabilityProbe`'s every fallback path, `parse_key_event()` and its cross-check against `main._parse_csi_params()` itself rather than a copy, `legacy_token()`, both held-note policies; the latter drives `main.RawKeys`' real byte paths over an `os.pipe()` plus one genuine `pty.openpty()` pair — negotiation, the DA1-only and answers-nothing fallbacks with their bounded timeout and typed-ahead-input preservation, the decode queue, arrows/Shift+arrows surviving the pushed mode, focus-loss release synthesis, and `restore()`'s mode pop — so it all runs with no TTY present; real kitty wire behaviour is smoke-tested by hand, per this repo's convention), `test_synth_layout.py` (ticket #119: the built-in layouts' keys/geometry/pad numbering, the custom-layout TOML round trip, and — the tests that matter most — the **seam with `score_audition.py`**, asserting across three base octaves that every key sounds the pitch `score_audition.pitch_for_key()` would play, which is what keeps the shared tracker keyboard from drifting), `test_synth_params.py` (log-vs-linear stepping, clamp-vs-wrap, the reachable-zero cases, formatting), `test_synth_tool.py` (`resolve_action()` — including a parametrised sweep asserting **no plain letter or number is ever a command**, in any overlay state — plus `ChannelRouter`'s channel dispatch, `polyphony_for_layout()`, the overlays, and every `SynthToolState` transition), `test_synth_display.py` (colour/caption/`KeyLights`/panel/overlay line builders — not `render()`, smoke-tested manually per this repo's convention), `test_main.py`'s `run_synth_tool()` dispatch coverage (ticket #120's scripted-`RawKeys` pattern extended with `_ScriptedSynthKeys`, driving the real loop headlessly against a fake sound engine: which keystroke becomes a note on which MIDI channel, full velocity, auto-repeat swallowing, Tab's layout cycle and voice budget, Shift+Arrow transpose, the fixed-duration status field, and that nothing is left sounding and the process-wide engine is restored on every exit path), and `test_main.py`'s `resolve_editor_action()` coverage (hardcoded arrow/Shift+arrow/Enter keys, every remappable score-editor action's default binding, the case-insensitive-except-undo/redo matching rule, remap honoring, defaulting to the module-level `config_store.store`), `_parse_csi_params()` (bare arrow vs. Shift-modified vs. an unrecognized-modifier fallback), and the inline header editor's `_property_field_texts()`/`_parse_property_input()`/`_handle_property_key()` (field-highlight-cycling, per-field key routing, typed-buffer accumulation/commit/backspace — not `run_score_editor()`'s loop itself, smoke-tested manually per this repo's existing convention)., `test_patch_format.py` (map #99/issue #115: per-field defaults and the everything-optional/no-version-field posture, wrong-typed values degrading and out-of-range values clamping, `parse_patch_text()`'s longest-valid-prefix recovery from a malformed file, `select_zone()`'s key-range/velocity-band matching incl. narrowest-wins, the nearest-band-rather-than-silence gap rule and tie determinism, `choked_zones()`, bare-name sample resolution/containment and `missing_samples()`, unknown-effect-type round-trip preservation, and synth/sampler/sf2 save->load round trips — every filesystem test on `tmp_path`, never the real `~/.config/note-color/`). ), `test_prototypes_display.py` (`list_prototypes()`'s sort/title-from-H1/no-README-skip/`script_path` resolution, `_find_entry_script()`'s candidate-name/sole-.py-file/ambiguous-None cases, `_wrap_readme()`'s wrapping, `_visible_slice()`'s pagination/clamping — not `run_prototypes_screen()` itself, smoke-tested manually per this repo's existing `run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention), `test_stats_display.py` (Feature 4: `compute_stats()`'s duration-summing/most-played-ranking-and-capping/sessions-by-date-grouping against synthesized `(path, events)` pairs, `_session_date()`'s filename parsing, `_format_duration()`, `stats_lines()`'s text content — not `run_stats_screen()` itself, smoke-tested manually per the same convention), `test_abc_export.py` (Concept A / Feature 2: `note_events_to_abc()`'s pitch/accidental/octave-mark/duration-digit tokens and chord-bracket rendering against synthesized column lists, `from_transcription_result()`/`from_session_log()`'s onset-hop/shared-`t` grouping and barline placement, `write_abc()`'s file-write round-trip), `test_score_editor_state.py` (issue #98: `new_blank_score()`'s defaults, `save_score()`/`load_score()` round trips — a chord column, a rest column, non-default time-signature/key/tempo, all built measure-aligned per that file's own docstring so music21's own tie-splitting-across-barlines doesn't confound the assertions — loading a `score_writer.write_score()`-produced file's missing tempo/key defaulting to 90.0/0 sharps, and `EditHistory`'s bound/undo/redo/redo-clears-on-new-edit behavior), `test_score_editor_display.py` (issue #98, UI layer: `pitch_at_row()`'s inverse relationship with `staff_map.staff_row()` and its key-signature-aware accidental defaulting, `_legend_letter()`'s key-aware legend spelling, cursor/column clamping, `toggle_note_at_cursor()`'s mutation behavior (including emptying a column to zero notes, issue #98 follow-up)/`transpose_note_at_cursor()`/`cycle_duration()`/`clear_to_rest()`/`insert_column_at()`/`delete_column_at()`'s refusal behavior, `cycle_zoom()`, `visible_column_range()`'s viewport-centering/edge-clamping, `chord_name_for_column()`'s blank-vs-recognized cases — not `render()` itself, smoke-tested manually per this repo's existing convention), `test_chord_builder_display.py` (`state_from_column()`/`notes_from_state()`'s reverse-lookup-and-reconstruction round trip, every reel's spin-and-wrap stepping, `apply_quality_preset()`, `step_root_typeahead()`'s letter-jump/accidental-nudge/exact-case behavior, `step_alias_typeahead()`/`force_commit_alias()`/`degree_alias_map()`'s buffer-then-auto-commit matching — the Up/Down-switches/Left/Right-spins key *dispatch* itself lives in `main.py`'s `_run_chord_builder()`, smoke-tested manually, not here), `test_score_properties_display.py` (each reel's spin-and-clamp/wrap stepping, `key_fifths_label()`'s pluralization — this module has no render loop left to smoke-test since issue #98's inline-header-editor follow-up), `test_score_editor_picker.py` (`score_file_paths()`'s glob, `build_menu_entries()`'s trailing "New score..." row, `move()`, `is_new_score_row()`, `resolve_new_score_path()`'s extension-appending/blank-name-fallback — not `run_score_editor_picker()`/`capture_filename()` themselves, smoke-tested manually), `test_staff_map.py`'s `key_signature_accidental()` coverage (issue #98 follow-up: order-of-sharps/order-of-flats correctness across `key_fifths` from -7 to 7), `test_synth_layout.py` (ticket #119: the built-in layouts' keys/geometry/pad numbering, the custom-layout TOML round trip, and — the tests that matter most — the **seam with `score_audition.py`**, asserting across three base octaves that every key sounds the pitch `score_audition.pitch_for_key()` would play, which is what keeps the shared tracker keyboard from drifting), `test_synth_params.py` (log-vs-linear stepping, clamp-vs-wrap, the reachable-zero cases, formatting), `test_synth_tool.py` (`resolve_action()` — including a parametrised sweep asserting **no plain letter or number is ever a command**, in any overlay state — plus `ChannelRouter`'s channel dispatch, `polyphony_for_layout()`, the overlays, and every `SynthToolState` transition), `test_synth_display.py` (colour/caption/`KeyLights`/panel/overlay line builders — not `render()`, smoke-tested manually per this repo's convention), `test_main.py`'s `run_synth_tool()` dispatch coverage (ticket #120's scripted-`RawKeys` pattern extended with `_ScriptedSynthKeys`, driving the real loop headlessly against a fake sound engine: which keystroke becomes a note on which MIDI channel, full velocity, auto-repeat swallowing, Tab's layout cycle and voice budget, Shift+Arrow transpose, the fixed-duration status field, and that nothing is left sounding and the process-wide engine is restored on every exit path), and `test_main.py`'s `resolve_editor_action()` coverage (hardcoded arrow/Shift+arrow/Enter keys, every remappable score-editor action's default binding, the case-insensitive-except-undo/redo matching rule, remap honoring, defaulting to the module-level `config_store.store`), `_parse_csi_params()` (bare arrow vs. Shift-modified vs. an unrecognized-modifier fallback), and the inline header editor's `_property_field_texts()`/`_parse_property_input()`/`_handle_property_key()` (field-highlight-cycling, per-field key routing, typed-buffer accumulation/commit/backspace — not `run_score_editor()`'s loop itself, smoke-tested manually per this repo's existing convention). playback.py` (map #24: `note_frequency()`'s MIDI-tuning math, `_adsr_envelope()`'s shape incl. the short-note scale-down case, `synthesize_note()`'s length/amplitude/velocity, `render_offline()`'s sample-accurate onset placement and multi-note mixing, `LiveScheduler`'s `trigger_note()`/callback voice-mixing-and-retirement logic exercised directly without ever opening a real `OutputStream` — same "pure logic unit-tested, no hardware I/O" split this suite already applies to `audio_capture.py`), `test_prototypes_display.py` (`list_prototypes()`'s sort/title-from-H1/no-README-skip/`script_path` resolution, `_find_entry_script()`'s candidate-name/sole-.py-file/ambiguous-None cases, `_wrap_readme()`'s wrapping, `_visible_slice()`'s pagination/clamping — not `run_prototypes_screen()` itself, smoke-tested manually per this repo's existing `run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention), `test_stats_display.py` (Feature 4: `compute_stats()`'s duration-summing/most-played-ranking-and-capping/sessions-by-date-grouping against synthesized `(path, events)` pairs, `_session_date()`'s filename parsing, `_format_duration()`, `stats_lines()`'s text content — not `run_stats_screen()` itself, smoke-tested manually per the same convention), `test_abc_export.py` (Concept A / Feature 2: `note_events_to_abc()`'s pitch/accidental/octave-mark/duration-digit tokens and chord-bracket rendering against synthesized column lists, `from_transcription_result()`/`from_session_log()`'s onset-hop/shared-`t` grouping and barline placement, `write_abc()`'s file-write round-trip), `test_score_editor_state.py` (issue #98: `new_blank_score()`'s defaults, `save_score()`/`load_score()` round trips — a chord column, a rest column, non-default time-signature/key/tempo, all built measure-aligned per that file's own docstring so music21's own tie-splitting-across-barlines doesn't confound the assertions — loading a `score_writer.write_score()`-produced file's missing tempo/key defaulting to 90.0/0 sharps, and `EditHistory`'s bound/undo/redo/redo-clears-on-new-edit behavior), `test_score_editor_display.py` (issue #98, UI layer: `pitch_at_row()`'s inverse relationship with `staff_map.staff_row()` and its key-signature-aware accidental defaulting, `_legend_letter()`'s key-aware legend spelling, cursor/column clamping, `toggle_note_at_cursor()`'s mutation behavior (including emptying a column to zero notes, issue #98 follow-up)/`transpose_note_at_cursor()`/`cycle_duration()`/`clear_to_rest()`/`insert_column_at()`/`delete_column_at()`'s refusal behavior, `cycle_zoom()`, `visible_column_range()`'s viewport-centering/edge-clamping, `chord_name_for_column()`'s blank-vs-recognized cases — not `render()` itself, smoke-tested manually per this repo's existing convention), `test_chord_builder_display.py` (`state_from_column()`/`notes_from_state()`'s reverse-lookup-and-reconstruction round trip, every reel's spin-and-wrap stepping, `apply_quality_preset()`, `step_root_typeahead()`'s letter-jump/accidental-nudge/exact-case behavior, `step_alias_typeahead()`/`force_commit_alias()`/`degree_alias_map()`'s buffer-then-auto-commit matching — the Up/Down-switches/Left/Right-spins key *dispatch* itself lives in `main.py`'s `_run_chord_builder()`, smoke-tested manually, not here), `test_score_properties_display.py` (each reel's spin-and-clamp/wrap stepping, `key_fifths_label()`'s pluralization — this module has no render loop left to smoke-test since issue #98's inline-header-editor follow-up), `test_score_editor_picker.py` (`score_file_paths()`'s glob, `build_menu_entries()`'s trailing "New score..." row, `move()`, `is_new_score_row()`, `resolve_new_score_path()`'s extension-appending/blank-name-fallback — not `run_score_editor_picker()`/`capture_filename()` themselves, smoke-tested manually), `test_staff_map.py`'s `key_signature_accidental()` coverage (issue #98 follow-up: order-of-sharps/order-of-flats correctness across `key_fifths` from -7 to 7), `test_kitty_keys.py`/`test_rawkeys.py` (map #99, ticket #118: the former is pure — negotiation sequences, `CapabilityProbe`'s every fallback path, `parse_key_event()` and its cross-check against `main._parse_csi_params()` itself rather than a copy, `legacy_token()`, both held-note policies; the latter drives `main.RawKeys`' real byte paths over an `os.pipe()` plus one genuine `pty.openpty()` pair — negotiation, the DA1-only and answers-nothing fallbacks with their bounded timeout and typed-ahead-input preservation, the decode queue, arrows/Shift+arrows surviving the pushed mode, focus-loss release synthesis, and `restore()`'s mode pop — so it all runs with no TTY present; real kitty wire behaviour is smoke-tested by hand, per this repo's convention), `test_synth_layout.py` (ticket #119: the built-in layouts' keys/geometry/pad numbering, the custom-layout TOML round trip, and — the tests that matter most — the **seam with `score_audition.py`**, asserting across three base octaves that every key sounds the pitch `score_audition.pitch_for_key()` would play, which is what keeps the shared tracker keyboard from drifting), `test_synth_params.py` (log-vs-linear stepping, clamp-vs-wrap, the reachable-zero cases, formatting), `test_synth_tool.py` (`resolve_action()` — including a parametrised sweep asserting **no plain letter or number is ever a command**, in any overlay state — plus `ChannelRouter`'s channel dispatch, `polyphony_for_layout()`, the overlays, and every `SynthToolState` transition), `test_synth_display.py` (colour/caption/`KeyLights`/panel/overlay line builders — not `render()`, smoke-tested manually per this repo's convention), `test_main.py`'s `run_synth_tool()` dispatch coverage (ticket #120's scripted-`RawKeys` pattern extended with `_ScriptedSynthKeys`, driving the real loop headlessly against a fake sound engine: which keystroke becomes a note on which MIDI channel, full velocity, auto-repeat swallowing, Tab's layout cycle and voice budget, Shift+Arrow transpose, the fixed-duration status field, and that nothing is left sounding and the process-wide engine is restored on every exit path), and `test_main.py`'s `resolve_editor_action()` coverage (hardcoded arrow/Shift+arrow/Enter keys, every remappable score-editor action's default binding, the case-insensitive-except-undo/redo matching rule, remap honoring, defaulting to the module-level `config_store.store`), `_parse_csi_params()` (bare arrow vs. Shift-modified vs. an unrecognized-modifier fallback), and the inline header editor's `_property_field_texts()`/`_parse_property_input()`/`_handle_property_key()` (field-highlight-cycling, per-field key routing, typed-buffer accumulation/commit/backspace — not `run_score_editor()`'s loop itself, smoke-tested manually per this repo's existing convention)., `test_wav_io.py`/`test_sampler.py` (map #99/ticket #116: the RIFF reader across PCM/float/extensible/stereo/truncated inputs cross-checked against stdlib `wave`-written files, `smpl` loop parsing, `import_sample()`'s copy/bare-name/collision/rejection behavior; and the sampler verified numerically with no audio device — FFT-measured pitch-shift ratios per semitone and per source sample rate (22050/48000/96000), time-stretch as the honest sampler tradeoff, loop-seam continuity as a slope bound, one-shot-ignores-note-off vs. loop-releases, attack ramp, choke frame counts incl. choke-during-release, RMS gain/volume/velocity routing, `SampleCache` mtime invalidation, `SamplerEngine`'s key+velocity-layer selection, hi-hat choke both directions, missing-sample silence with the rest of the kit sounding, an import->save->load->play kit round trip with bare names only, and the engine driven through `SoundEngine._callback()` directly — every WAV synthesized into `tmp_path`, never the real `~/.config/note-color/samples/`), `test_patch_format.py` (map #99/issue #115: per-field defaults and the everything-optional/no-version-field posture, wrong-typed values degrading and out-of-range values clamping, `parse_patch_text()`'s longest-valid-prefix recovery from a malformed file, `select_zone()`'s key-range/velocity-band matching incl. narrowest-wins, the nearest-band-rather-than-silence gap rule and tie determinism, `choked_zones()`, bare-name sample resolution/containment and `missing_samples()`, unknown-effect-type round-trip preservation, and synth/sampler/sf2 save->load round trips — every filesystem test on `tmp_path`, never the real `~/.config/note-color/`)., `test_log_import.py` (map #99, ticket #122: the grid helpers, `tempo_from_events()`'s bpm-estimate-else-reference fallback, and every `quantize_columns()` behaviour — chord collapsing, the rest inserted for a note that stopped early, a leading rest, a sub-grid note still becoming a column, re-quantizing the same untouched events at another grid — plus one `import_log()` file round trip), `test_session_recorder.py`'s played-note coverage (the four new optional fields, null hop fields, `t` measured from the take's first note, several notes in flight pairing by key, a pad hit's pad number, nothing recorded before arming, a still-held note truncated rather than dropped on close, `duration_class` derived against the reference tempo, and a played log grouping correctly through `session_player`), `test_score_editor_picker.py`'s log-row coverage, and `test_main.py`'s synth-recording dispatch (the real `run_synth_tool()` loop driven headlessly by the same scripted-`RawKeys` pattern #119/#120 established: `Shift`+S arming and the `rec=` status field, a played note's recorded shape and pitch, a pad hit, nothing before arming, a still-held note finalized on the way out, a session-owned recorder surviving the tool, and one play -> record -> import round trip). |

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
virtualnote transcribe song.wav --export-abc out.abc               # same, plus a hand-rolled ABC notation text file
virtualnote transcribe song.wav --play                            # same, plus oscillator+ADSR playback afterward
virtualnote replay session_log_20260101_120000.jsonl              # replay a recorded session through the tab view
virtualnote replay session.jsonl --speed 2                         # same, at 2x the original pacing
virtualnote replay session.jsonl --play                             # same, plus live audio playback per note
virtualnote edit song.musicxml                                        # terminal score editor -- loads it, or creates it blank
virtualnote edit session_log_20260101_120000.jsonl                     # quantize a recording into a score and open it
virtualnote edit session.jsonl --grid eighth --tempo 120 --out my.musicxml  # same, at a chosen grid/tempo/target path
virtualnote synth                                                      # standalone synth -- play the QWERTY keyboard and pads
.venv/bin/python -m pytest tests/                              # run the test suite
```

`virtualnote` (on PATH via `~/.local/bin/virtualnote`, added to `~/.zshrc`'s
PATH) is the one entry point for every tool this project offers (issue
#40), retiring the old per-tool `colorize` bash dispatcher. `pyproject.toml`
(architecture-modernization-plan.md §5) declares the same entry point as a
standard `[project.scripts]` console script, so `pip install -e .` also
works for local development instead of relying solely on the hand-written
`~/.local/bin/virtualnote` shim's hardcoded absolute paths; `librosa`/
`music21` (batch-only, never on the live path — see Key design decisions)
live in an optional `[project.optional-dependencies] batch` extra,
`pip install -e .[batch]` for `virtualnote transcribe`/`--write-score`.
SF2 soundfont playback (map #99, ticket #117) lives behind its own third
extra, `pip install -e .[sf2]` — kept separate from `[synth]` because
`pyfluidsynth` additionally needs the **system** `libfluidsynth` (Arch
`fluidsynth`, Debian `libfluidsynth3`, Homebrew `fluid-synth`) and so can
fail in a way no pure-Python extra can; `sf2_playback.py` degrades to
"unavailable" with a reason rather than crashing when either half is
missing, and no soundfont is bundled (one is discovered — see below).
Bare
`virtualnote` opens an animated ANSI menu (`menu_display.py`, issue #42's
design, built in #51) to pick a tool live — a spinning ASCII donut
re-skinned with the circle-of-fifths palette (rim letters in full mode)
beside the tool list: the four audio tools above, a `Settings` entry
(issue #43) for editing keybind remaps and per-note color overrides live
(see the Config file section below), a `Credits` entry (issue #44)
with full attribution, a `Prototypes` entry for running or reading
every `prototypes/*/` entry from inside the app (see `prototypes_display.py`
in the Files table above) — Enter runs the selected prototype's own
demo/harness script live, right in the terminal, so it can actually be
watched working instead of only read about; `i` opens its README for
context — and a `Stats` entry (Feature 4 in
`docs/research/notation-and-feature-ideas.md`) that aggregates every
`session_log_*.jsonl` the `S` session-recording keybind has ever written
into a summary screen: total logged practice time, most-played notes, and
a sessions-by-date breakdown (see `stats_display.py` in the Files table
above) — any key returns to the menu, same convention as Credits; the menu screen itself also names the author and a
clickable donation link (`config.AUTHOR_NAME`/`DONATION_URL`) right below
the title, regardless of which entry is selected. A performance-mode
fallback (half raster, coarser sampling, no letters, half framerate) is
auto-detected at startup for weaker hardware; `--menu-perf-mode
{auto,full,perf}` overrides it (as does `config.toml`'s
`[preferences].menu_perf_mode`, checked when the flag is omitted). Narrow
terminals drop the donut entirely and show a centered text-only list.
`virtualnote <view> [flags]` goes straight to an audio tool instead,
replicating every flag `colorize` used to forward (`settings`/`credits`/
`prototypes`/`stats` have no direct-launch form, menu-only). Both paths run through the same
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
(see below); while frozen, `R` triggers a non-causal rhythm re-analysis,
`Left`/`Right` scroll back/forward through retained history (issue
#77, see below), and `Enter` plays back what's on screen (map #99,
ticket #121, see below).

`S` toggles opt-in live session recording, available in every terminal
view (fill/wheel/tab; GUI has no live-hotkey mechanism, same established
out-of-scope precedent as chord mode's `P`) — and, as `Shift`+S, in the
standalone synth tool (map #99, ticket #122; plain `S` plays a note
there). Same field, same off-by-default posture, same file. Off by default — pressing
`S` opens a plain-text `session_log_<timestamp>.jsonl` file next to
`main.py` and appends one JSON line per finalized note (mono or
chord-tone) from then on; pressing it again closes the file. Current
state shown in the status line (`rec=on/off`). Hooked directly into
`analysis_loop()` (`session_recorder.py`'s `SessionRecorder`), the same
per-hop placement `ReanalysisBuffer` uses (issue #77) rather than the
render thread — `result_queue` is single-slot and overwrite-on-full, so a
render-thread-side recorder would silently miss a finalized note whenever
two hops complete between two polls. Barlines aren't captured in v1 (that
bookkeeping is `tab`-view-only, render-thread-side); each note's `bpm_
estimate` is still logged, so approximate bar boundaries are
reconstructable later if needed. `SessionState` owns one `SessionRecorder`
for the process's whole life (like `sensitivity`/`source_state`), so
recording state and its file both survive `|` back-to-menu tool switches
the same way.

**Recording the synth, and importing a recording (map #99, ticket #122,
decision #110).** `Shift`+S in the synth tool arms the *same* recorder
writing the *same* `session_log_*.jsonl`, extended additively with four
optional fields — `source` (`"played"`; **absent means `"detected"`**, so
every log written before this feature stays valid), `velocity` (MIDI
0-127), `patch`, `pad`. `virtualnote replay` therefore plays a synth
recording back with no changes at all. Timing is wall-clock
(`time.perf_counter()` at note-on, measured at note-off), `t` runs from
the take's first played note, and `duration_hops`/`bpm_estimate` are
written `null` — they mean nothing without a hop-driven pipeline, and a
synthetic hop clock would put a fictional number in a file people read by
hand. A no-key-release terminal's fixed-duration notes are recorded
exactly as played.

Nothing is quantized at capture: a rounded log cannot be un-rounded.
Rounding happens **on import into the editor**, at a selectable grid
(`log_import.py`) — from the menu, the score editor's picker lists
recordings alongside score files and prompts for a grid; from the CLI,
`virtualnote edit <log>.jsonl [--grid N] [--tempo BPM] [--out PATH]`. The
imported score opens *unsaved* at a `.musicxml` sibling path, so a grid
that turned out wrong costs one quit without saving and can simply be
redone against the untouched log. **Not** in scope (map #99 fog):
recording directly into an open score, and always-on recording with an
explicit save.

`virtualnote replay <file> [--speed N] [--dump-file PATH]` plays a
recorded `.jsonl` session log back through a real `TabDisplay` instead of
live audio — the note/chord/barline events `S` recorded reappear on
screen in their original order, paced by their real recorded timestamp
gaps (`--speed 2` replays twice as fast; default `1.0` is real time).
Standalone, offline, no mic/`SessionState` touched at all, same
`transcribe`-shaped CLI entry point (`main.run_replay_session()`); Ctrl+C
stops a replay early and still writes the on-quit ANSI dump
(`--dump-file`, same default-path convention as `tab`/`transcribe`) for
whatever was replayed up to that point. Doesn't replay raw audio (none
was ever recorded) — only whatever pitch/duration/tempo/chord data
`SessionRecorder` logged, so a replay looks like the original session but
can't literally sound like it — unless `--play` (below) is also passed,
which reconstructs an approximation of it via synthesis.

**Score playback (map #24, decision #32).** `virtualnote transcribe
song.wav --play` and `virtualnote replay session.jsonl --play` both add
audio playback through a NumPy oscillator+ADSR synth (`playback.py`) —
no soundfont/sample library, no new dependency (reuses
`sounddevice.OutputStream`, already a dependency via the mic-input side).
`transcribe --play` pre-renders the whole transcription to one buffer and
plays it back after transcription finishes; `replay --play` instead
triggers each note's synthesis live, in step with the note's own column
appearing on screen (so it speeds up/slows down together with
`--speed`). Playback is a genuine approximation, not the original
recording — a plain harmonic-stack tone, not the instrument that was
actually played — reflecting exactly (and only) whatever pitch/duration
data the detection pipeline itself captured, same honesty caveat as the
visual replay above. Never touches the live mic/`SessionState` path
either way.
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
`virtualnote transcribe <file> [--dump-file PATH] [--time-signature N/D]
[--write-score [PATH]] [--play]` runs the same rhythm/duration/tempo
detection offline against a pre-recorded audio file instead of live
input — no terminal window, no mic, just a `dump_ansi()`-format text dump
written on completion (same convention/default path as `tab`'s own
on-quit dump). `--play` (map #24's playback engine, see the Score
playback section above) plays the transcription back through an
oscillator+ADSR synth once transcription finishes.

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

**Loop/section markers, `tab`-view-only, freeze-mode-only.** While
frozen, `[`/`]` (remappable, `[keybinds].mark_range_start`/
`mark_range_end`, default `"["`/`"]"`) each mark one end of a range at
"the point in history currently being looked at" — i.e. respecting
whatever `Left`/`Right` scrollback position is active when the key is
pressed, not always the live tail — and, once both ends are set, scope a
subsequent `R`-key reanalysis to just that marked range instead of the
whole rolling buffer. Order-independent (`]` before `[` normalizes the
same as `[` before `]`); the current range (or a single placed end) shows
in the status line (`mark=[1.20s,3.45s]`, or `mark=[1.20s,...]` with only
one end set) whenever at least one mark is placed. Resets on unfreeze,
same "no catch-up" convention scroll_offset/the reanalysis-corrected
tempo already follow.

**Frozen-buffer playback, `tab`-view-only, freeze-mode-only (map #99,
ticket #121, decision #109).** While frozen, `Enter` (hardcoded, not
remappable) plays what is on screen through the sound engine, and a
second `Enter` stops it. Scope is the `[`/`]` marked range if one is set,
otherwise every column currently visible — the same rule the score
editor's own audition uses, so one gesture means one thing app-wide. In
chord mode every note of a column's detected stack sounds, each with its
own tracked duration: the honest playback of what was actually detected,
not a re-voiced chord name (which would sound better precisely by hiding
detection errors there is reason to hear). Column-to-column pacing comes
from the columns' own recorded timestamps (so real gaps, rushes and
pauses survive); each note's *length* comes from its measured
`duration_class` converted against whatever tempo the status line shows
(the `R`-corrected estimate if one exists, else the live one, else
`config.TAB_PLAYBACK_DEFAULT_BPM`), clamped at both ends. Status line
shows `play=<N>notes(enter)` while playing, or
`play=unavailable(<reason>)` if no sound engine could be started (e.g.
SciPy, the optional `[synth]` extra, isn't installed — a missing optional
dependency never takes the view down). Unfreezing stops playback, same
"no catch-up on unfreeze" convention every other frozen-only piece of
state here follows; nothing else about the frozen view (scrollback
position, marks, corrected tempo) is disturbed. **Live-view sonification
does not exist and is not coming**: #109 dropped it on a measured ~163ms
detection-to-sound latency (46ms window fill + 69.7ms `DEBOUNCE_HOPS`
lock-in + 34.8ms output stream + 11.6ms block) that can't be reduced
without damaging detection itself. `fill`/`wheel` keep no note history,
so the feature has no meaning there and they gain no freeze.

**Score editor (map #85, issue #98).** `virtualnote edit <path>` opens a
terminal editor for a MusicXML file — loads it if it exists
(`score_editor_state.load_score()`), otherwise starts a brand-new blank
score to be saved to `<path>` later (`new_blank_score()`). Never touches
the mic/`SessionState`, same as `transcribe`/`replay`; also reachable
live from the menu's `Edit` entry, which shows a picker over existing
`*.musicxml`/`*.xml` files next to `main.py` plus a "New score…" filename
prompt (`score_editor_picker.py`). Cursor = `(column, staff row)` on a
fixed grand staff (`score_editor_display.py`, extending `tab`'s rendering
approach into a random-access buffer instead of a live-scrolling one —
see `CONTEXT.md`'s Score editor glossary):

| Key | Keybind name | Action |
|---|---|---|
| Left/Right | (hardcoded) | move cursor between columns |
| Up/Down | (hardcoded) | move cursor between staff rows |
| Space | `note_toggle` | place/remove a note at the cursor — spelled per the active key signature by default (e.g. G major's F row places F♯); can empty a column all the way to a Rest, same as removing any other note (reversed from an earlier "refuses to remove the last note" rule — direct user feedback, see docs/DECISIONS.md) |
| Shift+Up/Shift+Down | (hardcoded) | shift the note under the cursor a semitone — hardcoded, not remappable, replacing an earlier remappable `+`/`-` (too far from the arrow keys already used for cursor movement — see docs/DECISIONS.md) |
| `,`/`.` | `duration_shorten`/`duration_lengthen` | step the column's duration through the standard note-value set |
| `r` | `clear_to_rest` | empty the column's notes outright in one press (still useful for a multi-note chord column, vs. Space's one-note-at-a-time removal) |
| `i` | `insert_column` | insert an empty (Rest) column before the cursor; cursor follows onto it |
| `x` | `delete_column` | delete the column at the cursor (refuses on the last remaining column) |
| `u`/`U` | `undo`/`redo` | multi-level, bounded (`config.EDITOR_UNDO_MAX_DEPTH`) |
| `z` | `zoom_cycle` | cycle the notehead's render detail: bare glyph → +letter → +octave → +duration text (render-only) |
| `c` | `chords_only_toggle` | swap noteheads for a lead-sheet chord-name-per-column view (render-only) |
| Enter | (hardcoded) | open the **Chord builder** for the column at the cursor |
| `w` | `save` | write to the loaded/target path |
| `t` | `score_properties` | toggle inline editing of the status line's `time=`/`key=`/`tempo=` fields |
| `P` | `piano_mode` | enter/leave **piano mode** (map #99, ticket #120) — letters become the two-octave tracker keyboard; `Esc` also leaves |
| `L` | `play_from_cursor` | play from the cursor to the end, or to the end of a marked `[`/`]` loop region; the view follows the playhead, any key stops |
| `M` | `metronome_toggle` | click along with playback, on the score's own `tempo=`/`time=` |
| `A` | `audition_toggle` | sound notes on cursor movement (default on; placement always sounds regardless) |
| `[`/`]` | `mark_range_start`/`mark_range_end` | mark a loop region at the cursor's column — the `tab` view's own mark keys (issue #77), order-independent |
| `\|`/`H` | (global) | back-to-menu / legend toggle, unaffected |

Status line shows `saved=yes/no` and, at all times (not just while
editing them), the score's `time=`/`key=`/`tempo=` fields — mirroring
`tab`'s own always-visible `tempo=`/`time=` status-field convention —
plus (ticket #120) `mode=edit/piano`, `audition=`, `metro=`, `oct=` (in
piano mode only), `loop=` (once a mark is placed) and `sound=unavailable`
(only when there's no audio engine at all).
Quitting (`|`/Ctrl+C) while `saved=no` needs a second confirming press of
the same key before actually discarding changes — the one editor view in
this app where quitting can lose real work, unlike every other terminal
view's purely ephemeral render state.

The **Chord builder** (Enter on a column, `chord_builder_exit` — default
`b` — to leave) has five independently spinnable/typeahead-able Reels
(root/quality/3rd/5th/7th), drawn as five stacked rows: Up/Down switches
the focused reel, Left/Right spins it (matching the reels' vertical
layout — swapped from an earlier Left/Right-switches/Up/Down-spins
binding inherited unchanged from the prototype this screen was built
from; direct user feedback found that backwards for a vertical list, see
docs/DECISIONS.md), and typing jumps it (a natural letter on ROOT, e.g.
`F`, jumps straight there — `#`/`b` immediately after nudges it a
semitone; a mnemonic like `m7`/`dim`/`b5`/`sus4` on the other reels
auto-commits the instant it's unambiguous). Every spin/typeahead applies
live to the screen's own working chord; `b` commits it into the real
column on exit. Root is ordered around the circle of fifths, same
theming every other fifths-scheme view in this app already uses.

**Inline header editor** (`t`/`score_properties` toggles it — no longer a
separate screen; a post-#98 hands-on-feedback follow-up retired the
original "Score properties" screen, reversing #90's original call,
because leaving the main view for this was unwanted friction, see
docs/DECISIONS.md). Highlights one of the always-visible `time=`/`key=`/
`tempo=` status-line fields at a time: Left/Right moves the highlight
(a *horizontal* strip of fields, opposite the Chord builder's vertical
Up/Down — different widget shapes, not an inconsistency), Up/Down spins
the highlighted field's value exactly as the old screen's reels did
(`score_properties_display.spin_time_signature()`/`spin_key_fifths()`/
`spin_tempo()`, reused unchanged), and typing digits (plus `/` for time
signature) opens a direct-entry buffer on the two typable fields — time
signature as free-form `N/D` (not snapped to the fixed-set spin), tempo
as a plain BPM number — shown in place of the field's normal value while
typing. Enter parses+applies any pending typed buffer and returns to
normal cursor editing; mutations apply directly to the real `EditorScore`
as they happen, same "no separate commit step" convention the old screen
already used.

**Piano mode, audition, playback, metronome, loop region (map #99,
ticket #120, decision #108).** `P`/`piano_mode` switches the editor's
letter keys from commands to a two-octave tracker keyboard
(`zsxdcvgbhnjm` = C..B, `q2w3er5t6y7u` = the octave above); the same key
or `Esc` leaves, and the status line always names the active mode
(`mode=edit`/`mode=piano`). Shift+Up/Shift+Down moves the keyboard's
octave in piano mode (it stays issue #98's transpose in edit mode);
`,`/`.`, the arrows and `[`/`]` all still work there. Keys pressed
**together** land in one column (a chord); keys pressed **in sequence**
fill successive columns, appending to the score when a run passes the
last one and inheriting that column's duration — a distinction only the
kitty protocol's key releases can report, so this is the one view
constructed with `RawKeys(want_kitty=True)`, degrading on any other
terminal to place-without-advancing (`score_audition.PianoEntry`).
Entered notes take the editor's current `,`/`.` duration, never how long
the key was held. Placing a note always auditions it; moving the cursor
auditions too, toggleably (`A`/`audition_toggle`, default on).
`L`/`play_from_cursor` plays to the end of the score, or to the end of a
`[`/`]` marked loop region (the `tab` view's own mark gesture applied to
columns — order-independent, same `_mark_range()`); the view scrolls to
follow the playhead, which renders reverse-video, and any key stops.
`M`/`metronome_toggle` adds a click on the score's own `tempo=`/`time=`,
downbeat a fifth higher. Entering piano mode and playing back are both
strictly non-dirtying — only actually writing a note is an edit. With no
audio engine available (no `[synth]` extra, no output device) the editor
opens and works exactly as before, silently, showing `sound=unavailable`.

**Synth tool (map #99, ticket #119, decision #107).** `virtualnote synth`,
or the menu's `Synth` entry, opens a standalone instrument. Never touches
the mic/`SessionState` (same as `edit`/`transcribe`/`replay`), but does
use this process's one `SoundEngine`, so switching menu -> synth ->
editor -> a live view never tears down and reopens the output device. The
screen is a **parameter panel above an always-visible input layer below**:
the input layer shows exactly the keys the current layout plays, in their
physical keyboard arrangement, tinted by pitch class in the fifths palette
(so a C here is the colour a detected C already is everywhere else) and lit
on press via `animation.ColorAnimator`. Pads tint by their assigned sample
instead — a pad has no pitch to be honest about.

**Every letter and number always plays a note.** There is no state in this
tool in which pressing a key does something other than sound — including
while a load/save/import overlay is open, and including while typing a
patch name into the save overlay (which both types the character and
sounds the note; the overlay says so on screen). That invariant decides
the whole key map by elimination:

| Key | Action |
|---|---|
| letters / numbers | play a note or hit a pad, at **full velocity** — no faked dynamics (real dynamics come from velocity-layered samples and, later, a MIDI controller) |
| `Tab` | cycle layouts — the switch key precisely because nearly every other key plays a note in *some* layout |
| Up/Down | select a parameter |
| Left/Right | sweep the selected parameter's value |
| Shift+Left/Right | coarse sweep (`SYNTH_PARAM_COARSE_STEPS`, ten ordinary presses) |
| Shift+Up/Down | transpose the note keys an octave (pads never transpose — a kick is a kick) |
| `Shift+P` / `Shift+W` / `Shift+I` | patch browser / save patch / import a sample — all **inline overlays** over the panel, never separate screens; the instrument stays visible and playable underneath |
| `Shift+N` / `Shift+B` / `<` `>` / `Shift+L` | start a custom layout by copying the active one / cycle the last-played key's binding kind / nudge its value / save the layout to its own file |
| `Shift+M` | all notes off (the stuck-note escape) |
| `Shift+H` | help legend (`h` itself plays a note here) |
| `\|` | back to the menu, as everywhere else |

None of the `Shift` bindings is remappable through `[keybinds]`, unlike
this app's other 22 — a remap onto a plain letter would silently break the
always-plays invariant. Same hardcoded tier as the score editor's
Shift+Arrow transpose; see `docs/DECISIONS.md`.

**Four layouts, cycled with `Tab`:** (1) two-octave tracker keyboard
(`zsxdcvgbhnjm` / `q2w3er5t6y7u`, black keys where they physically sit — the
same keyboard the score editor's piano mode plays, shared rather than
copied), no pads; (2) one octave plus a row of eight pads — the one layout
that plays a **kit and a synth patch simultaneously**, which is why it
gets its own lower voice budget (`polyphony_synth_dual`, a Settings-screen
field: one cap now feeds two hands, so the risk is a drum hit arriving to
find every slot held by sustained synth notes); (3) the 4x4 pad square
(`1234`/`qwer`/`asdf`/`zxcv`, numbered bottom-row-first like hardware);
(4) any custom layout found in `~/.config/note-color/layouts/*.toml` —
its own file, independent of patches, because a layout describes your
hands, not a sound.

Status line carries `layout=`/`oct=`/`patch=`/`kit=`/`voices=` and, always,
`keys=`: on a terminal that reports key releases it reads `keys=held` and a
held key sustains; on one that doesn't it reads
`keys=fixed 0.35s (no key release)` and every note is that fixed length.
Saying so plainly is deliberate — it is what keeps "why won't notes
sustain?" from becoming a bug report rather than a documented degradation.
The tool still opens and works either way; a drum pad in particular is
perfectly usable with fixed-duration one-shots.

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

- `[keybinds]` — remap any of the nine terminal hotkeys (`source_toggle`,
  `chord_mode_toggle`, `notehead_style_toggle`, `legend_toggle`,
  `freeze_toggle`, `rhythm_reanalysis`, `session_record_toggle`,
  `mark_range_start`, `mark_range_end`) plus the score editor's seventeen
  (issue #98 — `note_toggle`,
  `duration_shorten`, `duration_lengthen`, `clear_to_rest`,
  `insert_column`, `delete_column`, `undo`, `redo`, `zoom_cycle`,
  `chords_only_toggle`, `chord_builder_exit`, `save`, `score_properties`;
  ticket #120 — `piano_mode`, `play_from_cursor`, `metronome_toggle`,
  `audition_toggle`, all defaulting to a Shift+letter since plain-letter
  space is nearly exhausted and piano mode claims a two-octave block of
  it, and all four matched **exact-case** like `undo`/`redo` because `m`
  is a note while `M` is the metronome; the editor's loop region reuses
  `mark_range_start`/`mark_range_end` above rather than adding a second
  pair)
  to a different single character, e.g.
  `source_toggle = "x"`. The score editor's transpose (Shift+Up/Shift+Down)
  and every arrow/Enter key are hardcoded instead, never remappable here,
  as is every one of the synth tool's own `Shift`+key commands (ticket
  #119 — a remap onto a plain letter would silently break that tool's
  always-plays invariant), **including its `Shift`+S recording arm**
  (ticket #122, deliberately contradicting decision #110's "remappable
  like every other keybind" for that reason; `rec=`'s meaning is still
  identical across all four views, which is what #110 was asking for —
  see docs/DECISIONS.md) —
  same tier as this app's other hardcoded arrow-key handling (see the
  Score editor section above and docs/DECISIONS.md). `rhythm_reanalysis` (default `"r"`) is the
  tab view's freeze-mode-only non-causal rhythm re-analysis trigger
  (issue #77); `session_record_toggle` (default `"s"`) is the opt-in live
  session-log recorder toggle, available in every terminal view;
  `mark_range_start`/`mark_range_end` (default `"["`/`"]"`) are the tab
  view's freeze-mode-only loop/section markers that scope a subsequent
  `rhythm_reanalysis` press to just the marked range. `undo`/`redo`
  (default `"u"`/`"U"`) are matched case-sensitively by
  `main.run_score_editor()`, unlike every other keybind here (matched
  case-insensitively) — they deliberately share a letter, distinguished
  only by case; see docs/DECISIONS.md. The status line's hotkey hints
  (`(m)`, `(p)`, etc.) reflect the remap. Editable live from the menu's
  Settings screen (below), or by hand.
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
  `config.TAB_SCROLLBACK_SECONDS`; `polyphony_standalone` (default `40`)
  and `polyphony_with_detection` (default `24`), both valid range 1–128,
  step 4 — the sound engine's hard voice cap (map #99, decision #105) in
  each of its two contexts, measured rather than guessed (prototype
  #100), see `config.POLYPHONY_STANDALONE`/`POLYPHONY_WITH_DETECTION` and
  `sound_engine.polyphony_for()`; and `polyphony_synth_dual` (default
  `28`, same range and step) — the third context (ticket #119), the synth
  tool's layout 2 playing a kit and a synth patch from one cap, selected
  by `synth_tool.polyphony_for_layout()` through
  `SoundEngine.set_polyphony_override()` rather than a third branch
  inside `polyphony_for()`, so `sound_engine.py` stays ignorant of what a
  layout is. One non-numeric, hand-edit-only entry
  joins `menu_perf_mode`: `soundfont_path` (map #99, ticket #117) — an
  explicit path to an SF2/SF3 soundfont for the SF2 engine, layered over
  `sf2_playback.discover_soundfonts()`'s search of the samples directory,
  the XDG data dir, the standard system locations and a Homebrew prefix.
  Nothing is bundled, so with no soundfont installed anywhere the engine
  reports "no soundfont found" as status. All numeric fields are read/written
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
- `multipitch.detect()` bounds every candidate peak's frequency to
  `min_freq_hz`/`max_freq_hz` (default `DEFAULT_MIN_FREQ_HZ`/
  `DEFAULT_MAX_FREQ_HZ`, 65-1000Hz — issue #74) before any pruning — until
  this fix, `detect()` had no frequency-range bound at all, unlike the
  monophonic path (`pitch_detect.detect_pitch()`, bounded by
  `config.FMIN`/`FMAX`). A new acoustic-test suite (`percussion`,
  `scripts/acoustic_pipeline_test.py`) found a hi-hat's high-passed
  broadband noise (6-11kHz, ~3-4 octaves above `FMAX`) peak-picked as
  phantom notes at octave 8-9, occasionally even forming a spuriously
  "confident" chord name from pure percussive noise with zero pitched
  content playing. Reuses `config.FMIN`/`FMAX` directly rather than a
  separate polyphonic-only range — multipitch detects notes from the same
  real instrument register the monophonic path already targets, just more
  than one at a time, so there's no principled reason a chord's individual
  notes would plausibly sit outside YIN's own already-established range;
  confirmed by a direct sweep of this app's own chord-mode tests (up to
  B5, the 6-note dense-chord and harmonic-near-miss tests among them) that
  none get excluded by it. `main.py`/`batch_transcribe.py`'s real call
  sites pass `config.FMIN`/`config.FMAX` explicitly, same convention as
  every other `config.CHORD_*` constant already passed at those call
  sites. Measured on the `--source loopback` percussion suite: false-chord
  rate on a sustained beat-only drum pattern (kick/snare/hi-hat, no
  pitched content) dropped from 13.1-13.8% to 0%, non-empty false
  note-stack rate from 67-68% to ~30% (the residual ~30% is kick/snare's
  own genuine broadband energy still falling *inside* the valid pitch
  range — a separate, still-open gap this fix doesn't claim to close; see
  Known limitations). The `chords`/`density` suites' legitimate-chord
  accuracy was unaffected (100%/0 phantom, same as the pre-existing
  baseline) — see docs/DECISIONS.md for the full before/after numbers.
- `analysis_loop()` calls its two detection functions through
  `detection_backends.py`'s `MonoPitchBackend`/`PolyphonicBackend`
  Protocols (`SessionState.pitch_backend`/`poly_backend`) rather than
  calling `pitch_detect.detect_pitch()`/`multipitch.detect()` directly —
  a pure extraction, zero behavior change by default (`SessionState`
  still builds `YinBackend`/`SpectralPeakBackend` from `config.*` exactly
  as the old direct calls did). This is the seam
  `docs/research/architecture-modernization-plan.md`'s §3.1 identified as
  the actual prerequisite for any of the sibling algorithm-research docs
  (pYIN, NNLS-chroma, SwiftF0, ...) to land a finding without hand-editing
  `analysis_loop()`'s shared ~160-line body — before this, trying an
  alternative algorithm meant risking the chord/rhythm pipeline that
  shares that same function. `multipitch.select_window()`'s bass-gated
  long-window logic (issue #63) deliberately stays a plain call in
  `analysis_loop()`, not folded into `PolyphonicBackend.detect()` or the
  Protocol itself — it's YIN/spectral-peak-picking-specific window
  selection with no equivalent in any algorithm this codebase actually
  has yet, and the architecture doc explicitly warns against padding the
  Protocol with speculative params before a second real backend exists to
  design against.
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
- Mono *name* style gets its own wider column, `config.TAB_COLUMN_WIDTH_NAME`
  (9, mirroring `TAB_COLUMN_WIDTH_CHORD`'s existing precedent), selected in
  `TabDisplay.render()` only when `not chord_mode and notehead_style ==
  "name"` (issue #83) — its `f"{letter}·{suffix}"` duration text (e.g.
  "Bb·16th.") doesn't fit in the default `TAB_COLUMN_WIDTH` (3) the way
  symbol style's combining-mark duration glyphs do, and was rendering as an
  unreadable clipped stub ("C·whole" -> "C·w") before this fix.
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
- The `tab` view's frozen playback (ticket #121) reuses the renderer's own
  visible-column walk (`terminal_tab_display.select_visible_entries()`,
  extracted from `render()`) as its default scope, paces columns by their
  recorded timestamps but takes each note's *length* from its measured
  `duration_class` (a gap to the next column isn't a duration), plays at
  one fixed velocity since nothing here ever measured a per-note attack,
  and runs on a throwaway daemon thread rather than inside the render loop
  (which would quantise every onset to one 50ms frame and block the Enter
  that stops it). Full rationale in docs/DECISIONS.md.
- Loop/section markers (`mark_range_start`/`mark_range_end`) store
  timestamps, not `scroll_offset` counts or entry indices -- a timestamp
  stays meaningful as `scroll_offset` itself keeps changing across further
  Left/Right presses, where an index captured at mark-time would silently
  point at the wrong column once the view scrolls further. Captured via
  `TabDisplay.timestamp_at_offset()`, the same truncation `render(
  scroll_offset=N)` itself applies, so a mark lands on whatever column is
  actually on screen at the moment the key is pressed, not the live tail.
  `main._handle_reanalysis_key()`'s `mark_range=` param filters the
  `ReanalysisBuffer` snapshot down to that `[lo, hi]` window (via
  `main._filter_hop_records_to_range()`) *before* calling `rhythm_
  reanalysis.recompute()`, rather than teaching `recompute()` itself about
  ranges -- `recompute()` already treats an empty `hop_records` list as
  its existing "nothing to reanalyze" no-op, so a marked range with no
  hops inside it is handled for free, no new case to add there.
  Order-independent (`main._mark_range()` normalizes whichever end was
  pressed second into `(lo, hi)`) since there's no reason to require the
  user press start before end.
- `virtualnote replay` renders live (`main.run_replay_session()`) rather
  than following `run_batch_transcribe()`'s silent-sweep-then-dump shape,
  even though both are otherwise "build `TabDisplay` columns from
  already-detected note events, no live audio" — replay's whole point is
  reproducing "watch what I actually played" pacing on screen (feature
  idea 1 in `docs/research/notation-and-feature-ideas.md`), which a batch
  dump can't give; `--speed` divides the real recorded gap between
  columns rather than replacing it with a fixed rate, so the original
  performance's actual rhythm (rushed passages, pauses) survives the
  speed change instead of being flattened to a metronome.
- `session_player.group_columns()` groups "note" events sharing the exact
  same `t` into one `TabDisplay` column (a chord's tones share one `t` and
  one `chord_name` in the log, see `session_recorder.py`'s schema doc) and
  breaks a same-`t` tie between a note column and a barline column by
  placing the note column first -- a barline crossed at time `t` is
  conceptually placed just after the note that crossed it, the same order
  `run_batch_transcribe()` itself pushes them in (`push_notes()` then
  `push_barline()` within one onset_hop's iteration).
- The Prototypes screen (`prototypes_display.py`) runs a prototype's own
  demo/harness script exactly as its README's "How to run it" section
  already documents doing by hand (`.venv/bin/python
  prototypes/<name>/<script>.py`, no arguments) -- every existing
  prototype's demo is deliberately self-contained and argument-free (see
  each README), so "the no-arg script this convention already
  established" is a real, safe, generic "run it" action, not a guess;
  `_find_entry_script()` only ever resolves to that convention (a known
  demo-script name, or the sole `.py` file when unambiguous) and leaves
  an entry unrunnable rather than guessing when a prototype doesn't match
  it. Reading the README (`i`) stays available for context, but running
  the thing live -- watching its actual colored/staff output -- is now
  the primary action, since that's what "assess whether a prototype is
  worth adopting" actually benefits from over prose alone.
- Running a prototype hands the real terminal to it as a subprocess with
  stdio inherited (`_run_prototype()`), rather than capturing its output
  to redisplay inside this screen's own ANSI chrome -- a prototype's
  color/cursor-positioning output is meant to be seen exactly as it
  renders standalone (that's the whole point of watching it work), and
  re-parsing/re-emitting captured ANSI bytes through this screen's own
  rendering would risk exactly the kind of column-desync bug this
  project's `scripts/terminal_screenshot.py` (`docs/research/
  terminal-visual-capture-for-agents.md`) exists to catch, for zero
  benefit here. `RawKeys.restore()` drops this screen's own cbreak mode
  first (a subprocess expects an ordinary cooked terminal, not this
  screen's single-key polling), and a fresh `RawKeys()` re-enters it once
  the subprocess exits -- the same "construct a new instance to resume
  raw mode" pattern `run_menu_loop()` already uses per menu round-trip.
- `session-log-and-practice-mode/_repo_paths.py` appends `REPO_ROOT` to
  `sys.path` instead of inserting it at the front -- a real bug found
  while wiring up live prototype execution: this prototype's own local
  `session_recorder.py`/`session_player.py` (what the real, same-named
  repo-root modules were ported *from*) got shadowed the moment those
  real modules actually shipped, since inserting at index 0 put
  `REPO_ROOT` ahead of the script's own directory (which Python already
  puts at `sys.path[0]` automatically for a directly-run script).
  `demo.py`'s `from session_player import SessionPlayer` silently
  resolved to the real module (no `SessionPlayer` class there) instead of
  this prototype's own, crashing with an `ImportError`. Appending instead
  keeps a prototype's own same-named files authoritative for itself,
  falling back to the real repo only for names it doesn't define
  (`config`, `color_map`, `duration_tracker`). Every other prototype still
  inserts `REPO_ROOT` at the front of `sys.path` (harmless there -- none
  of them has a local file sharing a name with a real repo-root module),
  so this fix is scoped to this one prototype's own bootstrap file rather
  than a change to the convention every prototype follows.
- Score playback (`playback.py`, map #24, decision #32) is an oscillator+
  ADSR synth, not FluidSynth+soundfont -- decision #32 already settled
  this (see `gh issue view 32`/#28's research): a soundfont synth reopens
  exactly the wheel/dependency-risk tradeoff this project already avoided
  for `aubio`/`essentia` (a `pyfluidsynth` + system `libfluidsynth`
  binary dependency, plus bundling a real soundfont asset), for a timbre
  upgrade that's explicitly deferred as a future option rather than
  rejected outright. The oscillator's waveform is a small fixed harmonic
  stack (fundamental + 2nd + 3rd partial, `config.PLAYBACK_HARMONIC_
  WEIGHTS`), not a bare sine -- #28's research flagged "a few adjustable
  waveforms is the ceiling without real modelling work" as the honest
  scope for this tier; the specific weights were picked by ear, not
  measured, and are explicitly not treated as load-bearing the way e.g.
  `YIN_SUBHARMONIC_MARGIN` is.
- Playback is two genuinely different code paths, not one mode
  simulating the other, because two real callers have different natural
  shapes: `virtualnote transcribe --play` already has a complete
  `TranscriptionResult` in hand before playback starts, so
  `playback.render_offline()` pre-rendering the whole thing to one NumPy
  buffer up front is strictly simpler and free of scheduling jitter by
  construction (timing is a buffer-index computation, not a wall-clock
  one). `virtualnote replay --play` instead reuses
  `run_replay_session()`'s own already-paced `time.sleep()`-driven column
  loop, playing each note the instant its column is pushed --
  deliberately *not* pre-rendering the whole session up front (which
  would mean buffering a recording of unbounded length before playback
  could start at all) and deliberately *not* running a second,
  independent timing clock for it (which would risk the audio and the
  on-screen columns drifting apart over a long replay). Since map #99's
  ticket #112 that live half goes through `sound_engine.SoundEngine`'s
  note-on/note-off vocabulary rather than `playback.LiveScheduler`
  (deleted, not deprecated -- see docs/DECISIONS.md).
- The sound engine's polyphony budget is a **hard cap**, not load-driven,
  and never refuses a note -- prototype #100 measured the driver's ring
  buffer hiding overruns until the engine has already xrun, so the load
  signal a load-driven policy would steal against arrives too late to act
  on; and dropping the note the player just pressed is the most audible
  possible failure mode. Two `[preferences]` budgets rather than one
  (~40 standalone, ~24 with detection running) because #100 measured one
  thread doing this app's real 2048-point-FFT analysis work costing
  ~1.3ms/block mean and ~7ms p99 of the callback's deadline -- GIL
  contention, not CPU headroom, which is also why a startup hardware
  probe was rejected: the constraint is what else is running in the
  process. Full rationale and the measured before/after numbers in
  docs/DECISIONS.md.
- `SoundEngine.schedule_note_off()` is caller-side sugar resolved against
  the audio callback's own frame clock, not a second, duration-carrying
  primitive in the voice model -- a `Voice` only ever learns note-on and
  note-off (decision #105), and no timer thread or wall-clock sleep is
  involved. See docs/DECISIONS.md.
- `VoiceManager`'s voice list is guarded by an explicit
  `threading.Lock`, unlike `main.ReanalysisBuffer`'s deque (issue #77),
  which relies on individual `deque` operations already being atomic
  under CPython's GIL without an explicit lock. The two aren't the same
  situation: `ReanalysisBuffer` only ever does a single atomic append (or
  a single atomic full-snapshot read) per access, while the audio
  callback does a read-modify-write across the *whole* voice list every
  block (render each voice, then rebuild the list minus any that just
  finished) -- not a single atomic bytecode op, so a real lock is the
  correct call here rather than reaching for the same GIL-based argument.
- The score editor's `undo`/`redo` keybinds (default `"u"`/`"U"`) are
  matched case-sensitively by `main.run_score_editor()`, breaking this
  codebase's otherwise-universal case-insensitive keybind convention —
  they deliberately share one letter, distinguished only by case, so
  case-insensitive matching would collapse them into a single action. So
  is `chord_builder_exit` (default `"b"`), exact-case for a different
  reason: the Chord builder's ROOT reel needs to type the actual letter
  'B', which collides with 'b' as a plain case-insensitive exit key.
  (`score_properties_exit` used to share this same exact-case treatment,
  before a post-#98 hands-on-feedback follow-up retired the separate
  Score properties screen it belonged to — see docs/DECISIONS.md.) See
  docs/DECISIONS.md for both, and for why
  `score_editor_display.chord_name_for_column()` builds a synthetic
  chroma vector directly rather than literally calling `chroma.fold()`
  (which needs an audio spectrum a loaded score's columns don't have).
- `shell.py`'s live-menu `"edit"` entry gets its own dispatch branch,
  neither `_NON_SESSION_SCREENS` (Settings/Credits/Prototypes/Stats'
  shape — always straight back to the menu, no sentinel to interpret) nor
  `main.run_session()` (would call `session.ensure_started()`, needlessly
  opening the mic for a tool with nothing to do with audio) — see
  docs/DECISIONS.md.
- The synth tool's tracker keyboard is not defined in the synth tool:
  `synth_layout.py` derives its note pitches from
  `score_audition.PIANO_KEY_SEMITONES` (ticket #120), and adds only the
  display geometry the score editor has no use for — a second
  hand-written copy of `zsxdcvgbhnjm`/`q2w3er5t6y7u` would pass every
  test either module has right up until the day the two drifted apart.
- The synth tool's `Shift`+key commands are hardcoded, not remappable
  through `[keybinds]` like this app's other 22 — a remap onto a plain
  letter would silently break its always-plays invariant, and
  `settings_display.is_valid_remap_key()`'s fixed denylist can't express
  "not any letter, ever". Same tier as the score editor's Shift+Arrow
  transpose.
- Layout 2's simultaneous kit + synth patch is a *routing* question, not a
  second sound path: `synth_tool.ChannelRouter` dispatches by MIDI channel
  (pads on 9, keys on 0), which is also exactly the shape a MIDI
  controller plugs into later. Its voice budget is a third
  `[preferences]` context (`polyphony_synth_dual`) rather than a
  compromise on the existing two, because the failure mode it guards is
  *starvation* (a drum hit finding every slot held by sustained notes),
  not overrun.
- The synth's log-scaled parameters step by ratio, not by amount — a
  fixed-Hz cutoff sweep crawls at the bottom of its range and leaps at
  the top, so one press is a semitone everywhere instead. Numbers clamp
  and choice lists wrap, the same split `settings_display.py` and the
  Chord builder's reels already follow.
- The synth tool gets its own `shell.py` dispatch branch for `edit`'s
  exact reason: it returns a `"menu"`/`"quit"` sentinel that has to be
  interpreted (so not `_NON_SESSION_SCREENS`), but has no business
  opening the mic (so not `run_session()`).
- Four reversals of earlier score editor calls, made after the project
  owner's first hands-on session with the finished feature rather than
  from a new abstract argument (issue #98 follow-up; full rationale for
  each in docs/DECISIONS.md): (1) `note_toggle` (Space) can now empty a
  column to zero notes itself, reversing the original "refuses to remove
  the last note, use `r`" rule; (2) the Chord builder's Up/Down and
  Left/Right roles are swapped (Up/Down now switches reels, Left/Right
  spins) to match the reels' actual vertical layout, rather than the
  prototype's inherited horizontal-widget binding; (3) transpose moved
  off a remappable `+`/`-` onto hardcoded Shift+Up/Shift+Down, requiring
  `main.RawKeys.poll()`/`_parse_csi_params()` to parse the CSI
  parameter-byte form (`ESC [ 1 ; 2 <letter>`) terminals send for a
  modifier-held arrow, not just the bare `ESC [ <letter>` burst it
  handled before; (4) the separate Score properties screen (#90's
  original call) is gone, replaced by an inline header editor over the
  main view's own always-visible `time=`/`key=`/`tempo=` status-line
  fields. Also bundled into the same follow-up (not itself a reversal,
  a new capability): `note_toggle`'s default placement and the left
  legend's row letters are now key-signature-aware
  (`staff_map.key_signature_accidental()`, `pitch_at_row(key_fifths=)`,
  `score_editor_display._legend_letter()`) instead of always-natural.

## Known limitations / things learned

One-liners; full detail in `docs/DECISIONS.md`.

- Issue #74's frequency-range fix (see Key design decisions) only stops
  peaks *outside* `config.FMIN`/`FMAX` from being peak-picked as phantom
  notes — a kick or snare hit's own broadband energy that happens to fall
  *inside* that range (real low-frequency thump/body resonance, not an
  out-of-range artifact) still produces a non-empty, spurious note-stack
  on the percussion acoustic suite's `beat_only` tier (~30% of hops,
  down from ~67-68% pre-fix). No percussion/pitch-plausibility classifier
  exists anywhere in this pipeline (chord/multipitch always runs
  regardless of what's actually playing, see Architecture) — closing this
  residual gap would need one, which is out of this fix's scope.
- Issue #75 investigated one concrete instance of that residual gap: on a
  static, unchanging held chord with a basic beat underneath, a snare
  hit's own realistic ~200Hz tonal "poc" attack component (modeled by
  `scripts/acoustic_pipeline_test.py`'s `synth_snare()`, not a synthesis
  quirk -- real snares have this) lands ~35 cents from G3, close enough
  for `multipitch.detect()` to correctly find it as a real spectral peak
  and `chord_duration_tracker` to correctly track/finalize it as a
  short (~45-115ms) phantom duration event -- 8/8 kick-adjacent hits
  originally blamed on the kick, but root-caused via raw-log timing
  correlation to the snare instead (the kick's own decay was never
  actually the cause). Three candidate fixes (a chord-mode minimum-
  persistence gate, a within-window magnitude decay-shape heuristic in
  `multipitch.detect()`, tightening harmonic-pruning's tolerance/
  direction) were each prototyped and empirically rejected: the first has
  no safety margin against issue #55's own ~107ms fast-note stress case,
  the second is empirically indistinguishable from a real note's own
  onset transient (proven by running the same experiment against a real
  chord's genuine attack), and the third is already known-fragile
  tolerance-boundary territory (this exact case sits at -34.9 cents,
  inside the existing 35-cent tolerance by construction). A follow-up
  round tried two more angles -- a persistence gate scoped narrowly to
  "duplicate pitch class, different octave, already active" (not (a)'s
  blanket version) and a spectral-breadth check on the chroma novelty at
  onset -- and empirically rejected both the same way: matched-control
  testing showed each is indistinguishable from a common, legitimate
  case (a genuine octave-doubled note for the first, a genuine chord/note
  attack for the second). Left open with the full investigation (both
  rounds) in `docs/DECISIONS.md` rather than forcing an unsafe fix --
  closing it for real would need a genuine transient/onset classifier, a
  materially bigger feature than this issue's scope.
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
- The score editor's Chord builder can't type the third/fifth/seventh
  degree reels' own flat tokens (`b3`/`b5`/`b7`) while
  `chord_builder_exit` is bound to its default `"b"` — that key always
  exits the screen before any reel-specific typeahead logic sees the
  keystroke, regardless of which reel has focus. Spinning Up/Down still
  reaches every degree option including the flat ones, so this is a
  typing-shortcut gap, not a missing feature; remapping
  `chord_builder_exit` via the Settings screen sidesteps it entirely. See
  docs/DECISIONS.md for why the ROOT reel's own similar letter-vs-'b'-
  accidental conflict *was* resolvable (via exact-case letter matching)
  and this one isn't.
- The score editor's `saved=yes/no` quit-confirm treats `undo`/`redo` as
  always dirtying the score, even on a traversal that returns it to
  exactly its last-saved content — a deliberate, harmless-worst-case
  simplification (an occasional extra confirmation keypress) over
  tracking "distance from last save" precisely through arbitrary
  undo/redo. See docs/DECISIONS.md.
- The `tab` view's frozen playback (ticket #121) has been verified
  numerically and structurally only — scope selection, chord expansion,
  onset/duration computation and the start/stop/failure handshake are
  unit-tested against fake engines with no audio device opened. Nobody has
  listened to it yet: the worker thread's real-world timing accuracy, and
  whether a screen of detected notes actually sounds like what was played,
  both need a real TTY and working audio output. Playback is also only as
  honest as detection was — a wrong pitch or a mis-measured duration plays
  back exactly as wrong, deliberately (decision #109 chose that over
  re-voicing from the matched chord name).
- Nothing in the synth tool has been *heard*: it was built on a muted
  machine with no interactive TTY. Its dispatch is genuinely verified
  (ticket #120's scripted-`RawKeys` pattern drives the real
  `run_synth_tool()` loop headlessly against a fake sound engine — which
  keystroke becomes which note on which MIDI channel, Tab's layout cycle
  and voice budget, nothing left sounding on any exit path), but
  `synth_display.render()`, the audio path itself, and every timbre and
  ergonomics judgement remain unverified. `run_synth_tool()`'s standalone
  path (`virtualnote synth` with no session, which opens a real output
  device) is the one branch no test exercises at all.
- `config.SYNTH_FIXED_NOTE_SECONDS` (0.35s), the key-light animation
  constants, and the synth panel's step sizes are provisional by-ear
  values in the same spirit as `PLAYBACK_HARMONIC_WEIGHTS` — none is
  load-bearing the way e.g. `YIN_SUBHARMONIC_MARGIN` is, and all want
  tuning against real playing.
- The score editor's Shift+Up/Shift+Down transpose (issue #98 follow-up)
  depends on the terminal/multiplexer actually sending the standard xterm
  CSI encoding for a Shift-held arrow (`ESC [ 1 ; 2 <letter>`) —
  `main.RawKeys._parse_csi_params()` falls back gracefully to the plain
  arrow direction for any *other* recognized-but-unexpected parameter
  string, but a terminal that sends Shift+Arrow some third way entirely
  (not this app's own arrow-burst timing/multiplexer-lag concern
  `poll()`'s docstring already covers, a genuinely different encoding)
  would silently not transpose at all rather than error — a graceful-
  degradation posture, not a hard cross-terminal guarantee. Not yet
  smoke-tested against a real TTY in this environment; verified via
  synthetic byte-sequence unit tests only (`tests/test_main.py`).
- The kitty keyboard protocol support in `main.RawKeys` (map #99, ticket
  #118) is verified against the *spec* by unit tests over pipes and a real
  `pty.openpty()` pair, and was verified against real kitty by hand during
  issue #101's prototype round — but **tmux passthrough is still
  untested**. The failure mode is degradation, not breakage: tmux answers
  the DA1 sentinel, so the probe settles as "unsupported" and a view falls
  back to `kitty_keys.FixedDurationKeys`. Also unverified in a real
  terminal: focus-loss release synthesis (DECSET 1004), which is covered
  only by synthetic `CSI O` byte sequences here.
- The no-releases fallback (`kitty_keys.FixedDurationKeys`) is honestly
  not a good instrument, and can't be made one: with no release events,
  "held" and "struck repeatedly" are the same signal, so a held key
  extends its note (each auto-repeat press pushes the deadline out) at the
  cost of merging a genuine fast repeat of the same note into one. A
  terminal-level limit, documented rather than chased.

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
