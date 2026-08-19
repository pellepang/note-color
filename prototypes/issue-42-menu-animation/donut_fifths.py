#!/usr/bin/env python3
"""Prototype D for issue #42: the spinning donut from prototype A, but
re-skinned to carry actual music identity instead of an abstract
rainbow -- the torus's main loop (phi) is split into the same 12 bands
as the circle of fifths (color_map.NOTE_NAMES_FIFTHS, same hue-per-band
convention as terminal_wheel_display.py's FIFTHS_LABELS), so each
twelfth of the donut is literally one note's real app color. Each
band's letter is stamped onto the visible rim as it spins past the
front of the donut. (No chase/highlight sweep -- removed per feedback;
lighting variation comes only from the donut's own 3D shading.)

Run: .venv/bin/python prototypes/issue-42-menu-animation/donut_fifths.py
Quit: Ctrl+C
"""

import math
import shutil
import sys
import time

sys.path.insert(0, ".")
import config  # noqa: E402
from color_map import hsl_to_rgb255, NOTE_NAMES_FIFTHS  # noqa: E402

THETA_SPACING = 0.07
PHI_SPACING = 0.02
R1, R2, K2 = 1, 2, 5

FIFTHS_LABELS = [NOTE_NAMES_FIFTHS[(7 * i) % 12] for i in range(12)]


def band_for_phi(phi):
    return int((phi % (2 * math.pi)) / (2 * math.pi) * 12) % 12


def project(theta, phi, A, B, cols, K1):
    costheta, sintheta = math.cos(theta), math.sin(theta)
    cosphi, sinphi = math.cos(phi), math.sin(phi)
    cosA, sinA = math.cos(A), math.sin(A)
    cosB, sinB = math.cos(B), math.sin(B)

    circlex = R2 + R1 * costheta
    circley = R1 * sintheta

    x = circlex * (cosB * cosphi + sinA * sinB * sinphi) - circley * cosA * sinB
    y = circlex * (sinB * cosphi - sinA * cosB * sinphi) + circley * cosA * cosB
    z = K2 + cosA * circlex * sinphi + circley * sinA
    ooz = 1 / z

    L = (cosphi * costheta * sinB - cosA * costheta * sinphi
         - sinA * sintheta + cosB * (cosA * sintheta - costheta * sinA * sinphi))
    return x, y, ooz, L


def main():
    sys.stdout.write("\033[?25l\033[2J")
    A, B = 1.0, 0.5
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols, rows = size.columns, max(size.lines - 1, 1)
            K1 = cols * K2 * 3 / (8 * (R1 + R2))

            output = [" "] * (cols * rows)
            zbuffer = [0.0] * (cols * rows)
            band_of = [0] * (cols * rows)

            theta = 0.0
            while theta < 2 * math.pi:
                phi = 0.0
                while phi < 2 * math.pi:
                    x, y, ooz, L = project(theta, phi, A, B, cols, K1)
                    xp = int(cols / 2 + K1 * ooz * x)
                    yp = int(rows / 2 - K1 * ooz * y * 0.5)
                    if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                        idx = xp + cols * yp
                        if ooz > zbuffer[idx]:
                            zbuffer[idx] = ooz
                            output[idx] = "@" if L > 0.9 else ("#" if L > 0.6 else ("*" if L > 0.3 else "."))
                            band_of[idx] = band_for_phi(phi)
                    phi += PHI_SPACING
                theta += THETA_SPACING

            # Stamp each band's letter on the outer rim (theta=0) where visible.
            labels = {}
            for i in range(12):
                phi_i = (i + 0.5) * (2 * math.pi / 12)
                x, y, ooz, L = project(0.0, phi_i, A, B, cols, K1)
                xp = int(cols / 2 + K1 * ooz * x)
                yp = int(rows / 2 - K1 * ooz * y * 0.5)
                if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                    idx = xp + cols * yp
                    if ooz >= zbuffer[idx] * 0.97:
                        labels[idx] = i

            lines = ["\033[H"]
            for y in range(rows):
                row = []
                for x in range(cols):
                    idx = x + cols * y
                    ch = output[idx]
                    if ch == " ":
                        row.append(" ")
                        continue
                    band = band_of[idx]
                    hue = (band * 30 + config.HUE_OFFSET_DEG) % 360
                    lo, hi = config.BASE_LIGHTNESS_RANGE
                    light = lo + (hi - lo) * 0.5
                    r, g, b = hsl_to_rgb255(hue, config.BASE_SATURATION, min(light, hi))
                    if idx in labels:
                        letter = FIFTHS_LABELS[labels[idx]][0]
                        row.append(f"\033[1m\033[38;2;255;255;255m\033[48;2;{r};{g};{b}m{letter}\033[0m")
                    else:
                        row.append(f"\033[38;2;{r};{g};{b}m{ch}")
                lines.append("".join(row) + "\033[0m")
            lines.append("\033[K  donut_fifths  --  12 bands = circle of fifths  --  Ctrl+C to quit")
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

            A += 0.04
            B += 0.02
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
