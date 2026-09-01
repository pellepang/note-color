"""Tests for abc_export.py -- the hand-rolled ABC notation serializer
(Concept A / Feature 2 in docs/research/notation-and-feature-ideas.md).

Synthesizes short in-memory NoteEvent lists / session-log-shaped event
dicts directly (this repo's existing convention -- see
tests/test_batch_transcribe.py's make_tone()), no binary/file fixtures."""

from batch_transcribe import NoteEvent, TranscriptionResult
from abc_export import from_session_log, from_transcription_result, note_events_to_abc, write_abc


def _note_event(onset_hop, pitch_class, octave, duration_hops):
    return NoteEvent(
        onset_hop=onset_hop,
        onset_time=onset_hop * 0.01,
        pitch_class=pitch_class,
        octave=octave,
        duration_hops=duration_hops,
        chord_name=None,
    )


def test_note_events_to_abc_header_fields():
    columns = [[(0, 4, "quarter")]]
    text = note_events_to_abc(columns, title="Test Tune", time_signature=(3, 4), key="G", reference_number=7)
    lines = text.splitlines()
    assert lines[0] == "X:7"
    assert lines[1] == "T:Test Tune"
    assert lines[2] == "M:3/4"
    assert lines[3] == "L:1/4"
    assert lines[4] == "K:G"


def test_note_events_to_abc_plain_scale_matches_worked_example():
    # C D E F | G A B c | -- the exact worked example from
    # docs/research/notation-and-feature-ideas.md's Concept A section,
    # all quarter notes (bare pitch letters, no length suffix).
    columns = [
        [(0, 4, "quarter")], [(2, 4, "quarter")], [(4, 4, "quarter")], [(5, 4, "quarter")], "|",
        [(7, 4, "quarter")], [(9, 4, "quarter")], [(11, 4, "quarter")], [(0, 5, "quarter")], "|",
    ]
    text = note_events_to_abc(columns)
    body = text.splitlines()[-1]
    assert body == "C D E F | G A B c |"


def test_abc_pitch_accidentals_sharp_and_flat():
    # F#4 (pitch_class 6) -> sharp-spelled; Bb3 (pitch_class 10) -> flat-spelled.
    columns = [[(6, 4, "quarter")], [(10, 3, "quarter")]]
    body = note_events_to_abc(columns).splitlines()[-1]
    tokens = body.split(" ")
    assert tokens[0] == "^F"
    assert tokens[1] == "_B,"


def test_abc_pitch_octave_marks_above_and_below_reference():
    # Octave 5 -> lowercase with no comma/apostrophe; octave 6 -> one
    # apostrophe; octave 3 -> one comma; octave 2 -> two commas.
    columns = [
        [(0, 5, "quarter")], [(0, 6, "quarter")], [(0, 3, "quarter")], [(0, 2, "quarter")],
    ]
    tokens = note_events_to_abc(columns).splitlines()[-1].split(" ")
    assert tokens[0] == "c"
    assert tokens[1] == "c'"
    assert tokens[2] == "C,"
    assert tokens[3] == "C,,"


def test_abc_duration_digits_eighth_half_whole():
    columns = [[(0, 4, "eighth")], [(0, 4, "half")], [(0, 4, "whole")]]
    tokens = note_events_to_abc(columns).splitlines()[-1].split(" ")
    assert tokens[0] == "C/2"
    assert tokens[1] == "C2"
    assert tokens[2] == "C4"


def test_polyphonic_column_renders_as_chord_bracket_with_longest_duration():
    # Two simultaneous notes, different durations -- the bracket shares
    # the *longest* member's duration (mirrors run_batch_transcribe()'s
    # own column_beats = max(...) convention).
    columns = [[(0, 4, "quarter"), (7, 4, "half")]]
    body = note_events_to_abc(columns).splitlines()[-1]
    assert body == "[CG]2 |"


def test_from_transcription_result_groups_by_onset_hop_and_places_barline():
    # bpm=60, hop_seconds=0.01 -> 1 beat = 100 hops exactly, so four
    # quarter notes in 4/4 should land one barline right after the 4th.
    events = [
        _note_event(0, 0, 4, 100),    # C4 quarter
        _note_event(100, 2, 4, 100),  # D4 quarter
        _note_event(200, 4, 4, 100),  # E4 quarter
        _note_event(300, 5, 4, 100),  # F4 quarter
    ]
    result = TranscriptionResult(notes=events, mono_notes=[], bpm=60.0, hop_seconds=0.01, chroma_histogram=None)
    columns = from_transcription_result(result, time_signature=(4, 4))
    assert columns == [
        [(0, 4, "quarter")], [(2, 4, "quarter")], [(4, 4, "quarter")], [(5, 4, "quarter")], "|",
    ]


def test_from_transcription_result_groups_simultaneous_onset_hop_as_one_column():
    events = [
        _note_event(0, 0, 4, 100),  # C4
        _note_event(0, 4, 4, 100),  # E4, same onset_hop -- a chord
    ]
    result = TranscriptionResult(notes=events, mono_notes=[], bpm=60.0, hop_seconds=0.01, chroma_histogram=None)
    columns = from_transcription_result(result, time_signature=(4, 4))
    assert len(columns) == 2  # one note-column + its trailing barline
    assert set(columns[0]) == {(0, 4, "quarter"), (4, 4, "quarter")}
    assert columns[1] == "|"


def test_from_session_log_groups_shared_t_and_places_barline():
    events = [
        {"t": 0.0, "pc": 0, "octave": 4, "duration_class": "quarter"},
        {"t": 1.0, "pc": 2, "octave": 4, "duration_class": "quarter"},
        {"t": 2.0, "pc": 4, "octave": 4, "duration_class": "quarter"},
        {"t": 3.0, "pc": 5, "octave": 4, "duration_class": "quarter"},
    ]
    columns = from_session_log(events, time_signature=(4, 4))
    assert columns == [
        [(0, 4, "quarter")], [(2, 4, "quarter")], [(4, 4, "quarter")], [(5, 4, "quarter")], "|",
    ]


def test_from_session_log_groups_same_t_chord_tones_into_one_column():
    events = [
        {"t": 0.0, "pc": 0, "octave": 4, "duration_class": "quarter"},
        {"t": 0.0, "pc": 4, "octave": 4, "duration_class": "quarter"},
        {"t": 0.0, "pc": 7, "octave": 4, "duration_class": "quarter"},
    ]
    columns = from_session_log(events, time_signature=(4, 4))
    assert len(columns) == 2
    assert set(columns[0]) == {(0, 4, "quarter"), (4, 4, "quarter"), (7, 4, "quarter")}


def test_write_abc_writes_the_same_text_it_returns(tmp_path):
    columns = [[(0, 4, "quarter")], [(4, 4, "quarter")], "|"]
    path = tmp_path / "out.abc"
    returned = write_abc(columns, str(path), title="Round Trip", time_signature=(4, 4))
    assert path.read_text(encoding="utf-8") == returned
    assert "T:Round Trip" in returned
    assert returned.splitlines()[-1] == "C E |"


def test_note_events_to_abc_never_produces_doubled_barlines():
    columns = [[(0, 4, "quarter")], "|", "|"]
    body = note_events_to_abc(columns).splitlines()[-1]
    assert "| |" not in body
