#!/usr/bin/env python3
"""Prototype A for issue #42: a classic spinning ASCII donut (the
"rotating 3D ASCII graphics" the user originally asked for in #38/#39),
but shaded with truecolor per-cell instead of the classic monochrome
luminance ramp -- a rainbow gradient sweeps across the torus surface as
it spins, reusing this app's own hsl_to_rgb255().

Run: .venv/bin/python prototypes/issue-42-menu-animation/donut.py
Quit: Ctrl+C
"""

import math
import shutil
import sys
import time

sys.path.insert(0, ".")
from color_map import hsl_to_rgb255  # noqa: E402

THETA_SPACING = 0.07
PHI_SPACING = 0.02
R1, R2, K2 = 1, 2, 5


def frame(cols, rows, A, B):
    K1 = cols * K2 * 3 / (8 * (R1 + R2))
    output = [" "] * (cols * rows)
    zbuffer = [0.0] * (cols * rows)
    hue = [0.0] * (cols * rows)

    cosA, sinA = math.cos(A), math.sin(A)
    cosB, sinB = math.cos(B), math.sin(B)

    theta = 0.0
    while theta < 2 * math.pi:
        costheta, sintheta = math.cos(theta), math.sin(theta)
        phi = 0.0
        while phi < 2 * math.pi:
            cosphi, sinphi = math.cos(phi), math.sin(phi)

            circlex = R2 + R1 * costheta
            circley = R1 * sintheta

            x = circlex * (cosB * cosphi + sinA * sinB * sinphi) - circley * cosA * sinB
            y = circlex * (sinB * cosphi - sinA * cosB * sinphi) + circley * cosA * cosB
            z = K2 + cosA * circlex * sinphi + circley * sinA
            ooz = 1 / z

            xp = int(cols / 2 + K1 * ooz * x)
            yp = int(rows / 2 - K1 * ooz * y * 0.5)

            L = (cosphi * costheta * sinB - cosA * costheta * sinphi
                 - sinA * sintheta + cosB * (cosA * sintheta - costheta * sinA * sinphi))
            if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                idx = xp + cols * yp
                if ooz > zbuffer[idx]:
                    zbuffer[idx] = ooz
                    output[idx] = "@" if L > 0.9 else ("#" if L > 0.6 else ("*" if L > 0.3 else "."))
                    hue[idx] = (theta / (2 * math.pi)) * 360
            phi += PHI_SPACING
        theta += THETA_SPACING

    return output, hue


def main():
    sys.stdout.write("\033[?25l\033[2J")
    A, B = 1.0, 0.5
    t = 0.0
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols, rows = size.columns, max(size.lines - 1, 1)

            output, hue = frame(cols, rows, A, B)
            lines = ["\033[H"]
            for y in range(rows):
                row = []
                for x in range(cols):
                    ch = output[x + cols * y]
                    if ch == " ":
                        row.append(" ")
                        continue
                    h = (hue[x + cols * y] + t * 40) % 360
                    r, g, b = hsl_to_rgb255(h, 0.85, 0.55)
                    row.append(f"\033[38;2;{r};{g};{b}m{ch}")
                lines.append("".join(row) + "\033[0m")
            lines.append(f"\033[K  donut  --  A/B=torus rotation, hue sweeps with theta  --  Ctrl+C to quit")
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

            A += 0.04
            B += 0.02
            t += 0.03
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
