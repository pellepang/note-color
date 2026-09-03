"""PROTOTYPE -- throwaway, wayfinder ticket #101 (map #99, sound engine).

The I/O half: what `main.RawKeys` would become. Same class shape, same
`poll()` contract, plus a `poll_event()` that yields kitty press/repeat/
release events when the terminal supports them.

Deliberate differences from the real `main.RawKeys`, both of which are
findings this prototype exists to surface:

1. **`fd` is a constructor parameter** (defaulting to stdin) instead of
   `sys.stdin.fileno()` hard-coded inside `poll()`. That single change is
   what makes every byte-level path here testable against an `os.pipe()`
   without a TTY -- which is how `test_kitty_rawkeys.py` verifies the
   negotiation, the fallback, and legacy-token compatibility in an
   environment with no terminal at all.
2. **`poll()` drains through a decoded-token queue**, because one read can
   now legitimately yield several events (a chord of releases arrives in
   one burst), whereas today's `poll()` returns at most one token per call
   and discards nothing because nothing else was possible.

Everything else -- cbreak entry, `active`, `restore()`, returning bare
characters for ordinary keys, returning `"UP"`/`"SHIFT_UP"`/... for arrows
-- is preserved exactly.
"""

import os
import select
import sys
from collections import deque

import kitty_keys as kk

try:
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:  # Windows
    _HAS_TERMIOS = False

#: Same value as config.ESCAPE_SEQUENCE_TIMEOUT in the real app.
ESCAPE_SEQUENCE_TIMEOUT = 0.05

#: How long to wait for the terminal to answer PROBE_SEQUENCE before
#: giving up and assuming no support. Generous relative to a local pty
#: round trip (sub-millisecond) but still a blink to a human, and paid
#: exactly once when the view opens. The DA1 sentinel means a *terminal*
#: that answers at all settles the question long before this expires; the
#: timeout only covers something pathological (a pty with no terminal on
#: the far end at all).
NEGOTIATION_TIMEOUT = 0.25


class KittyRawKeys:
    """Non-blocking key reads with optional kitty keyboard protocol.

    Construct, use, `restore()` -- exactly as `main.RawKeys` is used
    today. `restore()` additionally pops the keyboard mode; it is
    idempotent and safe to call on any error path.
    """

    def __init__(self, fd=None, out_fd=None, want_kitty=True,
                 flags=kk.SYNTH_FLAGS,
                 negotiation_timeout=NEGOTIATION_TIMEOUT, set_cbreak=True):
        self._fd = sys.stdin.fileno() if fd is None else fd
        # Reads and writes are the same fd on a real terminal; separating
        # them is purely so tests can drive the read side from a pipe
        # while the escape sequences this class emits go somewhere inert.
        self._out_fd = self._fd if out_fd is None else out_fd
        self._active = _HAS_TERMIOS and os.isatty(self._fd)
        self._old_settings = None
        self._pushed = False
        self._flags = flags
        self._pending = deque()
        self.kitty = False
        self.kitty_flags = None
        self.kitty_flags_before = None
        self.negotiation = "skipped"
        if self._active and set_cbreak:
            self._old_settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        if want_kitty:
            self._negotiate(negotiation_timeout)

    # -- properties matching main.RawKeys -------------------------------

    @property
    def active(self):
        return self._active

    # -- negotiation ----------------------------------------------------

    def _negotiate(self, timeout):
        """Ask, wait bounded, decide. Pushes SYNTH_FLAGS only on success.

        The failure path is the one that matters: a terminal with no kitty
        support answers the DA1 sentinel, `CapabilityProbe` settles on
        "unsupported" immediately, no mode is ever pushed, and `poll()`
        behaves byte-for-byte as `main.RawKeys.poll()` does today. Bytes
        the user typed ahead during the probe are recovered from
        `probe.leftover` rather than dropped.
        """
        if not self._active:
            self.negotiation = "no-tty"
            return
        if not self._write(kk.PROBE_SEQUENCE):
            self.negotiation = "write-failed"
            return
        probe = kk.CapabilityProbe()
        deadline = _now() + timeout
        while not probe.settled:
            remaining = deadline - _now()
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
        if probe.supported:
            self._write(kk.push_sequence(self._flags))
            self._pushed = True
            self.kitty = True
            # The flags we *pushed*, not `probe.flags` -- the query reports
            # the terminal's flags as they were *before* the push, which is
            # 0 in a fresh kitty. Reporting that reads as "the protocol
            # isn't on" when in fact reaching this branch at all proves it
            # is: only a kitty-protocol terminal answers `CSI ? u`, and a
            # terminal that doesn't settles as unsupported via the DA1
            # sentinel instead. Confirmed live in real kitty (#101).
            self.kitty_flags = self._flags
            self.kitty_flags_before = probe.flags

    def _write(self, data):
        """Emit an escape sequence. Never raises -- a terminal that has
        gone away must degrade to "no protocol", not crash a render loop."""
        try:
            os.write(self._out_fd, data)
            return True
        except OSError:
            return False

    # -- reading --------------------------------------------------------

    def poll(self):
        """One legacy token, or None -- the exact contract every existing
        caller already depends on."""
        while True:
            event_or_token = self._next()
            if event_or_token is None:
                return None
            if isinstance(event_or_token, str):
                return event_or_token
            token = kk.legacy_token(event_or_token)
            if token is not None:
                return token
            # A release event, invisible to a legacy caller. Keep draining
            # rather than returning None -- a note-off must never make a
            # menu look like nothing was pressed.

    def poll_event(self):
        """One `kitty_keys.KeyEvent`, or None. On a terminal without the
        protocol, whatever `poll()` would have returned is synthesised as a
        PRESS event with no modifiers, so a caller written against events
        keeps working (with the fixed-duration policy) everywhere."""
        item = self._next()
        if item is None:
            return None
        if isinstance(item, str):
            return _synthetic_press(item)
        return item

    def _next(self):
        """Next queued item, else read the fd once. Items are either a str
        (legacy token, no protocol) or a KeyEvent."""
        if self._pending:
            return self._decode_pending()
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
        if not self._await_bytes():
            return "\x1b" if self.kitty else None
        if self._pending[0] != "[":
            return None
        self._pending.popleft()
        params = ""
        while True:
            if not self._await_bytes():
                return None
            ch = self._pending.popleft()
            if ch in "0123456789;:<>?":
                params += ch
                continue
            if self.kitty:
                return kk.parse_key_event(params, ch)
            return _legacy_csi(params, ch)

    def _await_bytes(self):
        """Ensure at least one byte is queued, giving a split escape burst
        the same brief grace window today's poll() gives it."""
        if self._pending:
            return True
        if not select.select([self._fd], [], [], ESCAPE_SEQUENCE_TIMEOUT)[0]:
            return False
        chunk = os.read(self._fd, 1024)
        for byte in chunk.decode("utf-8", "ignore"):
            self._pending.append(byte)
        return bool(self._pending)

    # -- teardown -------------------------------------------------------

    def restore(self):
        if self._pushed:
            self._write(kk.pop_sequence())
            self._pushed = False
        if self._active and self._old_settings is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
            self._old_settings = None


_ARROW_BY_FINAL_BYTE = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}


def _legacy_csi(params, final_byte):
    """Exactly `main._parse_csi_params()`, reproduced so the no-protocol
    path is provably unchanged."""
    direction = _ARROW_BY_FINAL_BYTE.get(final_byte)
    if direction is None:
        return None
    if params == "1;2":
        return {"A": "SHIFT_UP", "B": "SHIFT_DOWN"}.get(final_byte, direction)
    return direction


def _synthetic_press(token):
    codepoint = ord(token) if len(token) == 1 else 0
    return kk.KeyEvent(
        key=token if len(token) > 1 else token.lower(),
        event=kk.PRESS,
        mods=kk.MOD_SHIFT if token.isupper() and len(token) == 1 else 0,
        text=token if len(token) == 1 else "",
        codepoint=codepoint,
    )


def _now():
    import time

    return time.monotonic()
