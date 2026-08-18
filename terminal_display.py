"""Terminal-only display: fills the terminal with a live ANSI truecolor
background, no GUI window or display server required."""

import shutil
import sys


class TerminalDisplay:
    def __init__(self, fps=20):
        self.fps = fps
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.write("\033[2J")    # clear screen once
        sys.stdout.flush()

    def render(self, rgb, status=""):
        r, g, b = rgb
        cols, rows = shutil.get_terminal_size(fallback=(80, 24))
        rows = max(rows - 1, 1)  # reserve the last row for status text

        bg = f"\033[48;2;{r};{g};{b}m"
        reset = "\033[0m"
        block_line = bg + (" " * cols) + reset

        fg = f"\033[38;2;{r};{g};{b}m"

        out = ["\033[H"]  # cursor home (avoids full-clear flicker)
        out.extend([block_line] * rows)
        out.append(reset + "\033[K" + fg + status + reset)
        sys.stdout.write("\n".join(out))
        sys.stdout.flush()

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()
