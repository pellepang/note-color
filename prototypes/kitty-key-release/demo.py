#!/usr/bin/env python3
"""PROTOTYPE -- throwaway, wayfinder ticket #101 (map #99, sound engine).

Live harness. Run this **in kitty** (and then again in any other terminal)
and play the QWERTY piano row: every key press, auto-repeat and release is
decoded and printed, and a held key holds its note bar open until you let
go. That is the one thing no unit test in this repo can establish, since
it depends on what a real terminal actually puts on the wire.

    cd ~/note-color
    .venv/bin/python prototypes/kitty-key-release/demo.py

Keys:
    a w s e d f t g y h u j k   one octave, piano-layout (white/black rows)
    q                           quit  (Ctrl+C also works)
    f1 / any other key          decoded and shown too, just not played

What to look for:
    * `NEGOTIATED` line at the top -- "kitty (flags=N)" or "fallback".
    * Hold one key: exactly one `press`, then a stream of `repeat`, then
      one `release`. The note bar stays lit for the whole hold.
    * Hold three keys at once and let go in a different order: three
      independent bars, each closing on its own release.
    * In a terminal *without* the protocol: the header says fallback, no
      `release` events ever appear, and notes end on a timer instead --
      which is the degradation this ticket had to prove is clean.

No audio: this prototype answers the *input* question only. Colors are the
real app's fifths palette, imported read-only from `color_map.py`.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

import kitty_keys as kk
import kitty_rawkeys as kr
from color_map import hsl_to_rgb255, note_to_hsl

#: QWERTY piano, one octave from C4 -- the two-row layout every tracker
#: and soft-synth uses, so muscle memory transfers.
KEY_TO_SEMITONE = {
    "a": 0, "w": 1, "s": 2, "e": 3, "d": 4, "f": 5, "t": 6,
    "g": 7, "y": 8, "h": 9, "u": 10, "j": 11, "k": 12,
}
NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
BASE_OCTAVE = 4

FALLBACK_DURATION = 0.35  # seconds a note lasts with no release available
LOG_LINES = 14


def note_for(key):
    semitone = KEY_TO_SEMITONE.get(key)
    if semitone is None:
        return None
    pitch_class = semitone % 12
    octave = BASE_OCTAVE + semitone // 12
    return NOTE_NAMES[pitch_class], octave, pitch_class


def ansi(pitch_class):
    h, s, lightness = note_to_hsl(pitch_class, octave=4, scheme="fifths")
    r, g, b = hsl_to_rgb255(h, s, lightness)
    return "\x1b[38;2;%d;%d;%dm" % (r, g, b)


RESET = "\x1b[0m"
DIM = "\x1b[2m"


def main():
    keys = kr.KittyRawKeys()
    if not keys.active:
        print("stdin is not a TTY -- run this in a real terminal.")
        return 1
    held = kk.HeldKeys()
    fallback = kk.FixedDurationKeys(duration=FALLBACK_DURATION)
    sounding = {}  # key -> (note, octave, pitch_class, started_at)
    log = []

    def note_on(key):
        info = note_for(key)
        if info:
            sounding[key] = info + (time.monotonic(),)

    def note_off(key):
        sounding.pop(key, None)

    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25l")
    try:
        while True:
            event = keys.poll_event()
            if event is not None:
                if event.key == "q" and event.event == kk.PRESS:
                    break
                log.append(event)
                del log[:-LOG_LINES]
                if keys.kitty:
                    actions = held.apply(event)
                else:
                    actions = fallback.apply(event, time.monotonic())
                for action, key in actions:
                    (note_on if action == kk.NOTE_ON else note_off)(key)
            if not keys.kitty:
                for _, key in fallback.expire(time.monotonic()):
                    note_off(key)
            render(keys, log, sounding)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()
        keys.restore()
    return 0


def render(keys, log, sounding):
    out = ["\x1b[H"]
    if keys.kitty:
        mode = "\x1b[32mkitty keyboard protocol\x1b[0m (flags=%s)" % keys.kitty_flags
        policy = "held notes: press/release"
    else:
        mode = "\x1b[33mfallback\x1b[0m (negotiation=%s)" % keys.negotiation
        policy = "fixed-duration notes: %.2fs" % FALLBACK_DURATION
    out.append("\x1b[K  NEGOTIATED  %s -- %s\n" % (mode, policy))
    out.append("\x1b[K  %skeys a w s e d f t g y h u j k  |  q quits%s\n\n"
               % (DIM, RESET))

    out.append("\x1b[K  SOUNDING\n")
    if not sounding:
        out.append("\x1b[K    %s(silence)%s\n" % (DIM, RESET))
    else:
        now = time.monotonic()
        for key in sorted(sounding, key=lambda k: KEY_TO_SEMITONE.get(k, 99)):
            note, octave, pitch_class, started = sounding[key]
            bar = "#" * min(40, int((now - started) / 0.02))
            out.append("\x1b[K    %s%-3s %s%-2d  %s%s\n"
                       % (ansi(pitch_class), key, note, octave, bar, RESET))
    for _ in range(6 - len(sounding)):
        out.append("\x1b[K\n")

    out.append("\x1b[K\n\x1b[K  EVENTS (newest last)\n")
    for _ in range(LOG_LINES - len(log)):
        out.append("\x1b[K\n")
    for event in log:
        color = {kk.PRESS: "\x1b[32m", kk.REPEAT: "\x1b[34m",
                 kk.RELEASE: "\x1b[31m"}[event.event]
        out.append("\x1b[K    %s%-7s%s key=%-12s mods=%-3d text=%-6r cp=%d\n"
                   % (color, event.event, RESET, event.key, event.mods,
                      event.text, event.codepoint))
    out.append("\x1b[J")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
