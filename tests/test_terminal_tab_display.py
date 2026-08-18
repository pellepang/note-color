import io
import re
import shutil
import sys

from terminal_tab_display import TabDisplay


def _render(monkeypatch, rows, cols=80, pushes=()):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (cols, rows))
    display = TabDisplay(fps=20)
    display._last_size = (cols, rows)  # skip the resize-clear noise for these probes
    for pitch_class, octave, label in pushes:
        display.push(pitch_class, octave, (200, 50, 50), label)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    display.render("status")
    return buf.getvalue()


def test_small_terminal_never_addresses_a_row_past_its_height(monkeypatch):
    for rows in (30, 22, 21, 15, 10, 8):
        out = _render(monkeypatch, rows, pushes=[(i % 12, 4, "X") for i in range(20)])
        max_row = max(int(m) for m in re.findall(r"\033\[(\d+);1H", out))
        assert max_row <= rows


def test_note_outside_shrunk_range_is_dropped_not_misplaced(monkeypatch):
    # A 24-row terminal shrinks the staff enough that B5 (the top of the
    # app's range) falls outside it -- it must not render at all, since
    # drawing it clamped to the boundary row would show it at the wrong
    # staff position.
    out = _render(monkeypatch, rows=24, pushes=[(11, 5, "B5")])
    assert "B5" not in out


def test_note_inside_range_still_renders(monkeypatch):
    out = _render(monkeypatch, rows=30, pushes=[(9, 4, "A4")])
    assert "A4" in out
