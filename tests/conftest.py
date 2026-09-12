import os
import sys

# src layout (ticket #146): tests import through the installed package name,
# so `src` is what goes on the path -- not the repo root, which no longer
# holds any importable module. An editable install makes this redundant;
# it stays so `pytest tests/` works in a bare checkout too.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

# Qt tests run headless, always. Setting this per-module was order-dependent:
# whichever module imported PySide6 first decided the platform for the whole
# session, so a GUI test could silently run against the real display and, in
# one case, open a modal file dialog that never returned.
#
# Assigned, not `setdefault`. A developer running a Wayland desktop
# typically exports `QT_QPA_PLATFORM` in their own environment (on this
# project's own machine, `wayland;xcb`), and `setdefault` quietly defers to
# it -- so the suite opened real windows into their live session, where a
# tiling WM resized them at will. That is not just untidy: it made the
# splitter-drag tests flaky, because the window never kept the size the
# test asked for, and the failures looked like app bugs.
#
# `NOTECOLOR_TEST_QPA` is the deliberate escape hatch, for the rare case of
# wanting to watch a GUI test run for real.
os.environ["QT_QPA_PLATFORM"] = os.environ.get("NOTECOLOR_TEST_QPA", "offscreen")
