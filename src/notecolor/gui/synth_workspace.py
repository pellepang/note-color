"""The Synth View's reusable "freeform window manager" half (map #145,
ticket #157, stage 1 of 3): `ModuleWindow`, `Knob`, `Drawer`, `Canvas`.

Deliberately synth-agnostic -- nothing here knows what a "type key" means
(`osc2`, `filter`, `reverb`, ...) or how a knob maps to a `Patch` field.
That vocabulary belongs to `synth_view.py` (stage 3), which supplies a
`module_factory` callback to `Canvas` and populates each `ModuleWindow`'s
knobs via `set_knobs()`. Keeping the split means this module can be tested,
and eventually reused, with no `Patch`/`synth_params` import at all.

Follows `gui/piano_roll_panel.py`'s drag idiom throughout: plain `QWidget`s
positioned with `.move()`, a `self._drag` dict populated on press and
cleared on release, no `QGraphicsProxyWidget`, no `QMdiArea` -- see that
module's docstring for why this codebase never hosts widgets in a
`QGraphicsScene` for this kind of thing.
"""

import math

from PySide6 import QtCore, QtGui, QtWidgets

from notecolor.gui import theme

#: Module window body -- one knob's footprint (spec: prototype's
#: `.knob-wrap`), used both for painting and for the flex-wrap row layout.
KNOB_SIZE = 40
KNOB_GAP = 10

#: Tidy's grid math, ported 1:1 from the accepted prototype's `tidy()`.
TIDY_SLOT_W = 190
TIDY_SLOT_H = 132
TIDY_GAP = 14
TIDY_MARGIN = 14

#: Canvas background grid spacing (prototype: `radial-gradient(... 22px)`).
CANVAS_GRID_SPACING = 22

#: Effect types with real DSP behind them, kept in the same order
#: `audio/effects.py`'s own `EFFECT_TYPES` registry declares them --
#: reread from there rather than hand-copied, so a future effect (reverb,
#: per that module's own docstring) shows up here for free.
def _effect_drawer_entries():
    from notecolor.audio import effects

    labels = {"delay": "Delay", "chorus": "Chorus"}
    return [(kind, labels.get(kind, kind.title())) for kind in effects.EFFECT_TYPES]


#: Synth-core drawer rows -- fixed, not derived from anywhere else, since
#: these are always-available VisualNote Studio synth-engine stages (not
#: gated on any registry the way effects are).
SYNTH_CORE_MODULES = [
    ("osc2", "Osc 2"),
    ("noise", "Noise"),
    ("lfo", "LFO"),
    ("filter_env", "Filter Env"),
    ("voice", "Voice"),
]

#: Always present, never enabled -- the map's "kept in mind, not built"
#: CLAP loader placeholder.
CLAP_TYPE_KEY = "clap_plugin"


class Knob(QtWidgets.QWidget):
    """One rotary control: a painted circular indicator with a line rotated
    to the current value's angle (a clock hand), a label below, and a value
    readout below that -- matching the prototype's `.knob`/`.knob-label`/
    `.knob-val` stack.

    Knows nothing about what it controls. `set_display()` is the only way
    its rotation/text change; `wheelStepped` is the only way it reports
    interaction. The `Patch` field <-> angle mapping is stage 3's job
    (`settings/synth_params.step_value()`).
    """

    #: +1/-1 direction. `Qt.KeyboardModifiers` isn't passed along -- the
    #: caller who cares about a coarse (Shift-held) step reads
    #: `last_shift` right after the signal fires, same turn, before any
    #: other wheel event can land.
    wheelStepped = QtCore.Signal(int)

    def __init__(self, label="", value_text="", rotation_degrees=0.0, parent=None):
        super().__init__(parent)
        self._label = label
        self._value_text = value_text
        self._rotation = rotation_degrees
        #: Set immediately before `wheelStepped` fires each time, so a
        #: caller connected to the signal can read it in the same handler
        #: without this widget needing to know about `Patch`/coarse-step
        #: semantics at all.
        self.last_shift = False
        self.setFixedSize(KNOB_SIZE, KNOB_SIZE + 24)
        self.setToolTip(label)

    def set_display(self, value_text, rotation_degrees):
        self._value_text = value_text
        self._rotation = rotation_degrees
        self.update()

    def set_label(self, label):
        self._label = label
        self.setToolTip(label)
        self.update()

    def wheelEvent(self, event):
        self.last_shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        direction = 1 if event.angleDelta().y() > 0 else -1
        self.wheelStepped.emit(direction)
        event.accept()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        d = KNOB_SIZE
        centre = QtCore.QPointF(d / 2, d / 2)
        radius = d / 2 - 2

        p.setPen(QtGui.QPen(theme.RULE_STRONG, 1.5))
        p.setBrush(QtGui.QBrush(theme.CHROME_DEEP))
        p.drawEllipse(centre, radius, radius)

        angle_rad = math.radians(self._rotation - 90)
        hand_end = QtCore.QPointF(centre.x() + radius * 0.8 * math.cos(angle_rad),
                                  centre.y() + radius * 0.8 * math.sin(angle_rad))
        p.setPen(QtGui.QPen(theme.COPPER, 2))
        p.drawLine(centre, hand_end)

        p.setPen(theme.TEXT_DIM)
        p.setFont(theme.font(7))
        p.drawText(QtCore.QRectF(0, d, d, 12), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                  self._label)
        p.setPen(theme.TEXT)
        p.setFont(theme.font(7, bold=True))
        p.drawText(QtCore.QRectF(0, d + 12, d, 12), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                  self._value_text)


class ModuleWindow(QtWidgets.QWidget):
    """One open module: a title bar (dot + title + tag + close) you drag by,
    over a flex-wrap-like row of `Knob`s. Positioned with `.move()` inside
    whatever parent (`Canvas`) hosts it -- this widget clamps its own drag to
    that parent's bounds, mirroring `piano_roll_panel.py`'s note-drag clamp.
    """

    #: The module's type key, so the owner (stage 3) knows which patch
    #: section/effect instance just closed.
    closed = QtCore.Signal(str)
    #: Fires on every drag step and on release -- window layout is per-patch
    #: state the owner may want to snapshot (see the plan's note); this
    #: keeps that possible without this widget knowing about persistence.
    moved = QtCore.Signal(str)

    TITLE_H = 26

    def __init__(self, type_key, title, tag="", dot_color=None, knob_specs=(), parent=None):
        super().__init__(parent)
        self.type_key = type_key
        self._drag = None
        self.setFixedWidth(TIDY_SLOT_W - TIDY_GAP)
        self.setAutoFillBackground(False)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title_bar = _TitleBar(self, title, tag, dot_color)
        outer.addWidget(self._title_bar)

        self._body = QtWidgets.QWidget(self)
        self._body.setStyleSheet(f"background: {theme.rgba(theme.PANEL)};")
        self._flow = _FlowLayout(self._body, margin=8, spacing=KNOB_GAP)
        outer.addWidget(self._body)

        self._knobs = []
        self.set_knobs(knob_specs)
        self.adjustSize()

    # -- content ------------------------------------------------------------

    def set_knobs(self, knob_specs):
        """`knob_specs`: iterable of `(label, value_text, on_wheel)`, where
        `on_wheel(direction)` is called with the knob's `wheelStepped`
        value. Replaces whatever knobs were there before."""
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._knobs = []
        for label, value_text, on_wheel in knob_specs:
            knob = Knob(label, value_text, 0.0, parent=self._body)
            if on_wheel is not None:
                knob.wheelStepped.connect(on_wheel)
            self._flow.addWidget(knob)
            self._knobs.append(knob)

    def knobs(self):
        return list(self._knobs)

    # -- drag-to-move, following piano_roll_panel.py's `_drag` idiom --------

    def _title_press(self, pos):
        self._drag = {"grab": pos, "from": self.pos()}

    def _title_move(self, global_pos):
        if not self._drag:
            return
        # Computed from the original grab offset each move, not
        # incrementally, so rounding never accumulates drift over a long
        # drag.
        new_pos = global_pos - self._drag["grab"]
        self._move_clamped(new_pos)
        self.moved.emit(self.type_key)

    def _title_release(self):
        self._drag = None
        self.moved.emit(self.type_key)

    def _move_clamped(self, pos):
        parent = self.parentWidget()
        if parent is not None:
            max_x = max(0, parent.width() - self.width())
            max_y = max(0, parent.height() - self.height())
            pos = QtCore.QPoint(max(0, min(max_x, pos.x())),
                               max(0, min(max_y, pos.y())))
        self.move(pos)

    def closeEvent(self, event):
        self.closed.emit(self.type_key)
        super().closeEvent(event)

    def request_close(self):
        """Programmatic close (the × button) -- goes through `close()` so
        `closeEvent()` (and thus `closed`) fires exactly the same as a
        window-manager close would."""
        self.close()


class _TitleBar(QtWidgets.QWidget):
    """The draggable strip: coloured dot, title, tag, close button. A
    private helper -- `ModuleWindow` is the public surface; nothing outside
    this module should construct a `_TitleBar` directly."""

    def __init__(self, module, title, tag, dot_color, parent=None):
        super().__init__(parent)
        self._module = module
        self.setFixedHeight(ModuleWindow.TITLE_H)
        self.setStyleSheet(f"background: {theme.rgba(theme.CHROME)};")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(6)

        self._dot = QtWidgets.QWidget(self)
        self._dot.setFixedSize(8, 8)
        colour = dot_color or theme.COPPER
        self._dot.setStyleSheet(
            f"background: {colour if isinstance(colour, str) else theme.rgba(colour)};"
            "border-radius: 4px;")
        layout.addWidget(self._dot)

        self._title_label = QtWidgets.QLabel(title, self)
        self._title_label.setFont(theme.font(9, bold=True))
        self._title_label.setStyleSheet("background: transparent;")
        layout.addWidget(self._title_label)

        if tag:
            self._tag_label = QtWidgets.QLabel(tag, self)
            self._tag_label.setFont(theme.font(7))
            self._tag_label.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
            layout.addWidget(self._tag_label)

        layout.addStretch(1)

        close_button = QtWidgets.QToolButton(self)
        close_button.setText("×")
        close_button.setFixedSize(16, 16)
        close_button.setStyleSheet("background: transparent; border: none;")
        close_button.clicked.connect(module.request_close)
        layout.addWidget(close_button)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._module._title_press(event.globalPosition().toPoint() - self._module.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._module._drag:
            self._module._title_move(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._module._drag:
            self._module._title_release()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _FlowLayout(QtWidgets.QLayout):
    """A minimal flex-wrap row layout for the knob body -- Qt has no
    built-in equivalent. Wraps left-to-right, top-to-bottom, same idea as
    the prototype's CSS `flex-wrap`."""

    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(QtCore.Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QtCore.QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        spacing = self.spacing()
        right = rect.right() - margins.right()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > right and line_height > 0:
                x = rect.x() + margins.left()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class Drawer(QtWidgets.QWidget):
    """The left module list: Synth core group, Effects group (only types
    `audio/effects.py` really implements), and a disabled CLAP row. Each
    enabled row is a real `QDrag` source; `Canvas.dropEvent()` is the other
    end.
    """

    #: Toggled by the collapse tab -- exposed so a host can react (e.g.
    #: resize a splitter) without polling `isVisible()`.
    toggled = QtCore.Signal(bool)

    WIDTH = 168

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(f"background: {theme.rgba(theme.CHROME_DEEP)};")

        self._content = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(self._content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        layout.addWidget(self._group_label("Synth core"))
        for type_key, title in SYNTH_CORE_MODULES:
            layout.addWidget(_DrawerRow(type_key, title))

        layout.addWidget(self._group_label("Effects"))
        for type_key, title in _effect_drawer_entries():
            layout.addWidget(_DrawerRow(type_key, title))

        clap_row = _DrawerRow(CLAP_TYPE_KEY, "CLAP Plugin…", enabled=False)
        layout.addWidget(clap_row)
        layout.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._toggle_button = QtWidgets.QToolButton(self)
        self._toggle_button.setText("‹")
        self._toggle_button.setFixedHeight(18)
        self._toggle_button.clicked.connect(self.toggle)
        outer.addWidget(self._toggle_button)
        outer.addWidget(self._content, 1)

    def _group_label(self, text):
        label = QtWidgets.QLabel(text, self)
        label.setFont(theme.font(7, bold=True))
        label.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
        return label

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded):
        self._expanded = expanded
        self._content.setVisible(expanded)
        self._toggle_button.setText("‹" if expanded else "›")
        self.setFixedWidth(self.WIDTH if expanded else 18)
        self.toggled.emit(expanded)

    @property
    def expanded(self):
        return self._expanded


class _DrawerRow(QtWidgets.QLabel):
    """One draggable (or disabled) drawer entry. Plain-text `QMimeData` mime
    carries the type key -- `Canvas` is the only thing that reads it, and
    it treats the string as opaque."""

    def __init__(self, type_key, title, enabled=True, parent=None):
        super().__init__(title, parent)
        self.type_key = type_key
        self._enabled = enabled
        self.setFont(theme.font(8))
        self.setFixedHeight(22)
        self.setContentsMargins(6, 0, 6, 0)
        if enabled:
            self.setStyleSheet(f"color: {theme.rgba(theme.TEXT)}; background: {theme.rgba(theme.PANEL)};")
        else:
            self.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)}; background: {theme.rgba(theme.PANEL)};")
            self.setEnabled(False)

    def mousePressEvent(self, event):
        if not self._enabled or event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setText(self.type_key)
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.CopyAction)


class Canvas(QtWidgets.QWidget):
    """The drop target and container for `ModuleWindow`s: a dotted Copper
    grid background, drag-and-drop from `Drawer`, and `tidy()`.

    Knows nothing about what a type key means -- `module_factory(type_key,
    pos) -> ModuleWindow | None` is where that vocabulary lives (stage 3).
    Returning `None` (e.g. "this module type is already open, or is the
    disabled CLAP row") is a valid, silent no-op.
    """

    def __init__(self, module_factory, parent=None):
        super().__init__(parent)
        self._module_factory = module_factory
        self.setAcceptDrops(True)
        self.setStyleSheet(f"background: {theme.rgba(theme.CANVAS)};")
        self._windows = []

    # -- window bookkeeping ---------------------------------------------

    def add_window(self, window):
        window.setParent(self)
        window.show()
        window.closed.connect(lambda _key, w=window: self._on_window_closed(w))
        self._windows.append(window)
        return window

    def _on_window_closed(self, window):
        if window in self._windows:
            self._windows.remove(window)

    def windows(self):
        return list(self._windows)

    def window_count(self):
        return len(self._windows)

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        type_key = event.mimeData().text()
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self.spawn_module(type_key, pos)
        event.acceptProposedAction()

    def spawn_module(self, type_key, pos):
        """The actual drop-handling logic, factored out of `dropEvent()` so
        tests can call it directly -- synthesizing real Qt DnD is
        impractical (see the plan's testing note)."""
        clamped = QtCore.QPoint(max(0, min(pos.x(), max(0, self.width() - 1))),
                                max(0, min(pos.y(), max(0, self.height() - 1))))
        window = self._module_factory(type_key, clamped)
        if window is None:
            return None
        self.add_window(window)
        window.move(clamped)
        return window

    # -- background grid ----------------------------------------------------

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CANVAS)
        p.setPen(QtGui.QPen(theme.RULE, 1))
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        spacing = CANVAS_GRID_SPACING
        y = spacing / 2
        while y < self.height():
            x = spacing / 2
            while x < self.width():
                p.drawPoint(QtCore.QPointF(x, y))
                x += spacing
            y += spacing

    # -- Tidy: ported 1:1 from the prototype's JS tidy() --------------------

    def tidy(self):
        windows = sorted(self._windows, key=lambda w: (w.y(), w.x()))
        count = len(windows)
        if count == 0:
            return
        width = self.width()
        max_cols = max(1, math.floor((width - TIDY_MARGIN * 2 + TIDY_GAP)
                                     / (TIDY_SLOT_W + TIDY_GAP)))
        cols = min(max_cols, count)
        rows = math.ceil(count / cols)
        grid_w = cols * TIDY_SLOT_W + (cols - 1) * TIDY_GAP
        grid_h = rows * TIDY_SLOT_H + (rows - 1) * TIDY_GAP
        offset_x = max(TIDY_MARGIN, (width - grid_w) / 2)
        offset_y = max(TIDY_MARGIN, (self.height() - grid_h) / 2)

        for index, window in enumerate(windows):
            row, col = divmod(index, cols)
            x = offset_x + col * (TIDY_SLOT_W + TIDY_GAP)
            y = offset_y + row * (TIDY_SLOT_H + TIDY_GAP)
            window.move(round(x), round(y))

    @staticmethod
    def tidy_grid(width, height, count):
        """Pure grid math, exposed for tests that want positions without
        constructing real `ModuleWindow`s -- same formula `tidy()` uses."""
        if count == 0:
            return []
        max_cols = max(1, math.floor((width - TIDY_MARGIN * 2 + TIDY_GAP)
                                     / (TIDY_SLOT_W + TIDY_GAP)))
        cols = min(max_cols, count)
        rows = math.ceil(count / cols)
        grid_w = cols * TIDY_SLOT_W + (cols - 1) * TIDY_GAP
        grid_h = rows * TIDY_SLOT_H + (rows - 1) * TIDY_GAP
        offset_x = max(TIDY_MARGIN, (width - grid_w) / 2)
        offset_y = max(TIDY_MARGIN, (height - grid_h) / 2)
        positions = []
        for index in range(count):
            row, col = divmod(index, cols)
            x = offset_x + col * (TIDY_SLOT_W + TIDY_GAP)
            y = offset_y + row * (TIDY_SLOT_H + TIDY_GAP)
            positions.append((round(x), round(y)))
        return positions
