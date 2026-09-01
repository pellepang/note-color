"""Tests for main.py's pure, non-threaded helper logic. Mirrors this
repo's existing convention (see test_shell.py) of importing and directly
testing small pure functions extracted from main.py's render loops,
rather than the loops themselves (smoke-tested manually)."""

import pytest

from config_store import ConfigStore
from rhythm_reanalysis import HopRecord

import main
from main import _filter_hop_records_to_range, _handle_mark_keys, _hop_beats, _mark_range


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """_handle_mark_keys() consults store.keybind() for mark_range_start/
    mark_range_end -- isolate it from the real ~/.config/note-color/
    config.toml the module-level singleton normally points at, same
    pattern test_settings_display.py already establishes, so these tests
    aren't at the mercy of whatever the dev machine's own config happens
    to remap those actions to."""
    fresh = ConfigStore(path=str(tmp_path / "config.toml"))
    monkeypatch.setattr(main, "store", fresh)
    return fresh


def test_hop_beats_takes_max_not_sum_of_simultaneous_finalizations():
    # Issue #76: an ordinary single note finalized independently by both
    # the mono DurationTracker and multipitch's one-note "chord" must
    # credit its duration once toward the bar boundary, not twice.
    assert _hop_beats([2.0, 2.0]) == 2.0


def test_hop_beats_takes_max_across_differing_values():
    assert _hop_beats([1.5, 3.0, 0.5]) == 3.0


def test_hop_beats_treats_none_entries_as_zero():
    # A `beats` value is None when bpm_estimate was unknown at
    # finalization time -- shouldn't crash max()/comparisons.
    assert _hop_beats([None, 1.0]) == 1.0
    assert _hop_beats([None, None]) == 0.0


def test_hop_beats_empty_list_is_zero():
    # No note finalized this hop at all.
    assert _hop_beats([]) == 0.0


def test_hop_beats_single_mono_only_value_unchanged():
    # No chord/multipitch finalization this hop -- behaves like before
    # the fix for the common single-note case.
    assert _hop_beats([1.25]) == 1.25


# --- _handle_mark_keys() / _mark_range() (loop/section markers) --------

def test_mark_start_key_sets_start_leaves_end_unchanged():
    start, end = _handle_mark_keys("[", True, None, 5.0, timestamp=2.0)
    assert (start, end) == (2.0, 5.0)


def test_mark_end_key_sets_end_leaves_start_unchanged():
    start, end = _handle_mark_keys("]", True, 2.0, None, timestamp=5.0)
    assert (start, end) == (2.0, 5.0)


def test_mark_keys_are_no_op_when_not_frozen():
    # A live-scrolling tail has no stable "point in history" to mark --
    # same gating as scrollback/reanalysis themselves.
    start, end = _handle_mark_keys("[", False, None, None, timestamp=2.0)
    assert (start, end) == (None, None)


def test_mark_keys_are_no_op_with_no_timestamp():
    # Nothing pushed to the display yet -- nothing to mark.
    start, end = _handle_mark_keys("[", True, None, None, timestamp=None)
    assert (start, end) == (None, None)


def test_mark_keys_ignore_unrelated_key():
    start, end = _handle_mark_keys("x", True, 1.0, 2.0, timestamp=9.0)
    assert (start, end) == (1.0, 2.0)


def test_mark_keys_ignore_none_key():
    start, end = _handle_mark_keys(None, True, 1.0, 2.0, timestamp=9.0)
    assert (start, end) == (1.0, 2.0)


def test_mark_range_none_until_both_ends_set():
    assert _mark_range(None, None) is None
    assert _mark_range(2.0, None) is None
    assert _mark_range(None, 5.0) is None


def test_mark_range_normalizes_regardless_of_press_order():
    # mark_range_end pressed before mark_range_start still yields (lo, hi).
    assert _mark_range(5.0, 2.0) == (2.0, 5.0)
    assert _mark_range(2.0, 5.0) == (2.0, 5.0)


# --- _filter_hop_records_to_range() -------------------------------------

def _hop(index, t_field=None):
    # HopRecord's own fields (mono/chord_notes/chroma_novelty) don't
    # matter for this filter -- only hop_index does.
    return HopRecord(hop_index=index, mono=None, chord_notes=(), chroma_novelty=0.0)


def test_filter_returns_records_unchanged_when_no_range_marked():
    records = [_hop(0), _hop(1), _hop(2)]
    assert _filter_hop_records_to_range(records, None, hop_seconds=0.1) == records


def test_filter_keeps_only_hops_inside_inclusive_range():
    hop_seconds = 0.1
    records = [_hop(i) for i in range(10)]  # timestamps 0.0 .. 0.9
    kept = _filter_hop_records_to_range(records, (0.2, 0.5), hop_seconds)
    assert [r.hop_index for r in kept] == [2, 3, 4, 5]


def test_filter_range_boundaries_are_inclusive():
    hop_seconds = 1.0
    records = [_hop(0), _hop(1), _hop(2)]
    kept = _filter_hop_records_to_range(records, (1.0, 2.0), hop_seconds)
    assert [r.hop_index for r in kept] == [1, 2]


def test_filter_range_with_no_matching_hops_is_empty_not_a_crash():
    hop_seconds = 0.1
    records = [_hop(i) for i in range(5)]  # timestamps 0.0 .. 0.4
    kept = _filter_hop_records_to_range(records, (10.0, 20.0), hop_seconds)
    assert kept == []


# --- VIEWS display dispatch table (architecture-modernization-plan.md
# §3.3) ---------------------------------------------------------------

def test_views_covers_exactly_the_four_terminal_and_gui_tools_in_order():
    # Order matters -- menu_display.TOOLS derives its menu/digit-key order
    # directly from this dict's insertion order (fill, wheel, tab, gui,
    # unchanged from before this table existed).
    assert list(main.VIEWS.keys()) == ["fill", "wheel", "tab", "gui"]


def test_views_run_entries_are_the_real_run_functions():
    assert main.VIEWS["fill"]["run"] is main.run_terminal_fill
    assert main.VIEWS["wheel"]["run"] is main.run_terminal_wheel
    assert main.VIEWS["tab"]["run"] is main.run_terminal_tab
    assert main.VIEWS["gui"]["run"] is main.run_gui


def test_views_transcribe_and_replay_are_not_entries():
    # Neither is a SessionState-driven live view (see CLAUDE.md's
    # Architecture / run_batch_transcribe()'s and run_replay_session()'s
    # own docstrings) -- folding them in here would misrepresent them as
    # another run_session()-launchable tool.
    assert "transcribe" not in main.VIEWS
    assert "replay" not in main.VIEWS


def test_views_only_wheel_has_a_circle_alias():
    assert main.VIEWS["wheel"]["aliases"] == ["circle"]
    for name in ("fill", "tab", "gui"):
        assert "aliases" not in main.VIEWS[name]


def test_views_only_tab_and_gui_declare_extra_cli_args():
    assert "extra_args" not in main.VIEWS["fill"]
    assert "extra_args" not in main.VIEWS["wheel"]
    assert main.VIEWS["tab"]["extra_args"] is main._tab_extra_args
    assert main.VIEWS["gui"]["extra_args"] is main._gui_extra_args


def test_views_menu_labels_match_the_original_hand_written_tools_list():
    # menu_display.TOOLS is derived from these -- pinning the exact text
    # here documents that the derivation is a pure refactor, not a
    # user-visible wording change.
    assert main.VIEWS["fill"]["menu_label"] == "Fill -- full-terminal color fill"
    assert main.VIEWS["wheel"]["menu_label"] == "Wheel -- circle-of-fifths ring"
    assert main.VIEWS["tab"]["menu_label"] == "Tab -- scrolling sheet-music staff"
    assert main.VIEWS["gui"]["menu_label"] == "GUI -- native color window"


class _FakeSession:
    """Just enough of SessionState's shape for dispatch wrapper tests --
    no real AudioCapture/analysis thread involved."""

    def __init__(self):
        self.result_queue = "the-result-queue"
        self.sensitivity = "the-sensitivity"
        self.capture = "the-capture"
        self.source_state = "the-source-state"
        self.reanalysis_buffer = "the-reanalysis-buffer"
        self.session_recorder = "the-session-recorder"
        self.started = False

    def ensure_started(self):
        self.started = True


def test_dispatch_fill_forwards_session_fields_only(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_terminal_fill", lambda *a: calls.append(a))
    session = _FakeSession()
    main._dispatch_fill(session, "onset", "/dump", True, True, (3, 4))
    assert calls == [(session.result_queue, session.sensitivity, session.capture, session.source_state,
                       session.session_recorder)]


def test_dispatch_wheel_forwards_session_fields_only(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_terminal_wheel", lambda *a: calls.append(a))
    session = _FakeSession()
    main._dispatch_wheel(session, "onset", "/dump", True, True, (3, 4))
    assert calls == [(session.result_queue, session.sensitivity, session.capture, session.source_state,
                       session.session_recorder)]


def test_dispatch_tab_forwards_scroll_mode_dump_file_and_time_signature(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_terminal_tab", lambda *a, **kw: calls.append((a, kw)))
    session = _FakeSession()
    main._dispatch_tab(session, "onset", "/dump", True, True, (3, 4))
    (args, kwargs) = calls[0]
    assert args == (session.result_queue, "onset", "/dump", session.sensitivity, session.capture,
                     session.source_state, session.reanalysis_buffer, session.session_recorder)
    assert kwargs == {"time_signature": (3, 4)}


def test_dispatch_gui_forwards_fullscreen_and_debug_not_scroll_or_dump_file(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_gui", lambda *a: calls.append(a))
    session = _FakeSession()
    main._dispatch_gui(session, "onset", "/dump", True, False, (3, 4))
    assert calls == [(session.result_queue, True, False, session.sensitivity)]


def test_run_session_starts_the_session_then_dispatches_through_views(monkeypatch):
    calls = []
    fake_views = {"fill": {"dispatch": lambda session, *rest: calls.append((session, rest)) or "quit"}}
    monkeypatch.setattr(main, "VIEWS", fake_views)
    session = _FakeSession()
    result = main.run_session("fill", "onset", "/dump", False, False, session, time_signature=(3, 4))
    assert session.started is True
    assert calls == [(session, ("onset", "/dump", False, False, (3, 4)))]
    assert result == "quit"


def test_run_session_unrecognized_view_falls_back_to_fill(monkeypatch):
    # Defensive fallback only -- both real callers (main() and
    # virtualnote.py's argparse subparsers) only ever pass a name VIEWS
    # actually has.
    calls = []
    fake_views = {"fill": {"dispatch": lambda session, *rest: calls.append((session, rest))}}
    monkeypatch.setattr(main, "VIEWS", fake_views)
    session = _FakeSession()
    main.run_session("not-a-real-view", "onset", None, False, False, session)
    assert len(calls) == 1
