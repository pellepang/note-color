import json

from session_recorder import SessionRecorder


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_toggle_opens_and_closes_file(tmp_path):
    path = tmp_path / "session.jsonl"
    rec = SessionRecorder(path=str(path))

    assert rec.armed is False
    assert rec.toggle() is True
    assert rec.armed is True
    assert path.exists()

    assert rec.toggle() is False
    assert rec.armed is False


def test_record_hop_is_a_noop_when_not_armed(tmp_path):
    path = tmp_path / "session.jsonl"
    rec = SessionRecorder(path=str(path))

    rec.record_hop(0, 4, [], None, duration_hops=20, bpm_estimate=120.0, hop_index=20, hop_seconds=0.0232)

    assert not path.exists()


def test_mono_note_pairs_duration_with_previous_hop_pitch(tmp_path):
    path = tmp_path / "session.jsonl"
    rec = SessionRecorder(path=str(path))
    rec.toggle()

    hop_seconds = 0.0232
    # Hop 0: C4 starts sounding (no finalization yet).
    rec.record_hop(0, 4, [], None, duration_hops=None, bpm_estimate=None, hop_index=0, hop_seconds=hop_seconds)
    # Hop 20: pitch has moved on to D4, but DurationTracker only learns
    # C4 is done *this* hop -- duration_hops describes the *previous*
    # hop's note (C4), not this hop's (D4). This mirrors run_terminal_tab()'s
    # own prev_pitch_class/prev_octave pairing in main.py.
    rec.record_hop(2, 4, [], None, duration_hops=20, bpm_estimate=120.0, hop_index=20, hop_seconds=hop_seconds)
    rec.close()

    events = _lines(path)
    assert len(events) == 1
    event = events[0]
    assert event["pc"] == 0 and event["octave"] == 4 and event["label"] == "C4"
    assert event["duration_hops"] == 20
    assert event["duration_class"] == "quarter"  # 20 hops * 0.0232s * 120bpm/60 == ~0.928 beats
    # onset_hop = hop_index - duration_hops = 20 - 20 = 0
    assert event["t"] == 0.0


def test_chord_tone_note_stack_entries_are_recorded(tmp_path):
    path = tmp_path / "session.jsonl"
    rec = SessionRecorder(path=str(path))
    rec.toggle()

    note_stack = [
        {"pitch_class": 0, "octave": 3, "duration_hops": 40},
        {"pitch_class": 4, "octave": 3, "duration_hops": None},  # still sounding, not finalized
    ]
    rec.record_hop(None, None, note_stack, "Cmaj", duration_hops=None, bpm_estimate=None,
                    hop_index=40, hop_seconds=0.0232)
    rec.close()

    events = _lines(path)
    assert len(events) == 1
    assert events[0]["label"] == "C3"
    assert events[0]["chord_name"] == "Cmaj"
    assert events[0]["bpm_estimate"] is None


def test_close_is_idempotent_and_safe_before_ever_arming(tmp_path):
    rec = SessionRecorder(path=str(tmp_path / "session.jsonl"))
    rec.close()  # never armed -- must not raise
    rec.close()  # closing twice -- must not raise
    assert rec.armed is False


# --- played notes (map #99, ticket #122, decision #110) -------------------
#
# THE MACHINE THESE WERE WRITTEN ON IS MUTED: nothing below asserts
# anything was heard, only that the right line was written. Every clock
# value is injected, so no test here depends on real elapsed time.

def _armed(tmp_path, name="session.jsonl"):
    rec = SessionRecorder(path=str(tmp_path / name))
    rec.toggle()
    return rec


def test_a_played_note_writes_one_line_with_the_new_optional_fields(tmp_path):
    rec = _armed(tmp_path)
    rec.note_on("z", 0, 4, velocity=1.0, patch="Fat bass", now=100.0)
    rec.note_off("z", now=100.5)
    rec.close(now=100.5)

    (event,) = _lines(rec.path)
    assert event["source"] == "played"
    assert event["velocity"] == 127
    assert event["patch"] == "Fat bass"
    assert event["pad"] is None
    assert event["label"] == "C4"
    assert event["duration_seconds"] == 0.5


def test_played_notes_write_null_hop_fields(tmp_path):
    # #110 point 2: duration_hops/bpm_estimate are meaningless without a
    # hop-driven pipeline, and a synthetic hop clock would put a fictional
    # number into a file people read by hand.
    rec = _armed(tmp_path)
    rec.note_on("z", 0, 4, now=1.0)
    rec.note_off("z", now=1.25)
    rec.close(now=1.25)

    (event,) = _lines(rec.path)
    assert event["duration_hops"] is None
    assert event["bpm_estimate"] is None


def test_a_detected_note_still_writes_no_source_field(tmp_path):
    # "absent means detected" is what keeps every pre-existing log valid,
    # so record_hop()'s own line must stay byte-for-byte what it was.
    rec = _armed(tmp_path)
    rec.record_hop(0, 4, [], None, None, None, 10, 0.0116)
    rec.record_hop(0, 4, [], "C", 5, 100.0, 11, 0.0116)
    rec.close()

    (event,) = _lines(rec.path)
    assert "source" not in event and "velocity" not in event
    assert event["duration_hops"] == 5


def test_time_is_measured_from_the_first_played_note_of_the_take(tmp_path):
    rec = _armed(tmp_path)
    rec.note_on("z", 0, 4, now=500.0)
    rec.note_off("z", now=500.25)
    rec.note_on("x", 2, 4, now=501.0)
    rec.note_off("x", now=501.25)
    rec.close(now=501.25)

    assert [e["t"] for e in _lines(rec.path)] == [0.0, 1.0]


def test_several_notes_can_be_in_flight_at_once_and_pair_by_key(tmp_path):
    # A chord: three keys down together, released out of order.
    rec = _armed(tmp_path)
    rec.note_on("z", 0, 4, now=10.0)
    rec.note_on("c", 4, 4, now=10.0)
    rec.note_on("b", 7, 4, now=10.0)
    rec.note_off("c", now=10.4)
    rec.note_off("b", now=10.2)
    rec.note_off("z", now=10.6)
    rec.close(now=10.6)

    by_label = {e["label"]: e["duration_seconds"] for e in _lines(rec.path)}
    assert by_label == {"C4": 0.6, "E4": 0.4, "G4": 0.2}


def test_a_pad_hit_records_its_pad_number_and_the_kits_name(tmp_path):
    rec = _armed(tmp_path)
    rec.note_on("1", 0, 3, velocity=0.8, patch="Kit", pad=3, now=0.0)
    rec.note_off("1", now=0.1)
    rec.close(now=0.1)

    (event,) = _lines(rec.path)
    assert event["pad"] == 3
    assert event["patch"] == "Kit"
    assert event["velocity"] == 102


def test_note_on_is_a_noop_when_not_armed(tmp_path):
    path = tmp_path / "session.jsonl"
    rec = SessionRecorder(path=str(path))
    rec.note_on("z", 0, 4, now=1.0)
    rec.note_off("z", now=2.0)
    assert not path.exists()


def test_a_note_begun_before_arming_is_not_written_with_a_made_up_onset(tmp_path):
    rec = SessionRecorder(path=str(tmp_path / "session.jsonl"))
    rec.note_on("z", 0, 4, now=1.0)   # not armed yet: nothing remembered
    rec.toggle()
    rec.note_off("z", now=2.0)
    rec.close(now=2.0)
    assert _lines(rec.path) == []


def test_a_note_still_held_when_recording_stops_is_truncated_not_dropped(tmp_path):
    rec = _armed(tmp_path)
    rec.note_on("z", 0, 4, now=5.0)
    rec.close(now=5.75)

    (event,) = _lines(rec.path)
    assert event["duration_seconds"] == 0.75


def test_duration_class_is_derived_against_the_reference_tempo(tmp_path):
    # There is no tempo estimate for a played note, so duration_class --
    # the field `virtualnote replay` draws its glyphs from -- is snapped
    # against config.PLAYED_NOTE_REFERENCE_BPM, with the raw
    # duration_seconds written unrounded right beside it.
    import config

    rec = _armed(tmp_path)
    quarter = 60.0 / config.PLAYED_NOTE_REFERENCE_BPM
    rec.note_on("z", 0, 4, now=0.0)
    rec.note_off("z", now=quarter)
    rec.note_on("x", 2, 4, now=1.0)
    rec.note_off("x", now=1.0 + quarter / 2)
    rec.close(now=2.0)

    assert [e["duration_class"] for e in _lines(rec.path)] == ["quarter", "eighth"]


def test_a_played_log_replays_through_session_player_unchanged(tmp_path):
    # The whole point of extending the one schema additively (#110 point
    # 1): the existing reader needs no changes at all.
    from session_player import group_columns, load_events

    rec = _armed(tmp_path)
    rec.note_on("z", 0, 4, now=0.0)
    rec.note_on("c", 4, 4, now=0.0)
    rec.note_off("z", now=0.5)
    rec.note_off("c", now=0.5)
    rec.note_on("x", 2, 4, now=1.0)
    rec.note_off("x", now=1.5)
    rec.close(now=1.5)

    columns = group_columns(load_events(rec.path))
    assert [(kind, t, len(group)) for kind, t, group in columns] == [
        ("notes", 0.0, 2), ("notes", 1.0, 1)]
