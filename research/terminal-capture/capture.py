"""Render `TabDisplay`'s actual ANSI output to a PNG via pyte + Pillow, so a
rendering bug (column misalignment, wrong glyph widths, color mistakes) can
be inspected as an image instead of squinted at as raw escape-code text. See
README.md in this directory for the rationale and a known limitation.
"""
import argparse
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from terminal_tab_display import TabDisplay  # noqa: E402

CELL_W, CELL_H = 12, 22
MONO_FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFontMono-Regular.ttf",
]
MUSIC_FONT_CANDIDATES = [
    "/usr/share/fonts/noto/NotoMusic-Regular.ttf",
]


def _load_font(paths, size):
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _build_sample_display(cols, rows):
    """Push a handful of synthetic notes/durations/a barline deliberately
    exercising the exact bug class d7d2ea0 fixed: several different
    duration-glyph widths (whole/quarter/eighth/dotted-sixteenth -- 0 to 3
    combining marks each) on consecutive columns immediately ahead of a
    barline column, so any column-width miscount shows up as the barline
    drifting off the vertical."""
    os.environ["COLUMNS"] = str(cols)
    os.environ["LINES"] = str(rows)

    buf = io.StringIO()
    with redirect_stdout(buf):
        display = TabDisplay(fps=20)
        notes = [
            (0, 4, "whole"),       # C4, no stem/flag/dot at all
            (7, 4, "quarter"),     # G4, stem only
            (2, 4, "eighth"),      # D4, stem + 1 flag
            (9, 4, "dotted-sixteenth"),  # A4, stem + 2 flags + dot
        ]
        for pitch_class, octave, duration_class in notes:
            rgb = (120, 120, 200)
            display.push(pitch_class, octave, rgb, label=f"pc{pitch_class}", t=None)
            display._open_notes[(pitch_class, octave)]["duration_class"] = duration_class
            display._open_notes.pop((pitch_class, octave), None)
        display.push_barline()
        status = "note=A4  freq=440.0Hz  conf=0.95  rms=0.10  sens=1.0  src=mic  tempo=120  time=4/4"
        display.render(status, chord_mode=False, notehead_style="symbol", legend_on=True)
    return buf.getvalue()


def capture(cols, rows, out_path):
    ansi_text = _build_sample_display(cols, rows)

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(ansi_text)

    mono_font = _load_font(MONO_FONT_CANDIDATES, 16)
    music_font = _load_font(MUSIC_FONT_CANDIDATES, 16)

    img = Image.new("RGB", (cols * CELL_W, rows * CELL_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    def _rgb(color_name, default):
        if color_name in (None, "default"):
            return default
        if len(color_name) == 6:
            try:
                return tuple(int(color_name[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return default
        return default

    for y in range(rows):
        line = screen.buffer[y]
        for x in range(cols):
            cell = line[x]
            if not cell.data or cell.data == " ":
                continue
            bg = _rgb(cell.bg, (0, 0, 0))
            fg = _rgb(cell.fg, (230, 230, 230))
            px, py = x * CELL_W, y * CELL_H
            draw.rectangle([px, py, px + CELL_W, py + CELL_H], fill=bg)
            font = music_font if ord(cell.data[0]) > 0x2FFF else mono_font
            draw.text((px, py), cell.data, font=font, fill=fg)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"Wrote {out_path} ({cols}x{rows} cells, {img.width}x{img.height}px)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cols", type=int, default=80)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument(
        "--out", default=str(Path(__file__).parent / "output" / "sample_tab.png")
    )
    args = parser.parse_args()
    capture(args.cols, args.rows, args.out)
