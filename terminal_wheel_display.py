"""Circle-of-fifths wheel diagram for the terminal: draws all 12 notes
arranged in circle-of-fifths order around a ring (C at top, clockwise),
each in its fifths-mapped color, highlighting whichever is currently
playing. Independent of --color-scheme -- this diagram always visualizes
the circle of fifths itself."""

import math
import shutil
import sys

import config
from color_map import hsl_to_rgb255

FIFTHS_LABELS = ["C", "G", "D", "A", "E", "B", "F#", "Db", "Ab", "Eb", "Bb", "F"]

DIM_LIGHTNESS = 0.16


class WheelDisplay:
    def __init__(self, fps=12):
        self.fps = fps
        sys.stdout.write("\033[?25l\033[2J")
        sys.stdout.flush()

    def render(self, active_index, pulse, status):
        cols, rows = shutil.get_terminal_size(fallback=(80, 24))
        cx = cols // 2
        cy = max(rows // 2 - 1, 5)
        ry = max(min(rows // 2 - 4, 8), 3)
        rx = max(min(ry * 2, (cols - 16) // 2), 6)

        out = []
        for i in range(12):
            theta = math.radians(i * 30)
            x = cx + round(rx * math.sin(theta))
            y = cy - round(ry * math.cos(theta))
            label = FIFTHS_LABELS[i]
            hue = (i * 30 + config.HUE_OFFSET_DEG) % 360

            if i == active_index:
                lo, hi = config.BASE_LIGHTNESS_RANGE
                light = min(hi, lo + (hi - lo) * (0.6 + 0.4 * pulse))
            else:
                light = DIM_LIGHTNESS

            r, g, b = hsl_to_rgb255(hue, config.BASE_SATURATION, light)
            bg = f"\033[48;2;{r};{g};{b}m"
            fg_rgb = (20, 20, 20) if light > 0.45 else (220, 220, 220)
            fg = f"\033[38;2;{fg_rgb[0]};{fg_rgb[1]};{fg_rgb[2]}m"
            cell = f"{bg}{fg} {label:<2s} \033[0m"

            col = max(x - 2, 1)
            out.append(f"\033[{max(y, 1)};{col}H{cell}")

        status_row = cy + ry + 2
        out.append(f"\033[{status_row};1H\033[K{status}")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
