"""Animation math for virtualnote's bare-menu screen (issues #42/#51): a
spinning ASCII donut re-skinned with the app's circle-of-fifths palette --
its 12 phi-bands carry the same hue-per-band convention as
terminal_wheel_display.py's ring, with each note's letter stamped onto the
visible rim as it spins past (full mode) -- or a degraded, auto-detected
performance mode (half raster resolution, coarser surface sampling, no
letters, half framerate) for weaker hardware. No color-chase/highlight
sweep -- tried live and rejected during #42's design (see issue #45).

Ported from the throwaway prototype at
prototype/issue-42-menu-animation/{donut_fifths.py,autodetect.py}. The one
substantive change from that prototype: render_frame()'s point-projection
math is vectorized with NumPy instead of a plain-Python double loop over
theta/phi -- the prototype's loop measured ~149ms/frame on a 4-core
desktop against a 33ms/30fps budget (issue #42's flagged, not-yet-fixed
problem); see CLAUDE.md's Key design decisions for the approach and
before/after numbers.

Pure geometry/projection/decision logic lives here, unit-tested per this
project's convention (tests/test_menu_animation.py). menu_display.py owns
compositing this module's per-row output into the actual menu screen
(donut beside the title/donation/tool-list/status text pane) and the
interactive per-frame loop -- smoke-tested manually, like every other
run_terminal_*/render() path in this app.
"""

import math
import os
import time

import numpy as np

import config
from color_map import hsl_to_rgb255, NOTE_NAMES_FIFTHS

# Classic "spinning donut" torus/projection constants (the algorithm this
# is built on, Andy Sloane's ASCII donut) -- fixed geometry of the shape
# itself, not a per-machine tunable, so these stay local rather than
# joining the real tunables (spacing, perf knobs, fps) in config.py.
R1, R2, K2 = 1, 2, 5

# Ring position i -> pitch class, in circle-of-fifths order (0=C,
# clockwise) -- same convention as terminal_wheel_display.py's own
# FIFTHS_LABELS (recomputed independently here rather than imported, same
# one-line-duplication precedent that module already set).
FIFTHS_LABELS = [NOTE_NAMES_FIFTHS[(7 * i) % 12] for i in range(12)]

# Shading levels 0..3 (low -> high lighting term L), full mode only.
_SHADE_CHARS = ["." , "*", "#", "@"]


def band_for_phi(phi):
    """Which of the 12 circle-of-fifths bands a given phi angle (radians)
    falls in -- pure modular arithmetic, used both per-point (labels) and
    vectorized (main raster, via the same formula inlined in
    render_frame)."""
    return int((phi % (2 * math.pi)) / (2 * math.pi) * 12) % 12


def band_color(band):
    hue = (band * 30 + config.HUE_OFFSET_DEG) % 360
    lo, hi = config.BASE_LIGHTNESS_RANGE
    return hsl_to_rgb255(hue, config.BASE_SATURATION, min(lo + (hi - lo) * 0.5, hi))


def render_row(cells, cols):
    """`cells` is a list of (fg_code_or_None, char) for one row, one per
    raster column. Runs of identical fg_code get a single escape sequence
    instead of one per character -- cuts ANSI escape-sequence volume
    dramatically on a mostly-same-colored donut surface, worth doing on a
    likely I/O-bound link (slow serial console, SSH) per #42/#46."""
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


def _project(theta, phi, A, B, K1):
    """NumPy-vectorized torus point projection -- theta/phi may be
    scalars or broadcastable arrays (a meshgrid); returns (x, y, ooz, L)
    of the same broadcast shape. Same algebra as the prototype's
    per-point project(), just expressed as array ops so the whole
    theta/phi grid evaluates in one batch of C loops instead of one
    Python-level function call (with several `math` calls each) per
    point -- see this module's docstring for why that matters."""
    costheta, sintheta = np.cos(theta), np.sin(theta)
    cosphi, sinphi = np.cos(phi), np.sin(phi)
    cosA, sinA = math.cos(A), math.sin(A)
    cosB, sinB = math.cos(B), math.sin(B)

    circlex = R2 + R1 * costheta
    circley = R1 * sintheta

    x = circlex * (cosB * cosphi + sinA * sinB * sinphi) - circley * cosA * sinB
    y = circlex * (sinB * cosphi - sinA * cosB * sinphi) + circley * cosA * cosB
    z = K2 + cosA * circlex * sinphi + circley * sinA
    ooz = 1.0 / z

    L = (cosphi * costheta * sinB - cosA * costheta * sinphi
         - sinA * sintheta + cosB * (cosA * sintheta - costheta * sinA * sinphi))
    return x, y, ooz, L


def render_frame(cols_term, rows, A, B, perf):
    """Builds one full donut frame as a list of exactly `rows` row-strings,
    each `cols_term` printable terminal columns wide (perf mode's
    half-raster cells are each printed twice, so every row is still fully
    explicit content, not just the drawn cells -- no cursor positioning of
    its own, so menu_display.py can address/overlay each row directly.

    Kept as a single function (mirroring the prototype) specifically so
    the auto-detect probe below calls the *exact* function real rendering
    uses, not a synthetic timing proxy.
    """
    if perf:
        theta_spacing = config.MENU_DONUT_PERF_THETA_SPACING
        phi_spacing = config.MENU_DONUT_PERF_PHI_SPACING
        block_w = config.MENU_DONUT_PERF_BLOCK_WIDTH
    else:
        theta_spacing = config.MENU_DONUT_FULL_THETA_SPACING
        phi_spacing = config.MENU_DONUT_FULL_PHI_SPACING
        block_w = 1

    cols_term = max(int(cols_term), 1)
    rows = max(int(rows), 1)
    cols = max(1, cols_term // block_w)
    K1 = cols * K2 * 3 / (8 * (R1 + R2))

    theta_vals = np.arange(0.0, 2 * math.pi, theta_spacing)
    phi_vals = np.arange(0.0, 2 * math.pi, phi_spacing)
    Theta, Phi = np.meshgrid(theta_vals, phi_vals, indexing="ij")

    x, y, ooz, L = _project(Theta, Phi, A, B, K1)
    xp = (cols / 2 + K1 * ooz * x).astype(np.int64)
    yp = (rows / 2 - K1 * ooz * y * 0.5).astype(np.int64)

    valid = (L > 0) & (xp >= 0) & (xp < cols) & (yp >= 0) & (yp < rows)

    filled = np.zeros(cols * rows, dtype=bool)
    band_of = np.zeros(cols * rows, dtype=np.int64)
    zbuffer = np.zeros(cols * rows)
    shade_of = None if perf else np.zeros(cols * rows, dtype=np.int64)

    if np.any(valid):
        idx_v = (xp[valid] + cols * yp[valid]).astype(np.int64)
        ooz_v = ooz[valid]
        L_v = L[valid]
        phi_v = Phi[valid]
        band_v = ((phi_v % (2 * math.pi)) / (2 * math.pi) * 12).astype(np.int64) % 12

        # Painter's algorithm without a per-point Python comparison: sort
        # ascending by depth (ooz) and let NumPy's fancy-index assignment
        # keep the *last* value written to a repeated index -- so the
        # closest point (max ooz) per cell wins, same result as the
        # prototype's "if ooz > zbuffer[idx]" loop.
        order = np.argsort(ooz_v, kind="stable")
        idx_o = idx_v[order]

        filled[idx_o] = True
        band_of[idx_o] = band_v[order]
        zbuffer[idx_o] = ooz_v[order]
        if not perf:
            L_o = L_v[order]
            shade_level = np.select([L_o > 0.9, L_o > 0.6, L_o > 0.3], [3, 2, 1], default=0)
            shade_of[idx_o] = shade_level

    labels = {}
    if not perf:
        for i in range(12):
            phi_i = (i + 0.5) * (2 * math.pi / 12)
            xi, yi, oozi, Li = _project(0.0, phi_i, A, B, K1)
            xpi, ypi = int(cols / 2 + K1 * oozi * xi), int(rows / 2 - K1 * oozi * yi * 0.5)
            if Li > 0 and 0 <= xpi < cols and 0 <= ypi < rows:
                idxi = xpi + cols * ypi
                if oozi >= zbuffer[idxi] * 0.97:
                    labels[idxi] = i

    band_codes = {}

    def code_for(band):
        if band not in band_codes:
            r, g, b = band_color(band)
            band_codes[band] = f"\033[38;2;{r};{g};{b}m"
        return band_codes[band]

    # block_w doesn't necessarily divide cols_term evenly (perf mode's
    # block_w=2 leaves a 1-column remainder on any odd-width donut pane,
    # e.g. the very common 80-column-terminal case) -- widening the last
    # raster column to absorb that remainder keeps every row's *printed*
    # width exactly cols_term, matching this function's own contract
    # above. Full mode's block_w=1 always divides evenly, so this is a
    # no-op there.
    last_col_w = cols_term - (cols - 1) * block_w

    lines = []
    for y_row in range(rows):
        row_start = cols * y_row
        cells = []
        for x_col in range(cols):
            idx = row_start + x_col
            w = last_col_w if x_col == cols - 1 else block_w
            if not filled[idx]:
                cells.append((None, " " * w))
                continue
            band = int(band_of[idx])
            if idx in labels:
                r, g, b = band_color(band)
                letter = FIFTHS_LABELS[labels[idx]][0]
                cells.append((f"\033[1m\033[38;2;255;255;255m\033[48;2;{r};{g};{b}m", letter * w))
            elif perf:
                cells.append((code_for(band), "@" * w))
            else:
                ch = _SHADE_CHARS[int(shade_of[idx])]
                cells.append((code_for(band), ch * w))
        lines.append(render_row(cells, cols))
    return lines


# --- Performance-mode auto-detection (issue #46's decided heuristic) ------

def _decide_perf_mode(cpu_count, probe_avg, cpu_floor, budget):
    """Pure decision function, no timing/IO of its own -- separated out of
    detect_perf_mode() below so the heuristic itself (not the real clock)
    is what tests exercise. `probe_avg` is ignored (may be None) once
    cpu_count already trips the floor."""
    if cpu_count <= cpu_floor:
        return True, f"cpu_count={cpu_count} <= {cpu_floor}, skipped probe"
    if probe_avg > budget:
        return True, f"cpu_count={cpu_count}, probe avg={probe_avg * 1000:.1f}ms > budget={budget * 1000:.1f}ms"
    return False, f"cpu_count={cpu_count}, probe avg={probe_avg * 1000:.1f}ms within budget={budget * 1000:.1f}ms"


def detect_perf_mode(cols_term, rows, cpu_count=None):
    """Issue #46's auto-detect heuristic: a core-count floor skips the
    probe outright on weak hardware (a real frame is wasted time on a
    machine that's already decided); otherwise render_frame() runs
    MENU_AUTODETECT_PROBE_FRAMES real, off-screen (never printed) full-mode
    frames at the terminal's actual size and times their mean against full
    mode's own frame budget. Returns (perf: bool, reason: str) -- the probe
    is one-time at startup, same spirit as this app's other startup costs
    (device enumeration etc.), not worth hiding behind a spinner."""
    cpu_count = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if cpu_count <= config.MENU_AUTODETECT_CPU_FLOOR:
        return _decide_perf_mode(cpu_count, None, config.MENU_AUTODETECT_CPU_FLOOR, config.MENU_AUTODETECT_FRAME_BUDGET)

    A, B = 1.0, 0.5
    times = []
    for _ in range(config.MENU_AUTODETECT_PROBE_FRAMES):
        t0 = time.perf_counter()
        render_frame(cols_term, rows, A, B, perf=False)
        times.append(time.perf_counter() - t0)
        A += config.MENU_DONUT_SPIN_A_STEP
        B += config.MENU_DONUT_SPIN_B_STEP
    avg = sum(times) / len(times)
    return _decide_perf_mode(cpu_count, avg, config.MENU_AUTODETECT_CPU_FLOOR, config.MENU_AUTODETECT_FRAME_BUDGET)
