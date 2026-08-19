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

Picked design (see issue #45). This file now also answers #42's
performance-mode question: run with --perf for the degraded mode.

Full mode prints one ANSI escape per *run* of same-colored consecutive
cells along a row (not per character) -- #42's Notes flag the per-cell
truecolor escape-sequence volume as something to actually benchmark on
Pi Zero 2 W, not assume; run-length-encoding the color codes is the
direct fix, and costs nothing visually, so it applies in both modes.

--perf additionally renders at half the raster resolution (each
computed cell is printed twice, filling the same terminal width) with
a moderately coarser surface sampling on top -- benchmarked against
the obvious alternative of *just* thinning the surface sampling
against the full raster: that alternative computes faster too, but
leaves scattered gaps between sparse points that fragment otherwise-
long same-color runs, so despite drawing fewer characters it emits
*more* escape sequences and more total bytes than full mode -- the
opposite of what "performance mode" should do on likely I/O-bound
links (slow serial console, SSH). Halving the raster first guarantees
every cell has a same-color neighbor, so both CPU (~2x fewer points to
project) and escape-sequence volume (~30% fewer runs, ~20% fewer
bytes, measured) improve together. Also drops the rim letters,
collapses shading to a single glyph, and halves the frame rate.

Run: .venv/bin/python prototypes/issue-42-menu-animation/donut_fifths.py [--perf]
Quit: Ctrl+C
"""

import math
import shutil
import sys
import time

sys.path.insert(0, ".")
import config  # noqa: E402
from color_map import hsl_to_rgb255, NOTE_NAMES_FIFTHS  # noqa: E402

R1, R2, K2 = 1, 2, 5
FULL_THETA_SPACING, FULL_PHI_SPACING = 0.07, 0.02
PERF_THETA_SPACING, PERF_PHI_SPACING = 0.10, 0.03
PERF_BLOCK_WIDTH = 2  # each rendered cell prints twice -- half raster resolution

FIFTHS_LABELS = [NOTE_NAMES_FIFTHS[(7 * i) % 12] for i in range(12)]


def band_for_phi(phi):
    return int((phi % (2 * math.pi)) / (2 * math.pi) * 12) % 12


def project(theta, phi, A, B, K1):
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


def band_color(band):
    hue = (band * 30 + config.HUE_OFFSET_DEG) % 360
    lo, hi = config.BASE_LIGHTNESS_RANGE
    return hsl_to_rgb255(hue, config.BASE_SATURATION, min(lo + (hi - lo) * 0.5, hi))


def render_row(cells, cols):
    """`cells` is a list of (fg_code_or_None, char) for one row, one per
    column. Runs of identical fg_code get a single escape sequence
    instead of one per character -- the escape-volume optimization."""
    parts = []
    run_code, run_chars = None, []

    def flush():
        if not run_chars:
            return
        if run_code is None:
            parts.append("".join(run_chars))
        else:
            parts.append(run_code + "".join(run_chars) + "\033[0m")

    for code, ch in cells:
        if code != run_code:
            flush()
            run_code, run_chars = code, [ch]
        else:
            run_chars.append(ch)
    flush()
    return "".join(parts)


def render_frame(cols_term, rows, A, B, perf):
    """Builds one full frame (cursor-home + status line included) for a
    terminal of `cols_term` columns / `rows` content rows. Factored out
    of main()'s loop so autodetect.py can call the *real* render path
    directly when timing its startup probe, not a synthetic proxy."""
    theta_spacing, phi_spacing = (
        (PERF_THETA_SPACING, PERF_PHI_SPACING) if perf else (FULL_THETA_SPACING, FULL_PHI_SPACING)
    )
    block_w = PERF_BLOCK_WIDTH if perf else 1
    cols = max(1, cols_term // block_w)  # raster resolution -- each cell prints block_w wide
    K1 = cols * K2 * 3 / (8 * (R1 + R2))

    output = [" "] * (cols * rows)
    zbuffer = [0.0] * (cols * rows)
    band_of = [0] * (cols * rows)

    theta = 0.0
    while theta < 2 * math.pi:
        phi = 0.0
        while phi < 2 * math.pi:
            x, y, ooz, L = project(theta, phi, A, B, K1)
            xp = int(cols / 2 + K1 * ooz * x)
            yp = int(rows / 2 - K1 * ooz * y * 0.5)
            if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                idx = xp + cols * yp
                if ooz > zbuffer[idx]:
                    zbuffer[idx] = ooz
                    if perf:
                        output[idx] = "@"
                    else:
                        output[idx] = "@" if L > 0.9 else ("#" if L > 0.6 else ("*" if L > 0.3 else "."))
                    band_of[idx] = band_for_phi(phi)
            phi += phi_spacing
        theta += theta_spacing

    labels = {}
    if not perf:
        for i in range(12):
            phi_i = (i + 0.5) * (2 * math.pi / 12)
            x, y, ooz, L = project(0.0, phi_i, A, B, K1)
            xp = int(cols / 2 + K1 * ooz * x)
            yp = int(rows / 2 - K1 * ooz * y * 0.5)
            if L > 0 and 0 <= xp < cols and 0 <= yp < rows:
                idx = xp + cols * yp
                if ooz >= zbuffer[idx] * 0.97:
                    labels[idx] = i

    band_codes = {}

    def code_for(band):
        if band not in band_codes:
            r, g, b = band_color(band)
            band_codes[band] = f"\033[38;2;{r};{g};{b}m"
        return band_codes[band]

    lines = ["\033[H"]
    for y in range(rows):
        row_start = cols * y
        cells = []
        for x in range(cols):
            idx = row_start + x
            ch = output[idx]
            if ch == " ":
                cells.append((None, " " * block_w))
                continue
            band = band_of[idx]
            if idx in labels:
                r, g, b = band_color(band)
                letter = FIFTHS_LABELS[labels[idx]][0]
                cells.append((f"\033[1m\033[38;2;255;255;255m\033[48;2;{r};{g};{b}m", (letter * block_w)))
            else:
                cells.append((code_for(band), ch * block_w))
        lines.append(render_row(cells, cols))

    mode = "PERF (half raster, coarser sampling, no letters, half rate)" if perf else "FULL (RLE color runs)"
    lines.append(f"\033[K  donut_fifths [{mode}]  --  Ctrl+C to quit")
    return "\n".join(lines)


def main():
    perf = "--perf" in sys.argv
    fps = 15 if perf else 30

    sys.stdout.write("\033[?25l\033[2J")
    A, B = 1.0, 0.5
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            cols_term, rows = size.columns, max(size.lines - 1, 1)
            sys.stdout.write(render_frame(cols_term, rows, A, B, perf))
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
