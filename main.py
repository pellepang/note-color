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
import kitty_keys
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
from staff_map import staff_row

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


class PlaybackState:
    """Frozen-buffer playback's render-thread/worker-thread handshake (map
    #99, ticket #121, decision #109) -- the same shape as ReanalysisState
    above, for the same reason: the render loop needs one flag to show in
    the status line and to tell a second Enter press "stop" rather than
    "start again", and the worker needs one flag to know it has been
    asked to stop. Plain attribute access, safe under CPython's GIL
    (`threading.Event` for `stop` only because the worker *waits* on it,
    which an attribute can't do).

    `note_count` is what was scheduled, kept purely for the status line
    -- "playing N notes" is the one piece of feedback that tells a user
    the marked range they set actually covers what they thought it
    did."""

    def __init__(self):
        self.in_progress = False
        self.stop = threading.Event()
        self.note_count = 0
        self.unavailable = None


_ARROW_BY_FINAL_BYTE = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}

# The final byte(s) that mean "this was a Shift-modified arrow" once a CSI
# sequence's parameter bytes are known -- xterm's modifier encoding puts a
# ";2" second parameter (shift=2, out of a small fixed vocabulary of
# modifier codes: 2=shift, 3=alt, 4=shift+alt, 5=ctrl, ...) after the
# always-"1" first parameter for a *modified* arrow (an unmodified arrow
# sends no parameter bytes at all -- see _parse_csi_params()'s docstring).
_SHIFT_ARROW_FINAL_BYTES = {"A": "SHIFT_UP", "B": "SHIFT_DOWN"}


def _parse_csi_params(param_bytes, final_byte):
    """Pure: interprets one CSI arrow sequence's parameter bytes (whatever
    ASCII digits/semicolons `RawKeys.poll()` read between 'ESC [' and the
    final letter) plus that final letter, into the token `poll()` should
    return. `param_bytes == ""` is a bare, unmodified arrow burst (`ESC [
    <letter>`, this app's original and by far most common case) -- maps
    straight through `_ARROW_BY_FINAL_BYTE`, unchanged from before this
    function existed. `param_bytes == "1;2"` is the standard xterm
    encoding for a Shift-held arrow (`ESC [ 1 ; 2 <letter>`) -- 'A'/'B'
    (Up/Down) map to `"SHIFT_UP"`/`"SHIFT_DOWN"`, the two tokens
    Shift+Up/Down transpose (issue #98 follow-up) needs; Shift+Left/Right
    aren't consumed by anything in this app yet, so they -- and every
    *other* modifier code (Alt, Ctrl, combinations) -- fall back to the
    plain, unmodified direction for `final_byte` rather than dropping the
    keystroke, same graceful-degradation posture `poll()`'s own docstring
    already documents for a laggy/multiplexed pty (a modifier this app
    doesn't recognize is still "the user pressed an arrow key"). Returns
    None only when `final_byte` isn't a known arrow final byte at all."""
    direction = _ARROW_BY_FINAL_BYTE.get(final_byte)
    if direction is None:
        return None
    if param_bytes == "1;2":
        return _SHIFT_ARROW_FINAL_BYTES.get(final_byte, direction)
    return direction


class RawKeys:
    """Non-blocking single-key reads from stdin, for terminal hotkeys.
    Inert (poll() always returns None) when stdin isn't a real TTY or
    termios/tty aren't available (Windows) -- terminal modes keep working,
    just without live hotkeys, in that case.

    Optionally (``want_kitty=True``) negotiates the kitty keyboard
    protocol (`kitty_keys.py`, wayfinder map #99 / ticket #118), which is
    the only way a terminal reports key *releases* -- and therefore the
    only way a held key can sustain a note rather than machine-gunning
    it. Off by default, and deliberately so: negotiation is per-view, not
    process-wide, so `|` back-to-menu never pays the (<=0.25s worst case)
    round trip, which would work directly against the "instant
    transition" reason `|` exists at all. Every existing caller
    constructs `RawKeys()` with no arguments and gets byte-for-byte
    today's behaviour, including today's exact `poll()` contract.

    With the protocol active, `poll()` still returns exactly the same
    tokens it always has (`kitty_keys.legacy_token()` maps the richer
    event stream back down: releases are skipped rather than returned as
    None, auto-repeat maps to the same token a press does so holding Down
    on a menu still scrolls, and a bare modifier press maps to nothing so
    a "press any key" screen isn't dismissed by a stray Shift).
    `poll_event()` is the new, opt-in richer view: one
    `kitty_keys.KeyEvent` per call, with press/repeat/release
    distinguished. On a terminal without the protocol it synthesises a
    PRESS event from whatever `poll()` would have returned, so a caller
    written against events works everywhere (with the degraded
    fixed-duration note policy).
    """

    def __init__(self, fd=None, out_fd=None, want_kitty=False,
                 kitty_flags=kitty_keys.SYNTH_FLAGS,
                 negotiation_timeout=config.KITTY_NEGOTIATION_TIMEOUT,
                 set_cbreak=True):
        # `fd` is a constructor parameter rather than sys.stdin.fileno()
        # read inline (as it was before ticket #118) purely for
        # testability: it is what lets every byte path below -- the
        # negotiation, the fallback, the parser -- be exercised against an
        # os.pipe() in an environment with no TTY at all, per this repo's
        # "pure logic unit-tested, real terminal I/O smoke-tested"
        # convention. `out_fd` splits the write side off for the same
        # reason; on a real terminal the two are the same fd.
        self._fd = sys.stdin.fileno() if fd is None else fd
        self._out_fd = self._fd if out_fd is None else out_fd
        self._active = _HAS_TERMIOS and os.isatty(self._fd)
        self._old_settings = None
        self._pushed = False
        self._kitty_flags_wanted = kitty_flags
        self._pending = deque()
        self._queued_events = deque()
        self._held = {}
        self.kitty = False
        self.kitty_flags = None
        self.kitty_flags_before = None
        self.negotiation = "skipped"
        if self._active and set_cbreak:
            self._old_settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        if want_kitty:
            self._negotiate(negotiation_timeout)

    @property
    def active(self):
        """True once a real TTY was found and raw mode entered -- lets a
        caller that *requires* a keypress to proceed (unlike every
        run_terminal_* loop, which just keeps rendering regardless) avoid
        blocking forever on poll(), which always returns None when this is
        False."""
        return self._active

    # -- kitty keyboard protocol negotiation ----------------------------

    def _negotiate(self, timeout):
        """Ask whether the terminal speaks the kitty keyboard protocol,
        wait a bounded time, and push the mode only on success.

        The *failure* path is the one that matters, since it is what every
        non-kitty terminal takes. `kitty_keys.PROBE_SEQUENCE` sends the
        protocol query immediately followed by a DA1 request -- and every
        VT-lineage terminal answers DA1. So a terminal without the
        protocol settles the question the moment its DA1 reply lands,
        rather than waiting out a timeout; the timeout only covers
        something pathological (a pty with nothing on the far end).
        Bytes the user typed ahead during the probe are recovered from
        `probe.leftover` and queued as ordinary input rather than eaten.
        On any non-supported outcome no mode is ever pushed, and poll()
        behaves exactly as it did before this existed.
        """
        if not self._active:
            self.negotiation = "no-tty"
            return
        if not self._write(kitty_keys.PROBE_SEQUENCE):
            self.negotiation = "write-failed"
            return
        probe = kitty_keys.CapabilityProbe()
        deadline = time.monotonic() + timeout
        while not probe.settled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not select.select([self._fd], [], [], remaining)[0]:
                continue
            chunk = os.read(self._fd, 1024)
            if not chunk:
                break
            probe.feed(chunk)
        self.negotiation = probe.state
        for byte in probe.leftover.decode("utf-8", "ignore"):
            self._pending.append(byte)
        if not probe.supported:
            return
        self._write(kitty_keys.push_sequence(self._kitty_flags_wanted))
        self._write(kitty_keys.FOCUS_TRACKING_ON)
        self._pushed = True
        self.kitty = True
        # The flags *pushed*, not the ones the query reported: the query
        # answers with the terminal's flag state as it was *before* the
        # push, which is 0 in a fresh kitty. Reporting that reads as "the
        # protocol isn't on" when reaching this branch at all proves it
        # is -- only a kitty-protocol terminal answers CSI ? u, and one
        # that doesn't settles as unsupported via the DA1 sentinel. That
        # exact misreport was found and fixed during issue #101's live
        # verification; `kitty_flags_before` keeps the queried value,
        # which is the honest answer to "what was already active".
        self.kitty_flags = self._kitty_flags_wanted
        self.kitty_flags_before = probe.flags

    def _write(self, data):
        """Emit an escape sequence. Never raises -- a terminal that has
        gone away must degrade to "no protocol", not crash a render
        loop."""
        try:
            os.write(self._out_fd, data)
            return True
        except OSError:
            return False

    # -- reading --------------------------------------------------------

    def poll(self):
        """One key token, or None -- the exact contract every existing
        caller depends on, unchanged by the kitty protocol."""
        while True:
            item = self._next()
            if item is None:
                return None
            if isinstance(item, str):
                return item
            token = kitty_keys.legacy_token(item)
            if token is not None:
                return token
            # A release (or a bare modifier press): invisible to a legacy
            # caller. Keep draining rather than returning None -- a
            # note-off must never make a menu look like nothing was
            # pressed.

    def poll_event(self):
        """One `kitty_keys.KeyEvent`, or None when there's no input.

        On a terminal without the protocol, whatever `poll()` would have
        returned is synthesised as a PRESS with no modifiers, so a caller
        written against events keeps working everywhere -- it just never
        sees a release there, and must fall back to
        `kitty_keys.FixedDurationKeys`."""
        item = self._next()
        if item is None:
            return None
        if isinstance(item, str):
            return _synthetic_press(item)
        return item

    def release_all(self):
        """Synthetic RELEASE events for every key this instance currently
        believes is held down, and forget them. Returns [] when the
        protocol isn't active (nothing is ever tracked as held there).

        Called automatically on focus-out, and worth calling explicitly
        when a view exits: a stuck note is the single worst failure mode
        of a held-note instrument, and a release delivered to whichever
        window has focus *now* never reaches us."""
        events = [
            kitty_keys.KeyEvent(key=key, event=kitty_keys.RELEASE, mods=0,
                                text="", codepoint=codepoint)
            for key, codepoint in sorted(self._held.items())
        ]
        self._held.clear()
        return events

    def _next(self):
        """Next queued item, else read the fd once. Items are either a str
        (a legacy token, when the protocol isn't active) or a KeyEvent."""
        if self._queued_events:
            return self._queued_events.popleft()
        if self._pending:
            return self._decode_pending()
        # Reads via os.read() on the raw fd, never sys.stdin.read() --
        # sys.stdin is a buffered TextIOWrapper, and mixing select()
        # (which only sees data still sitting at the OS level) with a
        # buffered read() is a classic trap: read(1) can slurp every byte
        # the pty already delivered into Python's internal buffer while
        # only handing back the one requested, so the *next* select()
        # call sees nothing left at the fd and falsely reports "no more
        # input yet" -- even though the rest of an ESC [ <letter> arrow
        # burst was sitting right there. That's what made Up/Down on the
        # menu screen require holding the key instead of registering on a
        # single tap. os.read() is unbuffered, so select() and read() stay
        # in sync with the actual fd state.
        if not self._active or not select.select([self._fd], [], [], 0)[0]:
            return None
        chunk = os.read(self._fd, 1024)
        for byte in chunk.decode("utf-8", "ignore"):
            self._pending.append(byte)
        if not self._pending:
            return None
        return self._decode_pending()

    def _decode_pending(self):
        ch = self._pending.popleft()
        if ch != "\x1b":
            return ch
        # Arrow keys send ESC [ <letter> as one burst, but under a
        # multiplexer (tmux) or a laggy pty the two continuation bytes can
        # arrive a few ms after ESC itself rather than in the same read --
        # a 0-timeout select() right here would misread that as a lone
        # Escape keypress and silently drop the arrow key.
        # config.ESCAPE_SEQUENCE_TIMEOUT gives the rest of the burst a
        # brief window to show up.
        if not self._await_bytes():
            # With the protocol active a bare Escape is impossible (it
            # arrives as CSI 27 u), so an ESC with nothing behind it is
            # genuinely the Escape key only when the protocol is off --
            # where nothing in this app binds it, so returning None
            # preserves today's behaviour exactly.
            return "\x1b" if self.kitty else None
        if self._pending[0] != "[":
            return None
        self._pending.popleft()
        # A bare arrow is 'ESC [ <letter>' -- no parameter bytes at all.
        # A modified arrow (issue #98's Shift+Up/Down) instead sends
        # 'ESC [ <params> <letter>'; a kitty key event sends the same
        # shape with richer parameters (see kitty_keys.parse_key_event()).
        # Keep reading for as long as each byte is a parameter byte; the
        # first byte that isn't one is the sequence's final letter.
        params = ""
        while True:
            if not self._await_bytes():
                return None
            ch = self._pending.popleft()
            if ch and ch in "0123456789;:<>?":
                params += ch
                continue
            if not self.kitty:
                return _parse_csi_params(params, ch)
            if params == "" and ch in (kitty_keys.FOCUS_IN_FINAL,
                                       kitty_keys.FOCUS_OUT_FINAL):
                return self._handle_focus(ch)
            event = kitty_keys.parse_key_event(params, ch)
            if event is not None:
                self._note_held(event)
            return event

    def _handle_focus(self, final_byte):
        """Focus reporting (DECSET 1004, enabled alongside the keyboard
        mode). On focus-out, every key still held is released *to whoever
        has focus now* -- we will never see those releases -- so synthesise
        them here rather than let the notes hang. Returns the first such
        event (the rest are queued), or None on focus-in / nothing held."""
        if final_byte == kitty_keys.FOCUS_IN_FINAL:
            return None
        events = self.release_all()
        if not events:
            return None
        self._queued_events.extend(events[1:])
        return events[0]

    def _note_held(self, event):
        if event.event == kitty_keys.RELEASE:
            self._held.pop(event.key, None)
        else:
            self._held[event.key] = event.codepoint

    def _await_bytes(self):
        """Ensure at least one byte is queued, giving a split escape burst
        the same brief grace window poll() has always given it."""
        if self._pending:
            return True
        timeout = config.ESCAPE_SEQUENCE_TIMEOUT
        if not select.select([self._fd], [], [], timeout)[0]:
            return False
        chunk = os.read(self._fd, 1024)
        for byte in chunk.decode("utf-8", "ignore"):
            self._pending.append(byte)
        return bool(self._pending)

    # -- teardown -------------------------------------------------------

    def restore(self):
        """Leave the terminal exactly as it was found. Idempotent, and
        safe on any error path -- popping the keyboard mode is not
        optional: without it the user's shell inherits a terminal that
        reports every keystroke as an escape code."""
        if self._pushed:
            self._write(kitty_keys.FOCUS_TRACKING_OFF)
            self._write(kitty_keys.pop_sequence())
            self._pushed = False
            self.kitty = False
        if self._active and self._old_settings is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
            self._old_settings = None


def _synthetic_press(token):
    """A `poll()`-shaped token seen as a KeyEvent, for `poll_event()` on a
    terminal with no kitty protocol."""
    single = len(token) == 1
    return kitty_keys.KeyEvent(
        key=token.lower() if single else token,
        event=kitty_keys.PRESS,
        mods=kitty_keys.MOD_SHIFT if single and token.isupper() else 0,
        text=token if single else "",
        codepoint=ord(token) if single else 0,
    )


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


_EDITOR_ACTIONS = [
    "note_toggle", "duration_shorten", "duration_lengthen",
    "clear_to_rest", "insert_column", "delete_column", "undo", "redo", "zoom_cycle",
    "chords_only_toggle", "save", "score_properties",
]
# transpose_up/transpose_down (issue #98 follow-up) are deliberately *not*
# in _EDITOR_ACTIONS/config.DEFAULT_KEYBINDS -- they were remappable
# [keybinds]-table actions bound to '+'/'-' by default, but direct user
# feedback after hands-on use found '+'/'-' too far from the arrow keys
# already used for cursor movement. Replaced with hardcoded Shift+Up/
# Shift+Down (below, resolve_editor_action()'s SHIFT_UP/SHIFT_DOWN cases)
# -- same tier as Left/Right/Up/Down/Enter, never remappable, a modifier-
# arrow combo being a natural extension of "arrows are never remapped in
# this app" rather than something settings_display.is_valid_remap_key()
# could represent anyway (it validates a single character). See
# docs/DECISIONS.md.
# undo/redo (issue #98) intentionally share a letter ('u'/'U' by default) --
# matched case-sensitively below, unlike every other remappable action in
# this codebase (matched case-insensitively, e.g. 'M' also toggles source
# the same as 'm'). See docs/DECISIONS.md.
_EDITOR_CASE_SENSITIVE_ACTIONS = ("undo", "redo")


def _match_editor_action(key, action, keybind_store):
    if key is None:
        return False
    bound = keybind_store.keybind(action)
    if action in _EDITOR_CASE_SENSITIVE_ACTIONS:
        return key == bound
    return key.lower() == bound.lower()


def resolve_editor_action(key, keybind_store=None):
    """Pure keypress-to-action mapping for the score editor's main view
    (issue #98, run_score_editor). Left/Right/Up/Down (cursor movement),
    Shift+Up/Shift+Down (transpose, issue #98 follow-up -- see
    _EDITOR_ACTIONS' own comment for why this replaced a remappable
    '+'/'-'), and Enter (open the Chord builder) are hardcoded, never
    remappable -- same tier as every other view's arrow-key cursor
    handling; every other action goes through config_store's remappable
    [keybinds] table (config.DEFAULT_KEYBINDS' score-editor entries).
    Returns the matched action name ('LEFT'/'RIGHT'/'UP'/'DOWN'/'ENTER',
    'transpose_up'/'transpose_down' for the Shift+arrow tokens
    RawKeys.poll() returns, or one of _EDITOR_ACTIONS), or None if `key`
    doesn't match anything.

    `keybind_store` defaults to the module-level config_store.store
    singleton; a test can pass any object exposing a compatible
    `.keybind(action)` method instead, without needing to monkeypatch
    the module attribute."""
    keybind_store = keybind_store if keybind_store is not None else store
    if key in ("LEFT", "RIGHT", "UP", "DOWN"):
        return key
    if key == "SHIFT_UP":
        return "transpose_up"
    if key == "SHIFT_DOWN":
        return "transpose_down"
    if key in ("\r", "\n"):
        return "ENTER"
    for action in _EDITOR_ACTIONS:
        if _match_editor_action(key, action, keybind_store):
            return action
    return None


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


def is_playback_key(key):
    """Pure: is this keypress the frozen-playback trigger? Enter, and only
    Enter (both the `\r` a raw TTY sends and the `\n` a pipe would),
    hardcoded rather than remappable -- same tier as this app's other
    hardcoded Enter/arrow handling (`resolve_editor_action()`), and
    #109's decision 4: the `tab` view already carries `P N L S Space R [
    ] H |` plus arrows, Enter is the one unused key there, and
    overloading `Space` (freeze, then play) was rejected because it is
    the one key in this view whose meaning is currently crisp."""
    return key in ("\r", "\n")


def _handle_playback_key(key, frozen, playback_state, display, sound_engine_provider,
                          chord_mode=False, notehead_style="symbol", legend_on=True,
                          scroll_offset=0, mark_range=None, bpm=None):
    """'tab' view only: Enter starts frozen-buffer playback, and a second
    Enter stops it (map #99, ticket #121, decision #109). A no-op unless
    the view is currently frozen -- live-view sonification is explicitly
    out of scope (#109 dropped it on a measured ~163ms detection-to-sound
    latency that cannot be reduced without damaging detection itself), so
    this key must do nothing at all while the view is live.

    Scope is `tab_playback.select_columns()`'s: the `[`/`]` marked range
    if one is set, else exactly the columns `display.visible_entries()`
    reports -- the renderer's own width-budget walk, not a second guess
    at what is on screen.

    The schedule is built here, on the render thread, from a plain
    snapshot; the *waiting* happens on a throwaway daemon thread, the
    same shape `_handle_reanalysis_key()` uses and for the same reason --
    the render loop must keep polling keys (not least the Enter that
    stops this) while playback runs.

    A failure to obtain a sound engine (most plausibly
    `synth_engine.SynthUnavailable`: SciPy is an optional extra) is
    recorded on `playback_state.unavailable` for the status line rather
    than raised -- a missing optional dependency must not take down a
    view whose actual job is drawing notes."""
    # Imported locally, same convention as playback/score_writer/pygame
    # and SessionState.ensure_sound_engine() itself -- nothing on the
    # capture/analysis path may pay for the sound engine's import.
    import tab_playback

    if not is_playback_key(key) or not frozen:
        return
    if playback_state.in_progress:
        playback_state.stop.set()
        return
    columns = tab_playback.select_columns(
        display.visible_entries(chord_mode=chord_mode, notehead_style=notehead_style,
                                legend_on=legend_on, scroll_offset=scroll_offset),
        list(display.entries),
        mark_range,
    )
    schedule = tab_playback.build_schedule(columns, bpm=bpm)
    if not schedule:
        return
    if sound_engine_provider is None:
        playback_state.unavailable = "no engine"
        return
    try:
        engine = sound_engine_provider()
    except Exception as exc:                        # noqa: BLE001 -- surfaced in the status line
        playback_state.unavailable = str(exc).splitlines()[0][:60] or exc.__class__.__name__
        return
    playback_state.unavailable = None
    playback_state.note_count = len(schedule)
    playback_state.stop.clear()
    playback_state.in_progress = True
    threading.Thread(target=_playback_worker, args=(engine, schedule, playback_state), daemon=True).start()


def _playback_worker(engine, schedule, playback_state):
    """Plays one `tab_playback.build_schedule()` result in real time, on a
    throwaway thread. Each note is a note-on plus a `schedule_note_off()`
    of its own measured length -- resolved against the audio callback's
    own frame clock, so this thread never has to wake up again to end a
    note (#105 decision 1's "a caller that knows a duration arranges its
    own note-off").

    Sleeps toward each onset in short bounded slices rather than one long
    sleep per gap, so a second Enter (`playback_state.stop`) is acted on
    within a slice instead of after the next note. `all_notes_off()` on
    the way out covers both the stopped case and the natural end -- every
    voice still fades through its own release either way, so stopping
    never clicks.

    Smoke-tested only against a real audio device, per this repo's "pure
    logic unit-tested, real I/O smoke-tested" convention -- everything
    deciding *what* is played is in `tab_playback.py`, which is pure."""
    import sound_engine
    import tab_playback

    started = time.monotonic()
    try:
        for note in schedule:
            if not _wait_until(started + note.start_seconds, playback_state.stop):
                return
            voice_id = engine.note_on(sound_engine.NoteOn(note.pitch, note.velocity))
            engine.schedule_note_off(voice_id, note.duration_seconds)
        _wait_until(started + tab_playback.schedule_duration(schedule), playback_state.stop)
    finally:
        engine.all_notes_off()
        playback_state.in_progress = False


def _wait_until(deadline, stop_event, slice_seconds=0.01):
    """Sleeps until `time.monotonic()` reaches `deadline`, in `slice_
    seconds` steps, returning False the moment `stop_event` is set (and
    True if the deadline was reached un-interrupted). Deliberately not a
    single `Event.wait(remaining)`: the schedule's own onsets are what
    playback must stay aligned to, so each slice recomputes the remaining
    time against the real clock rather than accumulating per-note drift."""
    while True:
        if stop_event.is_set():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, slice_seconds))


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
                      reanalysis_buffer, session_recorder, time_signature=config.DEFAULT_TIME_SIGNATURE,
                      sound_engine_provider=None):
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
    # Frozen-buffer playback (map #99, ticket #121, decision #109):
    # Enter-while-frozen plays what's on screen (or the marked range).
    # Local to one run_terminal_tab call, like reanalysis_state -- there
    # is nothing to preserve across a '|' back-to-menu round trip;
    # `sound_engine_provider` (SessionState.ensure_sound_engine, passed
    # by run_session) is the one piece that outlives it, so the output
    # device isn't reopened per tool switch.
    playback_state = PlaybackState()

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
            if was_frozen and not frozen and playback_state.in_progress:
                # Unfreezing stops playback: what was being played back is
                # exactly "what is frozen on screen", and that stops being
                # a stable thing the moment columns start scrolling again.
                playback_state.stop.set()
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
            _handle_playback_key(key, frozen, playback_state, display, sound_engine_provider,
                                  chord_mode=chord_mode, notehead_style=notehead_style,
                                  legend_on=legend_on, scroll_offset=scroll_offset,
                                  mark_range=_mark_range(mark_start, mark_end),
                                  bpm=reanalysis_bpm_estimate if reanalysis_bpm_estimate is not None
                                  else bpm_estimate)
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

            if playback_state.in_progress:
                reanalysis_hint += f"  play={playback_state.note_count}notes(enter)"
            elif playback_state.unavailable:
                reanalysis_hint += f"  play=unavailable({playback_state.unavailable})"

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
                "enter=play(frozen)",
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
        playback_state.stop.set()
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
            import sound_engine

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
                                 session.session_recorder, time_signature=time_signature,
                                 sound_engine_provider=session.ensure_sound_engine)
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

    `play=True` (map #24's playback engine) plays each column's note(s)
    the instant that column is pushed on screen, reusing this loop's own
    already-paced `time.sleep()` clock rather than running a second,
    independent one. Each event's own recorded `duration_seconds` is
    divided by `speed` so the audio speeds up/slows down in lockstep with
    the visual pacing above, not just the gaps between notes.

    Since map #99's ticket #112 that goes through `sound_engine.
    SoundEngine`, not `playback.LiveScheduler` (superseded, decision
    #105): one note-on per event, with its matching note-off scheduled
    `duration_seconds / speed` later against the audio callback's own
    frame clock. This caller knows each note's duration up front, which
    is exactly the "arrange your own note-off" case #105 anticipated --
    the engine itself still has no duration-carrying primitive. A
    `SoundEngine` is built locally here rather than taken from
    `main.SessionState`: `virtualnote replay` never constructs a
    SessionState at all (see virtualnote.py's main()), being a standalone
    offline entry point that touches no audio *input*."""
    from terminal_tab_display import TabDisplay

    events = load_events(file_path)
    columns = group_columns(events)

    engine = None
    if play:
        import sound_engine

        engine = sound_engine.SoundEngine(detection_active=False)
        engine.ensure_started()

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
                    if engine is not None:
                        note_on = sound_engine.NoteOn.from_pitch_class(event["pc"], event["octave"])
                        voice_id = engine.note_on(note_on)
                        engine.schedule_note_off(
                            voice_id, event["duration_seconds"] / max(speed, 1e-6)
                        )
            status = f"virtualnote replay  file={os.path.basename(file_path)}  t={t:.2f}s  speed={speed}x"
            display.render(status, chord_mode=True)
    except KeyboardInterrupt:
        pass
    finally:
        if engine is not None:
            engine.stop()
        resolved_dump_path = dump_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        display.dump_ansi(resolved_dump_path)


def _run_chord_builder(keys, column):
    """Score editor (issue #98): the Chord builder screen's interactive
    loop -- opened by Enter on a column, closed by `chord_builder_exit`
    ('b' by default, matched case-sensitively so typing an uppercase 'B'
    on the ROOT reel -- a real root letter -- doesn't also exit the
    screen; see docs/DECISIONS.md). Mutates `column.notes` in place only
    on exit (chord_builder_display.notes_from_state()), never mid-edit --
    the caller (run_score_editor) already recorded an undo snapshot of
    the whole score before opening this screen, so the column's prior
    contents are always recoverable via `undo` regardless of what
    happens in here. Smoke-tested manually only, same convention as
    every other run_terminal_*/run_*_screen interactive loop.

    Up/Down switches the focused reel, Left/Right spins it -- the reverse
    of this screen's original Left/Right-switches/Up/Down-spins binding
    (inherited unchanged from the prototype it was built from), swapped
    per direct user feedback after hands-on use: the five reels render as
    five stacked *rows* (chord_builder_display.render()), so Up/Down
    should navigate a vertical list and Left/Right should adjust the
    focused row's value -- see docs/DECISIONS.md."""
    import chord_builder_display as cbd

    state = cbd.state_from_column(column)
    quality_index = 0
    dt = 1.0 / config.TERMINAL_FPS
    degree_options = {"third": cbd.THIRD_OPTIONS, "fifth": cbd.FIFTH_OPTIONS, "seventh": cbd.SEVENTH_OPTIONS}

    while True:
        cbd.render(state, quality_index, "Up/Down=reel  Left/Right=spin  type=jump  Enter=force-commit  b=done")
        key = keys.poll()
        if key is None:
            time.sleep(dt)
            continue

        if key == store.keybind("chord_builder_exit"):
            column.notes = cbd.notes_from_state(state)
            return

        slot_name = cbd.BUILDER_SLOTS[state.slot]
        if key == "UP":
            state.slot = cbd.move_slot(state.slot, -1)
            state.typed = ""
        elif key == "DOWN":
            state.slot = cbd.move_slot(state.slot, 1)
            state.typed = ""
        elif key in ("LEFT", "RIGHT"):
            delta = 1 if key == "RIGHT" else -1
            if slot_name == "root":
                state.root_pc = cbd.spin_root(state.root_pc, delta)
                state.root_just_jumped = False
            elif slot_name == "quality":
                quality_index, preset_key = cbd.spin_quality(quality_index, delta)
                cbd.apply_quality_preset(state, preset_key)
            elif slot_name in degree_options:
                token_attr = f"{slot_name}_token"
                setattr(state, token_attr, cbd.spin_degree(getattr(state, token_attr), degree_options[slot_name], delta))
        elif key in ("\r", "\n"):
            if slot_name == "quality":
                resolved = cbd.force_commit_alias(state.typed, cbd.QUALITY_ALIASES)
                if resolved is not None:
                    quality_index = next(i for i, p in enumerate(cbd.QUALITY_PRESETS) if p[0] == resolved)
                    cbd.apply_quality_preset(state, resolved)
                    state.typed = ""
            elif slot_name in degree_options:
                resolved = cbd.force_commit_alias(state.typed, cbd.degree_alias_map(degree_options[slot_name]))
                if resolved is not None:
                    setattr(state, f"{slot_name}_token", resolved)
                    state.typed = ""
        elif len(key) == 1 and key.isprintable():
            if slot_name == "root":
                state.root_pc, state.root_just_jumped = cbd.step_root_typeahead(
                    state.root_pc, state.root_just_jumped, key
                )
            elif slot_name == "quality":
                state.typed, resolved = cbd.step_alias_typeahead(state.typed, key, cbd.QUALITY_ALIASES)
                if resolved is not None:
                    quality_index = next(i for i, p in enumerate(cbd.QUALITY_PRESETS) if p[0] == resolved)
                    cbd.apply_quality_preset(state, resolved)
            elif slot_name in degree_options:
                state.typed, resolved = cbd.step_alias_typeahead(state.typed, key, cbd.degree_alias_map(
                    degree_options[slot_name]))
                if resolved is not None:
                    setattr(state, f"{slot_name}_token", resolved)


_PROPERTY_FIELD_PREFIX = {"time_signature": "time", "key_signature": "key", "tempo": "tempo"}
# The two fields with a natural typed-digit form (issue #98 follow-up --
# see _parse_property_input()). key_signature has no such form (there's
# no sensible digit string for "2 sharps"), so it's spin-only.
_PROPERTY_TYPABLE_SLOTS = ("time_signature", "tempo")


def _property_field_texts(score):
    """Plain (unhighlighted) status-line text for each of the score-level
    properties fields (score_properties, 't') -- shown at all times in
    run_score_editor()'s status line, not just while actively editing
    (issue #98 follow-up, direct user feedback: a second, separate screen
    for these was unwanted friction -- see docs/DECISIONS.md). Mirrors
    `tab`'s own tempo=/time= status-field convention. A plain dict
    keyed by score_properties_display.PROPERTY_SLOTS' own names, not a
    positional tuple, so callers don't need to remember field order."""
    import score_properties_display as spd

    numerator, denominator = score.time_signature
    return {
        "time_signature": f"time={numerator}/{denominator}",
        "key_signature": f"key={spd.key_fifths_label(score.key_fifths)}",
        "tempo": f"tempo={score.tempo_bpm:.0f}",
    }


def _parse_property_input(slot_name, text):
    """Parses the inline header editor's typed digit buffer for the
    highlighted field into a new value -- mirrors
    settings_display.parse_numeric_input()'s "empty means no typed value,
    anything unparseable raises ValueError" contract, but for the two
    score-properties fields with a natural typed form (see
    _PROPERTY_TYPABLE_SLOTS; key_signature has none and never reaches
    here). 'tempo' parses a plain BPM number, clamped into
    score_properties_display's TEMPO_MIN_BPM/TEMPO_MAX_BPM range, same
    clamp-not-wrap convention every bounded numeric field in this app
    uses. 'time_signature' parses free-form 'N/D' -- deliberately *not*
    snapped to spin_time_signature()'s fixed TIME_SIGNATURE_OPTIONS set,
    since typing a value directly (e.g. 11/8) is exactly the point of a
    free-form entry path alongside the fixed-set spin. Returns None for
    an empty buffer (no typed value yet -- a no-op, not an error)."""
    import score_properties_display as spd

    text = text.strip()
    if text == "":
        return None
    if slot_name == "tempo":
        value = float(int(text))
        return max(spd.TEMPO_MIN_BPM, min(spd.TEMPO_MAX_BPM, value))
    if slot_name == "time_signature":
        parts = text.split("/")
        if len(parts) != 2:
            raise ValueError("time signature must be N/D")
        numerator, denominator = int(parts[0]), int(parts[1])
        if numerator <= 0 or denominator <= 0:
            raise ValueError("both N and D must be > 0")
        return (numerator, denominator)
    raise ValueError(f"{slot_name} has no typed form")


def _handle_property_key(key, score, slot, buffer):
    """Score editor (issue #98 follow-up): pure-ish dispatch for one
    keypress while the inline header editor (score_properties, 't') is
    active -- mutates `score` in place like every other score-editor
    mutation function in this codebase (see score_editor_display.py's
    module docstring for that convention), rather than returning a new
    EditorScore. `slot` is the currently-highlighted field's index into
    score_properties_display.PROPERTY_SLOTS; `buffer` is that field's
    in-progress typed-digit buffer, reset whenever focus moves to a
    different field or the field is spun directly. Returns
    `(new_slot, new_buffer, still_editing)` -- `still_editing=False`
    means Enter was pressed and run_score_editor() should return to
    normal cursor editing.

    Left/Right move the highlighted field -- a *horizontal* strip of
    three fields, so per the same visual-orientation principle as the
    Chord builder's now-vertical Up/Down navigation (opposite physical
    mapping, since these are genuinely different widget shapes -- see
    docs/DECISIONS.md), Left/Right navigates here. Up/Down spins the
    highlighted field's value via score_properties_display's existing
    spin_time_signature()/spin_key_fifths()/spin_tempo() -- unchanged
    from the old standalone screen, only the screen/mode plumbing around
    them differs. A digit (or '/' for time signature) accumulates into
    `buffer` on a typable field (_PROPERTY_TYPABLE_SLOTS); Backspace
    trims it. Enter parses+applies any pending buffer
    (_parse_property_input(), swallowing an unparseable buffer rather
    than crashing -- same "leave the field unchanged" posture
    settings_display._capture_numeric() follows on a bad parse) and
    always exits edit mode, buffer or not."""
    import score_properties_display as spd

    slot_name = spd.PROPERTY_SLOTS[slot]

    if key == "LEFT":
        return spd.move_slot(slot, -1), "", True
    if key == "RIGHT":
        return spd.move_slot(slot, 1), "", True
    if key in ("UP", "DOWN"):
        delta = 1 if key == "UP" else -1
        if slot_name == "time_signature":
            score.time_signature = spd.spin_time_signature(score.time_signature, delta)
        elif slot_name == "key_signature":
            score.key_fifths = spd.spin_key_fifths(score.key_fifths, delta)
        elif slot_name == "tempo":
            score.tempo_bpm = spd.spin_tempo(score.tempo_bpm, delta)
        return slot, "", True
    if key in ("\r", "\n"):
        if buffer and slot_name in _PROPERTY_TYPABLE_SLOTS:
            try:
                value = _parse_property_input(slot_name, buffer)
            except ValueError:
                value = None
            if value is not None:
                if slot_name == "time_signature":
                    score.time_signature = value
                else:
                    score.tempo_bpm = value
        return slot, "", False
    if key in ("\x7f", "\x08"):
        return slot, buffer[:-1], True
    if (slot_name in _PROPERTY_TYPABLE_SLOTS and isinstance(key, str) and len(key) == 1
            and (key.isdigit() or key == "/")):
        return slot, buffer + key, True
    return slot, buffer, True


def run_score_editor(path):
    """`virtualnote edit <path>` (issue #98): loads `path` via
    score_editor_state.load_score() if it already exists, otherwise
    starts a brand-new blank score (new_blank_score()) to be saved to
    `path` later. Drives its own interactive loop (own RawKeys instance,
    mirroring every other run_terminal_* function) over
    score_editor_display.py's pure mutation/render layer, with
    EditHistory backing undo/redo -- never touches SessionState/audio, so
    virtualnote.py's 'edit' subcommand handles and returns before
    SessionState is even constructed, same shape as transcribe/replay.
    Returns the "quit"/"menu" sentinel convention every other
    run_terminal_* function does, so shell.py's menu loop can dispatch it
    exactly like a real session tool despite that.

    Quitting (| or Ctrl+C) while there are unsaved changes (saved=no in
    the status line) needs a second confirming press of the same key --
    the one editor view in this app where quitting can lose real work,
    unlike every other terminal view's purely ephemeral render state.
    The first press just arms `quit_pending` and shows an inline warning;
    any other keypress in between (including a real edit) disarms it
    again, so a user has to deliberately press the same quit key twice in
    a row to actually discard changes. Undo/redo don't attempt to track
    whether the score has returned to exactly its last-saved content --
    dirty stays True after either, a conservative "warn even if you
    undid your way back to the saved state" simplification (see
    docs/DECISIONS.md)."""
    import score_editor_display as sed
    import score_properties_display as spd
    from score_editor_state import EditHistory, load_score, new_blank_score, save_score

    score = load_score(path) if os.path.exists(path) else new_blank_score()
    history = EditHistory()
    dirty = False
    cursor_col = 0
    first_column = score.columns[0]
    cursor_row = (
        staff_row(first_column.notes[0].pitch_class, first_column.notes[0].octave)
        if first_column.notes else 10  # no note to anchor to -- land on middle C, a sane default
    )
    zoom_level = 0
    chords_only = False
    help_legend_on = True
    quit_pending = False
    properties_editing = False
    properties_slot = 0
    properties_buffer = ""
    dt = 1.0 / config.TERMINAL_FPS
    keys = RawKeys()

    def _record():
        history.record(score)

    try:
        while True:
            try:
                key = keys.poll()
                quit_requested, quit_result = (key == "|"), "menu"
            except KeyboardInterrupt:
                key, quit_requested, quit_result = None, True, "quit"

            if quit_requested:
                if dirty and not quit_pending:
                    quit_pending = True
                else:
                    return quit_result
            else:
                if key is not None:
                    quit_pending = False

                if properties_editing:
                    # Inline header editor (score_properties, 't', issue
                    # #98 follow-up): intercepts every key here instead of
                    # going through resolve_editor_action()'s normal
                    # cursor-editing dispatch below -- Left/Right/Up/Down
                    # mean "move/spin a properties field" while this mode
                    # is active, not "move the cursor"/"transpose".
                    properties_slot, properties_buffer, properties_editing = _handle_property_key(
                        key, score, properties_slot, properties_buffer)
                    if not properties_editing:
                        dirty = True
                else:
                    action = resolve_editor_action(key)

                    if action == "LEFT":
                        cursor_col = sed.clamp_column(cursor_col - 1, len(score.columns))
                    elif action == "RIGHT":
                        cursor_col = sed.clamp_column(cursor_col + 1, len(score.columns))
                    elif action == "UP":
                        cursor_row = sed.clamp_row(cursor_row + 1)
                    elif action == "DOWN":
                        cursor_row = sed.clamp_row(cursor_row - 1)
                    elif action == "note_toggle":
                        _record()
                        if sed.toggle_note_at_cursor(score.columns[cursor_col], cursor_row, score.key_fifths):
                            dirty = True
                    elif action in ("transpose_up", "transpose_down"):
                        _record()
                        direction = 1 if action == "transpose_up" else -1
                        new_row = sed.transpose_note_at_cursor(score.columns[cursor_col], cursor_row, direction)
                        if new_row is not None:
                            cursor_row = new_row
                            dirty = True
                    elif action in ("duration_shorten", "duration_lengthen"):
                        _record()
                        sed.cycle_duration(score.columns[cursor_col], 1 if action == "duration_shorten" else -1)
                        dirty = True
                    elif action == "clear_to_rest":
                        _record()
                        sed.clear_to_rest(score.columns[cursor_col])
                        dirty = True
                    elif action == "insert_column":
                        _record()
                        sed.insert_column_at(score, cursor_col)
                        dirty = True
                    elif action == "delete_column":
                        _record()
                        if sed.delete_column_at(score, cursor_col):
                            cursor_col = sed.clamp_column(cursor_col, len(score.columns))
                            dirty = True
                    elif action == "undo":
                        previous = history.undo(score)
                        if previous is not None:
                            score = previous
                            cursor_col = sed.clamp_column(cursor_col, len(score.columns))
                            dirty = True
                    elif action == "redo":
                        next_score = history.redo(score)
                        if next_score is not None:
                            score = next_score
                            cursor_col = sed.clamp_column(cursor_col, len(score.columns))
                            dirty = True
                    elif action == "zoom_cycle":
                        zoom_level = sed.cycle_zoom(zoom_level)
                    elif action == "chords_only_toggle":
                        chords_only = not chords_only
                    elif action == "ENTER":
                        _record()
                        _run_chord_builder(keys, score.columns[cursor_col])
                        dirty = True
                    elif action == "score_properties":
                        _record()
                        properties_editing = True
                        properties_slot = 0
                        properties_buffer = ""
                    elif action == "save":
                        save_score(score, path)
                        dirty = False
                    elif key is not None and key.lower() == "h":
                        help_legend_on = not help_legend_on

            zoom_name, _width = sed.ZOOM_LEVELS[zoom_level]
            field_texts = _property_field_texts(score)
            if properties_editing:
                highlighted_slot_name = spd.PROPERTY_SLOTS[properties_slot]
                field_parts = []
                for slot_name in spd.PROPERTY_SLOTS:
                    if slot_name == highlighted_slot_name:
                        text = (f"{_PROPERTY_FIELD_PREFIX[slot_name]}={properties_buffer}"
                                if properties_buffer else field_texts[slot_name])
                        text = f"\033[7m{text}\033[0m"
                    else:
                        text = field_texts[slot_name]
                    field_parts.append(text)
                properties_line = "  ".join(field_parts)
            else:
                properties_line = "  ".join(field_texts[s] for s in spd.PROPERTY_SLOTS)
            status = (f"saved={'no' if dirty else 'yes'}  col={cursor_col + 1}/{len(score.columns)}  "
                      f"{properties_line}  "
                      f"zoom={zoom_name}({_key_hint('zoom_cycle')})  "
                      f"chords={'on' if chords_only else 'off'}({_key_hint('chords_only_toggle')})  "
                      f"({_key_hint('save')})save  legend(h)")
            if quit_pending:
                status += "  [unsaved changes -- press quit again to discard, any other key to cancel]"
            if properties_editing:
                help_legend = _legend_line([
                    "left/right=field", "up/down=value", "0-9=type (time/tempo)", "enter=done",
                ]) if help_legend_on else ""
            else:
                help_legend = _legend_line([
                    "left/right=column", "up/down=pitch", f"{_key_hint('note_toggle')}=note",
                    "shift+up/shift+down=transpose",
                    f"{_key_hint('duration_shorten')}/{_key_hint('duration_lengthen')}=duration",
                    f"{_key_hint('clear_to_rest')}=rest", f"{_key_hint('insert_column')}=insert",
                    f"{_key_hint('delete_column')}=delete", f"{_key_hint('undo')}/{_key_hint('redo')}=undo/redo",
                    f"{_key_hint('zoom_cycle')}=zoom", f"{_key_hint('chords_only_toggle')}=chords",
                    "enter=chordbuilder", f"{_key_hint('score_properties')}=properties",
                    f"{_key_hint('save')}=save",
                ]) if help_legend_on else ""
            sed.render(score, cursor_col, cursor_row, zoom_level, chords_only, status, help_legend)
            time.sleep(dt)
    finally:
        keys.restore()


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
