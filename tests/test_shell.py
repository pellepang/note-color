"""Tests for issue #40's unified-shell pure logic: the new global terminal
key handlers and legend-line builder in main.py, MenuDisplay's selection
state in menu_display.py, shell.py's menu-key dispatch, and virtualnote.py's
CLI parsing. Per this repo's convention, only pure logic is unit-tested
here -- the threaded run_* loops and the interactive menu loop itself are
smoke-tested manually (see the issue), not driven by pytest."""

import argparse

import pytest

import config
from main import _handle_back_to_menu_key, _handle_help_legend_key, _legend_line, _parse_time_signature
from menu_display import MenuDisplay, MENU_ITEMS, TOOLS, osc8_link, _donation_line
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


def test_menu_items_includes_settings_and_credits_after_every_tool():
    # Settings and Credits are reachable from the menu at the same tier as
    # any tool (#43, #44) but neither is a run_session-launchable tool.
    assert MENU_ITEMS[:len(TOOLS)] == TOOLS
    assert MENU_ITEMS[len(TOOLS)][0] == "settings"
    assert MENU_ITEMS[len(TOOLS) + 1][0] == "credits"


def test_menu_display_move_wraps_both_directions():
    menu = MenuDisplay()
    menu.move(-1)
    assert menu.selected == len(MENU_ITEMS) - 1  # wraps from first to last
    menu.move(1)
    assert menu.selected == 0
    for _ in range(len(MENU_ITEMS)):
        menu.move(1)
    assert menu.selected == 0  # a full lap returns to the start


def test_menu_display_move_to_out_of_range_is_ignored():
    menu = MenuDisplay()
    menu.move_to(2)
    assert menu.selected == 2
    menu.move_to(len(MENU_ITEMS) + 5)
    assert menu.selected == 2  # unchanged -- out of range
    menu.move_to(-1)
    assert menu.selected == 2  # unchanged -- out of range


# --- menu_display.py: donation callout (issue #44) -------------------------

def test_osc8_link_wraps_text_with_escape_codes():
    link = osc8_link("click me", "https://example.com")
    assert link.startswith("\033]8;;https://example.com\033\\click me")
    assert link.endswith("\033]8;;\033\\")


def test_donation_line_names_author_and_platform():
    line = _donation_line(120)
    assert config.AUTHOR_NAME in line
    assert config.DONATION_PLATFORM in line
    assert config.DONATION_URL in line


def test_donation_line_never_negative_pads_on_narrow_terminals():
    # Shouldn't raise or produce a negative amount of leading whitespace
    # when the terminal is narrower than the visible text.
    line = _donation_line(1)
    assert config.DONATION_URL in line


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


def test_menu_key_digit_selects_settings_entry():
    menu = MenuDisplay()
    selection = _handle_menu_key(str(len(TOOLS) + 1), menu)
    assert selection == "settings"
    assert menu.selected == len(TOOLS)


def test_menu_key_digit_selects_credits_entry():
    menu = MenuDisplay()
    selection = _handle_menu_key(str(len(TOOLS) + 2), menu)
    assert selection == "credits"
    assert menu.selected == len(TOOLS) + 1


def test_menu_key_digit_out_of_range_is_ignored():
    menu = MenuDisplay()
    result = _handle_menu_key(str(len(MENU_ITEMS) + 1), menu)
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


def test_transcribe_requires_file_positional():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["transcribe"])


def test_transcribe_defaults():
    args = build_parser().parse_args(["transcribe", "somefile.wav"])
    assert args.view == "transcribe"
    assert args.file == "somefile.wav"
    assert args.time_signature == (4, 4)
    assert args.dump_file is None


def test_transcribe_accepts_time_signature():
    args = build_parser().parse_args(["transcribe", "somefile.wav", "--time-signature", "3/4"])
    assert args.time_signature == (3, 4)


def test_transcribe_rejects_malformed_time_signature():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["transcribe", "somefile.wav", "--time-signature", "nope"])


def test_tab_defaults_time_signature_to_four_four():
    args = build_parser().parse_args(["tab", "onset"])
    assert args.time_signature == (4, 4)


# --- main.py: _parse_time_signature -----------------------------------------

def test_parse_time_signature_valid():
    assert _parse_time_signature("4/4") == (4, 4)
    assert _parse_time_signature("3/4") == (3, 4)
    assert _parse_time_signature("7/8") == (7, 8)


@pytest.mark.parametrize("text", ["nope", "4", "4/4/4", "0/4", "4/0", "-3/4", "a/b"])
def test_parse_time_signature_rejects_bad_input(text):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_time_signature(text)
