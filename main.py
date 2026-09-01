"""Real-time audio -> color display.

mic or system-output loopback -> AudioCapture (callback thread)
    -> analysis thread: ring buffer -> YIN pitch detect -> NoteSmoother -> color_map
    -> single-slot queue
    -> main thread: ColorAnimator -> Display (pygame window, or terminal)

GUI controls: Esc/close window to quit, F to toggle fullscreen, D to toggle
debug overlay, Up/Down to adjust pitch-detection sensitivity, H to toggle
the keybind-legend line, backslash (unshifted '|') to return to the menu
when run via virtualnote.py's shell (a no-op quit when run standalone --
see main()).
Terminal mode: Ctrl+C to quit, Up/Down for sensitivity, M to toggle the
audio source (mic <-> loopback) live, P to toggle chord mode (chroma-vector
chord recognition, up to 6 simultaneous notes) live -- terminal views only,
not the GUI. 'fill'/'wheel' start monophonic and P opts *up* into chord
mode; 'tab' starts polyphonic (chord mode on) by default and P opts *down*
to monophonic instead -- same P key, same boolean flip, just a different
starting value for 'tab'. 'tab' view only: N toggles the notehead render
style (symbol glyph <-> bare letter name), L toggles the clef+note-letter
legend column on/off, Space freezes/un-freezes the view (scrolling and
per-column dimming pause; the pipeline keeps running in the background).
'tab' view only, freeze-mode-only (issue #77): R triggers a non-causal
rhythm re-analysis over a rolling buffer of recent hops, correcting
duration glyphs/tempo/barlines already on screen in place; Left/Right
scroll back/forward through retained note-column history.
Global across every terminal view (issue #40): '|' returns to virtualnote's
menu (a harmless quit when this module is run standalone via `main.py`,
which has no menu), H toggles a context-sensitive keybind-legend line
below the status line, on by default.
No display server required.
"""

import argparse
import math
import os
import queue
import select
import sys
import threading
import time
from collections import deque
from typing import NamedTuple, Optional

import numpy as np

import batch_transcribe
import chroma
import config
import multipitch
import rhythm_reanalysis
from config_store import store
from audio_capture import AudioCapture, resolve_loopback_device
from detection_backends import default_pitch_backend, default_poly_backend
from pitch_detect import compute_spectrum
from note_smoother import NoteSmoother
from chord_smoother import ChordSmoother
from duration_tracker import DurationTracker, duration_class_for_beats
from onset_detect import chroma_flux
from tempo_tracker import TempoTracker
from color_map import note_to_hsl, hsl_to_rgb255, fifths_index, NOTE_NAMES, NOTE_NAMES_FIFTHS
from animation import ColorAnimator
from session_recorder import SessionRecorder
from session_player import load_events, group_columns

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:  # Windows has neither module
    _HAS_TERMIOS = False

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


class RawKeys:
    """Non-blocking single-key reads from stdin, for terminal hotkeys.
    Inert (poll() always returns None) when stdin isn't a real TTY or
    termios/tty aren't available (Windows) -- terminal modes keep working,
    just without live hotkeys, in that case."""

    def __init__(self):
        self._active = _HAS_TERMIOS and sys.stdin.isatty()
        self._old_settings = None
        if self._active:
            self._old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

    @property
    def active(self):
        """True once a real TTY was found and raw mode entered -- lets a
        caller that *requires* a keypress to proceed (unlike every
        run_terminal_* loop, which just keeps rendering regardless) avoid
        blocking forever on poll(), which always returns None when this is
        False."""
        return self._active

    _ARROW_BY_FINAL_BYTE = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}

    def poll(self):
        # Reads via os.read() on the raw fd, never sys.stdin.read() --
        # sys.stdin is a buffered TextIOWrapper, and mixing select() (which
        # only sees data still sitting at the OS level) with a buffered
        # read() is a classic trap: read(1) can slurp every byte the pty
        # already delivered into Python's internal buffer while only
        # handing back the one requested, so the *next* select() call sees
        # nothing left at the fd and falsely reports "no more input yet" --
        # even though the rest of an ESC [ <letter> arrow burst was sitting
        # right there. That's what made Up/Down on the menu screen require
        # holding the key (repeated OS key-repeat bursts occasionally
        # landing on a lucky read boundary) instead of registering on a
        # single tap. os.read() is unbuffered, so select() and read() stay
        # in sync with the actual fd state.
        fd = sys.stdin.fileno()
        if not self._active or not select.select([fd], [], [], 0)[0]:
            return None
        ch = os.read(fd, 1).decode(errors="ignore")
        if ch != "\x1b":
            return ch
        # Arrow keys send ESC [ <letter> as one burst, but under a
        # multiplexer (tmux) or a laggy pty the two continuation bytes can
        # arrive a few ms after ESC itself rather than in the same read --
        # a 0-timeout select() right here would misread that as a lone
        # Escape keypress and silently drop the arrow key.
        # config.ESCAPE_SEQUENCE_TIMEOUT gives the rest of the burst a
        # brief window to show up; nothing in this app binds a bare
        # Escape keypress to an action, so the extra wait before falling
        # back to "not an arrow key" is never user-visible.
        timeout = config.ESCAPE_SEQUENCE_TIMEOUT
        if not select.select([fd], [], [], timeout)[0]:
            return None
        if os.read(fd, 1).decode(errors="ignore") != "[" or not select.select([fd], [], [], timeout)[0]:
            return None
        return self._ARROW_BY_FINAL_BYTE.get(os.read(fd, 1).decode(errors="ignore"))

    def restore(self):
        if self._active:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)


def _positive_float(text):
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _parse_time_signature(text):
    """'N/D' -> (N, D) as positive ints, for --time-signature. Mirrors
    _positive_float's style: raises argparse.ArgumentTypeError on anything
    that isn't exactly two positive-integer parts separated by '/'."""
    parts = text.split("/")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("must be in N/D form, e.g. 3/4")
    try:
        numerator, denominator = int(parts[0]), int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError("must be in N/D form, e.g. 3/4") from None
    if numerator <= 0 or denominator <= 0:
        raise argparse.ArgumentTypeError("both N and D must be > 0")
    return numerator, denominator


def _handle_sensitivity_key(key, sensitivity):
    if key == "DOWN":
        sensitivity.adjust(1.0 / SENSITIVITY_STEP)
    elif key == "UP":
        sensitivity.adjust(SENSITIVITY_STEP)


def _key_hint(action):
    """Status-line hint for a remappable action's bound key (issue #41) --
    'space' spelled out instead of the literal, invisible character."""
    bound = store.keybind(action)
    return "space" if bound == " " else bound


def _handle_source_key(key, capture, source_state):
    if key is None or key.lower() != store.keybind("source_toggle").lower():
        return
    new_source = "loopback" if source_state.value == "mic" else "mic"
    try:
        if new_source == "loopback":
            device = resolve_loopback_device()
        else:
            os.environ.pop("PULSE_SOURCE", None)
            device = None
    except RuntimeError as exc:
        source_state.error = str(exc)
        return
    capture.restart(device)
    source_state.value = new_source
    source_state.error = None


def _handle_session_record_key(key, session_recorder):
    """Opt-in live session recorder toggle (default 's'), available in
    every terminal view (fill/wheel/tab) -- GUI has no live-hotkey
    mechanism for toggles like this, same established out-of-scope
    precedent as chord mode's 'P'. Mutates session_recorder in place
    (opens/closes its backing file), same shape as _handle_source_key."""
    bound = store.keybind("session_record_toggle")
    if key is not None and key.lower() == bound.lower():
        session_recorder.toggle()


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


def _status_text(label, freq, confidence, rms, sensitivity, source_state=None, chord_name=None, chord_mode=False):
    if chord_mode:
        text = f"chord={(chord_name or ''):<14s} sens={sensitivity.value:.2f} (up/down)"
    else:
        freq_str = f"{freq:6.1f}Hz" if freq else "  --  "
        text = (f"note={label:<4s} freq={freq_str} conf={confidence:.2f} rms={rms:.4f} "
                f"sens={sensitivity.value:.2f} (up/down)")
    if source_state is not None:
        text += f"  src={source_state.value} ({_key_hint('source_toggle')})"
        if source_state.error:
            text += f"  [source switch failed: {source_state.error}]"
    return text


def _handle_chord_mode_key(key, chord_mode):
    """P toggles chord_mode -- a plain boolean flip, direction-agnostic.
    fill/wheel start False (opt *up* into chord mode); tab starts True
    (opt *down* to monophonic) -- the starting value lives in each view's
    own run_terminal_* function, not here."""
    bound = store.keybind("chord_mode_toggle")
    return not chord_mode if (key is not None and key.lower() == bound.lower()) else chord_mode


def _handle_notehead_style_key(key, notehead_style):
    """'tab' view only: N toggles the notehead render style (issue #21) --
    *symbol* (open notehead glyph + Unicode accidental) <-> *name* (bare
    letter + ASCII accidental, no octave digit)."""
    bound = store.keybind("notehead_style_toggle")
    if key is None or key.lower() != bound.lower():
        return notehead_style
    return "name" if notehead_style == "symbol" else "symbol"


def _handle_legend_key(key, legend_on):
    """'tab' view only: L toggles the clef+note-letter legend column on/off
    live (issue #19), reclaiming its width for note columns when off."""
    bound = store.keybind("legend_toggle")
    return not legend_on if (key is not None and key.lower() == bound.lower()) else legend_on


def _handle_back_to_menu_key(key):
    """Global (every terminal tool, issue #40): '|' is the always-live
    back-to-menu keybind, same tier as M/P/H -- a run_terminal_* loop
    returns the "menu" sentinel the instant this fires, through its
    existing finally block (keys.restore()/display.quit() still run).
    GUI wires its own pygame K_BACKSLASH check directly in run_gui rather
    than sharing this raw-key-string handler (the shifted '|' character
    isn't how pygame reports the unshifted physical key)."""
    return key == "|"


def _handle_help_legend_key(key, help_legend_on):
    """Global (every terminal tool, issue #40): H toggles the persistent,
    context-sensitive keybind-legend line shown below the status line.
    Default True; session-local only -- no persistence across runs, that's
    issue #41's job. Direction-agnostic boolean flip, same shape as
    _handle_chord_mode_key's P. Named to avoid colliding with tab's older,
    unrelated _handle_legend_key/legend_on (the staff clef+letter legend
    *column*, a different feature -- see that function's docstring)."""
    return not help_legend_on if (key is not None and key.lower() == "h") else help_legend_on


def _legend_line(view_hints):
    """Builds the optional extra status-line row shown when the H toggle
    is on: '|'/'h' first (always live, every view), then whatever hotkeys
    the calling view actually has. Deliberately a plain joined string, not
    a UI framework -- issue #40 owns only the toggle plumbing; the visual
    design of the whole shell (including this line) is #42's job."""
    return "  ".join(["|=menu", "h=legend"] + view_hints)


def _handle_freeze_key(key, frozen):
    """'tab' view only: Space toggles freeze-frame (issue #23) -- while
    frozen, run_terminal_tab stops pulling new items off result_queue (so
    no new columns get pushed and no stale label/freq/etc. get overwritten)
    and TabDisplay.render() is called with frozen=True (every visible
    column pinned to age 0, overriding issue #22's fade). The underlying
    analysis pipeline keeps running regardless -- result_queue is a
    single-slot always-overwritten queue, so simply not draining it while
    frozen causes no backlog, matching how every other view already
    behaves under backpressure. Un-freezing resumes live immediately, no
    catch-up of anything that happened while frozen."""
    bound = store.keybind("freeze_toggle")
    return not frozen if (key is not None and key.lower() == bound.lower()) else frozen


def _handle_scroll_keys(key, frozen, scroll_offset, max_offset):
    """'tab' view only: Left/Right scroll back/forward through TabDisplay's
    retained history while frozen (issue #77) -- a no-op outside freeze,
    since scroll_offset is meaningless against a live-scrolling tail (and
    run_terminal_tab resets it to 0 the moment freeze is turned back off,
    same "no catch-up" convention Space itself already follows). `key` is
    the raw "LEFT"/"RIGHT" token RawKeys.poll() returns. `max_offset`
    should be `len(display.entries) - 1` -- offset can't hide every
    retained entry off the tail; at least one must stay visible to play
    the role of "the newest visible column" for that offset. Left
    increases the offset (scrolls further back); Right decreases it
    (scrolls back toward live)."""
    if not frozen:
        return scroll_offset
    if key == "LEFT":
        return min(scroll_offset + 1, max(max_offset, 0))
    if key == "RIGHT":
        return max(scroll_offset - 1, 0)
    return scroll_offset


def _handle_mark_keys(key, frozen, mark_start, mark_end, timestamp):
    """'tab' view only: loop/section markers -- `mark_range_start`/
    `mark_range_end` each capture `timestamp` (the point in history
    currently being looked at; see TabDisplay.timestamp_at_offset(), which
    already accounts for any active Left/Right scrollback) as one end of a
    range that `_handle_reanalysis_key()` later scopes the R-key non-causal
    reanalysis to, instead of the whole rolling buffer -- see notation-
    and-feature-ideas.md's "Loop/section markers for review".

    A no-op (returns the marks unchanged) unless frozen -- same gating as
    scrollback/reanalysis themselves, since a live-scrolling tail has no
    stable "point in history" to mark -- or when `timestamp` is None (no
    entries pushed yet, nothing to mark). Order-independent: whichever
    mark's key is pressed just gets overwritten with the current
    timestamp; `_mark_range()` normalizes the pair into (lo, hi) only
    where the range is actually consumed, so pressing end-then-start
    works the same as start-then-end."""
    if not frozen or timestamp is None or key is None:
        return mark_start, mark_end
    if key.lower() == store.keybind("mark_range_start").lower():
        return timestamp, mark_end
    if key.lower() == store.keybind("mark_range_end").lower():
        return mark_start, timestamp
    return mark_start, mark_end


def _mark_range(mark_start, mark_end):
    """Returns a (lo, hi) tuple once both loop/section markers are set, or
    None otherwise (no marks, or only one end placed so far) -- the shape
    `_handle_reanalysis_key()`'s `mark_range=` param and the status line's
    mark hint both consume. Normalizes order since mark_range_start/
    mark_range_end can be pressed in either order relative to each other
    in time (see _handle_mark_keys)."""
    if mark_start is None or mark_end is None:
        return None
    return (min(mark_start, mark_end), max(mark_start, mark_end))


def _filter_hop_records_to_range(hop_records, mark_range, hop_seconds):
    """Restricts `hop_records` (see ReanalysisBuffer.snapshot()) to those
    whose real timestamp (`hop_index * hop_seconds`) falls within the
    inclusive `[lo, hi]` loop/section-marked range -- or returns
    `hop_records` unchanged when `mark_range` is None (no marks set, the
    R-key reanalysis's original whole-buffer scope). `rhythm_reanalysis.
    recompute()` already handles an empty list (returns None, the same
    "nothing to reanalyze" no-op its caller already treats as such), so a
    mark_range with no hops inside it is safe, not a crash."""
    if mark_range is None:
        return hop_records
    lo, hi = mark_range
    return [r for r in hop_records if lo <= r.hop_index * hop_seconds <= hi]


def _handle_reanalysis_key(key, frozen, reanalysis_state, reanalysis_buffer, result_queue, beats_per_bar,
                            hop_seconds, mark_range=None):
    """'tab' view only: R triggers the non-causal rhythm re-analysis
    (issue #77) -- a no-op unless the view is currently frozen, or a
    recompute is already running (reanalysis_state.in_progress guards
    against stacking up redundant recomputes on repeated presses).

    Spawns a throwaway thread rather than routing the recompute through
    the analysis thread: per docs/research/live-noncausal-rhythm-
    reanalysis.md's Q5, the analysis thread's own per-hop cadence must
    never stall on a recompute that can take up to ~1.3s at the largest
    configured window, and the render loop has nothing else to do while
    frozen anyway. `reanalysis_buffer.snapshot()` is read once, up front,
    on the render thread itself -- a plain deque copy is safe (if not
    perfectly point-in-time) against the analysis thread's concurrent
    appends under CPython's GIL; see ReanalysisBuffer's own docstring.
    The spawned thread then does the actual (slower) recompute work
    entirely off both the render and analysis threads, and hands its
    result back via `result_queue` (a single-slot queue.Queue, the same
    always-overwritten idiom this codebase already uses for the analysis
    -> render handoff) -- run_terminal_tab's main loop polls it
    non-blockingly once per iteration.

    `mark_range` (loop/section markers; see _mark_range()) optionally
    narrows the snapshot to just that `(lo, hi)` window via
    _filter_hop_records_to_range() before the recompute runs -- None (no
    marks set) reproduces the original whole-buffer scope exactly."""
    bound = store.keybind("rhythm_reanalysis")
    if key is None or key.lower() != bound.lower() or not frozen or reanalysis_state.in_progress:
        return
    reanalysis_state.in_progress = True
    hop_records = _filter_hop_records_to_range(reanalysis_buffer.snapshot(), mark_range, hop_seconds)

    def _worker():
        try:
            result = rhythm_reanalysis.recompute(hop_records, hop_seconds, beats_per_bar)
            _overwrite(result_queue, result)
        finally:
            reanalysis_state.in_progress = False

    threading.Thread(target=_worker, daemon=True).start()


def _apply_reanalysis_result(display, result, hop_seconds):
    """Applies one rhythm_reanalysis.RecomputeResult to the frozen
    TabDisplay -- called from run_terminal_tab's main loop once a pending
    recompute's result shows up on the reanalysis result queue. Corrected
    note durations always apply (they fall back to the same
    DEFAULT_DURATION_CLASS the live path already uses when no bpm was
    available, so applying them is never worse than what's already
    displayed). Barline reconciliation only happens when the recompute
    actually produced a bpm estimate -- with none, recompute() can't place
    any corrected barlines either (see its own docstring), and erasing the
    window's existing (live-estimated, imperfect but non-empty) barlines
    with nothing to replace them would be strictly worse than leaving them
    alone. `end_t` is nudged one hop_seconds past the window's last hop so
    a barline landing exactly at the final buffered hop is still erased."""
    for note in result.corrected_notes:
        display.correct_duration(note.pitch_class, note.octave, note.onset_time, note.duration_class)
    if result.bpm_estimate is not None:
        display.erase_barlines(result.window_start_time, result.window_end_time + hop_seconds)
        for t in result.barline_times:
            display.insert_barline(t)


def _fade_toward(value, target, dt, tau_ms):
    tau = max(tau_ms, 1) / 1000.0
    alpha = 1.0 - math.exp(-dt / tau)
    return value + (target - value) * alpha


def _animate_note_stack(animators, note_stack, dt):
    """note_stack is already sorted lowest-note-first by ChordSmoother --
    that's also bottom-to-top order for fill's proportional bands.
    Returns a list of animated RGB tuples in that same order, one per
    active note (or a single idle color if the stack is empty)."""
    if not note_stack:
        animators.clear()
        return [config.IDLE_RGB]

    active_keys = set()
    bands = []
    for entry in note_stack:
        key = (entry["pitch_class"], entry["octave"])
        active_keys.add(key)
        is_new = key not in animators
        anim = animators.setdefault(
            key, ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
        )
        bands.append(anim.update(dt, entry["rgb"], is_new))

    for stale_key in [k for k in animators if k not in active_keys]:
        del animators[stale_key]
    return bands


def run_terminal_fill(result_queue, sensitivity, capture, source_state, session_recorder):
    from terminal_display import TerminalDisplay

    display = TerminalDisplay(fps=config.TERMINAL_FPS)
    animator = ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
    band_animators = {}
    keys = RawKeys()
    chord_mode = False
    help_legend_on = True

    target_rgb, is_onset, label, freq, confidence, rms = config.IDLE_RGB, False, "-", 0.0, 0.0, 0.0
    note_stack, chord_name = [], None
    dt = 1.0 / display.fps

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            chord_mode = _handle_chord_mode_key(key, chord_mode)
            help_legend_on = _handle_help_legend_key(key, help_legend_on)
            _handle_session_record_key(key, session_recorder)
            if _handle_back_to_menu_key(key):
                return "menu"
            try:
                (target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, _pitch_class, _octave, note_stack, chord_name,
                 _duration_hops, _bpm_estimate) = result_queue.get_nowait()
            except queue.Empty:
                is_onset = False

            mode_hint = f"mode={'chord' if chord_mode else 'note'}({_key_hint('chord_mode_toggle')})  legend(h)"
            rec_hint = f"rec={'ON' if session_recorder.armed else 'off'}({_key_hint('session_record_toggle')})"
            legend = _legend_line(["up/down=sensitivity", f"{_key_hint('source_toggle')}=source",
                                    f"{_key_hint('chord_mode_toggle')}=mode",
                                    f"{_key_hint('session_record_toggle')}=record"]) if help_legend_on else ""
            if chord_mode:
                bands = _animate_note_stack(band_animators, note_stack, dt)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state,
                                        chord_name=chord_name, chord_mode=True)
                          + f"  {mode_hint}  {rec_hint}  (Ctrl+C to quit)")
                display.render_bands(bands, status, legend)
            else:
                rgb = animator.update(dt, target_rgb, is_onset)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state)
                          + f"  {mode_hint}  {rec_hint}  (Ctrl+C to quit)")
                display.render(rgb, status, legend)
            time.sleep(dt)
    except KeyboardInterrupt:
        return "quit"
    finally:
        keys.restore()
        display.quit()


def run_terminal_wheel(result_queue, sensitivity, capture, source_state, session_recorder):
    from terminal_wheel_display import WheelDisplay

    display = WheelDisplay(fps=config.WHEEL_FPS)
    pulse_decay = config.PULSE_DECAY_MS / 1000.0
    dt = 1.0 / display.fps
    keys = RawKeys()
    chord_mode = False
    help_legend_on = True
    wedge_fades = [0.0] * 12

    active_index = None
    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pulse = 0.0
    note_stack, chord_name = [], None

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            chord_mode = _handle_chord_mode_key(key, chord_mode)
            help_legend_on = _handle_help_legend_key(key, help_legend_on)
            _handle_session_record_key(key, session_recorder)
            if _handle_back_to_menu_key(key):
                return "menu"
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 active_index, _pitch_class, _octave, note_stack, chord_name,
                 _duration_hops, _bpm_estimate) = result_queue.get_nowait()
            except queue.Empty:
                pass

            mode_hint = f"mode={'chord' if chord_mode else 'note'}({_key_hint('chord_mode_toggle')})  legend(h)"
            rec_hint = f"rec={'ON' if session_recorder.armed else 'off'}({_key_hint('session_record_toggle')})"
            legend = _legend_line(["up/down=sensitivity", f"{_key_hint('source_toggle')}=source",
                                    f"{_key_hint('chord_mode_toggle')}=mode",
                                    f"{_key_hint('session_record_toggle')}=record"]) if help_legend_on else ""
            if chord_mode:
                active_pcs = {e["pitch_class"] for e in note_stack}
                bass_pc = next((e["pitch_class"] for e in note_stack if e["is_bass"]), None)
                for pc in range(12):
                    target = 1.0 if pc in active_pcs else 0.0
                    wedge_fades[pc] = _fade_toward(wedge_fades[pc], target, dt, config.CROSSFADE_TAU_MS)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state,
                                        chord_name=chord_name, chord_mode=True)
                          + f"  {mode_hint}  {rec_hint}  (Ctrl+C to quit)")
                display.render_chord(wedge_fades, bass_pc, status, legend)
            else:
                pulse = 1.0 if is_onset else pulse * math.exp(-dt / pulse_decay)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state)
                          + f"  {mode_hint}  {rec_hint}  (Ctrl+C to quit)")
                display.render(active_index, pulse, status, legend)
            time.sleep(dt)
    except KeyboardInterrupt:
        return "quit"
    finally:
        keys.restore()
        display.quit()


def _tab_note_rgb(pitch_class):
    """A note's tab-view glyph color. Always uses the fifths hue mapping,
    same as the wheel view (independent of --color-scheme, for the same
    reason the wheel is: this is a fixed note-identity color, not a
    representation of the currently-selected scheme), so a note reads as
    the same color in `tab` as it does in `wheel` -- e.g. B is green in
    both, not pink in one and green in the other. Uses a fixed lightness
    (config.TAB_NOTE_LIGHTNESS) instead of scaling by octave, unlike
    fill/GUI: octave already drives the note's row on the staff, and
    0.5 is where a given hue/saturation looks most vivid/saturated in
    HSL, rather than washing out toward white like a high lightness does."""
    if pitch_class is None:
        return config.IDLE_RGB
    hue, sat, _light = note_to_hsl(pitch_class, config.MAX_OCTAVE, scheme="fifths",
                                    hue_override=store.note_hue_override(pitch_class))
    return hsl_to_rgb255(hue, sat, config.TAB_NOTE_LIGHTNESS)


def _tab_note_label(pitch_class, octave):
    """Same fifths spelling as the wheel view (e.g. Ab, not G#), for the
    same reason as _tab_note_rgb: a note should read identically in `tab`
    as it does in `wheel`, independent of --color-scheme."""
    if pitch_class is None:
        return "-"
    return f"{NOTE_NAMES_FIFTHS[pitch_class]}{octave}"


def _hop_beats(beats_values):
    """The number of beats to credit toward `beats_accumulated` for one
    hop, taking the max across every note-duration finalization this hop
    rather than summing them (issue #76). The mono and chord/multipitch
    DurationTrackers both always run every hop (this codebase's
    always-on-pipeline convention) and routinely finalize the *same*
    underlying acoustic note independently -- e.g. an ordinary single note
    is tracked by both the mono smoother and multipitch's one-note
    "chord". Summing both trackers' contributions into `beats_accumulated`
    double-counted that shared note, roughly halving real barline spacing;
    taking the max instead mirrors run_batch_transcribe()'s already-correct
    per-onset `max()` over simultaneous notes at one column -- the beat
    position should advance once per hop's worth of music, not once per
    tracker that happened to notice it. `beats_values` is the list of
    `beats` values computed for whatever notes finalized this hop (mono's,
    if any, plus one per note_stack entry); an entry may itself be `None`
    (bpm_estimate was unknown at finalization time), treated as 0.0."""
    hop_beats = 0.0
    for beats in beats_values:
        hop_beats = max(hop_beats, beats or 0.0)
    return hop_beats


def run_terminal_tab(result_queue, scroll_mode, dump_file, sensitivity, capture, source_state,
                      reanalysis_buffer, session_recorder, time_signature=config.DEFAULT_TIME_SIGNATURE):
    from terminal_tab_display import TabDisplay

    display = TabDisplay(fps=config.TAB_FPS, scrollback_seconds=store.preference(
        "tab_scrollback_seconds", config.TAB_SCROLLBACK_SECONDS
    ))
    dt = 1.0 / display.fps
    fix_interval = 1.0 / config.TAB_FIX_HOPS_PER_SEC
    time_since_tick = 0.0
    keys = RawKeys()
    # tab opens polyphonic by default (issue #13's standing decision) --
    # flipped from fill/wheel, where chord_mode starts False and P opts
    # *up*. Here P still just flips the boolean (_handle_chord_mode_key
    # is direction-agnostic); only the starting value differs.
    chord_mode = True
    prev_chord_name = None
    notehead_style = config.TAB_DEFAULT_NOTEHEAD_STYLE
    legend_on = config.TAB_DEFAULT_LEGEND_ON
    frozen = False
    help_legend_on = True
    # Issue #77: R-key non-causal rhythm re-analysis + Left/Right scrollback,
    # both freeze-mode-only. reanalysis_state/reanalysis_result_queue are
    # this function's own, local to one run_terminal_tab call (unlike
    # reanalysis_buffer, which outlives it on SessionState) -- a fresh pair
    # every time 'tab' is entered is correct, there's nothing to preserve
    # across a '|' back-to-menu round trip the way the buffer itself is.
    reanalysis_state = ReanalysisState()
    reanalysis_result_queue = queue.Queue(maxsize=1)
    scroll_offset = 0
    # Corrected tempo from the most recent successful reanalysis, shown in
    # place of the live bpm_estimate once available -- see the tempo_str
    # computation below. Reset to None on unfreeze, same "no catch-up"
    # convention scroll_offset follows.
    reanalysis_bpm_estimate = None
    # Loop/section markers (notation-and-feature-ideas.md's Feature 6):
    # timestamps, not scroll-offset counts, so they stay meaningful even
    # as scroll_offset itself changes across further Left/Right presses.
    # None/None means no range is marked; reset on unfreeze, same "no
    # catch-up" convention every other frozen-only piece of state here
    # follows (scroll_offset, reanalysis_bpm_estimate above).
    mark_start, mark_end = None, None

    # time_signature arrives pre-validated as an (int, int) tuple from the
    # CLI layer (main._parse_time_signature / virtualnote.py), not a
    # string, so no parsing needed here. A "beat" throughout this codebase's
    # duration math (duration_tracker._DURATION_CLASSES) is a quarter
    # note -- beats_per_bar converts the time signature's own beat unit
    # into quarter-note-beats per bar.
    beats_numerator, beats_denominator = time_signature
    beats_per_bar = beats_numerator * (4.0 / beats_denominator)
    beats_accumulated = 0.0
    hop_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE

    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pitch_class, octave = None, None
    note_stack, chord_name = [], None
    bpm_estimate = None

    resolved_dump = dump_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
    )

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            chord_mode = _handle_chord_mode_key(key, chord_mode)
            notehead_style = _handle_notehead_style_key(key, notehead_style)
            legend_on = _handle_legend_key(key, legend_on)
            was_frozen = frozen
            frozen = _handle_freeze_key(key, frozen)
            if was_frozen and not frozen:
                # Un-freezing resumes live immediately -- no catch-up of
                # anything that happened while frozen, same convention
                # Space itself already follows (see _handle_freeze_key).
                # A stale scroll position or a stale corrected-tempo
                # display would both be exactly that kind of catch-up.
                scroll_offset = 0
                reanalysis_bpm_estimate = None
                mark_start, mark_end = None, None
            scroll_offset = _handle_scroll_keys(key, frozen, scroll_offset, len(display.entries) - 1)
            mark_start, mark_end = _handle_mark_keys(
                key, frozen, mark_start, mark_end, display.timestamp_at_offset(scroll_offset)
            )
            _handle_reanalysis_key(key, frozen, reanalysis_state, reanalysis_buffer, reanalysis_result_queue,
                                    beats_per_bar, hop_seconds, mark_range=_mark_range(mark_start, mark_end))
            help_legend_on = _handle_help_legend_key(key, help_legend_on)
            _handle_session_record_key(key, session_recorder)
            if _handle_back_to_menu_key(key):
                return "menu"

            try:
                reanalysis_result = reanalysis_result_queue.get_nowait()
            except queue.Empty:
                reanalysis_result = None
            if reanalysis_result is not None:
                _apply_reanalysis_result(display, reanalysis_result, hop_seconds)
                if reanalysis_result.bpm_estimate is not None:
                    reanalysis_bpm_estimate = reanalysis_result.bpm_estimate

            got_new = False
            is_onset = False
            # Frozen: don't drain result_queue at all, so the view keeps
            # showing its last-known state and no new column can be
            # pushed below -- the analysis thread keeps overwriting the
            # single-slot queue in the background regardless (issue #23).
            if not frozen:
                # The note that was displayed *last* hop -- this is the key
                # duration_hops (if set this hop) actually belongs to, since
                # DurationTracker was fed exactly this smoothed pitch_class/
                # octave sequence one hop behind what's about to be
                # displayed now.
                prev_pitch_class, prev_octave = pitch_class, octave
                try:
                    (_target_rgb, is_onset, label, freq, confidence, rms,
                     _fifths_idx, pitch_class, octave, note_stack, chord_name,
                     duration_hops, bpm_estimate) = result_queue.get_nowait()
                    got_new = True
                except queue.Empty:
                    pass

                if got_new:
                    # The mono and chord/multipitch trackers both always run
                    # (this codebase's always-on-pipeline convention) and
                    # routinely finalize the *same* underlying note in the
                    # same hop -- e.g. any ordinary single note is tracked by
                    # both the mono smoother and multipitch's one-note
                    # "chord". Summing both trackers' beats into
                    # beats_accumulated double-counted that shared note,
                    # roughly halving real barline spacing (issue #76).
                    # `_hop_beats()` takes the max across every finalization
                    # this hop instead, mirroring run_batch_transcribe()'s
                    # per-onset `max()` over simultaneous notes -- the beat
                    # position should advance once per hop's worth of
                    # music, not once per tracker that happened to notice it.
                    hop_beats_values = []

                    # Monophonic duration finalization belongs to the note
                    # displayed *before* this hop's update (see above).
                    if duration_hops is not None and prev_pitch_class is not None:
                        beats = (duration_hops * hop_seconds * bpm_estimate / 60.0) if bpm_estimate else None
                        dclass = duration_class_for_beats(beats)
                        display.finalize_duration(prev_pitch_class, prev_octave, dclass)
                        hop_beats_values.append(beats)

                    # Chord-mode duration tracking runs every hop regardless
                    # of the current chord_mode display toggle -- same
                    # always-on-pipeline convention as chroma/multipitch
                    # elsewhere in this codebase.
                    for entry in note_stack:
                        if entry["duration_hops"] is None:
                            continue
                        beats = (
                            entry["duration_hops"] * hop_seconds * bpm_estimate / 60.0
                        ) if bpm_estimate else None
                        dclass = duration_class_for_beats(beats)
                        display.finalize_duration(entry["pitch_class"], entry["octave"], dclass)
                        hop_beats_values.append(beats)

                    beats_accumulated += _hop_beats(hop_beats_values)

                    # A while, not an if, so a hop that somehow crosses more
                    # than one bar boundary (e.g. after a long freeze)
                    # doesn't lose barlines; keeping the remainder rather
                    # than zeroing avoids compounding drift.
                    while beats_accumulated >= beats_per_bar:
                        display.push_barline()
                        beats_accumulated -= beats_per_bar

            # A completed reanalysis's corrected tempo takes over the
            # display until the next unfreeze -- while frozen, the live
            # bpm_estimate isn't advancing anyway (result_queue isn't
            # being drained), so there's no "which is fresher" ambiguity.
            display_bpm = reanalysis_bpm_estimate if reanalysis_bpm_estimate is not None else bpm_estimate
            tempo_str = f"{display_bpm:.0f}" if display_bpm else "--"
            time_str = f"{beats_numerator}/{beats_denominator}"

            reanalysis_hint = ""
            if reanalysis_state.in_progress:
                reanalysis_hint = "  rhythm=recomputing..."
            elif scroll_offset:
                reanalysis_hint = f"  scrollback=-{scroll_offset}"

            marked_range = _mark_range(mark_start, mark_end)
            if marked_range is not None:
                reanalysis_hint += f"  mark=[{marked_range[0]:.2f}s,{marked_range[1]:.2f}s]"
            elif mark_start is not None:
                reanalysis_hint += f"  mark=[{mark_start:.2f}s,...]"
            elif mark_end is not None:
                reanalysis_hint += f"  mark=[...,{mark_end:.2f}s]"

            mode_hint = (f"mode={'chord' if chord_mode else 'note'}({_key_hint('chord_mode_toggle')})  "
                         f"notes={notehead_style}({_key_hint('notehead_style_toggle')})  "
                         f"legend={'on' if legend_on else 'off'}({_key_hint('legend_toggle')})  "
                         f"frozen={'on' if frozen else 'off'}({_key_hint('freeze_toggle')})  "
                         f"rec={'ON' if session_recorder.armed else 'off'}({_key_hint('session_record_toggle')})  "
                         f"helplegend(h)")
            help_legend = _legend_line([
                "up/down=sensitivity", f"{_key_hint('source_toggle')}=source",
                f"{_key_hint('chord_mode_toggle')}=mode", f"{_key_hint('notehead_style_toggle')}=notes",
                f"{_key_hint('legend_toggle')}=stafflegend", f"{_key_hint('freeze_toggle')}=freeze",
                f"{_key_hint('rhythm_reanalysis')}=reanalyze(frozen)", "left/right=scrollback(frozen)",
                f"{_key_hint('mark_range_start')}/{_key_hint('mark_range_end')}=mark range(frozen)",
                f"{_key_hint('session_record_toggle')}=record",
            ]) if help_legend_on else ""
            if chord_mode:
                notes = [
                    (e["pitch_class"], e["octave"], _tab_note_rgb(e["pitch_class"]),
                     _tab_note_label(e["pitch_class"], e["octave"]))
                    for e in note_stack
                ]
                # Chord-level onset (the recognized chord identity changing),
                # not per-note re-attack -- a strummed/arpeggiated chord
                # shouldn't spam a new column per note.
                is_chord_onset = got_new and chord_name != prev_chord_name
                if got_new:
                    prev_chord_name = chord_name

                if not frozen:
                    if scroll_mode == "onset":
                        if is_chord_onset:
                            display.push_notes(notes, chord_name)
                    else:  # "fix"
                        time_since_tick += dt
                        if time_since_tick >= fix_interval:
                            time_since_tick -= fix_interval
                            display.push_notes(notes, chord_name)

                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state,
                                        chord_name=chord_name, chord_mode=True)
                          + f"  tempo={tempo_str}  time={time_str}  {mode_hint}{reanalysis_hint}"
                            f"  [{scroll_mode}] (Ctrl+C to quit)")
                display.render(status, chord_mode=True, notehead_style=notehead_style, legend_on=legend_on,
                                frozen=frozen, help_legend=help_legend, scroll_offset=scroll_offset)
            else:
                glyph_rgb = _tab_note_rgb(pitch_class)
                tab_label = _tab_note_label(pitch_class, octave)

                if not frozen:
                    if scroll_mode == "onset":
                        if got_new and is_onset:
                            display.push(pitch_class, octave, glyph_rgb, tab_label)
                    else:  # "fix"
                        time_since_tick += dt
                        if time_since_tick >= fix_interval:
                            time_since_tick -= fix_interval
                            display.push(pitch_class, octave, glyph_rgb, tab_label)

                status = (_status_text(tab_label, freq, confidence, rms, sensitivity, source_state)
                          + f"  tempo={tempo_str}  time={time_str}  {mode_hint}{reanalysis_hint}"
                            f"  [{scroll_mode}] (Ctrl+C to quit)")
                display.render(status, chord_mode=False, notehead_style=notehead_style, legend_on=legend_on,
                                frozen=frozen, help_legend=help_legend, scroll_offset=scroll_offset)
            time.sleep(dt)
    except KeyboardInterrupt:
        return "quit"
    finally:
        keys.restore()
        try:
            display.dump_ansi(resolved_dump)
        finally:
            display.quit()


def run_gui(result_queue, fullscreen, start_debug, sensitivity):
    import pygame
    from display import Display

    display = Display(config.WINDOW_SIZE_PX, fullscreen=fullscreen, fps=config.FPS)
    animator = ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
    font = pygame.font.SysFont("monospace", 18)

    show_debug = start_debug
    help_legend_on = True
    back_to_menu = False
    target_rgb, is_onset, label, freq, confidence, rms = config.IDLE_RGB, False, "-", 0.0, 0.0, 0.0
    dt = 1.0 / config.FPS

    try:
        while display.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    display.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        display.running = False
                    elif event.key == pygame.K_f:
                        display.toggle_fullscreen()
                    elif event.key == pygame.K_d:
                        show_debug = not show_debug
                    elif event.key == pygame.K_h:
                        help_legend_on = not help_legend_on
                    elif event.key == pygame.K_BACKSLASH:
                        # Unshifted key for '|' -- pygame reports the shifted
                        # '|' character via this same physical keycode plus a
                        # shift modifier, not a keycode of its own, so this is
                        # the GUI's equivalent of the terminal views'
                        # _handle_back_to_menu_key (issue #40). Same tier as
                        # Esc: stop the event loop, but signal *why* via
                        # back_to_menu so the caller (run_session/shell.py)
                        # can return to the menu instead of tearing down.
                        display.running = False
                        back_to_menu = True
                    elif event.key == pygame.K_DOWN:
                        sensitivity.adjust(1.0 / SENSITIVITY_STEP)
                    elif event.key == pygame.K_UP:
                        sensitivity.adjust(SENSITIVITY_STEP)
            if not display.running:
                break

            try:
                (target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, _pitch_class, _octave, _note_stack, _chord_name,
                 _duration_hops, _bpm_estimate) = result_queue.get_nowait()
            except queue.Empty:
                is_onset = False

            rgb = animator.update(dt, target_rgb, is_onset)
            display.screen.fill(rgb)
            if show_debug:
                text = font.render(_status_text(label, freq, confidence, rms, sensitivity) + "  legend(h)",
                                    True, (255, 255, 255))
                display.screen.blit(text, (10, 10))
                if help_legend_on:
                    legend = font.render(
                        _legend_line(["esc=quit", "f=fullscreen", "d=debug", "up/down=sensitivity"]),
                        True, (255, 255, 255))
                    display.screen.blit(legend, (10, 32))
            pygame.display.flip()
            dt = display.clock.tick(display.fps) / 1000.0
    finally:
        display.quit()
    return "menu" if back_to_menu else "quit"


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
        if self.capture is None:
            return
        self.stop_event.set()
        self.capture.stop()


def run_session(view, scroll_mode, dump_file, fullscreen, debug, session,
                 time_signature=config.DEFAULT_TIME_SIGNATURE):
    """Dispatches to the right run_* function for `view` ('fill', 'wheel',
    'tab', or 'gui'), starting `session`'s capture/analysis thread first if
    this is the first tool entered this process. Returns whatever the
    run_* function returns: "quit" (Ctrl+C / window-close-or-Esc) or
    "menu" (the '|' / backslash back-to-menu keybind) -- the caller (either
    main(), which has no menu to return to, or shell.py's menu loop, which
    does) decides what to do with that sentinel. This is the extracted
    body of what used to be main()'s single-shot try/finally, made
    reusable so shell.py's menu loop can call it repeatedly against the
    same session (issue #40). `time_signature` is 'tab'-view-only (issue
    #55's barline placement) -- every other view ignores it."""
    session.ensure_started()
    if view == "gui":
        return run_gui(session.result_queue, fullscreen, debug, session.sensitivity)
    if view == "wheel":
        return run_terminal_wheel(session.result_queue, session.sensitivity, session.capture, session.source_state,
                                   session.session_recorder)
    if view == "tab":
        return run_terminal_tab(session.result_queue, scroll_mode, dump_file, session.sensitivity,
                                 session.capture, session.source_state, session.reanalysis_buffer,
                                 session.session_recorder, time_signature=time_signature)
    return run_terminal_fill(session.result_queue, session.sensitivity, session.capture, session.source_state,
                              session.session_recorder)


def run_batch_transcribe(file_path, time_signature, dump_file, write_score_path=None, export_abc_path=None,
                          play=False):
    """Offline transcription entry point (issue #55, `virtualnote
    transcribe`): loads `file_path`, runs batch_transcribe.transcribe()
    over the whole array, then builds TabDisplay columns from the result
    and dumps them via dump_ansi() -- no live render loop, no terminal
    interactivity, .render() is never called (a real TabDisplay is still
    constructed, reusing its column-building/dump_ansi() logic, which is
    what's actually needed here; its constructor's stray `\\033[?25l\\033[2J`
    terminal-control escape codes on stdout are harmless and not worth
    suppressing for a one-shot batch run).

    `write_score_path` (issue #65's CLI wiring) is `None` by default --
    no score is written, and `score_writer` (which imports `music21`) is
    never even imported, mirroring how `pygame` only gets imported inside
    `run_gui`. Passed as `""` (virtualnote.py's `--write-score` bare-flag
    sentinel, its `nargs="?"`/`const=""`) it resolves to a default path
    next to `main.py`, same `note_history_<timestamp>.txt`-style pattern
    `resolved_dump_path` below already uses but with a `score_` prefix and
    `.musicxml` extension; passed any other (truthy) string, that string
    is used verbatim as the output path. `result` -- the same
    `batch_transcribe.TranscriptionResult` already computed above for the
    `TabDisplay` columns -- is reused as-is; `score_writer.write_score()`
    consumes it directly, no recomputation. `export_abc_path` (the ABC
    export feature) follows the exact same `None`/`""`/explicit-path
    convention as `write_score_path`, defaulting to
    `transcription_<timestamp>.abc` next to `main.py` -- `abc_export.py`
    is imported locally the same way, and reuses this same `result`
    object via `abc_export.from_transcription_result()`. `play` (map #24's
    playback engine) triggers an offline pre-rendered playback of `result`
    once every other export has already run -- `result` already holds the
    whole transcription, so there's nothing left to schedule incrementally
    against, unlike `run_replay_session()`'s live-scheduled `play` below.

    Column-building choice: batch_transcribe.transcribe()'s polyphonic
    `notes` list (each NoteEvent already carries a resolved chord_name at
    its own onset) is grouped by onset_hop -- every NoteEvent sharing the
    same onset_hop becomes one push_notes() column (a single note is just
    a one-note "chord" here, so push_notes() covers both solo notes and
    real chords uniformly -- TabDisplay.push()/.push_notes() both just
    build a TabEntry internally, see terminal_tab_display.py, so
    dump_ansi()'s output is identical either way). Barlines are pushed by
    accumulating each column's beats -- the *longest* of its simultaneous
    notes' durations, in whichever unit result.bpm resolves beats to --
    against the same beats_per_bar formula run_terminal_tab() uses, walked
    in onset order across the whole file."""
    from terminal_tab_display import TabDisplay

    audio = batch_transcribe.load_audio(file_path)
    result = batch_transcribe.transcribe(audio, config.SAMPLE_RATE, time_signature=time_signature)

    beats_numerator, beats_denominator = time_signature
    beats_per_bar = beats_numerator * (4.0 / beats_denominator)

    display = TabDisplay(fps=config.TAB_FPS)

    by_hop = {}
    for note in result.notes:
        by_hop.setdefault(note.onset_hop, []).append(note)

    beats_accumulated = 0.0
    for onset_hop in sorted(by_hop):
        notes_here = by_hop[onset_hop]
        onset_time = onset_hop * result.hop_seconds
        chord_name = next((n.chord_name for n in notes_here if n.chord_name), None)
        push_tuples = [
            (n.pitch_class, n.octave, _tab_note_rgb(n.pitch_class), _tab_note_label(n.pitch_class, n.octave))
            for n in notes_here
        ]
        # `t=onset_time`: without this, TabDisplay stamps every column with
        # wall-clock time-since-construction, which is meaningless here --
        # a batch sweep pushes every column within milliseconds of real
        # time regardless of where the notes actually fall in the
        # recording (dump_ansi()'s "t" column would otherwise read ~0.00s
        # for the whole file).
        display.push_notes(push_tuples, chord_name, t=onset_time)

        column_beats = 0.0
        for n in notes_here:
            note_beats = (n.duration_hops * result.hop_seconds * result.bpm / 60.0) if result.bpm else None
            dclass = duration_class_for_beats(note_beats)
            display.finalize_duration(n.pitch_class, n.octave, dclass)
            column_beats = max(column_beats, note_beats or 0.0)

        beats_accumulated += column_beats
        while beats_accumulated >= beats_per_bar:
            # A barline crossed here belongs at (approximately) this
            # column's onset time -- the same approximation the live path
            # already accepts for barline placement (issue #55/#53).
            display.push_barline(t=onset_time)
            beats_accumulated -= beats_per_bar

    resolved_dump_path = dump_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
    )
    display.dump_ansi(resolved_dump_path)

    if write_score_path is not None:
        # Local import -- keeps music21's import cost (real, one-time, and
        # of no use to the live/Pi-constrained path) off every `transcribe`
        # run, paid only when --write-score is actually passed. Mirrors
        # this file's existing `pygame`-only-inside-`run_gui` convention.
        import score_writer

        resolved_write_score_path = write_score_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"score_{time.strftime('%Y%m%d_%H%M%S')}.musicxml",
        )
        score_writer.write_score(result, resolved_write_score_path, time_signature=time_signature)

    if export_abc_path is not None:
        # Local import mirrors write_score_path's own pattern above, though
        # abc_export has no heavy/deferred dependency of its own (no
        # music21 -- see that module's docstring) -- kept local anyway for
        # symmetry with the sibling export path and to avoid paying even
        # abc_export's own import cost on a `transcribe` run that never
        # asked for ABC output.
        import abc_export

        resolved_export_abc_path = export_abc_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"transcription_{time.strftime('%Y%m%d_%H%M%S')}.abc",
        )
        columns = abc_export.from_transcription_result(result, time_signature=time_signature)
        abc_export.write_abc(columns, resolved_export_abc_path, time_signature=time_signature)

    if play:
        # Local import, same "pay the cost only when the feature is used"
        # convention as score_writer/pygame above -- though playback.py's
        # own import cost is negligible (sounddevice is already loaded via
        # audio_capture.py in every other code path; this just avoids
        # opening an OutputStream device for a one-shot batch run that
        # never asked for one). Offline pre-render (playback.py's module
        # docstring) is the right mode here -- `result` already holds the
        # whole transcription, nothing left to schedule against.
        import playback

        notes = [
            (n.onset_hop * result.hop_seconds, n.pitch_class, n.octave, n.duration_hops * result.hop_seconds)
            for n in result.notes
        ]
        playback.play_offline(notes)


def run_replay_session(file_path, dump_file, speed=1.0, play=False):
    """`virtualnote replay <file>` (issue: session recording + playback,
    feature idea 1 in docs/research/notation-and-feature-ideas.md): reads
    a `.jsonl` session log written by `session_recorder.SessionRecorder`
    and re-drives a real `TabDisplay` from its recorded events instead of
    live audio -- the JSONL-log-shaped sibling of run_batch_transcribe()
    above (same "build TabDisplay columns from already-detected note
    events" shape, just from a session log's flat event stream instead of
    a batch_transcribe.TranscriptionResult). No SessionState/audio is
    touched at all, mirroring how 'transcribe' bypasses it too (see
    virtualnote.py's main()).

    Unlike batch transcription (a silent sweep with no interactive
    render), replay renders live -- `time.sleep()` between columns paced
    by their real recorded timestamp gaps (divided by `speed`, so 2.0
    replays twice as fast) reproduces the original session's actual
    pacing on screen, the same "watch what I actually played" value this
    feature exists for. `session_player.load_events()`/`group_columns()`
    do the pure reading/grouping (unit-tested there); this function owns
    only the TabDisplay-driving/timing side effects, same "pure logic
    unit-tested, real I/O smoke-tested" split as
    rhythm_reanalysis.recompute() vs. main.py's own `R`-key wiring.

    Ctrl+C stops the replay early (same as every other terminal view) --
    still dumps via TabDisplay.dump_ansi() on the way out, covering
    whatever was replayed up to that point, not just a full run.

    `play=True` (map #24's playback engine) triggers each column's
    note(s) through a `playback.LiveScheduler` the instant that column is
    pushed on screen, reusing this loop's own already-paced `time.sleep()`
    clock rather than running a second, independent one -- see
    playback.py's module docstring for why live scheduling (not offline
    pre-render) is the right mode for this specific caller. Each event's
    own recorded `duration_seconds` is divided by `speed` so the audio
    speeds up/slows down in lockstep with the visual pacing above, not
    just the gaps between notes."""
    from terminal_tab_display import TabDisplay

    events = load_events(file_path)
    columns = group_columns(events)

    scheduler = None
    if play:
        import playback

        scheduler = playback.LiveScheduler()
        scheduler.start()

    display = TabDisplay(fps=config.TAB_FPS)
    last_t = 0.0
    try:
        for kind, t, group in columns:
            gap = (t - last_t) / max(speed, 1e-6)
            if gap > 0:
                time.sleep(gap)
            last_t = t
            if kind == "barline":
                display.push_barline(t=t)
            else:
                push_tuples = [
                    (event["pc"], event["octave"], _tab_note_rgb(event["pc"]),
                     _tab_note_label(event["pc"], event["octave"]))
                    for event in group
                ]
                chord_name = next((event.get("chord_name") for event in group if event.get("chord_name")), None)
                display.push_notes(push_tuples, chord_name, t=t)
                for event in group:
                    display.finalize_duration(event["pc"], event["octave"], event["duration_class"])
                    if scheduler is not None:
                        scheduler.trigger_note(
                            event["pc"], event["octave"], event["duration_seconds"] / max(speed, 1e-6)
                        )
            status = f"virtualnote replay  file={os.path.basename(file_path)}  t={t:.2f}s  speed={speed}x"
            display.render(status, chord_mode=True)
    except KeyboardInterrupt:
        pass
    finally:
        if scheduler is not None:
            scheduler.stop()
        resolved_dump_path = dump_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        display.dump_ansi(resolved_dump_path)


def main():
    parser = argparse.ArgumentParser(description="Real-time audio-to-color display")
    parser.add_argument("--fullscreen", action="store_true", help="GUI mode: start fullscreen")
    parser.add_argument("--debug", action="store_true", help="GUI mode: show debug overlay on start")
    parser.add_argument("--terminal", action="store_true", help="run in the terminal instead of a GUI window")
    parser.add_argument("--view", choices=["fill", "wheel", "tab"], default="fill",
                         help="terminal mode only: 'fill' (solid color), 'wheel' (circle-of-fifths diagram), "
                              "or 'tab' (scrolling grand-staff note history)")
    parser.add_argument("--color-scheme", choices=["chromatic", "fifths"], default=config.DEFAULT_COLOR_SCHEME,
                         help="hue mapping for the fill/GUI views (wheel and tab views always use "
                              "the fifths layout)")
    parser.add_argument("--scroll", choices=["fix", "onset"], default=config.DEFAULT_SCROLL_MODE,
                         help="'tab' view only: 'fix' pushes a new column every tick; "
                              "'onset' pushes one only on a new note-attack")
    parser.add_argument("--dump-file", default=None,
                         help="'tab' view only: path for the ANSI session note-history dump written on quit "
                              "(default: note_history_<timestamp>.txt next to main.py)")
    parser.add_argument("--sensitivity", type=_positive_float, default=config.DEFAULT_SENSITIVITY,
                         help="pitch-detection sensitivity multiplier (default 1.0); higher registers "
                              "quieter/softer playing more readily. Adjustable live with Up/Down in any mode.")
    parser.add_argument("--source", choices=["mic", "loopback"], default="mic",
                         help="'mic' (default) listens to the microphone; 'loopback' listens to the "
                              "computer's own audio output instead (PipeWire/PulseAudio on Linux only), "
                              "for testing without playing anything out loud")
    parser.add_argument("--time-signature", type=_parse_time_signature, default=config.DEFAULT_TIME_SIGNATURE,
                         help="'tab' view only: N/D time signature for barline placement (default 4/4)")
    args = parser.parse_args()

    session = SessionState(args.color_scheme, args.sensitivity, args.source)
    try:
        session.ensure_started()
    except RuntimeError as exc:
        parser.error(str(exc))

    view = args.view if args.terminal else "gui"
    try:
        # The return value ("quit" or "menu") is intentionally ignored:
        # standalone `main.py` has no menu to fall back to, so a "menu"
        # sentinel (the user pressed '|'/backslash) is treated the same as
        # "quit" -- just exit cleanly either way. `virtualnote.py` is what
        # actually gives '|' somewhere to return to (see shell.py); H still
        # works here too, harmlessly, since it's pure render-thread-local
        # state with nothing shell-specific about it.
        run_session(view, args.scroll, args.dump_file, args.fullscreen, args.debug, session,
                     time_signature=args.time_signature)
    finally:
        session.stop()


if __name__ == "__main__":
    main()
