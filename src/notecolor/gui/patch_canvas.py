"""The patch canvas's Qt half (ticket #211, decision 57): jacks on the
module windows, cables with weight drawn between them, the Mix stripe, and
the connect gesture wired to `patch_graph`'s accept/refuse contract.

This **extends** `synth_workspace.Canvas` rather than replacing it with a
second canvas (decision 56 §9): today's module windows become the graph's
nodes. `PatchLayer.attach()` is the whole seam -- a `Canvas` with no layer
attached behaves exactly as it did before this ticket.

Three Qt facts shape the design, and each one is load-bearing:

* **Sockets are children of the canvas, not of the module window.** A jack
  sits half outside its module's edge (the prototype's `left:-6px`), and a
  Qt child widget is clipped to its parent's rect -- so a socket parented
  to the module would simply lose its outer half. Parenting to the canvas
  costs a reposition whenever a module moves, which is what
  `Canvas.layoutChanged` exists to tell us.
* **Cables are painted by one transparent overlay above every window.**
  Decision 57 §3 requires cables to stay drawn on top of the modules,
  including while routing. Qt has no way to paint one widget's content
  over a sibling's, so a single full-canvas overlay does all the cable
  painting -- and, for the same clipping reason, the dashed modulation
  ring around a knob, which does not fit inside the knob's own rect.
  It carries `WA_TransparentForMouseEvents`, so it never eats a click.
* **Cable hover is found with an event filter, not by the overlay.** The
  overlay cannot receive mouse events (see above) and the canvas stops
  getting them wherever a module window covers it -- but a cable crosses
  over modules constantly. So the layer filters mouse moves on the canvas
  and on every module window and child, and does its own hit-test against
  the drawn curves.

Knob painting is untouched, deliberately: decision 57 settles the look as
"the existing canvas grown sockets and cables", with `Knob.paintEvent`
unchanged and no restyle.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from notecolor.gui import theme
from notecolor.gui import patch_graph as pg
from notecolor.gui.patch_graph import Target

#: The hole itself, and the box the widget occupies -- larger, so a
#: refusal's glow and a hover's ring have room to paint without being
#: clipped by the widget's own edge.
SOCKET_HOLE = 10
SOCKET_BOX = 18

#: Where the first jack sits below a module's top edge, and the gap to the
#: next one down (the prototype's `top: 34 + i * 17`).
SOCKET_FIRST_Y = 34
SOCKET_PITCH = 17

#: How far a jack hangs outside its module's edge.
SOCKET_OVERHANG = 6

#: How close the cursor must come to a drawn cable to hover (and so to
#: unplug) it. Matches the prototype's 11px-wide invisible hit stroke.
CABLE_HIT_TOLERANCE = 11.0

#: Frame interval for the cable physics. The timer runs only while
#: something is actually moving -- see `PatchLayer._tick()`.
FRAME_MS = 16

#: Frames of animation to keep running after the last thing that could
#: have disturbed a cable. Covers the settle-out after a module is
#: dropped.
WAKE_FRAMES = 40

#: How long a refusal's explanation stays on screen.
REFUSAL_MS = 6000

#: How long "patched · X → Y" holds the status line before the standing
#: cable summary takes it back.
MESSAGE_MS = 4000

#: How long a modulation cable stays lit after a wheel-turn of its knob --
#: a wheel step has no "let go" to end the highlight the way a drag does.
WHEEL_LIT_MS = 700

#: The Mix stripe's fixed width, and where across the canvas it sits.
#: Decision 57 §1: the stripe costs ~150px of canvas permanently, which is
#: exactly why it was a design call and not a setting.
STRIPE_WIDTH = 150
STRIPE_POSITION = 0.56


def _qcolour(hex_colour, alpha=255):
    colour = QtGui.QColor(hex_colour)
    colour.setAlpha(alpha)
    return colour


class SocketWidget(QtWidgets.QWidget):
    """One jack. A square carries sound, a circle carries modulation
    (#208's port type made visible); the one always-present spare is drawn
    as a dashed empty hole so it reads as available rather than as an
    unused jack (decision 57 §4).

    Positioned by `PatchLayer`, which is also the only thing that reads its
    identity fields.
    """

    pressed = QtCore.Signal(object)
    hovered = QtCore.Signal(object, bool)
    #: The press that starts a cable drag gives this widget Qt's implicit
    #: mouse grab, so every subsequent move and the release land here and
    #: nowhere else -- these two carry them back to the layer in global
    #: coordinates, which is all it needs to find what is under the cursor.
    dragged = QtCore.Signal(QtCore.QPoint)
    released = QtCore.Signal(QtCore.QPoint)

    def __init__(self, node_id, io, slot, kind, parent=None):
        super().__init__(parent)
        self.node_id = node_id
        self.io = io
        self.slot = slot
        self.kind = kind
        self.spare = False
        self.pip_colour = None
        #: One of: "", "armed" (this is the jack a cable is being pulled
        #: out of), "ok"/"bad" (a drag is hovering it), "flash" (a refusal).
        self.state = ""
        self.setFixedSize(SOCKET_BOX, SOCKET_BOX)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setAttribute(QtCore.Qt.WA_Hover, True)

    def centre_in(self, widget):
        """This jack's centre in `widget`'s coordinates -- where a cable
        actually ends."""
        return self.mapTo(widget, QtCore.QPoint(SOCKET_BOX // 2, SOCKET_BOX // 2))

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.update()

    def set_pip(self, colour):
        if colour != self.pip_colour:
            self.pip_colour = colour
            self.update()

    def set_spare(self, spare):
        if spare != self.spare:
            self.spare = spare
            self.update()

    def enterEvent(self, event):
        self.hovered.emit(self, True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(self, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.pressed.emit(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.dragged.emit(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.released.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        inset = (SOCKET_BOX - SOCKET_HOLE) / 2
        hole = QtCore.QRectF(inset, inset, SOCKET_HOLE, SOCKET_HOLE)
        round_hole = self.kind == pg.KIND_MOD

        border = theme.ink(theme.RULE_2)
        fill = theme.ink(theme.INK_0)
        style = QtCore.Qt.SolidLine
        glow = None
        if self.spare and not self.state:
            border = theme.ink(theme.RULE, alpha=140)
            style = QtCore.Qt.DashLine
        if self.state == "armed":
            border, glow = theme.ink(theme.COPPER), _qcolour(theme.COPPER, 64)
        elif self.state == "ok":
            border, glow = theme.ink(theme.TEXT), _qcolour(theme.LINEN, 51)
        elif self.state in ("bad", "flash"):
            border = theme.ink(theme.CLAY_RED)
            fill = _qcolour(theme.CLAY_RED, 90)
            glow = _qcolour(theme.CLAY_RED, 71)

        if glow is not None:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(glow)
            ring = hole.adjusted(-3, -3, 3, 3)
            p.drawEllipse(ring) if round_hole else p.drawRect(ring)

        p.setPen(QtGui.QPen(border, 1, style))
        p.setBrush(fill)
        p.drawEllipse(hole) if round_hole else p.drawRect(hole)

        if self.pip_colour is not None:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(self.pip_colour))
            pip = hole.adjusted(2, 2, -2, -2)
            p.drawEllipse(pip) if round_hole else p.drawRect(pip)


class MixStripe(QtWidgets.QWidget):
    """The Mix node as a labelled stripe across the canvas (decision 57
    §1). Everything left of it runs once per held note; everything right of
    it runs once.

    A node like any other -- it has jacks on both edges and its cables obey
    the same rules -- but it is not a module window: it cannot be moved,
    closed, or focused, because it is a place on the canvas rather than a
    thing sitting on it.
    """

    NODE_ID = "mix"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        rect = self.rect()
        p.fillRect(rect, theme.ink(theme.INK_0, alpha=theme.CANVAS_ALPHA))

        p.setPen(QtGui.QPen(theme.ink(theme.RULE_2), 1))
        p.drawLine(rect.topLeft(), rect.bottomLeft())
        p.drawLine(rect.topRight(), rect.bottomRight())
        p.setPen(QtGui.QPen(theme.ink(theme.RULE), 1))
        p.drawLine(rect.left() + 14, rect.top(), rect.left() + 14, rect.bottom())
        p.drawLine(rect.right() - 14, rect.top(), rect.right() - 14, rect.bottom())

        # The node's own plate, centred: this is the thing the cables plug
        # into, and the Σ says what it does.
        plate = QtCore.QRect(rect.left() + 16, rect.center().y() - 52,
                             rect.width() - 32, 104)
        p.fillRect(plate, theme.ink(theme.INK_2, alpha=theme.CHROME_ALPHA))
        p.setPen(QtGui.QPen(theme.ink(theme.RULE_2), 1))
        p.drawRect(plate)
        p.setFont(theme.font(9, bold=True))
        p.setPen(theme.ink(theme.LINEN))
        p.drawText(QtCore.QRect(plate.left(), plate.top() + 8, plate.width(), 16),
                   QtCore.Qt.AlignHCenter, "MIX")
        p.setFont(theme.font(16))
        p.setPen(theme.ink(theme.LINEN_DIM))
        p.drawText(plate, QtCore.Qt.AlignCenter, "Σ")
        p.setFont(theme.font(7))
        p.setPen(theme.ink(theme.EMBER))
        p.drawText(QtCore.QRect(plate.left(), plate.bottom() - 18, plate.width(), 14),
                   QtCore.Qt.AlignHCenter, "16 voices → 1")


class CableOverlay(QtWidgets.QWidget):
    """Paints everything that has to sit above the module windows: the
    cables, the dashed ring on every modulated knob, the ghost cable while
    routing, the loop badge, the hover label, and a refusal's callout.

    Owns no state -- `PatchLayer` hands it what to draw. Transparent for
    mouse events throughout, so it is invisible to every gesture.
    """

    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self._layer = layer
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._layer.paint_rings(p)
        self._layer.paint_cables(p)
        self._layer.paint_drag(p)
        self._layer.paint_annotations(p)


class PatchLayer(QtCore.QObject):
    """Owns the patch's cables and everything that draws or edits them.

    One per `Canvas`. `attach()` takes over the canvas's spare surface
    (an overlay, a stripe, a crowd of small socket widgets) and connects
    to the canvas's own layout signals; nothing in `synth_workspace` needs
    to know what a cable is.

    The host supplies node identity -- which module is per-note, which
    sends modulation, which is a Delay -- through `add_node()`, because
    that vocabulary belongs to `synth_view.py`, the same way
    `module_factory` already does for windows.
    """

    #: A sentence for the status bar, and whether it is a refusal.
    statusChanged = QtCore.Signal(str, bool)
    #: Any change to the cable set -- the host persists these per patch.
    cablesChanged = QtCore.Signal()

    def __init__(self, appearance=None, parent=None):
        super().__init__(parent)
        self.graph = pg.PatchGraph()
        self.appearance = appearance or pg.CableAppearance()
        self.canvas = None
        self.overlay = None
        self.stripe = None

        self._sockets = {}        # (node_id, io, slot) -> SocketWidget
        self._hosts = {}          # node_id -> widget whose rect carries the jacks
        self._knobs = {}          # (node_id, label) -> Knob
        self._filtered = set()    # widgets we have installed a mouse filter on

        self._hover_cable = None
        self._hover_socket = None
        self._focused_node = None
        self._turning = None      # (node_id, knob label) while a knob is moving
        self._drag = None
        self._refusal = None      # {"text", "point"}

        self._awake = WAKE_FRAMES
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._refusal_timer = QtCore.QTimer(self)
        self._refusal_timer.setSingleShot(True)
        self._refusal_timer.timeout.connect(self.clear_refusal)
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.timeout.connect(self._flash_step)
        self._flash = None
        self._wheel_timer = QtCore.QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.timeout.connect(self._end_wheel_turn)
        #: "patched · X → Y" is news; it should not still be the status
        #: line ten cables later. Every transient message falls back to the
        #: standing summary after this long.
        self._message_timer = QtCore.QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.announce_summary)

    # -- attachment ----------------------------------------------------

    def attach(self, canvas):
        self.canvas = canvas
        self.stripe = MixStripe(canvas)
        self.overlay = CableOverlay(self, canvas)
        canvas.setMouseTracking(True)
        canvas.installEventFilter(self)
        self._filtered.add(canvas)
        canvas.layoutChanged.connect(self.relayout)
        canvas.windowRemoved.connect(self._on_window_removed)
        canvas.background_painter = self.paint_regions

        self.graph.add_node(pg.NodeSpec(
            MixStripe.NODE_ID, "MIX", side=pg.SIDE_BOUNDARY, is_mix=True))
        self._hosts[MixStripe.NODE_ID] = self.stripe
        self.relayout()
        return self

    # -- nodes ---------------------------------------------------------

    def add_node(self, spec, window):
        """Registers an open module window as a graph node. `window` is a
        `ModuleWindow`; its rect is where the jacks go."""
        self.graph.add_node(spec)
        self._hosts[spec.node_id] = window
        self._watch(window)
        window.moved.connect(lambda _key: self.relayout())
        self.relayout()

    def register_knobs(self, node_id, pairs):
        """`pairs`: `(label, knob_widget)`. A knob is a modulation *target*
        -- the only kind there is (decision 56 §5)."""
        for label, knob in pairs:
            self._knobs[(node_id, label)] = knob
            turning = getattr(knob, "turning", None)
            if turning is not None:
                turning.connect(lambda on, n=node_id, l=label: self._on_knob_turning(n, l, on))
            knob.wheelStepped.connect(lambda _d, n=node_id, l=label: self._on_knob_wheel(n, l))

    def _on_window_removed(self, window):
        node_id = getattr(window, "type_key", None)
        if node_id is None:
            return
        self.graph.remove_node(node_id)
        self._hosts.pop(node_id, None)
        for key in [k for k in self._knobs if k[0] == node_id]:
            self._knobs.pop(key)
        if self._focused_node == node_id:
            self._focused_node = None
        self.relayout()
        self.cablesChanged.emit()

    def set_focused_node(self, node_id):
        """A module's cables light up while it is the focused window
        (decision 57 §3)."""
        self._focused_node = node_id
        self.wake()

    # -- layout --------------------------------------------------------

    def relayout(self):
        """Rebuilds the socket row on every node -- as many jacks as there
        are cables, plus one spare -- and repositions everything. Cheap
        enough to run on any layout change: a patch has tens of jacks, not
        thousands."""
        if self.canvas is None:
            return
        self._place_stripe()

        wanted = {}
        for spec in self.graph.nodes():
            if spec.node_id not in self._hosts:
                continue
            in_count, out_count = self.graph.socket_counts(spec.node_id)
            for slot in range(in_count):
                wanted[(spec.node_id, "in", slot)] = (pg.KIND_AUDIO, slot == in_count - 1)
            for slot in range(out_count):
                kind = spec.out_kind
                wanted[(spec.node_id, "out", slot)] = (kind, slot == out_count - 1)

        for key in [k for k in self._sockets if k not in wanted]:
            socket = self._sockets.pop(key)
            socket.setParent(None)
            socket.deleteLater()

        for key, (kind, spare) in wanted.items():
            socket = self._sockets.get(key)
            if socket is None or socket.kind != kind:
                if socket is not None:
                    socket.setParent(None)
                    socket.deleteLater()
                node_id, io, slot = key
                socket = SocketWidget(node_id, io, slot, kind, self.canvas)
                socket.pressed.connect(self._on_socket_pressed)
                socket.hovered.connect(self._on_socket_hovered)
                socket.dragged.connect(self.drag_move)
                socket.released.connect(self.drag_release)
                socket.show()
                self._sockets[key] = socket
            socket.set_spare(spare)
            socket.setToolTip(self._socket_tooltip(key, spare))
            self._move_socket(key, socket)

        self._raise_layer()
        self.wake()

    def _socket_tooltip(self, key, spare):
        node_id, io, slot = key
        kind = self._sockets[key].kind
        word = "In" if io == "in" else ("Mod" if kind == pg.KIND_MOD else "Out")
        return f"{self.graph.title(node_id)} · {word} {slot + 1}" + (" · free" if spare else "")

    def _host_rect(self, node_id):
        """A node's rect in canvas coordinates. Deliberately not gated on
        `isVisible()`: a window that has been added but whose top-level has
        never been shown (every headless test, and the moment before the
        view's first `showEvent`) still has a real geometry, and sockets
        that hid themselves there would never come back."""
        host = self._hosts.get(node_id)
        if host is None or host.parentWidget() is None:
            return None
        top_left = host.mapTo(self.canvas, QtCore.QPoint(0, 0))
        return QtCore.QRect(top_left, host.size())

    def _move_socket(self, key, socket):
        node_id, io, slot = key
        rect = self._host_rect(node_id)
        if rect is None:
            socket.hide()
            return
        socket.show()
        if node_id == MixStripe.NODE_ID:
            # The stripe spans the whole canvas height, so its jacks are
            # centred on its plate rather than hung from its top edge.
            count = self.graph.socket_counts(node_id)[0 if io == "in" else 1]
            first_y = rect.center().y() - (count - 1) * SOCKET_PITCH / 2
            y = first_y + slot * SOCKET_PITCH
        else:
            y = rect.top() + SOCKET_FIRST_Y + slot * SOCKET_PITCH
        if io == "in":
            x = rect.left() - SOCKET_OVERHANG
        else:
            x = rect.left() + rect.width() - (SOCKET_HOLE - SOCKET_OVERHANG)
        centre_offset = (SOCKET_BOX - SOCKET_HOLE) / 2
        socket.move(round(x - centre_offset), round(y - SOCKET_BOX / 2))

    def _place_stripe(self):
        width = self.canvas.width()
        left = max(0, round(width * STRIPE_POSITION - STRIPE_WIDTH / 2))
        self.stripe.setGeometry(left, 0, STRIPE_WIDTH, self.canvas.height())
        self.overlay.setGeometry(0, 0, width, self.canvas.height())

    def stripe_x(self):
        """Where the boundary sits, for a host that wants to lay modules
        out on the correct side of it."""
        return self.stripe.x() if self.stripe else 0

    def _raise_layer(self):
        """Jacks and cables belong above the module windows -- including
        the one that was just raised by being focused."""
        self.stripe.lower()
        for socket in self._sockets.values():
            socket.raise_()
        self.overlay.raise_()

    def _watch(self, widget):
        """Mouse-move filter on a window and everything in it, so a cable
        can be hovered where it crosses over a module."""
        for child in [widget] + widget.findChildren(QtWidgets.QWidget):
            if child in self._filtered or isinstance(child, SocketWidget):
                continue
            child.setMouseTracking(True)
            child.installEventFilter(self)
            self._filtered.add(child)

    # -- animation -----------------------------------------------------

    def wake(self):
        self._awake = WAKE_FRAMES
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        moving = pg.step_physics(self.graph.cables, self._endpoints, self.appearance)
        self.graph.loop_members()
        self._sync_pips()
        self.overlay.update()
        if moving or self._drag is not None:
            self._awake = WAKE_FRAMES
        else:
            self._awake -= 1
            if self._awake <= 0:
                self._timer.stop()

    # -- geometry for the cables ---------------------------------------

    def _socket_centre(self, node_id, io, slot):
        socket = self._sockets.get((node_id, io, slot))
        if socket is None:
            return None
        point = socket.centre_in(self.canvas)
        return (float(point.x()), float(point.y()))

    def _knob_ring(self, node_id, label):
        """Centre and radius of a knob's modulation ring, in canvas
        coordinates."""
        knob = self._knobs.get((node_id, label))
        if knob is None or knob.parentWidget() is None:
            return None
        from notecolor.gui.synth_workspace import KNOB_SIZE
        centre = knob.mapTo(self.canvas, QtCore.QPoint(KNOB_SIZE // 2, KNOB_SIZE // 2))
        return (float(centre.x()), float(centre.y()), KNOB_SIZE / 2 + 5)

    def _endpoints(self, cable):
        start = self._socket_centre(cable.source, "out", self.graph.slot_of(cable, "out"))
        if start is None:
            return None
        if cable.knob is not None:
            ring = self._knob_ring(*cable.knob)
            if ring is None:
                return None
            cx, cy, radius = ring
            # A modulation cable stops at the ring's edge, on the side the
            # source is on, rather than burying its end under the knob.
            angle = math.atan2(start[1] - cy, start[0] - cx)
            return (start, (cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        end = self._socket_centre(cable.dest, "in", self.graph.slot_of(cable, "in"))
        return None if end is None else (start, end)

    def _curve(self, cable):
        ends = self._endpoints(cable)
        if ends is None or cable.px is None:
            return None
        start, end = ends
        return start, pg.control_point(cable, start, end), end

    # -- what is lit (decision 57 §3) ----------------------------------

    def _is_lit(self, cable):
        if cable is self._hover_cable:
            return True
        if self._turning is not None and cable.knob == self._turning:
            return True
        if self._focused_node is not None and (
                cable.source == self._focused_node
                or cable.dest == self._focused_node
                or (cable.knob and cable.knob[0] == self._focused_node)):
            return True
        socket = self._hover_socket
        if socket is not None:
            if socket.io == "out" and cable.source == socket.node_id \
                    and self.graph.slot_of(cable, "out") == socket.slot:
                return True
            if socket.io == "in" and cable.dest == socket.node_id \
                    and self.graph.slot_of(cable, "in") == socket.slot:
                return True
        return False

    def _cable_colour(self, cable, index):
        if cable.in_loop and self.appearance.loop_marking == "cables":
            return pg.LOOP_COLOUR
        if self.appearance.colour_scheme == "voice":
            return pg.VOICE_COLOURS[index % len(pg.VOICE_COLOURS)]
        return pg.ROLE_COLOURS[self.graph.cable_colour_role(cable)]

    def _sync_pips(self):
        pips = {}
        for index, cable in enumerate(self.graph.cables):
            colour = self._cable_colour(cable, index)
            pips[(cable.source, "out", self.graph.slot_of(cable, "out"))] = colour
            if cable.dest:
                pips[(cable.dest, "in", self.graph.slot_of(cable, "in"))] = colour
        for key, socket in self._sockets.items():
            socket.set_pip(pips.get(key))

    # -- painting ------------------------------------------------------

    def paint_regions(self, p):
        """The two halves the stripe divides, tinted and named. Painted by
        the canvas itself, under the module windows: a tint drawn over them
        would wash the modules out, and the whole point of naming the
        halves is to say which side a module is sitting on."""
        left = self.stripe.x()
        right = left + self.stripe.width()
        height = self.canvas.height()
        p.fillRect(QtCore.QRect(0, 0, left, height),
                   _qcolour(pg.ROLE_COLOURS[pg.SIDE_POLY], 14))
        p.fillRect(QtCore.QRect(right, 0, self.canvas.width() - right, height),
                   _qcolour(pg.ROLE_COLOURS[pg.SIDE_MONO], 13))
        p.setFont(theme.font(7))
        p.setPen(theme.ink(theme.EMBER))
        p.drawText(QtCore.QRect(12, 8, left - 24, 14),
                   QtCore.Qt.AlignLeft, "ONE PER HELD NOTE")
        p.drawText(QtCore.QRect(right + 12, 8, self.canvas.width() - right - 24, 14),
                   QtCore.Qt.AlignRight, "ONCE, FOR EVERYTHING")

    def paint_cables(self, p):
        for index, cable in enumerate(self.graph.cables):
            curve = self._curve(cable)
            if curve is None:
                continue
            start, control, end = curve
            path = QtGui.QPainterPath(QtCore.QPointF(*start))
            path.quadTo(QtCore.QPointF(*control), QtCore.QPointF(*end))

            lit = self._is_lit(cable)
            is_mod = cable.kind == pg.KIND_MOD
            alpha = 255 if lit else int(255 * self.appearance.resting_brightness
                                        * (0.7 if is_mod else 1.0))
            colour = _qcolour(self._cable_colour(cable, index), alpha)
            width = (2.0 if lit else 1.25) if is_mod else (3.2 if lit else 2.5)
            pen = QtGui.QPen(colour, width)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            if is_mod:
                pen.setStyle(QtCore.Qt.CustomDashLine)
                pen.setDashPattern([6, 3] if lit else [5, 4])
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPath(path)

    def paint_rings(self, p):
        """The dashed ring every modulated knob wears -- brighter while
        that knob is being turned (decision 57 §3). Painted here rather
        than in `Knob.paintEvent` for two reasons: the ring sits outside
        the knob's own rect and would be clipped, and decision 57 settles
        the knob's painting as unchanged."""
        for (node_id, label), _knob in self._knobs.items():
            cables = self.graph.knob_cables(node_id, label)
            target = self._drag_knob_target()
            is_target = target == (node_id, label)
            if not cables and not is_target:
                continue
            ring = self._knob_ring(node_id, label)
            if ring is None:
                continue
            cx, cy, radius = ring
            if is_target:
                ok = self._drag.get("ok", False)
                colour = _qcolour(theme.CLAY_RED if not ok else theme.CORAL)
                pen = QtGui.QPen(colour, 1)
            else:
                lit = any(self._is_lit(c) for c in cables)
                alpha = 255 if lit else int(255 * self.appearance.resting_brightness * 0.85)
                pen = QtGui.QPen(_qcolour(pg.ROLE_COLOURS[pg.KIND_MOD], alpha), 1)
                pen.setStyle(QtCore.Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(QtCore.QPointF(cx, cy), radius, radius)

    def _drag_knob_target(self):
        if self._drag is None:
            return None
        target = self._drag.get("target")
        if target is None or target.kind != "knob":
            return None
        return (target.node_id, target.knob)

    def paint_drag(self, p):
        """The cable being pulled out of a jack. Its loose end hangs too --
        it is the same object with weight, not a rubber band."""
        if self._drag is None:
            return
        start = self._drag.get("start")
        point = self._drag.get("point")
        if start is None or point is None:
            return
        is_mod = self._drag["kind"] == pg.KIND_MOD
        distance = math.hypot(point[0] - start[0], point[1] - start[1])
        mid = ((start[0] + point[0]) / 2,
               (start[1] + point[1]) / 2 + pg.rest_sag(distance, self.appearance, is_mod))
        control = (2 * mid[0] - (start[0] + point[0]) / 2,
                   2 * mid[1] - (start[1] + point[1]) / 2)
        path = QtGui.QPainterPath(QtCore.QPointF(*start))
        path.quadTo(QtCore.QPointF(*control), QtCore.QPointF(*point))
        role = pg.KIND_MOD if is_mod else self.graph.node_colour_role(self._drag["node"])
        pen = QtGui.QPen(_qcolour(pg.ROLE_COLOURS[role]), 1.6 if is_mod else 2.8)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        if is_mod:
            pen.setStyle(QtCore.Qt.CustomDashLine)
            pen.setDashPattern([6, 3])
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawPath(path)

    def paint_annotations(self, p):
        if self.appearance.loop_marking != "off":
            self._paint_loop_badge(p)
        if self._hover_cable is not None and self._drag is None:
            self._paint_hover_label(p)
        if self._refusal is not None and self.appearance.explain in ("inline", "both"):
            self._paint_callout(p)

    def _paint_loop_badge(self, p):
        members = [n for n in self.graph.loop_members() if n != MixStripe.NODE_ID]
        rects = [r for r in (self._host_rect(n) for n in members) if r is not None]
        if not rects:
            return
        left = min(r.left() for r in rects)
        right = max(r.right() for r in rects)
        bottom = max(r.bottom() for r in rects)
        text = "FEEDBACK LOOP"
        p.setFont(theme.font(7))
        width = QtGui.QFontMetrics(p.font()).horizontalAdvance(text) + 14
        x = max(4, min(self.canvas.width() - width - 4, (left + right) / 2 - width / 2))
        y = max(2, min(self.canvas.height() - 20, bottom + 10))
        box = QtCore.QRectF(x, y, width, 16)
        p.setPen(QtGui.QPen(theme.ink(theme.COPPER), 1))
        p.setBrush(theme.ink(theme.INK_0, alpha=theme.CHROME_ALPHA))
        p.drawRect(box)
        p.setPen(theme.ink(theme.COPPER_LIGHT))
        p.drawText(box, QtCore.Qt.AlignCenter, text)

    def _paint_hover_label(self, p):
        cable = self._hover_cable
        if cable.px is None:
            return
        text = self.cable_label(cable)
        p.setFont(theme.font(7))
        width = QtGui.QFontMetrics(p.font()).horizontalAdvance(text) + 12
        x = max(4, min(self.canvas.width() - width - 4, cable.px - width / 2))
        y = max(2, cable.py - 26)
        box = QtCore.QRectF(x, y, width, 16)
        p.setPen(QtGui.QPen(theme.ink(theme.RULE_2), 1))
        p.setBrush(theme.ink(theme.INK_0, alpha=theme.CHROME_ALPHA))
        p.drawRect(box)
        p.setPen(theme.ink(theme.LINEN_DIM))
        p.drawText(box, QtCore.Qt.AlignCenter, text)

    def _paint_callout(self, p):
        """A refusal explains itself where the cable was dropped -- the
        default position of decision 57 §5's `explain` setting."""
        text = self._refusal["text"]
        point = self._refusal["point"]
        p.setFont(theme.font(8))
        metrics = QtGui.QFontMetrics(p.font())
        width = 260
        body = QtCore.QRect(0, 0, width - 16, 1000)
        bounds = metrics.boundingRect(body, QtCore.Qt.TextWordWrap, text)
        height = bounds.height() + 16
        x = max(6, min(self.canvas.width() - width - 6, point[0] + 22))
        y = max(6, min(self.canvas.height() - height - 6, point[1] - 12))
        box = QtCore.QRectF(x, y, width, height)
        p.setPen(QtGui.QPen(theme.ink(theme.CLAY_RED), 1))
        p.setBrush(theme.ink(theme.INK_0, alpha=244))
        p.drawRect(box)
        p.setPen(theme.ink(theme.LINEN))
        p.drawText(box.adjusted(8, 8, -8, -8), QtCore.Qt.TextWordWrap, text)

    def cable_label(self, cable):
        if cable.knob is not None:
            return f"{self.graph.title(cable.source)} → " \
                   f"{self.graph.title(cable.knob[0])} · {cable.knob[1]}"
        return f"{self.graph.title(cable.source)} → {self.graph.title(cable.dest)}"

    # -- hover ---------------------------------------------------------

    def _on_socket_hovered(self, socket, entered):
        self._hover_socket = socket if entered else None
        self.wake()

    def _on_knob_turning(self, node_id, label, on):
        self._turning = (node_id, label) if on else None
        if on:
            self._focused_node = node_id
        self.wake()

    def _on_knob_wheel(self, node_id, label):
        self._turning = (node_id, label)
        self._wheel_timer.start(WHEEL_LIT_MS)
        self.wake()

    def _end_wheel_turn(self):
        self._turning = None
        self.wake()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QtCore.QEvent.MouseMove:
            self._update_hover(self._to_canvas(obj, event))
        elif kind == QtCore.QEvent.MouseButtonPress:
            if self._try_unplug(obj, event):
                return True
            self.clear_refusal()
        elif kind == QtCore.QEvent.Leave and obj is self.canvas:
            self._update_hover(None)
        return False

    def _to_canvas(self, obj, event):
        point = event.position().toPoint()
        if obj is self.canvas:
            return (float(point.x()), float(point.y()))
        mapped = obj.mapTo(self.canvas, point)
        return (float(mapped.x()), float(mapped.y()))

    def _cable_at(self, point):
        best, best_distance = None, CABLE_HIT_TOLERANCE
        for cable in self.graph.cables:
            curve = self._curve(cable)
            if curve is None:
                continue
            distance = pg.distance_to_curve(curve[0], curve[1], curve[2], point)
            if distance < best_distance:
                best, best_distance = cable, distance
        return best

    def _update_hover(self, point):
        found = None if point is None or self._drag is not None else self._cable_at(point)
        if found is not self._hover_cable:
            self._hover_cable = found
            self.wake()

    #: Widget class names a cable click must never steal from. Cables are
    #: drawn over the modules, so a cable crosses a title bar or a knob
    #: constantly -- and dragging the window or turning the knob has to
    #: keep working. Everywhere else (the canvas itself, a module's empty
    #: body) a click on a cable unplugs it, which is the only way a cable
    #: comes out.
    UNPLUG_BLOCKERS = ("Knob", "_TitleBar", "QToolButton", "_DrawerRow")

    def _try_unplug(self, obj, event):
        if event.button() != QtCore.Qt.LeftButton or self._drag is not None:
            return False
        if isinstance(obj, SocketWidget):
            return False
        if type(obj).__name__ in self.UNPLUG_BLOCKERS:
            return False
        cable = self._cable_at(self._to_canvas(obj, event))
        if cable is None:
            return False
        self.unplug(cable)
        return True

    def unplug(self, cable):
        label = self.cable_label(cable)
        if not self.graph.disconnect(cable):
            return
        if self._hover_cable is cable:
            self._hover_cable = None
        self._announce(f"unplugged · {label}")
        self.relayout()
        self.cablesChanged.emit()

    # -- the connect gesture -------------------------------------------

    def _on_socket_pressed(self, socket):
        if socket.io != "out":
            return
        self.clear_refusal()
        socket.set_state("armed")
        self._drag = {"node": socket.node_id, "socket": socket,
                      "kind": socket.kind, "target": None, "ok": False,
                      "start": self._socket_centre(socket.node_id, "out", socket.slot),
                      "point": None}
        self.wake()

    def drag_move(self, global_pos):
        if self._drag is None:
            return
        local = self.canvas.mapFromGlobal(global_pos)
        self._drag["point"] = (float(local.x()), float(local.y()))
        self._set_target(self._target_at(global_pos))
        self.wake()

    def drag_release(self, global_pos):
        if self._drag is None:
            return
        drag = self._drag
        target = self._target_at(global_pos)
        drag["socket"].set_state("")
        self._set_target(None)
        self._drag = None
        if target is not None:
            verdict = self.graph.judge(drag["node"], target)
            if verdict.ok:
                self.graph.connect(drag["node"], target)
                self._announce(f"patched · {self._describe(drag['node'], target)}")
                self.relayout()
                self.cablesChanged.emit()
            else:
                self.refuse(target, verdict.reason)
        self.wake()

    def _describe(self, source_id, target):
        text = f"{self.graph.title(source_id)} → {self.graph.title(target.node_id)}"
        return text + (f" · {target.knob}" if target.kind == "knob" else "")

    def _target_at(self, global_pos):
        """What is under the cursor: an input jack, or a knob. Found by
        screen position rather than by Qt's event routing, because the
        press that started this drag holds the mouse grab on the source
        socket -- so no other widget will see these moves at all."""
        widget = QtWidgets.QApplication.widgetAt(global_pos)
        from notecolor.gui.synth_workspace import Knob
        while widget is not None:
            if isinstance(widget, SocketWidget):
                if widget.io == "in":
                    return Target("socket", widget.node_id)
                return None
            if isinstance(widget, Knob):
                key = next((k for k, v in self._knobs.items() if v is widget), None)
                return None if key is None else Target("knob", key[0], key[1])
            widget = widget.parentWidget()
        return None

    def _set_target(self, target):
        if self._drag is None:
            return
        for socket in self._sockets.values():
            if socket.state in ("ok", "bad"):
                socket.set_state("")
        self._drag["target"] = target
        if target is None:
            self._drag["ok"] = False
            return
        verdict = self.graph.judge(self._drag["node"], target)
        self._drag["ok"] = verdict.ok
        if target.kind == "socket":
            for s in self._sockets_for_node_in(target.node_id):
                if s.slot == self.graph.socket_counts(target.node_id)[0] - 1:
                    s.set_state("ok" if verdict.ok else "bad")

    def _sockets_for_node_in(self, node_id):
        return [s for (n, io, _slot), s in self._sockets.items()
                if n == node_id and io == "in"]

    # -- refusals ------------------------------------------------------

    def refuse(self, target, reason):
        """A refused cable flashes at the jack it was dropped on and says
        why -- the whole point of the contract being accept/refuse *with a
        reason* (#203)."""
        point = None
        if target.kind == "socket":
            sockets = self._sockets_for_node_in(target.node_id)
            spare = max(sockets, key=lambda s: s.slot, default=None)
            if spare is not None:
                self._start_flash(spare)
                centre = spare.centre_in(self.canvas)
                point = (float(centre.x()), float(centre.y()))
        else:
            ring = self._knob_ring(target.node_id, target.knob)
            if ring is not None:
                point = (ring[0], ring[1])
        host = self._hosts.get(target.node_id)
        flash_refusal = getattr(host, "flash_refusal", None)
        if flash_refusal is not None:
            flash_refusal()
        if point is not None:
            self._refusal = {"text": reason, "point": point}
            self._refusal_timer.start(REFUSAL_MS)
        if self.appearance.explain in ("status", "both"):
            self.statusChanged.emit(reason, True)
        elif self.appearance.explain == "inline":
            self.statusChanged.emit("refused — see the note by the jack", True)
        self.wake()

    def _start_flash(self, socket):
        self._flash = {"socket": socket, "left": 6}
        self._flash_timer.start(150)
        socket.set_state("flash")

    def _flash_step(self):
        if self._flash is None:
            self._flash_timer.stop()
            return
        self._flash["left"] -= 1
        socket = self._flash["socket"]
        socket.set_state("" if socket.state == "flash" else "flash")
        if self._flash["left"] <= 0:
            socket.set_state("")
            self._flash = None
            self._flash_timer.stop()

    def clear_refusal(self):
        if self._refusal is None:
            return
        self._refusal = None
        self._refusal_timer.stop()
        if self._flash is not None:
            self._flash["socket"].set_state("")
            self._flash = None
            self._flash_timer.stop()
        self.announce_summary()
        self.wake()

    def message_pending(self):
        """Whether something just-happened (or a refusal) is holding the
        status line. A host polling for the standing summary should leave
        the line alone while this is true."""
        return self._message_timer.isActive() or self._refusal is not None

    def _announce(self, text):
        """Says something that has just happened, then lets the standing
        summary take the line back."""
        self.statusChanged.emit(text, False)
        self._message_timer.start(MESSAGE_MS)

    def announce_summary(self):
        self._message_timer.stop()
        self.statusChanged.emit(self.summary(), False)

    def summary(self):
        self.graph.loop_members()
        count = len(self.graph.cables)
        loops = any(c.in_loop for c in self.graph.cables)
        word = "cable" if count == 1 else "cables"
        return f"{count} {word} · " + ("feedback loop" if loops else "no loops")

    # -- persistence ---------------------------------------------------

    def snapshot(self):
        return self.graph.snapshot()

    def restore(self, entries):
        self.graph.restore(entries or [])
        self.relayout()
        self.cablesChanged.emit()
