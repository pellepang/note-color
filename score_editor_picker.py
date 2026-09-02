"""Score editor (issue #98): the live-menu entry point -- a picker over
existing score files next to `main.py`, plus a "New score..." action that
captures a filename. Reached from `shell.py`'s menu loop via a dedicated
`"edit"` branch, not the `_NON_SESSION_SCREENS` dict every other non-audio
screen (Settings/Credits/Prototypes/Stats) goes through: unlike those
four, actually launching the editor (`main.run_score_editor()`) needs to
relay the same `"quit"`/`"menu"` sentinel every `run_session` tool
returns, not the "always straight back to the menu" shape those four
screens share -- see `shell.py`'s own `"edit"` handling.

Filename capture (`capture_filename()`'s interactive loop): a small
raw-ANSI keystroke-buffer helper built on `main.RawKeys`, not
`settings_display.py`'s `blessed` exception. `blessed` there is
deliberately scoped to that one screen's field-navigation/remap-capture
UI (see that module's docstring and docs/DECISIONS.md); reusing that
carve-out for a second, unrelated "type a line of text and press Enter"
need would widen a one-screen exception into a second UI framework used
piecemeal across the shell. This screen's actual need -- a single-line,
backspace-editable buffer, Enter confirms, Esc cancels -- is exactly the
shape `settings_display._capture_hue()`/`_capture_numeric()` already poll
one keystroke at a time for, just against `main.RawKeys` instead of
`blessed.Terminal.inkey()`; see docs/DECISIONS.md for the full rationale.
"""

import glob
import os
import shutil
import sys

DEFAULT_NEW_SCORE_NAME = "score.musicxml"


def score_file_paths(directory):
    """Sorted, de-duplicated list of `*.musicxml`/`*.xml` paths in
    `directory` (flat, non-recursive) -- mirrors
    `stats_display.session_log_paths()`'s own "next to main.py" discovery
    convention."""
    paths = set()
    for pattern in ("*.musicxml", "*.xml"):
        paths.update(glob.glob(os.path.join(directory, pattern)))
    return sorted(paths)


def build_menu_entries(paths):
    """(basename) display rows for the picker screen, one per existing
    file, followed by a fixed "New score..." row -- pure, unit-testable
    independent of any real directory listing. The picker's own
    move()/selection logic reuses this list's length directly."""
    return [os.path.basename(p) for p in paths] + ["New score..."]


def move(selected, delta, num_entries):
    """Wraps both ends, same convention as menu_display.MenuDisplay.move
    /settings_display.move."""
    return (selected + delta) % num_entries


def is_new_score_row(selected, paths):
    """True when `selected` is the picker's trailing "New score..." row
    (always the last entry, one past every real file -- see
    build_menu_entries())."""
    return selected == len(paths)


def resolve_new_score_path(directory, typed_name):
    """A typed filename (from `capture_filename()`) -> a full path next
    to `directory`. An empty/whitespace-only name falls back to
    DEFAULT_NEW_SCORE_NAME rather than producing an unusable blank path.
    A name with no `.musicxml`/`.xml` extension gets `.musicxml` appended
    -- `score_editor_state.save_score()`/`load_score()` both go through
    `music21`, which infers the file format from this extension."""
    name = typed_name.strip() or DEFAULT_NEW_SCORE_NAME
    if not (name.endswith(".musicxml") or name.endswith(".xml")):
        name += ".musicxml"
    return os.path.join(directory, name)


def _render(entries, selected, prompt=""):
    size = shutil.get_terminal_size(fallback=(80, 24))
    lines = [
        "note-color -- score editor",
        "Up/Down move  Enter select  |/Esc back to menu",
        "",
    ]
    if not entries[:-1]:
        lines.append("  (no existing score files found next to main.py)")
        lines.append("")
    for i, label in enumerate(entries):
        marker = "> " if i == selected else "  "
        line = f"{marker}{label}"
        if i == selected:
            line = f"\033[7m{line}\033[0m"
        lines.append(line)
    if prompt:
        lines.append("")
        lines.append(prompt)
    out = ["\033[2J"]
    for i, line in enumerate(lines, start=1):
        out.append(f"\033[{i};1H\033[K{line[:size.columns]}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def capture_filename(keys, directory):
    """Interactive single-line filename capture for the "New score..."
    row: backspace-editable buffer, Enter confirms (resolved via
    `resolve_new_score_path()`), Esc cancels back to the picker list with
    no path chosen (returns None). Polls `keys` (a `main.RawKeys`
    instance already in raw mode) directly, one keystroke at a time --
    see the module docstring for why this doesn't reach for `blessed`."""
    import time

    buffer = ""
    while True:
        _render(["New score..."], 0, prompt=f"Filename (Enter to confirm, Esc cancels): {buffer}")
        ch = keys.poll()
        if ch is None:
            time.sleep(0.02)
            continue
        if ch == "\x1b":
            return None
        if ch in ("\r", "\n"):
            return resolve_new_score_path(directory, buffer)
        if ch in ("\x7f", "\x08"):
            buffer = buffer[:-1]
        elif ch.isprintable():
            buffer += ch


def run_score_editor_picker(directory=None):
    """Interactive picker: Up/Down move, Enter selects a file (or, on the
    trailing "New score..." row, opens `capture_filename()`), `|`/Esc
    cancels back to the menu with no file chosen. Returns a resolved path
    string, or None if the user backed out without choosing one. Inert
    (returns None immediately) when stdin isn't a real TTY, same
    graceful-degradation convention as every other screen's `main.RawKeys`
    use."""
    import time

    from main import RawKeys

    directory = directory or os.path.dirname(os.path.abspath(__file__))
    keys = RawKeys()
    try:
        if not keys.active:
            return None
        paths = score_file_paths(directory)
        entries = build_menu_entries(paths)
        selected = 0
        while True:
            _render(entries, selected)
            key = keys.poll()
            if key is None:
                time.sleep(0.02)
                continue
            if key == "|" or key == "\x1b":
                return None
            if key == "UP":
                selected = move(selected, -1, len(entries))
            elif key == "DOWN":
                selected = move(selected, 1, len(entries))
            elif key in ("\r", "\n"):
                if is_new_score_row(selected, paths):
                    path = capture_filename(keys, directory)
                    if path is not None:
                        return path
                    # Esc from the filename prompt: back to the list, not
                    # all the way out to the menu.
                    continue
                return paths[selected]
    finally:
        keys.restore()
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
