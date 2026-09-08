# Mono duration-class snapping undercounted short notes: three compounding causes fixed (issue #70)

`scripts/acoustic_pipeline_test.py`'s new `rhythm` suite (its first-ever
live round trip against issue #55's rhythm pipeline — previously verified
only via synthetic array-slicing unit tests and one batch `transcribe`
run) found `duration_class_for_beats()` snapping short notes to the wrong
standard note value: a played quarter note measured as `dotted-sixteenth`
(58% short), an eighth as `sixteenth` (40% short), and so on, while notes
>=1.5 beats classified correctly with only a small, fairly constant
absolute deficit (~50-90ms). Reproduced cheaply offline first — no audio
hardware needed — by driving the real mono pipeline (`compute_spectrum`
-> `detect_pitch` -> `NoteSmoother` -> `DurationTracker`) hop by hop over
synthesized notes (harmonics 1-4, 20ms linear fade in/out, matching
`scripts/acoustic_pipeline_test.py`'s own `synth_notes()`), which
surfaced two independent, compounding bugs; a third was only visible on
real recorded audio and found by re-running the actual acoustic suite.

**Bug 1: a spurious "ghost" duration-1 note after every note that decays
into silence.** `NoteSmoother` deliberately keeps echoing a just-ended
note's `(pitch_class, octave)` with `is_onset=False` for
`SILENCE_HOPS - 1` more hops after its magnitude crosses the silence
floor — a grace period against display flicker, unrelated to duration
tracking. But `DurationTracker.update()`'s `state is None` branch opened
a brand-new tracked state for *any* reappearance of a key with no
existing state, regardless of `is_onset` — so the very next hop after a
real note's decay-ratio finalize (which deletes its state), the smoother's
echo re-opened a new state with near-zero magnitude, which then finalized
again as a spurious ~1-hop "ghost" note two hops later once the echo
itself went silent. Every mono note that ended by decaying into silence
(as opposed to being cut off by a new note's attack) produced this ghost
immediately after its real finalize event.

Fix: `DurationTracker.__init__` gained `require_onset_for_new_note`
(default `False`, preserving chord mode's existing behavior — chord notes
have no reliable per-note onset signal at all, see below). Mono's tracker
sets it `True` (`main.py`): a key with no existing state only opens one
when `is_onset` is actually `True` for it. Mono's `NoteSmoother` output
guarantees this is always true for a genuine new note (`note_changed or
was_silent` is exactly `is_onset`'s trigger for a fresh attack), so the
echo hops — always `is_onset=False` — are silently ignored instead of
opening a ghost state. Chord mode's `chord_duration_tracker` keeps the
default off, since `main.py` intentionally hardcodes chord notes'
`is_onset=False` (no persistent per-note identity across
`multipitch.detect()`'s independent per-hop peak-picking — see the
existing "Chord-mode duration tracking always passes `is_onset=False`"
design decision above) and relies entirely on appear/absence for its
lifecycle.

**Bug 2: `NoteSmoother`'s debounce lock-in delay baked directly into
`onset_hop`.** A note-change promotion (`note_changed`) only fires once
`candidate_count` reaches `config.DEBOUNCE_HOPS` (3) *consecutive* hops
agreeing on the same candidate — meaning the true attack precedes the
promotion by exactly `DEBOUNCE_HOPS - 1` hops (~46ms at this app's hop
size), every single time, since a promotion by definition only ever lands
on the hop count first reaches that threshold. `DurationTracker` stamped
`onset_hop` at the hop `is_onset` actually fired, silently absorbing that
fixed, entirely predictable delay into every measured duration — invisible
for long notes, but a large fraction of a short one's total length.

Fix: `NoteSmoother` now exposes `onset_backdate_hops` (0 normally,
`DEBOUNCE_HOPS - 1` exactly on the hop a genuine note-change promotion
fires — computed directly from `note_changed`, so it's zero for an
RMS-jump/spectral-flux re-attack of an *already-current* note, which never
had to rebuild `candidate_count` and so has no such delay to correct for).
`DurationTracker.update()` gained an `onset_backdate` parameter (default
0, chord mode unaffected) that subtracts straight off `hop_index` when
stamping a new state's `onset_hop`; `main.py` passes
`smoother.onset_backdate_hops` for the mono tracker. Deliberately scoped
to the after-silence case only (a fresh `NoteSmoother`'s `history` deque
is empty, so the raw pitch estimate itself is already correct as soon as
SNR allows — the only remaining delay is `DEBOUNCE_HOPS`'s own buildup).
A same-key legato transition straight from one already-sounding note to
another, with no intervening silence, pays additional `MEDIAN_WINDOW`-
driven delay on top (the history deque isn't cleared, so stale old-note
samples still influence the median for a few more hops) — out of scope
here; `onset_backdate_hops` only ever claims to correct the debounce
portion, and is verified `0` in that scenario via
`test_onset_backdate_hops_set_on_genuine_note_change`'s own docstring.

With bugs 1+2 fixed, an offline synthetic sweep of all ten standard
duration classes (whole down to thirtysecond, silence-separated, 100bpm)
classified 10/10 correctly with exactly one finalize event per note — up
from a mix of double-counted ghosts and systematically undercounted
durations before.

**Bug 3, found only by re-running the real acoustic suite after 1+2:**
even with `duration_hops` itself now accurate, the reported *duration
class* for short notes was still frequently wrong — because
`duration_class_for_beats()`'s beats conversion (both in `main.py`'s
`tab` view and the acoustic test's own analysis) divides by whatever
`TempoTracker.update()`'s live `bpm_estimate` happens to be *at the exact
hop a note finalizes*, and that estimate was swinging wildly during the
rhythm suite's short-note phase: 99.38 -> 41.02 -> 47.85 -> 76.00 ->
48.75 bpm across consecutive re-estimates, confirmed reproducible offline
by feeding the suite's exact synthesized audio through the real
`chroma.fold()`/`chroma_flux()`/`TempoTracker` pipeline directly (no
hardware needed once the mechanism was suspected). Root cause:
`TempoTracker._estimate()`'s autocorrelation window
(`config.TEMPO_HISTORY_SECONDS`, 8s) still held the tail of the suite's
earlier isochronous 100bpm pulse train when the duration-class notes
began, giving a strong, confident periodicity match — but as that
periodic content scrolled out of the rolling window and was replaced by
genuinely non-periodic content (isolated single notes at irregular,
shrinking intervals, no consistent beat for autocorrelation to find at
all), the best-lag `argmax` degenerated into picking whichever lag was
marginally tallest among what was essentially noise, with no stable
answer to converge on.

Measured directly: the autocorrelation peak normalized against zero-lag
energy (`peak / acf[0]`) sat at ~0.85-0.90 throughout the genuinely
periodic pulse-train phase, and collapsed to ~0.09-0.19 once the window
was dominated by non-periodic content — a clean, wide margin. Fix:
`TempoTracker._estimate()` now computes this same ratio and, below
`config.TEMPO_MIN_CONFIDENCE` (0.3, comfortably inside that empirical
margin), returns `self._last_estimate` unchanged instead of re-locking
onto the best-of-a-bad-lot candidate — holding the last confident
estimate through a stretch of non-periodic input rather than chasing
noise. Re-running the exact same offline reproduction after this fix
holds a steady 99.4bpm through the entire duration-note phase instead of
wobbling.

**Combined result**, measured via
`scripts/acoustic_pipeline_test.py --suites rhythm --source loopback`
(a real speaker-absent loopback round trip through the actual PortAudio/
PipeWire stack, not a physical mic — see the caveat below):

| expected class | expected beats | before (detected / correct) | after (detected / correct) |
|---|---|---|---|
| whole | 4.0 | whole / True | whole / True |
| dotted-half | 3.0 | dotted-half / True | dotted-half / True |
| half | 2.0 | half / True | half / True |
| dotted-quarter | 1.5 | dotted-quarter / True | dotted-quarter / True |
| quarter | 1.0 | dotted-sixteenth / **False** | quarter / True |
| dotted-eighth | 0.75 | eighth / **False** | eighth / **False** |
| eighth | 0.5 | sixteenth / **False** | eighth / True |
| dotted-sixteenth | 0.375 | sixteenth / **False** | sixteenth / **False** |
| sixteenth | 0.25 | thirtysecond / **False** | thirtysecond / **False** |
| thirtysecond | 0.125 | thirtysecond / True (coincidence) | thirtysecond / True |

Duration-class accuracy rose from 50% (5/10) to 70% (7/10) on this run,
tempo-convergence accuracy was unaffected (99.4bpm measured, 0.6% error,
identical to before), and every note >=1 beat is now exactly correct.
Traced the three remaining short-note misses (dotted-eighth,
dotted-sixteenth, sixteenth) to a *fourth*, distinct and narrower
mechanism: on real recorded audio (not present in the idealized offline
reproduction above, which used exactly block-aligned synthetic slicing),
these notes' own 20ms linear attack fade occasionally straddles a hop
boundary awkwardly enough that the block-to-block RMS ratio during the
ramp-up itself clears `ONSET_RMS_JUMP_DB`, firing a spurious same-key
re-onset within 1-2 hops of the note's own genuine attack and splitting
one note into two duration-tracker events — the later of which (the one
`scripts/acoustic_pipeline_test.py`'s own nearest-to-segment-end picking
logic selects) is missing its own true beginning. This is real-audio-
timing-jitter-sensitive rather than a clean deterministic bug the way the
three fixed mechanisms were (the exact same idealized synthetic sweep
that found bugs 1/2 does not reproduce it), and tightening the RMS-jump/
spectral-flux onset heuristics to suppress it risks the opposite failure
mode (missing a genuine fast repeated note, issue #55 story 3's explicit
scope) without further empirical tuning against real playing — already
flagged as provisional in this file's "Rhythm mode's thresholds/constants"
known-limitation entry above. Documented here rather than chased further
within this fix's budget; a good candidate for a future issue if it
proves to matter against real (non-synthetic) playing specifically, not
just this acoustic test's own construction.

Verified via `--source loopback` (a real audio-stack round trip: real
PortAudio buffering/timing, real device resampling, real OS-level
capture — but no physical air gap, so no room acoustics/reflections/mic
frequency response), not a physical speaker->mic re-verification — this
codebase has a documented history (issue #69's round 2, above) of a
loopback/synthetic pass not surviving real-mic testing, so this result
should be read with that same caveat until a real-mic round confirms it.
Regression-tested: `tests/test_duration_tracker.py` (the ghost-suppression
and `onset_backdate` mechanisms), `tests/test_note_smoother.py`
(`onset_backdate_hops`' note-change-only trigger condition), and
`tests/test_tempo_tracker.py` (the confidence gate, both that it holds
under non-periodic input and that it still tracks a genuine periodicity
change) — full suite green throughout
(`.venv/bin/python -m pytest tests/`), and a `chromatic`/`sustain`
acoustic-suite spot check confirmed no regression to steady-state pitch
accuracy or the existing `is_onset` misfire-rate baseline.
