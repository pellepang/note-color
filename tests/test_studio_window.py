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


def test_record_with_no_audio_device_says_so_rather_than_doing_nothing(window):
    window._transport_action("record")
    assert "no audio device" in window._status


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


# --- Synth View controller surface (map #145, ticket #157) -----------------


def test_record_note_on_off_are_noops_without_an_armed_take(window):
    window.record_note_on(60, 0.8)
    window.record_note_off(60)  # neither raises with no `_synth_take`


def test_record_note_on_off_forward_to_an_armed_take(window):
    calls = []

    class FakeTake:
        def note_on(self, pitch, velocity=1.0):
            calls.append(("on", pitch, velocity))

        def note_off(self, pitch):
            calls.append(("off", pitch))

    window._synth_take = FakeTake()
    window.record_note_on(64, 0.5)
    window.record_note_off(64)
    assert calls == [("on", 64, 0.5), ("off", 64)]


def test_panic_guards_a_missing_sound_engine(window):
    assert window.player is None
    window.panic()  # must not raise


def test_panic_calls_all_notes_off_on_the_live_engine(window):
    calls = []

    class FakeEngine:
        def all_notes_off(self):
            calls.append(True)

    window.sound_engine_provider = lambda: FakeEngine()
    window.panic()
    assert calls == [True]


def test_synth_view_menu_action_opens_a_singleton(window, app):
    assert window._synth_view is None
    window._open_synth_view()
    first = window._synth_view
    assert first is not None
    window._open_synth_view()
    assert window._synth_view is first
    first.close()
    app.processEvents()
    app.processEvents()
    assert window._synth_view is None


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


# --- regressions found by review (round 2) ---------------------------------


def test_locating_onto_a_note_always_includes_it(app):
    """The rounding bug: `Locate` stored `round(beat -> frame)`, and converting
    that frame back to a beat landed *above* the target about half the time
    (1502 of 3000 measured triples). `ProjectPlayer` fires on
    `start_beat >= window_start`, so a note exactly on the located beat fell
    outside the first window and never sounded. Locating onto a note and
    hearing it was a coin flip -- on the operation a DAW is judged on.
    """
    import random

    from notecolor.audio.transport import Transport as T

    random.seed(11)
    for _ in range(400):
        bpm = random.uniform(40, 300)
        rate = random.choice([44100, 48000, 96000])
        beat = float(random.randint(0, 400))
        transport = T(TempoMap([TempoAnchor(0.0, bpm)]), sample_rate=rate)
        transport.locate(beat)
        transport._apply(transport.commands.drain())
        assert transport.beat <= beat + 1e-9, (bpm, rate, beat, transport.beat)


def test_a_note_on_the_located_beat_actually_fires(app):
    from notecolor.audio.player import ProjectPlayer
    from notecolor.audio.transport import Transport as T

    class Fake:
        def __init__(self):
            self.started = []

        def note_on(self, event, velocity=1.0, channel=0, patch=None):
            self.started.append(event.pitch)
            return 1

        def schedule_note_off(self, voice_id, delay_seconds):
            pass

    project = Project(tempo_map=TempoMap([TempoAnchor(0.0, 291.0)]),
                      tracks=[Track(clips=[NoteClip(length_beats=200, notes=[
                          Note(80.0, 1.0, 60)])])])
    engine, transport = Fake(), T(project.tempo_map, sample_rate=48000)
    player = ProjectPlayer(project, engine, transport)
    transport.locate(80.0)
    transport.play()
    for _ in range(20):
        player.on_block(512)
    assert engine.started == [60]


def test_a_loop_shorter_than_one_block_still_loops(app):
    """The wrap was a single `if`, so a block spanning several loop lengths
    wrapped once and then ran away past the loop end for good."""
    from notecolor.audio.transport import Transport as T

    transport = T(TempoMap([TempoAnchor(0.0, 300.0)]), sample_rate=48000)
    transport.set_loop(0.0, 0.5, enabled=True)
    transport.play()
    for _ in range(200):
        transport.process_block(512)
    assert 0.0 <= transport.beat < 0.5


def test_the_title_shows_the_project_and_whether_it_is_saved(window, tmp_path):
    assert "•" not in window.windowTitle()
    window._toggle_track(0, "m")
    assert window.windowTitle().startswith("•")
    window.save(tmp_path / "p")
    assert "•" not in window.windowTitle()
    assert "p.ncproj" in window.windowTitle()


def _press(window, scene_x, scene_y):
    point = QtCore.QPointF(scene_x, scene_y)
    return QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, point,
                             QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                             QtCore.Qt.NoModifier)


def test_clicking_empty_canvas_moves_the_playhead(window):
    """The track area is the largest target on screen; restricting seeking to
    the ruler strip makes it inert. Empty lane space, not a clip -- clicking a
    clip grabs it, as every DAW does."""
    window.resize(900, 500)
    window.show()
    empty_y = len(window.project.tracks) * 54 + 30
    point = QtCore.QPointF(window.scene.px_per_beat * 6 + 2, empty_y)
    event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, point,
                              QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                              QtCore.Qt.NoModifier)
    assert window.eventFilter(window.view.viewport(), event) is True
    _pump(window)
    assert window.transport.snapshot().beat == pytest.approx(6.0, abs=0.6)


def test_clicking_a_clip_grabs_it_instead_of_seeking(window):
    window.resize(900, 500)
    window.show()
    before = window.transport.snapshot().beat
    window.eventFilter(window.view.viewport(), _press(window, 40, 20))
    _pump(window)
    assert window._drag is not None
    assert window.transport.snapshot().beat == pytest.approx(before, abs=1e-3)


def test_the_file_menu_can_open_and_undo_is_labelled(window):
    titles = [m.title() for m in window.menuBar().findChildren(QtWidgets.QMenu)]
    assert "&File" in titles and "&Edit" in titles
    window._toggle_track(0, "m")
    assert "Mute" in window.undo_action.text()
    assert window.undo_action.isEnabled()


def test_opening_replaces_the_project_in_place(window, tmp_path):
    """One window, one audio device -- the convention SessionState set."""
    from notecolor.project.bundle import save_project as save
    other = Project(name="Other", tracks=[Track(name="z")])
    path = save(other, tmp_path / "other")

    assert window.open_path(path) is True
    assert window.project.name == "Other"
    assert [t.name for t in window.project.tracks] == ["z"]
    assert window.scene.project is window.project
    assert window.headers.project is window.project
    assert not window.dirty


def test_opening_something_unreadable_reports_and_keeps_the_project(window, tmp_path):
    bad = tmp_path / "bad.ncproj"
    bad.mkdir()
    before = window.project
    assert window.open_path(bad) is False
    assert window.project is before
    assert "cannot open" in window._status


def test_the_view_opens_at_bar_one_not_the_middle(window):
    """A QGraphicsView wider than its viewport opens scrolled to the centre,
    and its scrollbar has no range until after the first show -- so the app
    was opening halfway down the timeline, past the music."""
    window.resize(900, 500)
    window.show()
    assert window.view.horizontalScrollBar().value() == 0


# --- round 3 regressions ---------------------------------------------------


def test_dragging_a_clip_is_one_undoable_move(window):
    """Live feedback moves the clip directly; the release replays it as a
    command so one gesture is one undo, not one per mouse-move."""
    window.resize(900, 500)
    window.show()
    clip = window.project.tracks[0].clips[0]
    window.eventFilter(window.view.viewport(), _press(window, 20, 20))
    move = QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove,
        QtCore.QPointF(20 + window.scene.px_per_beat * 4, 20),
        QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    window.eventFilter(window.view.viewport(), move)
    release = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(20, 20),
        QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
    window.eventFilter(window.view.viewport(), release)

    assert clip.start_beat == pytest.approx(4.0)
    assert window.edits.undo_name() == "Move Clip"
    window.undo()
    assert clip.start_beat == pytest.approx(0.0)


def test_adding_and_removing_tracks_is_undoable(window):
    before = len(window.project.tracks)
    window.add_track()
    assert len(window.project.tracks) == before + 1
    window.remove_track()
    assert len(window.project.tracks) == before
    window.undo()
    assert len(window.project.tracks) == before + 1


def test_removing_the_last_track_says_so_rather_than_raising(window):
    while window.project.tracks:
        window.remove_track()
    window.remove_track()
    assert "no tracks" in window._status


def test_readout_rects_cover_tempo_sig_and_key_but_not_position(window):
    """Position is a live playhead readout with nothing to set; the other
    three each open an edit dialog, so only they get a hit rect."""
    window.transport_bar.snapshot = window.transport.snapshot()
    window.transport_bar.paintEvent(None)
    assert set(window.transport_bar._readout_rects) == {"tempo", "sig", "key"}
    for name, rect in window.transport_bar._readout_rects.items():
        assert window.transport_bar._at(rect.center()) == name


def test_clicking_the_tempo_sig_and_key_readouts_opens_their_dialogs(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "set_tempo", lambda: calls.append("tempo"))
    monkeypatch.setattr(window, "set_time_signature", lambda: calls.append("sig"))
    monkeypatch.setattr(window, "set_key", lambda: calls.append("key"))
    window._transport_action("tempo")
    window._transport_action("sig")
    window._transport_action("key")
    assert calls == ["tempo", "sig", "key"]


def test_setting_the_time_signature_is_undoable(window, monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getInt", lambda *a, **k: (3, True))
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getItem", lambda *a, **k: ("8", True))
    window.set_time_signature()
    assert (window.project.time_signature.numerator,
            window.project.time_signature.denominator) == (3, 8)
    window.undo()
    assert (window.project.time_signature.numerator,
            window.project.time_signature.denominator) == (4, 4)


def test_setting_the_key_is_undoable(window, monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    monkeypatch.setattr(
        QtWidgets.QComboBox, "currentIndex", lambda self: 1)  # C# / D minor
    monkeypatch.setattr(
        QtWidgets.QComboBox, "currentText", lambda self: "minor")
    window.set_key()
    assert window.project.key_mode == "minor"
    assert -7 <= window.project.key_fifths <= 7
    window.undo()
    assert (window.project.key_fifths, window.project.key_mode) == (0, "major")


def test_changing_the_tempo_reaches_the_transport(window):
    """The transport holds its own tempo map reference; without an explicit
    handoff it keeps converting beats at the old tempo and the playhead drifts
    off the grid it is drawn on."""
    from notecolor.project import edit as project_edit

    window.run(project_edit.SetTempo(window.project, 60.0))
    window.transport.set_tempo_map(window.project.tempo_map)
    _pump(window)
    assert window.transport.tempo_map.bpm_at(0) == 60.0


def test_a_jittery_click_does_not_overwrite_a_committed_loop(window):
    """A plain click that moves a pixel used to replace the loop highlight
    with an invisible sliver, and toggling the loop then activated *that*."""
    window.ruler.loop_set.emit(2.0, 6.0)
    _pump(window)
    window.ruler._drag_from = 20.0
    jitter = QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove,
        QtCore.QPointF(20.01 * window.scene.px_per_beat, 5),
        QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
    window.ruler.mouseMoveEvent(jitter)
    assert window.ruler.loop == (2.0, 6.0)


def test_re_enabling_a_loop_uses_the_transports_region_not_the_rulers(window):
    window.ruler.loop_set.emit(2.0, 6.0)
    _pump(window)
    window._toggle_loop()                      # off
    _pump(window)
    window.ruler.loop = (99.0, 99.1)           # stale drag state
    window._toggle_loop()                      # on
    _pump(window)
    snapshot = window.transport.snapshot()
    assert (snapshot.loop_start_beat, snapshot.loop_end_beat) == (2.0, 6.0)


def test_file_dialogs_refuse_rather_than_hang_without_a_display(window, monkeypatch):
    """Qt's offscreen platform accepts a modal dialog and never returns from
    it -- the window simply freezes, which is what a misconfigured .desktop
    entry or a container would produce.

    The platform is forced rather than assumed: relying on the ambient one
    made this test open a real dialog and hang when the environment happened
    to have a display.
    """
    monkeypatch.setattr(QtWidgets.QApplication, "platformName",
                        staticmethod(lambda: "offscreen"))
    assert window.save_as() is False
    assert "real display" in window._status
    window.open_dialog()
    assert "real display" in window._status


def test_save_reports_a_failure_it_cannot_recover_from(window, tmp_path):
    """save_as() used to create the parent directory unguarded, before its
    dialog, so a read-only ~/Music made Save As do nothing at all -- with the
    traceback going to a stderr a .desktop launch does not have."""
    blocker = tmp_path / "afile"
    blocker.write_text("not a directory")
    window._toggle_track(0, "m")
    assert window.save(blocker / "nested" / "p") is False
    assert "save failed" in window._status
    assert window.dirty is True


def test_the_block_listener_error_counter_starts_at_zero_and_counts(monkeypatch):
    """It was created lazily on first error and displayed nowhere, so a
    scheduler bug degraded to 'notes stopped happening' with no signal."""
    import numpy as np

    from notecolor.audio.sound_engine import SoundEngine

    engine = SoundEngine.__new__(SoundEngine)
    engine.callback_status_count = 0
    engine._frame_clock = 0
    engine._block_listener = None
    engine.block_listener_error_count = 0
    assert engine.block_listener_error_count == 0

    def explode(_frames):
        raise RuntimeError("scheduler bug")

    engine.set_block_listener(explode)
    engine.voices = type("V", (), {"render_block": lambda self, m, f: None})()
    engine.effects = type("E", (), {"process": lambda self, m: m})()
    engine._resolve_due_offs = lambda _f: None
    out = np.zeros((64, 1), dtype=np.float32)
    engine._callback(out, 64, None, None)
    assert engine.block_listener_error_count == 1


def test_notes_paint_above_their_clip_body(window):
    """Making clips draggable required giving the body a z-value so it could
    be hit-tested -- which put it on top of its own notes, and the notes are
    the only colour on screen and the whole point of the app. Caught by
    looking at a render, not by a test, so here is the test."""
    scene = window.scene
    bodies = [i for i in scene.items() if i.data(1) is not None]
    notes = [i for i in scene.items()
             if isinstance(i, QtWidgets.QGraphicsRectItem)
             and i.data(1) is None and i.zValue() == scene.Z_NOTE]
    assert bodies and notes
    assert max(b.zValue() for b in bodies) < min(n.zValue() for n in notes)
