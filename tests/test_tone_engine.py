"""Tests for `tone_engine.py` (map #99, ticket #112): the interim concrete
Engine/Voice behind sound_engine.py's seam -- map #24's harmonic-stack +
ADSR voice reshaped into a block-rendered, note-off-driven one.

The load-bearing properties here are all numerically checkable with the
machine muted: the envelope's stage timings and levels, that the release
takes exactly `release_seconds` from wherever it was cut, that phase is
carried across block boundaries (rendering in two blocks must equal
rendering in one), and that the rendered waveform's spectral peak is the
note's own frequency.
"""

import numpy as np
import pytest

import config
from sound_engine import NoteOn, frequency_for
from tone_engine import DONE, RELEASE, SUSTAIN, ToneEngine, ToneVoice


SR = 4000


def make_voice(pitch=69, velocity=1.0, **kwargs):
    kwargs.setdefault("attack", 0.01)
    kwargs.setdefault("decay", 0.02)
    kwargs.setdefault("sustain_level", 0.5)
    kwargs.setdefault("release", 0.04)
    return ToneVoice(NoteOn(pitch, velocity), SR, **kwargs)


def envelope(voice, frames):
    return voice._envelope_block(frames)


# --- envelope stages ---------------------------------------------------

def test_attack_ramps_from_zero_to_one_over_the_attack_time():
    voice = make_voice()
    attack_samples = int(0.01 * SR)
    env = envelope(voice, attack_samples)
    assert env[0] == pytest.approx(1.0 / attack_samples, rel=1e-6)
    assert env[-1] == pytest.approx(1.0, abs=1e-9)
    assert np.all(np.diff(env) > 0)


def test_decay_falls_to_the_sustain_level_and_then_holds():
    voice = make_voice()
    total = int((0.01 + 0.02) * SR)
    envelope(voice, total)                    # run attack + decay to completion
    held = envelope(voice, 100)
    assert voice._stage == SUSTAIN
    assert np.allclose(held, 0.5)


def test_release_takes_exactly_release_seconds_from_wherever_it_was_cut():
    voice = make_voice()
    envelope(voice, int(0.03 * SR))           # reach sustain (level 0.5)
    voice.note_off()
    release_samples = int(0.04 * SR)
    env = envelope(voice, release_samples)
    assert env[0] < 0.5
    assert env[-1] == pytest.approx(0.0, abs=1e-9)
    assert voice.finished


def test_release_from_mid_attack_still_reaches_zero_in_release_seconds():
    # Cut a note during its attack: the fade must start from the level it
    # actually reached, not from the sustain level, and still take the
    # full release time (no click, no premature silence).
    voice = make_voice()
    envelope(voice, int(0.005 * SR))
    level = voice._level
    assert 0.0 < level < 1.0
    voice.note_off()
    env = envelope(voice, int(0.04 * SR))
    assert env[0] < level
    assert env[-1] == pytest.approx(0.0, abs=1e-9)
    assert voice._stage == DONE


def test_note_off_is_idempotent_and_does_not_restart_the_fade():
    voice = make_voice()
    envelope(voice, int(0.03 * SR))
    voice.note_off()
    rate = voice._release_rate
    envelope(voice, 20)
    voice.note_off()
    assert voice._release_rate == rate
    assert voice._stage == RELEASE


def test_released_and_finished_track_the_stages():
    voice = make_voice()
    assert not voice.released and not voice.finished
    voice.note_off()
    assert voice.released and not voice.finished
    envelope(voice, int(0.04 * SR) + 10)
    assert voice.finished


def test_amplitude_reports_the_current_level_scaled_by_velocity():
    voice = make_voice(velocity=0.5)
    assert voice.amplitude() == 0.0
    envelope(voice, int(0.03 * SR))
    assert voice.amplitude() == pytest.approx(0.5 * 0.5, abs=1e-6)


# --- rendering ---------------------------------------------------------

def test_render_adds_into_the_caller_buffer_rather_than_overwriting_it():
    voice = make_voice()
    out = np.full(64, 5.0, dtype=np.float32)
    voice.render(out, 64)
    assert np.all(out != 5.0) or np.any(out != 5.0)
    assert out.min() > 4.0                      # the voice's own output is small next to the 5.0 offset


def test_render_is_block_size_invariant_because_phase_carries_across_blocks():
    one_shot = make_voice()
    split = make_voice()
    a = np.zeros(256, dtype=np.float64)
    one_shot.render(a, 256)
    b = np.zeros(256, dtype=np.float64)
    chunk = np.zeros(128, dtype=np.float64)
    split.render(chunk, 128)
    b[:128] = chunk
    chunk[:] = 0.0
    split.render(chunk, 128)
    b[128:] = chunk
    assert np.allclose(a, b, atol=1e-9)


def test_render_after_finish_is_silent():
    voice = make_voice()
    voice.note_off()
    out = np.zeros(int(0.04 * SR) + 200, dtype=np.float64)
    voice.render(out, len(out))
    assert voice.finished
    out[:] = 0.0
    voice.render(out, len(out))
    assert np.all(out == 0.0)


def test_velocity_scales_the_rendered_amplitude_proportionally():
    loud = np.zeros(512, dtype=np.float64)
    quiet = np.zeros(512, dtype=np.float64)
    make_voice(velocity=1.0).render(loud, 512)
    make_voice(velocity=0.25).render(quiet, 512)
    assert np.max(np.abs(quiet)) == pytest.approx(0.25 * np.max(np.abs(loud)), rel=1e-6)


def test_rendered_output_stays_within_unit_amplitude():
    out = np.zeros(2048, dtype=np.float64)
    voice = make_voice(pitch=60, attack=0.001, decay=0.001, sustain_level=1.0)
    voice.render(out, 2048)
    assert np.max(np.abs(out)) <= 1.0


def test_rendered_waveform_peaks_at_the_notes_own_frequency():
    sample_rate = 44100
    voice = ToneVoice(NoteOn(69), sample_rate, attack=0.001, decay=0.001, sustain_level=1.0)
    frames = 16384
    out = np.zeros(frames, dtype=np.float64)
    voice.render(out, frames)
    spectrum = np.abs(np.fft.rfft(out * np.hanning(frames)))
    peak_hz = np.argmax(spectrum) * sample_rate / frames
    assert peak_hz == pytest.approx(frequency_for(69), rel=0.01)


def test_harmonic_stack_matches_configs_weights():
    # The fundamental, 2nd and 3rd partials must appear in
    # config.PLAYBACK_HARMONIC_WEIGHTS' descending proportions -- this is
    # the same instrument playback.synthesize_note() renders, not a new
    # timbre invented for the block-based path.
    sample_rate = 44100
    voice = ToneVoice(NoteOn(45), sample_rate, attack=0.001, decay=0.001, sustain_level=1.0)
    frames = 32768
    out = np.zeros(frames, dtype=np.float64)
    voice.render(out, frames)
    spectrum = np.abs(np.fft.rfft(out * np.hanning(frames)))
    fundamental = frequency_for(45)
    levels = []
    for harmonic_number in range(1, len(config.PLAYBACK_HARMONIC_WEIGHTS) + 1):
        bin_index = int(round(fundamental * harmonic_number * frames / sample_rate))
        levels.append(spectrum[bin_index - 2:bin_index + 3].max())
    ratios = [level / levels[0] for level in levels]
    expected = [w / config.PLAYBACK_HARMONIC_WEIGHTS[0] for w in config.PLAYBACK_HARMONIC_WEIGHTS]
    # Loose tolerance on purpose: the partials do not land on exact FFT
    # bin centres at this pitch, so windowed leakage costs a few percent
    # of each peak's measured height. The claim under test is the
    # descending proportion, not a calibrated magnitude.
    assert ratios == pytest.approx(expected, rel=0.15)


def test_engine_produces_one_voice_per_note_on_at_the_requested_pitch():
    engine = ToneEngine()
    voice = engine.note_on(NoteOn.from_pitch_class(0, 4, velocity=0.75), 44100)
    assert isinstance(voice, ToneVoice)
    assert voice.pitch == 60
    assert voice.frequency == pytest.approx(frequency_for(60))
    assert voice.velocity == 0.75


def test_engine_satisfies_the_sound_engine_voice_protocol_end_to_end():
    # The whole seam, with no audio device: SoundEngine -> ToneEngine ->
    # ToneVoice -> the callback's mix buffer.
    import sound_engine

    engine = sound_engine.SoundEngine(engine=ToneEngine(), sample_rate=SR, block_size=64)
    voice_id = engine.note_on(NoteOn.from_pitch_class(9, 4))
    outdata = np.zeros((64, 1), dtype=np.float32)
    engine._callback(outdata, 64, None, None)
    assert np.any(outdata[:, 0] != 0.0)
    engine.release_voice(voice_id)
    for _ in range(int(config.PLAYBACK_RELEASE_SECONDS * SR / 64) + 4):
        engine._callback(outdata, 64, None, None)
    assert engine.voices.active_count() == 0
