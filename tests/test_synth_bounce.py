"""Bouncing a Synth-view take to a new Audio track (ticket #159).

`SynthTakeRecorder` is exercised by calling `note_on()`/`note_off()`
directly -- exactly as a live keyboard input would, but with no audio
device, no thread, and no Qt involved, matching this repo's "pure logic
unit-tested" convention. `render_take()`/`bounce_take_to_track()` need
SciPy (the synth engine's filter); skipped outright where it is absent.
"""

import os
import wave

import numpy as np
import pytest

from notecolor.audio import synth_bounce
from notecolor.project import bundle
from notecolor.project.model import AUDIO_TRACK, Project, TempoAnchor, TempoMap

scipy_signal = pytest.importorskip("scipy.signal")

from notecolor.audio.synth_engine import default_patch  # noqa: E402


def _project(bpm=120.0):
    return Project(name="T", tempo_map=TempoMap([TempoAnchor(0.0, bpm)]))


# --- SynthTakeRecorder: capture, reusing SessionRecorder as-is -------------


def test_a_fresh_recorder_captures_nothing_until_started():
    recorder = synth_bounce.SynthTakeRecorder()
    recorder.note_on(60, now=0.0)   # armed=False on the underlying SessionRecorder
    recorder.note_off(60, now=0.5)
    assert recorder.stop() == []


def test_one_note_on_off_round_trips_through_the_session_log():
    recorder = synth_bounce.SynthTakeRecorder()
    recorder.start(default_patch(), start_beat=4.0, now=0.0)
    recorder.note_on(60, velocity=0.8, now=0.0)
    recorder.note_off(60, now=0.5)
    events = recorder.stop()

    assert len(events) == 1
    event = events[0]
    assert event["pc"] == 0 and event["octave"] == 4   # MIDI 60 == C4
    assert event["t"] == pytest.approx(0.0)
    assert event["duration_seconds"] == pytest.approx(0.5)
    assert event["source"] == "played"
    assert event["velocity"] == round(0.8 * 127)


def test_a_note_still_held_at_stop_is_finalized_not_dropped():
    recorder = synth_bounce.SynthTakeRecorder()
    recorder.start(default_patch(), start_beat=0.0, now=0.0)
    recorder.note_on(67, now=0.0)
    events = recorder.stop(now=0.25)
    assert len(events) == 1
    assert events[0]["duration_seconds"] == pytest.approx(0.25)


def test_stop_deletes_the_temp_log_file():
    recorder = synth_bounce.SynthTakeRecorder()
    recorder.start(default_patch(), start_beat=0.0, now=0.0)
    recorder.note_on(60, now=0.0)
    recorder.note_off(60, now=0.1)
    path = recorder._recorder.path
    recorder.stop()
    assert not os.path.exists(path)


def test_events_come_back_time_ordered():
    recorder = synth_bounce.SynthTakeRecorder()
    recorder.start(default_patch(), start_beat=0.0, now=0.0)
    recorder.note_on(64, now=0.5)
    recorder.note_off(64, now=0.6)
    recorder.note_on(60, now=0.0)
    recorder.note_off(60, now=0.2)
    events = recorder.stop()
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)


# --- render_take: offline replay through a fresh VoiceManager --------------


def test_render_take_of_no_events_is_an_empty_buffer():
    buffer = synth_bounce.render_take([], default_patch(), 44100)
    assert buffer.shape == (0,)


def test_render_take_produces_nonsilent_audio_for_a_held_note():
    events = [{"t": 0.0, "pc": 0, "octave": 4, "duration_seconds": 0.2,
              "velocity": 100, "bpm_estimate": None, "chord_name": None}]
    buffer = synth_bounce.render_take(events, default_patch(), 44100)
    assert buffer.dtype == np.float32
    # At least a whole block's worth of tail beyond the note's own duration,
    # for the release stage.
    assert buffer.shape[0] > int(0.2 * 44100)
    assert np.abs(buffer).max() > 0.0


def test_render_take_is_silent_before_the_first_note_onset():
    events = [{"t": 0.3, "pc": 0, "octave": 4, "duration_seconds": 0.1,
              "velocity": 100}]
    buffer = synth_bounce.render_take(events, default_patch(), 44100, block_size=64)
    onset_frame = int(0.3 * 44100)
    # A handful of leading blocks, well before the note starts, must be
    # exactly silent -- nothing renders before its own note-on.
    assert np.abs(buffer[:onset_frame - 64]).max() == 0.0


# --- write_wav ---------------------------------------------------------


def test_write_wav_round_trips_a_buffer(tmp_path):
    buffer = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    path = tmp_path / "take.wav"
    synth_bounce.write_wav(buffer, 44100, path)

    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 44100
        assert handle.getnframes() == len(buffer)


# --- bounce_take_to_track: import_audio + AddTrack, undispatched -----------


def test_bounce_builds_an_undispatched_add_track_command(tmp_path):
    project = _project(bpm=120.0)
    path = bundle.save_project(project, tmp_path / "Demo")
    events = [{"t": 0.0, "pc": 0, "octave": 4, "duration_seconds": 0.1,
              "velocity": 100}]

    command = synth_bounce.bounce_take_to_track(
        path, project, events, default_patch(), 44100, start_beat=8.0,
        track_name="My Take")

    assert project.tracks == []                     # not applied yet
    assert command.track.kind == AUDIO_TRACK
    clip = command.track.clips[0]
    assert clip.start_beat == pytest.approx(8.0)
    assert clip.length_beats > 0.0
    assert clip.source_length_samples > 0
    assert os.path.exists(os.path.join(path, "audio", clip.source))

    command.do()
    assert project.tracks[0] is command.track
    command.undo()
    assert project.tracks == []


def test_bounce_of_an_empty_take_still_yields_a_visible_clip(tmp_path):
    project = _project()
    path = bundle.save_project(project, tmp_path / "Demo")
    command = synth_bounce.bounce_take_to_track(
        path, project, [], default_patch(), 44100, start_beat=0.0)
    clip = command.track.clips[0]
    assert clip.length_beats >= synth_bounce.MIN_CLIP_LENGTH_BEATS
