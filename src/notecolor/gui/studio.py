"""VisualNote Studio -- the arrange window (map #145, milestone 1).

Draws a real `Project` and is driven by the real `Transport`: the playhead
position comes from a snapshot the **audio callback** published, never from a
Qt timer. The Qt timer here only decides how often to *repaint*; it has no say
in where the playhead is. That distinction is the whole point of #151's seam,
and it is why the playhead does not drift against the sound.

Rendering is `QGraphicsView`/`QGraphicsScene` per #147, and every colour comes
from `theme` -- see that module for the rule that saturation means pitch.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from notecolor.analysis.color_map import (
    NOTE_NAMES_FIFTHS, fifths_index, hsl_to_rgb255, hue_for_step,
)
from notecolor.gui import theme
from notecolor.project import edit
from notecolor.project.bundle import (
    BUNDLE_SUFFIX, bundle_path, default_projects_dir, save_project,
)
from notecolor.gui.recent import recent_paths, remember_path
from notecolor.project.model import AUDIO_TRACK, NoteClip

LANE_H, HEADER_W, RULER_H = 54, 196, 22
DEFAULT_PX_PER_BEAT = 34
MIN_BARS = 16


def pitch_colour(pitch_class, lightness=0.66, alpha=255):
    """The one place a saturated colour is allowed, and it always means pitch.

    Lifted against Copper's warm ground: notes in the red-orange third of the
    fifths wheel otherwise sink into the brown.
    """
    rgb = hsl_to_rgb255(hue_for_step(fifths_index(pitch_class)), 0.62, lightness)
    return QtGui.QColor(*rgb, alpha)


class ArrangeScene(QtWidgets.QGraphicsScene):
    #: Explicit stacking, because the order items are *added* stopped deciding
    #: it the moment clips became draggable: giving the clip body a z-value so
    #: `clip_at()` could find it put the body on top of its own notes, and the
    #: notes -- the only colour on screen and the entire point of the app --
    #: silently vanished under it.
    Z_LANE, Z_CLIP, Z_CLIP_HEAD, Z_NOTE, Z_PLAYHEAD = 0, 1, 2, 3, 100

    def __init__(self, project, px_per_beat=DEFAULT_PX_PER_BEAT):
        super().__init__()
        self.project = project
        self.px_per_beat = px_per_beat
        self.playhead = None
        self.rebuild()

    @property
    def beats_per_bar(self):
        return max(1.0, self.project.time_signature.beats_per_bar)

    @property
    def total_beats(self):
        return max(self.project.end_beat, MIN_BARS * self.beats_per_bar)

    def _pen(self, colour, width=1):
        pen = QtGui.QPen(colour)
        pen.setWidth(width)
        pen.setCosmetic(True)
        return pen

    def rebuild(self):
        self.clear()
        rows = max(1, len(self.project.tracks))
        width = self.total_beats * self.px_per_beat
        height = rows * LANE_H
        self.setSceneRect(0, 0, width, height)

        for row in range(rows):
            self.addRect(0, row * LANE_H, width, LANE_H, QtGui.QPen(QtCore.Qt.NoPen),
                         QtGui.QBrush(theme.LANE if row % 2 == 0 else theme.LANE_ALT))
            self.addLine(0, (row + 1) * LANE_H, width, (row + 1) * LANE_H,
                         self._pen(theme.RULE))

        beat = 0.0
        while beat <= self.total_beats + 0.001:
            x = beat * self.px_per_beat
            on_bar = abs(beat % self.beats_per_bar) < 1e-6
            self.addLine(x, 0, x, height,
                         self._pen(theme.RULE_STRONG if on_bar else theme.RULE))
            beat += 1.0

        for row, track in enumerate(self.project.tracks):
            for clip in track.clips:
                self._clip(row, track, clip)

        self.playhead = self.addLine(0, 0, 0, height, self._pen(theme.PLAYHEAD))
        self.playhead.setZValue(self.Z_PLAYHEAD)

    def _clip(self, row, track, clip):
        x = clip.start_beat * self.px_per_beat
        w = max(6.0, clip.length_beats * self.px_per_beat - 1)
        y, h = row * LANE_H + 4, LANE_H - 9

        body = QtWidgets.QGraphicsRectItem(x, y, w, h)
        body.setBrush(QtGui.QBrush(theme.CLIP_BODY))
        body.setPen(QtGui.QPen(theme.CLIP_EDGE, 1))
        # Carry enough to identify what was grabbed. `MoveClip` existed and was
        # tested from the start with nothing able to drive it from the mouse.
        body.setData(0, row)
        body.setData(1, clip)
        body.setZValue(self.Z_CLIP)
        self.addItem(body)
        head = QtWidgets.QGraphicsRectItem(x, y, w, 12)
        head.setBrush(QtGui.QBrush(theme.CLIP_HEAD))
        head.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        head.setZValue(self.Z_CLIP_HEAD)
        self.addItem(head)
        label = self.addText(clip.name or track.name.lower(), theme.font(7))
        label.setZValue(self.Z_CLIP_HEAD)
        label.setDefaultTextColor(theme.TEXT_DIM)
        label.setPos(x + 2, y - 3)

        if track.kind == AUDIO_TRACK or not isinstance(clip, NoteClip) or not clip.notes:
            return
        pitches = [n.pitch for n in clip.notes]
        low, high = min(pitches), max(pitches)
        span = max(1, high - low)
        top, usable = y + 15, h - 19
        for note in clip.notes:
            nx = x + note.start_beat * self.px_per_beat
            nw = max(2.0, note.duration_beats * self.px_per_beat - 2)
            ny = top + usable * (1.0 - (note.pitch - low) / span) - 1.5
            item = QtWidgets.QGraphicsRectItem(nx, ny, nw, 3)
            item.setBrush(QtGui.QBrush(pitch_colour(note.pitch_class)))
            item.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            item.setZValue(self.Z_NOTE)
            self.addItem(item)

    def clip_at(self, scene_point):
        """`(row, clip)` under a scene position, or `None`.

        Topmost first, so a clip wins over the lane behind it.
        """
        for item in self.items(scene_point):
            clip = item.data(1)
            if clip is not None:
                return item.data(0), clip
        return None

    def move_playhead(self, beat):
        if self.playhead is None:
            return
        x = beat * self.px_per_beat
        self.playhead.setLine(x, 0, x, self.sceneRect().height())


class Ruler(QtWidgets.QWidget):
    """Bar numbers, click-to-locate, and drag-to-set-a-loop.

    Clicking a ruler to move the playhead is close to muscle memory, so it is
    here rather than behind a menu. Dragging sets the loop region, which is the
    same gesture the `tab` view's `[`/`]` marks express in a terminal -- one
    concept, two front-ends, per this map's parity rule.
    """

    located = QtCore.Signal(float)          # beat
    loop_set = QtCore.Signal(float, float)  # start beat, end beat

    def __init__(self, scene):
        super().__init__()
        self.scene, self.offset = scene, 0
        self.loop = None
        self._drag_from = None
        self.setFixedHeight(RULER_H)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def _beat_at(self, x):
        return max(0.0, (x + self.offset) / self.scene.px_per_beat)

    def mousePressEvent(self, event):
        beat = self._beat_at(event.position().x())
        if event.button() == QtCore.Qt.RightButton:
            self.loop_set.emit(0.0, 0.0)     # right-click clears the loop
            return
        self._drag_from = beat
        self.located.emit(beat)

    #: A drag has to exceed this before it counts as one. Below it, the
    #: gesture was a click that jittered -- extremely common on a trackpad --
    #: and overwriting the committed loop's highlight with a near-invisible
    #: sliver at the click point is not what the user did.
    DRAG_THRESHOLD_BEATS = 0.05

    def mouseMoveEvent(self, event):
        if self._drag_from is None:
            return
        beat = self._beat_at(event.position().x())
        if abs(beat - self._drag_from) < self.DRAG_THRESHOLD_BEATS:
            return
        # Order-independent, exactly as the tab view's marks are: dragging
        # right-to-left means the same thing as left-to-right.
        self.loop = (min(self._drag_from, beat), max(self._drag_from, beat))
        self.update()

    def mouseReleaseEvent(self, _event):
        if (self._drag_from is not None and self.loop
                and self.loop[1] - self.loop[0] > self.DRAG_THRESHOLD_BEATS):
            self.loop_set.emit(*self.loop)
        self._drag_from = None

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME_DEEP)
        p.setFont(theme.font(7))
        per_bar = self.scene.beats_per_bar * self.scene.px_per_beat
        bar = 0
        while bar * per_bar - self.offset < self.width() + per_bar:
            x = int(bar * per_bar - self.offset)
            if -60 < x < self.width() + 60:
                p.setPen(theme.RULE_STRONG)
                p.drawLine(x, RULER_H - 6, x, RULER_H)
                p.setPen(theme.TEXT_DIM)
                p.drawText(x + 3, RULER_H - 9, f"{bar + 1}")
            bar += 1
        if self.loop and self.loop[1] > self.loop[0]:
            x1 = int(self.loop[0] * self.scene.px_per_beat - self.offset)
            x2 = int(self.loop[1] * self.scene.px_per_beat - self.offset)
            p.fillRect(x1, 0, max(1, x2 - x1), RULER_H - 2, theme.SELECTION)
        p.setPen(theme.RULE)
        p.drawLine(0, RULER_H - 1, self.width(), RULER_H - 1)
        p.end()


class TrackHeaders(QtWidgets.QWidget):
    """Track names and their mute/solo buttons.

    Same discipline as the transport: the rectangles used for hit-testing are
    the ones used for drawing.
    """

    toggled = QtCore.Signal(int, str)       # track index, "m" | "s"
    selected = QtCore.Signal(int)

    LETTERS = ("m", "s", "r")

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.selected_index = 0
        self.scroll = 0
        self.setFixedWidth(HEADER_W)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def _button_rect(self, row, index):
        return QtCore.QRect(12 + index * 22, row * LANE_H - self.scroll + 24, 18, 14)

    def mousePressEvent(self, event):
        point = event.position().toPoint()
        row = int((point.y() + self.scroll) // LANE_H)
        if not 0 <= row < len(self.project.tracks):
            return
        self.selected_index = row
        self.selected.emit(row)
        for index, letter in enumerate(self.LETTERS):
            if letter != "r" and self._button_rect(row, index).contains(point):
                self.toggled.emit(row, letter)
                return
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        for row, track in enumerate(self.project.tracks):
            y = row * LANE_H - self.scroll
            if row % 2:
                p.fillRect(0, y, HEADER_W, LANE_H, theme.CHROME_DEEP)
            p.setPen(theme.RULE)
            p.drawLine(0, y + LANE_H - 1, HEADER_W, y + LANE_H - 1)
            if track.color_pitch_class is not None:
                p.fillRect(0, y + 6, 2, LANE_H - 13,
                           pitch_colour(track.color_pitch_class))
            p.setPen(theme.TEXT)
            p.setFont(theme.font(9, bold=True))
            p.drawText(12, y + 17, track.name[:18].upper())
            p.setPen(theme.TEXT_FAINT)
            p.setFont(theme.font(7))
            p.drawText(HEADER_W - 46, y + 17,
                       "perc" if track.color_pitch_class is None else "note")
            if row == self.selected_index:
                p.fillRect(0, y, 3, LANE_H - 1, theme.PLAYHEAD)
            for i, (letter, on) in enumerate((("m", track.muted), ("s", track.soloed),
                                              ("r", False))):
                box = self._button_rect(row, i)
                # "r" is drawn but does nothing yet, so it is drawn as
                # disabled -- a control that looks live and is inert is worse
                # than one that says it is not ready.
                inert = letter == "r"
                p.setPen(theme.RULE if inert else theme.RULE_STRONG)
                p.drawRect(box)
                p.setPen(theme.TEXT_FAINT if inert
                         else (theme.ARMED if on else theme.TEXT_DIM))
                p.drawText(box, QtCore.Qt.AlignCenter, letter)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(HEADER_W - 1, 0, HEADER_W - 1, self.height())
        p.end()


class TransportBar(QtWidgets.QWidget):
    """Transport controls. Buttons are real hit targets, not painted decor.

    Their rectangles are computed once in `_button_rects()` and used for both
    drawing and hit-testing, so what is clickable is exactly what is drawn --
    the two cannot drift apart the way parallel geometry always eventually
    does.
    """

    #: (id, glyph, tooltip) in the order every transport in the world has them.
    BUTTONS = (("rewind", "|◀", "return to start (home)"),
               ("stop", "■", "stop (space)"),
               ("play", "▶", "play (space)"),
               ("record", "●", "record — not implemented yet"),
               ("loop", "⟲", "toggle loop (l)"))

    clicked = QtCore.Signal(str)

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.snapshot = None
        self.message = ""
        self.setFixedHeight(38)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._hover = None

    def _button_rects(self):
        return {name: QtCore.QRect(10 + i * 30, 8, 26, 22)
                for i, (name, _glyph, _tip) in enumerate(self.BUTTONS)}

    def _at(self, point):
        for name, rect in self._button_rects().items():
            if rect.contains(point):
                return name
        return None

    def mousePressEvent(self, event):
        name = self._at(event.position().toPoint())
        if name:
            self.clicked.emit(name)

    def mouseMoveEvent(self, event):
        name = self._at(event.position().toPoint())
        if name != self._hover:
            self._hover = name
            tips = {n: t for n, _g, t in self.BUTTONS}
            self.setToolTip(tips.get(name, ""))
            self.update()

    def leaveEvent(self, _event):
        self._hover = None
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        playing = bool(self.snapshot and self.snapshot.playing)
        looping = bool(self.snapshot and self.snapshot.loop_enabled)
        lit = {"stop": not playing, "play": playing, "loop": looping}
        rects = self._button_rects()
        p.setFont(theme.font(10))
        for name, glyph, _tip in self.BUTTONS:
            box = rects[name]
            if name == self._hover:
                p.fillRect(box, theme.SELECTION)
            p.setPen(theme.RULE_STRONG)
            p.drawRect(box)
            p.setPen(theme.PLAYHEAD if lit.get(name) else theme.TEXT_FAINT)
            p.drawText(box, QtCore.Qt.AlignCenter, glyph)

        beat = self.snapshot.beat if self.snapshot else 0.0
        per_bar = self.project.time_signature.beats_per_bar
        bar = int(beat // per_bar) + 1
        in_bar = int(beat % per_bar) + 1
        tick = int((beat % 1.0) * 960)
        signature = self.project.time_signature
        readouts = [("position", f"{bar:03d}.{in_bar}.{tick:03d}"),
                    ("tempo", f"{self.project.tempo_map.bpm_at(beat):.2f}"),
                    ("sig", f"{signature.numerator}/{signature.denominator}")]
        x = 146
        for label, value in readouts:
            p.setPen(theme.TEXT_FAINT)
            p.setFont(theme.font(6))
            p.drawText(x, 15, label.upper())
            p.setPen(theme.TEXT)
            p.setFont(theme.font(11, bold=True))
            p.drawText(x, 30, value)
            x += max(76, len(value) * 9 + 22)
            p.setPen(theme.RULE)
            p.drawLine(x - 14, 8, x - 14, 30)

        p.setPen(theme.TEXT_FAINT)
        p.setFont(theme.font(7))
        p.drawText(x, 18, self.message or "space play    home rewind    q quit")
        if self.snapshot:
            p.drawText(x, 30, f"{self.snapshot.sample_rate} hz    "
                              f"xruns {self.snapshot.xruns}")
        p.end()


class StudioWindow(QtWidgets.QMainWindow):
    """The arrange window. Owns no clock -- it reads one."""

    #: How often to repaint. This is a *display* rate and nothing else: the
    #: playhead's position always comes from the audio callback's snapshot,
    #: so a slow repaint looks choppy but is never wrong.
    REPAINT_MS = 16

    #: Zoom limits, in pixels per beat. Below the floor a bar is a few pixels
    #: wide and nothing is clickable; above the ceiling one screen holds less
    #: than a bar. Both are clamps, not wraps -- the convention this repo's
    #: numeric settings already follow.
    MIN_PX_PER_BEAT, MAX_PX_PER_BEAT = 4.0, 220.0
    ZOOM_STEP = 1.25

    def __init__(self, project, transport=None, player=None, audio_error=None,
                 path=None):
        super().__init__()
        self.project = project
        self.transport = transport
        self.player = player
        self.audio_error = audio_error
        self.path = path
        self._shown = False
        self._drag = None
        self.edits = edit.EditStack()
        self.saved_revision = 0
        self._status = ""
        self.setWindowTitle(f"visualnote studio — {project.name}")
        self.setAcceptDrops(True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.resize(1280, 720)
        self._build()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(self.REPAINT_MS)

    def _build(self):
        central = QtWidgets.QWidget()
        central.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.transport_bar = TransportBar(self.project)
        self.transport_bar.clicked.connect(self._transport_action)
        if self.audio_error:
            self.transport_bar.message = f"no audio — {self.audio_error}"
        outer.addWidget(self.transport_bar)

        grid_host = QtWidgets.QWidget()
        grid_host.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        grid = QtWidgets.QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        self.scene = ArrangeScene(self.project)
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view.setBackgroundBrush(QtGui.QBrush(theme.CANVAS))
        self.view.setStyleSheet(theme.CANVAS_STYLESHEET)
        self.view.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.ruler = Ruler(self.scene)
        self.ruler.located.connect(self._locate)
        self.ruler.loop_set.connect(self._set_loop)
        self.headers = TrackHeaders(self.project)
        self.headers.toggled.connect(self._toggle_track)
        self.headers.selected.connect(lambda _i: self.headers.update())
        self.view.horizontalScrollBar().valueChanged.connect(self._sync_ruler)
        self.view.verticalScrollBar().valueChanged.connect(self._sync_headers)
        self.view.viewport().installEventFilter(self)
        self.view.setDragMode(QtWidgets.QGraphicsView.NoDrag)

        corner = QtWidgets.QWidget()
        corner.setFixedSize(HEADER_W, RULER_H)
        grid.addWidget(corner, 0, 0)
        grid.addWidget(self.ruler, 0, 1)
        grid.addWidget(self.headers, 1, 0)
        grid.addWidget(self.view, 1, 1)
        outer.addWidget(grid_host)
        self.setCentralWidget(central)

        for title, area in (("inspector", QtCore.Qt.LeftDockWidgetArea),
                            ("mixer", QtCore.Qt.RightDockWidgetArea)):
            dock = QtWidgets.QDockWidget(title, self)
            dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
            body = QtWidgets.QLabel(f"  {title}\n  ── empty ──")
            body.setAlignment(QtCore.Qt.AlignTop)
            body.setFont(theme.font(8))
            body.setFixedWidth(128)
            dock.setWidget(body)
            self.addDockWidget(area, dock)

        self._build_menus()
        self.setStyleSheet(theme.main_stylesheet())
        self._retitle()

    # -- menus and titles --------------------------------------------------

    def _build_menus(self):
        """A menu bar, because a window with no way to open a file is not an
        application -- it is a viewer for whatever the terminal handed it."""
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self._add(file_menu, "&Open…", "Ctrl+O", self.open_dialog)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self._rebuild_recent()
        file_menu.addSeparator()
        self._add(file_menu, "&Save", "Ctrl+S", self.save)
        self._add(file_menu, "Save &As…", "Ctrl+Shift+S", self.save_as)
        file_menu.addSeparator()
        self._add(file_menu, "&Quit", "Ctrl+Q", self.close)

        edit_menu = bar.addMenu("&Edit")
        self.undo_action = self._add(edit_menu, "&Undo", "Ctrl+Z", self.undo)
        self.redo_action = self._add(edit_menu, "&Redo", "Ctrl+Shift+Z", self.redo)
        edit_menu.addSeparator()
        self._add(edit_menu, "&Add Track", "Ctrl+T", self.add_track)
        self._add(edit_menu, "&Remove Track", "Ctrl+Shift+T", self.remove_track)
        self._add(edit_menu, "Re&name Track…", "F2", self.rename_track)
        self._add(edit_menu, "Set Te&mpo…", None, self.set_tempo)

        view_menu = bar.addMenu("&View")
        self._add(view_menu, "Zoom &In", "Ctrl++", lambda: self.zoom(self.ZOOM_STEP))
        self._add(view_menu, "Zoom &Out", "Ctrl+-", lambda: self.zoom(1 / self.ZOOM_STEP))
        self._add(view_menu, "Zoom to &Fit", "Ctrl+0", self.zoom_to_fit)

        transport_menu = bar.addMenu("&Transport")
        self._add(transport_menu, "&Play/Stop", "Space",
                  lambda: self._transport_action(
                      "stop" if self._playing() else "play"))
        self._add(transport_menu, "&Rewind", "Home",
                  lambda: self._transport_action("rewind"))
        self._add(transport_menu, "Toggle &Loop", "L", self._toggle_loop)

    def _add(self, menu, text, shortcut, slot):
        action = QtGui.QAction(text, self)
        if shortcut:
            action.setShortcut(QtGui.QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    def _playing(self):
        return bool(self.transport and self.transport.snapshot().playing)

    def _playhead_beat(self):
        return self.transport.snapshot().beat if self.transport else 0.0

    # -- track and tempo edits ---------------------------------------------

    def add_track(self):
        self.run(edit.AddTrack(self.project))
        self.headers.selected_index = len(self.project.tracks) - 1

    def remove_track(self):
        if not self.project.tracks:
            return self.say("no tracks to remove")
        index = min(self.headers.selected_index, len(self.project.tracks) - 1)
        self.run(edit.RemoveTrack(self.project, index))
        self.headers.selected_index = max(0, index - 1)

    def rename_track(self):
        if not self.project.tracks:
            return self.say("no tracks to rename")
        track = self.project.tracks[self.headers.selected_index]
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename track", "Name:", text=track.name)
        if ok and name.strip():
            self.run(edit.RenameTrack(track, self.project.unique_track_name(
                name.strip())))

    def set_tempo(self):
        current = self.project.tempo_map.bpm_at(0)
        bpm, ok = QtWidgets.QInputDialog.getDouble(
            self, "Tempo", "Beats per minute:", current, 20.0, 400.0, 2)
        if not ok:
            return
        self.run(edit.SetTempo(self.project, bpm))
        if self.transport is not None:
            # The transport holds its own reference; without this it keeps
            # converting beats at the old tempo and the playhead drifts from
            # the grid it is drawn on.
            self.transport.set_tempo_map(self.project.tempo_map)

    def _retitle(self):
        """The title says what is open and whether it is saved.

        `dirty` was tracked from the start and never displayed, which meant the
        one place a user looks to answer "did that save?" could not answer it.
        """
        mark = "• " if self.dirty else ""
        where = os.path.basename(self.path) if self.path else "unsaved"
        self.setWindowTitle(f"{mark}visualnote studio — {self.project.name} [{where}]")
        if hasattr(self, "undo_action"):
            self.undo_action.setText(f"&Undo {self.edits.undo_name() or ''}".rstrip())
            self.undo_action.setEnabled(self.edits.can_undo)
            self.redo_action.setText(f"&Redo {self.edits.redo_name() or ''}".rstrip())
            self.redo_action.setEnabled(self.edits.can_redo)

    def _rebuild_recent(self):
        self.recent_menu.clear()
        entries = recent_paths()
        if not entries:
            action = self.recent_menu.addAction("(nothing yet)")
            action.setEnabled(False)
            return
        for entry in entries:
            self._add(self.recent_menu, os.path.basename(entry), None,
                      lambda e=entry: self.open_path(e))

    # -- opening -----------------------------------------------------------

    def open_dialog(self):
        if not self._can_show_dialogs():
            return
        chosen, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open", default_projects_dir(),
            "Projects and scores (*.ncproj *.musicxml *.xml);;All files (*)")
        if chosen:
            self.open_path(chosen)

    def open_path(self, path):
        """Load another project into this window.

        Replaces the project in place rather than spawning a second window: the
        audio engine and its output device are process-wide and opened once
        (the convention `SessionState` set for the terminal app), so a second
        window would mean a second device or a shared one nobody owns.
        """
        from notecolor.gui.app import load_project

        if self.dirty and not self._confirm_discard():
            return False
        try:
            project = load_project(path)
        except Exception as exc:                    # noqa: BLE001
            self.say(f"cannot open: {exc}")
            return False
        self.project = project
        self.path = path if str(path).endswith(BUNDLE_SUFFIX) else None
        self.edits = edit.EditStack()
        self.saved_revision = self.edits.revision
        self.scene.project = project
        self.scene.rebuild()
        self.headers.project = project
        self.headers.selected_index = 0
        self.transport_bar.project = project
        if self.transport is not None:
            self.transport.set_tempo_map(project.tempo_map)
            self.transport.stop()
            self.transport.locate(0.0)
        if self.player is not None:
            self.player.project = project
            self.player.refresh()
        remember_path(path)
        self._rebuild_recent()
        self._retitle()
        self.say(f"opened {os.path.basename(str(path))}")
        return True

    def _confirm_discard(self):
        choice = QtWidgets.QMessageBox.question(
            self, "Unsaved changes",
            f"{self.project.name} has unsaved changes.",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel)
        if choice == QtWidgets.QMessageBox.Save:
            return self.save()
        return choice == QtWidgets.QMessageBox.Discard

    def zoom_to_fit(self):
        """Fit the whole project across the viewport."""
        beats = max(1.0, self.project.end_beat or self.scene.total_beats)
        width = max(200, self.view.viewport().width() - 20)
        self.zoom((width / beats) / self.scene.px_per_beat, 0.0)
        self.view.horizontalScrollBar().setValue(0)

    def showEvent(self, event):
        """Start at bar 1.

        A `QGraphicsView` whose scene is wider than its viewport opens
        **scrolled to the middle**, and the scrollbar has no range until after
        the first show -- so setting it during construction silently does
        nothing. Found by a test asserting where a canvas click lands: the app
        was opening halfway through the timeline, past the music.
        """
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            self.view.horizontalScrollBar().setValue(0)
            self.view.verticalScrollBar().setValue(0)

    def _sync_ruler(self, value):
        self.ruler.offset = value
        self.ruler.update()

    def _sync_headers(self, value):
        self.headers.scroll = value
        self.headers.update()

    # -- actions ----------------------------------------------------------

    def _transport_action(self, name):
        if self.transport is None:
            return
        if name == "play":
            self.transport.play()
        elif name == "stop":
            self.transport.stop()
        elif name == "rewind":
            self.transport.stop()
            self.transport.locate(0.0)
        elif name == "loop":
            self._toggle_loop()
        elif name == "record":
            self.say("record is not implemented yet")

    def _locate(self, beat):
        if self.transport is not None:
            self.transport.locate(self.snap(beat))

    def snap(self, beat):
        """Nearest grid position. The grid is one beat, or a bar when zoomed
        far enough out that a beat is only a few pixels wide -- snapping to
        something you cannot see is indistinguishable from a bug."""
        step = 1.0 if self.scene.px_per_beat >= 12 else self.scene.beats_per_bar
        return round(beat / step) * step

    def _set_loop(self, start, end):
        if self.transport is None:
            return
        if end <= start:
            self.ruler.loop = None
            self.transport.set_loop(0.0, 0.0, enabled=False)
            self.say("loop cleared")
            return
        start, end = self.snap(start), self.snap(end)
        if end <= start:
            end = start + self.scene.beats_per_bar
        self.ruler.loop = (start, end)
        self.transport.set_loop(start, end, enabled=True)
        self.say(f"loop {start:g}..{end:g}")

    def _toggle_loop(self):
        if self.transport is None:
            return
        snapshot = self.transport.snapshot()
        if snapshot.loop_enabled:
            self.transport.set_loop(snapshot.loop_start_beat,
                                    snapshot.loop_end_beat, enabled=False)
            self.say("loop off")
        elif snapshot.loop_end_beat > snapshot.loop_start_beat:
            # Read the transport's own last-set region, not the ruler's
            # transient drag state -- those disagree after an aborted drag,
            # and re-enabling the wrong one is an audible micro-stutter.
            self.transport.set_loop(snapshot.loop_start_beat,
                                    snapshot.loop_end_beat, enabled=True)
            self.ruler.loop = (snapshot.loop_start_beat, snapshot.loop_end_beat)
            self.ruler.update()
            self.say("loop on")
        else:
            self.say("drag on the ruler to set a loop first")

    def _toggle_track(self, index, letter):
        track = self.project.tracks[index]
        command = (edit.SetTrackMute(track, not track.muted) if letter == "m"
                   else edit.SetTrackSolo(track, not track.soloed))
        self.run(command)

    def run(self, command):
        """Apply an edit through the undo stack, and tell playback about it."""
        self.edits.run(command)
        self._after_edit(command.name)

    def _after_edit(self, name):
        if self.player is not None:
            self.player.refresh()
        self.headers.update()
        self.scene.rebuild()
        self._retitle()
        self.say(name)

    def undo(self):
        command = self.edits.undo()
        self._after_edit(f"undo {command.name}" if command else "nothing to undo")

    def redo(self):
        command = self.edits.redo()
        self._after_edit(f"redo {command.name}" if command else "nothing to redo")

    # -- zoom -------------------------------------------------------------

    def zoom(self, factor, anchor_beat=None):
        """Zoom horizontally, keeping `anchor_beat` under the same pixel.

        Without an anchor, zooming walks the music sideways out of view, which
        is the difference between a zoom that feels like a lens and one that
        feels like a scroll accident.
        """
        before = self.scene.px_per_beat
        after = max(self.MIN_PX_PER_BEAT,
                    min(self.MAX_PX_PER_BEAT, before * factor))
        if after == before:
            return
        bar = self.view.horizontalScrollBar()
        if anchor_beat is None:
            anchor_beat = (bar.value() + self.view.viewport().width() / 2) / before
        offset_px = anchor_beat * before - bar.value()
        self.scene.px_per_beat = after
        self.scene.rebuild()
        if self.transport is not None:
            self.scene.move_playhead(self.transport.snapshot().beat)
        bar.setValue(int(max(0, anchor_beat * after - offset_px)))
        self.ruler.update()
        self.say(f"zoom {after:.0f} px/beat")

    def eventFilter(self, watched, event):
        # Clicking anywhere in the track area moves the playhead. Every DAW
        # does this; restricting it to the ruler strip makes the largest
        # target on screen inert.
        if (watched is self.view.viewport()
                and event.type() == QtCore.QEvent.MouseButtonPress
                and event.button() == QtCore.Qt.LeftButton):
            point = self.view.mapToScene(event.position().toPoint())
            hit = self.scene.clip_at(point)
            if hit is not None:
                row, clip = hit
                self.headers.selected_index = row
                self.headers.update()
                self._drag = {"row": row, "clip": clip,
                              "grab": point.x() / self.scene.px_per_beat,
                              "from": clip.start_beat, "moved": False}
                return True
            self._locate(max(0.0, point.x() / self.scene.px_per_beat))
            return True
        if (watched is self.view.viewport()
                and event.type() == QtCore.QEvent.MouseMove and self._drag):
            point = self.view.mapToScene(event.position().toPoint())
            beat = point.x() / self.scene.px_per_beat
            target = self.snap(max(0.0, self._drag["from"]
                                   + beat - self._drag["grab"]))
            if target != self._drag["clip"].start_beat:
                self._drag["clip"].start_beat = target
                self._drag["moved"] = True
                self.scene.rebuild()
                self.scene.move_playhead(self._playhead_beat())
            return True
        if (watched is self.view.viewport()
                and event.type() == QtCore.QEvent.MouseButtonRelease and self._drag):
            drag, self._drag = self._drag, None
            if drag["moved"]:
                # The drag already moved the clip for live feedback; put it
                # back and redo it as a command, so one gesture is one undo.
                landed = drag["clip"].start_beat
                drag["clip"].start_beat = drag["from"]
                self.run(edit.MoveClip(self.project, drag["row"],
                                       drag["clip"], landed))
            return True
        if (watched is self.view.viewport()
                and event.type() == QtCore.QEvent.Wheel
                and event.modifiers() & QtCore.Qt.ControlModifier):
            beat = ((self.view.horizontalScrollBar().value()
                     + event.position().x()) / self.scene.px_per_beat)
            self.zoom(self.ZOOM_STEP if event.angleDelta().y() > 0
                      else 1 / self.ZOOM_STEP, beat)
            return True
        return super().eventFilter(watched, event)

    # -- files ------------------------------------------------------------

    @property
    def dirty(self):
        return self.edits.revision != self.saved_revision

    def save(self, path=None):
        target = path or self.path
        if target is None:
            return self.save_as()
        try:
            os.makedirs(os.path.dirname(os.path.abspath(bundle_path(target))),
                        exist_ok=True)
            self.path = save_project(self.project, target)
        except OSError as exc:
            # One guarded write path. `save_as()` used to create the parent
            # directory itself, unguarded, *before* opening its dialog -- so a
            # read-only or missing `~/Music` made Save As do nothing at all,
            # with the traceback going to a stderr that a .desktop launch does
            # not have.
            self.say(f"save failed: {exc}")
            return False
        self.saved_revision = self.edits.revision
        remember_path(self.path)
        if hasattr(self, "recent_menu"):
            self._rebuild_recent()
        self._retitle()
        self.say(f"saved {os.path.basename(self.path)}")
        return True

    def save_as(self):
        if not self._can_show_dialogs():
            return False
        base = os.path.join(default_projects_dir(), f"{self.project.name}")
        try:
            chosen, _filter = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save project", base, "VisualNote project (*.ncproj)")
        except Exception as exc:                    # noqa: BLE001
            self.say(f"save failed: {exc}")
            return False
        return self.save(bundle_path(chosen)) if chosen else False

    def _can_show_dialogs(self):
        """Whether a modal file dialog can actually be shown.

        Qt's `offscreen` and `minimal` platforms accept a modal dialog and
        then never return from it -- the window appears to freeze, with no
        diagnostic, which is exactly what a misconfigured `.desktop` entry or
        a container would produce. Saying so is better than hanging.
        """
        platform = QtWidgets.QApplication.platformName()
        if platform in ("offscreen", "minimal", ""):
            self.say(f"file dialogs need a real display (running on '{platform}')")
            return False
        return True

    def say(self, message):
        self._status = message
        self.transport_bar.message = message
        self.transport_bar.update()

    def _refresh(self):
        if self.transport is None:
            return
        snapshot = self.transport.snapshot()
        self.scene.move_playhead(snapshot.beat)
        self.transport_bar.snapshot = snapshot
        self.transport_bar.update()
        if snapshot.playing:
            self._follow(snapshot.beat)

    def _follow(self, beat):
        """Keep the playhead on screen without fighting a manual scroll: only
        scroll when it has actually left the viewport."""
        x = beat * self.scene.px_per_beat
        bar = self.view.horizontalScrollBar()
        left, width = bar.value(), self.view.viewport().width()
        if x < left or x > left + width - 40:
            bar.setValue(int(max(0, x - width * 0.25)))

    def keyPressEvent(self, event):
        key, mods = event.key(), event.modifiers()
        control = bool(mods & QtCore.Qt.ControlModifier)
        shift = bool(mods & QtCore.Qt.ShiftModifier)

        if control and key == QtCore.Qt.Key_S:
            self.save_as() if shift else self.save()
        elif control and key == QtCore.Qt.Key_Z:
            self.redo() if shift else self.undo()
        elif control and key == QtCore.Qt.Key_Y:
            self.redo()
        elif key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.zoom(self.ZOOM_STEP)
        elif key == QtCore.Qt.Key_Minus:
            self.zoom(1 / self.ZOOM_STEP)
        elif key in (QtCore.Qt.Key_Q, QtCore.Qt.Key_Escape):
            self.close()
        elif key == QtCore.Qt.Key_M:
            self._toggle_track(self.headers.selected_index, "m")
        elif key == QtCore.Qt.Key_S:
            self._toggle_track(self.headers.selected_index, "s")
        elif key == QtCore.Qt.Key_L:
            self._toggle_loop()
        elif key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            step = -1 if key == QtCore.Qt.Key_Up else 1
            count = max(1, len(self.project.tracks))
            self.headers.selected_index = (self.headers.selected_index + step) % count
            self.headers.update()
        elif self.transport is None:
            return
        elif key == QtCore.Qt.Key_Space:
            self._transport_action("stop" if self.transport.snapshot().playing
                                   else "play")
        elif key == QtCore.Qt.Key_Home:
            self._transport_action("rewind")
        elif key == QtCore.Qt.Key_End:
            self.transport.locate(self.project.end_beat)
        elif key in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            beats = self.scene.beats_per_bar if shift else 1.0
            direction = -1 if key == QtCore.Qt.Key_Left else 1
            self.transport.locate(max(0.0, self.transport.snapshot().beat
                                      + direction * beats))

    def closeEvent(self, event):
        """Refuse to lose work silently.

        The score editor already requires a second press to quit while dirty;
        this is the same promise in a window's idiom, and it is the one place
        in this app where closing can destroy something.
        """
        if not self.dirty:
            return event.accept()
        choice = QtWidgets.QMessageBox.question(
            self, "Unsaved changes",
            f"{self.project.name} has unsaved changes.",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel)
        if choice == QtWidgets.QMessageBox.Save:
            event.accept() if self.save() else event.ignore()
        elif choice == QtWidgets.QMessageBox.Discard:
            event.accept()
        else:
            event.ignore()
