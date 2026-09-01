"""virtualnote's menu screen (issue #40): pick a tool, then go run it.

render() draws issue #42's decided animated design (issue #51 built it):
a spinning ASCII donut re-skinned with the app's circle-of-fifths palette
(menu_animation.py owns the actual animation math/auto-detect heuristic)
as a left-hand pane, with the title/donation-callout/tool-list/hints/
status text overlaid in a fixed-width right-hand pane beside it -- see
_layout()'s docstring for the split and menu_animation.py's module
docstring for the animation itself. Narrow terminals (below
config.MENU_MIN_DONUT_COLS of leftover width) drop the donut entirely and
fall back to a centered text-only screen, same shape as this module's
original #40 placeholder.

The selection plumbing (the tool list, the selected-index state,
move()/move_to()/current_view()) is issue #40's and is unchanged by any of
this -- shell.py only ever calls move()/move_to()/current_view()/
render()/quit(), never reaches into render()'s internals.
"""

import shutil
import sys

import config
import menu_animation
from config_store import store
from main import VIEWS

# (view name passed to main.run_session, one-line description). Order here
# is also menu order and digit-key order (1-indexed) -- see shell.py's
# _handle_menu_key. Derived from main.VIEWS (architecture-modernization-
# plan.md §3.3) rather than a separate literal list, so this menu and
# run_session()/virtualnote.build_parser() can't drift out of sync about
# which views exist -- VIEWS's own dict order (fill, wheel, tab, gui) is
# preserved (insertion-ordered since Python 3.7), so menu/digit-key order
# is unchanged from before this table existed.
TOOLS = [(name, entry["menu_label"]) for name, entry in VIEWS.items()]

# TOOLS plus the non-audio screens (issue #43's Settings, issue #44's
# Credits, Prototypes, and the Feature-4 Play Stats screen below) that
# live in the same menu but don't go through main.run_session --
# shell.py special-cases these view names instead of dispatching them
# there. Selection/render/digit-jump all operate on this combined list so
# every entry is reachable "same tier as any tool" (#37), while TOOLS
# itself stays exactly the set run_session knows how to launch.
MENU_ITEMS = TOOLS + [
    ("settings", "Settings -- keybinds & note colors"),
    ("credits", "Credits -- author & attribution"),
    ("prototypes", "Prototypes -- browse prototypes/ READMEs"),
    ("stats", "Stats -- historical play stats"),
]


def osc8_link(text, url):
    """Wraps `text` in an OSC 8 terminal hyperlink escape sequence pointing
    at `url` -- genuinely clickable in terminals that support it (kitty,
    iTerm2, wezterm, gnome-terminal, etc.), and degrades to plain `text`
    everywhere else with no separate fallback branch needed (#37/#39's
    settled approach for the donation callout)."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _donation_line(width):
    """The main menu screen's author + donation callout (issue #44) --
    centered on `width` using the *visible* text's width, since the OSC 8
    escape bytes wrapping the URL would otherwise throw off str.center()'s
    character count without changing what's actually drawn on screen.

    The full "by <author> -- please support on <platform>: <url>" text
    (~70 visible chars) is wider than `config.MENU_TEXT_PANE_WIDTH` (46) --
    every real menu render hits this truncation branch, not just narrow
    terminals. Left un-truncated, the text pane's fixed-width layout
    (`_layout()`) would write it past the pane's right edge; the terminal
    then auto-wraps the overflow onto the donut pane or the following
    screen row, corrupting the whole frame. Truncated text drops the OSC 8
    wrapping too -- a hyperlink around a chopped-off substring would still
    technically work, but a clipped, silently-still-clickable URL reads as
    more broken than plain truncated text."""
    prefix = f"by {config.AUTHOR_NAME}  --  please support on {config.DONATION_PLATFORM}: "
    visible = prefix + config.DONATION_URL
    if len(visible) > width:
        return (visible[: width - 1] + "…") if width > 1 else visible[:width]
    pad = max((width - len(visible)) // 2, 0)
    return " " * pad + prefix + osc8_link(config.DONATION_URL, config.DONATION_URL)


def _layout(cols, rows):
    """Pure column-split math (no ANSI/terminal I/O) for the animated menu
    screen: a fixed-width text pane (title/donation/tool-list/hints/
    status) pinned to the terminal's right edge, with the donut animation
    filling the leftover columns to its left, separated by a 1-column gap.

    Below config.MENU_MIN_DONUT_COLS of leftover width, the donut is
    dropped entirely (donut_cols=0) rather than squeezed unreadably small,
    and the text pane re-centers on the full terminal width instead of
    staying pinned right -- the same centered-list look this screen had
    before #51's animation, just as the narrow-terminal fallback now
    rather than the only mode. `rows` isn't used by the split itself (the
    donut is exactly as tall as the terminal either way) but is accepted
    for symmetry with render_frame()'s signature and so callers don't need
    to special-case it.
    """
    text_width = min(config.MENU_TEXT_PANE_WIDTH, max(cols, 1))
    donut_cols = cols - text_width - 1  # 1-column gap between panes
    if donut_cols < config.MENU_MIN_DONUT_COLS:
        text_col = max((cols - text_width) // 2 + 1, 1)
        return 0, text_col, text_width
    return donut_cols, donut_cols + 2, text_width


# shell.py's run_menu_loop() constructs a fresh MenuDisplay on every '|'
# back-to-menu round trip (not just once at process start), so without this
# cache the 'auto' path's real timing probe (menu_animation.detect_perf_mode
# -- MENU_AUTODETECT_PROBE_FRAMES real frame renders) would re-run on every
# single trip back to the menu, silently working against the "instant
# transition, no relaunch latency" architecture goal '|' exists for (see
# CLAUDE.md's SessionState docstring/Architecture section). Keyed on
# (cols, rows) only -- cpu_count is constant for the process's life, and a
# genuine resize legitimately deserves a fresh probe at the new size, same
# spirit as this app's existing "terminal views clear on detected resize"
# behavior. An explicit override ('full'/'perf', from --menu-perf-mode or
# config.toml) never touches the probe at all, so it's already free and
# isn't cached here.
_perf_probe_cache = {}


def _resolve_perf_mode(cols, rows, override=None):
    """Resolution order for issue #42's "config/CLI override" requirement:
    an explicit `override` ('full'/'perf', from virtualnote's
    --menu-perf-mode flag) wins outright; otherwise config.toml's
    [preferences].menu_perf_mode (default 'auto'); 'auto' at either level
    falls through to menu_animation.detect_perf_mode()'s real startup
    probe, cached per terminal size (see `_perf_probe_cache` above) so it
    only actually runs once per size seen this process. Returns
    (perf: bool, reason: str)."""
    choice = override or store.preference("menu_perf_mode", "auto")
    if choice in ("full", "perf"):
        return choice == "perf", f"menu_perf_mode={choice}"
    key = (cols, rows)
    if key not in _perf_probe_cache:
        _perf_probe_cache[key] = menu_animation.detect_perf_mode(cols, rows)
    return _perf_probe_cache[key]


def _text_lines(rows, text_width, selected, status):
    """The text pane's content as a {terminal_row: content} map, pure and
    instance-free (selected/status passed in) so it's unit-testable
    without a MenuDisplay or a terminal. Vertical placement mirrors this
    screen's pre-#51 layout exactly, just narrowed to `text_width` instead
    of the full terminal width."""
    title = "note-color"
    top = max(rows // 2 - len(MENU_ITEMS) - 2, 1)
    lines = {
        top: title.center(text_width),
        top + 1: _donation_line(text_width),
    }
    for i, (_view, desc) in enumerate(MENU_ITEMS):
        row = top + 2 + i
        marker = "> " if i == selected else "  "
        line = f"{marker}{i + 1}. {desc}"
        if i == selected:
            line = f"\033[7m{line}\033[0m"  # reverse-video highlight
        lines[row] = line
    hint_row = top + 2 + len(MENU_ITEMS) + 1
    lines[hint_row] = "Up/Down or 1-{}=select  Enter=go  Ctrl+C=quit".format(len(MENU_ITEMS))
    lines[hint_row + 1] = status
    return lines


class MenuDisplay:
    def __init__(self, perf_mode_override=None):
        self.selected = 0
        self._last_size = None
        self.A, self.B = 1.0, 0.5  # donut rotation phase, advanced each render()

        size = shutil.get_terminal_size(fallback=(80, 24))
        donut_cols, _, _ = _layout(size.columns, size.lines)
        if donut_cols > 0:
            self.perf, _reason = _resolve_perf_mode(donut_cols, size.lines, perf_mode_override)
        else:
            # No donut pane at this size -- nothing to probe; perf/fps
            # still needs a value (used by shell.py to pace the loop) so
            # default to the cheaper cadence rather than guessing.
            self.perf = True
        self.fps = config.MENU_FPS_PERF if self.perf else config.MENU_FPS_FULL

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

        donut_cols, text_col, text_width = _layout(cols, rows)
        out = [clear] if clear else []

        if donut_cols > 0:
            donut_lines = menu_animation.render_frame(donut_cols, rows, self.A, self.B, self.perf)
            self.A += config.MENU_DONUT_SPIN_A_STEP
            self.B += config.MENU_DONUT_SPIN_B_STEP
            for i, line in enumerate(donut_lines):
                out.append(f"\033[{i + 1};1H{line}")

        for row, content in _text_lines(rows, text_width, self.selected, status).items():
            out.append(f"\033[{row};{text_col}H\033[K{content}")

        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
