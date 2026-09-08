"""The core/GUI seam (ticket #151).

The whole design rests on one claim -- the audio callback owns time, and the UI
only sends commands and reads snapshots -- so these tests are mostly about the
*direction* of that traffic holding: that a UI call changes nothing until a
block is processed, that a snapshot is coherent, and that nothing here needs a
lock.
"""

import pytest

from notecolor.audio import transport as tp
from notecolor.project.model import TempoAnchor, TempoMap

RATE = 48000
BLOCK = 512


def _transport(bpm=120.0):
    return tp.Transport(TempoMap([TempoAnchor(0.0, bpm)]), sample_rate=RATE)


# --- the callback owns time ------------------------------------------------


def test_a_ui_call_changes_nothing_until_a_block_is_processed():
    """This is the seam's central claim. If `play()` moved the transport, the
    UI would own time and the whole design would be decorative."""
    t = _transport()
    t.play()
    t.locate(16.0)
    assert t.snapshot().state == tp.STOPPED
    assert t.snapshot().beat == 0.0

    # That block applies the commands and then plays through, so the position
    # is the located beat plus exactly one block -- not the located beat.
    t.process_block(BLOCK)
    assert t.snapshot().state == tp.PLAYING
    one_block_in_beats = BLOCK / RATE * 2.0
    assert t.snapshot().beat == pytest.approx(16.0 + one_block_in_beats, abs=1e-3)


def test_position_advances_by_exactly_the_frames_the_driver_asked_for():
    t = _transport()
    t.play()
    for _ in range(10):
        t.process_block(BLOCK)
    assert t.frame == 10 * BLOCK
    assert t.snapshot().seconds == pytest.approx(10 * BLOCK / RATE)


def test_a_stopped_transport_does_not_move():
    t = _transport()
    for _ in range(5):
        t.process_block(BLOCK)
    assert t.frame == 0


def test_process_block_returns_the_half_open_beat_window():
    """A window, not a point: a scheduler has to be able to ask what fell due
    *between* two blocks, and a point would let notes slip through the gap."""
    t = _transport(bpm=120.0)
    t.play()
    t.process_block(BLOCK)                     # the block that starts playback
    start, end = t.process_block(BLOCK)
    assert start == pytest.approx(BLOCK / RATE * 2)
    assert end == pytest.approx(2 * BLOCK / RATE * 2)
    assert end > start


def test_consecutive_windows_are_contiguous():
    t = _transport()
    t.play()
    t.process_block(BLOCK)
    previous_end = None
    for _ in range(20):
        start, end = t.process_block(BLOCK)
        if previous_end is not None:
            assert start == pytest.approx(previous_end)
        previous_end = end


# --- commands --------------------------------------------------------------


def test_commands_apply_in_the_order_they_were_sent():
    t = _transport()
    t.locate(8.0)
    t.locate(4.0)
    t.process_block(BLOCK)
    assert t.snapshot().beat == pytest.approx(4.0, abs=1e-3)


def test_stop_to_start_returns_to_where_playback_began():
    t = _transport()
    t.locate(4.0)
    t.play()
    for _ in range(20):
        t.process_block(BLOCK)
    assert t.beat > 4.0

    t.stop(to_start=True)
    t.process_block(BLOCK)
    assert t.snapshot().beat == pytest.approx(4.0, abs=1e-3)
    assert t.snapshot().state == tp.STOPPED


def test_plain_stop_leaves_the_playhead_where_it_is():
    t = _transport()
    t.play()
    for _ in range(20):
        t.process_block(BLOCK)
    here = t.beat
    t.stop()
    t.process_block(BLOCK)
    assert t.snapshot().beat == pytest.approx(here, abs=1e-3)


def test_the_queue_never_drops_a_command():
    """Dropping the Stop that was meant to end playback is the failure this
    guards; `VoiceManager` refuses to drop a note for the same reason."""
    queue = tp.CommandQueue()
    for i in range(10000):
        queue.send(tp.Locate(float(i)))
    assert len(queue.drain()) == 10000
    assert queue.drain() == []


# --- looping ---------------------------------------------------------------


def test_playback_wraps_at_the_loop_end():
    t = _transport(bpm=120.0)
    t.set_loop(0.0, 1.0, enabled=True)
    t.play()
    for _ in range(200):
        t.process_block(BLOCK)
    assert 0.0 <= t.beat < 1.0


def test_the_loop_does_not_drift_by_a_fraction_of_a_block_each_time_round():
    """The overshoot past the loop end is carried, not discarded. Discarding
    it makes a loop gain time on every repeat, which is audible within
    seconds."""
    t = _transport(bpm=120.0)
    t.set_loop(0.0, 2.0, enabled=True)
    t.play()
    total = 0
    for _ in range(500):
        t.process_block(BLOCK)
        total += BLOCK
    elapsed_beats = total / RATE * 2.0
    expected = elapsed_beats % 2.0
    assert t.beat == pytest.approx(expected, abs=0.02)


def test_a_disabled_loop_does_not_wrap():
    t = _transport()
    t.set_loop(0.0, 1.0, enabled=False)
    t.play()
    for _ in range(200):
        t.process_block(BLOCK)
    assert t.beat > 1.0


# --- tempo -----------------------------------------------------------------


def test_changing_the_tempo_map_keeps_the_musical_position():
    """Changing the tempo moves the music; it does not teleport the playhead
    into a different bar."""
    t = _transport(bpm=120.0)
    t.locate(8.0)
    t.process_block(BLOCK)
    assert t.snapshot().beat == pytest.approx(8.0, abs=1e-3)

    t.set_tempo_map(TempoMap([TempoAnchor(0.0, 60.0)]))
    t.process_block(BLOCK)
    assert t.snapshot().beat == pytest.approx(8.0, abs=1e-3)
    assert t.snapshot().seconds == pytest.approx(8.0, abs=0.05)   # half speed
    assert t.snapshot().bpm == 60.0


# --- the snapshot ----------------------------------------------------------


def test_a_snapshot_is_immutable_so_a_reader_cannot_corrupt_the_engine():
    snapshot = _transport().snapshot()
    with pytest.raises(Exception):
        snapshot.frame = 999


def test_publishing_replaces_the_slot_wholesale_never_mutates_in_place():
    """A reader holding an old snapshot must keep seeing consistent values --
    that is what makes the single attribute assignment safe without a lock."""
    t = _transport()
    t.play()
    t.process_block(BLOCK)
    first = t.snapshot()
    for _ in range(5):
        t.process_block(BLOCK)
    second = t.snapshot()
    assert first is not second
    assert first.frame == BLOCK        # the old object did not move underneath
    assert second.frame == 6 * BLOCK


def test_the_snapshot_carries_beats_and_samples_together():
    """So a UI never has to hold a second copy of the tempo map to draw a
    ruler -- duplicated state is state that drifts."""
    t = _transport(bpm=120.0)
    t.play()
    for _ in range(100):
        t.process_block(BLOCK)
    s = t.snapshot()
    assert s.beat == pytest.approx(s.seconds * 2.0)
    assert s.frame == pytest.approx(s.seconds * s.sample_rate)


def test_xruns_are_surfaced_because_the_ring_buffer_hides_them():
    """#100 measured that overruns are invisible until the engine has already
    xrun, so the driver's own counter is the only honest signal."""
    t = _transport()
    t.process_block(BLOCK, xruns=3)
    assert t.snapshot().xruns == 3


def test_block_count_increments_even_while_stopped():
    """A UI showing 'audio running' needs to distinguish a stopped transport
    from a dead callback."""
    t = _transport()
    for _ in range(4):
        t.process_block(BLOCK)
    assert t.snapshot().block_count == 4
    assert t.snapshot().state == tp.STOPPED
