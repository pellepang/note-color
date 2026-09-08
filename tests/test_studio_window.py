"""The arrange window's behaviour, driven headlessly (map #145).

Qt widgets are exercised with the offscreen platform, no display and no audio
device -- the same "pure logic unit-tested, hardware smoke-tested" split this
repo applies to `audio_capture` and the terminal views. What is asserted here
is the *decisions* the window makes: what a click hits, where a seek lands,
what zoom preserves, when it refuses to lose work.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from notecolor.audio.transport import Transport  # noqa: E402
from notecolor.project.model import (  # noqa: E402
    Note, NoteClip, Project, TempoAnchor, TempoMap, TimeSignature, Track,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    from notecolor.gui.studio import StudioWindow

    project = Project(
        name="T",
        tempo_map=TempoMap([TempoAnchor(0.0, 120.0)]),
        tracks=[Track(name="a", clips=[NoteClip(length_beats=8,
                                                notes=[Note(0, 1, 60)])]),
                Track(name="b", clips=[NoteClip(length_beats=8,
                                                notes=[Note(0, 1, 67)])])])
    win = StudioWindow(project, transport=Transport(project.tempo_map,
                                                    sample_rate=48000))
    win.timer.stop()
    yield win
    win.deleteLater()


def _pump(window, blocks=4):
    """Commands apply at a block boundary, so a test has to run blocks."""
    for _ in range(blocks):
        window.transport.process_block(512)


# --- transport buttons -----------------------------------------------------


def test_every_painted_button_is_also_a_hit_target(window):
    """Drawing and hit-testing share one set of rectangles, so they cannot
    drift apart. This asserts the set is complete rather than that it exists."""
    rects = window.transport_bar._button_rects()
    assert set(rects) == {name for name, _g, _t in window.transport_bar.BUTTONS}
    for name, rect in rects.items():
        assert window.transport_bar._at(rect.center()) == name


def test_clicking_play_then_stop(window):
    window._transport_action("play")
    _pump(window)
    assert window.transport.snapshot().playing
    window._transport_action("stop")
    _pump(window)
    assert not window.transport.snapshot().playing


def test_rewind_stops_and_returns_to_the_start(window):
    window._transport_action("play")
    _pump(window, 200)
    assert window.transport.snapshot().beat > 0
    window._transport_action("rewind")
    _pump(window)
    assert window.transport.snapshot().beat == pytest.approx(0.0, abs=1e-3)
    assert not window.transport.snapshot().playing


def test_record_says_it_is_unimplemented_rather_than_doing_nothing(window):
    window._transport_action("record")
    assert "not implemented" in window._status


# --- seeking ---------------------------------------------------------------


def test_clicking_the_ruler_locates(window):
    window.ruler.located.emit(6.4)
    _pump(window)
    assert window.transport.snapshot().beat == pytest.approx(6.0, abs=1e-3)


def test_seeks_snap_to_the_beat_grid(window):
    assert window.snap(3.4) == 3.0
    assert window.snap(3.6) == 4.0


def test_zoomed_far_out_the_grid_becomes_a_bar(window):
    """Snapping to something too small to see is indistinguishable from a bug."""
    window.scene.px_per_beat = 6
    assert window.snap(2.4) == 4.0          # 4/4, so a bar is 4 beats


# --- loop ------------------------------------------------------------------


def test_dragging_the_ruler_sets_a_loop(window):
    window.ruler.loop_set.emit(2.0, 6.0)
    _pump(window)
    snapshot = window.transport.snapshot()
    assert snapshot.loop_enabled
    assert (snapshot.loop_start_beat, snapshot.loop_end_beat) == (2.0, 6.0)


def test_a_backwards_drag_means_the_same_as_a_forwards_one(window):
    """Order-independent, exactly as the tab view's `[`/`]` marks are."""
    window.ruler._drag_from = 8.0
    event = QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, QtCore.QPointF(2.0 * window.scene.px_per_beat, 5),
        QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
    window.ruler.mouseMoveEvent(event)
    assert window.ruler.loop == (2.0, 8.0)


def test_an_empty_loop_clears_rather_than_looping_zero_beats(window):
    window.ruler.loop_set.emit(4.0, 4.0)
    _pump(window)
    assert not window.transport.snapshot().loop_enabled
    assert "cleared" in window._status


def test_toggling_a_loop_needs_one_to_exist_and_says_so(window):
    window._toggle_loop()
    assert "drag" in window._status


# --- track headers ---------------------------------------------------------


def test_clicking_mute_toggles_it_undoably(window):
    window._toggle_track(0, "m")
    assert window.project.tracks[0].muted is True
    window.undo()
    assert window.project.tracks[0].muted is False


def test_mute_and_solo_have_separate_hit_targets(window):
    mute = window.headers._button_rect(0, 0)
    solo = window.headers._button_rect(0, 1)
    assert not mute.intersects(solo)


def test_a_track_edit_reaches_playback(window):
    """The player holds a flattened copy, so an edit that never reaches it is
    an edit you can see but not hear."""
    calls = []
    window.player = type("P", (), {"refresh": lambda self: calls.append(1)})()
    window._toggle_track(1, "m")
    assert calls == [1]


# --- zoom ------------------------------------------------------------------


def test_zoom_clamps_rather_than_wrapping(window):
    for _ in range(60):
        window.zoom(window.ZOOM_STEP)
    assert window.scene.px_per_beat == window.MAX_PX_PER_BEAT
    for _ in range(120):
        window.zoom(1 / window.ZOOM_STEP)
    assert window.scene.px_per_beat == window.MIN_PX_PER_BEAT


def test_zoom_keeps_the_anchor_beat_under_the_same_pixel(window):
    """Without this, zooming walks the music sideways out of view."""
    window.resize(900, 500)
    window.show()
    bar = window.view.horizontalScrollBar()
    bar.setValue(200)
    anchor = 9.0
    before = anchor * window.scene.px_per_beat - bar.value()
    window.zoom(window.ZOOM_STEP, anchor)
    after = anchor * window.scene.px_per_beat - window.view.horizontalScrollBar().value()
    assert after == pytest.approx(before, abs=2.0)


# --- files -----------------------------------------------------------------


def test_a_window_starts_clean(window):
    assert window.dirty is False


def test_an_edit_makes_it_dirty_and_saving_makes_it_clean(window, tmp_path):
    window._toggle_track(0, "m")
    assert window.dirty is True
    assert window.save(tmp_path / "p") is True
    assert window.dirty is False


def test_saving_writes_a_bundle_that_loads_back(window, tmp_path):
    window._toggle_track(1, "m")
    window.save(tmp_path / "p")

    from notecolor.project.bundle import load_project
    loaded = load_project(window.path)
    assert loaded.tracks[1].muted is True
    assert [t.name for t in loaded.tracks] == ["a", "b"]


def test_a_failed_save_reports_and_stays_dirty(window, tmp_path, monkeypatch):
    from notecolor.gui import studio

    def explode(*_args, **_kwargs):
        raise OSError("read-only file system")

    window._toggle_track(0, "m")
    monkeypatch.setattr(studio, "save_project", explode)
    assert window.save(tmp_path / "p") is False
    assert window.dirty is True
    assert "save failed" in window._status
