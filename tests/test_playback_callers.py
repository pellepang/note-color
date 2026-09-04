"""Regression tests for the two existing playback callers across map #99
ticket #112's engine change: `virtualnote transcribe --play` (offline
pre-render, deliberately untouched) and `virtualnote replay --play`
(moved off the retired `playback.LiveScheduler` onto
`sound_engine.SoundEngine`).

Neither test opens an audio device: the one real I/O call each path makes
(`sd.play()` for transcribe, `SoundEngine.ensure_started()` for replay) is
substituted, and the assertions are on what the caller *asked for* --
note onsets/durations in the pre-rendered buffer, and note-on/note-off
pairs with their scheduled delays. This is the "both callers keep
working" evidence ticket #112 asks for, in the form this repo's
convention allows (real audio output is smoke-tested by hand, and by
scripts/sound_engine_smoke.py against PortAudio's own xrun counters).
"""

import json

import numpy as np
import pytest

import config
import main
import playback
import sound_engine


# --- transcribe --play: still the untouched offline pre-render ---------

def test_transcribe_play_path_still_renders_offline_without_the_voice_manager(monkeypatch):
    played = {}

    def fake_play(buffer, sample_rate, blocking=True):
        played["buffer"] = np.asarray(buffer)
        played["sample_rate"] = sample_rate

    monkeypatch.setattr(playback.sd, "play", fake_play)
    playback.play_offline([(0.0, 0, 4, 0.2), (0.5, 7, 4, 0.2)])

    assert played["sample_rate"] == config.PLAYBACK_SAMPLE_RATE
    buffer = played["buffer"]
    # Sample-accurate by construction: the second note's onset sits exactly
    # 0.5s in, and nothing sounds in the silence just before it.
    onset = int(0.5 * config.PLAYBACK_SAMPLE_RATE)
    assert np.max(np.abs(buffer[onset:onset + 200])) > 0.0
    assert np.max(np.abs(buffer[onset - 200:onset - 50])) < np.max(np.abs(buffer[onset:onset + 200]))
    assert np.max(np.abs(buffer)) <= 1.0


def test_playback_no_longer_exposes_the_superseded_live_scheduler():
    # Decision #105: one voice-mixing callback per process, so the old
    # LiveScheduler is gone rather than left as a second one.
    assert not hasattr(playback, "LiveScheduler")
    assert hasattr(playback, "render_offline") and hasattr(playback, "play_offline")


# --- replay --play: now driven by note-on / scheduled note-off ---------

class RecordingEngine:
    """Stands in for sound_engine.SoundEngine, recording the vocabulary
    the caller speaks rather than making any sound."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.note_ons = []
        self.scheduled = []
        RecordingEngine.instances.append(self)

    def ensure_started(self):
        self.started = True

    def note_on(self, event, **kwargs):
        self.note_ons.append(event)
        return len(self.note_ons)

    def schedule_note_off(self, voice_id, delay_seconds):
        self.scheduled.append((voice_id, delay_seconds))

    def stop(self):
        self.stopped = True


@pytest.fixture
def recording_engine(monkeypatch):
    RecordingEngine.instances = []
    monkeypatch.setattr(sound_engine, "SoundEngine", RecordingEngine)
    return RecordingEngine


def write_log(tmp_path):
    events = [
        {"t": 0.0, "pc": 0, "octave": 4, "duration_class": "quarter", "duration_seconds": 0.5},
        {"t": 0.0, "pc": 4, "octave": 4, "duration_class": "quarter", "duration_seconds": 0.5},
        {"t": 0.02, "pc": 7, "octave": 4, "duration_class": "eighth", "duration_seconds": 0.25},
    ]
    path = tmp_path / "session_log_test.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    return str(path)


def test_replay_play_issues_one_note_on_per_event_with_a_scheduled_note_off(tmp_path, recording_engine):
    log_path = write_log(tmp_path)
    main.run_replay_session(log_path, dump_file=str(tmp_path / "dump.txt"), speed=100.0, play=True)

    engine = recording_engine.instances[0]
    assert engine.started and engine.stopped
    assert engine.kwargs.get("detection_active") is False   # replay never opens the mic

    # One note-on per recorded note, MIDI-pitched, in log order.
    assert [event.pitch for event in engine.note_ons] == [60, 64, 67]
    assert all(isinstance(event, sound_engine.NoteOn) for event in engine.note_ons)

    # Every note-on has exactly one matching scheduled note-off, whose
    # delay is the recorded duration divided by --speed (audio speeds up
    # in lockstep with the visuals, not just the gaps between columns).
    assert [voice_id for voice_id, _ in engine.scheduled] == [1, 2, 3]
    assert [delay for _, delay in engine.scheduled] == pytest.approx([0.005, 0.005, 0.0025])


def test_replay_without_play_never_constructs_a_sound_engine(tmp_path, recording_engine):
    log_path = write_log(tmp_path)
    main.run_replay_session(log_path, dump_file=str(tmp_path / "dump.txt"), speed=100.0, play=False)
    assert recording_engine.instances == []


# --- the seam itself, end to end with the real engine, no device -------

def test_a_replay_shaped_note_on_plus_scheduled_note_off_fully_retires_its_voice():
    """The real SoundEngine + ToneEngine, driven exactly as replay drives
    them, with the audio callback pulled by hand: the voice sounds, its
    scheduled note-off fires on time, and its slot is reclaimed."""
    engine = sound_engine.SoundEngine(sample_rate=8000, block_size=64)
    voice_id = engine.note_on(sound_engine.NoteOn.from_pitch_class(0, 4))
    engine.schedule_note_off(voice_id, 0.1)

    outdata = np.zeros((64, 1), dtype=np.float32)
    peak = 0.0
    blocks_until_release = None
    for block in range(400):
        outdata[:] = 0.0
        engine._callback(outdata, 64, None, None)
        peak = max(peak, float(np.max(np.abs(outdata))))
        if blocks_until_release is None and engine.voices.snapshot() and \
                engine.voices.snapshot()[0].voice.released:
            blocks_until_release = block + 1
        if engine.voices.active_count() == 0:
            break

    assert peak > 0.0                                        # it actually made samples
    # 0.1s at 8000Hz / 64-frame blocks = 12.5 blocks, resolved at the
    # first block boundary at or past the deadline: block 13.
    assert blocks_until_release == 13
    assert engine.voices.active_count() == 0                 # released, faded, reclaimed
    assert engine.callback_status_count == 0
