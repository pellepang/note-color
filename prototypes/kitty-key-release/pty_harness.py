#!/usr/bin/env python3
"""PROTOTYPE -- throwaway, wayfinder ticket #101 (map #99, sound engine).

Runs `demo.py` under a **simulated terminal**: a real pty whose master
side this script drives, answering the negotiation probe and then typing
synthetic kitty key events at it. Two passes:

    1. a terminal that claims kitty support -- press / repeat / release
    2. a terminal that answers only DA1 -- the fallback path

This proves the whole stack end to end over real file descriptors,
including cbreak entry and the escape sequences actually written out. What
it can NOT prove is what a *real* kitty puts on the wire when a human
holds a key down -- for that, run `demo.py` in kitty by hand. This harness
tests the code against the specification; the human tests the
specification against kitty.

    cd ~/note-color
    .venv/bin/python prototypes/kitty-key-release/pty_harness.py
"""

import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
_VENV = REPO / ".venv" / "bin" / "python"
PYTHON = str(_VENV) if _VENV.exists() else sys.executable


def drain(fd, seconds):
    out = b""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if select.select([fd], [], [], remaining)[0]:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


def run(label, reply, script):
    print("=" * 72)
    print(label)
    print("=" * 72)
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [PYTHON, str(HERE / "demo.py")],
        stdin=slave, stdout=slave, stderr=subprocess.PIPE, cwd=str(REPO),
    )
    os.close(slave)
    seen = b""
    try:
        # The probe: demo.py writes CSI ? u  CSI c and waits for us --
        # so answer as soon as it arrives, not after a fixed sleep, or the
        # negotiation times out exactly as it would against a dead pty.
        deadline = time.monotonic() + 2.0
        while b"\x1b[c" not in seen and time.monotonic() < deadline:
            seen += drain(master, 0.02)
        probe_ok = b"\x1b[?u" in seen and b"\x1b[c" in seen
        print("  probe written by demo.py:      %s" % ("yes" if probe_ok else "NO"))
        os.write(master, reply)
        seen += drain(master, 0.4)
        pushed = re.search(rb"\x1b\[>(\d+)u", seen)
        print("  pushed keyboard flags:         %s"
              % (pushed.group(1).decode() if pushed else "none (fallback)"))
        for chunk, pause in script:
            os.write(master, chunk)
            seen += drain(master, pause)
        seen += drain(master, 0.3)
        snapshot = seen  # the live screen, before the quit key clears it
        os.write(master, b"\x1b[113;1:1u" if pushed else b"q")
        seen += drain(master, 0.4)
        popped = b"\x1b[<1u" in seen
        print("  popped keyboard mode on exit:  %s"
              % ("yes" if popped else ("no (nothing was pushed)" if not pushed else "NO")))
    finally:
        proc.wait(timeout=5)
        os.close(master)
    text = seen.decode("utf-8", "ignore")
    print("  demo.py exit status:           %d" % proc.returncode)
    print("  screen at the end of the script:")
    frame = snapshot.decode("utf-8", "ignore").split("\x1b[H")[-1]
    for line in frame.splitlines():
        stripped = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).rstrip()
        if stripped:
            print("    | " + stripped)
    print()
    return text


def main():
    # Pass 1: a terminal that supports the protocol.
    kitty_script = [
        (b"\x1b[97;1:1u", 0.15),                    # a down
        (b"\x1b[100;1:1u", 0.15),                   # d down
        (b"\x1b[97;1:2u\x1b[97;1:2u", 0.15),        # a auto-repeats
        (b"\x1b[103;1:1u", 0.15),                   # g down  -> a+d+g held
    ]
    text = run("PASS 1 -- terminal reports kitty support (flags=27)",
               b"\x1b[?27u\x1b[?62;c", kitty_script)
    assert "kitty keyboard protocol" in text, "negotiation did not report kitty"
    assert "press" in text and "repeat" in text, "press/repeat not decoded"

    # ...and now release them out of order, in a second run so the frame
    # printed above is the "three notes held" one.
    text = run("PASS 2 -- same, then releases out of order",
               b"\x1b[?27u",
               kitty_script + [
                   (b"\x1b[100;1:3u", 0.12),        # d up
                   (b"\x1b[97;1:3u", 0.12),         # a up
                   (b"\x1b[103;1:3u", 0.12),        # g up  -> silence
               ])
    assert "release" in text, "release events not decoded"
    assert "(silence)" in text, "notes did not stop on release"

    # Pass 3: a terminal that ignores the query entirely.
    text = run("PASS 3 -- terminal answers DA1 only (no kitty support)",
               b"\x1b[?62;1;6c",
               [(b"a", 0.1), (b"d", 0.1), (b"a", 0.1)])
    assert "fallback" in text, "fallback banner missing"
    assert "release" not in text, "a non-kitty terminal reported releases?"

    print("All simulated-terminal passes behaved as specified.")
    print("Still unverified: what real kitty sends. Run demo.py there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
