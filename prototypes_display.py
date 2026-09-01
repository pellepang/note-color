"""virtualnote's Prototypes screen: browse and *run* `prototypes/` from
inside the running app, so a throwaway prototype can actually be watched
working -- its own real, colored terminal output -- instead of only being
read about. Reached as its own menu entry (menu_display.MENU_ITEMS), same
tier as Settings/Credits.

Enter is the primary action: it hands the real terminal to the selected
prototype's own no-argument demo/harness script as a subprocess (stdio
inherited, so its raw ANSI/color output renders exactly as if launched by
hand from a shell -- these are the same scripts each README's own "How to
run it" section already documents running standalone), waits for it to
exit, then waits for one keypress before returning to the list. `i`/Right
opens a secondary, scrollable README view for whichever context/write-up
a prototype's own README carries beyond its bare output -- this is the
list/detail viewer an earlier version of this screen used as its *only*
action; execution is more useful for "watch it work" but the README is
still worth keeping one key-press away.

Two-level loop, both driven by this repo's raw-ANSI convention (`|`
returns to the menu from either level; the running-a-subprocess step is
the one place this screen briefly hands the terminal back to cooked mode
-- see run_prototypes_screen()'s docstring). No `blessed` dependency,
same "no editable state beyond selection" reasoning credits_display.py
documents (#37/#39).

Per this repo's test convention: the pure listing/entry-script-resolution/
wrapping/pagination helpers below (list_prototypes(), _find_entry_script(),
_wrap_readme(), _visible_slice()) are unit-tested; the interactive
poll-and-render loop itself (run_prototypes_screen) is smoke-tested
manually, same as menu_display's/credits_display's own render loops.
"""

import os
import shutil
import subprocess
import sys
import textwrap
import time

import config

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PROTOTYPES_ROOT = os.path.join(REPO_ROOT, "prototypes")

# The no-argument demo entry point every existing prototype's own README
# "How to run it" section documents follows one of these common demo-
# script names, checked in this order. A prototype with no file matching
# any of these (piano-roll-view/piano_roll.py, tracker-grid-view/
# tracker_grid.py -- both single-script prototypes with no separate
# demo/harness wrapper) falls back to "the one .py file in this
# directory" in _find_entry_script() below, rather than guessing a
# name-derived filename (dropping "-view"/"-mode"/etc. suffixes from the
# directory name isn't a real, generalizable rule -- a directory with
# more than one non-candidate .py file has no unambiguous entry point
# this way, so it just isn't offered a 'run' action; its README is still
# browsable via 'i').
_ENTRY_SCRIPT_CANDIDATES = ("demo.py", "run_demo.py", "harness.py")


def _find_entry_script(prototype_dir, name):
    """The no-argument script list_prototypes() should offer to run for
    the prototype at `prototype_dir`, or None if nothing matches this
    repo's established naming convention (see module docstring). `name`
    is accepted for a consistent signature with list_prototypes()'s other
    per-entry helpers but isn't otherwise used. Pure filesystem lookup, no
    execution -- unit-tested directly."""
    for candidate in _ENTRY_SCRIPT_CANDIDATES:
        path = os.path.join(prototype_dir, candidate)
        if os.path.isfile(path):
            return path
    py_files = sorted(f for f in os.listdir(prototype_dir) if f.endswith(".py"))
    if len(py_files) == 1:
        return os.path.join(prototype_dir, py_files[0])
    return None


def list_prototypes(root=None):
    """Every immediate subdirectory of `root` (default PROTOTYPES_ROOT)
    that has a README.md, sorted by name -- pure, so it's unit-testable
    without touching the real prototypes/ tree. A subdirectory with no
    README.md is skipped rather than shown with an empty write-up: every
    real prototype in this repo has one (this module's own convention,
    see docstring above), so a missing one signals scratch/incomplete
    content, not a real prototype ready to assess.

    Returns a list of {"name", "title", "readme_path", "script_path"}
    dicts -- `title` is the README's first non-blank line with a leading
    '#'/whitespace stripped (its H1), falling back to `name` if the file
    has none; `script_path` is `_find_entry_script()`'s result, or None
    if this prototype has no runnable entry point by that convention."""
    root = root or PROTOTYPES_ROOT
    entries = []
    if not os.path.isdir(root):
        return entries
    for name in sorted(os.listdir(root)):
        prototype_dir = os.path.join(root, name)
        readme_path = os.path.join(prototype_dir, "README.md")
        if not os.path.isfile(readme_path):
            continue
        title = name
        with open(readme_path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    title = stripped
                    break
        entries.append({
            "name": name,
            "title": title,
            "readme_path": readme_path,
            "script_path": _find_entry_script(prototype_dir, name),
        })
    return entries


def _wrap_readme(readme_path, width):
    """A README's raw text as a flat list of display lines, each wrapped
    to at most `width` columns -- pure text processing (no ANSI/terminal
    I/O), so pagination can be tested against a fixed line list without a
    real file or terminal. A blank source line stays blank (paragraph
    spacing is part of a README's own formatting, not collapsed here);
    `width` is clamped to at least 1 so a pathologically narrow terminal
    can't make textwrap.wrap() raise."""
    width = max(width, 1)
    with open(readme_path, encoding="utf-8") as fh:
        raw_lines = fh.read().splitlines()
    wrapped = []
    for line in raw_lines:
        if not line.strip():
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width=width) or [""])
    return wrapped


def _visible_slice(lines, scroll, height):
    """The `height`-line window of `lines` starting at `scroll`, clamped
    so scrolling can never run past the content in either direction --
    `scroll` itself is clamped too (a caller's Up/Down delta might have
    pushed it out of range), returned alongside the window so the caller
    can persist the clamped value back into its own state. Pure, no
    terminal/instance state, so this is unit-tested directly."""
    max_scroll = max(len(lines) - height, 0)
    scroll = max(0, min(scroll, max_scroll))
    return lines[scroll:scroll + height], scroll


def _render_list(entries, selected, status):
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols, rows = size
    lines = ["Prototypes -- run or read prototypes/ (from note-color's own repo)", ""]
    if not entries:
        lines.append("(no prototypes with a README.md found)")
    for i, entry in enumerate(entries):
        marker = "> " if i == selected else "  "
        tag = " [run]" if entry["script_path"] else " [no runnable demo]"
        line = f"{marker}{entry['name']} -- {entry['title']}{tag}"
        if i == selected:
            line = f"\033[7m{line}\033[0m"
        lines.append(line)
    lines.append("")
    lines.append(status)
    top = max((rows - len(lines)) // 2, 1)

    out = ["\033[2J"]
    for i, line in enumerate(lines):
        out.append(f"\033[{top + i};1H\033[K" + line[:cols])
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _render_detail(entry, wrapped_lines, scroll, rows_available):
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols, rows = size
    visible, scroll = _visible_slice(wrapped_lines, scroll, rows_available)

    header = f"Prototypes / {entry['name']}  (README)"
    shown_through = min(scroll + rows_available, len(wrapped_lines))
    status = (f"Up/Down=scroll  Left/Backspace=back to list  |=back to menu"
              f"  ({shown_through}/{len(wrapped_lines)} lines)")

    out = ["\033[2J", f"\033[1;1H\033[K{header[:cols]}", f"\033[2;1H\033[K{'-' * min(len(header), cols)}"]
    for i, line in enumerate(visible):
        out.append(f"\033[{3 + i};1H\033[K{line[:cols]}")
    out.append(f"\033[{rows};1H\033[K{status[:cols]}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return scroll


def _run_prototype(keys, entry):
    """Hands the real terminal over to `entry`'s demo script for the
    duration of one subprocess run -- this screen's own raw/cbreak stdin
    mode is restored to cooked first (`keys.restore()`) since a child
    script expects an ordinary terminal, not this screen's single-key
    polling mode; the caller re-enters raw mode via a fresh RawKeys()
    once this returns. stdout/stderr are inherited (no capture) so the
    prototype's raw ANSI/color output renders directly, the same as
    running it by hand per its own README's "How to run it" section.
    Blocks until the subprocess exits, then waits for one real keypress
    (a plain blocking `input()`, since stdin is back in cooked/line mode
    here) before returning control to the list."""
    keys.restore()
    sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
    sys.stdout.flush()
    print(f"Running {entry['name']}: {os.path.relpath(entry['script_path'], REPO_ROOT)}\n")
    subprocess.run([sys.executable, entry["script_path"]], cwd=REPO_ROOT)
    try:
        input("\n-- prototype exited. Press Enter to return to Prototypes. --")
    except EOFError:
        pass


def run_prototypes_screen():
    """Interactive list/detail loop. In the list: Up/Down moves the
    selection; Enter runs the selected prototype's own demo script live
    (see _run_prototype()) if it has one, falling back to the README view
    when it doesn't; `i`/Right always opens the README view regardless.
    In the README view: Up/Down scrolls, Left/Backspace closes back to
    the list. `|` returns to the menu from either level, same global
    convention every other screen follows. Inert (returns immediately)
    when stdin isn't a real TTY, same graceful-degradation rationale as
    credits_display.run_credits_screen()."""
    from main import RawKeys

    entries = list_prototypes()
    selected = 0
    mode = "list"
    detail_entry = None
    detail_lines = []
    detail_scroll = 0
    list_status = "Up/Down=select  Enter=run  i/Right=info(readme)  |=back to menu"

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    keys = RawKeys()
    try:
        if not keys.active:
            return
        _render_list(entries, selected, list_status)
        while True:
            key = keys.poll()
            if key is None:
                time.sleep(1.0 / config.TERMINAL_FPS)
                continue
            if key == "|":
                return

            # Handle the keypress first, then render whichever mode it
            # left us in -- a single render dispatch at the bottom (rather
            # than one per branch above) is what makes an action take
            # effect on the very same keypress instead of a frame late.
            if mode == "list":
                if key == "UP" and entries:
                    selected = (selected - 1) % len(entries)
                elif key == "DOWN" and entries:
                    selected = (selected + 1) % len(entries)
                elif entries and key in ("i", "I", "RIGHT"):
                    detail_entry = entries[selected]
                    size = shutil.get_terminal_size(fallback=(80, 24))
                    detail_lines = _wrap_readme(detail_entry["readme_path"], size.columns)
                    detail_scroll = 0
                    mode = "detail"
                elif entries and key in ("\r", "\n"):
                    entry = entries[selected]
                    if entry["script_path"]:
                        _run_prototype(keys, entry)
                        keys = RawKeys()
                        sys.stdout.write("\033[?25l")
                        sys.stdout.flush()
                    else:
                        detail_entry = entry
                        size = shutil.get_terminal_size(fallback=(80, 24))
                        detail_lines = _wrap_readme(detail_entry["readme_path"], size.columns)
                        detail_scroll = 0
                        mode = "detail"
            else:
                if key == "UP":
                    detail_scroll -= 1
                elif key == "DOWN":
                    detail_scroll += 1
                elif key == "LEFT" or key in ("\x7f", "\x08"):
                    mode = "list"

            if mode == "list":
                _render_list(entries, selected, list_status)
            else:
                size = shutil.get_terminal_size(fallback=(80, 24))
                rows_available = max(size.lines - 3, 1)
                detail_scroll = _render_detail(detail_entry, detail_lines, detail_scroll, rows_available)
    finally:
        keys.restore()
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
