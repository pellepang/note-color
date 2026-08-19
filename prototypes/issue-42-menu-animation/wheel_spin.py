#!/usr/bin/env python3
"""Prototype B for issue #42: animates this app's *own* circle-of-fifths
wheel (the same ring `--view wheel` draws) -- a bright pulse chases
around the 12 wedges in fifths order like a metronome ticking through
every key, each wedge flashing to full color then decaying to
DIM_LIGHTNESS. Reuses color_map.py/config.py directly so the menu's
palette can never drift from the live views' palette.

Run: .venv/bin/python prototypes/issue-42-menu-animation/wheel_spin.py
Quit: Ctrl+C
"""

import math
import shutil
import sys
import time

sys.path.insert(0, ".")
import config  # noqa: E402
from color_map import hsl_to_rgb255, NOTE_NAMES_FIFTHS  # noqa: E402

FIFTHS_LABELS = [NOTE_NAMES_FIFTHS[(7 * i) % 12] for i in range(12)]
STEP_SECONDS = 0.35  # tempo of the chase


def main():
    sys.stdout.write("\033[?25l\033[2J")
    start = time.monotonic()
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols, rows = size.columns, size.lines

            cx = cols // 2
            cy = max(rows // 2 - 1, 5)
            ry = max(min(rows // 2 - 4, 9), 4)
            rx = max(min(ry * 2, (cols - 16) // 2), 8)

            elapsed = time.monotonic() - start
            lead = elapsed / STEP_SECONDS  # fractional wedge index of the chase head

            out = ["\033[2J"]
            for i in range(12):
                theta = math.radians(i * 30)
                x = cx + round(rx * math.sin(theta))
                y = cy - round(ry * math.cos(theta))
                label = FIFTHS_LABELS[i]
                hue = (i * 30 + config.HUE_OFFSET_DEG) % 360

                dist = (lead - i) % 12
                if dist < 1:
                    glow = 1.0 - dist  # freshly hit: brightest
                elif dist < 4:
                    glow = max(0.0, 1.0 - (dist - 1) / 3)  # trailing decay
                else:
                    glow = 0.0

                lo, hi = config.BASE_LIGHTNESS_RANGE
                light = config.DIM_LIGHTNESS + (hi - config.DIM_LIGHTNESS) * glow
                r, g, b = hsl_to_rgb255(hue, config.BASE_SATURATION, light)
                bg = f"\033[48;2;{r};{g};{b}m"
                fg_rgb = (20, 20, 20) if light > 0.45 else (220, 220, 220)
                fg = f"\033[38;2;{fg_rgb[0]};{fg_rgb[1]};{fg_rgb[2]}m"
                cell = f"{bg}{fg} {label:<2s} \033[0m"
                col = max(x - 2, 1)
                out.append(f"\033[{max(y, 1)};{col}H{cell}")

            status = "\033[K  wheel_spin  --  chase pulses through the circle of fifths  --  Ctrl+C to quit"
            out.append(f"\033[{cy + ry + 2};1H{status}")
            sys.stdout.write("".join(out))
            sys.stdout.flush()
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
