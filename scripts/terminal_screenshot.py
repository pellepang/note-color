"""Render a captured raw-ANSI terminal transcript to a PNG, so a human (or
an agent) can actually *see* what a terminal view produced instead of
reading raw escape-code text.

Per docs/research/terminal-visual-capture-for-agents.md's recommendation:
feed the raw bytes through `pyte` (a pure-Python terminal emulator -- does
the real column-width/combining-character resolution, so this script
never has to reimplement wcwidth logic itself) and rasterize the resolved
screen buffer with Pillow, one filled bg rect + fg text per cell.

Usage:
    .venv/bin/python scripts/terminal_screenshot.py INPUT.ansi OUTPUT.png \
        [--cols N] [--rows N]

INPUT.ansi is any file containing raw bytes with SGR truecolor escapes
(`\\x1b[38;2;r;g;bm` / `\\x1b[48;2;r;g;bm`) -- e.g. captured via
`some_view.py > out.ansi` (stdout redirection preserves the escape bytes
verbatim; only a real interactive TTY check inside the target script, if
any, would need `script -qc` / a pty to fool, which none of this repo's
non-interactive prototype scripts need).

Dev-tooling only -- pyte/Pillow are not runtime dependencies of the
shipped app (see the research doc for why that distinction matters on
this project's Pi-portability-conscious dependency policy).
"""

import argparse
import sys

import pyte
from PIL import Image, ImageDraw, ImageFont

CELL_W = 9
CELL_H = 18
FONT_SIZE = 15
DEFAULT_FG = (220, 220, 220)
DEFAULT_BG = (10, 10, 10)

_FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def _load_font():
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def render(ansi_bytes, cols, rows, out_path):
    # pyte emulates a raw terminal: a bare "\n" (LF) moves the cursor down
    # one row but does NOT return it to column 0 (that's a real tty's
    # ONLCR driver behavior, which a plain `print()`-to-a-file capture
    # never goes through) -- without normalizing to CRLF first, every
    # line after the first starts wherever the previous line's cursor
    # happened to end, producing exactly the staggered/overlapping
    # garbage this normalization fixes.
    ansi_bytes = ansi_bytes.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(ansi_bytes.decode("utf-8", errors="replace"))

    font = _load_font()
    img = Image.new("RGB", (cols * CELL_W, rows * CELL_H), DEFAULT_BG)
    draw = ImageDraw.Draw(img)

    for row_idx in range(rows):
        row = screen.buffer[row_idx]
        for col_idx in range(cols):
            char = row[col_idx]
            data = char.data
            if not data:
                continue
            # A colored space is real content -- this project's `fill`
            # view IS its background color (solid blocks of colored
            # spaces, no glyphs at all). Skipping the bg-rect draw here
            # (an earlier version of this script did, by `continue`-ing
            # before ever reaching it) silently blanked that entire view
            # class -- caught by actually trying to screenshot it.
            fg = _resolve_color(char.fg, DEFAULT_FG)
            bg = _resolve_color(char.bg, None)
            x0, y0 = col_idx * CELL_W, row_idx * CELL_H
            if bg is not None:
                draw.rectangle([x0, y0, x0 + CELL_W, y0 + CELL_H], fill=bg)
            if data != " ":
                draw.text((x0, y0), data, fill=fg, font=font)

    img.save(out_path)
    return out_path


def _resolve_color(value, default):
    # pyte.Char.fg/bg is either a named ANSI color, "default", or a
    # "rrggbb" hex string once a 24-bit SGR sequence (38;2;r;g;b) has been
    # applied -- this project's terminal modules only ever emit 24-bit
    # truecolor SGR, so the hex-string branch is the one that matters.
    if value in (None, "default"):
        return default
    if isinstance(value, str) and len(value) == 6:
        try:
            return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return default
    return default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to a raw-ANSI transcript file")
    parser.add_argument("output", help="PNG output path")
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=60)
    args = parser.parse_args()

    with open(args.input, "rb") as f:
        data = f.read()

    path = render(data, args.cols, args.rows, args.output)
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
