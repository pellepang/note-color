"""Tests for session_score.py (issue #89: `virtualnote replay --write-score`).

Synthesizes session-log-shaped event dicts directly (this repo's existing
convention -- see tests/test_abc_export.py's from_session_log() tests,
tests/test_session_player.py), no binary/file fixtures. Mirrors
tests/test_score_writer.py's "build a small TranscriptionResult-shaped
object, then verify via music21.converter.parse()" pattern for the one
integration test confirming score_writer.write_score() accepts
from_session_log()'s output without any changes to that module."""

from music21 import converter

from session_score import HOP_SECONDS, from_session_log


def _event(t, pc, octave, duration_seconds, bpm_estimate=None, chord_name=None):
    """A session_recorder.py-shaped event dict, trimmed to the fields
    from_session_log() actually reads (t, pc, octave, duration_seconds,
    bpm_estimate, chord_name) -- label/duration_hops/duration_class are
    real fields in an actual log but unused by this adapter."""
    return {
        "t": t,
        "pc": pc,
        "octave": octave,
        "duration_seconds": duration_seconds,
        "bpm_estimate": bpm_estimate,
        "chord_name": chord_name,
    }


def test_hop_seconds_is_fixed_at_one_so_onset_hop_and_duration_hops_are_real_seconds():
    assert HOP_SECONDS == 1.0


def test_reconstructs_two_solo_notes_with_onset_and_duration_as_real_seconds():
    events = [
        _event(0.0, 0, 4, 0.5, bpm_estimate=120.0),
        _event(0.5, 4, 4, 0.25, bpm_estimate=120.0),
    ]

    result = from_session_log(events)

    assert len(result.notes) == 2
    first, second = result.notes
    assert first.onset_hop == 0.0
    assert first.onset_time == 0.0
    assert first.pitch_class == 0
    assert first.octave == 4
    assert first.duration_hops == 0.5
    assert second.onset_hop == 0.5
    assert second.pitch_class == 4
    assert second.duration_hops == 0.25
    assert result.hop_seconds == 1.0
    assert result.mono_notes == []


def test_chord_tones_sharing_one_t_all_land_at_the_same_onset_hop_with_the_shared_chord_name():
    events = [
        _event(1.0, 0, 3, 1.0, bpm_estimate=100.0, chord_name="C"),
        _event(1.0, 4, 3, 1.0, bpm_estimate=100.0, chord_name="C"),
        _event(1.0, 7, 3, 1.0, bpm_estimate=100.0, chord_name="C"),
    ]

    result = from_session_log(events)

    assert len(result.notes) == 3
    assert all(note.onset_hop == 1.0 for note in result.notes)
    assert all(note.chord_name == "C" for note in result.notes)
    assert sorted(note.pitch_class for note in result.notes) == [0, 4, 7]


def test_bpm_is_the_median_of_logged_non_null_bpm_estimates():
    events = [
        _event(0.0, 0, 4, 0.5, bpm_estimate=100.0),
        _event(0.5, 2, 4, 0.5, bpm_estimate=None),
        _event(1.0, 4, 4, 0.5, bpm_estimate=140.0),
    ]

    result = from_session_log(events)

    assert result.bpm == 120.0


def test_bpm_is_none_when_no_event_carries_a_bpm_estimate():
    events = [
        _event(0.0, 0, 4, 0.5, bpm_estimate=None),
        _event(0.5, 2, 4, 0.5, bpm_estimate=None),
    ]

    result = from_session_log(events)

    assert result.bpm is None


def test_chroma_histogram_sums_duration_weighted_pitch_class_across_mono_and_chord_tones():
    events = [
        _event(0.0, 0, 4, 2.0),  # pitch class 0, sounded 2.0s
        _event(2.0, 0, 4, 1.0),  # pitch class 0 again, sounded 1.0s more
        _event(3.0, 7, 4, 0.5),  # pitch class 7, sounded 0.5s
    ]

    result = from_session_log(events)

    assert len(result.chroma_histogram) == 12
    assert result.chroma_histogram[0] == 3.0
    assert result.chroma_histogram[7] == 0.5
    assert sum(result.chroma_histogram) == 3.5


def test_empty_log_produces_an_empty_but_valid_result():
    result = from_session_log([])

    assert result.notes == []
    assert result.mono_notes == []
    assert result.bpm is None
    assert sum(result.chroma_histogram) == 0.0


def test_write_score_accepts_from_session_log_output_without_raising(tmp_path):
    # Integration check that score_writer.write_score() -- unchanged by
    # this ticket, per issue #89's resolution -- consumes the adapter's
    # TranscriptionResult-shaped output correctly: a C-major triad onset
    # at t=0 (one Chord) plus a later solo bass note (one Note), same
    # structure test_score_writer.py's own write_score() test checks.
    import score_writer

    events = [
        _event(0.0, 0, 4, 1.0, bpm_estimate=120.0, chord_name="C"),
        _event(0.0, 4, 4, 1.0, bpm_estimate=120.0, chord_name="C"),
        _event(0.0, 7, 4, 1.0, bpm_estimate=120.0, chord_name="C"),
        _event(2.0, 9, 2, 0.5, bpm_estimate=120.0, chord_name=None),
    ]
    result = from_session_log(events)
    out_path = tmp_path / "replay_score.musicxml"

    score_writer.write_score(result, str(out_path))  # must not raise

    assert out_path.exists()
    parsed = converter.parse(str(out_path))
    all_notes = list(parsed.recurse().notes)
    # 1 Chord (3 pitches) + 1 solo Note == 2 note-ish elements.
    assert len(all_notes) == 2
