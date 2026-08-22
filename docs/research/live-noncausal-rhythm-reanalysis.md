# Live non-causal rhythm re-analysis (`R` while frozen): feasibility research

Research for the proposed `R`-while-frozen feature: re-run the batch
pipeline's non-causal duration/tempo/barline accuracy against a rolling
window of *recently played* live audio, in place, without redoing
pitch/chord detection. No ticket number yet assigned at time of writing;
this doc is the pre-design research the ticket should cite.

## Question

1. What data does `R` need buffered live to make non-causal recompute
   possible? Raw audio, or smaller derived per-hop values?
2. Memory/perf estimate for each candidate buffer type at 30s/2min/5min/
   30min windows — what's Pi Zero 2 W (512MB) plausible vs. desktop-only?
3. Can `finalize_noncausal()`/`librosa.beat.beat_track()` run mid-stream
   on a rolling window (not a complete pre-loaded file)? What "whole file
   up front" assumptions in the current batch code would need to change?
4. What would a real "quality/time-budget knob" concretely control — is
   there genuine multi-pass tunability, or is analysis-window length the
   only real lever?
5. Given the three-thread model, where does the rolling buffer live, and
   how does `R`'s recompute avoid blocking the render thread or
   corrupting analysis-thread state?

## Answer

### 1. What `R` needs buffered: derived per-hop values, not raw audio

`duration_tracker.DurationTracker.finalize_noncausal()` is a `@staticmethod`
(`/home/pelle/note-color/duration_tracker.py:131-176`) whose only inputs
are:

- `magnitude_history`: a 1D array, one scalar magnitude per hop, for a
  **single already-identified note key** (`(pitch_class, octave)`) —
  built in batch from RMS (mono) or `multipitch.detect()` confidence
  (chord), never from raw audio inside this function.
- `onset_indices`: hop indices where a fresh onset was already detected
  for that key.
- `decay_ratio` (`config.DURATION_DECAY_RATIO`), `smooth_window`.

It does no FFT, no pitch detection, no audio decoding — it is pure
post-hoc smoothing (`np.convolve` centered kernel) and threshold-crossing
over a scalar time series that must already exist. Confirmed by reading
`batch_transcribe.py:114-167`, `176-196`: `transcribe()` builds these
per-key `mono_magnitude`/`chord_magnitude`/`*_onsets` arrays **during**
its per-hop loop, from the same `rms`/`raw_stack[...]["confidence"]`/
`is_onset` values `main.py`'s live `analysis_loop()` *already computes
every hop* (`main.py:262,270,300,323,338-357` — see below) and then
discards after feeding them into the causal `DurationTracker.update()`.

**Conclusion: `finalize_noncausal()` needs only the small, already-derived
per-hop scalars analysis_loop() computes today, not raw PCM.** A rolling
buffer of `(hop_index, pitch_class, octave, magnitude, is_onset)` tuples
(mono: at most 1/hop; chord: up to `config.CHORD_MAX_NOTES` = 6/hop) is
sufficient to reconstruct exactly the per-key arrays batch builds, on
demand, at `R`-press time.

For tempo: `librosa.beat.beat_track()`'s real signature (inspected
directly, librosa 1.0.0 installed in this repo's `.venv`) is:

```
beat_track(*, y=None, sr=22050, onset_envelope=None, hop_length=512,
           start_bpm=120.0, tightness=100, trim=True, bpm=None,
           prior=None, units='frames', sparse=True)
```

`onset_envelope=` is a first-class alternative to `y=` — **no raw audio
required**, confirmed by direct inspection, not assumption.
`batch_transcribe.py`'s own `_estimate_bpm()` (lines 199-213) currently
passes `y=audio` (librosa computes its own internal onset envelope from
raw audio via `onset_strength`), but that's a batch-code convenience, not
a hard requirement of the API.

**Bigger finding, worth flagging explicitly:** this project's own live
novelty signal — `chroma_flux()` (`onset_detect.py:73-86`), computed every
hop already for the live `TempoTracker` (`main.py:294-296`) — is sampled
at exactly `config.BLOCK_SIZE=512` / `config.SAMPLE_RATE=22050`
(`config.py:5-6`), which is **librosa's own default `hop_length=512`,
`sr=22050`** for `beat_track()`/`onset_strength()`. So a rolling buffer of
this app's existing per-hop `chroma_novelty` scalar can be fed directly as
`onset_envelope=` to `librosa.beat.beat_track(onset_envelope=buffered_array,
sr=22050, hop_length=512)` with no resampling/re-framing — the frame
clocks already line up. (The absolute scale/shape of `chroma_flux()`'s
output differs from librosa's own mel-spectrogram-based `onset_strength()`
— it's a coarser, 12-bin chroma-difference signal rather than a full
spectral one — so beat-tracking accuracy off this signal directly is an
open empirical question, not a proven drop-in; but it removes any
architectural need to touch raw audio for tempo re-estimation.)

**Practical implication:** `R` does not need a raw-audio ring buffer at
all under the stated scope ("already-detected notes/pitches are NOT
redone"). It needs a rolling deque of tiny structured hop-records — this
is the single most consequential finding for feasibility/memory, since it
eliminates the raw-audio-buffering option from serious consideration (see
Q2).

### 2. Memory/perf estimate

Hop rate: `config.BLOCK_SIZE=512` / `config.SAMPLE_RATE=22050` ≈ 23.2ms/hop
→ ~43.07 hops/sec (`config.py:4-5`).

**Raw float32 audio** (for reference, shown to be unnecessary per Q1):
22050 samples/sec × 4 bytes = 88,200 B/s ≈ 86.1 KiB/s.

| Window | Raw audio (f32) |
|---|---|
| 30s | ~2.6 MB |
| 2min | ~10.3 MB |
| 5min | ~25.8 MB |
| 30min | ~154.9 MB |

**Derived per-hop records** (the actually-needed option): per hop, worst
case is 1 mono tuple + up to 6 chord tuples + 1 novelty scalar. Each tuple
is 2 small ints + 1 float + 1 bool ≈ 32-40 bytes as raw values; even
generously accounting for Python object/tuple overhead in a plain list (no
NumPy packing), call it ~200 bytes/hop worst case (unoptimized) or
realistically <50 bytes/hop if packed into small per-key/per-hop NumPy
arrays instead of Python objects.

| Window | Hops | Unoptimized (~200 B/hop, Python objects) | Packed NumPy (~50 B/hop) |
|---|---|---|---|
| 30s | ~1,292 | ~258 KB | ~65 KB |
| 2min | ~5,168 | ~1.0 MB | ~258 KB |
| 5min | ~12,921 | ~2.6 MB | ~646 KB |
| 30min | ~77,527 | ~15.5 MB | ~3.9 MB |

**Conclusion:** even at the sloppy, unoptimized Python-object estimate,
30 minutes of derived per-hop rhythm data is ~15MB — trivial against a
512MB Pi Zero 2 W budget, and two full orders of magnitude cheaper than
buffering 30 minutes of raw audio (~155MB, a real risk on that hardware
once Python/NumPy/OS overhead is counted). **The "several minutes on
Pi-class hardware" goal in the brief is comfortably achievable, and in
fact 30+ minutes is achievable too, provided the buffer holds derived
values (as Q1 shows it can) rather than raw audio.** This removes what
would otherwise be the single biggest hardware-floor risk in the feature.

Actual compute cost of the recompute itself, benchmarked directly in this
repo's `.venv` (not estimated):

- `DurationTracker.finalize_noncausal()`, 6 simultaneous keys, one call
  per key (matches `batch_transcribe.py:180-182`'s pattern):
  - 30s window: 4.3ms; 2min: 13.1ms; 5min: 22.8ms; 30min: 135.5ms.
- `librosa.beat.beat_track(onset_envelope=...)` (first call includes
  librosa's one-time numba/JIT warm-up cost, ~2.3s; steady-state calls
  after warm-up are what matters for repeated `R` presses):
  - 30s: 82.6ms (steady-state; ~2.3s only on the very first call in the
    process); 2min: 82.6ms; 5min: 209.1ms; 30min: 1282.6ms.

These numbers are desktop-measured; expect roughly 3-8x slower on a Pi
Zero 2 W (weak single/quad-core Cortex-A53 vs. this dev desktop), which
still puts even a 30-minute recompute in the low single-digit seconds —
well within a one-shot, user-triggered `R` press's acceptable latency
(this is not a per-hop or per-frame cost).

### 3. Mid-stream / rolling-window feasibility

`finalize_noncausal()` itself has **no whole-file assumption** — it
operates on whatever-length array it's given, using `np.pad(..., mode="edge")`
for the centered smoothing kernel at the array boundaries
(`duration_tracker.py:153-158`), which degrades gracefully at a rolling
window's edges rather than requiring a specific total length.

`librosa.beat.beat_track()` similarly has no "whole song" precondition —
it's a per-frame dynamic-programming beat tracker (Ellis 2007) that
operates on any-length onset envelope; benchmarked above at 4 different
lengths with no special-casing needed.

The actual "whole file up front" pattern lives in `batch_transcribe.py`,
not in the two functions R would call:

- `transcribe()` computes `n_hops = len(audio) // config.BLOCK_SIZE` once
  (`batch_transcribe.py:90`) and pre-allocates full-`n_hops`-length
  `np.zeros(n_hops)` arrays per key (`batch_transcribe.py:97-104,
  129-131,158-160`) — this is a convenience allocation, not a hard
  requirement of `finalize_noncausal()`/`beat_track()` themselves. A live
  rolling implementation would instead keep a bounded `deque(maxlen=...)`
  of per-hop records and, at `R`-press time, do one linear scan over
  whatever's currently in that deque to reconstruct the same per-key
  arrays batch builds — mechanically straightforward, no change needed
  to either target function's signature or behavior.
- The one genuine, load-bearing difference: batch's `n_hops` is known
  and fixed before the loop starts, so segment boundaries
  (`segment_end = sorted_onsets[i+1] if ... else n`,
  `duration_tracker.py:163`) always terminate at a real, final array
  length. In a rolling live buffer, "the end" is always "right now" — a
  note still sounding in the last few hops of the window (including,
  worst case, whatever was playing at the exact moment `R` was pressed)
  has no true decay boundary inside the window and will be truncated to
  whatever's currently visible, exactly like the existing documented edge
  case for "a note still sounding when the process quits" (`duration_
  tracker.py:30-33`, `DEFAULT_DURATION_CLASS` fallback). Since the
  feature is specced as "press `R` while **frozen**," and freeze itself
  doesn't stop audio capture or analysis (`main.py:704-707`'s comment: the
  analysis thread keeps running in the background while frozen — see
  Architecture / `_handle_freeze_key`'s docstring at `main.py:465-476`),
  this edge case is mitigated in the common case (freeze naturally happens
  after playing has paused, so recent notes have usually already decayed
  by the time `R` fires) but not eliminated — a re-analysis triggered
  immediately after a note's attack, before it's decayed, will still
  under-measure that one note. Same class of limitation batch already
  documents and accepts, not a new risk.

**Conclusion: no code-level blocker.** The two functions are already
rolling-window-safe; only the *caller* (a new live equivalent of
`transcribe()`'s per-key-array-building loop, driven off a bounded deque
instead of a preallocated full-length array) needs to be written new.

### 4. What a real "quality/time-budget knob" would control

Investigated directly rather than assumed:

- `librosa.beat.beat_track()`'s own tunable parameters (`start_bpm`,
  `tightness`, `trim`, `prior`, `bpm`) adjust the DP search's *prior bias*
  (how strongly it favors a particular tempo/consistency), not a compute-
  for-accuracy tradeoff — the algorithm is a single fixed-cost dynamic
  program, observed near-linear in input length in this repo's own
  benchmark above (82.6ms → 1282.6ms scaling roughly with the ~15.5x
  input-length increase from 2min→30min hops, i.e. genuinely linear, not
  superlinear) with no internal "spend more cycles, get a better number"
  lever once given a fixed onset envelope. There is no meaningful multi-
  pass/iterative-refinement mode inside `beat_track()` itself.
- `librosa.beat.plp()` (signature confirmed: `plp(*, y=None, sr=22050,
  onset_envelope=None, hop_length=512, win_length=384, tempo_min=30,
  tempo_max=300, prior=None)`) computes a continuous "predominant local
  pulse" curve via windowed Fourier tempogram-like analysis; its
  `win_length` is a genuine speed/robustness knob (larger window = more
  periods averaged = more robust local pulse estimate at higher cost),
  but it produces a different kind of output (a pulse curve, not
  discrete beat times/tempo) — usable as a cross-check/ensemble input
  alongside `beat_track()`, not a drop-in "better `beat_track()`."
- `finalize_noncausal()`'s own `smooth_window` parameter
  (`duration_tracker.py:132,153-158`) is a bias/variance tradeoff (larger
  window suppresses more noise-driven false boundary calls but also
  blurs genuinely short notes), not a monotonic "more compute → strictly
  more accurate" dial — there's a real optimum, not a one-directional
  slider.

**Honest, evidence-based recommendation:** there is no real "spend more
CPU, get a strictly more accurate single number" lever hiding inside
`beat_track()`/`finalize_noncausal()` themselves — both are fixed-cost,
close-to-linear-time algorithms once given their input. The two
genuinely real levers, confirmed by direct benchmarking and signature
inspection, are:

1. **Analysis window length** (how far back `R` reaches) — this *is* a
   real accuracy-via-more-data lever (more cycles of the beat to
   autocorrelate/DP against, matching this project's own existing
   `TEMPO_HISTORY_SECONDS` rationale in `tempo_tracker.py`/`config.py:225`),
   and it's the one the brief already asks for as the primary control —
   confirmed cheap even at 30 minutes (Q2's benchmarks).
2. **A small multi-hypothesis ensemble**, not currently implemented
   anywhere in this codebase or exposed by librosa as a single call: run
   `beat_track()` a handful of times with different `start_bpm` priors
   (e.g. sweeping octave-related seeds like 80/120/160bpm) and/or cross-
   check against `plp()`'s pulse curve, then reconcile via majority
   vote/confidence. This is a well-documented, real mitigation in the
   beat-tracking literature for **tempo-octave errors** (locking onto
   2x/0.5x the true tempo) — the single most common failure mode for
   exactly this class of DP beat tracker — and each extra pass costs only
   the already-cheap per-call time measured above (tens to low hundreds
   of ms even at 5-30min windows), so a few extra passes is a genuinely
   affordable "quality" dial with a real accuracy payoff, unlike tuning
   `beat_track()`'s own single-pass parameters.

If a "quality knob" ships, it should honestly be framed/implemented as
**(a) window length** (already user-facing per the brief) **and,
optionally, (b) ensemble pass count** (new, not yet built) — not as any
single hidden parameter inside `beat_track()` or `finalize_noncausal()`,
because neither of those has a real internal speed/accuracy tradeoff to
expose.

### 5. Threading/architecture placement

Confirmed from `main.py` and `CLAUDE.md`'s Architecture section: three
threads (PortAudio capture callback, analysis thread, render thread),
connected only by non-blocking `queue.Queue`s — capture→analysis is a
bounded drop-oldest queue, analysis→render is a single-slot always-
overwritten queue (`RenderItem`, `main.py:213-233`).

- **Where the rolling buffer should live:** the **analysis thread**,
  since that's the only thread that already computes the per-hop
  derived values (`rms`, `pitch_class`/`octave`, `is_onset`, `raw_stack`
  confidences, `chroma_novelty`) `R`'s recompute needs (`main.py:236-373`,
  specifically `mono_notes` at line 300 and `raw_stack`/`chord_
  duration_tracker.update()` at lines 323-357). A bounded
  `collections.deque(maxlen=window_hops)` appended to once per hop,
  right alongside the existing `mono_duration_tracker.update()`/
  `chord_duration_tracker.update()`/`tempo_tracker.update()` calls, is
  the natural, minimal-diff place — it mirrors this project's existing
  pattern of per-hop trackers holding their own bounded rolling state
  (`TempoTracker.history`, itself a `deque(maxlen=...)`,
  `tempo_tracker.py:29`).
- **How `R` avoids blocking the render thread:** the render thread
  (`run_terminal_tab`'s poll loop, `main.py:690-816`) never touches the
  analysis thread's internal state directly today — it only reads
  `RenderItem`s off the single-slot queue. `R`'s handler would need a
  **new, explicit hand-off mechanism** (there is no existing "render
  thread asks analysis thread to do a one-off computation" path in this
  codebase — every existing cross-thread interaction is either the
  one-way capture→analysis→render queue chain, or `AudioCapture.restart()`
  under `M`, which is a different kind of already-solved cross-thread
  call). Two options, both consistent with this project's existing
  "non-blocking queues at every boundary" rule (CLAUDE.md, Architecture):
  1. A second small request/response queue pair (render thread pushes an
     `R`-press request with the desired window length; analysis thread,
     between its normal per-hop work, notices the request, does the
     recompute, and pushes a result back) — keeps the analysis thread
     doing the actual heavy lifting (it already owns the deque, no data
     needs to cross threads at request time) but means the recompute
     briefly delays that thread's normal per-hop cadence by however long
     `finalize_noncausal()`+`beat_track()` take (tens of ms to ~1.3s per
     Q2's benchmarks) — acceptable if hops are simply queued up behind it
     (the existing bounded, drop-oldest capture queue already absorbs
     exactly this kind of transient backpressure, per `CLAUDE.md`'s
     Architecture section), but a real, measurable latency bump on the
     *live* pipeline for up to ~1.3s at the largest windows, which no
     other single hop-processing step in this codebase currently causes.
  2. Have the render thread's `R` handler **read a consistent snapshot of
     the deque and do the recompute itself**, off the render thread but
     not literally *in* the render loop's own per-frame path — e.g.
     spawning a one-off Python thread (or reusing `concurrent.futures`)
     the moment `R` is pressed, since the view is already frozen (no new
     rendering needs to happen concurrently with the recompute) and the
     analysis thread's own per-hop cadence must not stall. This avoids
     hitting the analysis thread's real-time budget at all, at the cost
     of needing a **thread-safe read of the analysis thread's rolling
     deque** — the deque is mutated by the analysis thread every ~23ms;
     a plain `collections.deque.copy()`/`list(deque)` snapshot read from
     another thread is safe against corruption in CPython (deque
     operations are individually atomic under the GIL) but *not*
     inherently a fixed-point-in-time snapshot — an append mid-copy could
     interleave. Given `R` only fires while frozen (i.e., the user has
     already signaled "nothing new should be showing"), a slightly stale
     or off-by-one-hop snapshot at request time is a low-stakes
     imprecision, not a correctness bug, and is the pragmatic choice
     given this codebase's existing "state changes are fine as long as no
     thread ever blocks another" philosophy.
  Option 2 is the better fit for this project's existing conventions
  (freeze already establishes "the render thread does its own thing,
  the analysis thread doesn't care" — see `_handle_freeze_key`'s
  docstring, `main.py:465-476`) and avoids introducing the first-ever
  case of a live hop's processing latency depending on a user action.
- **How results get back onto already-rendered, frozen columns:** this
  is a **real gap in the current `TabDisplay` API**, not just a threading
  question. `TabDisplay.finalize_duration()` (`terminal_tab_display.py:
  218-226`) mutates a note dict in place, but only via `self._open_notes`,
  a dict keyed by `(pitch_class, octave)` pointing at **the single most
  recently pushed, still-open note at that key** — it has no way to
  address "the note at this specific onset_hop/column," only "whatever's
  currently the open one for this key." A rolling non-causal recompute
  that revises the duration of a note that has *already been closed and
  superseded* by a later note at the same key (a real possibility over a
  multi-minute window) has no existing hook to reach back and correct
  it. Likewise, `TabDisplay.push_barline()` only ever *appends* — there
  is no existing API to remove an already-placed (now known-wrong)
  barline column or insert a corrected one at the right position in
  `self.entries`/`self.session_history` (both plain `deque`/`list`,
  append-only in every current call site: `terminal_tab_display.py:
  213-216`). **Concretely, shipping this feature needs new `TabDisplay`
  API** — at minimum, a way to look up/replace a specific past `TabEntry`'s
  note dict by onset time/hop (not just by key), and a way to reconcile
  (remove+reinsert, not just append) the barline set within the recomputed
  window. This is a real, previously-invisible piece of scope, not a
  minor implementation detail — it affects both the render-side data
  model and the size of the change, independent of the buffering/
  threading questions above.

## Key risks/unknowns flagged for design

- **Barline/column replacement has no existing API surface** (see Q5) —
  this is probably the single biggest unscoped chunk of work the feature
  needs beyond "buffer some data and call two existing functions."
- **`chroma_flux()` as `beat_track()`'s `onset_envelope=` is untested for
  accuracy** — the frame-rate alignment is a confirmed, free win, but
  whether this app's coarse 12-bin chroma-difference novelty signal beat-
  tracks as well as librosa's own mel-spectrogram-based `onset_strength()`
  (which batch mode gets "for free" via `y=`) is an open empirical
  question, not yet measured on real or synthetic signals here.
- **A note still sounding at the moment `R` is pressed** truncates at the
  window edge (same documented class of limitation as
  `DEFAULT_DURATION_CLASS`'s existing "still sounding at quit" case) —
  mitigated by the feature only firing while frozen, not eliminated.
- Pi-class timing above is extrapolated (3-8x desktop), not measured on
  real Pi Zero 2 W hardware — flagged as an assumption, not a measurement,
  consistent with this project's own convention of calling out untested
  extrapolations explicitly (see `CLAUDE.md`'s Known limitations section's
  own repeated "confirmed only synthetically/only on this dev machine"
  caveats elsewhere in the codebase).

## Sources

- `/home/pelle/note-color/duration_tracker.py` (read in full)
- `/home/pelle/note-color/batch_transcribe.py` (read in full)
- `/home/pelle/note-color/tempo_tracker.py` (read in full)
- `/home/pelle/note-color/onset_detect.py` (read in full)
- `/home/pelle/note-color/config.py` (read in full)
- `/home/pelle/note-color/main.py` lines 213-380, 649-825 (read directly)
- `/home/pelle/note-color/terminal_tab_display.py` lines 155-230 (read
  directly)
- `librosa` 1.0.0, as actually installed in this repo's `.venv` —
  `inspect.signature()` run directly against `librosa.beat.beat_track`,
  `librosa.beat.plp`, `librosa.onset.onset_detect` (not taken from
  external docs/memory)
- Direct benchmarks run in this repo's `.venv`
  (`DurationTracker.finalize_noncausal()` and
  `librosa.beat.beat_track(onset_envelope=...)`) at synthetic 30s/2min/
  5min/30min window lengths, numbers reported above are this run's actual
  output, not estimates
