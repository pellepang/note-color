"""VisualNote Studio skeleton -- a throwaway look at the arrange window.

Wayfinder map #145, ticket #152. Nothing here is meant to be kept: it exists
so there is something concrete to react to before the real window is built.

Two questions it is asking:

1. Does this read as a DAW? Transport, track headers, arrange canvas with a
   live ruler and moving playhead, a bottom editor pane, and mixer/inspector
   docks that are deliberately empty.
2. **How does this app's fifths colour palette coexist with DAW chrome?**
   Colour on every note is the entire point of the project, but a DAW surface
   is designed to recede, and saturated per-pitch hues fight it. Two takes,
   toggled live -- see the keys below.

Keys:  C  colour take (clip-level <-> note-level)
       T  chrome (graphite <-> silver, the Logic 9 register)
       Space  run/stop the playhead        Q / Esc  quit

Run:  .venv/bin/python prototypes/visualnote-studio-skeleton/demo.py
      ... --shot out.png   renders one frame offscreen and exits
"""

import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.join(_REPO, "src") not in sys.path:
    sys.path.append(os.path.join(_REPO, "src"))

if "--shot" in sys.argv and not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from notecolor.analysis.color_map import (  # noqa: E402
    NOTE_NAMES_FIFTHS,
    fifths_index,
    hsl_to_rgb255,
    hue_for_step,
)

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------
# Two chromes rather than one, because "old Logic Pro" is genuinely ambiguous:
# Logic 9 was a light silver-metallic window, while every DAW since has gone
# dark. The colour question below answers differently against each, so both
# are here to be compared rather than guessed at.

GRAPHITE = dict(
    name="graphite",
    window="#1e2024", chrome="#2b2e33", chrome_hi="#34383e",
    ruler="#26292e", lane_a="#212429", lane_b="#1c1f23",
    grid="#2f333a", grid_bar="#3d434c", text="#c8ccd2", text_dim="#7b828c",
    lcd="#10161a", lcd_text="#8fe3c0", playhead="#ff5f56", accent="#4c8dff",
    clip_neutral="#3a3f47", clip_edge="#4a5058",
)
SILVER = dict(
    name="silver",
    window="#b9bcc1", chrome="#d2d5da", chrome_hi="#e4e7ea",
    ruler="#c6c9cf", lane_a="#9fa3a9", lane_b="#989ca2",
    grid="#8b8f95", grid_bar="#75797f", text="#22252a", text_dim="#5c6views",
    lcd="#1b2320", lcd_text="#9ff0c8", playhead="#c8301f", accent="#2f6fd0",
    clip_neutral="#8e939a", clip_edge="#6f747b",
)
SILVER["text_dim"] = "#4d5157"

CLIP_COLOUR, NOTE_COLOUR = "clip", "note"

BARS = 32
BEATS_PER_BAR = 4
PX_PER_BEAT = 34
LANE_H = 62
HEADER_W = 186
RULER_H = 26


def pitch_rgb(pitch_class, lightness=0.5):
    """This app's own fifths hue for a pitch class, at a chosen lightness --
    the same mapping `tab`, the score writer and the synth keys already use, so
    a C here is the colour a detected C is everywhere else."""
    hue = hue_for_step(fifths_index(pitch_class))
    return hsl_to_rgb255(hue, 0.62, lightness)


def qcolor(pitch_class, lightness=0.5):
    return QtGui.QColor(*pitch_rgb(pitch_class, lightness))


# --------------------------------------------------------------------------
# Fake content -- one bar of "music" per clip, enough to colour
# --------------------------------------------------------------------------

PARTS = [
    ("Vocals",  7, [(0, 7), (2, 11), (4, 2), (6, 7), (8, 4)]),
    ("Guitar",  4, [(0, 4), (1, 9), (3, 0), (5, 4), (7, 11), (9, 2)]),
    ("Bass",    0, [(0, 0), (2, 0), (4, 7), (6, 5), (8, 0)]),
    ("Keys",    9, [(0, 9), (2, 4), (4, 0), (6, 9), (8, 2), (10, 7)]),
    ("Drums",  None, [(i, None) for i in range(0, 12, 1)]),
]
CLIPS = [
    (0, 0, 8), (0, 12, 12),
    (1, 4, 12), (1, 20, 8),
    (2, 0, 28),
    (3, 8, 16),
    (4, 0, 32),
]


class ArrangeScene(QtWidgets.QGraphicsScene):
    """The arrange canvas.

    `QGraphicsView`/`QGraphicsScene` per ticket #147's research: its default
    `MinimalViewportUpdate` already scopes repaints to what changed, which is
    what makes a playhead cheap to move across a mostly-static background
    without hand-rolling the double-buffering Qtractor does.
    """

    def __init__(self, theme, take):
        super().__init__()
        self.theme, self.take = theme, take
        self.width_px = BARS * BEATS_PER_BAR * PX_PER_BEAT
        self.height_px = len(PARTS) * LANE_H
        self.setSceneRect(0, 0, self.width_px, self.height_px)
        self.playhead = None
        self.rebuild()

    def rebuild(self):
        self.clear()
        t = self.theme
        for row in range(len(PARTS)):
            colour = t["lane_a"] if row % 2 == 0 else t["lane_b"]
            self.addRect(0, row * LANE_H, self.width_px, LANE_H,
                         QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(colour)))
        for beat in range(BARS * BEATS_PER_BAR + 1):
            x = beat * PX_PER_BEAT
            bar_line = beat % BEATS_PER_BAR == 0
            pen = QtGui.QPen(QtGui.QColor(t["grid_bar"] if bar_line else t["grid"]))
            pen.setWidth(1)
            self.addLine(x, 0, x, self.height_px, pen)
        for row, start, length in CLIPS:
            self._clip(row, start, length)
        pen = QtGui.QPen(QtGui.QColor(t["playhead"]))
        pen.setWidth(2)
        self.playhead = self.addLine(0, 0, 0, self.height_px, pen)
        self.playhead.setZValue(100)

    def _clip(self, row, start_beat, length_beats):
        t, name, pc, notes = self.theme, *PARTS[row]
        x, y = start_beat * PX_PER_BEAT, row * LANE_H + 5
        w, h = length_beats * PX_PER_BEAT - 2, LANE_H - 12

        if self.take == CLIP_COLOUR and pc is not None:
            body = qcolor(pc, 0.34)
            edge = qcolor(pc, 0.58)
        else:
            body = QtGui.QColor(t["clip_neutral"])
            edge = QtGui.QColor(t["clip_edge"])

        rect = QtWidgets.QGraphicsRectItem(x, y, w, h)
        rect.setBrush(QtGui.QBrush(body))
        rect.setPen(QtGui.QPen(edge, 1))
        self.addItem(rect)

        strip = QtWidgets.QGraphicsRectItem(x, y, w, 13)
        strip.setBrush(QtGui.QBrush(edge))
        strip.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        self.addItem(strip)
        label = self.addText(name, QtGui.QFont("Sans", 7))
        label.setDefaultTextColor(QtGui.QColor("#12141a"))
        label.setPos(x + 3, y - 2)

        # The notes inside. In the note-level take this is where all the colour
        # lives, so a clip's harmony is legible from the arrangement itself.
        inner_top, inner_h = y + 16, h - 20
        for beat_off, note_pc in notes:
            if beat_off >= length_beats:
                continue
            nx = x + beat_off * PX_PER_BEAT + 2
            nw = PX_PER_BEAT - 5
            if note_pc is None:                       # drums: no pitch to be honest about
                ny, nh, colour = inner_top + inner_h * 0.5, 3, QtGui.QColor(t["text_dim"])
            else:
                slot = fifths_index(note_pc) / 12.0
                ny = inner_top + inner_h * (1.0 - slot) - 3
                nh = 4
                colour = (qcolor(note_pc, 0.62) if self.take == NOTE_COLOUR
                          else QtGui.QColor(255, 255, 255, 150))
            bar = QtWidgets.QGraphicsRectItem(nx, ny, nw, nh)
            bar.setBrush(QtGui.QBrush(colour))
            bar.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            self.addItem(bar)

    def move_playhead(self, beat):
        x = beat * PX_PER_BEAT
        self.playhead.setLine(x, 0, x, self.height_px)


class Ruler(QtWidgets.QWidget):
    def __init__(self, theme):
        super().__init__()
        self.theme, self.offset = theme, 0
        self.setFixedHeight(RULER_H)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        t = self.theme
        p.fillRect(self.rect(), QtGui.QColor(t["ruler"]))
        p.setPen(QtGui.QColor(t["text_dim"]))
        f = QtGui.QFont("Sans", 7)
        p.setFont(f)
        for bar in range(BARS + 1):
            x = bar * BEATS_PER_BAR * PX_PER_BEAT - self.offset
            if -40 < x < self.width() + 40:
                p.drawLine(x, RULER_H - 8, x, RULER_H)
                p.drawText(x + 3, RULER_H - 11, str(bar + 1))
        p.end()


class TrackHeaders(QtWidgets.QWidget):
    def __init__(self, theme, take):
        super().__init__()
        self.theme, self.take = theme, take
        self.setFixedWidth(HEADER_W)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        t = self.theme
        p.fillRect(self.rect(), QtGui.QColor(t["chrome"]))
        for row, (name, pc, _notes) in enumerate(PARTS):
            y = row * LANE_H
            p.fillRect(0, y, HEADER_W, LANE_H - 1, QtGui.QColor(t["chrome_hi"] if row % 2 else t["chrome"]))
            swatch = QtGui.QColor(t["text_dim"]) if pc is None else qcolor(pc, 0.5)
            p.fillRect(0, y, 5, LANE_H - 1, swatch)
            p.setPen(QtGui.QColor(t["text"]))
            p.setFont(QtGui.QFont("Sans", 9, QtGui.QFont.DemiBold))
            p.drawText(16, y + 20, name)
            for i, (letter, on) in enumerate((("M", False), ("S", False), ("R", row == 0))):
                bx = 16 + i * 26
                box = QtCore.QRect(bx, y + 30, 22, 16)
                p.fillRect(box, QtGui.QColor(t["playhead"] if on else t["window"]))
                p.setPen(QtGui.QColor("#ffffff" if on else t["text_dim"]))
                p.setFont(QtGui.QFont("Sans", 7, QtGui.QFont.Bold))
                p.drawText(box, QtCore.Qt.AlignCenter, letter)
        p.end()


class Transport(QtWidgets.QWidget):
    def __init__(self, theme):
        super().__init__()
        self.theme, self.beat = theme, 0.0
        self.setFixedHeight(56)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        t = self.theme
        grad = QtGui.QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QtGui.QColor(t["chrome_hi"]))
        grad.setColorAt(1, QtGui.QColor(t["chrome"]))
        p.fillRect(self.rect(), grad)

        for i, glyph in enumerate("⏮⏹▶⏺⟳"):
            box = QtCore.QRect(14 + i * 38, 12, 32, 30)
            p.setBrush(QtGui.QColor(t["window"]))
            p.setPen(QtGui.QPen(QtGui.QColor(t["grid_bar"]), 1))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QtGui.QColor(t["playhead"] if glyph == "⏺" else t["text"]))
            p.setFont(QtGui.QFont("Sans", 12))
            p.drawText(box, QtCore.Qt.AlignCenter, glyph)

        lcd = QtCore.QRect(230, 10, 250, 36)
        p.setBrush(QtGui.QColor(t["lcd"]))
        p.setPen(QtGui.QPen(QtGui.QColor(t["grid_bar"]), 1))
        p.drawRoundedRect(lcd, 3, 3)
        bar = int(self.beat // BEATS_PER_BAR) + 1
        beat_in_bar = int(self.beat % BEATS_PER_BAR) + 1
        p.setPen(QtGui.QColor(t["lcd_text"]))
        p.setFont(QtGui.QFont("Monospace", 13, QtGui.QFont.Bold))
        p.drawText(lcd.adjusted(10, 0, 0, 0), QtCore.Qt.AlignVCenter,
                   f"{bar:>3} {beat_in_bar} 1 1")
        p.setFont(QtGui.QFont("Monospace", 8))
        p.drawText(lcd.adjusted(168, 0, 0, 0), QtCore.Qt.AlignVCenter, "120.00\n4/4")

        p.setPen(QtGui.QColor(t["text_dim"]))
        p.setFont(QtGui.QFont("Sans", 8))
        p.drawText(502, 32, "C  colour take     T  chrome     Space  play     Q  quit")
        p.end()


class EditorPane(QtWidgets.QWidget):
    """The bottom editor pane -- a piano roll, sketched only."""

    def __init__(self, theme, take):
        super().__init__()
        self.theme, self.take = theme, take
        self.setMinimumHeight(150)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        t = self.theme
        p.fillRect(self.rect(), QtGui.QColor(t["lane_b"]))
        key_w, row_h = 54, 13
        for i in range(24):
            pc = (60 + 23 - i) % 12
            black = pc in (1, 3, 6, 8, 10)
            y = i * row_h
            p.fillRect(0, y, key_w, row_h - 1,
                       QtGui.QColor("#1a1c20") if black else QtGui.QColor("#dfe2e6"))
            p.fillRect(key_w, y, self.width(), row_h - 1,
                       QtGui.QColor(t["lane_a"] if black else t["lane_b"]))
            if not black:
                p.setPen(QtGui.QColor("#3a3f46"))
                p.setFont(QtGui.QFont("Sans", 6))
                p.drawText(4, y + row_h - 4, NOTE_NAMES_FIFTHS[fifths_index(pc)])
        for beat, pc in [(0, 0), (2, 4), (4, 7), (6, 11), (8, 7), (10, 4), (12, 0)]:
            row = 23 - ((pc - 60) % 12) - 6
            x = key_w + 10 + beat * 30
            colour = qcolor(pc, 0.6) if self.take == NOTE_COLOUR else QtGui.QColor(t["accent"])
            p.fillRect(x, row * row_h + 1, 56, row_h - 3, colour)
        p.end()


class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme, self.take = GRAPHITE, CLIP_COLOUR
        self.beat, self.running = 0.0, True
        self.setWindowTitle("VisualNote Studio — skeleton (prototype)")
        self.resize(1280, 760)
        self._build()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def _build(self):
        t = self.theme
        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.transport = Transport(t)
        outer.addWidget(self.transport)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        top = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(top)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        self.ruler = Ruler(t)
        self.headers = TrackHeaders(t, self.take)
        self.scene = ArrangeScene(t, self.take)
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(t["window"])))
        self.view.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view.horizontalScrollBar().valueChanged.connect(self._sync_ruler)
        self.view.horizontalScrollBar().setValue(0)

        self.corner = QtWidgets.QWidget()
        self.corner.setFixedSize(HEADER_W, RULER_H)
        self.corner.setStyleSheet(f"background:{t['chrome']};")
        grid.addWidget(self.corner, 0, 0)
        grid.addWidget(self.ruler, 0, 1)
        grid.addWidget(self.headers, 1, 0)
        grid.addWidget(self.view, 1, 1)
        split.addWidget(top)

        self.editor = EditorPane(t, self.take)
        split.addWidget(self.editor)
        split.setSizes([520, 190])
        outer.addWidget(split)
        self.setCentralWidget(central)

        self.dock_bodies = []
        for title, area in (("Inspector", QtCore.Qt.LeftDockWidgetArea),
                            ("Mixer", QtCore.Qt.RightDockWidgetArea)):
            dock = QtWidgets.QDockWidget(title, self)
            body = QtWidgets.QLabel(f"  {title}\n  (empty -- skeleton only)")
            body.setAlignment(QtCore.Qt.AlignTop)
            body.setStyleSheet(f"color:{t['text_dim']}; background:{t['chrome']}; padding:8px;")
            body.setFixedWidth(132)
            self.dock_bodies.append(body)
            dock.setWidget(body)
            dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
            self.addDockWidget(area, dock)

        self.setStyleSheet(
            f"QMainWindow{{background:{t['window']};}}"
            f"QDockWidget{{color:{t['text']}; font-size:10px;}}"
            f"QDockWidget::title{{background:{t['chrome_hi']}; padding:4px;}}"
        )

    def _sync_ruler(self, value):
        self.ruler.offset = value
        self.ruler.update()

    def _tick(self):
        if not self.running:
            return
        # A prototype timer. The real playhead reads the audio callback's own
        # sample clock through a lock-free snapshot (map #145) -- a Qt timer
        # would drift against the audio it is supposed to be pointing at.
        self.beat = (self.beat + 0.033) % (BARS * BEATS_PER_BAR)
        self.scene.move_playhead(self.beat)
        self.transport.beat = self.beat
        self.transport.update()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key_Q, QtCore.Qt.Key_Escape):
            self.close()
        elif key == QtCore.Qt.Key_C:
            self.take = NOTE_COLOUR if self.take == CLIP_COLOUR else CLIP_COLOUR
            self._reload()
        elif key == QtCore.Qt.Key_T:
            self.theme = SILVER if self.theme is GRAPHITE else GRAPHITE
            self._reload()
        elif key == QtCore.Qt.Key_Space:
            self.running = not self.running

    def _reload(self):
        """Re-skin in place rather than rebuilding the window.

        Calling `_build()` a second time left the ruler connected to the old
        (now deleted) scroll bar and crashed. Nothing about a colour or chrome
        change needs new widgets anyway -- only new values in the ones that
        are already there.
        """
        t = self.theme
        for widget in (self.ruler, self.headers, self.transport, self.editor):
            widget.theme = t
        self.headers.take = self.take
        self.editor.take = self.take
        self.scene.theme, self.scene.take = t, self.take
        self.scene.rebuild()
        self.scene.move_playhead(self.beat)
        self.view.horizontalScrollBar().setValue(0)
        self.view.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(t["window"])))
        self.corner.setStyleSheet(f"background:{t['chrome']};")
        for body in self.dock_bodies:
            body.setStyleSheet(
                f"color:{t['text_dim']}; background:{t['chrome']}; padding:8px;")
        self.setStyleSheet(
            f"QMainWindow{{background:{t['window']};}}"
            f"QDockWidget{{color:{t['text']}; font-size:10px;}}"
            f"QDockWidget::title{{background:{t['chrome_hi']}; padding:4px;}}"
        )
        for widget in (self.ruler, self.headers, self.transport, self.editor):
            widget.update()
        print(f"[take={self.take}  chrome={self.theme['name']}]", flush=True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = Window()
    if "--shot" in sys.argv:
        index = sys.argv.index("--shot")
        out = sys.argv[index + 1] if len(sys.argv) > index + 1 else "skeleton.png"
        takes = [(CLIP_COLOUR, GRAPHITE), (NOTE_COLOUR, GRAPHITE),
                 (CLIP_COLOUR, SILVER), (NOTE_COLOUR, SILVER)]
        window.timer.stop()
        window.show()
        # resize() only takes effect after show() under Wayland, and the view
        # otherwise opens scrolled to the middle of the scene -- past every clip.
        for _ in range(5):
            app.processEvents()
        window.resize(1400, 820)
        for _ in range(10):
            app.processEvents()
        for take, theme in takes:
            shot = window
            shot.take, shot.theme = take, theme
            shot._reload()
            shot.resize(1400, 820)   # re-applied: restyling relayouts the docks
            shot.beat = 9.0
            shot.scene.move_playhead(9.0)
            shot.transport.beat = 9.0
            for _ in range(40):
                app.processEvents()
            path = out.replace(".png", f"-{take}-{theme['name']}.png")
            shot.grab().save(path)
            print("wrote", path)
        sys.stdout.flush()
        os._exit(0)  # Qt's offscreen platform segfaults on teardown; files are written.
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
