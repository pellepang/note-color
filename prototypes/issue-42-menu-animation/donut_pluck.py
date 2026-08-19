#!/usr/bin/env python3
"""Prototype F for issue #42: same donut_fifths shape/coloring (12
fifths-colored bands, letters on the rim), but instead of a color
highlight, actual note events *deform the surface* -- a small simulated
arpeggio "plucks" the torus at each note's band position, producing a
localized outward bulge that swells and decays like a struck string.
The donut's own lighting model does the rest: bulges catch the light
differently as they rise and fall, so the music reads as motion/shape,
not a moving spotlight. A plucked band's letter only appears while its
pluck is still audible/visible, fading with it.

Run: .venv/bin/python prototypes/issue-42-menu-animation/donut_pluck.py
Quit: Ctrl+C
"""

import math
import shutil
import sys
import time

sys.path.insert(0, ".")
import config  # noqa: E402
from color_map import hsl_to_rgb255, NOTE_NAMES_FIFTHS  # noqa: E402
from color_map import fifths_index  # noqa: E402

THETA_SPACING = 0.08
PHI_SPACING = 0.025
R1_BASE, R2, K2 = 1.0, 2, 5
BULGE_AMPLITUDE = 0.55
DECAY_SECONDS = 1.1
STEP_SECONDS = 0.42

FIFTHS_LABELS = [NOTE_NAMES_FIFTHS[(7 * i) % 12] for i in range(12)]

# A little C-major-ish arpeggio, expressed as pitch classes then mapped
# to circle-of-fifths band indices via the app's own fifths_index() --
# same convention wheel/tab already use for ring position -> hue.
ARPEGGIO_PITCH_CLASSES = [0, 4, 7, 4, 9, 4, 7, 4]  # C E G E A E G E
ARPEGGIO_BANDS = [fifths_index(pc) for pc in ARPEGGIO_PITCH_CLASSES]


def band_for_phi(phi):
    return int((phi % (2 * math.pi)) / (2 * math.pi) * 12) % 12


def band_center_phi(band):
    return (band + 0.5) * (2 * math.pi / 12)


def active_triggers(now, start):
    """Last few arpeggio notes still within their decay window, as
    (band, age_seconds) pairs."""
    beat = (now - start) / STEP_SECONDS
    triggers = []
    last_i = int(beat)
    for i in range(max(0, last_i - 3), last_i + 1):
        note_time = start + i * STEP_SECONDS
        age = now - note_time
        if 0 <= age < DECAY_SECONDS:
            band = ARPEGGIO_BANDS[i % len(ARPEGGIO_BANDS)]
            triggers.append((band, age))
    return triggers


def bulge_at(phi, triggers):
    total = 0.0
    for band, age in triggers:
        center = band_center_phi(band)
        d = (phi - center + math.pi) % (2 * math.pi) - math.pi
        spatial = math.exp(-(d * d) / (2 * 0.35 ** 2))
        temporal = math.exp(-age / (DECAY_SECONDS * 0.4)) * math.sin(min(1.0, age * 6) * math.pi / 2)
        total += spatial * temporal
    return total


def project(theta, phi, A, B, K1, r1):
    costheta, sintheta = math.cos(theta), math.sin(theta)
    cosphi, sinphi = math.cos(phi), math.sin(phi)
    cosA, sinA = math.cos(A), math.sin(A)
    cosB, sinB = math.cos(B), math.sin(B)

    circlex = R2 + r1 * costheta
    circley = r1 * sintheta

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
    start = time.monotonic()
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols, rows = size.columns, max(size.lines - 1, 1)
            K1 = cols * K2 * 3 / (8 * (R1_BASE + R2))

            now = time.monotonic()
            triggers = active_triggers(now, start)

            output = [" "] * (cols * rows)
            zbuffer = [0.0] * (cols * rows)
            band_of = [0] * (cols * rows)

            # bulge_at() depends only on phi, not theta -- compute each
            # phi ring's radius once and reuse it across every theta on
            # that ring, instead of recomputing per (theta, phi) point
            # (was the entire frame budget: ~80x more calls than needed).
            phi = 0.0
            while phi < 2 * math.pi:
                r1 = R1_BASE + BULGE_AMPLITUDE * bulge_at(phi, triggers)
                band = band_for_phi(phi)
                theta = 0.0
                while theta < 2 * math.pi:
                    x, y, ooz, L = project(theta, phi, A, B, K1, r1)
                    xp = int(cols / 2 + K1 * ooz * x)
                    yp = int(rows / 2 - K1 * ooz * y * 0.5)
                    if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                        idx = xp + cols * yp
                        if ooz > zbuffer[idx]:
                            zbuffer[idx] = ooz
                            output[idx] = "@" if L > 0.9 else ("#" if L > 0.6 else ("*" if L > 0.3 else "."))
                            band_of[idx] = band
                    theta += THETA_SPACING
                phi += PHI_SPACING

            labels = {}
            for band, age in triggers:
                r1 = R1_BASE + BULGE_AMPLITUDE * bulge_at(band_center_phi(band), triggers)
                x, y, ooz, L = project(0.0, band_center_phi(band), A, B, K1, r1)
                xp = int(cols / 2 + K1 * ooz * x)
                yp = int(rows / 2 - K1 * ooz * y * 0.5)
                if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                    idx = xp + cols * yp
                    if ooz >= zbuffer[idx] * 0.97:
                        labels[idx] = band

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
            lines.append("\033[K  donut_pluck  --  arpeggio plucks the surface, no color chase  --  Ctrl+C to quit")
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
