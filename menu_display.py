"""virtualnote's menu screen (issue #40): pick a tool, then go run it.

Deliberately minimal -- a static, numbered list of tools with the current
selection highlighted, raw ANSI, no animation. Issue #42 owns the actual
animated visual design map #37 wants for this screen and will replace this
module's render() internals wholesale; the surrounding selection plumbing
(the tool list, the selected-index state, move()/move_to()/current_view())
is issue #40's to define and is meant to stay stable across that rewrite --
shell.py only ever calls move()/move_to()/current_view()/render()/quit(),
never reaches into anything #42 would touch.
"""

import shutil
import sys

import config

# (view name passed to main.run_session, one-line description). Order here
# is also menu order and digit-key order (1-indexed) -- see shell.py's
# _handle_menu_key.
TOOLS = [
    ("fill", "Fill -- full-terminal color fill"),
    ("wheel", "Wheel -- circle-of-fifths ring"),
    ("tab", "Tab -- scrolling sheet-music staff"),
    ("gui", "GUI -- native color window"),
]

# TOOLS plus the non-audio screens (issue #43's Settings, issue #44's
# Credits) that live in the same menu but don't go through
# main.run_session -- shell.py special-cases these view names instead of
# dispatching them there. Selection/render/digit-jump all operate on this
# combined list so every entry is reachable "same tier as any tool" (#37),
# while TOOLS itself stays exactly the set run_session knows how to launch.
MENU_ITEMS = TOOLS + [
    ("settings", "Settings -- keybinds & note colors"),
    ("credits", "Credits -- author & attribution"),
]


def osc8_link(text, url):
    """Wraps `text` in an OSC 8 terminal hyperlink escape sequence pointing
    at `url` -- genuinely clickable in terminals that support it (kitty,
    iTerm2, wezterm, gnome-terminal, etc.), and degrades to plain `text`
    everywhere else with no separate fallback branch needed (#37/#39's
    settled approach for the donation callout)."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _donation_line(cols):
    """The main menu screen's author + donation callout (issue #44) --
    centered on `cols` using the *visible* text's width, since the OSC 8
    escape bytes wrapping the URL would otherwise throw off str.center()'s
    character count without changing what's actually drawn on screen."""
    prefix = f"by {config.AUTHOR_NAME}  --  please support on {config.DONATION_PLATFORM}: "
    visible = prefix + config.DONATION_URL
    pad = max((cols - len(visible)) // 2, 0)
    return " " * pad + prefix + osc8_link(config.DONATION_URL, config.DONATION_URL)


class MenuDisplay:
    def __init__(self):
        self.selected = 0
        self._last_size = None
        sys.stdout.write("\033[?25l\033[2J")
        sys.stdout.flush()

    def move(self, delta):
        """Wraps around both ends -- Up from the top row selects the last
        tool and vice versa, standard terminal-menu convention."""
        self.selected = (self.selected + delta) % len(MENU_ITEMS)

    def move_to(self, index):
        if 0 <= index < len(MENU_ITEMS):
            self.selected = index

    def current_view(self):
        return MENU_ITEMS[self.selected][0]

    def render(self, status=""):
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size

        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

        title = "note-color"
        top = max(rows // 2 - len(MENU_ITEMS) - 2, 1)

        out = [f"\033[{top};1H\033[K" + title.center(cols)]
        out.append(f"\033[{top + 1};1H\033[K" + _donation_line(cols))
        for i, (_view, desc) in enumerate(MENU_ITEMS):
            row = top + 2 + i
            marker = "> " if i == self.selected else "  "
            line = f"{marker}{i + 1}. {desc}"
            if i == self.selected:
                line = f"\033[7m{line}\033[0m"  # reverse-video highlight
            out.append(f"\033[{row};1H\033[K{line}")

        hint = "Up/Down or 1-{}=select  Enter=go  Ctrl+C=quit".format(len(MENU_ITEMS))
        hint_row = top + 2 + len(MENU_ITEMS) + 1
        out.append(f"\033[{hint_row};1H\033[K{hint}")
        status_row = hint_row + 1
        out.append(f"\033[{status_row};1H\033[K{status}")
        sys.stdout.write(clear + "".join(out))
        sys.stdout.flush()

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
