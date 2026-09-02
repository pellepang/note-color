"""A hand-built `batch_transcribe.TranscriptionResult`, synthesized (no
binary fixtures) -- same convention `tests/test_score_writer.py` and
`tests/test_batch_transcribe.py` already use. Spans a short melody plus one
chord, a clear key profile (so `guess_key_signature()` returns a real key,
not None), and a real bpm (so tempo/offset math both exercise their normal
path, not their None-fallback path) -- deliberately built to exercise every
one of `score_writer.py`'s features at once: solo notes, a same-staff
chord, a bass-register note, varied durations, a guessed key signature, and
a non-default time signature.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for config/batch_transcribe

import numpy as np

import config
from batch_transcribe import NoteEvent, TranscriptionResult

BPM = 132.0  # deliberately not 120 (music21's/MIDI's own default tempo), so the demo's
# tempo-survival check can actually distinguish "the real bpm was written" from
# "nothing was written and it silently fell back to the format default."
HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE


def _hops_for_beats(beats):
    seconds = beats * 60.0 / BPM
    return max(1, round(seconds / HOP_SECONDS))


def _hop_at_beat(beat):
    seconds = beat * 60.0 / BPM
    return round(seconds / HOP_SECONDS)


# Krumhansl-Kessler C-major profile, unrotated -- the same clean signal
# tests/test_score_writer.py uses to get a deterministic, confident C-major
# key guess out of guess_key_signature().
C_MAJOR_CHROMA_HISTOGRAM = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)

# A short melody (C4 D4 E4 F4) + a C-major triad on beat 4 (chord mode) +
# a solo bass note (C2) an octave+ down, landing on the bass staff.
SAMPLE_NOTES = [
    NoteEvent(onset_hop=_hop_at_beat(0.0), onset_time=0.0, pitch_class=0, octave=4,
              duration_hops=_hops_for_beats(1.0), chord_name=None),  # C4, quarter
    NoteEvent(onset_hop=_hop_at_beat(1.0), onset_time=1.0 * 60.0 / BPM, pitch_class=2, octave=4,
              duration_hops=_hops_for_beats(0.5), chord_name=None),  # D4, eighth
    NoteEvent(onset_hop=_hop_at_beat(1.5), onset_time=1.5 * 60.0 / BPM, pitch_class=4, octave=4,
              duration_hops=_hops_for_beats(0.5), chord_name=None),  # E4, eighth
    NoteEvent(onset_hop=_hop_at_beat(2.0), onset_time=2.0 * 60.0 / BPM, pitch_class=5, octave=4,
              duration_hops=_hops_for_beats(2.0), chord_name=None),  # F4, half
    # C-major triad, all three sharing one onset -- becomes one music21
    # Chord (same-staff simultaneous notes, per score_writer.py's own
    # <chord/> grouping).
    NoteEvent(onset_hop=_hop_at_beat(4.0), onset_time=4.0 * 60.0 / BPM, pitch_class=0, octave=4,
              duration_hops=_hops_for_beats(1.0), chord_name="C"),
    NoteEvent(onset_hop=_hop_at_beat(4.0), onset_time=4.0 * 60.0 / BPM, pitch_class=4, octave=4,
              duration_hops=_hops_for_beats(1.0), chord_name="C"),
    NoteEvent(onset_hop=_hop_at_beat(4.0), onset_time=4.0 * 60.0 / BPM, pitch_class=7, octave=4,
              duration_hops=_hops_for_beats(1.0), chord_name="C"),
    # Solo bass note, below middle C -- lands on the bass staff/part
    # (staff_map.staff_row() < 10).
    NoteEvent(onset_hop=_hop_at_beat(5.0), onset_time=5.0 * 60.0 / BPM, pitch_class=0, octave=2,
              duration_hops=_hops_for_beats(4.0), chord_name=None),  # C2, whole
]

SAMPLE_RESULT = TranscriptionResult(
    notes=SAMPLE_NOTES,
    mono_notes=[],
    bpm=BPM,
    hop_seconds=HOP_SECONDS,
    chroma_histogram=C_MAJOR_CHROMA_HISTOGRAM,
)
