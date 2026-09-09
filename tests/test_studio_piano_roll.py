"""The piano roll -- editable note rendering on the same arrange canvas
(map #145 milestone 2, decided in #155). Same offscreen-Qt discipline as
`test_studio_window.py`: no display, no audio device, assert the decisions
the window makes rather than pixels.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from notecolor.audio.transport import Transport  # noqa: E402
from notecolor.project import edit as project_edit  # noqa: E402
from notecolor.project.model import (  # noqa: E402
    AUDIO_TRACK, AudioClip, Note, NoteClip, Project, TempoAnchor, TempoMap,
    Track,
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
        tracks=[Track(name="a", clips=[NoteClip(length_beats=8, notes=[
                          Note(0.0, 1.0, 60), Note(2.0, 1.0, 64)])]),
                Track(name="b", clips=[NoteClip(length_beats=8,
                                                notes=[Note(0, 1, 67)])]),
                Track(name="drums", kind=AUDIO_TRACK,
                     clips=[AudioClip(length_beats=8)])])
    win = StudioWindow(project, transport=Transport(project.tempo_map,
                                                    sample_rate=48000))
    win.timer.stop()
    win.resize(900, 600)
    win.show()
    yield win
    win.deleteLater()


def _press(window, scene_x, scene_y, modifiers=QtCore.Qt.NoModifier):
    point = QtCore.QPointF(scene_x, scene_y)
    return QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, point,
                             QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                             modifiers)


def _move(window, scene_x, scene_y):
    return QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(scene_x, scene_y),
                             QtCore.Qt.NoButton, QtCore.Qt.LeftButton,
                             QtCore.Qt.NoModifier)


def _release(window, scene_x, scene_y):
    return QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,
                             QtCore.QPointF(scene_x, scene_y),
                             QtCore.Qt.LeftButton, QtCore.Qt.NoButton,
                             QtCore.Qt.NoModifier)


def _note_item(window, note):
    for item in window.scene.items():
        if item.data(2) is note:
            return item
    return None


# --- entering / exiting ------------------------------------------------


def test_double_click_opens_and_closes_the_piano_roll(window):
    assert window.scene.piano_roll_row is None
    window.toggle_piano_roll(0)
    assert window.scene.piano_roll_row == 0
    window.toggle_piano_roll(0)
    assert window.scene.piano_roll_row is None


def test_opening_a_different_track_switches_rather_than_stacks(window):
    window.toggle_piano_roll(0)
    window.toggle_piano_roll(1)
    assert window.scene.piano_roll_row == 1


def test_an_audio_track_refuses_a_piano_roll(window):
    window.toggle_piano_roll(2)
    assert window.scene.piano_roll_row is None
    assert "audio track" in window._status


def test_the_header_double_click_drives_the_same_toggle(window):
    window.headers.mouseDoubleClickEvent(
        QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick,
                          QtCore.QPointF(20, 20), QtCore.Qt.LeftButton,
                          QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
    assert window.scene.piano_roll_row == 0


# --- rendering / hit-testing --------------------------------------------


def test_opening_the_piano_roll_makes_notes_real_hit_targets(window):
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    assert window.scene.note_at(QtCore.QPointF(5, 5)) is None
    window.toggle_piano_roll(0)
    top = window.scene.row_top(0)
    y = window.scene.pitch_to_y(note.pitch, top) + 1
    hit = window.scene.note_at(QtCore.QPointF(2, y))
    assert hit is not None
    hit_note, hit_clip, is_resize = hit
    assert hit_note is note and hit_clip is clip
    assert is_resize is False


def test_the_right_edge_is_a_resize_handle_not_a_move_grab(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    item = _note_item(window, note)
    right = item.rect().right()
    top = window.scene.row_top(0)
    y = window.scene.pitch_to_y(note.pitch, top) + 1
    _, _, is_resize = window.scene.note_at(QtCore.QPointF(right - 1, y))
    assert is_resize is True
    _, _, is_resize = window.scene.note_at(QtCore.QPointF(right - 20, y))
    assert is_resize is False


def test_the_thumbnail_view_stays_unclickable(window):
    """Track b's clip is never opened -- its notes keep the old 3px
    decoration, with no `data(2)` for `note_at()` to find."""
    window.toggle_piano_roll(0)
    for item in window.scene.items():
        if item.data(1) is window.project.tracks[1].clips[0]:
            continue
    assert window.scene.note_at(QtCore.QPointF(2, window.scene.row_top(1) + 30)) is None


# --- add ------------------------------------------------------------------


def test_ctrl_click_on_a_clip_adds_one_note_undoably(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    before = len(clip.notes)
    top = window.scene.row_top(0)
    point = QtCore.QPointF(4 * window.scene.px_per_beat, top + 20)
    handled = window.eventFilter(window.view.viewport(),
                                 _press(window, point.x(), point.y(),
                                       QtCore.Qt.ControlModifier))
    assert handled is True
    assert len(clip.notes) == before + 1
    assert window.edits.undo_name() == "Add Note"
    window.undo()
    assert len(clip.notes) == before


def test_plain_click_on_a_clip_still_grabs_it_not_adds_a_note(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    before = len(clip.notes)
    top = window.scene.row_top(0)
    window.eventFilter(window.view.viewport(),
                       _press(window, 6 * window.scene.px_per_beat, top + 45))
    assert len(clip.notes) == before
    assert window._drag is not None and window._drag["kind"] == "clip"


# --- move -------------------------------------------------------------


def test_dragging_a_note_moves_it_in_time_and_pitch_as_one_command(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    top = window.scene.row_top(0)
    start_y = window.scene.pitch_to_y(note.pitch, top) + 1
    window.eventFilter(window.view.viewport(), _press(window, 2, start_y))
    assert window._drag["kind"] == "move-note"
    new_x = 3 * window.scene.px_per_beat
    new_y = window.scene.pitch_to_y(note.pitch - 2, top) + 1
    window.eventFilter(window.view.viewport(), _move(window, new_x, new_y))
    window.eventFilter(window.view.viewport(), _release(window, new_x, new_y))

    assert note.start_beat == pytest.approx(3.0)
    assert note.pitch == 58
    assert window.edits.undo_name() == "Move Note"
    window.undo()
    assert note.start_beat == pytest.approx(0.0)
    assert note.pitch == 60


def test_a_click_with_no_movement_is_not_an_undoable_command(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    top = window.scene.row_top(0)
    y = window.scene.pitch_to_y(note.pitch, top) + 1
    before_name = window.edits.undo_name()
    window.eventFilter(window.view.viewport(), _press(window, 2, y))
    window.eventFilter(window.view.viewport(), _release(window, 2, y))
    assert window.edits.undo_name() == before_name


# --- resize -----------------------------------------------------------


def test_dragging_the_right_edge_resizes_undoably(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    top = window.scene.row_top(0)
    item = _note_item(window, note)
    right = item.rect().right()
    y = window.scene.pitch_to_y(note.pitch, top) + 1
    window.eventFilter(window.view.viewport(), _press(window, right - 1, y))
    assert window._drag["kind"] == "resize-note"
    new_x = right + window.scene.px_per_beat
    window.eventFilter(window.view.viewport(), _move(window, new_x, y))
    window.eventFilter(window.view.viewport(), _release(window, new_x, y))

    assert note.duration_beats == pytest.approx(2.0, abs=0.15)
    assert window.edits.undo_name() == "Resize Note"
    window.undo()
    assert note.duration_beats == pytest.approx(1.0)


def test_resize_clamps_to_the_minimum_duration(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    top = window.scene.row_top(0)
    item = _note_item(window, note)
    right = item.rect().right()
    y = window.scene.pitch_to_y(note.pitch, top) + 1
    window.eventFilter(window.view.viewport(), _press(window, right - 1, y))
    far_left = -500
    window.eventFilter(window.view.viewport(), _move(window, far_left, y))
    window.eventFilter(window.view.viewport(), _release(window, far_left, y))
    assert note.duration_beats == pytest.approx(project_edit.ResizeNote.MIN_DURATION_BEATS)


# --- delete -------------------------------------------------------------


def test_selecting_then_deleting_a_note_is_one_undoable_command(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    top = window.scene.row_top(0)
    y = window.scene.pitch_to_y(note.pitch, top) + 1
    window.eventFilter(window.view.viewport(), _press(window, 2, y))
    window.eventFilter(window.view.viewport(), _release(window, 2, y))
    assert window.scene.selected_note == (clip, note)

    before = len(clip.notes)
    window.delete_selected_note()
    assert len(clip.notes) == before - 1
    assert note not in clip.notes
    assert window.edits.undo_name() == "Delete Note"
    window.undo()
    assert note in clip.notes


def test_deleting_with_nothing_selected_says_so_rather_than_raising(window):
    window.scene.selected_note = None
    window.delete_selected_note()
    assert "no note selected" in window._status


def test_delete_key_removes_the_selected_note(window):
    window.toggle_piano_roll(0)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    window.scene.selected_note = (clip, note)
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Delete,
                            QtCore.Qt.NoModifier)
    window.keyPressEvent(event)
    assert note not in clip.notes
