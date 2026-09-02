"""PROTOTYPE -- throwaway, wayfinder ticket #101 (map #99, sound engine).

Pure logic for the kitty keyboard protocol: negotiation byte sequences,
capability detection with a safe fallback, the CSI event-encoding parser
(press / repeat / release, modifiers, multiple simultaneously-held keys),
and the two held-note policies a QWERTY piano needs -- the real one
(kitty releases) and the degraded one (fixed-duration notes).

Nothing in here does I/O, opens a terminal, or imports from the real app.
`kitty_rawkeys.py` is the thin I/O layer on top; `demo.py` is the live
harness a human runs in kitty.

Protocol reference: https://sw.kovidgoyal.net/kitty/keyboard-protocol/
Everything below was written against that spec and is exercised by
`test_kitty_keys.py`; the *wire* behaviour of a real kitty terminal is
what the demo exists to confirm.
"""

from collections import namedtuple

# --------------------------------------------------------------------------
# Negotiation byte sequences
# --------------------------------------------------------------------------

# Progressive-enhancement flags (kitty protocol, section "Progressive
# enhancement"). Bit values, OR-ed together:
FLAG_DISAMBIGUATE = 0b00001  # 1  -- escape codes for otherwise-ambiguous keys
FLAG_EVENT_TYPES = 0b00010  # 2  -- press / repeat / release event types
FLAG_ALTERNATE_KEYS = 0b00100  # 4  -- shifted/base-layout key codes
FLAG_ALL_AS_ESCAPES = 0b01000  # 8  -- *every* key as an escape code, text included
FLAG_ASSOCIATED_TEXT = 0b10000  # 16 -- the text the key would have produced

# What a held-note QWERTY instrument actually needs.
#
# FLAG_EVENT_TYPES alone is NOT enough: keys that would otherwise be sent
# as plain text keep being sent as plain text on press, and a bare text
# byte has nowhere to carry an event type -- so no release is ever
# reported for exactly the letter keys a QWERTY piano is played on.
# FLAG_ALL_AS_ESCAPES is what forces those keys through the CSI ... u
# encoding, where a release *can* be expressed.
#
# FLAG_ASSOCIATED_TEXT is included so the terminal, not this code, decides
# what a keystroke "means" as text under whatever keyboard layout is
# actually installed -- which is what lets the compatibility shim in
# kitty_rawkeys.py hand existing RawKeys callers the same plain characters
# they get today (see that module's docstring).
SYNTH_FLAGS = (
    FLAG_DISAMBIGUATE | FLAG_EVENT_TYPES | FLAG_ALL_AS_ESCAPES | FLAG_ASSOCIATED_TEXT
)  # == 27

#: Ask the terminal which flags are currently set. A terminal that speaks
#: the protocol answers ``CSI ? <flags> u``; one that does not answers
#: nothing at all -- which is exactly why this is never sent on its own
#: (see PROBE_SEQUENCE).
QUERY_SEQUENCE = b"\x1b[?u"

#: Primary Device Attributes. Every VT-lineage terminal in existence
#: answers this (``CSI ? ... c``). Sent immediately *after* QUERY_SEQUENCE
#: it acts as a sentinel: if the DA1 reply arrives and no kitty reply came
#: before it, the terminal definitively does not support the protocol.
#: That turns "wait for a reply that may never come" (a hang) into "wait
#: for a reply that is guaranteed to come" (bounded), which is the whole
#: reason capability detection here is safe on a non-kitty terminal.
DA1_SEQUENCE = b"\x1b[c"

#: The two together, in the order they must be written.
PROBE_SEQUENCE = QUERY_SEQUENCE + DA1_SEQUENCE


def push_sequence(flags=SYNTH_FLAGS):
    """``CSI > <flags> u`` -- push `flags` onto the terminal's own stack of
    keyboard modes. Stack-based (not set-based) by design: a nested screen,
    a subprocess, or a crash-and-restore can pop back to exactly whatever
    the enclosing context had, without this app having to remember it."""
    return b"\x1b[>" + str(int(flags)).encode() + b"u"


def pop_sequence(count=1):
    """``CSI < <count> u`` -- pop `count` entries back off that stack.
    MUST be paired with every push, including on the exception path, or the
    user's shell inherits a terminal that reports every keystroke as an
    escape code."""
    return b"\x1b[<" + str(int(count)).encode() + b"u"


# --------------------------------------------------------------------------
# Capability detection
# --------------------------------------------------------------------------

_PROBE_PENDING = "pending"
_PROBE_SUPPORTED = "supported"
_PROBE_UNSUPPORTED = "unsupported"


class CapabilityProbe:
    """Consumes the bytes a terminal sends back after PROBE_SEQUENCE and
    decides whether the kitty keyboard protocol is available.

    Pure: `feed()` takes bytes, `state` reports the verdict. Three
    outcomes, and the third is the one that matters most:

    * ``"supported"``   -- a ``CSI ? <flags> u`` reply arrived.
    * ``"unsupported"`` -- the DA1 reply (``CSI ? ... c``) arrived without
      one, so the terminal parsed the query and had nothing to say.
    * ``"pending"``     -- neither has arrived yet. A caller times out on
      this and treats it as unsupported; a terminal so unusual that it
      answers *neither* probe still costs only the timeout, never a hang.

    Anything fed in that is not part of a recognised reply is preserved in
    `leftover`, so a keystroke the user typed ahead during negotiation is
    handed back to the normal input path instead of being eaten. That is
    the "must not garble input" half of the requirement; the DA1 sentinel
    is the "must not hang" half.
    """

    def __init__(self):
        self._buf = b""
        self._state = _PROBE_PENDING
        self.flags = None
        self.leftover = b""

    @property
    def state(self):
        return self._state

    @property
    def supported(self):
        return self._state == _PROBE_SUPPORTED

    @property
    def settled(self):
        return self._state != _PROBE_PENDING

    def feed(self, data):
        """Feed raw bytes read from the terminal. Safe to call repeatedly
        with arbitrary chunk boundaries -- a reply split across two reads
        is reassembled, since terminals give no delivery guarantees."""
        self._buf += data
        self._drain()

    def _drain(self):
        while self._buf:
            index = self._buf.find(b"\x1b[?")
            if index == -1:
                # No CSI ? introducer anywhere. Everything except a
                # trailing partial introducer is ordinary input.
                keep = _trailing_partial(self._buf, b"\x1b[?")
                if keep:
                    self.leftover += self._buf[:-keep]
                    self._buf = self._buf[-keep:]
                else:
                    self.leftover += self._buf
                    self._buf = b""
                return
            self.leftover += self._buf[:index]
            rest = self._buf[index + 3 :]
            end = _find_final_byte(rest)
            if end is None:
                self._buf = self._buf[index:]  # incomplete, wait for more
                return
            params, final = rest[:end], rest[end : end + 1]
            self._buf = rest[end + 1 :]
            if final == b"u":
                if self._state == _PROBE_PENDING:
                    self._state = _PROBE_SUPPORTED
                    self.flags = _first_int(params)
            elif final == b"c":
                if self._state == _PROBE_PENDING:
                    self._state = _PROBE_UNSUPPORTED
            else:
                # Some other CSI ? ... reply (e.g. a DECRPM report).
                # Not ours; not input either -- discard it rather than
                # letting it reach the key parser as garbage.
                pass


def _trailing_partial(buf, introducer):
    """How many trailing bytes of `buf` could still grow into `introducer`."""
    for size in range(len(introducer) - 1, 0, -1):
        if buf.endswith(introducer[:size]):
            return size
    return 0


def _find_final_byte(data):
    """Index of the first CSI final byte (0x40-0x7E) in `data`, or None."""
    for index, byte in enumerate(data):
        if 0x40 <= byte <= 0x7E:
            return index
    return None


def _first_int(params, default=None):
    text = params.decode("ascii", "ignore").split(";")[0].split(":")[0]
    try:
        return int(text)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Event encoding
# --------------------------------------------------------------------------

PRESS = "press"
REPEAT = "repeat"
RELEASE = "release"

_EVENT_TYPES = {1: PRESS, 2: REPEAT, 3: RELEASE}

MOD_SHIFT = 0b1
MOD_ALT = 0b10
MOD_CTRL = 0b100
MOD_SUPER = 0b1000
MOD_HYPER = 0b10000
MOD_META = 0b100000
MOD_CAPS_LOCK = 0b1000000
MOD_NUM_LOCK = 0b10000000

#: Functional keys that keep a legacy CSI final letter rather than 'u'.
_FINAL_LETTER_KEYS = {
    "A": "UP",
    "B": "DOWN",
    "C": "RIGHT",
    "D": "LEFT",
    "E": "KP_BEGIN",
    "F": "END",
    "H": "HOME",
    "P": "F1",
    "Q": "F2",
    "S": "F4",
}

#: ``CSI <number> ~`` functional keys.
_TILDE_KEYS = {
    2: "INSERT",
    3: "DELETE",
    5: "PAGE_UP",
    6: "PAGE_DOWN",
    11: "F1",
    12: "F2",
    13: "F3",
    14: "F4",
    15: "F5",
    17: "F6",
    18: "F7",
    19: "F8",
    20: "F9",
    21: "F10",
    23: "F11",
    24: "F12",
}

#: Unicode code points kitty assigns to keys with no text of their own --
#: the plain ASCII ones plus the private-use range for the rest.
_NAMED_CODEPOINTS = {
    9: "TAB",
    13: "ENTER",
    27: "ESC",
    32: "SPACE",
    127: "BACKSPACE",
    57358: "CAPS_LOCK",
    57399: "KP_0",
    57441: "LEFT_SHIFT",
    57442: "LEFT_CONTROL",
    57443: "LEFT_ALT",
    57444: "LEFT_SUPER",
    57447: "RIGHT_SHIFT",
    57448: "RIGHT_CONTROL",
    57449: "RIGHT_ALT",
    57450: "RIGHT_SUPER",
}

#: One decoded key event.
#:
#: ``key``        -- normalised token: a single lowercase character for an
#:                   ordinary text key ("a", "5", ";"), or an upper-case
#:                   name ("UP", "ESC", "SPACE", "F5", "LEFT_SHIFT").
#:                   Deliberately *layout position*, not what was typed:
#:                   a QWERTY piano binds physical keys, and a release
#:                   event must produce the same token as its own press
#:                   even if Shift was let go in between.
#: ``event``      -- PRESS / REPEAT / RELEASE.
#: ``mods``       -- modifier bitmask (MOD_* above), 0 for none.
#: ``text``       -- the text the terminal says this keystroke produced
#:                   (FLAG_ASSOCIATED_TEXT), or "" when there is none.
#: ``codepoint``  -- the raw key code, for anything this table missed.
KeyEvent = namedtuple("KeyEvent", "key event mods text codepoint")


def parse_key_event(param_bytes, final_byte):
    """Pure: one kitty CSI key event -> a KeyEvent, or None if this is not
    a key event at all.

    `param_bytes` is everything between ``ESC [`` and the final byte;
    `final_byte` is that byte. Both are str, matching the shape
    `main._parse_csi_params()` already reads them in -- this function is
    the drop-in generalisation of it.

    Grammar (kitty protocol, "The full escape code")::

        CSI <key>[:<shifted>[:<base>]] ; <mods>[:<event>] ; <text codepoints> u

    Every field except the first is optional, and a missing field means its
    default (mods=1 i.e. none, event=1 i.e. press) -- so the legacy
    unmodified sequences this app already parses are the degenerate case of
    the same grammar, which is why the existing arrow handling survives
    unchanged.
    """
    if final_byte not in "u~ABCDEFHPQS":
        return None
    fields = param_bytes.split(";") if param_bytes else []
    key_field = fields[0] if len(fields) > 0 else ""
    mod_field = fields[1] if len(fields) > 1 else ""
    text_field = fields[2] if len(fields) > 2 else ""

    key_parts = key_field.split(":")
    number = _as_int(key_parts[0], default=1)

    mod_parts = mod_field.split(":")
    mod_value = _as_int(mod_parts[0], default=1) or 1
    mods = mod_value - 1
    event_code = _as_int(mod_parts[1], default=1) if len(mod_parts) > 1 else 1
    event = _EVENT_TYPES.get(event_code)
    if event is None:
        return None

    text = "".join(
        chr(cp)
        for cp in (_as_int(part, default=None) for part in text_field.split(":"))
        if cp
    )

    if final_byte == "u":
        key = _key_name_for_codepoint(number)
    elif final_byte == "~":
        key = _TILDE_KEYS.get(number, "UNKNOWN_%d" % number)
    else:
        key = _FINAL_LETTER_KEYS.get(final_byte, "UNKNOWN_%s" % final_byte)
    return KeyEvent(key=key, event=event, mods=mods, text=text, codepoint=number)


def _key_name_for_codepoint(codepoint):
    named = _NAMED_CODEPOINTS.get(codepoint)
    if named:
        return named
    if 33 <= codepoint < 127:
        return chr(codepoint).lower()
    if codepoint >= 57344:
        return "FUNC_%d" % codepoint
    try:
        return chr(codepoint).lower()
    except ValueError:
        return "UNKNOWN_%d" % codepoint


def _as_int(text, default=None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def legacy_token(event):
    """The token `main.RawKeys.poll()` returns today for this same
    keystroke -- or None if today's poll() would have returned nothing.

    This is the whole backward-compatibility story: with FLAG_ALL_AS_ESCAPES
    pushed, an ordinary letter arrives as ``CSI 97;1:1u`` instead of the
    byte ``a``, which would silently break every existing caller
    (`_handle_source_key` and friends compare against single characters).
    Mapping back down here means callers see exactly what they see today,
    and only a caller that *asks* for `poll_event()` sees the richer stream.

    Rules, matched to today's behaviour:
    * releases and repeats produce nothing (today's terminal reports neither,
      except that OS auto-repeat arrives as an indistinguishable press --
      so a repeat maps to the same token a press does, preserving today's
      key-repeat feel in menus);
    * arrows map to "UP"/"DOWN"/"LEFT"/"RIGHT", Shift+Up/Down to
      "SHIFT_UP"/"SHIFT_DOWN" (issue #98's score-editor transpose);
    * a text key produces its associated text when the terminal supplied
      it, so Shift+a is "A" exactly as it is today.
    """
    if event.event == RELEASE:
        return None
    if event.key in ("UP", "DOWN"):
        if event.mods & MOD_SHIFT:
            return "SHIFT_" + event.key
        return event.key
    if event.key in ("LEFT", "RIGHT"):
        return event.key
    if event.key == "SPACE":
        return " "
    if event.key == "ENTER":
        return "\r"
    if event.key == "ESC":
        return "\x1b"
    if event.key == "TAB":
        return "\t"
    if event.key == "BACKSPACE":
        return "\x7f"
    if event.text:
        return event.text
    if len(event.key) == 1:
        return event.key
    return None


# --------------------------------------------------------------------------
# Held-note policies
# --------------------------------------------------------------------------

NOTE_ON = "note_on"
NOTE_OFF = "note_off"


class HeldKeys:
    """The real thing: press -> note_on, release -> note_off, repeat -> nothing.

    Auto-repeat is deliberately swallowed. On a terminal without the
    protocol a held key machine-guns presses; with event types it arrives
    tagged REPEAT, and a sustaining instrument wants exactly one note_on
    for one physical press. Several keys can be down at once -- that is the
    point -- so state is a set, and `apply()` returns the note events that
    set transition implies.
    """

    def __init__(self):
        self._held = set()

    @property
    def held(self):
        """Keys currently down, in press order-insensitive set form."""
        return set(self._held)

    def apply(self, event):
        """One KeyEvent in, a list of ``(NOTE_ON|NOTE_OFF, key)`` out."""
        if event.event == PRESS:
            if event.key in self._held:
                # A press for an already-held key: the terminal lost a
                # release (focus change, mode pushed mid-hold). Retrigger
                # rather than stacking two voices on one key.
                self._held.add(event.key)
                return [(NOTE_OFF, event.key), (NOTE_ON, event.key)]
            self._held.add(event.key)
            return [(NOTE_ON, event.key)]
        if event.event == REPEAT:
            if event.key not in self._held:
                # A repeat with no press seen -- the press was eaten during
                # negotiation. Treat it as the press.
                self._held.add(event.key)
                return [(NOTE_ON, event.key)]
            return []
        if event.event == RELEASE:
            if event.key in self._held:
                self._held.discard(event.key)
                return [(NOTE_OFF, event.key)]
            return []
        return []

    def release_all(self):
        """Every held key off, in sorted order for determinism. Called on
        focus-out, on leaving the view, and on any error path -- a stuck
        note is the single worst failure mode of a held-note instrument."""
        events = [(NOTE_OFF, key) for key in sorted(self._held)]
        self._held.clear()
        return events


class FixedDurationKeys:
    """The degraded path: no releases exist, so every note is a fixed length.

    Presses (including OS auto-repeat presses, which are indistinguishable
    from real ones here -- that *is* the limitation) start a note that ends
    `duration` seconds later. A further press of a still-sounding key
    extends its end time instead of retriggering, which makes a held key
    approximate a sustained note rather than machine-gunning; a genuine
    fast repeat of the same note is the cost, and is the honest price of
    the terminal reporting no releases.

    Time is injected, never read: `apply(event, now)` and `expire(now)`.
    """

    def __init__(self, duration=0.35):
        self.duration = duration
        self._deadlines = {}

    @property
    def held(self):
        return set(self._deadlines)

    def apply(self, event, now):
        if event.event == RELEASE:
            return []  # cannot happen on this path; ignored if it does
        deadline = now + self.duration
        if event.key in self._deadlines:
            self._deadlines[event.key] = deadline  # extend, don't retrigger
            return []
        self._deadlines[event.key] = deadline
        return [(NOTE_ON, event.key)]

    def expire(self, now):
        """Notes whose fixed duration has elapsed. Must be called from the
        render loop every frame -- unlike HeldKeys, this policy needs a
        clock tick to produce any note_off at all."""
        due = sorted(key for key, when in self._deadlines.items() if when <= now)
        for key in due:
            del self._deadlines[key]
        return [(NOTE_OFF, key) for key in due]

    def release_all(self):
        events = [(NOTE_OFF, key) for key in sorted(self._deadlines)]
        self._deadlines.clear()
        return events
