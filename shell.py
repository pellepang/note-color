"""virtualnote's unified in-process orchestrator (issue #40).

Both of `virtualnote`'s entry paths -- bare `virtualnote` (menu first) and
`virtualnote <view> [flags]` (straight to a tool) -- end up running through
this same loop once a tool exits back to the menu, since #37/#39 settled on
one long-lived process rather than subprocess-per-tool: that's what makes
the `|` back-to-menu keybind an instant state transition instead of a
relaunch with startup latency. `virtualnote.py` (the CLI entry point) is
the only caller of `run_menu_loop()`.

The menu loop itself does nothing about audio -- it shows `MenuDisplay`,
waits for a tool to be picked, then hands off to `main.run_session()`,
which lazily starts (or reuses) the shared `main.SessionState` -- capture,
analysis thread, sensitivity, source -- the whole point of issue #40's
"created once, persists across every menu round-trip" design. See
`main.SessionState`'s docstring for the full rationale.
"""

import sys
import time

import config
from main import RawKeys, run_session
from menu_display import MenuDisplay, MENU_ITEMS
from settings_display import run_settings_screen


def _handle_menu_key(key, menu):
    """Pure selection-state update for one keypress on the menu screen.
    Returns the chosen view name ('fill'/'wheel'/'tab'/'gui'/'settings')
    the instant an entry is selected (Enter confirms the highlighted row;
    a digit key 1..len(MENU_ITEMS) jumps straight to and selects that row
    in one keypress, the one-key-select convenience the old
    `colorize <subcommand>` launcher had). Returns None while still just
    browsing (arrow keys, or no key at all)."""
    if key is None:
        return None
    if key == "UP":
        menu.move(-1)
    elif key == "DOWN":
        menu.move(1)
    elif key in ("\r", "\n"):
        return menu.current_view()
    elif key.isdigit() and key != "0":
        index = int(key) - 1
        if index < len(MENU_ITEMS):
            menu.move_to(index)
            return menu.current_view()
    return None


def run_menu_loop(session, fps=None):
    """Shows the menu; on a tool being picked, runs it via run_session()
    against the shared `session`; on that tool returning "menu" (the '|'
    keybind), loops back to the menu with capture/analysis thread/
    sensitivity/source all still alive and reused. Returns only once the
    user quits from the menu itself (Ctrl+C with no tool running) or a
    tool returns "quit" (Ctrl+C / window-close-or-Esc from inside it) --
    either way, the caller (virtualnote.py) is expected to tear the
    session down right after this returns."""
    fps = fps or config.TERMINAL_FPS
    dt = 1.0 / fps

    while True:
        menu = MenuDisplay()
        keys = RawKeys()
        selection = None
        status = ""
        try:
            while selection is None:
                key = keys.poll()
                selection = _handle_menu_key(key, menu)
                menu.render(status)
                time.sleep(dt)
        except KeyboardInterrupt:
            keys.restore()
            menu.quit()
            return
        keys.restore()
        menu.quit()

        if selection == "settings":
            # Settings is a menu entry, not a run_session tool (issue #43)
            # -- it doesn't touch audio, so it never calls
            # session.ensure_started(), unlike every real tool. Always
            # returns to the menu; there's no "quit" out of it.
            run_settings_screen()
            continue

        try:
            result = run_session(selection, config.DEFAULT_SCROLL_MODE, None, False, False, session)
        except RuntimeError as exc:
            # e.g. the loopback device is unavailable on first tool entry
            # (--source loopback with no PipeWire/PulseAudio monitor, or
            # `pactl` missing) -- report it and stay in the shell instead
            # of crashing the whole process. main.py's standalone path
            # instead surfaces this at argparse time via parser.error(),
            # before any menu exists to fall back to; here there always is
            # one, so falling back to it is strictly better than exiting.
            print(f"[virtualnote] couldn't start '{selection}': {exc}", file=sys.stderr)
            continue

        if result == "quit":
            return
        # result == "menu": loop back around and show the menu again.
