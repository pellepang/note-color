"""Tests for stats_display.py (Feature 4, "historical play stats").

Per this repo's test convention, only the pure aggregation/text-building
functions (compute_stats(), stats_lines(), _session_date(),
_format_duration()) are unit-tested here, against synthesized (path,
events) pairs -- no real session_log_*.jsonl files, no tmp_path -- the
interactive render/wait-for-keypress loop (run_stats_screen) is
smoke-tested manually, same convention as credits_display's own
run_credits_screen()."""

from stats_display import _format_duration, _session_date, compute_stats, stats_lines


def _event(label, duration_seconds=1.0):
    return {"pc": 0, "octave": 4, "label": label, "duration_seconds": duration_seconds}


def test_compute_stats_empty_sessions_list():
    stats = compute_stats([])
    assert stats["session_count"] == 0
    assert stats["total_practice_seconds"] == 0.0
    assert stats["most_played"] == []
    assert stats["sessions_by_date"] == {}


def test_compute_stats_sums_duration_across_sessions():
    sessions = [
        ("session_log_20260101_120000.jsonl", [_event("C4", 1.5), _event("D4", 2.0)]),
        ("session_log_20260102_090000.jsonl", [_event("E4", 0.5)]),
    ]
    stats = compute_stats(sessions)
    assert stats["session_count"] == 2
    assert stats["total_practice_seconds"] == 4.0


def test_compute_stats_most_played_counts_and_ranks_by_frequency():
    events = [_event("C4"), _event("C4"), _event("C4"), _event("D4"), _event("D4"), _event("E4")]
    stats = compute_stats([("session_log_20260101_120000.jsonl", events)])
    assert stats["most_played"][0] == ("C4", 3)
    assert stats["most_played"][1] == ("D4", 2)
    assert stats["most_played"][2] == ("E4", 1)


def test_compute_stats_most_played_caps_at_ten():
    events = [_event(f"note{i}") for i in range(15)]
    stats = compute_stats([("session_log_20260101_120000.jsonl", events)])
    assert len(stats["most_played"]) == 10


def test_compute_stats_sessions_by_date_groups_and_sorts_chronologically():
    sessions = [
        ("session_log_20260103_120000.jsonl", []),
        ("session_log_20260101_090000.jsonl", []),
        ("session_log_20260101_180000.jsonl", []),
    ]
    stats = compute_stats(sessions)
    assert stats["sessions_by_date"] == {"2026-01-01": 2, "2026-01-03": 1}
    assert list(stats["sessions_by_date"].keys()) == ["2026-01-01", "2026-01-03"]


def test_compute_stats_ignores_non_matching_filenames_in_date_breakdown():
    sessions = [("hand_renamed_log.jsonl", [_event("C4")])]
    stats = compute_stats(sessions)
    assert stats["session_count"] == 1  # still counted as a session...
    assert stats["sessions_by_date"] == {}  # ...just excluded from the date breakdown


def test_session_date_parses_the_recorder_naming_convention():
    assert _session_date("/some/dir/session_log_20260315_143022.jsonl") == "2026-03-15"


def test_session_date_returns_none_for_a_non_matching_name():
    assert _session_date("/some/dir/not_a_session_log.txt") is None


def test_format_duration_seconds_only():
    assert _format_duration(45) == "45s"


def test_format_duration_minutes_and_seconds():
    assert _format_duration(192) == "3m 12s"


def test_format_duration_hours_minutes_seconds():
    assert _format_duration(3600 + 240) == "1h 4m 0s"


def test_stats_lines_reports_session_count_and_total_time():
    stats = compute_stats([("session_log_20260101_120000.jsonl", [_event("C4", 30.0)])])
    lines = stats_lines(stats)
    joined = "\n".join(lines)
    assert "sessions logged: 1" in joined
    assert "30s" in joined


def test_stats_lines_lists_most_played_notes():
    events = [_event("C4"), _event("C4"), _event("D4")]
    stats = compute_stats([("session_log_20260101_120000.jsonl", events)])
    lines = stats_lines(stats)
    joined = "\n".join(lines)
    assert "C4" in joined
    assert "D4" in joined


def test_stats_lines_handles_no_sessions_gracefully():
    lines = stats_lines(compute_stats([]))
    joined = "\n".join(lines)
    assert "sessions logged: 0" in joined
    assert "no sessions logged yet" in joined
