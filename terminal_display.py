"""Terminal-only display: fills the terminal with a live ANSI truecolor
background, no GUI window or display server required."""

import shutil
import sys


class TerminalDisplay:
    def __init__(self, fps=20):
        self.fps = fps
        self._last_size = None
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.write("\033[2J")    # clear screen once
        sys.stdout.flush()

    def render(self, rgb, status=""):
        r, g, b = rgb
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size
        rows = max(rows - 1, 1)  # reserve the last row for status text

        bg = f"\033[48;2;{r};{g};{b}m"
        reset = "\033[0m"
        block_line = bg + (" " * cols) + reset

        fg = f"\033[38;2;{r};{g};{b}m"

        # A tiling WM resizes the terminal window often; without a full
        # clear on resize, stale content from the previous (larger) size
        # is never overwritten and lingers as ghost/duplicated pixels.
        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

        out = ["\033[H"]  # cursor home (avoids full-clear flicker on unchanged size)
        out.extend([block_line] * rows)
        out.append(reset + "\033[K" + fg + status + reset)
        sys.stdout.write(clear + "\n".join(out))
        sys.stdout.flush()

    def render_bands(self, rgbs, status=""):
        """Chord mode: `rgbs` is a list of RGB tuples, bottom-to-top, one
        per currently-active note -- splits the fill area into that many
        proportional horizontal bands instead of one solid color. A
        single-entry list reduces to exactly `render()`'s behavior."""
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size
        rows = max(rows - 1, 1)

        n = len(rgbs)
        base, remainder = divmod(rows, n)
        counts = [base + (1 if i < remainder else 0) for i in range(n)]

        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

        reset = "\033[0m"
        lines = []
        for band_rgb, band_rows in reversed(list(zip(rgbs, counts))):
            r, g, b = band_rgb
            bg = f"\033[48;2;{r};{g};{b}m"
            block_line = bg + (" " * cols) + reset
            lines.extend([block_line] * band_rows)

        top_r, top_g, top_b = rgbs[-1]
        fg = f"\033[38;2;{top_r};{top_g};{top_b}m"

        out = ["\033[H"]
        out.extend(lines)
        out.append(reset + "\033[K" + fg + status + reset)
        sys.stdout.write(clear + "\n".join(out))
        sys.stdout.flush()

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
