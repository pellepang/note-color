"""virtualnote's Credits screen (issue #44): static full attribution --
the author, Claude/AI-assistance, and third-party library credit -- reached
as its own menu entry (menu_display.MENU_ITEMS), separate from the shorter
author + donation-link callout the main menu screen shows on its own
(menu_display._donation_line).

Static content, raw ANSI (per #37/#39's grilling: this screen carries no
user-editable state, so it doesn't need the settings screen's blessed form
controls -- it stays consistent with every other screen in the shell
instead).

Per this repo's test convention, credits_lines() (the pure text-building
function) is unit-tested; the interactive poll-and-render loop itself
(run_credits_screen) is smoke-tested manually, same as menu_display's own
render()/MenuDisplay.
"""

import shutil
import sys
import time

import config
from menu_display import osc8_link

# (library name, one-line blurb) -- mirrors requirements.txt; not
# generated from it so the blurb text stays hand-written and stable even
# if a version pin changes.
THIRD_PARTY_LIBRARIES = [
    ("numpy", "FFT/array math behind pitch detection, chroma folding, and multipitch peak-picking."),
    ("sounddevice", "PortAudio bindings -- the microphone/loopback capture stream."),
    ("pygame-ce", "the GUI window (issue #40's virtualnote gui)."),
    ("blessed", "form controls on the Settings screen (issue #43)."),
]


def credits_lines(donation_line=None):
    """The screen's full text content as a list of lines, independent of
    terminal width -- render() centers/positions each line itself. Kept
    separate from render() so the actual wording is unit-testable without
    a terminal.

    `donation_line`, if given, replaces the plain default author/donation
    line -- lets run_credits_screen() substitute its OSC-8-wrapped
    clickable version without _render() having to guess which rendered
    line was the donation line by sniffing its text."""
    if donation_line is None:
        donation_line = f"please support on {config.DONATION_PLATFORM}: {config.DONATION_URL}"
    lines = [
        "note-color -- credits",
        "",
        f"by {config.AUTHOR_NAME}",
        donation_line,
        "",
        "Built with AI assistance from Claude (Anthropic).",
        "",
        "Third-party libraries:",
    ]
    for name, blurb in THIRD_PARTY_LIBRARIES:
        lines.append(f"  {name} -- {blurb}")
    lines.append("")
    lines.append("Press any key to return to the menu.")
    return lines


def _render(donation_line=None):
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols, rows = size
    lines = credits_lines(donation_line)
    top = max((rows - len(lines)) // 2, 1)

    out = ["\033[2J"]
    for i, line in enumerate(lines):
        out.append(f"\033[{top + i};1H\033[K" + line.center(cols))
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def run_credits_screen():
    """Renders the credits screen once and waits for a single keypress (any
    key, not just '|' -- there's no other state on this screen a stray
    keypress could disturb, so being lenient here is strictly more usable
    than requiring the exact global back-to-menu key) before returning to
    the menu. Inert (returns immediately) when stdin isn't a real TTY, same
    graceful-degradation rationale as main.RawKeys."""
    from main import RawKeys

    donation_line = f"please support on {config.DONATION_PLATFORM}: " + \
        osc8_link(config.DONATION_URL, config.DONATION_URL)

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    keys = RawKeys()
    try:
        _render(donation_line)
        if not keys.active:
            return
        while keys.poll() is None:
            time.sleep(1.0 / config.TERMINAL_FPS)
    finally:
        keys.restore()
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
