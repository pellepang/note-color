#!/usr/bin/env python3
"""Screenshot a local HTML file — for the prototypes in `docs/prototypes/`.

The sibling of `uishot.py`, which shoots the real Qt app through a nested
headless compositor (decision 53). This one has a narrower job: the design
prototypes this project settles its UI with (decision 56 §9, decision 57) are
plain HTML, and there is no browser on the dev machine. Rendering them needs
QtWebEngine, which arrives with `PySide6-Addons`.

    python scripts/htmlshot.py docs/prototypes/210-patch-canvas.html out.png
    python scripts/htmlshot.py <file> <out.png> --width 1860 --height 1000 \
                               --settle 2.5

`--settle` matters for any prototype that animates: #210's cables run a
physics loop on `requestAnimationFrame` and need a moment to hang and stop
swinging before the frame is worth looking at.

This is a pre-handover self-check, never a verdict. #191 standing decision 8:
an agent does not declare a GUI result correct from its own screenshot.
"""

import argparse
import os
import pathlib
import sys

# These must be set before QtWebEngine initialises, and they are ASSIGNED,
# not `setdefault`-ed. The dev environment exports
# `QT_QPA_PLATFORM=wayland;xcb`, so a `setdefault` here is a silent no-op and
# the "headless" screenshot opens a real window that Hyprland tiles into the
# user's session — the exact failure decision 53 rejected for the Qt harness.
# Nothing in this script may ever touch the live session.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# The offscreen platform serves a virtual screen and clamps a top-level widget
# to it; without this a resize() to 1860px silently came back 666px wide.
os.environ["QT_QPA_OFFSCREEN_VIRTUAL_SCREEN"] = "4096x4096"
# No GPU session exists offscreen, and WebEngine otherwise waits on one it
# will never get.
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --disable-gpu-compositing --no-sandbox "
    "--disable-features=VizDisplayCompositor"
)
for _leak in ("WAYLAND_DISPLAY", "DISPLAY"):
    os.environ.pop(_leak, None)   # belt and braces: nothing to connect to

from PySide6 import QtCore, QtGui, QtWidgets          # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView   # noqa: E402


def shoot(path, out, width, height, settle_ms, full_page):
    app = QtWidgets.QApplication(sys.argv[:1])
    if QtGui.QGuiApplication.platformName() != "offscreen":
        raise SystemExit("htmlshot: refusing to run on platform "
                         f"'{QtGui.QGuiApplication.platformName()}' — this "
                         "script must never open a window in the live session")
    view = QWebEngineView()
    view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
    view.setFixedSize(width, height)

    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.ShowScrollBars, False)
    # Local prototypes pull JetBrains Mono from Google Fonts; without this a
    # file:// page is not allowed to reach it and silently falls back.
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)

    view.show()
    app.processEvents(QtCore.QEventLoop.AllEvents, 50)

    done = {"loaded": False, "ok": False}

    def on_load(ok):
        done["loaded"] = True
        done["ok"] = ok

    view.loadFinished.connect(on_load)
    view.load(QtCore.QUrl.fromLocalFile(str(pathlib.Path(path).resolve())))

    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while not done["loaded"] and deadline.elapsed() < 30_000:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)
    if not done["ok"]:
        raise SystemExit(f"htmlshot: failed to load {path}")

    if full_page:
        # Grow the view to the document's own height so nothing is cut off.
        got = {}
        view.page().runJavaScript(
            "Math.ceil(document.documentElement.scrollHeight)",
            lambda v: got.setdefault("h", v))
        spin = QtCore.QElapsedTimer(); spin.start()
        while "h" not in got and spin.elapsed() < 5_000:
            app.processEvents(QtCore.QEventLoop.AllEvents, 20)
        if got.get("h"):
            view.setFixedSize(width, min(int(got["h"]) + 8, 12_000))
            app.processEvents(QtCore.QEventLoop.AllEvents, 50)

    # Let webfonts land and any animation settle.
    spin = QtCore.QElapsedTimer()
    spin.start()
    while spin.elapsed() < settle_ms:
        app.processEvents(QtCore.QEventLoop.AllEvents, 30)

    pixmap = view.grab()
    if pixmap.isNull() or pixmap.size().isEmpty():
        raise SystemExit("htmlshot: grabbed an empty frame")
    image = pixmap.toImage()
    if image.allGray():
        print("htmlshot: warning — frame is uniform; WebEngine may not have "
              "painted", file=sys.stderr)
    if not image.save(str(out)):
        raise SystemExit(f"htmlshot: could not write {out}")
    print(f"{out}  {image.width()}x{image.height()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1860)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--settle", type=float, default=2.5,
                    help="seconds to let fonts load and animation settle")
    ap.add_argument("--viewport-only", action="store_true",
                    help="do not grow the view to the document height")
    args = ap.parse_args()
    shoot(args.html, args.out, args.width, args.height,
          int(args.settle * 1000), not args.viewport_only)


if __name__ == "__main__":
    main()
