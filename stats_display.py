"""virtualnote's Play Stats screen (Feature 4 in
docs/research/notation-and-feature-ideas.md, "Historical play stats"):
aggregates every `session_log_*.jsonl` the opt-in `S` session-recording
keybind (`session_recorder.py`) has ever written next to `main.py` into a
simple summary -- total logged practice time, most-played notes, and a
sessions-by-date breakdown.

Zero new detection work: pure aggregation over data `SessionRecorder`
already logs, reusing `session_player.load_events()` for the actual JSONL
reading rather than reimplementing it (the same per-file reader
`virtualnote replay` already uses). Static content, raw ANSI, no
user-editable state -- same `blessed`-free reasoning as
`credits_display.py`, and the same "pure logic unit-tested, real I/O
smoke-tested" split: `compute_stats()` is the pure aggregation function
(a list of already-loaded `(path, events)` pairs in, a stats dict out) and
`stats_lines()` is the pure text-building function (mirrors
`credits_display.credits_lines()`); `load_sessions()` (real file I/O) and
`run_stats_screen()` (the interactive poll-and-render loop) are the two
real-I/O-touching pieces, smoke-tested manually per this repo's existing
`run_terminal_*`/`run_credits_screen`/`run_settings_screen` convention.
"""

import glob
import os
import re
import shutil
import sys
import time
from collections import Counter

import config
import session_player

_FILENAME_RE = re.compile(r"session_log_(\d{4})(\d{2})(\d{2})_\d{6}\.jsonl$")


def session_log_paths(directory):
    """Sorted list of `session_log_*.jsonl` paths in `directory` -- a
    flat, non-recursive glob, matching `session_recorder.py`'s own "next
    to main.py" placement convention."""
    return sorted(glob.glob(os.path.join(directory, "session_log_*.jsonl")))


def _session_date(path):
    """Extracts a `YYYY-MM-DD` date string from a
    `session_log_<timestamp>.jsonl` filename (session_recorder.py's own
    `time.strftime('%Y%m%d_%H%M%S')` naming convention) -- returns `None`
    if the filename doesn't match, so a hand-renamed or foreign file in
    the same directory is excluded from the by-date breakdown rather than
    crashing stats computation."""
    match = _FILENAME_RE.search(os.path.basename(path))
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def compute_stats(sessions):
    """`sessions`: a list of `(path, events)` pairs, `events` as returned
    by `session_player.load_events()` -- kept separate from any real file
    I/O so this is directly unit-testable against synthesized event lists,
    no `tmp_path`/real files needed. Returns a dict:
    `session_count`, `total_practice_seconds` (sum of every logged note's
    `duration_seconds`), `most_played` (list of `(label, count)`, most
    frequent first, top 10), `sessions_by_date` (dict `date -> session
    count`, ordered chronologically, dates only for filenames matching the
    naming convention)."""
    total_seconds = 0.0
    note_counts = Counter()
    sessions_by_date = Counter()
    for path, events in sessions:
        for event in events:
            total_seconds += event.get("duration_seconds", 0.0)
            note_counts[event["label"]] += 1
        date = _session_date(path)
        if date is not None:
            sessions_by_date[date] += 1
    return {
        "session_count": len(sessions),
        "total_practice_seconds": total_seconds,
        "most_played": note_counts.most_common(10),
        "sessions_by_date": dict(sorted(sessions_by_date.items())),
    }


def load_sessions(directory):
    """Real I/O: reads every `session_log_*.jsonl` in `directory` via
    `session_player.load_events()` into the `(path, events)` pairs
    `compute_stats()` consumes. Kept separate from `compute_stats()`
    itself so tests exercise the pure aggregation logic without touching
    disk."""
    return [(path, session_player.load_events(path)) for path in session_log_paths(directory)]


def _format_duration(seconds):
    """`total_practice_seconds` -> a short human string, dropping leading
    zero units (e.g. `"45s"`, `"3m 12s"`, `"1h 4m 0s"`) -- mirrors this
    app's other status-line formatting's "no more precision than useful"
    posture."""
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def stats_lines(stats):
    """`stats` (as returned by `compute_stats()`) -> the screen's full
    text content as a list of lines, independent of terminal width --
    `_render()` centers each line itself. Kept separate from `_render()`
    so the actual wording is unit-testable without a terminal, same split
    as `credits_display.credits_lines()`."""
    lines = [
        "note-color -- play stats",
        "",
        f"sessions logged: {stats['session_count']}",
        f"total logged practice time: {_format_duration(stats['total_practice_seconds'])}",
        "",
    ]
    if stats["most_played"]:
        lines.append("most-played notes:")
        for label, count in stats["most_played"]:
            lines.append(f"  {label:<4} {count}")
    else:
        lines.append("most-played notes: (no sessions logged yet -- press 's' in a terminal view to start)")
    lines.append("")
    if stats["sessions_by_date"]:
        lines.append("sessions by date:")
        for date, count in stats["sessions_by_date"].items():
            lines.append(f"  {date}  {count}")
        lines.append("")
    lines.append("Press any key to return to the menu.")
    return lines


def _render(stats):
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols, rows = size
    lines = stats_lines(stats)
    top = max((rows - len(lines)) // 2, 1)

    out = ["\033[2J"]
    for i, line in enumerate(lines):
        out.append(f"\033[{top + i};1H\033[K" + line.center(cols))
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def run_stats_screen():
    """Renders the play-stats screen once (aggregating every session log
    next to `main.py` at render time -- a fresh read each visit, so a
    session recorded since the last time this screen was open shows up
    without restarting `virtualnote`) and waits for a single keypress (any
    key, same "no other state to disturb" leniency as `run_credits_screen`)
    before returning to the menu. Inert when stdin isn't a real TTY, same
    graceful-degradation rationale as `main.RawKeys`."""
    from main import RawKeys

    directory = os.path.dirname(os.path.abspath(__file__))
    stats = compute_stats(load_sessions(directory))

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    keys = RawKeys()
    try:
        _render(stats)
        if not keys.active:
            return
        while keys.poll() is None:
            time.sleep(1.0 / config.TERMINAL_FPS)
    finally:
        keys.restore()
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
