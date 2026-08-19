#!/usr/bin/env python3
"""Prototype E for issue #42: the circle-of-fifths wheel, but actually
in 3D -- 12 glowing note-color nodes on a ring that tilts and spins in
perspective (nearer nodes bigger/brighter, same z-buffer idea as the
donut prototypes), joined by a wireframe rim, with a metronome-tempo
chase pulse traveling around it. Aims to keep wheel_spin's direct music
identity (real fifths colors + letters) while chasing donut's "wow."

Run: .venv/bin/python prototypes/issue-42-menu-animation/ring3d.py
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
STEP_SECONDS = 0.5
TILT = math.radians(55)
K = 4.0  # camera distance


def rotate_project(x, y, z, spin, cols, rows):
    # spin around Y (the vertical axis), then tilt around X for perspective.
    x1 = x * math.cos(spin) + z * math.sin(spin)
    z1 = -x * math.sin(spin) + z * math.cos(spin)
    y1 = y

    y2 = y1 * math.cos(TILT) - z1 * math.sin(TILT)
    z2 = y1 * math.sin(TILT) + z1 * math.cos(TILT)
    x2 = x1

    dist = z2 + K
    f = cols * 0.9
    sx = cols / 2 + f * x2 / dist
    sy = rows / 2 - (f * 0.5) * y2 / dist
    return sx, sy, 1 / dist  # closeness (bigger = nearer)


def lerp(a, b, t):
    return a + (b - a) * t


def main():
    sys.stdout.write("\033[?25l\033[2J")
    start = time.monotonic()
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols, rows = size.columns, max(size.lines - 1, 1)

            elapsed = time.monotonic() - start
            spin = elapsed * 0.5
            lead = elapsed / STEP_SECONDS

            output = [" "] * (cols * rows)
            zbuffer = [0.0] * (cols * rows)
            colorbuf = [(0, 0, 0)] * (cols * rows)

            def plot(px, py, closeness, rgb, ch):
                xi, yi = int(round(px)), int(round(py))
                if 0 <= xi < cols and 0 <= yi < rows:
                    idx = xi + cols * yi
                    if closeness > zbuffer[idx]:
                        zbuffer[idx] = closeness
                        colorbuf[idx] = rgb
                        output[idx] = ch

            node_xyz = []
            node_screen = []
            for i in range(12):
                theta = math.radians(i * 30)
                x, y, z = math.sin(theta), 0.0, math.cos(theta)
                node_xyz.append((x, y, z))
                node_screen.append(rotate_project(x, y, z, spin, cols, rows))

            # Wireframe rim: interpolate between consecutive nodes.
            for i in range(12):
                x0, y0, z0 = node_xyz[i]
                x1, y1, z1 = node_xyz[(i + 1) % 12]
                hue0 = (i * 30 + config.HUE_OFFSET_DEG) % 360
                hue1 = ((i + 1) * 30 + config.HUE_OFFSET_DEG) % 360
                for s in range(8):
                    t = s / 8
                    x, y, z = lerp(x0, x1, t), lerp(y0, y1, t), lerp(z0, z1, t)
                    sx, sy, closeness = rotate_project(x, y, z, spin, cols, rows)
                    hue = lerp(hue0, hue1 if abs(hue1 - hue0) < 180 else hue1 - 360, t) % 360
                    r, g, b = hsl_to_rgb255(hue, config.BASE_SATURATION, config.DIM_LIGHTNESS + 0.1)
                    plot(sx, sy, closeness * 0.999, (r, g, b), "·")

            # Nodes: blobs sized/brightened by depth and chase glow, letter on top.
            for i in range(12):
                sx, sy, closeness = node_screen[i]
                hue = (i * 30 + config.HUE_OFFSET_DEG) % 360
                dist = (lead - i) % 12
                glow = max(0.0, 1.0 - dist) if dist < 1 else (max(0.0, 1.0 - (dist - 1) / 3) if dist < 4 else 0.0)
                lo, hi = config.BASE_LIGHTNESS_RANGE
                light = min(hi, lo + (hi - lo) * (0.5 + 0.5 * glow))
                r, g, b = hsl_to_rgb255(hue, config.BASE_SATURATION, light)

                radius = max(1, round(closeness * 3.2))
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius * 2, radius * 2 + 1):  # widen for cell aspect ratio
                        if (dx / 2) ** 2 + dy ** 2 <= radius ** 2:
                            plot(sx + dx, sy + dy, closeness + 0.01, (r, g, b), "@" if glow > 0.5 else "o")

                label = FIFTHS_LABELS[i]
                fg = (20, 20, 20) if light > 0.45 else (230, 230, 230)
                letter = f"\033[1m\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{label[:1]}\033[0m"
                xi, yi = int(round(sx)), int(round(sy))
                if 0 <= xi < cols and 0 <= yi < rows:
                    lines_label = (xi, yi, letter)
                else:
                    lines_label = None
                node_screen[i] = (sx, sy, closeness, lines_label)

            lines = ["\033[H"]
            for y in range(rows):
                row = []
                for x in range(cols):
                    idx = x + cols * y
                    ch = output[idx]
                    if ch == " ":
                        row.append(" ")
                        continue
                    r, g, b = colorbuf[idx]
                    row.append(f"\033[38;2;{r};{g};{b}m{ch}")
                lines.append("".join(row) + "\033[0m")

            out = "\n".join(lines)
            for item in node_screen:
                if len(item) == 4 and item[3]:
                    xi, yi, letter = item[3]
                    out += f"\033[{yi + 1};{xi + 1}H{letter}"

            out += f"\033[{rows + 1};1H\033[K  ring3d  --  12 note-nodes in perspective, chase = metronome  --  Ctrl+C to quit"
            sys.stdout.write(out)
            sys.stdout.flush()
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
