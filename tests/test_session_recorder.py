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
