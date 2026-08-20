"""Tests for issue #44's credits-screen text content. Per this repo's test
convention, only the pure credits_lines() text builder is unit-tested here;
the interactive render/wait-for-keypress loop (run_credits_screen) is
smoke-tested manually, same as menu_display's own render()/MenuDisplay.
"""

import config
from credits_display import credits_lines, THIRD_PARTY_LIBRARIES


def test_credits_names_the_author():
    lines = credits_lines()
    assert any(config.AUTHOR_NAME in line for line in lines)


def test_credits_includes_donation_platform_and_url():
    lines = credits_lines()
    assert any(config.DONATION_PLATFORM in line for line in lines)
    assert any(config.DONATION_URL in line for line in lines)


def test_credits_names_claude_ai_assistance():
    lines = credits_lines()
    assert any("Claude" in line for line in lines)


def test_credits_lists_every_third_party_library():
    lines = credits_lines()
    joined = "\n".join(lines)
    for name, _blurb in THIRD_PARTY_LIBRARIES:
        assert name in joined
