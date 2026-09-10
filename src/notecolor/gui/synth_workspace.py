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
        #: Set while a left-button drag is in progress; `None` otherwise.
        #: Follows the same "populated on press, cleared on release"
        #: `_drag` idiom `ModuleWindow` uses for its own window-drag (see
        #: this module's docstring), just keyed on a y-position instead of
        #: a grab point since a knob only cares about vertical motion.
        self._drag_start_y = None
        #: Accumulates raw wheel `angleDelta().y()` units between emitted
        #: steps -- see `wheelEvent()`. A hi-res trackpad delivers dozens of
        #: small-delta wheel events per "flick" instead of one, and the old
        #: code emitted a full step on every single one of them, so a light
        #: trackpad scroll could fly through a knob's whole range in one
        #: gesture (issue #161).
        self._wheel_accum = 0
        self.setFixedSize(KNOB_SIZE, KNOB_SIZE + 24)
        self.setToolTip(f"{label} — wheel or drag to sweep, shift = coarse")
        self.setCursor(QtCore.Qt.SizeVerCursor)

    def set_display(self, value_text, rotation_degrees):
        self._value_text = value_text
        self._rotation = rotation_degrees
        self.update()

    def set_label(self, label):
        self._label = label
        self.setToolTip(f"{label} — wheel or drag to sweep, shift = coarse")
        self.update()

    #: Standard wheel "detent" size (`QWheelEvent.angleDelta()` units per
    #: physical click on a notched mouse wheel, per Qt convention). A
    #: hi-res/trackpad wheel reports many small deltas per gesture instead
    #: of one 120-unit tick, so accumulating to this threshold before
    #: emitting a step keeps one detent's worth of physical scrolling equal
    #: to one step everywhere, rather than one step per tiny sub-delta
    #: (issue #161: a light trackpad flick used to blow through the whole
    #: range).
    WHEEL_UNITS_PER_STEP = 120

    def wheelEvent(self, event):
        self.last_shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        self._wheel_accum += event.angleDelta().y()
        step = self.WHEEL_UNITS_PER_STEP
        while abs(self._wheel_accum) >= step:
            direction = 1 if self._wheel_accum > 0 else -1
            self.wheelStepped.emit(direction)
            self._wheel_accum -= direction * step
        event.accept()

    #: Pixels of vertical drag per one `wheelStepped` step. Was 4px --
    #: hair-trigger enough that a couple of pixels of mouse jitter swept a
    #: knob through several steps. Raised substantially (issue #161: user
    #: wants deliberate, controlled drag distance per step, not raw speed).
    DRAG_PIXELS_PER_STEP = 28

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_y = event.position().y()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_y is None:
            super().mouseMoveEvent(event)
            return
        # Dragging UP increases the value -- matches every DAW/synth
        # convention, and this widget's own wheelEvent (angleDelta().y() >
        # 0, i.e. wheel-up, already emits +1).
        current_y = event.position().y()
        delta = self._drag_start_y - current_y
        step = self.DRAG_PIXELS_PER_STEP
        while abs(delta) >= step:
            direction = 1 if delta > 0 else -1
            self.last_shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
            self.wheelStepped.emit(direction)
            delta -= direction * step
        # Reset the baseline so the next move's delta picks up exactly
        # where this one left off (less than one full step), so a long
        # drag emits many discrete steps rather than one giant jump.
        self._drag_start_y = current_y + delta
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._drag_start_y is not None:
            self._drag_start_y = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        p.setPen(QtGui.QPen(theme.ink(theme.AMBER), 2))
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
    #: Fires whenever this window should become the focused one -- on a
    #: title-bar press or a click anywhere in the window's body. `Canvas`
    #: is the one that actually arbitrates focus (only it knows about
    #: sibling windows), so this just requests it.
    focusRequested = QtCore.Signal(object)

    TITLE_H = 26

    def __init__(self, type_key, title, tag="", dot_color=None, knob_specs=(), parent=None):
        super().__init__(parent)
        self.type_key = type_key
        self._drag = None
        self._focused = False
        self.setFixedWidth(TIDY_SLOT_W - TIDY_GAP)
        # Scoped to this widget alone (via the object-name selector in
        # `_apply_border_style()`) so the focus border can't leak onto
        # descendants (title/tag labels, the close button, Knobs) that
        # don't explicitly set their own `border` -- an unscoped/bare
        # stylesheet rule on an ancestor is Qt's effective style root for
        # any descendant that doesn't fully override it, which used to
        # paint a "weird box" around the title-bar text too.
        self.setObjectName("moduleWindow")
        self.setAutoFillBackground(False)
        # Required for a plain QWidget subclass to actually paint the
        # border/background set via setStyleSheet() below.
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._apply_border_style()

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
        self._sync_content_height()

    def knobs(self):
        return list(self._knobs)

    def _sync_content_height(self):
        """Pin `_body`'s height to exactly what `_FlowLayout` needs at this
        window's real (fixed) width, then resize the window to title bar +
        that height (issue #163).

        `adjustSize()`'s automatic height-for-width negotiation doesn't
        work here: threading a height-for-width layout (`_FlowLayout`)
        through a plain `QWidget` (`_body`) and then a `QVBoxLayout`
        computes the *unconstrained* sizeHint's width first (the narrowest
        the flow could wrap to, which packs far fewer knobs per row) and
        reserves height for wrapping at *that* width, then only lays the
        knobs out for real at the window's actual (wider) fixed width --
        so the window ends up sized for a narrow-and-tall wrap it never
        actually uses, leaving dead space below the last real knob row.
        Asking the flow layout directly for `heightForWidth()` at the
        width the window will really have sidesteps that negotiation
        entirely.
        """
        width = self.width()
        body_height = self._flow.heightForWidth(width)
        self._body.setFixedHeight(body_height)
        self.setFixedHeight(self.TITLE_H + body_height)

    # -- focus state ----------------------------------------------------

    def _apply_border_style(self):
        border_colour = theme.COPPER if self._focused else theme.RULE_2
        self.setStyleSheet(
            f"#moduleWindow {{ background: {theme.rgba(theme.CHROME)}; "
            f"border: 1px solid {theme.rgba(theme.ink(border_colour))}; }}"
        )

    def set_focused(self, focused):
        """Called by `Canvas` to make this the (un)focused window -- only
        the border colour changes, matching the prototype's `.win.focused`
        rule; the background stays whatever `.win` already uses."""
        self._focused = focused
        self._apply_border_style()

    def mousePressEvent(self, event):
        # Covers clicks on the knob body area -- title-bar presses already
        # request focus via `_title_press()`, called from
        # `_TitleBar.mousePressEvent()`.
        self.focusRequested.emit(self)
        super().mousePressEvent(event)

    # -- drag-to-move, following piano_roll_panel.py's `_drag` idiom --------

    def _title_press(self, pos):
        self.focusRequested.emit(self)
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

        self._title_label = QtWidgets.QLabel(self)
        self._title_label.setFont(theme.font(9, bold=True))
        # `border: none` is explicit, not decorative -- without it this
        # label has no border of its own, so Qt's stylesheet cascade falls
        # through to ModuleWindow's ancestor rule (see
        # `ModuleWindow._apply_border_style()`), which paints its focus
        # border onto every descendant that doesn't opt out (bug: "weird
        # box" around title-bar text). Same for `_tag_label` below.
        self._title_label.setStyleSheet("background: transparent; border: none;")
        self._title_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        layout.addWidget(self._title_label)

        self._tag_label = None
        if tag:
            self._tag_label = QtWidgets.QLabel(self)
            self._tag_label.setFont(theme.font(7))
            self._tag_label.setStyleSheet(
                f"background: transparent; border: none; color: {theme.rgba(theme.TEXT_FAINT)};")
            self._tag_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            layout.addWidget(self._tag_label)

        self._title_text = title
        self._tag_text = tag

        layout.addStretch(1)

        close_button = QtWidgets.QToolButton(self)
        close_button.setText("×")
        close_button.setFixedSize(16, 16)
        close_button.setStyleSheet("background: transparent; border: none;")
        close_button.clicked.connect(module.request_close)
        layout.addWidget(close_button)

        self._set_elided_texts()

    def _set_elided_texts(self):
        """Squeeze `_title_label`/`_tag_label` to fit the title bar's fixed
        (narrow -- `ModuleWindow.setFixedWidth`) width by eliding with
        `...` rather than letting Qt clip the paint at the label's edge.
        Title always keeps as much space as it needs first; the tag gives
        way (shrinks, then disappears) when there isn't room left for it.

        Uses `ModuleWindow`'s own fixed width directly rather than this
        title bar's live `self.width()`: the outer `QVBoxLayout` stretches
        the title bar to exactly that width with zero margins, but
        `self.width()` can read a stale or mid-layout-pass value here
        (this runs from `__init__`, before the widget has ever been shown
        or laid out for real) -- the module's width is a constant set
        before this title bar is even constructed, so it's the reliable
        source.
        """
        width = self._module.width()

        # Budget: title bar width minus the dot, close button, stretch's
        # minimum, and layout margins/spacing -- everything but the two
        # text labels. The layout is [dot, title, (tag,) stretch, close]
        # with `spacing` between every consecutive pair -- 3 gaps normally,
        # or 4 when the tag label is also present. This used to hardcode 3
        # gaps unconditionally, so with a tag row it handed `elidedText()`
        # one `spacing`'s worth (6px) more room than the real layout had
        # left over -- the elided string it produced legitimately fit the
        # (wrong, too-generous) budget, but the title bar then had to
        # squeeze it into a rect 6px narrower than that, clipping it
        # mid-glyph instead of at the "..." (issue #164).
        margins = self.layout().contentsMargins()
        spacing = self.layout().spacing()
        gaps = 4 if self._tag_label is not None else 3
        reserved = 8 + 16 + spacing * gaps + margins.left() + margins.right()
        available = max(0, width - reserved)

        title_metrics = QtGui.QFontMetrics(self._title_label.font())
        title_full_width = title_metrics.horizontalAdvance(self._title_text)
        # `elidedText()` is given the full `available` budget, not the
        # narrower `title_width` below -- passing a width that exactly
        # equals the text's own measured width can still trigger a
        # spurious one-character elision (font-metrics rounding), so a
        # title that actually fits must be handed real slack to fit in.
        elided_title = title_metrics.elidedText(
            self._title_text, QtCore.Qt.ElideRight, available)
        title_width = min(title_full_width, available)
        self._title_label.setText(elided_title)
        self._title_label.setFixedWidth(max(title_width, 1))

        if self._tag_label is None:
            return
        tag_available = max(0, available - title_width)
        if tag_available < 12:
            # No usable room left -- hide rather than paint a
            # single-character sliver.
            self._tag_label.setVisible(False)
            return
        self._tag_label.setVisible(True)
        tag_metrics = QtGui.QFontMetrics(self._tag_label.font())
        self._tag_label.setText(tag_metrics.elidedText(
            self._tag_text, QtCore.Qt.ElideRight, tag_available))

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

    WIDTH = 178
    #: Width of the collapsed strip -- just the toggle tab, no content.
    COLLAPSED_WIDTH = 20

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

        layout.addWidget(self._group_label("Instruments"))
        clap_row = _DrawerRow(CLAP_TYPE_KEY, "CLAP Plugin…", enabled=False)
        layout.addWidget(clap_row)
        layout.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._toggle_button = QtWidgets.QToolButton(self)
        self._toggle_button.setText("☰")
        # No fixed width: a `QVBoxLayout` item without one stretches to
        # fill the full cross-axis (here, horizontal) width the layout has
        # -- which used to be exactly what a fixed width of 20 prevented.
        # With the drawer expanded to its full 178px, that left a bare
        # 158px-wide strip of the drawer's own raw background to the
        # button's right (issue #160's "black band to the right of the
        # icon, the width of the opened tab"); letting it stretch makes
        # the button itself span the drawer's whole current width in both
        # states, so there's no gap of raw background beside it to read as
        # a stray band.
        self._toggle_button.setMinimumHeight(28)
        # `QToolButton`'s own default size policy is Fixed/Fixed, which is
        # exactly what made a `QVBoxLayout` leave it at its bare sizeHint
        # in both directions instead of stretching it to fill the layout's
        # cross-axis width (or, once expanded/collapsed toggles the
        # remaining stretch below, the leftover height) -- Expanding in
        # both directions is what actually makes it track the drawer.
        self._toggle_button.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._toggle_button.setStyleSheet(
            f"QToolButton {{ background: {theme.rgba(theme.ink(theme.INK_2))};"
            f" border: 1px solid {theme.rgba(theme.RULE)}; color: {theme.rgba(theme.TEXT_FAINT)}; }}"
            f" QToolButton:hover {{ color: {theme.rgba(theme.ink(theme.COPPER))};"
            f" border-color: {theme.rgba(theme.ink(theme.COPPER))}; }}"
        )
        self._toggle_button.clicked.connect(self.toggle)
        self._outer_layout = outer
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
        self.setFixedWidth(self.WIDTH if expanded else self.COLLAPSED_WIDTH)
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
        # QLabel defaults to top-left alignment, not vertical centering --
        # combined with the fixed height below, that left descenders
        # ("y", "p") sitting right on (or past) the bottom edge. Centering
        # vertically gives the font's ascent/descent equal headroom instead
        # of assuming top alignment has room to spare.
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        # Height derived from the label's own font metrics plus fixed
        # vertical padding, rather than a hardcoded "22" -- issue #168: on
        # the system where this actually rendered, the font's real
        # (ascent + descent + internal leading) height was taller than 22,
        # so AlignVCenter had no slack to center into and the label's paint
        # rect clipped the glyphs' tops and bottoms. Deriving the height
        # from `QFontMetrics` keeps this correct for whatever font a given
        # system substitutes, instead of only whatever font this was last
        # measured against.
        metrics = QtGui.QFontMetrics(self.font())
        self.setFixedHeight(metrics.height() + 10)
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
        # Without an explicit pixmap, `QDrag` drags nothing visible -- the
        # canvas's own drop-target highlight worked (that's `Canvas`
        # reacting to `dragEnterEvent`/`dragMoveEvent`, unrelated to this),
        # but the row itself vanished the moment the drag started, leaving
        # no feedback for where it actually is. Grabbing the row's own
        # rendered pixels and anchoring the pixmap at the same point the
        # mouse grabbed it (`setHotSpot`) makes the drag image track the
        # cursor exactly the way the row sat under it before the drag
        # (issue #167).
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
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
        self._drop_hover = False

    # -- window bookkeeping ---------------------------------------------

    def add_window(self, window):
        window.setParent(self)
        window.show()
        window.closed.connect(lambda _key, w=window: self._on_window_closed(w))
        window.focusRequested.connect(self._focus_window)
        self._windows.append(window)
        return window

    def _on_window_closed(self, window):
        if window in self._windows:
            self._windows.remove(window)

    def _focus_window(self, window):
        """Exactly one window is ever focused at a time -- the newly
        clicked one, raised above its (overlapping) siblings."""
        for w in self._windows:
            w.set_focused(w is window)
        window.raise_()

    def windows(self):
        return list(self._windows)

    def window_count(self):
        return len(self._windows)

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self._drop_hover = True
            self.update()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self._drop_hover = True
            self.update()

    def dragLeaveEvent(self, event):
        self._drop_hover = False
        self.update()

    def dropEvent(self, event):
        self._drop_hover = False
        self.update()
        type_key = event.mimeData().text()
        # Derived from the event's global position rather than trusted
        # from `event.position()`/`event.pos()` directly: those are
        # supposed to already be canvas-local, but this is the one point
        # in the drop path any stale/mismapped coordinate would silently
        # survive all the way to `spawn_module()`. `mapFromGlobal()` is
        # unambiguous -- wherever the cursor actually is on screen, mapped
        # into this widget's own frame -- so if a mismapped local position
        # was ever the reason a dropped module used to land somewhere
        # other than the cursor (issue #167), this removes that path
        # entirely rather than trusting the event to have gotten it right.
        pos = self.mapFromGlobal(event.globalPosition().toPoint())
        self.spawn_module(type_key, pos)
        event.acceptProposedAction()

    def spawn_module(self, type_key, pos):
        """The actual drop-handling logic, factored out of `dropEvent()` so
        tests can call it directly -- synthesizing real Qt DnD is
        impractical (see the plan's testing note)."""
        window = self._module_factory(type_key, pos)
        if window is None:
            return None
        # Clamped against the window's own size (not just a 1px inset off
        # the canvas edge) so a module dropped near the canvas's right or
        # bottom edge stays fully on-screen instead of hanging mostly off
        # it -- part of issue #167's "drops in the wrong place" (a window
        # that's 90% off-canvas reads as "wrong place" even when its
        # top-left is technically at the cursor).
        max_x = max(0, self.width() - window.width())
        max_y = max(0, self.height() - window.height())
        clamped = QtCore.QPoint(max(0, min(pos.x(), max_x)),
                                max(0, min(pos.y(), max_y)))
        self.add_window(window)
        window.move(clamped)
        self._focus_window(window)
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

        if self._drop_hover:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            inset_rect = self.rect().adjusted(8, 8, -8, -8)
            p.fillRect(inset_rect, theme.ink(theme.COPPER, alpha=15))
            p.setPen(QtGui.QPen(theme.ink(theme.COPPER), 1, QtCore.Qt.DashLine))
            p.drawRect(inset_rect)
            p.setPen(QtGui.QPen(theme.ink(theme.COPPER), 1))
            p.setFont(theme.font(11))
            p.drawText(inset_rect, QtCore.Qt.AlignCenter, "Drop to add module")

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
