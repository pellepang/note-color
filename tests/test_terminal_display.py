import io
import shutil
import sys

import terminal_display


def _render(monkeypatch, rows, cols=80, legend="", bands=False):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (cols, rows))
    display = terminal_display.TerminalDisplay(fps=20)
    display._last_size = (cols, rows)  # skip the resize-clear noise for this probe
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    if bands:
        display.render_bands([(10, 20, 30), (40, 50, 60)], status="STATUS", legend=legend)
    else:
        display.render((10, 20, 30), status="STATUS", legend=legend)
    return buf.getvalue()


def _lines_after_cursor_home(out):
    # render()/render_bands() write a single leading "\033[H" then "\n"-join
    # every content line -- splitting on the first "\033[H" and counting "\n"
    # -joined segments gives exactly how many terminal rows the frame used.
    body = out.split("\033[H", 1)[1]
    return body.split("\n")


def test_render_never_writes_past_terminal_height(monkeypatch):
    # Regression for the off-by-one that made "\033[H" a joined list element:
    # that burned an extra row before the first block line, pushing the
    # status (and, with H's legend on, the legend) line one row past the
    # bottom -- forcing an unwanted scroll every frame that looked like the
    # legend drifting/duplicating on screen.
    for rows in (24, 10, 3):
        for legend in ("", "|=menu  h=legend"):
            out = _render(monkeypatch, rows, legend=legend)
            lines = _lines_after_cursor_home(out)
            assert len(lines) == rows


def test_render_bands_never_writes_past_terminal_height(monkeypatch):
    for rows in (24, 10, 3):
        for legend in ("", "|=menu  h=legend"):
            out = _render(monkeypatch, rows, legend=legend, bands=True)
            lines = _lines_after_cursor_home(out)
            assert len(lines) == rows


def test_legend_is_the_final_line_when_present(monkeypatch):
    out = _render(monkeypatch, rows=24, legend="LEGENDTEXT")
    lines = _lines_after_cursor_home(out)
    assert "LEGENDTEXT" in lines[-1]
    assert "STATUS" in lines[-2]


def test_status_is_the_final_line_when_no_legend(monkeypatch):
    out = _render(monkeypatch, rows=24, legend="")
    lines = _lines_after_cursor_home(out)
    assert "STATUS" in lines[-1]
