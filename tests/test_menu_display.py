"""Tests for issue #51's menu_display.py additions: the donut/text-pane
column split (_layout), the config/CLI perf-mode override resolution
(_resolve_perf_mode), and the text-pane content builder (_text_lines).
Per this repo's convention, MenuDisplay's selection-state API is covered
in test_shell.py (unchanged by #51) and the interactive render() loop
itself is smoke-tested manually, not here."""

import re

import pytest

import config
import menu_display
from menu_display import MENU_ITEMS, _donation_line, _layout, _resolve_perf_mode, _text_lines

_OSC8_RE = re.compile(r"\x1b\]8;;.*?\x1b\\")


def _visible_len(line):
    """Strips OSC 8 hyperlink escape bytes (zero-width on screen) so the
    remainder measures what actually occupies terminal columns -- the same
    distinction _donation_line's own centering math has to make."""
    return len(_OSC8_RE.sub("", line))


@pytest.fixture(autouse=True)
def clear_perf_probe_cache():
    """_resolve_perf_mode's per-(cols, rows) auto-probe cache is a plain
    module-level dict shared across the whole test session -- without
    clearing it, whichever test happens to run first for a given size
    would silently poison every later test at that same size, since the
    cache doesn't know it's being fed stubs instead of the real probe."""
    menu_display._perf_probe_cache.clear()
    yield
    menu_display._perf_probe_cache.clear()


# --- _layout -----------------------------------------------------------

def test_layout_gives_donut_room_on_a_normal_desktop_terminal():
    donut_cols, text_col, text_width = _layout(120, 40)
    assert donut_cols >= config.MENU_MIN_DONUT_COLS
    assert text_width == config.MENU_TEXT_PANE_WIDTH
    assert text_col == donut_cols + 2


def test_layout_drops_donut_on_a_narrow_terminal():
    donut_cols, text_col, text_width = _layout(40, 24)
    assert donut_cols == 0
    assert text_col >= 1


def test_layout_text_width_never_exceeds_terminal_width():
    donut_cols, text_col, text_width = _layout(10, 24)
    assert text_width <= 10
    assert text_col >= 1


def test_layout_donut_and_text_pane_never_overlap():
    for cols in (30, 46, 60, 80, 120, 200):
        donut_cols, text_col, text_width = _layout(cols, 40)
        if donut_cols > 0:
            assert text_col > donut_cols
            assert text_col + text_width - 1 <= cols + 1  # allow OSC8-inflated last col slack


# --- _donation_line -----------------------------------------------------

def test_donation_line_never_exceeds_the_pane_width_it_was_given():
    # "by Pelle -- please support on Patreon: https://patreon.com/notecolor"
    # (~70 visible chars) is longer than config.MENU_TEXT_PANE_WIDTH (46) --
    # _donation_line must truncate to fit rather than overflow into the
    # donut pane (or off the terminal edge in the narrow-fallback layout),
    # which corrupts the whole screen once the terminal auto-wraps it.
    for width in (10, 30, 46, config.MENU_TEXT_PANE_WIDTH, 80, 200):
        assert _visible_len(_donation_line(width)) <= width


def test_donation_line_at_the_actual_default_pane_width_fits():
    # Regression check pinned to this app's real, fixed text-pane width --
    # this is the width every real menu render actually uses.
    line = _donation_line(config.MENU_TEXT_PANE_WIDTH)
    assert _visible_len(line) <= config.MENU_TEXT_PANE_WIDTH


# --- _resolve_perf_mode --------------------------------------------------

def test_resolve_perf_mode_explicit_override_wins(monkeypatch):
    # Should never even consult the config store or the real probe when
    # an explicit override is given.
    monkeypatch.setattr(menu_display.store, "preference", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    perf, reason = _resolve_perf_mode(60, 30, override="perf")
    assert perf is True
    perf, reason = _resolve_perf_mode(60, 30, override="full")
    assert perf is False


def test_resolve_perf_mode_falls_back_to_config_store(monkeypatch):
    monkeypatch.setattr(menu_display.store, "preference", lambda name, default: "perf")
    perf, reason = _resolve_perf_mode(60, 30, override=None)
    assert perf is True
    assert "menu_perf_mode=perf" in reason


def test_resolve_perf_mode_auto_calls_real_autodetect(monkeypatch):
    monkeypatch.setattr(menu_display.store, "preference", lambda name, default: "auto")
    monkeypatch.setattr(menu_display.menu_animation, "detect_perf_mode", lambda cols, rows: (False, "stubbed"))
    perf, reason = _resolve_perf_mode(60, 30, override=None)
    assert perf is False
    assert reason == "stubbed"


def test_resolve_perf_mode_auto_probe_is_cached_per_size(monkeypatch):
    # shell.py builds a fresh MenuDisplay on every '|' back-to-menu round
    # trip -- without caching, the real probe (several actual frame
    # renders) would re-run every single time, working against the
    # "instant transition" back-to-menu is supposed to give.
    monkeypatch.setattr(menu_display.store, "preference", lambda name, default: "auto")
    calls = []

    def fake_probe(cols, rows):
        calls.append((cols, rows))
        return False, "stubbed"

    monkeypatch.setattr(menu_display.menu_animation, "detect_perf_mode", fake_probe)

    _resolve_perf_mode(60, 30, override=None)
    _resolve_perf_mode(60, 30, override=None)
    assert calls == [(60, 30)]  # second call at the same size hit the cache

    _resolve_perf_mode(80, 24, override=None)
    assert calls == [(60, 30), (80, 24)]  # a different size still probes fresh


def test_resolve_perf_mode_override_never_touches_the_cache(monkeypatch):
    monkeypatch.setattr(menu_display.store, "preference", lambda name, default: "auto")
    calls = []
    monkeypatch.setattr(menu_display.menu_animation, "detect_perf_mode",
                         lambda cols, rows: calls.append((cols, rows)) or (False, "stubbed"))

    _resolve_perf_mode(60, 30, override="full")  # bypasses the probe entirely
    _resolve_perf_mode(60, 30, override=None)     # auto at the same size -- must still probe for real
    assert calls == [(60, 30)]


# --- _text_lines -----------------------------------------------------------

def test_text_lines_includes_every_menu_item():
    lines = _text_lines(rows=40, text_width=46, selected=0, status="")
    joined = " ".join(lines.values())
    for _view, desc in MENU_ITEMS:
        assert desc in joined


def test_text_lines_highlights_only_the_selected_item():
    lines = _text_lines(rows=40, text_width=46, selected=2, status="")
    highlighted = [row for row, content in lines.items() if "\033[7m" in content]
    assert len(highlighted) == 1


def test_text_lines_includes_status_as_its_own_line():
    lines = _text_lines(rows=40, text_width=46, selected=0, status="sens=1.0")
    assert "sens=1.0" in lines.values()


def test_text_lines_rows_stay_within_terminal_height():
    rows = 24
    lines = _text_lines(rows=rows, text_width=46, selected=0, status="")
    assert max(lines.keys()) <= rows
    assert min(lines.keys()) >= 1
