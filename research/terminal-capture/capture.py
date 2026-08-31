"""Render any of note-color's terminal views' real ANSI output to a PNG via
pyte + Pillow, so a rendering bug (column misalignment, wrong glyph widths,
color mistakes) can be inspected as an image instead of squinted at as raw
escape-code text. See README.md in this directory for the rationale and a
known limitation.

Usage: .venv/bin/python research/terminal-capture/capture.py [--scene NAME] [--cols N] [--rows N] [--out PATH]
Scenes: tab, tab-chord, tab-name, fill, fill-bands, wheel, wheel-chord (default: all of them)
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
from terminal_display import TerminalDisplay  # noqa: E402
from terminal_wheel_display import WheelDisplay  # noqa: E402

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


def _with_terminal_size(cols, rows, fn):
    """shutil.get_terminal_size() checks $COLUMNS/$LINES before falling
    back to the real tty -- setting them lets every view's render() believe
    it's running in a cols x rows terminal with no real tty needed."""
    old_cols, old_lines = os.environ.get("COLUMNS"), os.environ.get("LINES")
    os.environ["COLUMNS"], os.environ["LINES"] = str(cols), str(rows)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            fn()
    finally:
        for key, val in (("COLUMNS", old_cols), ("LINES", old_lines)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
    return buf.getvalue()


# --- Scenes -----------------------------------------------------------

def _scene_tab(cols, rows, notehead_style="symbol", chord_mode=False):
    def build():
        display = TabDisplay(fps=20)
        if chord_mode:
            notes = [
                (0, 4, (200, 100, 100), "C4"),
                (4, 4, (100, 200, 100), "E4"),
                (7, 4, (100, 100, 200), "G4"),
            ]
            display.push_notes(notes, chord_name="CΔ")
            for pc, oc, _, _ in notes:
                display._open_notes[(pc, oc)]["duration_class"] = "quarter"
                display._open_notes.pop((pc, oc), None)
        else:
            notes = [(0, 4, "whole"), (7, 4, "quarter"), (2, 4, "eighth"), (9, 4, "dotted-sixteenth")]
            for pitch_class, octave, duration_class in notes:
                display.push(pitch_class, octave, (120, 120, 200), label=f"pc{pitch_class}")
                display._open_notes[(pitch_class, octave)]["duration_class"] = duration_class
                display._open_notes.pop((pitch_class, octave), None)
        display.push_barline()
        status = "note=A4  freq=440.0Hz  conf=0.95  rms=0.10  sens=1.0  src=mic  tempo=120  time=4/4"
        display.render(status, chord_mode=chord_mode, notehead_style=notehead_style, legend_on=True)
    return _with_terminal_size(cols, rows, build)


def _scene_fill(cols, rows):
    def build():
        display = TerminalDisplay(fps=20)
        display.render((180, 60, 90), status="note=A4  freq=440.0Hz  conf=0.95  rms=0.10  sens=1.0  src=mic")
    return _with_terminal_size(cols, rows, build)


def _scene_fill_bands(cols, rows):
    def build():
        display = TerminalDisplay(fps=20)
        display.render_bands(
            [(200, 100, 100), (100, 200, 100), (100, 100, 200)],
            status="chord=CΔ  sens=1.0  src=mic",
        )
    return _with_terminal_size(cols, rows, build)


def _scene_wheel(cols, rows):
    def build():
        display = WheelDisplay(fps=12)
        display.render(active_index=0, pulse=1.0, status="note=C4  freq=261.6Hz  conf=0.90  sens=1.0  src=mic")
    return _with_terminal_size(cols, rows, build)


def _scene_wheel_chord(cols, rows):
    def build():
        display = WheelDisplay(fps=12)
        fades = [0.0] * 12
        for pc in (0, 4, 7):
            fades[pc] = 1.0
        display.render_chord(fades, bass_pitch_class=0, status="chord=CΔ  sens=1.0  src=mic")
    return _with_terminal_size(cols, rows, build)


SCENES = {
    "tab": lambda c, r: _scene_tab(c, r),
    "tab-chord": lambda c, r: _scene_tab(c, r, chord_mode=True),
    "tab-name": lambda c, r: _scene_tab(c, r, notehead_style="name"),
    "fill": _scene_fill,
    "fill-bands": _scene_fill_bands,
    "wheel": _scene_wheel,
    "wheel-chord": _scene_wheel_chord,
}


# --- ANSI -> PNG --------------------------------------------------------

def _rgb(color_name, default):
    if color_name in (None, "default"):
        return default
    if len(color_name) == 6:
        try:
            return tuple(int(color_name[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return default
    return default


def render_to_image(ansi_text, cols, rows):
    # A real terminal is reached through a pty, whose line discipline
    # translates a bare "\n" to "\r\n" on output (the ONLCR flag, on by
    # default) -- that's what lets terminal_display.py's fill view get
    # away with "\n".join(out) with no explicit "\r" of its own. Feeding
    # pyte directly bypasses that pty layer, so a bare "\n" only moves the
    # cursor down without resetting its column (strict VT100 LF semantics),
    # corrupting any view that relies on the implicit CR -- confirmed by
    # reproducing exactly that as a striped/staircased fill_bands render
    # before adding this normalization.
    ansi_text = ansi_text.replace("\r\n", "\n").replace("\n", "\r\n")
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(ansi_text)

    mono_font = _load_font(MONO_FONT_CANDIDATES, 16)
    music_font = _load_font(MUSIC_FONT_CANDIDATES, 16)

    img = Image.new("RGB", (cols * CELL_W, rows * CELL_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    for y in range(rows):
        line = screen.buffer[y]
        for x in range(cols):
            cell = line[x]
            px, py = x * CELL_W, y * CELL_H
            if cell.bg not in (None, "default"):
                draw.rectangle([px, py, px + CELL_W, py + CELL_H], fill=_rgb(cell.bg, (0, 0, 0)))
            if cell.data and cell.data != " ":
                fg = _rgb(cell.fg, (230, 230, 230))
                font = music_font if ord(cell.data[0]) > 0x2FFF else mono_font
                draw.text((px, py), cell.data, font=font, fill=fg)
    return img


def capture(scene_name, cols, rows, out_path):
    ansi_text = SCENES[scene_name](cols, rows)
    img = render_to_image(ansi_text, cols, rows)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"Wrote {out_path} ({cols}x{rows} cells, {img.width}x{img.height}px)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=list(SCENES) + ["all"], default="all")
    parser.add_argument("--cols", type=int, default=80)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--out", default=None, help="only valid with a single --scene")
    args = parser.parse_args()

    out_dir = Path(__file__).parent / "output"
    scenes = list(SCENES) if args.scene == "all" else [args.scene]
    for name in scenes:
        out = args.out if (args.out and len(scenes) == 1) else str(out_dir / f"{name}.png")
        capture(name, args.cols, args.rows, out)
