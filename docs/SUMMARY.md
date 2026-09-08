# Design decisions and limitations — quick reference

One-liner summaries of note-color's key design calls and known limitations, split out of `CLAUDE.md` to keep that file's always-loaded footprint small. Full rationale behind any one-liner lives in `docs/decisions/` (see `docs/DECISIONS.md`'s index) — these bullets are the condensed version, not a duplicate of it.

## Key design decisions

One-liners; full rationale in `docs/DECISIONS.md`.

- Python + NumPy — cheap enough at these buffer sizes, no build toolchain.
- MIT, and third-party models/weights judged in four categories with full
  GPL refused outright and NC/unlicensed material allowed to inform a
  decision but never to ship inside one (issue #139) — see the Licence
  section above.
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
- The `tab` view's frozen playback (ticket #121) plays back only as
  honestly as detection heard it — a wrong pitch or a mis-measured
  duration plays back exactly as wrong, deliberately (decision #109 chose
  that over re-voicing from the matched chord name). No longer a
  verification gap: it, the synth tool and the score editor's
  audition/piano mode have all been played and heard on a real TTY with
  working audio.
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

