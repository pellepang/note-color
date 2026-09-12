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


# -- fill-to-available-width, not centered-with-dead-margins (ticket #184) --

def test_key_box_row_column_gets_stretch_instead_of_flanking_spacers(app):
    # Ticket #157 centered a fixed-size row with flanking stretch spacers;
    # ticket #184's bug report wants the keys themselves to grow and fill
    # the available width instead, so those flanking spacers are gone and
    # the pill+keys column now carries the row's own stretch factor.
    band = sk.SynthKeyboardBand(synth_names=["Fat Bass"], kit_zone_names={})
    row_box = _row_box(band, "upper")
    assert all(item.spacerItem() is None for item in
               (row_box.itemAt(i) for i in range(row_box.count())))
    assert row_box.stretch(row_box.count() - 1) > 0


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

def test_key_box_row_minimum_height_grew_to_fit_the_stagger(app):
    # Ticket #184 bug #2: KeyBoxRow no longer pins itself to a fixed
    # pixel size (it resizes to fill whatever room its layout gives it,
    # via `_geometry()` at paint time) -- but it still reports a real
    # minimum, with equal top/bottom margin so the black+white contour
    # centers instead of sitting flush top/bottom.
    assert sk.KeyBoxRow.BOX_SIZE + sk.KeyBoxRow.STAGGER > sk.KeyBoxRow.BOX_SIZE
    row = sk.KeyBoxRow()
    expected = sk.KeyBoxRow.BOX_SIZE + sk.KeyBoxRow.STAGGER + 2 * sk.KeyBoxRow.MARGIN
    assert row.minimumSizeHint().height() == expected
    assert row.sizePolicy().verticalPolicy() == QtWidgets.QSizePolicy.Expanding
    assert row.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Expanding


def test_key_box_row_geometry_scales_up_to_fill_extra_height_and_stays_square(app):
    # Ticket #186: boxes must be square (width == height) at every scale,
    # not just resized to fill height with an arbitrary width.
    row = sk.KeyBoxRow()
    row.set_boxes([{"letter": "a", "label": "C4", "color": None, "is_black": False}])
    ref_height = sk.KeyBoxRow.BOX_SIZE + sk.KeyBoxRow.STAGGER + 2 * sk.KeyBoxRow.MARGIN
    # Wide enough that width is never the limiting dimension for one box.
    wide = 4000

    row.resize(wide, ref_height)
    box_side, *_ = row._geometry()
    assert box_side == pytest.approx(sk.KeyBoxRow.BOX_SIZE)

    row.resize(wide, ref_height * 2)
    box_side_scaled, *_ = row._geometry()
    assert box_side_scaled == pytest.approx(sk.KeyBoxRow.BOX_SIZE * 2)


def test_key_box_row_geometry_square_boxes_scale_down_to_fit_narrow_width(app):
    # Ticket #186: when width is the tighter constraint (many boxes, a
    # narrow footer), boxes shrink together and stay square rather than
    # a height-only scale overflowing the available width.
    row = sk.KeyBoxRow()
    boxes = [{"letter": c, "label": c.upper(), "color": None, "is_black": False}
              for c in PIANO_UPPER_ROW]
    row.set_boxes(boxes)
    tall = 4000
    # Enough width for ~2x scale (comfortably above the unscaled minimum,
    # so this exercises "width is the tighter constraint" rather than the
    # unrelated "can't shrink below the reference size" floor).
    narrow = 900

    row.resize(narrow, tall)
    box_side, gap, _stagger, _margin, x_offset, y_offset = row._geometry()
    count = len(boxes)
    content_width = count * (box_side + gap) - gap
    assert content_width <= narrow + 1e-6
    assert x_offset >= 0
    assert y_offset >= 0


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


def test_assignment_pill_click_opens_row_popup(app):
    # Ticket #184 traded the chevrons for click-to-open-popup; wheel-to-step
    # (tested above) remains the fast path.
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    pill, _ = band._row_widgets["upper"]
    opened = []
    band._open_row_popup = lambda row_key: opened.append(row_key)  # patched pre-build closure target

    pill.clicked.emit()

    assert opened == ["upper"]


def test_assignment_pill_click_opens_a_real_visible_popup_with_folders(app):
    # Ticket #187: #184's two prior passes both shipped a pill that opened
    # nothing verifiable. `_open_row_popup` now uses `QMenu.popup()`
    # (non-blocking) rather than `.exec()` (which runs its own nested event
    # loop and blocks the caller until dismissed, making it undrivable
    # here) specifically so this can be checked directly instead of via a
    # stubbed-out `_open_row_popup`.
    band = sk.SynthKeyboardBand(
        synth_names=["Fat Bass", "Glass Keys", "808 Sub"], kit_zone_names={},
        patch_folders={"Fat Bass": "Saw", "808 Sub": "Saw", "Glass Keys": "Sine"})
    pill, _ = band._row_widgets["upper"]

    pill.clicked.emit()

    menu = band._active_popup
    assert menu is not None
    assert menu.isVisible()
    actions = menu.actions()
    # Folder headers are `QWidgetAction`s wrapping a plain `QLabel` (not
    # `addSection()`) -- see `_add_popup_header`'s docstring for why a
    # titled separator's text silently stopped rendering once this menu's
    # QSS was applied, and this is what that regression check verifies:
    # each header's actual label widget carries the folder name as real,
    # visible text.
    headers = [a.defaultWidget().text() for a in actions if isinstance(a, QtWidgets.QWidgetAction)]
    assert headers == ["SAW", "SINE"]  # folder grouping from real patch_folders data
    for a in actions:
        if isinstance(a, QtWidgets.QWidgetAction):
            assert a.defaultWidget().isVisible()
    item_texts = [a.text() for a in actions
                  if not isinstance(a, QtWidgets.QWidgetAction) and not a.isSeparator()]
    assert item_texts == ["Fat Bass", "808 Sub", "Glass Keys"]
    menu.close()


def test_picking_a_popup_action_assigns_the_row(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    pill, _ = band._row_widgets["upper"]

    pill.clicked.emit()
    menu = band._active_popup
    beta_action = next(a for a in menu.actions() if a.text() == "Beta")
    menu.triggered.emit(beta_action)

    assert band.assignments.name_for("upper", band.layout_state) == "Beta"


def test_assignment_pill_paints_a_visible_panel_background(app):
    # Root cause of #184's invisible pill: a plain QWidget subclass with
    # only setStyleSheet("background: ...") never actually paints that
    # background unless WA_StyledBackground is set -- Qt's stylesheet
    # engine skips the fill entirely for a bare QWidget otherwise. Render
    # the pill and confirm its fill colour comes through onscreen (a pixel
    # inside its bounds, away from the centered label text, should match
    # theme.PANEL rather than the parent's own background).
    band = sk.SynthKeyboardBand(synth_names=["Alpha"], kit_zone_names={})
    band.resize(300, 200)
    band.show()
    QtWidgets.QApplication.processEvents()
    pill, _ = band._row_widgets["upper"]

    assert pill.testAttribute(QtCore.Qt.WA_StyledBackground)

    img = pill.grab().toImage()
    corner = img.pixelColor(1, 1)  # top-left corner, off the centered label
    from notecolor.gui import theme
    expected = theme.PANEL
    assert abs(corner.red() - expected.red()) <= 2
    assert abs(corner.green() - expected.green()) <= 2
    assert abs(corner.blue() - expected.blue()) <= 2
    band.close()


def test_assign_row_jumps_straight_to_the_named_patch_and_records_recent(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    assert band.assignments.name_for("upper", band.layout_state) == "Alpha"

    band._assign_row("upper", "Gamma")

    assert band.assignments.name_for("upper", band.layout_state) == "Gamma"
    assert band.assignments.recents[0] == "Gamma"


# -- per-key override (ticket #184) ------------------------------------------

def test_key_override_wins_over_row_default_for_note_preview(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    received = []
    band.notePreviewRequested.connect(received.append)
    letter = PIANO_LOWER_ROW[0]

    band._assign_key("lower", letter, "Beta")
    _press(band, letter)

    assert received[0].name == "Beta"
    boxes = band._boxes_for_row("lower")
    box = next(b for b in boxes if b["letter"] == letter)
    assert box["overridden"] is True
    other = next(b for b in boxes if b["letter"] != letter)
    assert other["overridden"] is False


def test_key_override_ignored_when_name_not_in_kind_list(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha"], kit_zone_names={})
    letter = PIANO_LOWER_ROW[0]

    band._assign_key("lower", letter, "Nonexistent")

    assert ("lower", letter) not in band.assignments.key_override


def test_key_box_click_opens_the_same_popup_scoped_to_that_key(app):
    # Ticket #188: a left click on an individual key box opens the exact
    # same folder-grouped popup picker #187 fixed for the row pill, but
    # scoped to overriding just that one key rather than the whole row.
    # `.popup()` (not `.exec()`) is what makes this drivable at all -- see
    # `_show_popup`'s docstring and #187's comment on this same repo class.
    band = sk.SynthKeyboardBand(
        synth_names=["Fat Bass", "Glass Keys", "808 Sub"], kit_zone_names={},
        patch_folders={"Fat Bass": "Saw", "808 Sub": "Saw", "Glass Keys": "Sine"})
    _, key_box_row = band._row_widgets["lower"]
    letter = PIANO_LOWER_ROW[2]

    key_box_row.boxClicked.emit(letter)

    menu = band._active_popup
    assert menu is not None
    assert menu.isVisible()
    actions = menu.actions()
    headers = [a.defaultWidget().text() for a in actions if isinstance(a, QtWidgets.QWidgetAction)]
    assert headers == ["SAW", "SINE"]
    item_texts = [a.text() for a in actions
                  if not isinstance(a, QtWidgets.QWidgetAction) and not a.isSeparator()]
    assert item_texts == ["Fat Bass", "808 Sub", "Glass Keys"]
    # Scoped to the row's default (no override set yet), same as the row
    # popup's own "current" checkmark convention.
    checked = next(a.text() for a in actions
                   if not isinstance(a, QtWidgets.QWidgetAction) and a.isCheckable() and a.isChecked())
    assert checked == "Fat Bass"
    menu.close()


def test_picking_a_key_popup_action_sets_only_that_key_override(app):
    band = sk.SynthKeyboardBand(
        synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    _, key_box_row = band._row_widgets["lower"]
    letter = PIANO_LOWER_ROW[2]
    other_letter = PIANO_LOWER_ROW[3]

    key_box_row.boxClicked.emit(letter)
    menu = band._active_popup
    beta_action = next(a for a in menu.actions() if a.text() == "Beta")
    menu.triggered.emit(beta_action)

    assert band.assignments.key_override[("lower", letter)] == "Beta"
    assert ("lower", other_letter) not in band.assignments.key_override
    # Row default is untouched -- only the one key was overridden.
    assert band.assignments.name_for("lower", band.layout_state) == "Alpha"

    boxes = band._boxes_for_row("lower")
    overridden_box = next(b for b in boxes if b["letter"] == letter)
    plain_box = next(b for b in boxes if b["letter"] == other_letter)
    assert overridden_box["overridden"] is True
    assert plain_box["overridden"] is False


def test_key_box_paints_a_visible_override_marker_dot(app):
    # Real-pixel regression guard (companion to the pill's own
    # WA_StyledBackground pixel test): an overridden key box's top-right
    # corner should actually render the amber marker dot, and a plain
    # neighbouring box's same corner should not.
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    band.resize(520, 260)
    band.show()
    QtWidgets.QApplication.processEvents()
    _, key_box_row = band._row_widgets["lower"]
    letter = PIANO_LOWER_ROW[2]
    other_letter = PIANO_LOWER_ROW[3]

    band._assign_key("lower", letter, "Beta")
    QtWidgets.QApplication.processEvents()

    box_side, gap, stagger, margin, x_offset, y_offset = key_box_row._geometry()
    boxes = band._boxes_for_row("lower")

    def corner_pixel(target_letter):
        index = next(i for i, b in enumerate(boxes) if b["letter"] == target_letter)
        box = boxes[index]
        x = x_offset + index * (box_side + gap)
        y = y_offset + (margin if box.get("is_black") else margin + stagger)
        img = key_box_row.grab().toImage()
        return img.pixelColor(int(x + box_side - 3), int(y + 3))

    from notecolor.gui import theme
    overridden_corner = corner_pixel(letter)
    plain_corner = corner_pixel(other_letter)

    amber = QtGui.QColor(theme.AMBER)
    assert abs(overridden_corner.red() - amber.red()) <= 12
    assert abs(overridden_corner.green() - amber.green()) <= 12
    assert abs(overridden_corner.blue() - amber.blue()) <= 12
    # The plain box's same corner must not read as amber.
    assert not (abs(plain_corner.red() - amber.red()) <= 12
                and abs(plain_corner.green() - amber.green()) <= 12
                and abs(plain_corner.blue() - amber.blue()) <= 12)
    band.close()


# -- recents (ticket #184) ----------------------------------------------------

def test_cycling_and_assigning_records_recents_most_recent_first(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    band._cycle_row("upper", 1)   # -> Beta
    band._assign_row("upper", "Gamma")

    assert band.assignments.recents[:2] == ["Gamma", "Beta"]


def test_recents_rail_reflects_current_tabs_recents(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    band._assign_row("upper", "Beta")

    labels = [w.text() for w in band.recents_rail.findChildren(sk._RecentChip)]
    assert labels == ["Beta"]


def test_recents_rail_spans_the_full_footer_width(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    band.resize(900, 360)
    band.show()
    # Without this the band's layout has never run, so every child is
    # still at its default geometry and the width check below is
    # meaningless (the first draft of this test asserted against exactly
    # that unlaid-out state and failed).
    band.layout().activate()
    band._assign_row("upper", "Beta")

    # Same left/right inset (4px, `_body`'s contents margins) as every
    # other row in the band -- ticket #189 requires the rail to read as
    # the footer's full width, not a narrower strip floating inside it.
    assert band.recents_rail.x() == 4
    assert band.recents_rail.width() == band.width() - 2 * 4


def test_repopulating_the_rail_leaves_no_stale_chip_on_screen(app):
    # Regression test for ticket #189: RecentsRail.set_names() used to
    # removeWidget() an outgoing chip and deleteLater() it, but never
    # hide() it -- removeWidget() only stops layout management, it
    # doesn't hide the widget, so the outgoing chip kept painting at its
    # old on-screen position (visibly overlapping the "RECENT" label and
    # the incoming chips) until deleteLater()'s deferred deletion
    # actually ran. Caught by grabbing a real re-populated rail rather
    # than trusting `_chips`/layout state alone.
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    band.resize(900, 360)
    band.show()

    band._assign_row("upper", "Beta")
    band._assign_row("upper", "Gamma")  # triggers a second set_names() call
    band.layout().activate()

    # `deleteLater()` is deferred, so the outgoing chips are still
    # children here -- which is exactly the point: what matters is that
    # they are no longer *visible*. Note they still carry overlapping
    # geometry (a stale chip sits right where a live one now draws), so
    # visibility is the only thing keeping them off screen.
    all_chips = band.recents_rail.findChildren(sk._RecentChip)
    assert len(all_chips) > 2, "expected pending-deletion chips to still be children"
    visible = [c.name for c in all_chips if c.isVisible()]
    assert visible == ["Gamma", "Beta"]


# -- per-layout-tab persistence (ticket #184) ---------------------------------

def test_each_layout_tab_keeps_its_own_row_assignment():
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    band._cycle_row("upper", 1)  # dual tab: upper -> Beta

    band.set_layout(sk.LAYOUT_HYBRID)
    assert band.assignments.name_for("upper", band.layout_state) == "Alpha"  # fresh tab
    band._cycle_row("upper", 2)  # hybrid tab: upper -> Gamma

    band.set_layout(sk.LAYOUT_DUAL)
    assert band.assignments.name_for("upper", band.layout_state) == "Beta"  # restored

    band.set_layout(sk.LAYOUT_HYBRID)
    assert band.assignments.name_for("upper", band.layout_state) == "Gamma"  # restored


def test_snapshot_and_restore_assignments_round_trips_every_tab():
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    band._cycle_row("upper", 1)  # dual tab: upper -> Beta
    band.set_layout(sk.LAYOUT_HYBRID)
    band._assign_row("upper", "Beta")  # hybrid's upper is synth-kind too
    snapshot = band.snapshot_assignments()

    fresh = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    fresh.restore_assignments(snapshot)

    assert fresh._assignments_by_layout[sk.LAYOUT_DUAL].name_for(
        "upper", sk.KeyboardLayoutState()) == "Beta"
    hybrid_state = sk.KeyboardLayoutState()
    hybrid_state.layout = sk.LAYOUT_HYBRID
    assert fresh._assignments_by_layout[sk.LAYOUT_HYBRID].name_for("upper", hybrid_state) == "Beta"


# -- popup folders (ticket #184) ----------------------------------------------

def test_folders_for_kind_groups_by_supplied_patch_folder():
    band = sk.SynthKeyboardBand(
        synth_names=["Fat Bass", "Glass Keys", "808 Sub"], kit_zone_names={},
        patch_folders={"Fat Bass": "Saw", "808 Sub": "Saw", "Glass Keys": "Sine"})

    folders = band._folders_for_kind("synth")

    assert folders == {"Saw": ["Fat Bass", "808 Sub"], "Sine": ["Glass Keys"]}


def test_folders_for_kind_falls_back_to_other_when_unmapped():
    band = sk.SynthKeyboardBand(synth_names=["Mystery"], kit_zone_names={})
    assert band._folders_for_kind("synth") == {sk.OTHER_FOLDER: ["Mystery"]}


# -- drag-and-drop assignment (ticket #184) -----------------------------------

class _FakeMimeData:
    def __init__(self, text):
        self._text = text

    def hasText(self):
        return True

    def text(self):
        return self._text


class _FakeDropEvent:
    def __init__(self, text, x=0.0):
        self._mime = _FakeMimeData(text)
        self._x = x

    def mimeData(self):
        return self._mime

    def position(self):
        return QtCore.QPointF(self._x, 0.0)

    def acceptProposedAction(self):
        pass


def test_pill_drop_assigns_the_row(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    pill, _ = band._row_widgets["upper"]

    pill.dropEvent(_FakeDropEvent("Beta"))

    assert band.assignments.name_for("upper", band.layout_state) == "Beta"


def test_key_box_drop_assigns_just_that_key(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta"], kit_zone_names={})
    _, key_box_row = band._row_widgets["lower"]
    letter = PIANO_LOWER_ROW[0]
    # Boxes are centered horizontally (ticket #186), so the first box no
    # longer starts at x=0 -- ask `_geometry()` for the real offset rather
    # than assuming one.
    _box_side, _gap, _stagger, _margin, x_offset, _y_offset = key_box_row._geometry()
    box_x = x_offset + 0.5 * sk.KeyBoxRow.BOX_SIZE  # inside the first box

    key_box_row.dropEvent(_FakeDropEvent("Beta", x=box_x))

    assert band.assignments.key_override[("lower", letter)] == "Beta"


def _chip_drag_text(chip):
    """The plain text a real `_RecentChip` drag would carry. `mousePressEvent`
    itself can't be driven here -- `QDrag.exec()` spins its own blocking event
    loop -- so this mirrors the one line of mime payload it builds, the same
    way `Canvas.spawn_module()` is exercised without synthesizing real Qt DnD
    (issue #175)."""
    return chip.name


def test_dragging_a_rail_chip_onto_a_key_assigns_that_key(app):
    # End-to-end for ticket #189: the rail is the drag *source*, the key
    # box is the drop target, and the payload connecting them is the
    # chip's own name.
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    band.resize(900, 300)
    band.show()
    band.layout().activate()
    band._assign_row("upper", "Gamma")  # puts "Gamma" in the rail

    chip = band.recents_rail._chips[0]
    assert _chip_drag_text(chip) == "Gamma"

    _, key_box_row = band._row_widgets["lower"]
    letter = PIANO_LOWER_ROW[2]
    box_side, gap, _stagger, _margin, x_offset, _y_offset = key_box_row._geometry()
    box_x = x_offset + 2 * (box_side + gap) + box_side / 2

    key_box_row.dropEvent(_FakeDropEvent(_chip_drag_text(chip), x=box_x))

    assert band.assignments.key_override[("lower", letter)] == "Gamma"
    # ...and the key now paints its per-key override marker.
    assert key_box_row._boxes[2]["overridden"] is True
    # Untouched neighbours keep the row's own assignment (no override).
    assert ("lower", PIANO_LOWER_ROW[1]) not in band.assignments.key_override


def test_dragging_a_rail_chip_onto_a_pill_assigns_the_whole_row(app):
    band = sk.SynthKeyboardBand(synth_names=["Alpha", "Beta", "Gamma"], kit_zone_names={})
    band.resize(900, 300)
    band.show()
    band.layout().activate()
    band._assign_row("upper", "Gamma")

    chip = band.recents_rail._chips[0]
    pill, _ = band._row_widgets["lower"]
    pill.dropEvent(_FakeDropEvent(_chip_drag_text(chip)))

    assert band.assignments.name_for("lower", band.layout_state) == "Gamma"


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
