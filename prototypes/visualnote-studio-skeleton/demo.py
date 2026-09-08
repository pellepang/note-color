"""VisualNote Studio skeleton -- arrange window, second pass.

Wayfinder map #145, ticket #152. Throwaway; nothing here is meant to be kept.

The first pass was rejected for looking like generic modern software. This one
follows a stated visual language, in `notecolor/gui/theme.py`:

- **Saturation means pitch, and nothing else.** Every surface, label, rule and
  button is muted ink. The only vivid things on screen are notes, carrying this
  app's own circle-of-fifths hue -- so a C is the colour a C is in `tab`, in an
  exported score and on the synth keyboard. Colour is information, not paint.
- **Kanagawa** ink-wash palette, **JetBrains Mono Nerd Font** everywhere
  (labels included -- a DAW is a grid of numbers, and this project's other
  front-end is a terminal; they should look like one program).
- **Translucent**, so the compositor shows through. Square corners, hairlines,
  no gradients, no shadows.

That rule also answers the question the first pass was asking. Clip-level
colour is out: a clip is not a pitch, so it may not be vivid. Colour lives on
the notes inside, which means the arrangement shows you *harmony* rather than
track identity.

Keys:  Space  run/stop the playhead      B  toggle the note-colour layer
       Q / Esc  quit

Run:  .venv/bin/python prototypes/visualnote-studio-skeleton/demo.py
      ... --shot out.png   renders offscreen and exits
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
    NOTE_NAMES_FIFTHS, fifths_index, hsl_to_rgb255, hue_for_step,
)
from notecolor.gui import theme  # noqa: E402

BARS, BEATS_PER_BAR, PX_PER_BEAT = 32, 4, 34
LANE_H, HEADER_W, RULER_H = 54, 196, 22


def pitch_colour(pitch_class, lightness=0.66, alpha=255):
    """The one place a saturated colour is allowed, and it always means pitch.

    Lighter and a touch more saturated than the first pass. Copper's ground is
    warm brown, so a note in the red-orange third of the fifths wheel sank into
    it -- the bass part became almost unreadable. Pitch has to stay the vivid
    layer for the palette rule to mean anything, so the notes are lifted rather
    than the ground being cooled.
    """
    rgb = hsl_to_rgb255(hue_for_step(fifths_index(pitch_class)), 0.62, lightness)
    return QtGui.QColor(*rgb, alpha)


PARTS = [
    ("VOX",   7, [(0, 7), (2, 11), (4, 2), (6, 7), (8, 4), (10, 9)]),
    ("GTR",   4, [(0, 4), (1, 9), (3, 0), (5, 4), (7, 11), (9, 2), (11, 4)]),
    ("BASS",  0, [(0, 0), (2, 0), (4, 7), (6, 5), (8, 0), (10, 5)]),
    ("KEYS",  9, [(0, 9), (2, 4), (4, 0), (6, 9), (8, 2), (10, 7)]),
    ("DRUMS", None, [(i, None) for i in range(12)]),
]
CLIPS = [(0, 0, 8), (0, 12, 12), (1, 4, 12), (1, 20, 8),
         (2, 0, 28), (3, 8, 16), (4, 0, 32)]


class ArrangeScene(QtWidgets.QGraphicsScene):
    """The arrange canvas (QGraphicsView per ticket #147)."""

    def __init__(self):
        super().__init__()
        self.show_notes = True
        self.width_px = BARS * BEATS_PER_BAR * PX_PER_BEAT
        self.height_px = len(PARTS) * LANE_H
        self.setSceneRect(0, 0, self.width_px, self.height_px)
        self.playhead = None
        self.rebuild()

    def _line(self, x1, y1, x2, y2, colour, width=1):
        pen = QtGui.QPen(colour)
        pen.setWidth(width)
        pen.setCosmetic(True)
        return self.addLine(x1, y1, x2, y2, pen)

    def rebuild(self):
        self.clear()
        for row in range(len(PARTS)):
            self.addRect(0, row * LANE_H, self.width_px, LANE_H,
                         QtGui.QPen(QtCore.Qt.NoPen),
                         QtGui.QBrush(theme.LANE if row % 2 == 0 else theme.LANE_ALT))
            self._line(0, (row + 1) * LANE_H, self.width_px, (row + 1) * LANE_H, theme.RULE)
        for beat in range(BARS * BEATS_PER_BAR + 1):
            x = beat * PX_PER_BEAT
            on_bar = beat % BEATS_PER_BAR == 0
            self._line(x, 0, x, self.height_px,
                       theme.RULE_STRONG if on_bar else theme.RULE)
        for row, start, length in CLIPS:
            self._clip(row, start, length)
        self.playhead = self._line(0, 0, 0, self.height_px, theme.PLAYHEAD)
        self.playhead.setZValue(100)

    def _clip(self, row, start_beat, length_beats):
        name, pitch_class, notes = PARTS[row]
        x, y = start_beat * PX_PER_BEAT, row * LANE_H + 4
        w, h = length_beats * PX_PER_BEAT - 1, LANE_H - 9

        # The clip body is muted ink. A clip is not a pitch, so it is not vivid.
        rect = QtWidgets.QGraphicsRectItem(x, y, w, h)
        rect.setBrush(QtGui.QBrush(theme.CLIP_BODY))
        rect.setPen(QtGui.QPen(theme.CLIP_EDGE, 1))
        self.addItem(rect)
        head = QtWidgets.QGraphicsRectItem(x, y, w, 12)
        head.setBrush(QtGui.QBrush(theme.CLIP_HEAD))
        head.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        self.addItem(head)
        label = self.addText(name.lower(), theme.font(7))
        label.setDefaultTextColor(theme.TEXT_DIM)
        label.setPos(x + 2, y - 3)

        if not self.show_notes:
            return
        top, span = y + 15, h - 19
        for beat_off, note_pc in notes:
            if beat_off >= length_beats:
                continue
            nx, nw = x + beat_off * PX_PER_BEAT + 2, PX_PER_BEAT - 6
            if note_pc is None:
                # Percussion has no pitch to be honest about, so it gets no
                # hue -- the same posture the synth tool's pads take.
                ny, nh, colour = top + span * 0.55, 2, theme.TEXT_FAINT
            else:
                ny = top + span * (1.0 - fifths_index(note_pc) / 12.0) - 2
                nh, colour = 3, pitch_colour(note_pc)
            bar = QtWidgets.QGraphicsRectItem(nx, ny, nw, nh)
            bar.setBrush(QtGui.QBrush(colour))
            bar.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            self.addItem(bar)

    def move_playhead(self, beat):
        x = beat * PX_PER_BEAT
        self.playhead.setLine(x, 0, x, self.height_px)


class Ruler(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.offset = 0
        self.setFixedHeight(RULER_H)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME_DEEP)
        p.setFont(theme.font(7))
        for bar in range(BARS + 1):
            x = bar * BEATS_PER_BAR * PX_PER_BEAT - self.offset
            if not -40 < x < self.width() + 40:
                continue
            p.setPen(theme.RULE_STRONG)
            p.drawLine(x, RULER_H - 6, x, RULER_H)
            p.setPen(theme.TEXT_DIM)
            p.drawText(x + 3, RULER_H - 9, f"{bar + 1}")
        p.setPen(theme.RULE)
        p.drawLine(0, RULER_H - 1, self.width(), RULER_H - 1)
        p.end()


class TrackHeaders(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(HEADER_W)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        for row, (name, pitch_class, _notes) in enumerate(PARTS):
            y = row * LANE_H
            if row % 2:
                p.fillRect(0, y, HEADER_W, LANE_H, theme.CHROME_DEEP)
            p.setPen(theme.RULE)
            p.drawLine(0, y + LANE_H - 1, HEADER_W, y + LANE_H - 1)

            # A 2px pitch tick, not a colour block: it refers to the note
            # system, so it is allowed a hue -- but it is not the loudest
            # thing in the row.
            if pitch_class is not None:
                p.fillRect(0, y + 6, 2, LANE_H - 13, pitch_colour(pitch_class))

            p.setPen(theme.TEXT)
            p.setFont(theme.font(9, bold=True))
            p.drawText(12, y + 17, name)
            p.setPen(theme.TEXT_FAINT)
            p.setFont(theme.font(7))
            p.drawText(HEADER_W - 46, y + 17,
                       f"{'note' if pitch_class is not None else 'perc'}")
            p.setFont(theme.font(7))
            for i, (letter, on) in enumerate((("m", False), ("s", False), ("r", row == 0))):
                bx = 12 + i * 22
                box = QtCore.QRect(bx, y + 24, 18, 14)
                p.setPen(theme.RULE_STRONG)
                p.drawRect(box)
                p.setPen(theme.ARMED if on else theme.TEXT_FAINT)
                p.drawText(box, QtCore.Qt.AlignCenter, letter)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(HEADER_W - 1, 0, HEADER_W - 1, self.height())
        p.end()


class Transport(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.beat, self.running = 0.0, True
        self.setFixedHeight(38)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        p.setFont(theme.font(10))
        for i, (glyph, armed) in enumerate((("|◀", False), ("■", False),
                                            ("▶", False), ("●", True), ("⟲", False))):
            box = QtCore.QRect(10 + i * 30, 8, 26, 22)
            p.setPen(theme.RULE_STRONG)
            p.drawRect(box)
            p.setPen(theme.ARMED if armed else theme.TEXT_DIM)
            p.drawText(box, QtCore.Qt.AlignCenter, glyph)

        bar = int(self.beat // BEATS_PER_BAR) + 1
        beat_in_bar = int(self.beat % BEATS_PER_BAR) + 1
        tick = int((self.beat % 1.0) * 960)
        readouts = [("position", f"{bar:03d}.{beat_in_bar}.{tick:03d}"),
                    ("tempo", "120.00"), ("sig", "4/4")]
        x = 176
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
        p.drawText(x, 24, "space  play      b  notes      q  quit")
        p.end()


class EditorPane(QtWidgets.QWidget):
    """Bottom editor pane -- piano roll, sketched."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CANVAS)
        p.setPen(theme.RULE_STRONG)
        p.drawLine(0, 0, self.width(), 0)
        key_w, row_h = 46, 13
        p.setFont(theme.font(6))
        for i in range(22):
            pc = (71 - i) % 12
            black = pc in (1, 3, 6, 8, 10)
            y = 4 + i * row_h
            p.fillRect(0, y, key_w, row_h - 1,
                       theme.CHROME_DEEP if black else theme.CHROME)
            p.fillRect(key_w, y, self.width(), row_h - 1,
                       theme.LANE_ALT if black else theme.LANE)
            p.setPen(theme.RULE)
            p.drawLine(key_w, y + row_h - 1, self.width(), y + row_h - 1)
            # The key letter is the one label allowed a hue: it names a pitch.
            p.setPen(pitch_colour(pc, 0.5) if not black else theme.TEXT_FAINT)
            p.drawText(5, y + row_h - 3, NOTE_NAMES_FIFTHS[fifths_index(pc)])
        p.setPen(theme.RULE_STRONG)
        p.drawLine(key_w, 0, key_w, self.height())
        for beat, pc in [(0, 0), (2, 4), (4, 7), (6, 11), (8, 7), (10, 4), (12, 0), (14, 5)]:
            row = (71 - (60 + pc)) % 12 + 5
            x = key_w + 12 + beat * 30
            p.fillRect(x, 4 + row * row_h + 2, 54, row_h - 5, pitch_colour(pc))
        p.end()


class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.beat, self.running = 0.0, True
        self.setWindowTitle("visualnote studio")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.resize(1280, 720)
        self._build()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def _build(self):
        central = QtWidgets.QWidget()
        central.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.transport = Transport()
        outer.addWidget(self.transport)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        top = QtWidgets.QWidget()
        top.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        grid = QtWidgets.QGridLayout(top)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        self.ruler = Ruler()
        self.headers = TrackHeaders()
        self.scene = ArrangeScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view.setBackgroundBrush(QtGui.QBrush(theme.CANVAS))
        self.view.setStyleSheet("background: transparent; border: 0;")
        self.view.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view.horizontalScrollBar().valueChanged.connect(self._sync_ruler)
        self.view.horizontalScrollBar().setValue(0)

        self.corner = QtWidgets.QWidget()
        self.corner.setFixedSize(HEADER_W, RULER_H)
        grid.addWidget(self.corner, 0, 0)
        grid.addWidget(self.ruler, 0, 1)
        grid.addWidget(self.headers, 1, 0)
        grid.addWidget(self.view, 1, 1)
        split.addWidget(top)

        self.editor = EditorPane()
        split.addWidget(self.editor)
        split.setSizes([460, 200])
        outer.addWidget(split)
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

    def _tick(self):
        if not self.running:
            return
        # A prototype timer. The real playhead reads the audio callback's own
        # sample clock through `audio.transport`'s snapshot -- a Qt timer would
        # drift against the audio it is meant to be pointing at.
        self.beat = (self.beat + 0.033) % (BARS * BEATS_PER_BAR)
        self.scene.move_playhead(self.beat)
        self.transport.beat = self.beat
        self.transport.update()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key_Q, QtCore.Qt.Key_Escape):
            self.close()
        elif key == QtCore.Qt.Key_Space:
            self.running = not self.running
        elif key == QtCore.Qt.Key_B:
            self.scene.show_notes = not self.scene.show_notes
            self.scene.rebuild()
            self.scene.move_playhead(self.beat)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("visualnote studio")
    app.setFont(theme.font(9))
    window = Window()
    if "--shot" in sys.argv:
        index = sys.argv.index("--shot")
        out = sys.argv[index + 1] if len(sys.argv) > index + 1 else "skeleton.png"
        # Hyprland tiles, so a shown window is whatever size the compositor
        # decides and resize() is ignored. Render the widget offscreen at a
        # fixed size instead of grabbing a live window -- Qt renders hidden
        # widgets fine, and the result is reproducible.
        window.timer.stop()
        window.resize(1400, 800)
        window.ensurePolished()
        for _ in range(10):
            app.processEvents()
        window.view.horizontalScrollBar().setValue(0)
        window.beat = 9.0
        window.scene.move_playhead(9.0)
        window.transport.beat = 9.0
        for _ in range(10):
            app.processEvents()
        # The window is translucent, so a bare grab has transparent pixels
        # that read as white in a PNG. Composite over a dark ground so the
        # screenshot shows roughly what the compositor will.
        shot = QtGui.QPixmap(window.size())
        shot.fill(QtGui.QColor("#0E0A09"))
        painter = QtGui.QPainter(shot)
        window.render(painter, QtCore.QPoint(0, 0))
        painter.end()
        shot.save(out)
        print("wrote", out)
        sys.stdout.flush()
        os._exit(0)   # Qt's offscreen platform segfaults on teardown
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
