"""Wiring the transport's record button to a Synth-view take, bounced to a
new track (ticket #159) -- `StudioWindow._start_synth_recording()` /
`._stop_synth_recording()`, headless, no real audio device or thread.

The background render/import/AddTrack hop (`gui/synth_recording.start_bounce()`)
is stubbed to run synchronously, per this repo's existing convention for
testing background work without spinning a real thread.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
scipy_signal = pytest.importorskip("scipy.signal")

from notecolor.audio.transport import Transport  # noqa: E402
from notecolor.project import bundle  # noqa: E402
from notecolor.project.model import (  # noqa: E402
    AUDIO_TRACK, Project, TempoAnchor, TempoMap,
)


@pytest.fixture(scope="module")
def app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeSynthEngine:
    """Stands in for `synth_engine.SynthEngine`: only `patch_for()` matters
    to the recording path."""

    def __init__(self):
        from notecolor.audio.synth_engine import default_patch

        self.patch = default_patch()

    def patch_for(self, name):
        return self.patch


class FakeSoundEngine:
    """Stands in for `sound_engine.SoundEngine`: `.engine` is the voice
    engine (`FakeSynthEngine`), `.sample_rate` is read by
    `_stop_synth_recording()`."""

    def __init__(self):
        self.engine = FakeSynthEngine()
        self.sample_rate = 44100


class FakePlayer:
    def __init__(self):
        self.engine = FakeSoundEngine()

    def refresh(self):
        pass


def _pump(window, blocks=4):
    """Transport commands (including `locate()`) apply at a block boundary
    -- same helper `test_studio_window.py` uses."""
    for _ in range(blocks):
        window.transport.process_block(512)


def _window(app, tmp_path, player=True):
    from notecolor.gui.studio import StudioWindow

    project = Project(name="T", tempo_map=TempoMap([TempoAnchor(0.0, 120.0)]))
    path = bundle.save_project(project, tmp_path / "Demo")
    win = StudioWindow(project, transport=Transport(project.tempo_map, sample_rate=44100),
                       player=FakePlayer() if player else None, path=path)
    win.timer.stop()
    return win


def _run_bounce_synchronously(monkeypatch):
    """Replaces `synth_recording.start_bounce()` with a synchronous call to
    the same worker logic, per the ticket's own suggested test approach:
    run the background function directly rather than spinning a real
    `QThread`. Returns the list of `on_done`/`on_error` calls made."""
    from notecolor.gui import studio as studio_module
    from notecolor.audio import synth_bounce

    calls = []

    def fake_start_bounce(owner, bundle_path, project, events, patch, sample_rate,
                          start_beat, on_done, on_error=None, track_name=None):
        try:
            command = synth_bounce.bounce_take_to_track(
                bundle_path, project, events, patch, sample_rate, start_beat,
                track_name=track_name)
        except Exception as exc:  # noqa: BLE001
            calls.append(("error", str(exc)))
            if on_error:
                on_error(str(exc))
        else:
            calls.append(("done", command))
            on_done(command)
        return None, None

    monkeypatch.setattr(studio_module.synth_recording, "start_bounce", fake_start_bounce)
    return calls


# --- arming / disarming ------------------------------------------------


def test_record_with_no_saved_path_refuses(app, tmp_path):
    win = _window(app, tmp_path)
    win.path = None
    win._transport_action("record")
    assert "save the project" in win._status
    assert win._synth_take is None


def test_clicking_record_arms_a_take_and_lights_the_button(app, tmp_path):
    win = _window(app, tmp_path)
    win._transport_action("record")
    assert win._synth_take is not None
    assert win.transport_bar.recording is True
    assert win._synth_take.patch is win.player.engine.engine.patch


def test_a_take_is_stamped_with_the_transports_beat_at_record_start(app, tmp_path):
    win = _window(app, tmp_path)
    win.transport.locate(4.0)
    _pump(win)
    win._transport_action("record")
    assert win._synth_take.start_beat == pytest.approx(4.0)


def test_clicking_record_again_disarms_and_reports_nothing_recorded(app, tmp_path):
    win = _window(app, tmp_path)
    win._transport_action("record")
    win._transport_action("record")
    assert win._synth_take is None
    assert win.transport_bar.recording is False
    assert "nothing recorded" in win._status


# --- a real take: capture -> offline bounce -> undoable AddTrack -----------


def test_a_played_take_bounces_to_a_new_undoable_audio_track(app, tmp_path, monkeypatch):
    calls = _run_bounce_synchronously(monkeypatch)
    win = _window(app, tmp_path)
    win.transport.locate(2.0)
    _pump(win)

    win._transport_action("record")
    take = win._synth_take
    take.note_on(60, velocity=0.9, now=0.0)
    take.note_off(60, now=0.2)
    win._transport_action("record")   # stop -> bounces synchronously

    assert calls and calls[0][0] == "done"
    assert len(win.project.tracks) == 1
    track = win.project.tracks[0]
    assert track.kind == AUDIO_TRACK
    assert track.clips[0].start_beat == pytest.approx(2.0)
    assert "recorded" in win._status

    # Undoable, like every other project edit (#159's own requirement).
    win.undo()
    assert win.project.tracks == []
    win.redo()
    assert len(win.project.tracks) == 1


def test_a_bounce_failure_is_reported_and_does_not_touch_the_project(app, tmp_path, monkeypatch):
    from notecolor.audio import synth_bounce as synth_bounce_module

    calls = _run_bounce_synchronously(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(synth_bounce_module, "bounce_take_to_track", boom)

    win = _window(app, tmp_path)
    win._transport_action("record")
    win._synth_take.note_on(60, now=0.0)
    win._synth_take.note_off(60, now=0.1)
    win._transport_action("record")

    assert calls and calls[0][0] == "error"
    assert win.project.tracks == []
    assert "recording failed" in win._status
