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
76 tests), and detection has been confirmed with a real speaker→mic
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

## Backlog (open problems, not yet fixed)

None currently open.

## Architecture

```
mic -> AudioCapture (PortAudio callback thread, never blocks)
    -> bounded queue.Queue (drop-oldest on full)
    -> analysis thread: rolling 2048-sample ring buffer
                         -> pitch_detect.compute_spectrum()      (shared FFT)
                         -> pitch_detect.detect_pitch()          (YIN, monophonic)
                         -> note_smoother.NoteSmoother            (stabilize)
                         -> color_map.note_to_hsl()               (note -> color)
                         -> chroma.fold() / fold_bass()           (chord mode)
                         -> multipitch.detect()                   (chord mode, up to 6 notes)
                         -> chord_smoother.ChordSmoother           (stabilize chord+notes)
                         -> chord_templates.match()                (chord mode, chroma -> name)
    -> single-slot queue.Queue (always overwritten with latest RenderItem)
    -> render loop (GUI window, or one of three terminal views)
```

The chord-mode pipeline (chroma/multipitch/chord_smoother/chord_templates)
always runs every hop regardless of whether any view has `P` toggled on —
cheap enough that gating it wasn't worth the extra shared state (see
Key design decisions). `P` is a pure render-thread-local flag.

Three threads, connected by non-blocking queues at every boundary, so no
stage can ever stall another. Target end-to-end latency: comfortably under
150ms.

## Files

| File | Responsibility |
|---|---|
| `config.py` | All tunable constants (sample rate, buffer sizes, thresholds, color/animation/chord-mode params). Check here first. |
| `audio_capture.py` | `AudioCapture` — `sounddevice.InputStream` callback → bounded drop-oldest queue. `resolve_loopback_device()` finds the system-output monitor for `--source loopback`. |
| `pitch_detect.py` | `compute_spectrum()` — shared FFT reused by YIN, chroma, and (via its own Hann-windowed variant) multipitch. `detect_pitch()` — hand-rolled YIN (pure NumPy, FFT autocorrelation + parabolic interpolation) over a precomputed spectrum. |
| `note_smoother.py` | `NoteSmoother` — silence/confidence gate, median filter, debounce, onset detection (monophonic path). |
| `chroma.py` | `fold()`/`fold_bass()` — 12-bin chroma vector via a precomputed Gaussian log-frequency weighting matrix summing 1st–4th harmonics per pitch class; `fold_bass()` restricts to <~250Hz for bass/inversion detection. |
| `chord_templates.py` | ~360-template dictionary (30 qualities × 12 roots) + `match()` — cosine-similarity chord recognition, bass-chroma-gated slash/inversion naming and rotational-tie-breaking. |
| `multipitch.py` | `detect()` — spectral peak-picking (own Hann-windowed FFT, not the shared one — see Key design decisions) + harmonic-consistency pruning, up to 6 simultaneous notes with confidence. |
| `chord_smoother.py` | `ChordSmoother` — mirrors `NoteSmoother`'s shape for chord mode: chroma rolling-average + chord-name debounce, plus asymmetric attack/release hysteresis per note-stack slot. |
| `color_map.py` | `note_to_hsl()`, `hsl_to_rgb255()`, `fifths_index()`, `NOTE_NAMES`, `NOTE_NAMES_FIFTHS`. |
| `staff_map.py` | `staff_row()`, `ledger_rows()` — grand-staff placement, used only by `tab` view. |
| `animation.py` | `ColorAnimator` — crossfade + onset pulse. Used by GUI, terminal-fill, and (per-note-keyed) chord-mode fill bands. |
| `display.py` | `Display` — pygame GUI window (fullscreen, debug overlay). Chord mode is out of scope for the GUI (no live-hotkey mechanism). |
| `terminal_display.py` | `TerminalDisplay` — ANSI truecolor full-terminal fill; `render_bands()` for chord mode's proportional per-note bands. |
| `terminal_wheel_display.py` | `WheelDisplay` — 12-note fifths ring, always fifths color regardless of `--color-scheme`; `render_chord()` for chord mode's multi-wedge steady-lit display. |
| `terminal_tab_display.py` | `TabDisplay` — scrolling grand-staff note history rendered as sheet-music noteheads; `push()`/`push_notes()`, `render()` (takes live `notehead_style`/`legend_on`/`frozen`, and age-fades each column's lightness per issue #22), `dump_ansi()` on quit (always letter+octave, unaffected by any toggle). |
| `main.py` | Wires threads together, dispatches GUI/terminal views by CLI flag; `RenderItem` NamedTuple is the render-queue shape. `pygame` imported only inside `run_gui`. |
| `tests/` | `test_pitch_detect.py`, `test_note_smoother.py`, `test_color_map.py`, `test_staff_map.py`, `test_chroma.py`, `test_chord_templates.py`, `test_multipitch.py`, `test_chord_smoother.py`, `test_terminal_tab_display.py`. |

## Running it

```
cd ~/note-color
.venv/bin/python main.py                              # GUI window (chromatic scheme)
.venv/bin/python main.py --terminal --view fill        # terminal fill
.venv/bin/python main.py --terminal --view wheel        # terminal circle-of-fifths ring
.venv/bin/python main.py --terminal --view tab --scroll onset   # scrolling staff, new column per note-attack
.venv/bin/python main.py --terminal --view tab --scroll fix     # scrolling staff, new column every tick
.venv/bin/python main.py --color-scheme fifths           # any mode, fifths hue mapping instead of chromatic
.venv/bin/python main.py --terminal --view fill --source loopback  # listen to system audio output, not mic
.venv/bin/python -m pytest tests/                          # run the test suite
```

Shorter launchers on PATH (`~/.local/bin/colorize`, added to `~/.zshrc`'s
PATH): `colorize fill`, `colorize circle`, `colorize tab fix`, and
`colorize tab onset` — thin wrappers forwarding extra flags (e.g.
`colorize tab onset --color-scheme fifths`).

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
(see below).
`--sensitivity FLOAT` sets the starting value (default 1.0); raises it to
register quieter/softer playing more readily. Current value shown in the
status line (`sens=`).

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
legend column (clef glyphs + natural-note letters, itself merged into one
`TAB_LEGEND_WIDTH`-wide region, octave-digit-free) on/off live, reclaiming
its width for note columns when off. Current state of both shown in the
status line (`notes=`/`legend=`). The on-quit `dump_ansi()` text dump is
unaffected by either toggle — always letter+octave, as before.

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

## Key design decisions

One-liners; full rationale in `docs/DECISIONS.md`.

- Python + NumPy — cheap enough at these buffer sizes, no build toolchain.
- Hand-rolled YIN, not `aubio`/`librosa` — wheel/dependency risk on Pi.
- Microphone is the default input; `--source loopback` is opt-in and
  Linux-only (PipeWire/PulseAudio monitor), so portability of the default
  path is unaffected.
- Monophonic only — simpler, real-time, fits the use case.
- `pygame-ce` for the GUI — reliable wheels across target platforms.
- `--color-scheme fifths` is additive; `wheel`/`tab` always use fifths so a
  note's color stays consistent between views.
- `tab` uses a grand staff, not single treble — manageable ledger lines
  across the app's 4-octave range.
- `tab`'s left legend column shows a treble/bass clef glyph on each staff's
  anchor line (G4/F3) and the natural-note name on every other staff line
  — added so the grand staff is legible without already knowing note
  positions by heart, especially in the bass register.
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

## Known limitations / things learned

One-liners; full detail in `docs/DECISIONS.md`.

- Octave-error blips (~100ms) can occur during note decay; not worth fixing
  without a concrete complaint.
- Live pitch-tracking quality varies run-to-run with room/mic conditions —
  not a regression.
- Target 64-bit Raspberry Pi OS (Bookworm+) — 32-bit is a wheel risk.
- macOS/Windows gate mic access per-app; a denied prompt gives silent zeros,
  not an error.
- `~/.local/bin` is on PATH via `~/.zshrc`, for `colorize`.
- `tab --scroll onset` freezes on sustained notes/silence, by design.
- Terminals <~22 rows clip outermost `tab`-view ledger-line notes.
- A minor-7th chord and its relative-major 6th chord share the exact same
  pitch-class set (e.g. Am7 = A-C-E-G, C6 = C-E-G-A) — an inherent
  music-theory ambiguity, not a bug. Without a confident bass note to
  disambiguate the root, `chord_templates.match()` deterministically picks
  the lower root-index template; this is correct behavior, not a wrong
  answer, when no bass is actually present.
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
