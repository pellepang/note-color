"""Edit round-trip demo (prototype, research demo for docs/research/
notation-and-feature-ideas.md's Concept A): write a short piece out as
ABC text to a .abc file, hand-edit it as *plain text* (a literal
`str.replace()` on the file contents -- no dict/index bookkeeping), then
re-parse and re-render, proving the edit took effect.

This is the concrete "simpler to hand-edit" claim from the research doc
made real: compare the one-line `text.replace(...)` this script performs
to what the same edit ("change this note's pitch/duration") would require
against `terminal_tab_display.TabDisplay`'s live data model today --
walking `self.session_history` or `self._open_notes` to find the right
mutable per-note dict (the same lookup `correct_duration()` already has to
do, disambiguating by closest timestamp since there's no per-note id --
see that method's docstring), then mutating specific dict keys in place.
Here it's: open the file, replace a substring, save.

Run: `.venv/bin/python edit_demo.py [--find TOKEN] [--replace TOKEN]`
Defaults to a canned edit if neither flag is given (see DEFAULT_FIND/
DEFAULT_REPLACE below).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from abc_convert import note_events_to_abc, with_barlines
from abc_terminal_preview import render_preview
from note_events import MELODY

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
ORIGINAL_PATH = OUTPUT_DIR / "melody.abc"
EDITED_PATH = OUTPUT_DIR / "melody_edited.abc"

# The canned edit: bar 2's "G/2" (G4, eighth note) becomes "A/2" (A4,
# still an eighth note) -- a pure pitch change via one substring replace,
# deliberately picking a token specific enough not to collide with any
# other "G/2" elsewhere in the piece. Kept duration-preserving on purpose:
# see README.md's "Friction encountered" section for what happens (a real,
# reproduced finding) when an edit changes a note's *duration* instead --
# ABC has no structural check that a bar's tokens still sum to the meter,
# so an unbalanced edit still parses, but music21's reader silently
# re-bars/splits the overflowing note rather than rejecting it.
DEFAULT_FIND = "G/2 ^F"
DEFAULT_REPLACE = "A/2 ^F"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--find", default=DEFAULT_FIND, help="substring to replace in the .abc body")
    parser.add_argument("--replace", default=DEFAULT_REPLACE, help="replacement substring")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    events = with_barlines(MELODY, beats_per_bar=4)
    original_abc = note_events_to_abc(events, title="ABC prototype demo", time_signature=(4, 4), key="C")
    ORIGINAL_PATH.write_text(original_abc)

    print(f"Wrote {ORIGINAL_PATH}")
    print("=" * 70)
    print(original_abc)
    print("--- terminal preview (original) ---")
    render_preview(original_abc)

    # --- The actual edit: plain string editing of the .abc file, nothing
    # more. This is the whole "edit" step -- read the file, replace text,
    # write it back.
    text = ORIGINAL_PATH.read_text()
    if args.find not in text:
        print(f"\n(!) --find token {args.find!r} not present in the ABC body -- no edit applied.")
        edited_text = text
    else:
        edited_text = text.replace(args.find, args.replace, 1)
    EDITED_PATH.write_text(edited_text)

    print()
    print("=" * 70)
    print(f"Edited {args.find!r} -> {args.replace!r}, wrote {EDITED_PATH}")
    print("=" * 70)
    print(edited_text)
    print("--- terminal preview (edited) ---")
    render_preview(edited_text)


if __name__ == "__main__":
    main()
