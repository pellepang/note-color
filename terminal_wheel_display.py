"""Circle-of-fifths wheel diagram for the terminal: draws all 12 notes
arranged in circle-of-fifths order around a ring (C at top, clockwise),
each in its fifths-mapped color, highlighting whichever is currently
playing. Independent of --color-scheme -- this diagram always visualizes
the circle of fifths itself."""

import math
import shutil
import sys

import config
from color_map import hsl_to_rgb255, NOTE_NAMES_FIFTHS

# Ring position i -> pitch class, in circle-of-fifths order (0=C, clockwise).
FIFTHS_LABELS = [NOTE_NAMES_FIFTHS[(7 * i) % 12] for i in range(12)]

# Promoted to config.DIM_LIGHTNESS so terminal_tab_display.py's per-column
# age-based fade (issue #22) can reuse the exact same floor -- see
# config.py's comment on DIM_LIGHTNESS for the rationale. Keep this alias
# rather than a second literal copy, so the two views can never drift.
DIM_LIGHTNESS = config.DIM_LIGHTNESS


class WheelDisplay:
    def __init__(self, fps=12):
        self.fps = fps
        self._last_size = None
        sys.stdout.write("\033[?25l\033[2J")
        sys.stdout.flush()

    def render(self, active_index, pulse, status):
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size

        # Only 12 fixed-size cells + a status line are drawn each frame; on
        # a resize the ring recenters, so the previous frame's cells (at
        # the old positions) would otherwise never get overwritten and
        # linger as ghost/duplicated notes. A tiling WM resizes often.
        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

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
        sys.stdout.write(clear + "".join(out))
        sys.stdout.flush()

    def render_chord(self, fades, bass_pitch_class, status):
        """Chord mode: `fades` is a 12-element list of 0..1 crossfade
        levels (one per pitch class, ring order via FIFTHS_LABELS/index
        (7*i)%12), steadily lit rather than pulsing -- a sustained chord
        shouldn't visually flicker. `bass_pitch_class` (or None) gets a
        thicker bracket border instead of the plain space-padded cell."""
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size

        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

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
            pitch_class = (7 * i) % 12
            hue = (i * 30 + config.HUE_OFFSET_DEG) % 360
            fade = fades[pitch_class]

            lo, hi = config.BASE_LIGHTNESS_RANGE
            lit_level = hi * 0.85
            light = DIM_LIGHTNESS + (lit_level - DIM_LIGHTNESS) * fade

            r, g, b = hsl_to_rgb255(hue, config.BASE_SATURATION, light)
            bg = f"\033[48;2;{r};{g};{b}m"
            fg_rgb = (20, 20, 20) if light > 0.45 else (220, 220, 220)
            fg = f"\033[38;2;{fg_rgb[0]};{fg_rgb[1]};{fg_rgb[2]}m"
            if pitch_class == bass_pitch_class and fade > 0.5:
                cell = f"{bg}{fg}[{label:<2s}]\033[0m"
            else:
                cell = f"{bg}{fg} {label:<2s} \033[0m"

            col = max(x - 2, 1)
            out.append(f"\033[{max(y, 1)};{col}H{cell}")

        status_row = cy + ry + 2
        out.append(f"\033[{status_row};1H\033[K{status}")
        sys.stdout.write(clear + "".join(out))
        sys.stdout.flush()

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
