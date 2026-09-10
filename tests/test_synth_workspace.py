"""Stage 1 of the Synth View (map #145, ticket #157): the reusable
freeform window manager in `gui/synth_workspace.py`, tested independent of
any synth-specific wiring (`ModuleWindow`/`Knob`/`Drawer`/`Canvas` know
nothing about `Patch` or `synth_params`).

Same offscreen-Qt discipline as `test_studio_piano_roll.py`: no display, no
audio device, direct method calls over simulated `QDrag`/wheel events where
synthesizing real Qt input is impractical (drag-to-move, drawer drop).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from notecolor.audio import effects as effects_module  # noqa: E402
from notecolor.gui import synth_workspace as sw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeWheel:
    """Stands in for a `QWheelEvent`: `Knob.wheelEvent()` only reads
    `angleDelta().y()`, `modifiers()`, and calls `accept()`."""

    def __init__(self, dy, shift=False):
        self._dy = dy
        self._shift = shift

    def angleDelta(self):
        return QtCore.QPoint(0, self._dy)

    def modifiers(self):
        return QtCore.Qt.ShiftModifier if self._shift else QtCore.Qt.NoModifier

    def accept(self):
        pass


# -- ModuleWindow: drag-to-move ---------------------------------------------

def test_module_window_drag_moves_and_clamps(app):
    canvas = sw.Canvas(module_factory=lambda key, pos: None)
    canvas.resize(400, 300)
    window = sw.ModuleWindow("osc2", "Osc 2")
    canvas.add_window(window)
    window.move(10, 10)

    window._title_press(QtCore.QPoint(5, 5))
    window._title_move(QtCore.QPoint(105, 105))
    window._title_release()

    assert window.pos() == QtCore.QPoint(100, 100)

    # Drag far past the canvas bounds -- clamped, never off-canvas.
    window._title_press(QtCore.QPoint(0, 0))
    window._title_move(QtCore.QPoint(10_000, 10_000))
    window._title_release()

    max_x = canvas.width() - window.width()
    max_y = canvas.height() - window.height()
    assert window.pos() == QtCore.QPoint(max_x, max_y)


def test_module_window_close_emits_type_key(app):
    window = sw.ModuleWindow("filter", "Filter")
    received = []
    window.closed.connect(received.append)
    window.request_close()
    assert received == ["filter"]


def test_module_window_set_knobs(app):
    window = sw.ModuleWindow("filter", "Filter")
    calls = []
    window.set_knobs([
        ("Cutoff", "440Hz", lambda d: calls.append(("cutoff", d))),
        ("Res", "0.30", lambda d: calls.append(("res", d))),
    ])
    knobs = window.knobs()
    assert len(knobs) == 2
    knobs[0].wheelEvent(_FakeWheel(120))
    assert calls == [("cutoff", 1)]


# -- Knob wheel interaction --------------------------------------------------

def test_knob_wheel_up_emits_positive(app):
    knob = sw.Knob("Cutoff", "440Hz", 0.0)
    received = []
    knob.wheelStepped.connect(received.append)
    knob.wheelEvent(_FakeWheel(120))
    assert received == [1]
    assert knob.last_shift is False


def test_knob_wheel_down_emits_negative(app):
    knob = sw.Knob("Cutoff", "440Hz", 0.0)
    received = []
    knob.wheelStepped.connect(received.append)
    knob.wheelEvent(_FakeWheel(-120))
    assert received == [-1]


def test_knob_wheel_shift_is_exposed(app):
    knob = sw.Knob("Cutoff", "440Hz", 0.0)
    knob.wheelEvent(_FakeWheel(120, shift=True))
    assert knob.last_shift is True


def test_knob_set_display(app):
    knob = sw.Knob("Cutoff", "440Hz", 0.0)
    knob.set_display("880Hz", 90.0)
    assert knob._value_text == "880Hz"
    assert knob._rotation == 90.0


# -- Drawer: exactly the real-DSP effects, plus disabled CLAP --------------

def test_drawer_contents_match_effects_registry(app):
    drawer = sw.Drawer()
    rows = drawer.findChildren(sw._DrawerRow)
    keys = [row.type_key for row in rows]

    core_keys = [k for k, _ in sw.SYNTH_CORE_MODULES]
    for key in core_keys:
        assert key in keys

    assert set(effects_module.EFFECT_TYPES) == {"delay", "chorus"}
    for key in effects_module.EFFECT_TYPES:
        assert key in keys

    assert sw.CLAP_TYPE_KEY in keys
    clap_row = next(r for r in rows if r.type_key == sw.CLAP_TYPE_KEY)
    assert clap_row.isEnabled() is False

    # Nothing beyond core + real effects + the CLAP placeholder.
    assert len(keys) == len(core_keys) + len(effects_module.EFFECT_TYPES) + 1


def test_drawer_has_instruments_group_label_before_clap_row(app):
    drawer = sw.Drawer()
    children = drawer.findChildren(QtWidgets.QWidget)
    labels = [w for w in children if isinstance(w, QtWidgets.QLabel) and w.text() == "Instruments"]
    assert len(labels) == 1

    clap_row = next(r for r in drawer.findChildren(sw._DrawerRow) if r.type_key == sw.CLAP_TYPE_KEY)
    assert children.index(labels[0]) < children.index(clap_row)


def test_drawer_toggle_collapses(app):
    drawer = sw.Drawer()
    assert drawer.expanded is True
    drawer.toggle()
    assert drawer.expanded is False
    assert drawer.width() < drawer.WIDTH
    drawer.toggle()
    assert drawer.expanded is True


# -- Canvas.tidy(): grid math ported from the prototype ---------------------

@pytest.mark.parametrize("width,height,count,expected", [
    (400, 300, 1, [(105, 84)]),
    (400, 300, 2, [(105, 14), (105, 160)]),
    (800, 300, 2, [(203, 84), (407, 84)]),
    (800, 300, 5, [(101, 14), (305, 14), (509, 14), (101, 160), (305, 160)]),
])
def test_tidy_grid_math(width, height, count, expected):
    assert sw.Canvas.tidy_grid(width, height, count) == expected


def test_tidy_repositions_windows(app):
    canvas = sw.Canvas(module_factory=lambda key, pos: None)
    canvas.resize(800, 300)
    windows = [canvas.add_window(sw.ModuleWindow(f"m{i}", f"M{i}")) for i in range(5)]
    for i, window in enumerate(windows):
        window.move(999 - i, 999 - i)   # scramble, in reverse-(y,x) order

    canvas.tidy()

    expected = sw.Canvas.tidy_grid(800, 300, 5)
    # tidy() sorts by (y, x) before packing -- the scrambled windows were
    # placed in exactly reverse order, so tidy() should reverse them back.
    actual = [(w.x(), w.y()) for w in sorted(canvas.windows(), key=lambda w: w.type_key)]
    assert sorted(actual) == sorted(expected)


# -- Canvas: window bookkeeping and drop handling ---------------------------

def test_canvas_window_count_tracks_close(app):
    canvas = sw.Canvas(module_factory=lambda key, pos: None)
    canvas.resize(400, 300)
    window = canvas.add_window(sw.ModuleWindow("osc2", "Osc 2"))
    assert canvas.window_count() == 1
    window.request_close()
    assert canvas.window_count() == 0


def test_canvas_spawn_module_via_factory(app):
    canvas = sw.Canvas(module_factory=lambda key, pos: sw.ModuleWindow(key, key.title()))
    canvas.resize(400, 300)
    window = canvas.spawn_module("filter", QtCore.QPoint(50, 60))
    assert window is not None
    assert window.type_key == "filter"
    assert canvas.window_count() == 1
    assert window.pos() == QtCore.QPoint(50, 60)


def test_canvas_spawn_module_clamps_drop_position(app):
    canvas = sw.Canvas(module_factory=lambda key, pos: sw.ModuleWindow(key, key.title()))
    canvas.resize(400, 300)
    window = canvas.spawn_module("filter", QtCore.QPoint(10_000, 10_000))
    assert window.x() <= canvas.width() - 1
    assert window.y() <= canvas.height() - 1


def test_canvas_spawn_module_factory_declines(app):
    canvas = sw.Canvas(module_factory=lambda key, pos: None)
    canvas.resize(400, 300)
    result = canvas.spawn_module("clap_plugin", QtCore.QPoint(10, 10))
    assert result is None
    assert canvas.window_count() == 0
