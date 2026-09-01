"""End-to-end runner for the ABC-notation-view prototype (issue-#N-style
research demo -- see README.md). Runs, in order:

1. `note_events.MELODY` -> ABC text (`abc_convert.note_events_to_abc()`),
   with `validate=True` round-tripping it back through music21 to confirm
   it's well-formed.
2. A cross-check that `abc_convert.from_note_events()` (given the
   equivalent real `batch_transcribe.NoteEvent` list, `note_events.
   SAMPLE_NOTE_EVENTS`) reproduces the exact same `ProtoNote` list as the
   hand-built `MELODY` -- proving this prototype's data shape really is
   compatible with the real batch-transcription pipeline's output, not
   just superficially similar.
3. The terminal preview of that ABC text (`abc_terminal_preview.
   render_preview()`).
4. The edit round-trip demo (`edit_demo.main()`), which writes `output/
   melody.abc`, performs a plain-text edit, writes `output/
   melody_edited.abc`, and re-renders.

Run: `.venv/bin/python prototypes/abc-notation-view/run_demo.py`
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from abc_convert import from_note_events, note_events_to_abc, with_barlines
from abc_terminal_preview import render_preview
from note_events import MELODY, SAMPLE_BPM, SAMPLE_HOP_SECONDS, SAMPLE_NOTE_EVENTS


def _hr(title):
    print()
    print("#" * 78)
    print(f"# {title}")
    print("#" * 78)


def main():
    _hr("Step 1 -- note events -> ABC text (music21-validated)")
    events = with_barlines(MELODY, beats_per_bar=4)
    abc_text = note_events_to_abc(
        events, title="ABC prototype demo", time_signature=(4, 4), key="C", validate=True
    )
    print("music21 validated the generated ABC text (parsed back without error).")
    print()
    print(abc_text)

    _hr("Step 2 -- cross-check: batch_transcribe.NoteEvent -> ProtoNote adapter")
    expected = [n for n in MELODY if n is not None]
    adapted = from_note_events(SAMPLE_NOTE_EVENTS, SAMPLE_HOP_SECONDS, SAMPLE_BPM)
    ok = expected == adapted
    print(f"from_note_events(SAMPLE_NOTE_EVENTS, ...) == [n for n in MELODY if n is not None]: {ok}")
    if not ok:
        print("MISMATCH:")
        for e, a in zip(expected, adapted):
            marker = "" if e == a else "  <-- differs"
            print(f"  expected={e}  adapted={a}{marker}")
        sys.exit(1)

    _hr("Step 3 -- terminal preview")
    render_preview(abc_text)

    _hr("Step 4 -- edit round-trip demo")
    import edit_demo
    edit_demo.main()

    _hr("Done")
    print("All steps completed without error.")


if __name__ == "__main__":
    main()
