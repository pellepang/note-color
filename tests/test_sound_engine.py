"""Tests for the sound engine's core seam (map #99, ticket #112, decision
#105): the event model, the stealing policy, `VoiceManager`'s allocation
bookkeeping and `SoundEngine`'s note-off deadline arithmetic and audio
callback.

Everything here runs without opening an audio device -- `_callback()` is
called directly with a plain NumPy buffer, exactly the "pure logic
unit-tested, real hardware I/O smoke-tested" split this suite already
applies to `audio_capture.py` and (previously) `playback.LiveScheduler`.
The real-device check lives in `scripts/sound_engine_smoke.py`, which
reports PortAudio's own xrun counters rather than asserting on sound.
"""

import numpy as np
import pytest

import config
import sound_engine
from config_store import ConfigStore
from sound_engine import (
    ActiveVoice, NoteOn, SoundEngine, VoiceManager, frequency_for, midi_pitch, pitch_class_octave,
    polyphony_for, select_steal_index,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """polyphony_for() reads through config_store's module-level singleton
    -- point it at a throwaway file so these tests never depend on (or
    write to) the dev machine's real config.toml. Same pattern
    test_settings_display.py established."""
    fresh = ConfigStore(path=str(tmp_path / "config.toml"))
    monkeypatch.setattr(sound_engine, "store", fresh)
    return fresh


class FakeVoice:
    """A `Voice` with no audio in it: renders a constant, retires after a
    fixed number of blocks, and reports whatever amplitude the test wants
    the stealing policy to rank it by."""

    def __init__(self, level=1.0, blocks_to_finish=None, value=1.0):
        self.level = level
        self.value = value
        self._released = False
        self._finished = False
        self.blocks_left = blocks_to_finish
        self.render_calls = 0
        self.note_off_calls = 0

    def render(self, out, frames):
        self.render_calls += 1
        out[:frames] += self.value
        if self.blocks_left is not None:
            self.blocks_left -= 1
            if self.blocks_left <= 0:
                self._finished = True

    def note_off(self):
        self.note_off_calls += 1
        self._released = True

    @property
    def released(self):
        return self._released

    @property
    def finished(self):
        return self._finished

    def amplitude(self):
        return self.level


class FakeEngine:
    def __init__(self):
        self.events = []

    def note_on(self, event, sample_rate):
        self.events.append((event, sample_rate))
        return FakeVoice()


def record(voice, seq, pitch=60, channel=0, voice_id=None):
    return ActiveVoice(voice_id if voice_id is not None else seq + 1, voice, pitch, channel, seq)


# --- event model -------------------------------------------------------

def test_midi_pitch_and_pitch_class_octave_round_trip():
    for pitch in range(21, 109):
        pitch_class, octave = pitch_class_octave(pitch)
        assert midi_pitch(pitch_class, octave) == pitch


def test_midi_pitch_uses_the_same_tuning_as_playback_note_frequency():
    from playback import note_frequency

    for pitch_class, octave in [(0, 4), (9, 4), (4, 2), (11, 6)]:
        assert frequency_for(midi_pitch(pitch_class, octave)) == pytest.approx(
            note_frequency(pitch_class, octave)
        )


def test_frequency_for_a4_is_440():
    assert frequency_for(69) == pytest.approx(440.0)


def test_note_on_from_pitch_class_carries_velocity_channel_and_patch():
    event = NoteOn.from_pitch_class(9, 4, velocity=0.5, channel=3, patch="rhodes")
    assert (event.pitch, event.velocity, event.channel, event.patch) == (69, 0.5, 3, "rhodes")


def test_note_on_from_midi_velocity_normalizes_and_clamps():
    assert NoteOn.from_midi_velocity(60, 127).velocity == pytest.approx(1.0)
    assert NoteOn.from_midi_velocity(60, 64).velocity == pytest.approx(64 / 127)
    assert NoteOn.from_midi_velocity(60, 999).velocity == pytest.approx(1.0)
    assert NoteOn.from_midi_velocity(60, -5).velocity == 0.0


# --- stealing policy (#105 decision 3) ---------------------------------

def test_select_steal_index_returns_none_for_no_voices():
    assert select_steal_index([]) is None


def test_select_steal_index_prefers_a_released_voice_over_an_older_held_one():
    held_old = record(FakeVoice(level=0.01), seq=0)
    released_new = record(FakeVoice(level=0.9), seq=5)
    released_new.voice.note_off()
    assert select_steal_index([held_old, released_new]) == 1


def test_select_steal_index_picks_the_quietest_among_released_voices():
    loud = record(FakeVoice(level=0.8), seq=0)
    quiet = record(FakeVoice(level=0.05), seq=1)
    for r in (loud, quiet):
        r.voice.note_off()
    assert select_steal_index([loud, quiet]) == 1


def test_select_steal_index_breaks_an_amplitude_tie_by_age():
    newer = record(FakeVoice(level=0.3), seq=9)
    older = record(FakeVoice(level=0.3), seq=2)
    for r in (newer, older):
        r.voice.note_off()
    assert select_steal_index([newer, older]) == 1


def test_select_steal_index_falls_back_to_the_oldest_held_note():
    # No released voice anywhere: the oldest still-held note goes, and
    # loudness is deliberately not consulted (a held note's level is a
    # performance decision, not an aging signal).
    newest = record(FakeVoice(level=0.01), seq=7)
    oldest = record(FakeVoice(level=0.99), seq=1)
    middle = record(FakeVoice(level=0.5), seq=4)
    assert select_steal_index([newest, oldest, middle]) == 1


# --- VoiceManager ------------------------------------------------------

def test_allocate_never_exceeds_the_polyphony_cap_and_never_refuses():
    manager = VoiceManager(polyphony=3)
    ids = [manager.allocate(FakeVoice(), pitch=60 + i) for i in range(10)]
    assert len(ids) == len(set(ids)) == 10          # every note got a voice; no id reuse
    assert manager.active_count() == 3              # hard cap held throughout
    assert manager.steal_count == 7
    # The most recent note-on is always among the survivors -- the
    # "never drop the note just pressed" rule.
    assert ids[-1] in [r.voice_id for r in manager.snapshot()]


def test_allocate_steals_the_released_voice_not_the_held_one():
    manager = VoiceManager(polyphony=2)
    held = FakeVoice(level=0.9)
    releasing = FakeVoice(level=0.1)
    manager.allocate(held, pitch=60)
    manager.allocate(releasing, pitch=62)
    releasing.note_off()
    manager.allocate(FakeVoice(), pitch=64)
    surviving = [r.voice for r in manager.snapshot()]
    assert held in surviving
    assert releasing not in surviving


def test_polyphony_is_re_read_live_from_a_callable():
    budget = {"value": 4}
    manager = VoiceManager(polyphony=lambda: budget["value"])
    for _ in range(4):
        manager.allocate(FakeVoice())
    assert manager.active_count() == 4
    budget["value"] = 2                      # a Settings-screen edit mid-session
    manager.allocate(FakeVoice())
    assert manager.active_count() == 2


def test_polyphony_floors_at_one_and_falls_back_on_a_broken_value():
    assert VoiceManager(polyphony=0).polyphony == 1
    assert VoiceManager(polyphony=-5).polyphony == 1
    assert VoiceManager(polyphony=lambda: "nonsense").polyphony == config.POLYPHONY_STANDALONE


def test_note_off_releases_the_oldest_held_voice_at_that_pitch():
    manager = VoiceManager(polyphony=8)
    first = FakeVoice()
    second = FakeVoice()
    manager.allocate(first, pitch=60, channel=0)
    manager.allocate(second, pitch=60, channel=0)
    assert manager.note_off(60) is not None
    assert first.released and not second.released
    manager.note_off(60)
    assert second.released


def test_note_off_is_channel_scoped_and_returns_none_when_nothing_matches():
    manager = VoiceManager(polyphony=8)
    voice = FakeVoice()
    manager.allocate(voice, pitch=60, channel=1)
    assert manager.note_off(60, channel=0) is None
    assert not voice.released
    assert manager.note_off(60, channel=1) is not None


def test_release_voice_by_id_and_a_stale_handle_is_a_no_op():
    manager = VoiceManager(polyphony=8)
    voice = FakeVoice()
    voice_id = manager.allocate(voice, pitch=60)
    assert manager.release_voice(voice_id) is True
    assert voice.released
    assert manager.release_voice(voice_id + 999) is False


def test_all_notes_off_releases_only_the_still_held_voices():
    manager = VoiceManager(polyphony=8)
    already = FakeVoice()
    manager.allocate(already, pitch=60)
    already.note_off()
    manager.allocate(FakeVoice(), pitch=62)
    manager.allocate(FakeVoice(), pitch=64)
    assert manager.all_notes_off() == 2
    assert all(r.voice.released for r in manager.snapshot())


def test_render_block_mixes_additively_and_retires_finished_voices():
    manager = VoiceManager(polyphony=8)
    manager.allocate(FakeVoice(value=1.0, blocks_to_finish=1))
    manager.allocate(FakeVoice(value=0.25, blocks_to_finish=3))
    out = np.zeros(16, dtype=np.float32)
    remaining = manager.render_block(out, 16)
    assert np.allclose(out, 1.25)               # both voices summed into one buffer
    assert remaining == 1                        # the one-block voice retired
    out[:] = 0.0
    manager.render_block(out, 16)
    assert np.allclose(out, 0.25)


def test_clear_drops_every_voice_without_releasing_them():
    manager = VoiceManager(polyphony=8)
    voice = FakeVoice()
    manager.allocate(voice)
    manager.clear()
    assert manager.active_count() == 0
    assert voice.note_off_calls == 0


# --- polyphony preference (#105 decision 4) ----------------------------

def test_polyphony_for_defaults_to_the_measured_constants(isolated_store):
    assert polyphony_for(detection_active=False) == config.POLYPHONY_STANDALONE
    assert polyphony_for(detection_active=True) == config.POLYPHONY_WITH_DETECTION
    assert config.POLYPHONY_WITH_DETECTION < config.POLYPHONY_STANDALONE


def test_polyphony_for_reads_the_preferences_override(isolated_store):
    isolated_store.set_preference("polyphony_standalone", 12)
    isolated_store.set_preference("polyphony_with_detection", 6)
    assert polyphony_for(detection_active=False) == 12
    assert polyphony_for(detection_active=True) == 6


def test_settings_screen_exposes_both_polyphony_fields():
    import settings_display

    keys = [spec.key for spec in settings_display.NUMERIC_FIELDS]
    assert "polyphony_standalone" in keys
    assert "polyphony_with_detection" in keys


# --- SoundEngine -------------------------------------------------------

def make_engine(**kwargs):
    kwargs.setdefault("engine", FakeEngine())
    kwargs.setdefault("sample_rate", 1000)
    kwargs.setdefault("block_size", 100)
    return SoundEngine(**kwargs)


def test_note_on_accepts_a_bare_pitch_and_a_note_on_object():
    engine = make_engine()
    engine.note_on(60, velocity=0.5, channel=2)
    engine.note_on(NoteOn(64, 0.25, 1, "pad"))
    events = [event for event, _ in engine.engine.events]
    assert (events[0].pitch, events[0].velocity, events[0].channel) == (60, 0.5, 2)
    assert (events[1].pitch, events[1].patch) == (64, "pad")
    assert engine.voices.active_count() == 2


def test_note_on_selects_polyphony_from_the_live_detection_flag(isolated_store):
    detection = {"on": False}
    engine = make_engine(detection_active=lambda: detection["on"])
    assert engine.voices.polyphony == config.POLYPHONY_STANDALONE
    detection["on"] = True
    assert engine.voices.polyphony == config.POLYPHONY_WITH_DETECTION


def test_scheduled_note_off_fires_on_the_block_covering_its_deadline():
    # 1000Hz sample rate, 100-frame blocks: a 0.25s note is due during the
    # third block (frame clock 300 >= deadline 250), not the second.
    engine = make_engine()
    voice_id = engine.note_on(60)
    voice = engine.voices.snapshot()[0].voice
    assert engine.schedule_note_off(voice_id, 0.25) == pytest.approx(250.0)

    outdata = np.zeros((100, 1), dtype=np.float32)
    for _ in range(2):
        engine._callback(outdata, 100, None, None)
    assert not voice.released
    engine._callback(outdata, 100, None, None)
    assert voice.released


def test_release_voice_cancels_a_pending_scheduled_note_off():
    engine = make_engine()
    voice_id = engine.note_on(60)
    engine.schedule_note_off(voice_id, 10.0)
    engine.release_voice(voice_id)
    assert engine._pending_offs == {}


def test_all_notes_off_clears_pending_deadlines_and_releases_everything():
    engine = make_engine()
    ids = [engine.note_on(60 + i) for i in range(3)]
    for voice_id in ids:
        engine.schedule_note_off(voice_id, 5.0)
    assert engine.all_notes_off() == 3
    assert engine._pending_offs == {}


def test_callback_advances_the_frame_clock_and_writes_a_soft_clipped_mix():
    engine = make_engine()
    engine.note_on(60)
    engine.note_on(64)                              # two FakeVoices, each rendering 1.0
    outdata = np.zeros((100, 1), dtype=np.float32)
    engine._callback(outdata, 100, None, None)
    assert engine._frame_clock == 100
    assert np.allclose(outdata[:, 0], np.tanh(2.0), atol=1e-6)   # summed, then tanh-clipped


def test_callback_is_silent_and_overwrites_stale_output_with_no_voices():
    engine = make_engine()
    outdata = np.ones((64, 1), dtype=np.float32)     # garbage the callback must overwrite
    engine._callback(outdata, 64, None, None)
    assert np.all(outdata[:, 0] == 0.0)


def test_callback_counts_driver_status_flags():
    engine = make_engine()
    outdata = np.zeros((64, 1), dtype=np.float32)
    engine._callback(outdata, 64, None, "underflow")
    engine._callback(outdata, 64, None, None)
    assert engine.callback_status_count == 1


def test_stop_is_idempotent_without_ever_starting_and_drops_voices():
    engine = make_engine()
    engine.note_on(60)
    assert engine.started is False
    engine.stop()
    engine.stop()
    assert engine.voices.active_count() == 0


# --- the effects bus (ticket #114) ------------------------------------------

def test_engine_starts_with_an_empty_chain_prepared_to_its_own_rate_and_block():
    from effects import EffectsChain

    engine = make_engine()
    assert isinstance(engine.effects, EffectsChain) and len(engine.effects) == 0
    chain = engine.set_effects(EffectsChain([_wet_delay()]))
    assert engine.effects is chain
    assert chain[0].sample_rate == 1000 and chain[0].block_size == 100     # prepare()d by set_effects
    assert engine.set_effects(None) is engine.effects and len(engine.effects) == 0


def _wet_delay():
    from effects import Delay

    return Delay(time=0.1, feedback=0.0, mix=1.0)   # 100 samples at the 1000Hz test rate = one block


def test_callback_runs_the_effects_bus_on_the_summed_mix_before_the_clip():
    from effects import EffectsChain

    engine = make_engine(effects=EffectsChain([_wet_delay()]))
    engine.note_on(60)
    engine.note_on(64)                              # two FakeVoices, each rendering 1.0
    outdata = np.zeros((100, 1), dtype=np.float32)
    engine._callback(outdata, 100, None, None)
    assert np.all(outdata[:, 0] == 0.0)             # fully wet, one block of delay: nothing yet
    engine._callback(outdata, 100, None, None)
    assert np.allclose(outdata[:, 0], np.tanh(2.0), atol=1e-6)   # the sum arrives one block late, then clips


def test_effects_state_survives_all_notes_off_but_not_stop():
    from effects import EffectsChain

    engine = make_engine(effects=EffectsChain([_wet_delay()]))
    engine.note_on(60)
    outdata = np.zeros((100, 1), dtype=np.float32)
    engine._callback(outdata, 100, None, None)
    engine.all_notes_off()                          # the tail must keep ringing (#104: the whole point of a delay)
    engine.voices.clear()                           # (FakeVoice never finishes on its own)
    engine._callback(outdata, 100, None, None)
    assert np.all(outdata[:, 0] > 0.0)
    engine._callback(outdata, 100, None, None)
    engine.stop()                                   # teardown drops the tail with the voices
    engine._callback(outdata, 100, None, None)
    assert np.all(outdata[:, 0] == 0.0)
