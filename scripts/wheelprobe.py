"""Fog 1 of #192: what does a two-finger trackpad swipe actually deliver?

Everything in the two-pane swipe design rests on one assumption -- that a
two-finger scroll reaches Qt as a `QWheelEvent` carrying `pixelDelta` and a
begin/update/end `phase`, which is what makes 1:1 follow-the-finger and a
velocity-carried settle possible. XWayland flattens the phase away and the
gesture degrades to a janky snap.

Both ends of the chain check out statically (measured 2026-09-12):

* the session is native Wayland and the app's own `build_app()` reports
  `platformName() == "wayland"`, not `xcb`;
* Hyprland 0.56.2 sends `axis_source`, `axis_value120` and
  `axis_relative_direction`;
* Qt 6.11's Wayland client handles all three, plus
  `zwp_pointer_gesture_swipe_v1`.

What no amount of reading settles is whether the events *arrive shaped the way
the design needs*. That takes a real touchpad and real fingers, so this window
prints what it gets:

    .venv/bin/python scripts/wheelprobe.py

Two-finger swipe across it, horizontally and vertically, fast and slow, and
read the phases. `ScrollBegin ... ScrollUpdate ... ScrollEnd` with non-zero
`pixelDelta` is the answer the design assumes. `NoScrollPhase` with only
`angleDelta` in 120-unit steps is the degraded path, and #192's Fog 5
(a keybinding fallback) becomes live.

Throwaway. Delete it once Fog 1 is recorded in the ticket.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "src"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402


class Probe(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("wheel probe -- two-finger swipe here")
        self.lines = []
        self.label = QtWidgets.QLabel("swipe with two fingers…", self)
        self.label.setWordWrap(True)
        self.label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.label)
        self.resize(760, 520)
        self.grabGesture(QtCore.Qt.PanGesture)

    def log(self, text):
        print(text, flush=True)
        self.lines.append(text)
        del self.lines[:-24]
        self.label.setText("\n".join(self.lines))

    def wheelEvent(self, event):
        self.log("wheel  phase=%-14s pixelDelta=%-14s angleDelta=%-14s "
                 "inverted=%s src=%s"
                 % (event.phase().name.decode()
                    if isinstance(event.phase().name, bytes)
                    else str(event.phase()).rsplit(".", 1)[-1],
                    "%d,%d" % (event.pixelDelta().x(), event.pixelDelta().y()),
                    "%d,%d" % (event.angleDelta().x(), event.angleDelta().y()),
                    event.isInverted(),
                    str(event.source()).rsplit(".", 1)[-1]))
        event.accept()

    def event(self, event):
        if isinstance(event, QtGui.QNativeGestureEvent):
            self.log("native %s value=%.3f delta=%s"
                     % (str(event.gestureType()).rsplit(".", 1)[-1],
                        event.value(),
                        "%.1f,%.1f" % (event.delta().x(), event.delta().y())))
        return super().event(event)


def main():
    from notecolor.gui.app import build_app

    app = build_app()
    probe = Probe()
    probe.show()
    print("platformName: %s" % app.platformName(), flush=True)
    print("(xcb here means XWayland, and Fog 1 is answered badly)", flush=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
