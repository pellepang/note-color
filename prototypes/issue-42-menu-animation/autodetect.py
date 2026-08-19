#!/usr/bin/env python3
"""Prototype G for issue #42: the auto-detection heuristic for the
animated menu's performance mode. Answers #42's last open question --
"what to probe, what thresholds" -- by calling donut_fifths.py's *real*
render_frame() at the terminal's actual size a few times at startup and
timing it, rather than guessing from CPU specs alone.

Heuristic:

1. os.cpu_count() <= CPU_FLOOR (2): skip the probe and go straight to
   perf mode. A machine with only 1-2 cores is a strong enough signal
   on its own, and skipping the probe avoids spending real time on a
   frame we already know we won't keep.
2. Otherwise, render PROBE_FRAMES full-mode frames off-screen (computed
   but never written to the terminal) at the real terminal size, and
   take the mean. If it's slower than FULL_FRAME_BUDGET (can't sustain
   the full mode's own 30fps target), fall back to perf mode.
3. Otherwise, full mode.

The probe cost itself (a handful of real frames, not printed) is small
and one-time at startup, same spirit as this project's existing
startup costs (device enumeration, etc.) -- not worth hiding behind a
loading spinner.

Run: .venv/bin/python prototypes/issue-42-menu-animation/autodetect.py
   (Env vars let you rehearse both branches without different hardware:
   FORCE_CPU_COUNT=2 fakes a weak core count;
   FORCE_SLOW_PROBE=1 fakes a slow probe measurement.)
Quit: Ctrl+C
"""

import importlib.util
import os
import shutil
import sys
import time

sys.path.insert(0, ".")

_spec = importlib.util.spec_from_file_location(
    "donut_fifths", os.path.join(os.path.dirname(__file__), "donut_fifths.py")
)
donut_fifths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(donut_fifths)

CPU_FLOOR = 2
PROBE_FRAMES = 3
FULL_FRAME_BUDGET = 1 / 30  # full mode's own target frame time


def detect_perf_mode(cols_term, rows):
    cpu_count = os.cpu_count() or 1
    if "FORCE_CPU_COUNT" in os.environ:
        cpu_count = int(os.environ["FORCE_CPU_COUNT"])

    if cpu_count <= CPU_FLOOR:
        return True, f"cpu_count={cpu_count} <= {CPU_FLOOR}, skipped probe"

    A, B = 1.0, 0.5
    times = []
    for _ in range(PROBE_FRAMES):
        t0 = time.perf_counter()
        donut_fifths.render_frame(cols_term, rows, A, B, perf=False)
        times.append(time.perf_counter() - t0)
        A += 0.04
        B += 0.02
    avg = sum(times) / len(times)
    if "FORCE_SLOW_PROBE" in os.environ:
        avg = 1.0

    if avg > FULL_FRAME_BUDGET:
        return True, f"cpu_count={cpu_count}, probe avg={avg * 1000:.1f}ms > budget={FULL_FRAME_BUDGET * 1000:.1f}ms"
    return False, f"cpu_count={cpu_count}, probe avg={avg * 1000:.1f}ms within budget={FULL_FRAME_BUDGET * 1000:.1f}ms"


def main():
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols_term, rows = size.columns, max(size.lines - 1, 1)

    t_detect = time.perf_counter()
    perf, reason = detect_perf_mode(cols_term, rows)
    t_detect = time.perf_counter() - t_detect

    sys.stderr.write(
        f"[autodetect] mode={'PERF' if perf else 'FULL'} ({reason}), "
        f"detection took {t_detect * 1000:.1f}ms\n"
    )
    time.sleep(1.2)  # let the decision be readable before the screen clears

    fps = 15 if perf else 30
    sys.stdout.write("\033[?25l\033[2J")
    A, B = 1.0, 0.5
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols_term, rows = size.columns, max(size.lines - 1, 1)
            sys.stdout.write(donut_fifths.render_frame(cols_term, rows, A, B, perf))
            sys.stdout.flush()
            A += 0.04
            B += 0.02
            time.sleep(1 / fps)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
