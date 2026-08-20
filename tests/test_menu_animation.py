"""Tests for issue #51's animated-menu pure logic: menu_animation.py's
projection/shading helpers, the auto-detect heuristic's decision function,
and render_frame() itself as a smoke/shape check (not a timing benchmark --
see the issue for the actual before/after numbers). Per this repo's test
convention, the interactive per-frame loop and any real terminal I/O are
smoke-tested manually, not driven by pytest."""

import config
from menu_animation import (
    band_color,
    render_row,
    render_frame,
    _decide_perf_mode,
    detect_perf_mode,
)


# --- band_color --------------------------------------------------------

def test_band_color_returns_valid_rgb_for_every_band():
    for band in range(12):
        r, g, b = band_color(band)
        assert all(0 <= c <= 255 for c in (r, g, b))


def test_band_color_differs_between_distinct_bands():
    # Not every pair needs to differ, but the full set of 12 shouldn't
    # collapse onto one or two colors.
    colors = {band_color(b) for b in range(12)}
    assert len(colors) > 1


# --- render_row (RLE) -------------------------------------------------------

def test_render_row_no_escape_for_all_uncolored_cells():
    row = render_row([(None, "x"), (None, "y")], 2)
    assert row == "xy"
    assert "\033" not in row


def test_render_row_collapses_a_run_of_same_color_into_one_escape():
    code = "\033[38;2;1;2;3m"
    row = render_row([(code, "a"), (code, "b"), (code, "c")], 3)
    assert row.count(code) == 1
    assert row == code + "abc" + "\033[0m"


def test_render_row_starts_a_new_run_on_color_change():
    code_a, code_b = "\033[38;2;1;1;1m", "\033[38;2;2;2;2m"
    row = render_row([(code_a, "a"), (code_b, "b")], 2)
    assert row.count("\033[0m") == 2  # each run closed separately


# --- render_frame (shape/smoke) --------------------------------------------

def test_render_frame_full_mode_returns_one_string_per_row():
    lines = render_frame(40, 20, A=1.0, B=0.5, perf=False)
    assert len(lines) == 20


def test_render_frame_perf_mode_returns_one_string_per_row():
    lines = render_frame(40, 20, A=1.0, B=0.5, perf=True)
    assert len(lines) == 20


def test_render_frame_handles_tiny_terminal_without_crashing():
    lines = render_frame(1, 1, A=1.0, B=0.5, perf=False)
    assert len(lines) == 1
    lines = render_frame(1, 1, A=1.0, B=0.5, perf=True)
    assert len(lines) == 1


def test_render_frame_full_mode_produces_some_colored_output():
    # A blank frame (no donut cells at all) would mean the projection
    # math is broken -- at a reasonably sized terminal, some cells should
    # carry a color escape code.
    lines = render_frame(60, 30, A=1.0, B=0.5, perf=False)
    assert any("\033[38;2;" in line for line in lines)


def test_render_frame_perf_mode_never_emits_shading_variety():
    # Perf mode drops shading levels -- every filled cell is the same '@'
    # glyph, no '.', '*', '#'.
    lines = render_frame(60, 30, A=1.0, B=0.5, perf=True)
    joined = "".join(lines)
    assert "." not in joined and "*" not in joined and "#" not in joined


def _visible_width(line):
    """Strips ANSI SGR escapes to get the actual printed column count."""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", line))


def test_render_frame_perf_mode_matches_requested_width_when_odd():
    # Perf mode's block_w=2 raster cells don't evenly divide an odd
    # cols_term (e.g. an 80-column terminal's 33-column leftover donut
    # pane, per menu_display._layout) -- every row must still print
    # exactly cols_term columns, not floor(cols_term / block_w) * block_w,
    # or the donut pane falls short of the space menu_display allocated it
    # and leftover/stale terminal content can show through the gap since
    # donut rows aren't \033[K-cleared per cell.
    for cols_term in (1, 31, 33, 47, 79):
        lines = render_frame(cols_term, 5, A=1.0, B=0.5, perf=True)
        for line in lines:
            assert _visible_width(line) == cols_term


def test_render_frame_full_mode_matches_requested_width_when_odd():
    # Full mode's block_w=1 always divides evenly, but assert the
    # contract explicitly so a future full-mode block_w change is caught
    # too.
    for cols_term in (1, 31, 33, 47, 79):
        lines = render_frame(cols_term, 5, A=1.0, B=0.5, perf=False)
        for line in lines:
            assert _visible_width(line) == cols_term


# --- auto-detect heuristic's decision function ------------------------------

def test_decide_perf_mode_weak_cpu_skips_probe():
    perf, reason = _decide_perf_mode(cpu_count=1, probe_avg=None, cpu_floor=2, budget=0.033)
    assert perf is True
    assert "skipped probe" in reason


def test_decide_perf_mode_slow_probe_falls_back_to_perf():
    perf, reason = _decide_perf_mode(cpu_count=8, probe_avg=0.5, cpu_floor=2, budget=0.033)
    assert perf is True
    assert "probe avg" in reason


def test_decide_perf_mode_fast_probe_stays_full():
    perf, reason = _decide_perf_mode(cpu_count=8, probe_avg=0.005, cpu_floor=2, budget=0.033)
    assert perf is False
    assert "within budget" in reason


def test_decide_perf_mode_uses_configured_defaults_via_detect_perf_mode():
    # cpu_count forced low enough to trip the floor -- exercises
    # detect_perf_mode()'s own wiring of config.py's constants without
    # needing to time a real probe.
    perf, reason = detect_perf_mode(40, 20, cpu_count=1)
    assert perf is True
    assert f"<= {config.MENU_AUTODETECT_CPU_FLOOR}" in reason
