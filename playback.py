"""Score playback (map #24, subsystem 3 of rhythm -> score writer ->
**playback** -> editor -> theory analysis, per issue #35's build order).

Decision #32 (`gh issue view 32`) already settled the approach: a NumPy
oscillator+ADSR synth (no soundfont/FluidSynth dependency, per #28's
research -- that stays a future upgrade, not rejected outright), reusing
`sounddevice.OutputStream` the same way `audio_capture.py` reuses
`InputStream` (already a dependency, zero new package). Both an offline
whole-buffer pre-render mode and a live per-event scheduling mode are
first-class (#32's explicit "both, not a single pick"). Color
sonification is explicitly out of scope for v1 (#32).

Never imported by `analysis_loop()`/`SessionState`/any live-capture code
path -- playback is strictly an opt-in offline/replay-time feature, same
isolation convention `batch_transcribe.py` (librosa) and `score_writer.py`
(music21) already establish for this project's other "real but
live-path-irrelevant" dependencies. Unlike those two, `sounddevice` is
already a live-path dependency (via `audio_capture.py`), so there's no
import-cost concern here -- the isolation is about *usage* (never called
from the capture/analysis thread), not import weight.

**Which mode fits which caller, and why (not a free choice per call
site):** offline pre-render is the natural fit for `virtualnote
transcribe --play` -- a whole `TranscriptionResult` already exists in
full before playback starts, so there is nothing to schedule against;
pre-rendering once up front is strictly simpler and gives sample-accurate
timing for free. Live per-event scheduling is the natural fit for
`virtualnote replay --play` -- `run_replay_session()`'s loop already
walks `session_player.group_columns()` paced by real elapsed time
(`time.sleep()` between columns, divided by `--speed`), so triggering
each note's audio at the exact moment its column is pushed reuses that
existing pacing clock instead of running a second, independent one that
could drift against the visuals. Pre-rendering the whole replay up front
would also mean buffering a session of unknown, possibly very long,
duration entirely in memory for no benefit `virtualnote transcribe`
already needed anyway.

**Timing accuracy, live mode:** `LiveScheduler.trigger_note()` does not
sleep at all -- it synthesizes the note's full waveform immediately (a
few hundred microseconds of NumPy, not audible-scale latency) and hands
it to a lock-protected list of "active voices" that `OutputStream`'s own
audio callback mixes into each output block as it's pulled. This mirrors
`AudioCapture`'s own callback-thread + non-blocking-handoff shape in
reverse (`audio_capture.py`'s docstring): the caller thread only ever
appends to a list, it never blocks on or drives the audio clock itself,
so onset latency is bounded by one callback block
(`config.PLAYBACK_BLOCK_SIZE`/`config.PLAYBACK_SAMPLE_RATE` seconds,
~11ms at the defaults below) rather than by Python-thread sleep-loop
jitter -- the same class of latency #28's research measured for
callback-based playback (~3ms +/- 1ms) and comfortably inside this
project's existing "under 150ms end-to-end" budget. `LiveScheduler` also
exposes `schedule_note(..., delay_seconds=...)` for a future caller that
needs the scheduler itself to own timing (e.g. a not-yet-built live
practice-mode target-playback) -- implemented as one dedicated thread
that sleeps in short bounded increments toward the next due event's
`time.perf_counter()` deadline rather than one dumb full-duration sleep
per note (the sleep-jitter difference #28's research measured: ~2ms
perf_counter-paced vs. ~15ms naive). No caller in this codebase uses
`schedule_note()` yet; `trigger_note()` (fired directly off
`run_replay_session()`'s own already-paced loop) is what both current
integrations need.
"""

import queue
import threading
import time

import numpy as np
import sounddevice as sd

import config


def note_frequency(pitch_class, octave):
    """Hz for `pitch_class` (0-11)/`octave` under standard MIDI tuning
    (A4=440Hz, C4=midi 60) -- the same formula this repo's test suite
    already assumes independently (see e.g. tests/test_chroma.py's
    freq_for()). Kept local rather than factored into a shared module:
    no other non-test code needs pitch-class/octave -> frequency
    conversion until this module."""
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _adsr_envelope(num_samples, sample_rate, attack, decay, sustain_level, release):
    """A linear-segment ADSR envelope, `num_samples` long. `attack`/`decay`/
    `release` are seconds; `sustain_level` is 0..1. The sustain segment
    fills whatever's left after attack+decay+release are subtracted from
    `num_samples` (clamped to >=0) -- for a note shorter than
    attack+decay+release, attack/decay/release are scaled down
    proportionally so the envelope still reaches zero exactly at
    `num_samples` rather than clipping mid-release (an audible click a
    fixed-length envelope would otherwise produce on short/staccato
    notes, which duration-tracked notes as short as ~50ms, per this
    project's own duration_tracker.py doc, are a real, not hypothetical,
    case here)."""
    total = attack + decay + release
    if total > 0 and (attack + decay + release) * sample_rate > num_samples:
        scale = num_samples / (total * sample_rate)
        attack, decay, release = attack * scale, decay * scale, release * scale

    n_attack = int(attack * sample_rate)
    n_decay = int(decay * sample_rate)
    n_release = int(release * sample_rate)
    n_sustain = max(0, num_samples - n_attack - n_decay - n_release)

    segments = []
    if n_attack:
        segments.append(np.linspace(0.0, 1.0, n_attack, endpoint=False))
    if n_decay:
        segments.append(np.linspace(1.0, sustain_level, n_decay, endpoint=False))
    if n_sustain:
        segments.append(np.full(n_sustain, sustain_level))
    if n_release:
        segments.append(np.linspace(sustain_level, 0.0, n_release, endpoint=True))

    envelope = np.concatenate(segments) if segments else np.zeros(0)
    # Rounding attack/decay/release*sample_rate to ints can leave the
    # concatenated envelope a sample or two short of num_samples --
    # pad with silence (zeros) rather than repeat/stretch a segment,
    # since any such gap is sub-millisecond and inaudible either way.
    if len(envelope) < num_samples:
        envelope = np.concatenate([envelope, np.zeros(num_samples - len(envelope))])
    return envelope[:num_samples]


def synthesize_note(pitch_class, octave, duration_seconds, sample_rate=None, velocity=1.0):
    """One ADSR-enveloped note as a mono float32 NumPy array, covering
    `duration_seconds` plus config.PLAYBACK_RELEASE_SECONDS of release
    tail (so the note actually fades out audibly rather than being cut
    off at the exact instant it was detected as ending).

    Waveform: a small fixed harmonic stack (fundamental + 2nd + 3rd
    partials at descending weight), not a bare sine -- a pure sine at
    these ADSR settings reads as an audibly thin "test tone," while a
    handful of harmonics gets meaningfully closer to "an instrument" for
    negligible extra NumPy cost (issue #28's research flagged this exact
    tradeoff -- "a few adjustable waveforms is the ceiling without real
    modelling work" -- this is that low-effort ceiling, not an attempt at
    realism). Weights/ratios are `config.PLAYBACK_HARMONIC_WEIGHTS`, a
    judgment call left open by #32 -- picked empirically (by ear) rather
    than measured, documented here rather than treated as load-bearing:
    revisit freely if the timbre doesn't hold up in practice."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    total_seconds = duration_seconds + config.PLAYBACK_RELEASE_SECONDS
    num_samples = max(1, int(total_seconds * sample_rate))

    freq = note_frequency(pitch_class, octave)
    t = np.arange(num_samples) / sample_rate
    wave = np.zeros(num_samples, dtype=np.float64)
    for harmonic_number, weight in enumerate(config.PLAYBACK_HARMONIC_WEIGHTS, start=1):
        wave += weight * np.sin(2.0 * np.pi * freq * harmonic_number * t)
    wave /= sum(config.PLAYBACK_HARMONIC_WEIGHTS)  # keep peak amplitude <=1 regardless of stack size

    envelope = _adsr_envelope(
        num_samples, sample_rate,
        attack=config.PLAYBACK_ATTACK_SECONDS,
        decay=config.PLAYBACK_DECAY_SECONDS,
        sustain_level=config.PLAYBACK_SUSTAIN_LEVEL,
        release=config.PLAYBACK_RELEASE_SECONDS,
    )
    return (wave * envelope * velocity).astype(np.float32)


def render_offline(notes, sample_rate=None):
    """Pre-renders `notes` (an iterable of `(onset_seconds, pitch_class,
    octave, duration_seconds)` tuples -- a `velocity` 5th element is
    optional, defaulting to 1.0) into one mono float32 NumPy buffer,
    additively mixed (simultaneous notes -- e.g. a chord's tones sharing
    one onset -- simply sum at the same buffer positions) and soft-clipped
    via `np.tanh` to avoid harsh digital clipping if several notes'
    peaks happen to coincide. Buffer length is exactly long enough for the
    latest-ending note (its own onset + duration + release tail).

    Sample-accurate by construction: unlike live scheduling, timing here
    is entirely a buffer-index computation, not a wall-clock one, so
    there is no scheduling jitter to reason about at all -- see this
    module's docstring for why offline pre-render is the right mode for
    `virtualnote transcribe --play` specifically (a whole
    TranscriptionResult already exists before playback starts)."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    notes = list(notes)
    if not notes:
        return np.zeros(0, dtype=np.float32)

    rendered = []
    end_samples = []
    for note in notes:
        onset_seconds, pitch_class, octave, duration_seconds = note[:4]
        velocity = note[4] if len(note) > 4 else 1.0
        samples = synthesize_note(pitch_class, octave, duration_seconds, sample_rate, velocity)
        onset_sample = max(0, int(round(onset_seconds * sample_rate)))
        rendered.append((onset_sample, samples))
        end_samples.append(onset_sample + len(samples))

    buffer = np.zeros(max(end_samples), dtype=np.float64)
    for onset_sample, samples in rendered:
        buffer[onset_sample:onset_sample + len(samples)] += samples
    return np.tanh(buffer).astype(np.float32)


def play_offline(notes, sample_rate=None, blocking=True):
    """Renders `notes` via `render_offline()` then plays the whole buffer
    through one `sd.OutputStream.write()` call -- a single write, not a
    per-note stream, since the entire buffer is already sample-accurate
    on its own. `blocking=True` (default) waits for playback to finish
    before returning, matching `virtualnote transcribe`'s existing
    one-shot-then-exit shape; `blocking=False` returns immediately for a
    caller that wants to do something else while it plays (no current
    caller needs this, exposed for symmetry with `sd.play()`'s own API)."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    buffer = render_offline(notes, sample_rate)
    if len(buffer) == 0:
        return
    sd.play(buffer, sample_rate, blocking=blocking)


class LiveScheduler:
    """Per-event live playback -- see module docstring for the full
    rationale versus `render_offline()`/`play_offline()`.

    Not thread-per-note: `_active` is one list of `(samples, position)`
    pairs, appended to by whichever thread calls `trigger_note()`
    (typically the caller's own already-paced loop, e.g.
    `run_replay_session()`) and drained/mixed by the `OutputStream`
    callback. `_lock` guards every read/write of `_active` -- unlike
    `main.ReanalysisBuffer`'s deque (safe under the GIL for single
    append/snapshot ops without an explicit lock, per that module's own
    documented reasoning), this class does an append *and* an in-place
    list-comprehension rebuild every callback tick, which is not a single
    atomic bytecode op, so an explicit lock is the correct call here
    rather than relying on the same GIL argument."""

    def __init__(self, sample_rate=None, block_size=None):
        self.sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
        self.block_size = block_size or config.PLAYBACK_BLOCK_SIZE
        self._active = []
        self._lock = threading.Lock()
        self._stream = None
        self._due_queue = queue.PriorityQueue()
        self._scheduler_thread = None
        self._stop_event = threading.Event()

    def start(self):
        """Opens the output stream and starts the background due-event
        thread. Idempotent -- calling start() while already started is a
        no-op, mirroring AudioCapture's own defensive shape."""
        if self._stream is not None:
            return
        self._stop_event.clear()
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate, blocksize=self.block_size, channels=1,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()
        self._scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self._scheduler_thread.start()

    def stop(self):
        """Closes the stream and drops any still-sounding voices.
        Idempotent, same convention as SessionRecorder.close()."""
        self._stop_event.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=1.0)
            self._scheduler_thread = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._active = []

    def trigger_note(self, pitch_class, octave, duration_seconds, velocity=1.0):
        """Synthesizes and immediately queues one note for playback on the
        next callback pull -- no scheduling delay. This is what
        `run_replay_session()` calls directly from its own already-paced
        loop (see module docstring for why)."""
        samples = synthesize_note(pitch_class, octave, duration_seconds, self.sample_rate, velocity)
        with self._lock:
            self._active.append([samples, 0])

    def schedule_note(self, pitch_class, octave, duration_seconds, delay_seconds=0.0, velocity=1.0):
        """Queues a note to be triggered `delay_seconds` from now, via the
        background scheduler thread's own precise wait (see module
        docstring). No current caller uses this -- `trigger_note()` covers
        both of this module's real integrations -- kept for a future
        caller that needs the scheduler to own timing rather than an
        external already-paced loop."""
        due_time = time.perf_counter() + max(0.0, delay_seconds)
        self._due_queue.put((due_time, (pitch_class, octave, duration_seconds, velocity)))

    def _run_scheduler(self):
        # Sleeps in short bounded increments toward the next due event's
        # deadline rather than one dumb sleep per note (see module
        # docstring's #28-research citation on why) -- also lets stop()
        # break out promptly via _stop_event rather than sleeping through
        # a shutdown request.
        max_step = 0.02
        while not self._stop_event.is_set():
            try:
                due_time, note = self._due_queue.get(timeout=max_step)
            except queue.Empty:
                continue
            remaining = due_time - time.perf_counter()
            while remaining > 0 and not self._stop_event.is_set():
                time.sleep(min(max_step, remaining))
                remaining = due_time - time.perf_counter()
            if not self._stop_event.is_set():
                self.trigger_note(*note)

    def _callback(self, outdata, frames, time_info, status):
        if status:
            print(f"[playback] status: {status}")
        mix = np.zeros(frames, dtype=np.float32)
        with self._lock:
            still_active = []
            for samples, position in self._active:
                available = len(samples) - position
                take = min(frames, available)
                if take > 0:
                    mix[:take] += samples[position:position + take]
                new_position = position + take
                if new_position < len(samples):
                    still_active.append([samples, new_position])
            self._active = still_active
        outdata[:, 0] = np.tanh(mix)
