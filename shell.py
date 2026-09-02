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
from main import RawKeys, run_score_editor, run_session
from menu_display import MenuDisplay, MENU_ITEMS
from settings_display import run_settings_screen
from credits_display import run_credits_screen
from prototypes_display import run_prototypes_screen
from stats_display import run_stats_screen

# Menu entries handled directly by this module instead of main.run_session
# -- none of the four touches audio/SessionState (issues #43, #44, the
# Prototypes browser, and the Feature-4 Play Stats screen).
_NON_SESSION_SCREENS = {
    "settings": run_settings_screen,
    "credits": run_credits_screen,
    "prototypes": run_prototypes_screen,
    "stats": run_stats_screen,
}


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


def run_menu_loop(session, fps=None, perf_mode_override=None):
    """Shows the menu; on a tool being picked, runs it via run_session()
    against the shared `session`; on that tool returning "menu" (the '|'
    keybind), loops back to the menu with capture/analysis thread/
    sensitivity/source all still alive and reused. Returns only once the
    user quits from the menu itself (Ctrl+C with no tool running) or a
    tool returns "quit" (Ctrl+C / window-close-or-Esc from inside it) --
    either way, the caller (virtualnote.py) is expected to tear the
    session down right after this returns.

    `fps`, if given, overrides the poll/render loop's cadence outright
    (kept for callers/tests that want a fixed rate); otherwise each
    MenuDisplay's own `fps` (30 full mode / 15 perf mode, issue #51) paces
    the loop -- the animated donut's own designed frame rate, not the
    unrelated config.TERMINAL_FPS every *tool* view polls at.
    `perf_mode_override` ('full'/'perf'/None) is virtualnote's
    --menu-perf-mode CLI flag, forwarded to MenuDisplay's own
    config/CLI-override resolution (see menu_display._resolve_perf_mode)."""
    while True:
        menu = MenuDisplay(perf_mode_override=perf_mode_override)
        dt = 1.0 / (fps or menu.fps)
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

        if selection == "edit":
            # The score editor (issue #98) doesn't fit _NON_SESSION_SCREENS'
            # shape: unlike Settings/Credits/Prototypes/Stats, actually
            # running it (main.run_score_editor()) returns the same
            # "menu"/"quit" sentinel every real run_session tool does (it's
            # also directly reachable as `virtualnote edit <path>`), so its
            # result has to be handled the same way run_session's is below
            # -- but it still never touches SessionState/audio, so it can't
            # go through run_session() either. score_editor_picker.
            # run_score_editor_picker() shows the file picker first (an
            # existing score, or "New score..."); a cancelled picker (no
            # path chosen) just loops back to the menu, same as backing out
            # of any other menu entry.
            from score_editor_picker import run_score_editor_picker

            try:
                path = run_score_editor_picker()
            except KeyboardInterrupt:
                return
            if path is None:
                continue
            try:
                result = run_score_editor(path)
            except KeyboardInterrupt:
                return
            if result == "quit":
                return
            continue

        if selection in _NON_SESSION_SCREENS:
            # Settings/Credits are menu entries, not run_session tools
            # (issues #43, #44) -- neither touches audio, so neither ever
            # calls session.ensure_started(), unlike every real tool. Both
            # always return straight to the menu; there's no "quit" out of
            # either via their own return value (unlike run_session's
            # "menu"/"quit" sentinel) -- but Ctrl+C during either must still
            # quit the whole app, same as every other view, so it's caught
            # here explicitly rather than left to propagate out of
            # run_menu_loop() uncaught (both screens' own cbreak-mode raw
            # keyboard handling leaves SIGINT/KeyboardInterrupt enabled,
            # same as main.RawKeys elsewhere in this app).
            try:
                _NON_SESSION_SCREENS[selection]()
            except KeyboardInterrupt:
                return
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
