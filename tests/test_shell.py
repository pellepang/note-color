"""Tests for issue #40's unified-shell pure logic: the new global terminal
key handlers and legend-line builder in main.py, MenuDisplay's selection
state in menu_display.py, shell.py's menu-key dispatch, and virtualnote.py's
CLI parsing. Per this repo's convention, only pure logic is unit-tested
here -- the threaded run_* loops and the interactive menu loop itself are
smoke-tested manually (see the issue), not driven by pytest."""

import argparse

import pytest

from main import _handle_back_to_menu_key, _handle_help_legend_key, _legend_line
from menu_display import MenuDisplay, TOOLS
from shell import _handle_menu_key
from virtualnote import build_parser


# --- main.py: global key handlers -----------------------------------------

def test_back_to_menu_key_fires_only_on_pipe():
    assert _handle_back_to_menu_key("|") is True
    assert _handle_back_to_menu_key("p") is False
    assert _handle_back_to_menu_key(None) is False
    assert _handle_back_to_menu_key("h") is False


def test_help_legend_key_toggles_case_insensitively():
    assert _handle_help_legend_key("h", True) is False
    assert _handle_help_legend_key("H", False) is True
    # any other key, or no key at all, leaves the state untouched
    assert _handle_help_legend_key("p", True) is True
    assert _handle_help_legend_key(None, False) is False


def test_legend_line_always_leads_with_global_keys():
    line = _legend_line(["m=source", "p=mode"])
    assert line.startswith("|=menu  h=legend")
    assert "m=source" in line
    assert "p=mode" in line


def test_legend_line_with_no_extra_hints():
    assert _legend_line([]) == "|=menu  h=legend"


# --- menu_display.py: selection state --------------------------------------

def test_menu_display_starts_at_first_tool():
    menu = MenuDisplay()
    assert menu.selected == 0
    assert menu.current_view() == TOOLS[0][0]


def test_menu_display_move_wraps_both_directions():
    menu = MenuDisplay()
    menu.move(-1)
    assert menu.selected == len(TOOLS) - 1  # wraps from first to last
    menu.move(1)
    assert menu.selected == 0
    for _ in range(len(TOOLS)):
        menu.move(1)
    assert menu.selected == 0  # a full lap returns to the start


def test_menu_display_move_to_out_of_range_is_ignored():
    menu = MenuDisplay()
    menu.move_to(2)
    assert menu.selected == 2
    menu.move_to(len(TOOLS) + 5)
    assert menu.selected == 2  # unchanged -- out of range
    menu.move_to(-1)
    assert menu.selected == 2  # unchanged -- out of range


# --- shell.py: menu key dispatch -------------------------------------------

def test_menu_key_arrows_move_without_selecting():
    menu = MenuDisplay()
    assert _handle_menu_key("DOWN", menu) is None
    assert menu.selected == 1
    assert _handle_menu_key("UP", menu) is None
    assert menu.selected == 0


def test_menu_key_enter_selects_current_row():
    menu = MenuDisplay()
    menu.move(2)
    assert _handle_menu_key("\r", menu) == menu.current_view()


def test_menu_key_digit_jumps_and_selects_in_one_key():
    menu = MenuDisplay()
    selection = _handle_menu_key("3", menu)
    assert menu.selected == 2
    assert selection == TOOLS[2][0]


def test_menu_key_digit_out_of_range_is_ignored():
    menu = MenuDisplay()
    result = _handle_menu_key(str(len(TOOLS) + 1), menu)
    assert result is None
    assert menu.selected == 0


def test_menu_key_zero_and_none_do_nothing():
    menu = MenuDisplay()
    assert _handle_menu_key("0", menu) is None
    assert _handle_menu_key(None, menu) is None
    assert menu.selected == 0


# --- virtualnote.py: CLI parsing -------------------------------------------

def test_bare_invocation_has_no_view():
    args = build_parser().parse_args([])
    assert args.view is None


def test_direct_view_flags_forward_correctly():
    args = build_parser().parse_args(["fill", "--color-scheme", "fifths", "--sensitivity", "2.0"])
    assert args.view == "fill"
    assert args.color_scheme == "fifths"
    assert args.sensitivity == 2.0


def test_circle_is_a_wheel_alias():
    args = build_parser().parse_args(["circle"])
    assert args.view == "circle"  # normalized to "wheel" downstream in virtualnote.main()


def test_tab_requires_scroll_positional():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["tab"])


def test_tab_accepts_scroll_and_dump_file():
    args = build_parser().parse_args(["tab", "onset", "--dump-file", "/tmp/x.txt"])
    assert args.view == "tab"
    assert args.scroll == "onset"
    assert args.dump_file == "/tmp/x.txt"


def test_gui_accepts_fullscreen_and_debug():
    args = build_parser().parse_args(["gui", "--fullscreen", "--debug"])
    assert args.view == "gui"
    assert args.fullscreen is True
    assert args.debug is True


def test_gui_defaults_are_off():
    args = build_parser().parse_args(["gui"])
    assert args.fullscreen is False
    assert args.debug is False


def test_invalid_view_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["not-a-real-view"])


def test_sensitivity_must_be_positive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fill", "--sensitivity", "-1"])
