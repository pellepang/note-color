"""The live analysis session: capture, the analysis thread, and the state
bundle both front-ends share.

Extracted from what used to be ``main.py`` (wayfinder map #145, ticket #146).
It lives in ``audio`` rather than in a front-end package for one reason: it is
core. ``SessionState`` owns the microphone, the analysis thread, the
sensitivity and source state, and the process-wide ``SoundEngine`` -- none of
which is terminal-specific, and all of which the DAW front-end needs. Leaving
it inside the terminal app would have forced ``gui`` to import ``tui`` to
reach it, which is exactly the dependency the package boundary exists to
prevent.

Nothing here renders anything. The ``run_*`` view drivers that consume it stay
in their own front-end packages.
"""

import queue
import threading
import time
from collections import deque
from typing import NamedTuple, Optional

import numpy as np

from notecolor.analysis import chroma
from notecolor.analysis import multipitch
from notecolor.analysis import rhythm_reanalysis
from notecolor.settings import config
from notecolor.settings.config_store import store
from notecolor.audio.audio_capture import AudioCapture, resolve_loopback_device
from notecolor.analysis.detection_backends import default_pitch_backend, default_poly_backend
from notecolor.analysis.pitch_detect import compute_spectrum
from notecolor.analysis.note_smoother import NoteSmoother
from notecolor.analysis.chord_smoother import ChordSmoother
from notecolor.analysis.duration_tracker import DurationTracker, duration_class_for_beats
from notecolor.analysis.onset_detect import chroma_flux
from notecolor.analysis.tempo_tracker import TempoTracker
from notecolor.analysis.color_map import note_to_hsl, hsl_to_rgb255, fifths_index
from notecolor.notation.session_recorder import SessionRecorder


SENSITIVITY_STEP = 1.25
SENSITIVITY_MIN = 0.1
SENSITIVITY_MAX = 10.0

class SourceState:
    """Shared between the render thread (owns the 'm' hotkey and the
    AudioCapture) and the status line (reads .value, .error every frame).
    Only the render thread ever writes it, so plain attribute access is
    fine, same rationale as Sensitivity."""

    def __init__(self, value):
        self.value = value
        self.error = None


class Sensitivity:
    """Shared between the analysis thread (reads .value every hop) and
    whichever thread owns the render loop (writes .value on a hotkey).
    Plain attribute access is safe here under CPython's GIL -- the value is
    read/written as a whole float, and staleness by one hop is harmless."""

    def __init__(self, value):
        self.value = value

    def adjust(self, factor):
        self.value = min(max(self.value * factor, SENSITIVITY_MIN), SENSITIVITY_MAX)


class ReanalysisBuffer:
    """Rolling per-hop history feeding the `tab` view's `R`-key non-causal
    rhythm re-analysis (issue #77) -- owned and appended to by the
    analysis thread alongside its other per-hop trackers
    (mono_duration_tracker/chord_duration_tracker/tempo_tracker, see
    analysis_loop()), read via `snapshot()` from the render thread's
    throwaway recompute thread (see `_handle_reanalysis_key()`). Holds
    `rhythm_reanalysis.HopRecord`s -- cheap derived per-hop values, not raw
    audio (see docs/research/live-noncausal-rhythm-reanalysis.md's Q1/Q2).

    Bounded by `config_store.store.preference("rhythm_reanalysis_window_seconds",
    ...)`, re-checked (cheap, mtime-checked, same hot-reload convention as
    every other preference this codebase reads every hop/frame) on every
    append so a live Settings-screen edit takes effect on the very next
    hop with no restart -- widening the window only grows *future*
    retention; a moment when the window was smaller has already discarded
    whatever's now outside even that older, smaller bound, so growing the
    window doesn't retroactively recover history that was never kept.

    `snapshot()` returns a plain list copy of the underlying deque -- safe
    against corruption from a concurrent append under CPython's GIL, but
    not a guaranteed fixed-point-in-time read (an append mid-copy could
    interleave). Acceptable because `R` only ever fires while frozen --
    see docs/research/live-noncausal-rhythm-reanalysis.md's Q5 for the
    full reasoning behind this choice over a request/response queue pair
    into the analysis thread."""

    def __init__(self, hop_seconds):
        self.hop_seconds = hop_seconds
        self._window_hops = 1
        self._deque = deque(maxlen=self._window_hops)

    def append(self, record):
        window_hops = max(1, int(round(
            store.preference("rhythm_reanalysis_window_seconds", config.RHYTHM_REANALYSIS_WINDOW_SECONDS)
            / self.hop_seconds
        )))
        if window_hops != self._window_hops:
            self._deque = deque(self._deque, maxlen=window_hops)
            self._window_hops = window_hops
        self._deque.append(record)

    def snapshot(self):
        return list(self._deque)


class ReanalysisState:
    """Shared between the render thread ('R' spawns the throwaway
    recompute thread and reads .in_progress every frame for the status
    line) and that thread itself (clears .in_progress when done) -- plain
    attribute access is safe under CPython's GIL, same rationale as
    Sensitivity/SourceState above."""

    def __init__(self):
        self.in_progress = False

class RenderItem(NamedTuple):
    """Per-hop analysis result, single-slot queue item. The first 9 fields
    are the original monophonic-pipeline shape/order; `note_stack` and
    `chord_name` are chord-mode additions, and `duration_hops`/
    `bpm_estimate` (issue #55) are the rhythm-pipeline additions after
    that. Existing call sites keep unpacking the first 9 positionally,
    adding a trailing capture for each later addition."""

    target_rgb: tuple
    is_onset: bool
    label: str
    freq: Optional[float]
    confidence: float
    rms: float
    fifths_idx: Optional[int]
    pitch_class: Optional[int]
    octave: Optional[int]
    note_stack: list
    chord_name: Optional[str]
    duration_hops: Optional[int]   # set only on the hop a note's duration finalizes, else None
    bpm_estimate: Optional[float]  # live tempo estimate, or None before enough history exists

def analysis_loop(capture, result_queue, stop_event, color_scheme, sensitivity, reanalysis_buffer,
                   session_recorder, pitch_backend, poly_backend):
    ring = np.zeros(config.WINDOW_SIZE, dtype=np.float64)
    low_ring = np.zeros(config.MULTIPITCH_LOW_WINDOW_SIZE, dtype=np.float64)
    smoother = NoteSmoother(config, sensitivity.value)
    chord_smoother = ChordSmoother(config)
    # require_onset_for_new_note=True: mono's NoteSmoother always carries a
    # trustworthy is_onset, so DurationTracker can (and, per issue #70,
    # must) refuse to open a new tracked note on a hop that isn't a real
    # attack -- see DurationTracker.__init__'s docstring. Chord mode has
    # no such signal (chord_notes below hardcodes is_onset=False) so its
    # tracker keeps the default off.
    mono_duration_tracker = DurationTracker(config, require_onset_for_new_note=True)
    chord_duration_tracker = DurationTracker(config)
    hop_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
    tempo_tracker = TempoTracker(config, hop_seconds)
    prev_chroma = None
    hop_index = 0

    while not stop_event.is_set():
        try:
            block = capture.get_block(timeout=0.5)
        except queue.Empty:
            continue

        block = block.astype(np.float64)
        ring = np.concatenate([ring[len(block):], block])
        low_ring = np.concatenate([low_ring[len(block):], block])
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0

        smoother.set_sensitivity(sensitivity.value)
        spectrum = compute_spectrum(ring)
        freq, confidence = pitch_backend.detect(ring, spectrum, config.SAMPLE_RATE)
        pitch_class, octave, is_onset = smoother.update(freq, confidence, rms, spectrum)

        if pitch_class is None:
            target_rgb = config.IDLE_RGB
            label = "-"
            fifths_idx = None
        else:
            hue, sat, light = note_to_hsl(pitch_class, octave, scheme=color_scheme,
                                           hue_override=store.note_hue_override(pitch_class))
            target_rgb = hsl_to_rgb255(hue, sat, light)
            label = f"{NOTE_NAMES[pitch_class]}{octave}"
            fifths_idx = fifths_index(pitch_class)

        # Chord-mode pipeline always runs, regardless of whether any
        # terminal view currently has 'P' toggled on -- validated cheap by
        # the latency budget, and it lets 'P' be a pure render-thread-local
        # flag with no shared state to coordinate.
        main_chroma = chroma.fold(spectrum, config.SAMPLE_RATE)
        bass_chroma = chroma.fold_bass(spectrum, config.SAMPLE_RATE)

        # Tempo tracking (issue #55) rides on the same chroma-flux novelty
        # signal chord mode already computes each hop -- always-on, same
        # "cheap enough, no gating" convention as the rest of the chord
        # pipeline above.
        chroma_novelty = chroma_flux(main_chroma, prev_chroma)
        bpm_estimate = tempo_tracker.update(chroma_novelty)
        prev_chroma = main_chroma

        # Monophonic duration tracking: at most one note-slot active at a
        # time, so mono_finalized has at most one entry.
        mono_notes = [(pitch_class, octave, rms, is_onset)] if pitch_class is not None else []
        # Issue #70: backdate a fresh note-change onset's onset_hop by
        # NoteSmoother's own known debounce lock-in delay -- see
        # note_smoother.py's onset_backdate_hops and DurationTracker.update()'s
        # docstring. 0 whenever this hop isn't itself a note-change onset.
        mono_finalized = mono_duration_tracker.update(
            mono_notes, hop_index, onset_backdate=smoother.onset_backdate_hops
        )
        duration_hops = mono_finalized[0][2] if mono_finalized else None

        multipitch_window = multipitch.select_window(
            ring, low_ring, main_chroma, bass_chroma, gate_ratio=config.MULTIPITCH_BASS_GATE_RATIO
        )
        raw_notes = poly_backend.detect(multipitch_window, config.SAMPLE_RATE)

        chord_name, raw_stack = chord_smoother.update(main_chroma, bass_chroma, raw_notes)

        # Chord-mode duration tracking (issue #64): fed from chord_smoother's
        # already-debounced raw_stack, not raw multipitch.detect() output.
        # multipitch.detect() re-picks spectral peaks independently every
        # hop, so a single noisy hop can drop a note from raw_notes even
        # while it's genuinely still sounding; chord_smoother's
        # NOTE_STACK_ATTACK_HOPS/RELEASE_HOPS hysteresis already absorbs
        # that kind of blip for display purposes (see its module
        # docstring). Driving chord_duration_tracker straight from
        # raw_notes bypassed that hysteresis entirely, so the same 1-hop
        # dropout that display shrugs off would still finalize the note's
        # duration early via DurationTracker.update()'s absence-based
        # path -- fragmenting one continuously-*displayed* note into two
        # short, individually-wrong duration events. Sourcing from
        # raw_stack instead means duration tracking only ever sees a note
        # disappear when the display does too. Mirrors
        # batch_transcribe.py's already-correct pattern of building its
        # chord_magnitude/chord_onsets from chord_smoother.update()'s
        # debounced output rather than raw multipitch.detect().
        #
        # is_onset is still hardcoded False here deliberately: neither
        # multipitch.detect() nor chord_smoother's hysteresis carries a
        # persistent per-note identity that could distinguish "genuine
        # re-attack of an already-sounding pitch" from "still the same
        # note" the way NoteSmoother's monophonic onset gate can -- the
        # ordinary appear/sustain/disappear lifecycle still tracks
        # correctly via DurationTracker's absence-based finalization, it
        # just won't split a same-pitch re-attack mid-sustain into two
        # separate chord-mode notes. A deliberate, bounded scope-narrowing
        # versus the mono path, unchanged by this fix.
        chord_notes = [
            (entry["pitch_class"], entry["octave"], entry["confidence"], False) for entry in raw_stack
        ]
        chord_finalized = chord_duration_tracker.update(chord_notes, hop_index)
        chord_finalized_by_key = {(pc, oct_): dur for pc, oct_, dur in chord_finalized}

        note_stack = []
        for entry in raw_stack:
            stack_hue, stack_sat, stack_light = note_to_hsl(
                entry["pitch_class"], entry["octave"], scheme=color_scheme,
                hue_override=store.note_hue_override(entry["pitch_class"]),
            )
            note_stack.append(
                {
                    "pitch_class": entry["pitch_class"],
                    "octave": entry["octave"],
                    "confidence": entry["confidence"],
                    "rgb": hsl_to_rgb255(stack_hue, stack_sat, stack_light),
                    "is_bass": entry["is_bass"],
                    "duration_hops": chord_finalized_by_key.get((entry["pitch_class"], entry["octave"])),
                }
            )

        # Issue #77: append this hop's cheap derived values (not raw audio,
        # see rhythm_reanalysis.py's docstring) to the rolling buffer the
        # tab view's 'R' non-causal recompute snapshots from. raw_stack --
        # not note_stack -- for chord_notes, since it's the plain
        # (pitch_class, octave, confidence) shape rhythm_reanalysis.recompute()
        # reconstructs magnitude arrays from, mirroring how
        # chord_duration_tracker above is already fed from the same source.
        reanalysis_buffer.append(
            rhythm_reanalysis.HopRecord(
                hop_index=hop_index,
                mono=(pitch_class, octave, rms, is_onset) if pitch_class is not None else None,
                chord_notes=tuple((e["pitch_class"], e["octave"], e["confidence"]) for e in raw_stack),
                chroma_novelty=chroma_novelty,
            )
        )

        # Opt-in session recording (armed live via 's', off by default) --
        # hooked here rather than render-thread-side for the same reason
        # reanalysis_buffer.append() is: result_queue is single-slot and
        # overwrite-on-full, so a render-thread-side recorder would
        # silently miss a finalized note whenever two hops complete
        # between two polls. record_hop() is a cheap no-op whenever not
        # armed (see session_recorder.py).
        session_recorder.record_hop(pitch_class, octave, note_stack, chord_name, duration_hops, bpm_estimate,
                                     hop_index, hop_seconds)

        item = RenderItem(target_rgb, is_onset, label, freq, confidence, rms, fifths_idx, pitch_class, octave,
                           note_stack, chord_name, duration_hops, bpm_estimate)
        hop_index += 1
        _overwrite(result_queue, item)


def _overwrite(q, item):
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass

class SessionState:
    """Everything a run_* function needs, created lazily once per process
    and reused for its entire life -- the mechanism behind `virtualnote`'s
    instant "back to menu" transitions (issue #40). `AudioCapture`, the
    analysis thread, `Sensitivity`, and `SourceState` all live here rather
    than being recreated per tool switch: opening the mic and spinning up
    the analysis thread has a real startup cost (and, for the mic itself,
    a visible "listening" side effect), so `ensure_started()` defers both
    until the first tool actually needs them -- sitting at `virtualnote`'s
    bare menu never opens the mic. Once created they persist across
    repeated menu round-trips (no `AudioCapture` teardown/rebuild, unlike
    `M`'s deliberate `.restart()` for an actual source *change*), and so
    do `sensitivity`/`source_state`'s current values -- better UX than
    resetting to CLI defaults every time a user picks a different tool.
    A single `color_scheme` is fixed for the session's whole life, same as
    it always has been for one process -- there's no live toggle for it,
    so there's nothing to persist differently per tool switch.

    `pitch_backend`/`poly_backend` (detection_backends.py) default to
    `None`, resolved to `default_pitch_backend(config)`/
    `default_poly_backend(config)` here -- YinBackend/SpectralPeakBackend
    built from config.* exactly as analysis_loop() called detect_pitch()/
    multipitch.detect() directly before this seam existed, so default
    behavior is unchanged. Explicit params exist so a future alternative
    backend can be swapped in by construction, without editing
    analysis_loop()'s body."""

    def __init__(self, color_scheme, sensitivity_value, source_value, pitch_backend=None, poly_backend=None):
        self.color_scheme = color_scheme
        self.sensitivity = Sensitivity(sensitivity_value)
        self.source_state = SourceState(source_value)
        self.pitch_backend = pitch_backend if pitch_backend is not None else default_pitch_backend(config)
        self.poly_backend = poly_backend if poly_backend is not None else default_poly_backend(config)
        self.capture = None
        self.result_queue = None
        self.stop_event = None
        self.analysis_thread = None
        self.reanalysis_buffer = None
        # Opt-in, off by default (armed live via 's') -- constructed eagerly
        # here rather than lazily in ensure_started(), unlike capture/the
        # analysis thread: unlike opening the mic, constructing a
        # SessionRecorder has no side effect (no file is opened until
        # armed), and it needs to exist before ensure_started() builds the
        # analysis thread's arg tuple below.
        self.session_recorder = SessionRecorder()
        # Map #99 / decision #105: the one process-wide SoundEngine, created
        # lazily by ensure_sound_engine() below (constructing one opens no
        # device, but there is no reason to construct it for a session that
        # never plays a note either) and then kept for the process's whole
        # life, exactly as `capture` is -- so switching from the editor to a
        # live view never drops or reopens the audio *output* device, the
        # same way `|` never reopens the mic.
        self.sound_engine = None

    def ensure_sound_engine(self):
        """Idempotent: returns this process's one `sound_engine.SoundEngine`,
        creating and starting it on first use. Mirrors `ensure_started()`
        above (issue #40's lifecycle for audio input) for audio output, per
        decision #105 -- two independent lazy starts rather than one, since
        a tool can want input without output (every existing live view) or
        output without input (the score editor, the coming synth tool).

        `detection_active` is passed as a callable, not a bool: which of the
        two polyphony budgets applies depends on whether the analysis thread
        is running *at the moment a note is played*, which can change during
        the engine's life (menu -> editor -> a live view, all one process).

        Imported locally, same convention as `playback`/`score_writer`/
        `pygame` -- nothing on the capture/analysis path may pay for the
        sound engine."""
        if self.sound_engine is None:
            from notecolor.audio import sound_engine

            self.sound_engine = sound_engine.SoundEngine(
                detection_active=lambda: self.capture is not None,
            )
        self.sound_engine.ensure_started()
        return self.sound_engine

    def ensure_started(self):
        """Idempotent: a no-op once the capture/analysis thread already
        exist, so both main()'s standalone (eager, called once) and
        shell.py's menu loop (lazy, called before every tool entry) can
        call this unconditionally. May raise RuntimeError if the initial
        source is 'loopback' and no loopback device can be resolved --
        callers decide how to surface that (main() maps it to
        parser.error(); shell.py reports it inline and stays at the menu)."""
        if self.capture is not None:
            return
        device = None
        if self.source_state.value == "loopback":
            device = resolve_loopback_device()  # raises RuntimeError on failure
        self.capture = AudioCapture(config.SAMPLE_RATE, config.BLOCK_SIZE, config.QUEUE_SIZE, device=device)
        self.capture.start()
        self.result_queue = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        # Issue #77: owned by the analysis thread for its whole life, same
        # as the trackers analysis_loop() constructs for itself -- created
        # here (not inside analysis_loop()) so run_terminal_tab (via
        # run_session, below) can reach the same instance to snapshot from.
        self.reanalysis_buffer = ReanalysisBuffer(config.BLOCK_SIZE / config.SAMPLE_RATE)
        self.analysis_thread = threading.Thread(
            target=analysis_loop,
            args=(self.capture, self.result_queue, self.stop_event, self.color_scheme, self.sensitivity,
                  self.reanalysis_buffer, self.session_recorder, self.pitch_backend, self.poly_backend),
            daemon=True,
        )
        self.analysis_thread.start()

    def stop(self):
        """Process-exit-only teardown -- never called between tool
        switches, only once the whole session (menu included) is done."""
        # Idempotent and safe even if recording was never armed (see
        # SessionRecorder.close()) -- flushes/closes a still-armed
        # recorder's file rather than relying on the user to press 's'
        # again before quitting.
        self.session_recorder.close()
        # Idempotent and safe whether or not a note was ever played (see
        # SoundEngine.stop()); closed unconditionally, before the capture
        # check below, since a session can have opened output without ever
        # having opened input.
        if self.sound_engine is not None:
            self.sound_engine.stop()
        if self.capture is None:
            return
        self.stop_event.set()
        self.capture.stop()

