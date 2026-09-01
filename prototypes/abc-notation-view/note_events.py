"""Sample note-event data for the ABC-notation-view prototype: a short
synthesized melody spanning octaves 3-5, mixing several duration classes
(quarter/eighth/dotted-quarter/half/whole/sixteenth), a rest, and both a
flat- and a sharp-spelled accidental (Bb3, F#4) -- enough variety to
exercise `abc_convert.py`'s pitch/accidental/octave/duration handling and
give the terminal preview something visually interesting to place across
a couple of octaves.

Two representations are provided:

- `MELODY`: hand-built `abc_convert.ProtoNote` events directly (what
  `note_events_to_abc()` consumes) -- four 4/4 bars.
- `SAMPLE_NOTE_EVENTS` + `SAMPLE_HOP_SECONDS`/`SAMPLE_BPM`: the *same*
  melody expressed as real `batch_transcribe.NoteEvent` tuples (the shape
  this would actually consume from `batch_transcribe.transcribe()` output
  in a real integration), run through `abc_convert.from_note_events()` to
  prove the adapter reproduces the same `ProtoNote` list -- see
  `run_demo.py`, which asserts the two match.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for batch_transcribe

from batch_transcribe import NoteEvent

from abc_convert import ProtoNote

# Bar 1 (4 beats): C4 qtr, D4 qtr, E4 8th, F4 8th, G4 qtr
# Bar 2 (4 beats): A4 dotted-qtr, G4 8th, F#4 qtr, rest qtr
# Bar 3 (4 beats): C5 half, Bb3 qtr, D3 qtr
# Bar 4 (4 beats): E4 whole
MELODY = [
    ProtoNote(0, 4, "quarter"),   # C4
    ProtoNote(2, 4, "quarter"),   # D4
    ProtoNote(4, 4, "eighth"),    # E4
    ProtoNote(5, 4, "eighth"),    # F4
    ProtoNote(7, 4, "quarter"),   # G4

    ProtoNote(9, 4, "dotted-quarter"),  # A4
    ProtoNote(7, 4, "eighth"),          # G4
    ProtoNote(6, 4, "quarter"),         # F#4 (sharp-spelled accidental)
    None,                                # rest, quarter

    ProtoNote(0, 5, "half"),      # C5
    ProtoNote(10, 3, "quarter"),  # Bb3 (flat-spelled accidental)
    ProtoNote(2, 3, "quarter"),   # D3

    ProtoNote(4, 4, "whole"),     # E4
]

# ---------------------------------------------------------------------
# Same melody, as real batch_transcribe.NoteEvent tuples -- demonstrates
# from_note_events()'s duration_hops -> duration_class derivation matches
# MELODY's hand-picked duration_class values exactly, given a consistent
# hop_seconds/bpm (see run_demo.py's cross-check).
SAMPLE_BPM = 120.0
SAMPLE_HOP_SECONDS = 0.0116099773  # config.BLOCK_SIZE / config.SAMPLE_RATE, hardcoded
                                    # here to keep this file import-light


def _hops_for_beats(beats):
    beat_seconds = 60.0 / SAMPLE_BPM
    return round(beats * beat_seconds / SAMPLE_HOP_SECONDS)


_BEATS = {
    "quarter": 1.0, "eighth": 0.5, "dotted-quarter": 1.5,
    "half": 2.0, "whole": 4.0, "sixteenth": 0.25,
}

SAMPLE_NOTE_EVENTS = []
_hop = 0
_t = 0.0
for _note in MELODY:
    if _note is not None:
        _beats = _BEATS[_note.duration_class]
        _hops = _hops_for_beats(_beats)
        SAMPLE_NOTE_EVENTS.append(
            NoteEvent(
                onset_hop=_hop,
                onset_time=_t,
                pitch_class=_note.pitch_class,
                octave=_note.octave,
                duration_hops=_hops,
                chord_name=None,
            )
        )
        _hop += _hops
        _t += _hops * SAMPLE_HOP_SECONDS
    else:
        _hops = _hops_for_beats(1.0)
        _hop += _hops
        _t += _hops * SAMPLE_HOP_SECONDS
