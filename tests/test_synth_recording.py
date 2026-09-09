"""`gui/synth_recording.py`'s background-thread-to-GUI-thread hop (#159).

Runs a real `QThread` (joined with `wait()`, not polled) rather than
stubbing it out entirely -- this file's whole point is to prove the thread
plumbing itself works; `test_studio_synth_recording.py` is the one that
stubs it out to test `StudioWindow`'s own logic in isolation.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
scipy_signal = pytest.importorskip("scipy.signal")

from PySide6 import QtWidgets  # noqa: E402

from notecolor.audio.synth_engine import default_patch  # noqa: E402
from notecolor.gui import synth_recording  # noqa: E402
from notecolor.project import bundle  # noqa: E402
from notecolor.project.model import AUDIO_TRACK, Project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_start_bounce_dispatches_on_done_with_the_add_track_command(app, tmp_path):
    project = Project(name="T")
    path = bundle.save_project(project, tmp_path / "Demo")
    events = [{"t": 0.0, "pc": 0, "octave": 4, "duration_seconds": 0.1, "velocity": 100}]

    results = []
    owner = QtWidgets.QWidget()
    thread, worker = synth_recording.start_bounce(
        owner, path, project, events, default_patch(), 44100, start_beat=0.0,
        on_done=results.append)

    assert thread.wait(5000), "bounce thread did not finish in time"
    assert len(results) == 1
    command = results[0]
    assert command.track.kind == AUDIO_TRACK
    assert project.tracks == []   # start_bounce never applies the command itself


def test_start_bounce_reports_a_render_failure_via_on_error(app, tmp_path, monkeypatch):
    from notecolor.audio import synth_bounce

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(synth_bounce, "bounce_take_to_track", boom)

    project = Project(name="T")
    path = bundle.save_project(project, tmp_path / "Demo")
    done, errors = [], []
    owner = QtWidgets.QWidget()
    thread, worker = synth_recording.start_bounce(
        owner, path, project, [{"t": 0.0, "pc": 0, "octave": 4,
                               "duration_seconds": 0.1, "velocity": 100}],
        default_patch(), 44100, start_beat=0.0,
        on_done=done.append, on_error=errors.append)

    assert thread.wait(5000), "bounce thread did not finish in time"
    assert done == []
    assert errors == ["boom"]
