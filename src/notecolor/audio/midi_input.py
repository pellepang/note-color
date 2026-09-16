"""MIDI hardware input (issue #173, decision 72): raw-byte parsing, the
note-on/off/sustain/mod-wheel/pitch-bend dispatch policy, and the
`python-rtmidi`-backed device that feeds it.

Three layers, split so the two that matter most can be tested with no
device and no Qt event loop at all:

1. `parse_message()` -- raw MIDI bytes to a `MidiEvent`. Pure.
2. `MidiDispatcher` -- policy: sustain-pedal hold-over, and the
   `note_hold.NoteHoldGate` cross-source rule -- against a plain `sink`
   object (`note_on`/`note_off`/`pitch_bend`/`mod_wheel`). Pure; a test
   feeds it raw byte lists and asserts what the fake sink recorded.
3. `MidiInput` -- owns one `rtmidi.MidiIn` port, lazily, and calls
   `on_message(raw_bytes)` from RtMidi's own reader thread. This is the
   only layer that touches a real device or `python-rtmidi`, and the only
   one this session's lack of MIDI hardware leaves genuinely unverified.

## Where this lives in the process (issue #40's lifecycle question)

A third independently-lazy device, alongside `SessionState.
ensure_sound_engine()` (output) and `ensure_started()` (the mic/analysis
pipeline) -- opened only by whatever wants live MIDI, exactly the same
"a tool that only plays never opens the mic, nor vice versa" shape
CLAUDE.md already documents for those two. `SessionState.
ensure_midi_input()` is that lazy accessor for the session-based terminal
world.

**The Synth View's actual host has no `SessionState` to call that on.**
`visualnote studio` (`gui/app.py`'s `main()`) never constructs one at all
-- `gui/app.py.start_audio()` builds its own `SoundEngine` directly, the
same "this entry point is standalone, not session-based" shape
`docs/decisions/70-...md`'s module and CLAUDE.md's own "`virtualnote
replay --play` ... builds its own `SoundEngine`, since that entry point
constructs no `SessionState`" already establish. `gui/synth_view.SynthView`
mirrors that precedent for MIDI: it owns its own `MidiInput` instance,
opened lazily on the view's first `showEvent` (the same "on first show,
not on construction" convention that view already uses for
`_claim_voice_budget()`/`_apply_patch()`) and closed in `closeEvent` --
tied to the window's own lifetime (it is destroyed and rebuilt on every
close/reopen, `WA_DeleteOnClose`), not to the process's. A `tab`/score-
editor session and the arrangement window never ask for MIDI and so never
open a port, the same guarantee `ensure_sound_engine()` gives audio
output.

## Threading (decision 70, reused)

RtMidi's ALSA backend spins up its own reader pthread and calls the
registered Python callback synchronously from it -- a fifth native OS
thread, verified against `RtMidi.cpp` directly in
`docs/research/midi-hardware-input.md` §3, not assumed. That thread starts
at ordinary `SCHED_OTHER` priority, exactly where decision 70 found the
audio *output* callback thread starting before its fix -- so this reuses
`sound_engine._try_realtime_scheduling()` outright rather than
re-deriving the same trick: same call (`os.sched_setscheduler(0,
SCHED_FIFO, ...)`, `pid=0` meaning "the calling thread"), same
graceful-degradation contract (catch `PermissionError`/`OSError`, never
raise out of the callback, report once), attempted from inside this
module's own callback's first invocation for the same reason decision 70
gives: there is no Python `Thread` object for RtMidi's pthread to
configure ahead of time.

**Never let an exception escape the RtMidi callback.** A Python exception
escaping a `sounddevice` callback tears down that stream (decision 70 §3);
nothing in `python-rtmidi`'s own docs promises anything gentler for its
callback trampoline, so the same "this function truly never raises" rule
applies here, checked the same way -- a dedicated test per failure shape.

## Off the UI thread, onto it

`MidiInput` calls `on_message` from RtMidi's reader thread. Everything in
this file up to `on_message` is safe to call from any thread (pure
functions and a dispatcher with only its own private state) --
`MidiDispatcher.handle_message()` itself does not touch Qt or the engine
directly, only through `sink`, so *if* `sink` is Qt/engine-touching code,
the caller wiring this up (`gui/synth_view.py`) is responsible for
marshalling onto the Qt UI thread first (a queued Qt signal carrying the
raw bytes, decoded on the receiving end) rather than calling `sink`
methods straight from the reader thread. See that module for how.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: Standard MIDI status nibbles (channel messages; the channel is the low
#: nibble and is stripped by `_kind()`/`parse_message()`).
_NOTE_OFF = 0x80
_NOTE_ON = 0x90
_CONTROL_CHANGE = 0xB0
_PITCH_BEND = 0xE0

#: CC64, per the MIDI spec -- "damper pedal (sustain)". >= 64 is down.
SUSTAIN_CONTROLLER = 64
#: CC1 -- "modulation wheel or lever".
MOD_WHEEL_CONTROLLER = 1

#: Pitch bend's default MIDI-wide implied range is +/-2 semitones (200
#: cents); `oscillator.py`'s `fine` destination is declared +/-100 cents
#: (decision 56 §5's original range, unrelated to this ticket). Rather
#: than widen a released parameter's range to match a controller's
#: *typical* default (many controllers' actual bend range is host- or
#: patch-configurable and not knowable from the wire protocol at all --
#: RPN 0 sets it, and nothing here parses RPNs, a deliberate v1 scope cut),
#: v1 scales MIDI's full bend travel onto `fine`'s existing travel: a full
#: bend reaches +/-100 cents (one semitone), not +/-200. Named explicitly
#: rather than silently: a keyboard whose own bend LED/patch says "2
#: semitones" will feel like it reaches only one through this engine until
#: `fine`'s range is revisited (research's own flagged open question,
#: `docs/research/midi-hardware-input.md` §4).
PITCH_BEND_RANGE_CENTS = 100.0


@dataclass(frozen=True)
class MidiEvent:
    """One parsed MIDI channel-voice message. `kind` is one of
    `"note_on"`, `"note_off"`, `"control_change"`, `"pitch_bend"`, or
    `"other"` (system messages, and channel messages this project has no
    use for -- program change, aftertouch, deliberately not decoded
    further per the issue's scope). Fields not meaningful for a given
    `kind` are left at their default."""

    kind: str
    channel: int = 0
    note: Optional[int] = None
    #: 0..1, already normalized from the raw 0..127 byte -- the same
    #: convention `sound_engine.NoteOn.from_midi_velocity()` already uses.
    velocity: float = 0.0
    control: Optional[int] = None
    #: 0..1, normalized from the raw 0..127 CC value.
    value: float = 0.0
    #: -1..~+1 (16383/8192 - 1 at the very top of the range), normalized
    #: from the raw 14-bit pitch-bend value, 0 at center (8192).
    bend: float = 0.0


def parse_message(data):
    """Raw MIDI bytes (a `list`/`bytes`/`tuple` of ints, as `python-rtmidi`
    hands a callback) -> one `MidiEvent`, or `None` for an empty message.

    Pure -- no device, no state, no import of `rtmidi`. `python-rtmidi`'s
    own reader already expands running status and strips real-time bytes
    (per RtMidi's own parser), so a channel message here always starts
    with its own status byte; this function does not have to handle
    running status itself.

    A note-on with velocity 0 is MIDI's own long-standing convention for
    "note off" (lets a running-status stream send only note-on bytes for
    an entire phrase) -- normalized to `kind="note_off"` here, once, so
    nothing downstream has to remember the special case.
    """
    if not data:
        return None
    status = data[0]
    kind_byte = status & 0xF0
    channel = status & 0x0F
    b1 = data[1] if len(data) > 1 else 0
    b2 = data[2] if len(data) > 2 else 0

    if kind_byte == _NOTE_ON:
        if b2 == 0:
            return MidiEvent("note_off", channel, note=b1, velocity=0.0)
        return MidiEvent("note_on", channel, note=b1, velocity=b2 / 127.0)
    if kind_byte == _NOTE_OFF:
        return MidiEvent("note_off", channel, note=b1, velocity=b2 / 127.0)
    if kind_byte == _CONTROL_CHANGE:
        return MidiEvent("control_change", channel, control=b1, value=b2 / 127.0)
    if kind_byte == _PITCH_BEND:
        raw = (b2 << 7) | b1  # 14-bit, 0..16383, center 8192
        return MidiEvent("pitch_bend", channel, bend=(raw - 8192) / 8192.0)
    return MidiEvent("other", channel)


class MidiDispatcher:
    """Turns parsed MIDI events into calls on `sink`, applying sustain-hold
    and the cross-source `NoteHoldGate` rule.

    `sink` is duck-typed: `note_on(pitch, velocity)`, `note_off(pitch)`,
    and optionally `pitch_bend(bend)`/`mod_wheel(value)` (checked with
    `hasattr` and skipped silently if absent -- a fake sink in a test that
    only cares about notes need not implement either).

    `gate`/`source_label` are the `NoteHoldGate` and this dispatcher's own
    name on it (`"midi"` in the real wiring) -- omit `gate` (leave it
    `None`) to run with no cross-source awareness at all, which is exactly
    right for a test that only wants to check MIDI's own note/sustain
    logic in isolation.

    Channel is deliberately not filtered here -- "omni" (any channel
    plays), which is the simplest first-version answer to the channel
    question `docs/research/midi-hardware-input.md` §5 leaves for the UI;
    a per-channel filter is a constructor argument away (`channel=None`
    for omni, an int to restrict) if a future UI wants to expose one, but
    nothing here builds that UI.
    """

    def __init__(self, sink, gate=None, source_label="midi", channel=None):
        self.sink = sink
        self.gate = gate
        self.source_label = source_label
        self.channel = channel
        #: Every pitch this dispatcher's *sink* currently sounds because a
        #: physical key is down -- not the same set as "held by the pedal"
        #: (see `_sustained` below). Tracked so `reset()`/device-loss can
        #: release exactly the notes this dispatcher is responsible for.
        self._held = set()
        #: Physically released while the pedal was down -- ringing only
        #: because of the pedal, released for real the moment it lifts
        #: (unless re-struck first, which removes a pitch from this set
        #: back into `_held`).
        self._sustained = set()
        self._sustain_down = False

    # -- the parsed-event entry point -----------------------------------

    def handle_message(self, data):
        """Parse `data` and apply it. Never raises -- see the module
        docstring's "never let an exception escape the RtMidi callback"
        rule; this is the function that rule protects, since it is what
        a callback ultimately calls into."""
        try:
            event = parse_message(data)
            if event is None or (self.channel is not None and event.channel != self.channel):
                return
            self._apply(event)
        except Exception:
            # A malformed or unexpected message must not take the whole
            # input path down -- degrade to "this one message did
            # nothing," the same posture every other degradation path in
            # this ticket takes.
            pass

    def _apply(self, event):
        if event.kind == "note_on":
            self._note_on(event.note, event.velocity)
        elif event.kind == "note_off":
            self._note_off(event.note)
        elif event.kind == "control_change":
            self._control_change(event.control, event.value)
        elif event.kind == "pitch_bend":
            bend = getattr(self.sink, "pitch_bend", None)
            if bend is not None:
                bend(event.bend)

    # -- notes -------------------------------------------------------------

    def _note_on(self, pitch, velocity):
        # A re-strike of a pitch still ringing only because the pedal is
        # holding it: this is a fresh physical press, not a continuation,
        # so it leaves the sustained set and becomes an ordinary held note
        # again -- the pedal no longer has anything to do when it lifts
        # for this pitch, because a real key is down now.
        self._sustained.discard(pitch)
        self._held.add(pitch)
        if self.gate is not None:
            self.gate.press(pitch, self.source_label)
        self.sink.note_on(pitch, velocity)

    def _note_off(self, pitch):
        self._held.discard(pitch)
        if self._sustain_down:
            # Don't release yet -- the pedal is down. The note keeps
            # ringing (whatever is already sounding it keeps sounding);
            # nothing to tell the gate, because this source has not
            # actually let go from the gate's point of view until the
            # pedal lifts.
            self._sustained.add(pitch)
            return
        self._release_now(pitch)

    def _release_now(self, pitch):
        if self.gate is not None:
            if not self.gate.release(pitch, self.source_label):
                return  # another source is still holding this pitch
        self.sink.note_off(pitch)

    # -- controllers ---------------------------------------------------

    def _control_change(self, control, value):
        if control == SUSTAIN_CONTROLLER:
            down = value >= (64 / 127.0)
            if down and not self._sustain_down:
                self._sustain_down = True
            elif not down and self._sustain_down:
                self._sustain_down = False
                for pitch in list(self._sustained):
                    self._sustained.discard(pitch)
                    self._release_now(pitch)
            return
        if control == MOD_WHEEL_CONTROLLER:
            mod_wheel = getattr(self.sink, "mod_wheel", None)
            if mod_wheel is not None:
                mod_wheel(value)

    # -- cleanup -------------------------------------------------------

    def reset(self):
        """Releases every pitch this dispatcher is holding (physically, or
        only via the pedal) without waiting for a note-off that may never
        arrive -- the device-unplugged, window-closed, or panic path.
        Idempotent: a second call finds nothing left to release."""
        self._sustain_down = False
        pending = set(self._held) | set(self._sustained)
        self._held.clear()
        self._sustained.clear()
        for pitch in pending:
            self._release_now(pitch)


# ---------------------------------------------------------------------------
# The device (issue #40's third lazy lifecycle, decision 70's thread reused)
# ---------------------------------------------------------------------------


def rtmidi_available():
    """Whether `python-rtmidi` can be imported at all, without importing it
    into the caller's namespace -- for a status line that wants to say
    "not installed" before ever trying to open a port."""
    try:
        import rtmidi  # noqa: F401
    except ImportError:
        return False
    return True


class MidiInput:
    """Owns at most one `rtmidi.MidiIn` port for as long as its caller
    keeps it around. `ensure_started()` is idempotent and lazy, mirroring
    `sound_engine.SoundEngine.ensure_started()`'s own shape exactly -- see
    the module docstring for why this project has two independent owners
    of one rather than routing everything through `SessionState`.

    Never raises. Every failure mode collapses to `available=False`
    (already `None` means "not attempted yet"), a one-line
    `status_message` (the same `print(f"[midi] ...")` convention
    `sound_engine.py`/`audio_capture.py` already use for their own status
    lines), and a synth that behaves exactly as it did before this file
    existed -- no MIDI hardware means no change in behaviour at all,
    which is the one hard requirement the issue names.
    """

    def __init__(self, on_message=None, on_callback_error=None, port_name=None):
        #: Called with the raw message byte list, from RtMidi's own reader
        #: thread -- see the module docstring's "off the UI thread, onto
        #: it" section for why a caller touching Qt/the engine must not
        #: call into either directly here.
        self.on_message = on_message
        #: Called (with no arguments) whenever `_callback` swallows an
        #: exception -- a status-bar hook, never required.
        self.on_callback_error = on_callback_error
        #: A specific port name to prefer on `ensure_started()`, or None
        #: for "the first port available". Device selection beyond this is
        #: named but not designed by the research (§5) and stays that way
        #: here -- `list_ports()` exists so a future picker has something
        #: to show.
        self._wanted_port_name = port_name
        self._midi_in = None
        #: None: never attempted. True/False: attempted, with the reason
        #: in `status_message`.
        self.available = None
        self.status_message = ""
        self.port_name = None
        self.callback_error_count = 0
        #: Same shape as `sound_engine.SoundEngine.realtime_priority_active`
        #: -- None until the callback's first real invocation.
        self.realtime_priority_active = None
        self.realtime_priority_message = ""

    # -- device enumeration ----------------------------------------------

    def list_ports(self):
        """A snapshot of currently visible MIDI input port names, or `[]`
        if `python-rtmidi` is not installed or the scan itself fails --
        both silent, not exceptions: a device picker calling this in a
        loop for hot-plug polling (research §5's recommended approach;
        not built here, this just does not get in its way) must never
        crash because a device vanished between calls."""
        try:
            import rtmidi
        except ImportError:
            return []
        try:
            probe = rtmidi.MidiIn()
            ports = list(probe.get_ports())
            del probe
            return ports
        except Exception:
            return []

    # -- lifecycle ---------------------------------------------------------

    def ensure_started(self):
        """Opens a port if one isn't open yet. Idempotent."""
        if self._midi_in is not None:
            return
        try:
            import rtmidi
        except ImportError:
            self.available = False
            self.status_message = (
                "python-rtmidi not installed -- MIDI input unavailable "
                "(pip install 'note-color[midi]')"
            )
            print(f"[midi] {self.status_message}")
            return
        try:
            midi_in = rtmidi.MidiIn()
            ports = list(midi_in.get_ports())
            if not ports:
                self.available = False
                self.status_message = "no MIDI input device found"
                print(f"[midi] {self.status_message}")
                return
            index = self._resolve_port(ports)
            midi_in.open_port(index)
            # Real-time/sysex/active-sense bytes have no destination in
            # this project's vocabulary (parse_message() has no case for
            # them anyway) -- ignored at the source so RtMidi's own queue
            # never fills with bytes nothing reads.
            midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
            midi_in.set_callback(self._callback)
            self._midi_in = midi_in
            self.port_name = ports[index]
            self.available = True
            self.status_message = f"MIDI input: {self.port_name}"
            print(f"[midi] {self.status_message}")
        except Exception as exc:
            # Deliberately broad -- see sound_engine.SoundEngine's own
            # ensure_started() convention and gui/app.py's start_audio():
            # a permissions error, a device that vanished between
            # get_ports() and open_port(), or anything else RtMidi's C++
            # side can raise all mean the same thing to the user, "no
            # MIDI this session," never a crash.
            self._midi_in = None
            self.available = False
            self.status_message = f"MIDI input unavailable ({exc})"
            print(f"[midi] {self.status_message}")

    def _resolve_port(self, ports):
        if self._wanted_port_name is not None:
            for i, name in enumerate(ports):
                if name == self._wanted_port_name:
                    return i
        return 0

    def stop(self):
        """Closes the port, if one is open. Idempotent and safe before any
        `ensure_started()`. Does not itself release any held notes --
        that is `MidiDispatcher.reset()`'s job, and the caller (holding
        both) is what sequences "stop taking new messages" before "let go
        of whatever this device was holding"."""
        if self._midi_in is not None:
            self._midi_in.close_port()
            self._midi_in = None
        self.available = None
        self.status_message = ""
        self.port_name = None
        self.realtime_priority_active = None
        self.realtime_priority_message = ""

    @property
    def started(self):
        return self._midi_in is not None

    # -- the callback --------------------------------------------------

    def _ensure_realtime_priority(self):
        """Decision 70's exact mechanism, reused rather than re-derived --
        see the module docstring's "threading" section. Idempotent, same
        shape as `SoundEngine._ensure_realtime_priority()`."""
        if self.realtime_priority_active is not None:
            return
        from notecolor.audio.sound_engine import _try_realtime_scheduling

        self.realtime_priority_active, self.realtime_priority_message = (
            _try_realtime_scheduling()
        )
        if self.realtime_priority_message:
            print(f"[midi] {self.realtime_priority_message}")

    def _callback(self, event, _data=None):
        """`python-rtmidi`'s callback shape: `event` is `(message, delta_time)`.
        Must never raise -- see the module docstring."""
        try:
            self._ensure_realtime_priority()
            message, _delta_time = event
            if self.on_message is not None and message:
                self.on_message(list(message))
        except Exception:
            self.callback_error_count += 1
            if self.on_callback_error is not None:
                try:
                    self.on_callback_error()
                except Exception:
                    pass
