"""The piano roll -- editable note editing in its own bottom panel (map #145
milestone 2). Originally built inline on the main arrange canvas (#155);
reversed into a separate, resizable `PianoRollPanel` after hands-on use
surfaced keybinding collisions with the main window's own transport/track-
select shortcuts -- see
`docs/decisions/52-the-piano-roll-editing-surface-reversed-into-its-own-panel-issue-155-reopened-by-hands-on-use.md`.

Same offscreen-Qt discipline as `test_studio_window.py`: no display, no audio
device, assert the decisions the window makes rather than pixels. The one
place this file departs from that and drives real Qt event delivery
(`QTest.keyClick`) is the focus-scoping tests -- the whole point of the panel
being its own widget is that Qt's real focus/shortcut machinery routes keys
correctly, so that is the one behaviour a direct method call cannot verify.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

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


def _pump(window, blocks=4):
    """Commands apply at a block boundary, so a test has to run blocks."""
    for _ in range(blocks):
        window.transport.process_block(512)


def _key(qt_key, modifiers=QtCore.Qt.NoModifier):
    return QtGui.QKeyEvent(QtCore.QEvent.KeyPress, qt_key, modifiers)


def _press(scene_x, scene_y, modifiers=QtCore.Qt.NoModifier):
    return QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,
                             QtCore.QPointF(scene_x, scene_y),
                             QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                             modifiers)


def _move(scene_x, scene_y):
    return QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(scene_x, scene_y),
                             QtCore.Qt.NoButton, QtCore.Qt.LeftButton,
                             QtCore.Qt.NoModifier)


def _release(scene_x, scene_y):
    return QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,
                             QtCore.QPointF(scene_x, scene_y),
                             QtCore.Qt.LeftButton, QtCore.Qt.NoButton,
                             QtCore.Qt.NoModifier)


def _note_item(view, note):
    for item in view.scene().items():
        if item.data(2) is note:
            return item
    return None


def _open(window, row=0):
    window.toggle_piano_roll(row)
    return window.piano_panel


# --- entering / exiting -----------------------------------------------------


def test_double_click_opens_and_closes_the_panel(window):
    assert not window.piano_panel.is_open
    window.toggle_piano_roll(0)
    assert window.piano_panel.is_open
    assert window.piano_panel.isVisible()
    window.toggle_piano_roll(0)
    assert not window.piano_panel.is_open
    assert not window.piano_panel.isVisible()


def test_opening_a_different_track_switches_rather_than_stacks(window):
    window.toggle_piano_roll(0)
    window.toggle_piano_roll(1)
    assert window.piano_panel.track_index == 1
    assert window.scene.piano_roll_row == 1


def test_an_audio_track_refuses_a_panel(window):
    window.toggle_piano_roll(2)
    assert not window.piano_panel.is_open
    assert "audio track" in window._status


def test_the_header_double_click_drives_the_same_toggle(window):
    window.headers.mouseDoubleClickEvent(
        QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick,
                          QtCore.QPointF(20, 20), QtCore.Qt.LeftButton,
                          QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
    assert window.piano_panel.is_open


def test_the_panels_own_close_button_clears_the_header_marker(window):
    panel = _open(window)
    panel._on_close_clicked()
    assert not panel.is_open
    assert window.scene.piano_roll_row is None


def test_the_main_canvas_never_grows_a_row_for_editing(window):
    """#52 reversed #155's inline row-height-expansion -- every row stays
    `LANE_H` regardless of which piano roll (if any) is open."""
    from notecolor.gui.studio import LANE_H

    before = [window.scene.row_height(r) for r in range(3)]
    window.toggle_piano_roll(0)
    after = [window.scene.row_height(r) for r in range(3)]
    assert before == after == [LANE_H] * 3


# --- rendering / hit-testing -------------------------------------------------


def test_opening_the_panel_makes_notes_real_hit_targets(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    y = scene.pitch_to_y(note.pitch) + 1
    hit = scene.note_at(QtCore.QPointF(2, y))
    assert hit is not None
    hit_note, hit_clip, is_resize = hit
    assert hit_note is note and hit_clip is clip
    assert is_resize is False


def test_the_right_edge_is_a_resize_handle_not_a_move_grab(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    item = _note_item(panel.view, note)
    right = item.rect().right()
    y = scene.pitch_to_y(note.pitch) + 1
    _, _, is_resize = scene.note_at(QtCore.QPointF(right - 1, y))
    assert is_resize is True
    _, _, is_resize = scene.note_at(QtCore.QPointF(right - 20, y))
    assert is_resize is False


def test_the_main_canvas_thumbnail_stays_unclickable(window):
    """The always-visible mini view on the main arrange canvas carries no
    hit-testable note data at all -- editing happens only in the panel."""
    _open(window, row=0)
    for item in window.scene.items():
        assert item.data(2) is None


# --- add ---------------------------------------------------------------


def test_ctrl_click_on_a_clip_adds_one_note_undoably(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    before = len(clip.notes)
    point = QtCore.QPointF(4 * panel.view.scene().px_per_beat, 20)
    panel.view.mousePressEvent(_press(point.x(), point.y(), QtCore.Qt.ControlModifier))
    assert len(clip.notes) == before + 1
    assert window.edits.undo_name() == "Add Note"
    window.undo()
    assert len(clip.notes) == before


def test_plain_click_on_empty_space_parks_the_cursor_there(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    before = len(clip.notes)
    scene = panel.view.scene()
    panel.view.mousePressEvent(_press(6 * scene.px_per_beat, 200))
    assert len(clip.notes) == before
    assert scene.selected_note is None
    assert scene.cursor is not None


# --- move -------------------------------------------------------------


def test_dragging_a_note_moves_it_in_time_and_pitch_as_one_command(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    start_y = scene.pitch_to_y(note.pitch) + 1
    panel.view.mousePressEvent(_press(2, start_y))
    assert panel.view._drag["kind"] == "move"
    new_x = 3 * scene.px_per_beat
    new_y = scene.pitch_to_y(note.pitch - 2) + 1
    panel.view.mouseMoveEvent(_move(new_x, new_y))
    panel.view.mouseReleaseEvent(_release(new_x, new_y))

    assert note.start_beat == pytest.approx(3.0)
    assert note.pitch == 58
    assert window.edits.undo_name() == "Move Note"
    window.undo()
    assert note.start_beat == pytest.approx(0.0)
    assert note.pitch == 60


def test_a_click_with_no_movement_is_not_an_undoable_command(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    y = scene.pitch_to_y(note.pitch) + 1
    before_name = window.edits.undo_name()
    panel.view.mousePressEvent(_press(2, y))
    panel.view.mouseReleaseEvent(_release(2, y))
    assert window.edits.undo_name() == before_name


# --- resize -----------------------------------------------------------


def test_dragging_the_right_edge_resizes_undoably(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    item = _note_item(panel.view, note)
    right = item.rect().right()
    y = scene.pitch_to_y(note.pitch) + 1
    panel.view.mousePressEvent(_press(right - 1, y))
    assert panel.view._drag["kind"] == "resize"
    new_x = right + scene.px_per_beat
    panel.view.mouseMoveEvent(_move(new_x, y))
    panel.view.mouseReleaseEvent(_release(new_x, y))

    assert note.duration_beats == pytest.approx(2.0, abs=0.15)
    assert window.edits.undo_name() == "Resize Note"
    window.undo()
    assert note.duration_beats == pytest.approx(1.0)


def test_resize_clamps_to_the_minimum_duration(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    item = _note_item(panel.view, note)
    right = item.rect().right()
    y = scene.pitch_to_y(note.pitch) + 1
    panel.view.mousePressEvent(_press(right - 1, y))
    far_left = -500
    panel.view.mouseMoveEvent(_move(far_left, y))
    panel.view.mouseReleaseEvent(_release(far_left, y))
    assert note.duration_beats == pytest.approx(project_edit.ResizeNote.MIN_DURATION_BEATS)


# --- delete -------------------------------------------------------------


def test_selecting_then_deleting_a_note_is_one_undoable_command(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    y = scene.pitch_to_y(note.pitch) + 1
    panel.view.mousePressEvent(_press(2, y))
    panel.view.mouseReleaseEvent(_release(2, y))
    assert scene.selected_note == (clip, note)

    before = len(clip.notes)
    panel.view.delete_selected()
    assert len(clip.notes) == before - 1
    assert note not in clip.notes
    assert window.edits.undo_name() == "Delete Note"
    window.undo()
    assert note in clip.notes


def test_deleting_with_nothing_selected_says_so_rather_than_raising(window):
    panel = _open(window)
    panel.view.scene().selected_note = None
    panel.view.delete_selected()
    assert "no note selected" in window._status


def test_delete_key_removes_the_selected_note(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    panel.view.scene().selected_note = (clip, note)
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Delete))
    assert note not in clip.notes


# --- ghost cursor: keyboard movement -----------------------------------


def test_opening_the_panel_starts_a_ghost_cursor_on_the_first_note(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    scene = panel.view.scene()
    assert scene.selected_note is None
    assert scene.cursor == (clip.notes[0].start_beat, clip.notes[0].pitch)


def test_closing_the_panel_clears_the_cursor(window):
    panel = _open(window)
    window.toggle_piano_roll(0)
    assert panel.view.scene().cursor is None


def test_arrow_keys_move_the_ghost_cursor_when_no_note_is_selected(window):
    panel = _open(window)
    scene = panel.view.scene()
    beat, pitch = scene.cursor

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Right))
    assert scene.cursor == (beat + 1.0, pitch)
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Up))
    assert scene.cursor == (beat + 1.0, pitch + 1)
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Left, QtCore.Qt.ShiftModifier))
    assert scene.cursor == (max(0.0, beat + 1.0 - 4.0), pitch + 1)


def test_space_at_an_empty_cursor_position_adds_a_note(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    scene = panel.view.scene()
    scene.cursor = (5.0, 71)
    before = len(clip.notes)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Space))
    assert len(clip.notes) == before + 1
    added = clip.notes[-1]
    assert (added.start_beat, added.pitch) == (5.0, 71)
    assert window.edits.undo_name() == "Add Note"
    # Stays in cursor mode with the cursor untouched, so Space can be
    # pressed repeatedly to lay down a run of notes (#155 hands-on fix).
    assert scene.selected_note is None
    assert scene.cursor == (5.0, 71)


def test_space_in_note_select_mode_is_a_no_op(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    scene.selected_note = (clip, note)
    before = len(clip.notes)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Space))
    assert len(clip.notes) == before
    assert scene.selected_note == (clip, note)


def test_space_on_an_existing_note_removes_it(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    scene.cursor = (note.start_beat, note.pitch)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Space))
    assert note not in clip.notes
    assert window.edits.undo_name() == "Delete Note"


def test_space_outside_any_clip_says_so_rather_than_crashing(window):
    panel = _open(window)
    panel.view.scene().cursor = (999.0, 71)
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Space))
    assert "no clip here" in window._status


# --- note mode: Shift+arrow nudging --------------------------------------


def test_shift_arrow_nudges_the_selected_note_in_pitch_and_time(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    panel.view.scene().selected_note = (clip, note)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Up, QtCore.Qt.ShiftModifier))
    assert note.pitch == 61
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Down, QtCore.Qt.ShiftModifier))
    assert note.pitch == 60
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Right, QtCore.Qt.ShiftModifier))
    assert note.start_beat == 1.0
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Left, QtCore.Qt.ShiftModifier))
    assert note.start_beat == 0.0

    assert window.edits.undo_name() == "Move Note"
    window.undo()
    assert note.start_beat == 1.0


def test_nudge_clamps_pitch_to_the_panels_own_bounds(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    scene.selected_note = (clip, note)
    _, high = scene.bounds
    note.pitch = high

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Up, QtCore.Qt.ShiftModifier))
    assert note.pitch == high


def test_shift_left_arrow_does_not_move_a_note_before_the_start_of_time(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    assert note.start_beat == 0.0
    panel.view.scene().selected_note = (clip, note)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Left, QtCore.Qt.ShiftModifier))
    assert note.start_beat == 0.0
    assert window.edits.undo_name() is None


# --- note mode: plain-arrow note-to-note navigation ----------------------


def test_left_right_arrows_step_between_notes_in_time(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    first, second = clip.notes[0], clip.notes[1]
    scene = panel.view.scene()
    scene.selected_note = (clip, first)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Right))
    assert scene.selected_note == (clip, second)
    # A plain arrow only re-selects -- it never edits the model.
    assert first.start_beat == 0.0 and second.start_beat == 2.0

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Left))
    assert scene.selected_note == (clip, first)


def test_right_arrow_at_the_last_note_says_so(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    last = clip.notes[1]
    scene = panel.view.scene()
    scene.selected_note = (clip, last)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Right))
    assert scene.selected_note == (clip, last)
    assert "no more notes" in window._status


def test_up_down_arrows_step_between_notes_in_pitch(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    lower, higher = clip.notes[0], clip.notes[1]
    assert lower.pitch < higher.pitch
    scene = panel.view.scene()
    scene.selected_note = (clip, lower)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Up))
    assert scene.selected_note == (clip, higher)
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Down))
    assert scene.selected_note == (clip, lower)


def test_up_arrow_with_no_higher_note_says_so(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    highest = clip.notes[1]
    scene = panel.view.scene()
    scene.selected_note = (clip, highest)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Up))
    assert scene.selected_note == (clip, highest)
    assert "no note there" in window._status


def test_note_navigation_crosses_clip_boundaries(window):
    """Left/Right/Up/Down look across every clip in the open track, not just
    the selected note's own clip."""
    panel = _open(window)
    track = window.project.tracks[0]
    far_clip = NoteClip(start_beat=20, length_beats=4,
                        notes=[Note(0.0, 1.0, 90)])
    track.clips.append(far_clip)
    clip = track.clips[0]
    second = clip.notes[1]
    scene = panel.view.scene()
    scene.selected_note = (clip, second)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Right))
    assert scene.selected_note == (far_clip, far_clip.notes[0])


# --- mode switching: keyboard (Enter) and toolbar button ----------------


def test_enter_switches_from_cursor_mode_to_note_mode_and_back(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    scene = panel.view.scene()
    scene.cursor = (note.start_beat, note.pitch)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Return))
    assert scene.selected_note == (clip, note)
    assert panel.view.in_note_mode()

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Up, QtCore.Qt.ShiftModifier))
    assert note.pitch == 61
    # Note-mode moves the note, not the parked cursor -- it only catches up
    # once Enter hands control back to it.
    assert scene.cursor == (note.start_beat, note.pitch - 1)

    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Return))
    assert scene.selected_note is None
    assert scene.cursor == (note.start_beat, note.pitch)


def test_enter_on_an_empty_cursor_position_says_so(window):
    panel = _open(window)
    panel.view.scene().cursor = (999.0, 71)
    panel.view.keyPressEvent(_key(QtCore.Qt.Key_Return))
    assert panel.view.scene().selected_note is None
    assert "no note under cursor" in window._status


def test_mode_button_label_reflects_the_current_mode(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    panel.view.scene().cursor = (note.start_beat, note.pitch)

    assert "cursor mode" in panel.mode_button.text()
    panel._on_mode_clicked()
    assert "note mode" in panel.mode_button.text()
    assert panel.delete_button.isEnabled()
    panel._on_mode_clicked()
    assert "cursor mode" in panel.mode_button.text()
    assert not panel.delete_button.isEnabled()


def test_add_delete_button_mirrors_space(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    panel.view.scene().cursor = (note.start_beat, note.pitch)

    panel._on_add_delete_clicked()
    assert note not in clip.notes
    assert window.edits.undo_name() == "Delete Note"


def test_delete_button_mirrors_delete_key(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    panel.view.scene().selected_note = (clip, note)

    panel._on_delete_clicked()
    assert note not in clip.notes
    assert window.edits.undo_name() == "Delete Note"


def test_close_button_closes_the_panel(window):
    panel = _open(window)
    panel._on_close_clicked()
    assert not panel.is_open


# --- focus scoping: the actual bug being fixed --------------------------
#
# These drive real Qt event delivery (`QTest.keyClick`), unlike every test
# above -- the whole point of moving the piano roll into its own widget is
# that Qt's real focus/shortcut machinery (including `QAction` shortcuts
# registered with `Qt.WindowShortcut` context, which fire on a
# `ShortcutOverride` event ahead of a widget's own `keyPressEvent()`) routes
# a key to the right place. A direct method call can't exercise that at all.


def test_space_on_the_main_window_still_means_play_stop_when_the_panel_is_closed(window):
    assert not window.piano_panel.is_open
    assert not window.transport.snapshot().playing
    QTest.keyClick(window, QtCore.Qt.Key_Space)
    _pump(window)
    assert window.transport.snapshot().playing


def test_space_on_the_panel_view_means_add_delete_not_play_stop(window):
    panel = _open(window)
    clip = window.project.tracks[0].clips[0]
    note = clip.notes[0]
    panel.view.scene().cursor = (note.start_beat, note.pitch)
    assert not window.transport.snapshot().playing

    QTest.keyClick(panel.view, QtCore.Qt.Key_Space)

    assert note not in clip.notes                       # the panel handled it
    _pump(window)
    assert not window.transport.snapshot().playing       # transport untouched


def test_arrow_keys_on_the_main_window_still_select_tracks_when_the_panel_is_closed(window):
    assert not window.piano_panel.is_open
    window.headers.selected_index = 0
    QTest.keyClick(window, QtCore.Qt.Key_Down)
    assert window.headers.selected_index == 1


def test_arrow_keys_on_the_panel_view_move_the_cursor_not_the_track_selection(window):
    panel = _open(window)
    window.headers.selected_index = 0
    scene = panel.view.scene()
    beat, pitch = scene.cursor

    QTest.keyClick(panel.view, QtCore.Qt.Key_Up)

    assert scene.cursor == (beat, pitch + 1)             # the panel handled it
    assert window.headers.selected_index == 0            # main window untouched


def test_clicking_the_main_canvas_returns_focus_and_normal_arrow_meaning(window):
    panel = _open(window)
    panel.view.setFocus()

    point = QtCore.QPointF(6 * window.scene.px_per_beat, window.scene.row_top(0) + 20)
    window.eventFilter(window.view.viewport(),
                       QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, point,
                                        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                                        QtCore.Qt.NoModifier))
    window.eventFilter(window.view.viewport(),
                       QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, point,
                                        QtCore.Qt.LeftButton, QtCore.Qt.NoButton,
                                        QtCore.Qt.NoModifier))

    window.headers.selected_index = 0
    QTest.keyClick(window, QtCore.Qt.Key_Down)
    assert window.headers.selected_index == 1


# --- note-name column --------------------------------------------------


def test_note_name_column_reads_the_live_key(window):
    window.project.key_fifths = 0
    window.project.key_mode = "major"
    panel = _open(window)
    from notecolor.project.model import chromatic_note_names
    assert chromatic_note_names(*panel.keys_column._key_signature())[1] == "Db"


def test_note_name_column_reads_the_live_key_at_negative_fifths(window):
    window.project.key_fifths = -3
    window.project.key_mode = "major"
    panel = _open(window)
    from notecolor.project.model import chromatic_note_names
    assert chromatic_note_names(*panel.keys_column._key_signature())[1] == "Db"


def test_note_name_column_follows_a_live_key_change(window):
    window.project.key_fifths = 0
    window.project.key_mode = "major"
    panel = _open(window)
    from notecolor.project.model import chromatic_note_names
    assert chromatic_note_names(*panel.keys_column._key_signature())[1] == "Db"

    window.project.key_fifths = -5     # changed while the panel stays open
    assert chromatic_note_names(*panel.keys_column._key_signature())[1] == "Db"


def test_note_name_column_spells_c_major_per_the_circle_of_fifths(window):
    """C major's chromatic passing tones are flats of the degree above,
    except the raised 4th (F#) -- not a blanket all-sharp/all-flat table."""
    window.project.key_fifths = 0
    window.project.key_mode = "major"
    panel = _open(window)
    from notecolor.project.model import chromatic_note_names
    assert chromatic_note_names(*panel.keys_column._key_signature()) == [
        "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def test_note_name_column_highlights_the_keys_own_scale_not_just_white_keys(window):
    """F# major's diatonic scale is all black keys except one -- the tint
    must follow the key's 7 scale notes, not the piano's white keys."""
    window.project.key_fifths = 6
    window.project.key_mode = "major"
    panel = _open(window)
    from notecolor.project.model import diatonic_pitch_classes
    scale = diatonic_pitch_classes(*panel.keys_column._key_signature())
    assert scale == {6, 8, 10, 11, 1, 3, 5}
    assert 0 not in scale   # C is not in F# major's scale


def test_note_name_column_paints_without_error(window):
    """Not a pixel test (see module docstring) -- just confirms the paint
    path (row loop, natural/accidental branch, scroll offset) runs clean
    over a real track's bounds."""
    panel = _open(window)
    panel.keys_column.resize(panel.keys_column.width(), 200)
    panel.keys_column.set_scroll(10)
    panel.keys_column.repaint()


# --- zoom ----------------------------------------------------------------


def test_zoom_in_increases_both_axes(window):
    from notecolor.gui import piano_roll_panel as prp
    panel = _open(window)
    scene = panel.view.scene()
    before = (scene.px_per_beat, scene.px_per_semitone)

    panel.view.zoom_in()

    assert scene.px_per_beat == pytest.approx(before[0] * prp.ZOOM_FACTOR)
    assert scene.px_per_semitone == pytest.approx(before[1] * prp.ZOOM_FACTOR)


def test_zoom_out_decreases_both_axes(window):
    from notecolor.gui import piano_roll_panel as prp
    panel = _open(window)
    scene = panel.view.scene()
    before = (scene.px_per_beat, scene.px_per_semitone)

    panel.view.zoom_out()

    assert scene.px_per_beat == pytest.approx(before[0] / prp.ZOOM_FACTOR)
    assert scene.px_per_semitone == pytest.approx(before[1] / prp.ZOOM_FACTOR)


def test_zoom_clamps_at_the_minimum(window):
    from notecolor.gui import piano_roll_panel as prp
    panel = _open(window)
    scene = panel.view.scene()
    for _ in range(60):
        panel.view.zoom_out()
    assert scene.px_per_beat == pytest.approx(prp.MIN_PX_PER_BEAT)
    assert scene.px_per_semitone == pytest.approx(prp.MIN_PX_PER_SEMITONE)


def test_zoom_clamps_at_the_maximum(window):
    from notecolor.gui import piano_roll_panel as prp
    panel = _open(window)
    scene = panel.view.scene()
    for _ in range(60):
        panel.view.zoom_in()
    assert scene.px_per_beat == pytest.approx(prp.MAX_PX_PER_BEAT)
    assert scene.px_per_semitone == pytest.approx(prp.MAX_PX_PER_SEMITONE)


def test_zoom_buttons_drive_the_same_zoom(window):
    from notecolor.gui import piano_roll_panel as prp
    panel = _open(window)
    scene = panel.view.scene()
    before = scene.px_per_beat

    panel._on_zoom_in_clicked()
    assert scene.px_per_beat == pytest.approx(before * prp.ZOOM_FACTOR)

    panel._on_zoom_out_clicked()
    assert scene.px_per_beat == pytest.approx(before)


def _wheel(angle_delta_y, modifiers=QtCore.Qt.NoModifier, pos=QtCore.QPointF(50, 50)):
    return QtGui.QWheelEvent(
        pos, pos, QtCore.QPoint(0, 0), QtCore.QPoint(0, angle_delta_y),
        QtCore.Qt.NoButton, modifiers, QtCore.Qt.NoScrollPhase, False)


def test_ctrl_wheel_zooms_the_panel(window):
    panel = _open(window)
    scene = panel.view.scene()
    before = scene.px_per_beat

    panel.view.wheelEvent(_wheel(120, QtCore.Qt.ControlModifier))

    assert scene.px_per_beat != before


def test_plain_wheel_does_not_zoom(window):
    panel = _open(window)
    scene = panel.view.scene()
    before = (scene.px_per_beat, scene.px_per_semitone)

    panel.view.wheelEvent(_wheel(120))

    assert (scene.px_per_beat, scene.px_per_semitone) == before


def test_opening_the_panel_gives_it_a_real_usable_height(window):
    """Regression for the bug this fixes: with no default `QSplitter` size,
    the panel opened at its bare `setMinimumHeight(60)` -- mostly toolbar,
    a sliver of actual roll -- so the mouse was rarely really over it."""
    panel = _open(window)
    assert panel.height() > panel.minimumHeight()


def test_ctrl_wheel_over_the_open_panel_zooms_it_not_the_main_canvas(window):
    """Drives the real Qt hit-testing/dispatch path (see this file's
    docstring), not a direct `wheelEvent()` call -- a fake call can't catch
    a real routing/layout bug like the one `test_opening_the_panel_gives_it_
    a_real_usable_height` above regresses against, where Ctrl+scroll over
    what the user sees as the piano roll actually lands on the main canvas
    underneath because the panel had no real screen area."""
    panel = _open(window)
    viewport = panel.view.viewport()
    global_point = viewport.mapToGlobal(QtCore.QPoint(30, viewport.height() // 2))
    target = QtWidgets.QApplication.widgetAt(global_point)
    assert target is viewport      # the point really is over the piano roll

    main_before = window.scene.px_per_beat
    panel_before = panel.view.scene().px_per_beat
    local_point = target.mapFromGlobal(global_point)
    event = QtGui.QWheelEvent(
        QtCore.QPointF(local_point), QtCore.QPointF(global_point),
        QtCore.QPoint(0, 0), QtCore.QPoint(0, 120), QtCore.Qt.NoButton,
        QtCore.Qt.ControlModifier, QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(target, event)

    assert window.scene.px_per_beat == main_before
    assert panel.view.scene().px_per_beat != panel_before
