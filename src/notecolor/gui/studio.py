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
        self.playhead.setZValue(100)

    def _clip(self, row, track, clip):
        x = clip.start_beat * self.px_per_beat
        w = max(6.0, clip.length_beats * self.px_per_beat - 1)
        y, h = row * LANE_H + 4, LANE_H - 9

        body = QtWidgets.QGraphicsRectItem(x, y, w, h)
        body.setBrush(QtGui.QBrush(theme.CLIP_BODY))
        body.setPen(QtGui.QPen(theme.CLIP_EDGE, 1))
        self.addItem(body)
        head = QtWidgets.QGraphicsRectItem(x, y, w, 12)
        head.setBrush(QtGui.QBrush(theme.CLIP_HEAD))
        head.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        self.addItem(head)
        label = self.addText(clip.name or track.name.lower(), theme.font(7))
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
            self.addItem(item)

    def move_playhead(self, beat):
        if self.playhead is None:
            return
        x = beat * self.px_per_beat
        self.playhead.setLine(x, 0, x, self.sceneRect().height())


class Ruler(QtWidgets.QWidget):
    def __init__(self, scene):
        super().__init__()
        self.scene, self.offset = scene, 0
        self.setFixedHeight(RULER_H)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

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
        p.setPen(theme.RULE)
        p.drawLine(0, RULER_H - 1, self.width(), RULER_H - 1)
        p.end()


class TrackHeaders(QtWidgets.QWidget):
    def __init__(self, project):
        super().__init__()
        self.project = project
        self.setFixedWidth(HEADER_W)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        for row, track in enumerate(self.project.tracks):
            y = row * LANE_H
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
            for i, (letter, on) in enumerate((("m", track.muted), ("s", track.soloed),
                                              ("r", False))):
                box = QtCore.QRect(12 + i * 22, y + 24, 18, 14)
                p.setPen(theme.RULE_STRONG)
                p.drawRect(box)
                p.setPen(theme.ARMED if on else theme.TEXT_FAINT)
                p.drawText(box, QtCore.Qt.AlignCenter, letter)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(HEADER_W - 1, 0, HEADER_W - 1, self.height())
        p.end()


class TransportBar(QtWidgets.QWidget):
    def __init__(self, project):
        super().__init__()
        self.project = project
        self.snapshot = None
        self.message = ""
        self.setFixedHeight(38)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        playing = bool(self.snapshot and self.snapshot.playing)
        p.setFont(theme.font(10))
        for i, (glyph, lit) in enumerate((("|◀", False), ("■", not playing),
                                          ("▶", playing), ("●", False))):
            box = QtCore.QRect(10 + i * 30, 8, 26, 22)
            p.setPen(theme.RULE_STRONG)
            p.drawRect(box)
            p.setPen(theme.PLAYHEAD if lit else theme.TEXT_FAINT)
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

    def __init__(self, project, transport=None, player=None, audio_error=None):
        super().__init__()
        self.project = project
        self.transport = transport
        self.player = player
        self.audio_error = audio_error
        self.setWindowTitle(f"visualnote studio — {project.name}")
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
        self.view.setStyleSheet("background: transparent; border: 0;")
        self.view.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.ruler = Ruler(self.scene)
        self.headers = TrackHeaders(self.project)
        self.view.horizontalScrollBar().valueChanged.connect(self._sync_ruler)
        self.view.verticalScrollBar().valueChanged.connect(
            lambda _v: self.headers.update())

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

        self.setStyleSheet(theme.main_stylesheet())

    def _sync_ruler(self, value):
        self.ruler.offset = value
        self.ruler.update()

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
        key = event.key()
        if key in (QtCore.Qt.Key_Q, QtCore.Qt.Key_Escape):
            self.close()
        elif self.transport is None:
            return
        elif key == QtCore.Qt.Key_Space:
            if self.transport.snapshot().playing:
                self.transport.stop()
            else:
                self.transport.play()
        elif key == QtCore.Qt.Key_Home:
            self.transport.stop()
            self.transport.locate(0.0)
