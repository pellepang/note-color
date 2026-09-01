"""virtualnote's Prototypes screen: browse `prototypes/` from inside the
running app, so a throwaway prototype's own README write-up (this repo's
existing convention -- see every `prototypes/*/README.md` and
`docs/research/project-retrospective-and-alternatives.md`'s Part 3 table)
can be skimmed and assessed without leaving the terminal or hunting
through the tree by hand. Reached as its own menu entry
(menu_display.MENU_ITEMS), same tier as Settings/Credits.

Two-level list/detail viewer, both read-only: a list of every prototype
that has a README.md, and a scrollable full-text view of the selected
one's README. Static content, raw ANSI, no user-editable state -- same
"scoped exception only for Settings' form controls" reasoning
credits_display.py already documents (#37/#39): this screen only ever
reads files, so it doesn't need Settings' `blessed` dependency either.

Never executes any prototype's code -- prototypes are throwaway,
standalone scripts with their own ad hoc CLI usage (each README's own
"How to run it" section). This screen is purely for reading the
write-up; actually running one is still a manual
`.venv/bin/python prototypes/<name>/<script>.py` outside virtualnote,
same as always.

Per this repo's test convention: the pure listing/wrapping/pagination
helpers below (list_prototypes(), _wrap_readme(), _visible_slice()) are
unit-tested; the interactive poll-and-render loop itself
(run_prototypes_screen) is smoke-tested manually, same as
menu_display's/credits_display's own render loops.
"""

import os
import shutil
import sys
import textwrap
import time

import config

PROTOTYPES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prototypes")


def list_prototypes(root=None):
    """Every immediate subdirectory of `root` (default PROTOTYPES_ROOT)
    that has a README.md, sorted by name -- pure, so it's unit-testable
    without touching the real prototypes/ tree. A subdirectory with no
    README.md is skipped rather than shown with an empty write-up: every
    real prototype in this repo has one (this module's own convention,
    see docstring above), so a missing one signals scratch/incomplete
    content, not a real prototype ready to assess.

    Returns a list of {"name", "title", "readme_path"} dicts -- `title`
    is the README's first non-blank line with a leading '#'/whitespace
    stripped (its H1), falling back to `name` if the file has none."""
    root = root or PROTOTYPES_ROOT
    entries = []
    if not os.path.isdir(root):
        return entries
    for name in sorted(os.listdir(root)):
        readme_path = os.path.join(root, name, "README.md")
        if not os.path.isfile(readme_path):
            continue
        title = name
        with open(readme_path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    title = stripped
                    break
        entries.append({"name": name, "title": title, "readme_path": readme_path})
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
    lines = ["Prototypes -- browse prototypes/ (read-only)", ""]
    if not entries:
        lines.append("(no prototypes with a README.md found)")
    for i, entry in enumerate(entries):
        marker = "> " if i == selected else "  "
        line = f"{marker}{entry['name']} -- {entry['title']}"
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

    header = f"Prototypes / {entry['name']}"
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


def run_prototypes_screen():
    """Interactive list/detail loop -- Up/Down moves the list selection or
    scrolls the open detail view (one line at a time); Enter/Right opens
    the selected prototype's README; Left/Backspace closes it back to the
    list; `|` returns to the menu from either level, same global
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

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    keys = RawKeys()
    try:
        if not keys.active:
            return
        _render_list(entries, selected, "Up/Down=select  Enter/Right=view  |=back to menu")
        while True:
            key = keys.poll()
            if key is None:
                time.sleep(1.0 / config.TERMINAL_FPS)
                continue
            if key == "|":
                return

            # Handle the keypress first, then render whichever mode it
            # left us in -- a single render dispatch at the bottom (rather
            # than one per branch above) is what makes Enter open the
            # detail view on the very same keypress instead of a frame
            # late (an earlier version rendered the list unconditionally
            # here, so opening a README only actually appeared on the
            # *next* keypress).
            if mode == "list":
                if key == "UP" and entries:
                    selected = (selected - 1) % len(entries)
                elif key == "DOWN" and entries:
                    selected = (selected + 1) % len(entries)
                elif entries and (key in ("\r", "\n") or key == "RIGHT"):
                    detail_entry = entries[selected]
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
                _render_list(entries, selected, "Up/Down=select  Enter/Right=view  |=back to menu")
            else:
                size = shutil.get_terminal_size(fallback=(80, 24))
                rows_available = max(size.lines - 3, 1)
                detail_scroll = _render_detail(detail_entry, detail_lines, detail_scroll, rows_available)
    finally:
        keys.restore()
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
