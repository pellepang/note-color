"""`theme.main_stylesheet()` -- the one place every widget's default colours
come from (see `gui/theme.py`'s own docstring: no widget names a raw hex
value). Regression coverage for the bug where new widget classes (the piano
roll panel's `QPushButton`s) fell back to Qt's default black text over this
app's transparent/dark backgrounds and were unreadable: the base `QWidget`
rule now sets a light default `color`, and `QPushButton` gets its own
explicit light-on-dark rule rather than inheriting an unreadable default.
"""

import re

from notecolor.gui import theme


def _rule_body(stylesheet, selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", stylesheet)
    assert match, f"no {selector!r} rule in the stylesheet"
    return match.group(1)


def test_the_base_widget_rule_sets_a_light_default_text_colour():
    body = _rule_body(theme.main_stylesheet(), "QMainWindow, QWidget")
    assert "color:" in body
    assert theme.rgba(theme.TEXT) in body


def test_push_buttons_get_light_text_over_a_dark_background():
    body = _rule_body(theme.main_stylesheet(), "QPushButton")
    assert theme.rgba(theme.TEXT) in body
    assert "background:" in body


def test_disabled_push_buttons_stay_readable_not_invisible():
    stylesheet = theme.main_stylesheet()
    body = _rule_body(stylesheet, "QPushButton:disabled")
    # Faint, not literally absent -- `TEXT_FAINT` is still a real, if dim,
    # colour, unlike the unset default that caused this bug.
    assert theme.rgba(theme.TEXT_FAINT) in body
