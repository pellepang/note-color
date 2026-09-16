"""Tests for MIDI hardware input (issue #173, decision 72):
`audio/midi_input.py`'s raw-byte parser, the note/sustain/two-source
dispatch policy, and `MidiInput`'s device lifecycle -- every degradation
path a user with no MIDI hardware (or a device that misbehaves) can hit.

No MIDI hardware and no `python-rtmidi` device is opened anywhere in this
file. `MidiDispatcher`'s tests feed it raw byte lists directly against a
plain fake sink -- exactly the "pure logic unit-tested, real I/O
smoke-tested" split this repo's own audio tests already use
(`test_sound_engine.py`'s own docstring). `MidiInput`'s tests either call
its `_callback()` directly (mirroring `test_sound_engine.py`'s
`engine._callback(...)` convention) or substitute a fake `rtmidi` module
into `sys.modules` so `ensure_started()`'s real code path runs against
something that behaves like the library without needing it installed or
a device plugged in.
"""

import sys

import pytest

from notecolor.audio import midi_input as mi
from notecolor.audio.midi_input import (
    MOD_WHEEL_CONTROLLER, SUSTAIN_CONTROLLER, MidiDispatcher, MidiEvent, MidiInput,
    parse_message, rtmidi_available,
)
from notecolor.audio.note_hold import NoteHoldGate


# -- parse_message(): pure, no device -----------------------------------------

def test_empty_message_parses_to_none():
    assert parse_message([]) is None
    assert parse_message(None) is None


def test_note_on_parses_with_normalized_velocity():
    event = parse_message([0x90, 60, 100])
    assert event == MidiEvent("note_on", channel=0, note=60, velocity=100 / 127.0)


def test_note_on_with_zero_velocity_is_a_note_off():
    """The long-standing MIDI convention: note-on velocity 0 means
    note-off, so a running-status stream never needs to send the 0x80
    status byte at all."""
    event = parse_message([0x90, 60, 0])
    assert event.kind == "note_off"
    assert event.note == 60


def test_note_off_parses():
    event = parse_message([0x80, 60, 64])
    assert event == MidiEvent("note_off", channel=0, note=60, velocity=64 / 127.0)


def test_channel_is_the_low_nibble():
    event = parse_message([0x91, 60, 100])  # note-on, channel 1
    assert event.channel == 1


def test_control_change_parses_with_normalized_value():
    event = parse_message([0xB0, 64, 127])
    assert event == MidiEvent("control_change", channel=0, control=64, value=1.0)


def test_pitch_bend_center_is_zero():
    event = parse_message([0xE0, 0, 64])  # 64 << 7 | 0 = 8192, the center
    assert event.kind == "pitch_bend"
    assert event.bend == pytest.approx(0.0)


def test_pitch_bend_extremes():
    low = parse_message([0xE0, 0, 0])
    high = parse_message([0xE0, 0x7F, 0x7F])
    assert low.bend == pytest.approx(-1.0)
    assert high.bend == pytest.approx((16383 - 8192) / 8192.0)


def test_unrecognised_status_byte_becomes_other_rather_than_raising():
    event = parse_message([0xF8])  # a real-time clock byte, not decoded
    assert event.kind == "other"


def test_a_short_message_does_not_index_error():
    """A note-on with only the status byte (truncated, or a device that
    sends malformed data) must not raise -- missing bytes default to 0."""
    event = parse_message([0x90])
    assert event.kind == "note_on" or event.kind == "note_off"


# -- MidiDispatcher: notes, velocity ------------------------------------------

class FakeSink:
    def __init__(self):
        self.log = []

    def note_on(self, pitch, velocity):
        self.log.append(("on", pitch, velocity))

    def note_off(self, pitch):
        self.log.append(("off", pitch))

    def pitch_bend(self, bend):
        self.log.append(("bend", bend))

    def mod_wheel(self, value):
        self.log.append(("mod", value))


class BareSink:
    """A sink implementing only note_on/note_off -- no pitch_bend/mod_wheel
    at all, the shape a test that only cares about notes can use, and the
    shape `MidiDispatcher` must not crash against."""

    def __init__(self):
        self.log = []

    def note_on(self, pitch, velocity):
        self.log.append(("on", pitch, velocity))

    def note_off(self, pitch):
        self.log.append(("off", pitch))


def test_note_on_off_reach_the_sink_with_real_velocity():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x90, 60, 100])
    d.handle_message([0x80, 60, 0])
    assert sink.log == [("on", 60, pytest.approx(100 / 127.0)), ("off", 60)]


def test_a_note_off_with_no_matching_note_on_reaches_the_sink_harmlessly():
    """A spurious/duplicate note-off (no `_held` bookkeeping stops it) is
    simply forwarded -- exactly as harmless downstream as it already is at
    the engine layer (`poly.PolyGraph.note_off()`/`VoiceManager.note_off()`
    both no-op on a pitch nothing is sounding), so there is nothing here
    worth refusing to forward."""
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x80, 60, 0])  # never pressed
    assert sink.log == [("off", 60)]


def test_pitch_bend_and_mod_wheel_reach_a_sink_that_offers_them():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0xE0, 0, 0])
    d.handle_message([0xB0, MOD_WHEEL_CONTROLLER, 127])
    assert sink.log == [("bend", pytest.approx(-1.0)), ("mod", pytest.approx(1.0))]


def test_pitch_bend_against_a_sink_with_no_such_method_does_not_raise():
    sink = BareSink()
    d = MidiDispatcher(sink)
    d.handle_message([0xE0, 0, 0])
    d.handle_message([0xB0, MOD_WHEEL_CONTROLLER, 127])
    d.handle_message([0x90, 60, 100])
    assert sink.log == [("on", 60, pytest.approx(100 / 127.0))]


def test_omni_by_default_any_channel_plays():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x93, 60, 100])  # channel 3
    assert sink.log == [("on", 60, pytest.approx(100 / 127.0))]


def test_a_specific_channel_filters_out_every_other_channel():
    sink = FakeSink()
    d = MidiDispatcher(sink, channel=2)
    d.handle_message([0x90, 60, 100])   # channel 0 -- filtered out
    d.handle_message([0x92, 61, 100])   # channel 2 -- passes
    assert sink.log == [("on", 61, pytest.approx(100 / 127.0))]


# -- sustain pedal (CC64) ------------------------------------------------------

def test_sustain_holds_a_note_off_until_the_pedal_lifts():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x90, 64, 100])
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 127])   # pedal down
    d.handle_message([0x80, 64, 0])                      # key up -- held
    assert sink.log == [("on", 64, pytest.approx(100 / 127.0))]
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 0])      # pedal up -- releases
    assert sink.log[-1] == ("off", 64)


def test_sustain_threshold_is_64_per_the_midi_spec():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x90, 64, 100])
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 63])   # just below the threshold: pedal up
    d.handle_message([0x80, 64, 0])
    assert sink.log[-1] == ("off", 64)   # released immediately, pedal was never "down"


def test_re_striking_a_sustained_note_takes_it_out_of_the_sustained_set():
    """A held-over note that gets physically re-pressed is a live note
    again -- releasing the pedal afterwards must not double-release it or
    release a note nothing is holding any more."""
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x90, 64, 100])
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 127])
    d.handle_message([0x80, 64, 0])    # sustained
    d.handle_message([0x90, 64, 90])   # re-struck while still ringing
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 0])   # pedal up
    # No note-off from the pedal lift -- the key is physically down again.
    assert sink.log == [
        ("on", 64, pytest.approx(100 / 127.0)),
        ("on", 64, pytest.approx(90 / 127.0)),
    ]
    d.handle_message([0x80, 64, 0])   # now actually release it
    assert sink.log[-1] == ("off", 64)


def test_pedal_down_with_nothing_sounding_is_harmless():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 127])
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 0])
    assert sink.log == []


# -- two-source interaction: NoteHoldGate --------------------------------------

def test_a_pitch_held_by_two_sources_survives_one_releasing():
    """The scenario the issue names explicitly: the computer keyboard and
    a MIDI keyboard both play middle C; releasing MIDI's copy must not
    silence the keyboard's held copy."""
    gate = NoteHoldGate()
    midi_sink = FakeSink()
    d = MidiDispatcher(midi_sink, gate=gate, source_label="midi")

    gate.press(60, "keys")            # the computer keyboard is holding it
    d.handle_message([0x90, 60, 100])  # MIDI also plays it
    d.handle_message([0x80, 60, 0])    # MIDI lets go

    assert midi_sink.log == [("on", 60, pytest.approx(100 / 127.0))]
    # No note-off reached the sink: "keys" is still holding it.
    assert gate.release(60, "keys") is True   # now nothing holds it


def test_the_last_source_to_release_actually_releases():
    gate = NoteHoldGate()
    midi_sink = FakeSink()
    d = MidiDispatcher(midi_sink, gate=gate, source_label="midi")
    gate.press(60, "keys")
    d.handle_message([0x90, 60, 100])
    gate.release(60, "keys")           # keys lets go first
    d.handle_message([0x80, 60, 0])    # midi lets go last -- this one releases
    assert midi_sink.log[-1] == ("off", 60)


def test_note_hold_gate_release_of_an_untracked_pitch_is_permissive():
    gate = NoteHoldGate()
    assert gate.release(72, "keys") is True


def test_note_hold_gate_clear_source_reports_per_pitch_release():
    gate = NoteHoldGate()
    gate.press(60, "keys")
    gate.press(60, "midi")
    gate.press(61, "midi")
    results = dict(gate.clear_source("midi"))
    assert results[60] is False   # "keys" still holds 60
    assert results[61] is True    # nothing else held 61
    assert gate.held_by("midi") == []
    assert gate.held_by("keys") == [60]


# -- graceful cleanup: device loss / panic ------------------------------------

def test_reset_releases_every_pitch_this_dispatcher_holds():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x90, 60, 100])
    d.handle_message([0x90, 64, 100])
    d.handle_message([0xB0, SUSTAIN_CONTROLLER, 127])
    d.handle_message([0x80, 64, 0])   # sustained, not released
    d.reset()
    assert ("off", 60) in sink.log
    assert ("off", 64) in sink.log


def test_reset_does_not_release_a_pitch_still_held_by_the_other_source():
    gate = NoteHoldGate()
    midi_sink = FakeSink()
    d = MidiDispatcher(midi_sink, gate=gate, source_label="midi")
    gate.press(60, "keys")
    d.handle_message([0x90, 60, 100])
    d.reset()   # e.g. device unplugged mid-play
    assert ("off", 60) not in midi_sink.log   # "keys" is still holding it


def test_reset_is_idempotent():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([0x90, 60, 100])
    d.reset()
    before = list(sink.log)
    d.reset()
    assert sink.log == before   # nothing new happened


def test_a_malformed_message_does_not_crash_the_dispatcher():
    sink = FakeSink()
    d = MidiDispatcher(sink)
    d.handle_message([])
    d.handle_message([0x90])
    d.handle_message(None)


# -- MidiInput: device lifecycle and every degradation path -------------------

def test_rtmidi_not_installed_reports_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", None)   # forces ImportError
    m = MidiInput()
    m.ensure_started()
    assert m.available is False
    assert "not installed" in m.status_message
    assert m.started is False


def test_rtmidi_availability_probe_matches_import_success(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", None)
    assert rtmidi_available() is False


class FakeRtMidiIn:
    """A `rtmidi.MidiIn` stand-in -- class attributes are the test's knobs,
    reset by the `fake_rtmidi` fixture before every test that uses it."""

    ports = ["USB MIDI Keyboard"]
    open_raises = None

    def __init__(self):
        self.callback = None
        self.opened_index = None
        self.closed = False

    def get_ports(self):
        if isinstance(self.ports, Exception):
            raise self.ports
        return list(self.ports)

    def open_port(self, index):
        if FakeRtMidiIn.open_raises is not None:
            raise FakeRtMidiIn.open_raises
        self.opened_index = index

    def ignore_types(self, **kwargs):
        pass

    def set_callback(self, callback, data=None):
        self.callback = callback

    def close_port(self):
        self.closed = True


class FakeRtMidiModule:
    MidiIn = FakeRtMidiIn


@pytest.fixture
def fake_rtmidi(monkeypatch):
    FakeRtMidiIn.ports = ["USB MIDI Keyboard"]
    FakeRtMidiIn.open_raises = None
    monkeypatch.setitem(sys.modules, "rtmidi", FakeRtMidiModule())
    yield FakeRtMidiIn


def test_no_ports_found_reports_unavailable(fake_rtmidi):
    fake_rtmidi.ports = []
    m = MidiInput()
    m.ensure_started()
    assert m.available is False
    assert "no MIDI input device found" in m.status_message


def test_a_port_opens_and_is_reported_by_name(fake_rtmidi):
    m = MidiInput()
    m.ensure_started()
    assert m.available is True
    assert m.port_name == "USB MIDI Keyboard"
    assert m.started is True


def test_permission_denied_on_open_degrades_rather_than_raising(fake_rtmidi):
    fake_rtmidi.open_raises = PermissionError("Operation not permitted")
    m = MidiInput()
    m.ensure_started()
    assert m.available is False
    assert "unavailable" in m.status_message
    assert m.started is False


def test_an_unexpected_error_scanning_ports_also_degrades(fake_rtmidi):
    fake_rtmidi.ports = RuntimeError("ALSA seq: no such device")
    m = MidiInput()
    m.ensure_started()
    assert m.available is False


def test_ensure_started_is_idempotent(fake_rtmidi):
    m = MidiInput()
    m.ensure_started()
    m.ensure_started()   # must not reopen or raise
    assert m.started is True


def test_stop_closes_the_port_and_resets_status(fake_rtmidi):
    m = MidiInput()
    m.ensure_started()
    m.stop()
    assert m.started is False
    assert m.available is None
    assert m.port_name is None


def test_stop_before_ever_starting_is_a_safe_no_op():
    m = MidiInput()
    m.stop()   # must not raise
    assert m.started is False


def test_list_ports_returns_a_snapshot(fake_rtmidi):
    m = MidiInput()
    assert m.list_ports() == ["USB MIDI Keyboard"]


def test_list_ports_with_no_rtmidi_returns_empty_rather_than_raising(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", None)
    m = MidiInput()
    assert m.list_ports() == []


def test_the_callback_delivers_raw_bytes_to_on_message(fake_rtmidi):
    received = []
    m = MidiInput(on_message=received.append)
    m.ensure_started()
    m._callback(([0x90, 60, 100], 0.0))
    assert received == [[0x90, 60, 100]]


def test_the_callback_never_raises_even_if_on_message_does():
    def boom(_message):
        raise RuntimeError("a bug in dispatch")

    errors = []
    m = MidiInput(on_message=boom, on_callback_error=lambda: errors.append(1))
    m._callback(([0x90, 60, 100], 0.0))   # must not raise
    assert m.callback_error_count == 1
    assert errors == [1]


def test_the_callback_reuses_decision_70s_realtime_scheduling_mechanism(monkeypatch):
    """Same mechanism as `sound_engine._try_realtime_scheduling()`, reused
    rather than re-derived -- see the module docstring. Denied here for
    the same reason it is denied on this dev machine (RLIMIT_RTPRIO==0)."""
    from notecolor.audio import sound_engine

    calls = []

    def deny(pid, policy, param):
        calls.append((pid, policy, param))
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(sound_engine.os, "sched_setscheduler", deny)
    m = MidiInput()
    m._callback(([0x90, 60, 100], 0.0))
    assert m.realtime_priority_active is False
    assert "normal priority" in m.realtime_priority_message
    # Idempotent: a second callback does not ask again -- attempted once
    # per stream lifetime, same as SoundEngine's own.
    m._callback(([0x91, 61, 100], 0.0))
    assert len(calls) == 1
