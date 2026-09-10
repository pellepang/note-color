"""Stage 2 of the Synth View (map #145, ticket #157): `gui/synth_keyboard.py`
-- layout state, row assignment, and the live keyboard-to-preview wiring.

Same offscreen-Qt discipline as `test_synth_workspace.py`: no display, no
audio device, no real patch directory -- synth/kit names are always handed
to `SynthKeyboardBand` explicitly so these tests never depend on whatever
happens to live under `~/.config/note-color/patches/`.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from notecolor.settings import config  # noqa: E402
from notecolor.settings.patch_format import Zone  # noqa: E402
from notecolor.notation.score_audition import (  # noqa: E402
    PIANO_LOWER_ROW, PIANO_UPPER_ROW, pitch_for_key,
)
from notecolor.audio.sound_engine import midi_pitch  # noqa: E402
from notecolor.gui import synth_keyboard as sk  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _kit(names):
    """A tiny one-key-wide-zone kit, `low_key` ascending from 36 -- enough
    for `RowAssignments`/`SynthKeyboardBand` to resolve sample names and
    pitches without touching disk."""
    zones = []
    for i, name in enumerate(names):
        key = 36 + i
        zones.append(Zone(sample=name, low_key=key, high_key=key, root_key=key))
    return zones


class _FakeKeyEvent:
    """Stands in for a `QKeyEvent`: `SynthKeyboardBand`'s handlers only
    read `.key()`, `.isAutoRepeat()`, and `.modifiers()`, and call
    `.accept()`."""

    def __init__(self, key, auto_repeat=False, modifiers=QtCore.Qt.NoModifier):
        self._key = key
        self._auto_repeat = auto_repeat
        self._modifiers = modifiers

    def key(self):
        return self._key

    def isAutoRepeat(self):
        return self._auto_repeat

    def modifiers(self):
        return self._modifiers

    def accept(self):
        pass


def _qt_key_for(letter):
    return QtCore.Qt.Key(ord(letter.upper()))


def _press(band, letter, auto_repeat=False):
    band.keyPressEvent(_FakeKeyEvent(_qt_key_for(letter), auto_repeat))


def _release(band, letter):
    band.keyReleaseEvent(_FakeKeyEvent(_qt_key_for(letter)))


# -- KeyboardLayoutState.cycle() ---------------------------------------------

def test_layout_cycles_through_all_four_and_back():
    state = sk.KeyboardLayoutState()
    assert state.layout == sk.LAYOUT_DUAL
    assert state.cycle() == sk.LAYOUT_HYBRID
    assert state.cycle() == sk.LAYOUT_ALLPADS
    assert state.cycle() == sk.LAYOUT_CUSTOM
    assert state.cycle() == sk.LAYOUT_DUAL


# -- RowAssignments.cycle_row() wrap-around ----------------------------------

def test_cycle_row_wraps_for_synth_names():
    state = sk.KeyboardLayoutState()  # dual: both rows synth
    assignments = sk.RowAssignments(["Alpha", "Beta", "Gamma"], [])
    assert assignments.name_for("upper", state) == "Alpha"
    assignments.cycle_row("upper", state, -1)
    assert assignments.name_for("upper", state) == "Gamma"
    assignments.cycle_row("upper", state, 1)
    assignments.cycle_row("upper", state, 1)
    assignments.cycle_row("upper", state, 1)
    assert assignments.name_for("upper", state) == "Gamma"


def test_cycle_row_wraps_for_kit_names():
    state = sk.KeyboardLayoutState()
    state.layout = sk.LAYOUT_ALLPADS  # both rows pad
    assignments = sk.RowAssignments([], ["KitA", "KitB"])
    assignments.cycle_row("lower", state, -1)
    assert assignments.name_for("lower", state) == "KitB"
    assignments.cycle_row("lower", state, 1)
    assert assignments.name_for("lower", state) == "KitA"


def test_custom_split_creates_independent_left_right_assignment():
    state = sk.KeyboardLayoutState()
    state.layout = sk.LAYOUT_CUSTOM
    state.toggle_split("upper")
    assignments = sk.RowAssignments(["Alpha", "Beta"], ["KitA", "KitB"])
    # "upper" (left half) defaults to synth, "upper-r" defaults to pad
    # (kind_for_row_key's "the other kind" rule).
    assert assignments.name_for("upper", state) == "Alpha"
    assert assignments.name_for("upper-r", state) == "KitA"
    assignments.cycle_row("upper", state, 1)
    assert assignments.name_for("upper", state) == "Beta"
    # Cycling the left half must not touch the right half's own index.
    assert assignments.name_for("upper-r", state) == "KitA"
    assignments.cycle_row("upper-r", state, 1)
    assert assignments.name_for("upper-r", state) == "KitB"
    assert assignments.name_for("upper", state) == "Beta"


def _row_box(band, base_row):
    """The `QHBoxLayout` `_build_base_row` built for `base_row`, found by
    position in `band._rows_layout` (upper added first, then lower --
    see `_rebuild_structure`)."""
    index = 0 if base_row == "upper" else 1
    return band._rows_layout.itemAt(index).layout()


def _count_dividers(row_box):
    count = 0
    for i in range(row_box.count()):
        widget = row_box.itemAt(i).widget()
        if isinstance(widget, QtWidgets.QFrame) and widget.frameShape() == QtWidgets.QFrame.VLine:
            count += 1
    return count


def test_unsplit_custom_row_has_no_divider(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    band.layout_state.layout = sk.LAYOUT_CUSTOM
    band._rebuild_structure()

    assert _count_dividers(_row_box(band, "upper")) == 0
    assert _count_dividers(_row_box(band, "lower")) == 0


def test_split_custom_row_has_exactly_one_divider(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    band.layout_state.layout = sk.LAYOUT_CUSTOM
    band.layout_state.toggle_split("upper")
    band._rebuild_structure()

    assert _count_dividers(_row_box(band, "upper")) == 1
    # The untouched (unsplit) lower row still has none.
    assert _count_dividers(_row_box(band, "lower")) == 0


# -- SynthKeyboardBand: live key handling ------------------------------------

def test_synth_row_key_press_emits_correct_pitch_and_patch(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    received = []
    band.notePreviewRequested.connect(received.append)

    letter = PIANO_LOWER_ROW[3]
    _press(band, letter)

    assert len(received) == 1
    preview = received[0]
    pitch_class, octave = pitch_for_key(letter, band.base_octave)
    assert preview.pitch == midi_pitch(pitch_class, octave)
    assert preview.name == "Fat Bass"
    assert preview.channel == sk.NOTE_CHANNEL

    # The key's box is lit.
    _, key_box_row = band._row_widgets["lower"]
    boxes = band._boxes_for_row("lower")
    lit = [b for b in boxes if b["letter"] == letter]
    assert lit and lit[0]["color"] is not None


def test_pad_row_key_press_emits_sample_name(app):
    kit_zones = {"Drum Kit": _kit(["kick.wav", "snare.wav"])}
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names=kit_zones)
    band.layout_state.layout = sk.LAYOUT_HYBRID  # lower row -> pad
    band._rebuild_structure()
    received = []
    band.notePreviewRequested.connect(received.append)

    letter = PIANO_LOWER_ROW[0]
    _press(band, letter)

    assert len(received) == 1
    preview = received[0]
    assert preview.name == "kick.wav"
    assert preview.channel == sk.PAD_CHANNEL
    assert preview.pitch == 36


def test_key_repeat_does_not_double_emit(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    received = []
    band.notePreviewRequested.connect(received.append)

    letter = PIANO_UPPER_ROW[2]
    _press(band, letter)
    _press(band, letter, auto_repeat=True)
    _press(band, letter)  # even a non-auto-repeat re-press while held: no-op

    assert len(received) == 1


def test_key_release_emits_and_unlights(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    released = []
    band.noteReleased.connect(released.append)

    letter = PIANO_LOWER_ROW[5]
    _press(band, letter)
    assert letter in band._held
    _release(band, letter)

    assert released == [letter]
    assert letter not in band._held
    boxes = band._boxes_for_row("lower")
    lit = [b for b in boxes if b["letter"] == letter]
    assert lit and lit[0]["color"] is None


def test_non_piano_key_does_nothing(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    received = []
    band.notePreviewRequested.connect(received.append)

    # Not on PIANO_LOWER_ROW/PIANO_UPPER_ROW, so this falls through to
    # `super().keyPressEvent()` -- a real QKeyEvent, since Qt's own base
    # implementation (unlike this module's handlers) insists on one.
    real_event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_1,
                                 QtCore.Qt.NoModifier, "1")
    band.keyPressEvent(real_event)

    assert received == []
    assert band._held == {}


# -- centering (user feedback round on ticket #157) -------------------------

def test_dual_row_has_stretch_at_both_ends(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    row_box = _row_box(band, "upper")
    assert row_box.itemAt(0).spacerItem() is not None
    assert row_box.itemAt(row_box.count() - 1).spacerItem() is not None


# -- staggered black keys (user feedback round on ticket #157) --------------

def test_boxes_for_row_marks_black_keys_for_known_base_octave(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    boxes = band._boxes_for_row("lower")
    for letter, box in zip(PIANO_LOWER_ROW, boxes):
        pitch_class, _octave = pitch_for_key(letter, band.base_octave)
        assert box["is_black"] == (pitch_class in {1, 3, 6, 8, 10})


def test_pad_row_boxes_never_marked_black(app):
    kit_zones = {"Drum Kit": _kit(["kick.wav", "snare.wav"])}
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names=kit_zones)
    band.layout_state.layout = sk.LAYOUT_HYBRID  # lower row -> pad
    band._rebuild_structure()
    boxes = band._boxes_for_row("lower")
    assert boxes  # sanity: pad row actually produced boxes
    assert all(not b.get("is_black", False) for b in boxes)


# -- KeyBoxRow: taller fixed height + is_black storage -----------------------

def test_key_box_row_height_grew_to_fit_the_stagger(app):
    assert sk.KeyBoxRow.BOX_H + sk.KeyBoxRow.STAGGER > sk.KeyBoxRow.BOX_H
    row = sk.KeyBoxRow()
    assert row.height() == sk.KeyBoxRow.BOX_H + sk.KeyBoxRow.STAGGER


def test_key_box_row_set_boxes_stores_mixed_black_white_without_raising(app):
    row = sk.KeyBoxRow()
    boxes = [
        {"letter": "a", "label": "C4", "color": None, "is_black": False},
        {"letter": "s", "label": "Db4", "color": None, "is_black": True},
    ]
    row.set_boxes(boxes)
    assert row._boxes[0]["is_black"] is False
    assert row._boxes[1]["is_black"] is True


def _flush_deferred_deletes(app):
    for _ in range(5):
        app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        app.processEvents()


def test_rebuild_structure_does_not_leak_row_widgets_across_layout_switches(app):
    # Regression: `_rebuild_structure`'s cleanup used to check only
    # `item.widget()`, which is always None for a row added via
    # `addLayout()` -- every chip label/pill/key-box-row widget nested
    # inside a taken row silently piled up on every switch instead of
    # being deleted.
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    before = len(band.findChildren(QtWidgets.QWidget))
    band.set_layout(sk.LAYOUT_HYBRID)
    _flush_deferred_deletes(app)
    band.set_layout(sk.LAYOUT_ALLPADS)
    _flush_deferred_deletes(app)
    after = len(band.findChildren(QtWidgets.QWidget))
    assert after == before


def test_tab_cycles_layout(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    assert band.layout_state.layout == sk.LAYOUT_DUAL
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Tab))
    assert band.layout_state.layout == sk.LAYOUT_HYBRID
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Tab))
    assert band.layout_state.layout == sk.LAYOUT_ALLPADS
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Tab))
    assert band.layout_state.layout == sk.LAYOUT_CUSTOM
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Tab))
    assert band.layout_state.layout == sk.LAYOUT_DUAL


def test_up_down_shift_base_octave_within_bounds(app):
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={},
                                base_octave=config.MIN_OCTAVE)
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Down))
    assert band.base_octave == config.MIN_OCTAVE  # clamped, can't go lower

    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={},
                                base_octave=config.MAX_OCTAVE - 1)
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Up))
    assert band.base_octave == config.MAX_OCTAVE - 1  # clamped, can't go higher

    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={},
                                base_octave=config.MIN_OCTAVE)
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_Up))
    assert band.base_octave == config.MIN_OCTAVE + 1


# -- AssignmentPill: wheel/chevron -> real row cycling ----------------------

class _FakeWheel:
    """Stands in for a `QWheelEvent`: `AssignmentPill.wheelEvent()` only
    reads `angleDelta().y()` and calls `.accept()` (same shape as
    `test_synth_workspace.py`'s own `_FakeWheel` for `Knob.wheelEvent()`)."""

    def __init__(self, dy):
        self._dy = dy

    def angleDelta(self):
        return QtCore.QPoint(0, self._dy)

    def accept(self):
        pass


def test_assignment_pill_wheel_cycles_the_row(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    pill, _ = band._row_widgets["upper"]
    assert band.assignments.name_for("upper", band.layout_state) == "Alpha"

    pill.wheelEvent(_FakeWheel(120))  # positive angleDelta.y() -> step forward
    assert band.assignments.name_for("upper", band.layout_state) == "Beta"

    pill.wheelEvent(_FakeWheel(-120))  # negative -> step back
    assert band.assignments.name_for("upper", band.layout_state) == "Alpha"


def test_assignment_pill_chevrons_step_forward_and_backward(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    pill, _ = band._row_widgets["upper"]
    assert band.assignments.name_for("upper", band.layout_state) == "Alpha"

    pill._right.click()  # right chevron = +1 = next
    assert band.assignments.name_for("upper", band.layout_state) == "Beta"

    pill._left.click()  # left chevron = -1 = previous
    assert band.assignments.name_for("upper", band.layout_state) == "Alpha"

    pill._left.click()  # wraps backward
    assert band.assignments.name_for("upper", band.layout_state) == "Gamma"


def test_shift_m_emits_panic_not_note_preview(app):
    # 'm' is a live piano key on PIANO_LOWER_ROW, so Shift+M must be
    # distinguished from a plain `m` note-preview keypress (ticket #157
    # round 2) rather than also triggering a note preview.
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    panics = []
    previews = []
    band.panicRequested.connect(lambda: panics.append(True))
    band.notePreviewRequested.connect(previews.append)

    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_M, modifiers=QtCore.Qt.ShiftModifier))

    assert panics == [True]
    assert previews == []
    assert band._held == {}

    # Regression check: plain `m` (no shift) still previews a note as before.
    band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key_M))

    assert len(previews) == 1
    assert panics == [True]
