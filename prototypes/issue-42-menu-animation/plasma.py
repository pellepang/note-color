#!/usr/bin/env python3
"""Prototype C for issue #42: a flowing sine-interference "plasma" field,
hue-mapped -- evokes sound waves rather than a fixed 3D object. Cheap
per-cell math (a handful of sin() calls), so it doubles as a concrete
performance-mode candidate: run with --perf to see the degraded mode
side by side with the full one.

Full mode: half-block characters (▀) with independent fg/bg color
double the vertical resolution -- two color samples per terminal cell.
Perf mode (--perf): single space-per-cell (background color only, half
the color samples), a coarsened cell grid (samples every other column),
quantized hue (24 steps instead of continuous) and half the frame rate --
a stand-in for what Pi Zero 2 W-class hardware would fall back to.

Run: .venv/bin/python prototypes/issue-42-menu-animation/plasma.py [--perf]
Quit: Ctrl+C
"""

import math
import shutil
import sys
import time

sys.path.insert(0, ".")
from color_map import hsl_to_rgb255  # noqa: E402


def value_at(x, y, t):
    return (
        math.sin(x * 0.15 + t)
        + math.sin(y * 0.10 - t * 0.7)
        + math.sin((x + y) * 0.08 + t * 0.5)
        + math.sin(math.hypot(x - 40, y * 2 - 20) * 0.12 - t * 1.3)
    )


def color_for(v, perf):
    hue = ((v + 4) / 8) * 360
    if perf:
        hue = round(hue / 15) * 15  # 24 quantized steps
    return hsl_to_rgb255(hue % 360, 0.75, 0.55)


def main():
    perf = "--perf" in sys.argv
    sys.stdout.write("\033[?25l\033[2J")
    t = 0.0
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols, rows = size.columns, max(size.lines - 1, 1)

            lines = ["\033[H"]
            if perf:
                for y in range(rows):
                    row = []
                    for x in range(0, cols, 2):
                        v = value_at(x, y, t)
                        r, g, b = color_for(v, perf=True)
                        row.append(f"\033[48;2;{r};{g};{b}m  ")
                    lines.append("".join(row) + "\033[0m")
            else:
                for y in range(rows):
                    row = []
                    for x in range(cols):
                        v_top = value_at(x, y * 2, t)
                        v_bot = value_at(x, y * 2 + 1, t)
                        rt, gt, bt = color_for(v_top, perf=False)
                        rb, gb, bb = color_for(v_bot, perf=False)
                        row.append(f"\033[38;2;{rt};{gt};{bt}m\033[48;2;{rb};{gb};{bb}m▀")
                    lines.append("".join(row) + "\033[0m")

            mode = "PERF (coarse grid, quantized hue, half rate)" if perf else "FULL (half-block, continuous hue)"
            lines.append(f"\033[K  plasma [{mode}]  --  Ctrl+C to quit")
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

            t += 0.12 if not perf else 0.06
            time.sleep(1 / 30 if not perf else 1 / 15)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
