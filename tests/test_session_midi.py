"""`SessionState.ensure_midi_input()`/`stop()` (issue #173, decision 72):
the third independently-lazy device, mirroring `ensure_sound_engine()`.
No real device or `python-rtmidi` install needed -- a fake `rtmidi` module
substituted into `sys.modules`, same convention `test_midi_input.py` uses
for `MidiInput` itself. Constructing a bare `SessionState()` opens nothing
(only `ensure_*()` calls have side effects), so this needs no audio
capture or output device either.
"""

import sys

import pytest

from notecolor.audio.session import SessionState


class FakeRtMidiIn:
    def __init__(self):
        self.closed = False

    def get_ports(self):
        return ["Fake MIDI Port"]

    def open_port(self, index):
        pass

    def ignore_types(self, **kwargs):
        pass

    def set_callback(self, callback, data=None):
        pass

    def close_port(self):
        self.closed = True


class FakeRtMidiModule:
    MidiIn = FakeRtMidiIn


@pytest.fixture
def fake_rtmidi(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", FakeRtMidiModule())


def _session():
    return SessionState(color_scheme="default", sensitivity_value=1.0, source_value="mic")


def test_ensure_midi_input_is_idempotent(fake_rtmidi):
    session = _session()
    first = session.ensure_midi_input()
    second = session.ensure_midi_input()
    assert first is second
    assert first.started is True


def test_a_session_that_never_asks_for_midi_never_opens_a_port():
    session = _session()
    assert session.midi_input is None
    session.stop()   # must not raise, must not have opened anything
    assert session.midi_input is None


def test_no_rtmidi_installed_degrades_without_raising(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", None)
    session = _session()
    midi = session.ensure_midi_input()
    assert midi.available is False
    assert midi.started is False


def test_stop_closes_midi_input_if_it_was_ever_opened(fake_rtmidi):
    session = _session()
    midi = session.ensure_midi_input()
    session.stop()
    assert midi.started is False


def test_stop_before_ensure_midi_input_is_a_safe_no_op():
    session = _session()
    session.stop()   # capture was never opened either -- must return early, not raise
