"""Tests for sampler.py (map #99, ticket #116): the sampler `Engine`/
`Voice` behind `sound_engine.py`'s seam.

The machine these run on has no speakers in the loop, so every claim is
*numerical*: a pitch-shifted sample's fundamental is measured by FFT and
compared to the ratio the root key says; loop continuity is a bound on
the sample-to-sample step across the seam; a choke is a count of nonzero
frames after the cut; gain is an RMS ratio. Every WAV is synthesized
through `wav_io.write_wav()` into `tmp_path` (no binary fixtures, never
the real `~/.config/note-color/samples/`), same convention as
`tests/test_wav_io.py` and `tests/test_patch_format.py`.
"""

import os
import time

import numpy as np
import pytest

import config
import patch_format
import sampler
import wav_io
from patch_format import Patch, Zone
from sampler import (
    SampleCache, SamplerEngine, SamplerVoice, SilentVoice, gain_to_linear, midi_velocity,
    playback_ratio, velocity_amplitude,
)
from sound_engine import NoteOn, SoundEngine

SR = 44100


def sine(freq=440.0, sample_rate=SR, seconds=0.5, amplitude=0.5):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def peak_hz(signal, sample_rate):
    """Parabolically-interpolated FFT peak -- the same measurement idea
    `pitch_detect.py` uses for YIN's own refinement."""
    n = signal.shape[0]
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(n)))
    k = int(np.argmax(spectrum))
    if 0 < k < spectrum.shape[0] - 1:
        a, b, c = spectrum[k - 1], spectrum[k], spectrum[k + 1]
        k = k + 0.5 * (a - c) / (a - 2 * b + c)
    return k * sample_rate / n


def render(voice, seconds, sample_rate=SR, block=512):
    """Drive a voice block by block the way `VoiceManager.render_block()`
    does, returning the concatenated output (stopping early once the
    voice reports finished)."""
    out = np.zeros(int(sample_rate * seconds), dtype=np.float32)
    for start in range(0, out.shape[0], block):
        frames = min(block, out.shape[0] - start)
        buf = np.zeros(frames, dtype=np.float32)
        voice.render(buf, frames)
        out[start:start + frames] = buf
        if voice.finished:
            break
    return out


@pytest.fixture
def samples_dir(tmp_path, monkeypatch):
    directory = tmp_path / "samples"
    directory.mkdir()
    monkeypatch.setattr(patch_format, "samples_dir", lambda: str(directory))
    return directory


@pytest.fixture
def a440(samples_dir):
    wav_io.write_wav(str(samples_dir / "a440.wav"), sine(440.0, seconds=1.0), SR)
    return wav_io.read_wav(str(samples_dir / "a440.wav"))


def voice(sample, pitch, root=69, engine_rate=SR, **kw):
    kw.setdefault("attack_seconds", 0.0)
    return SamplerVoice(sample, pitch, root, engine_rate, **kw)


# --- pure helpers -------------------------------------------------------------

def test_velocity_amplitude_routing():
    assert velocity_amplitude(0.5, 0.0) == 1.0  # patch ignores velocity
    assert velocity_amplitude(0.5, 1.0) == 0.5  # fully velocity-driven
    assert velocity_amplitude(0.5, 0.5) == 0.75
    assert velocity_amplitude(2.0, 1.0) == 1.0  # clamped


def test_gain_to_linear_in_db():
    assert gain_to_linear(0.0) == 1.0
    assert gain_to_linear(-6.0) == pytest.approx(0.501, abs=1e-3)
    assert gain_to_linear(-20.0) == pytest.approx(0.1)
    assert gain_to_linear(6.0) == pytest.approx(1.995, abs=1e-3)


def test_playback_ratio_combines_transpose_and_sample_rate():
    assert playback_ratio(69, 69, SR, SR) == 1.0
    assert playback_ratio(81, 69, SR, SR) == 2.0
    assert playback_ratio(57, 69, SR, SR) == 0.5
    assert playback_ratio(69, 69, 22050, SR) == 0.5
    assert playback_ratio(81, 69, 22050, SR) == 1.0
    assert playback_ratio(69, 69, 48000, SR) == pytest.approx(48000 / 44100)


def test_midi_velocity_reaches_127_at_full_velocity():
    """QWERTY plays at full velocity (#107); a `low_vel = 96` layer must
    therefore be reachable, which needs 1.0 -> 127 exactly."""
    assert midi_velocity(1.0) == 127
    assert midi_velocity(0.0) == 0
    assert midi_velocity(0.5) == 64
    assert midi_velocity(7.0) == 127


# --- SamplerVoice: pitch shifting --------------------------------------------

@pytest.mark.parametrize("semitones", [0, 7, 12, -12, -5, 3])
def test_pitch_shift_lands_on_the_root_key_ratio(a440, semitones):
    out = render(voice(a440, 69 + semitones), 0.5)
    expected = 440.0 * 2.0 ** (semitones / 12.0)
    assert peak_hz(out, SR) == pytest.approx(expected, rel=0.003)


def test_pitch_shift_stretches_time_as_a_real_sampler_does(a440):
    """Reading faster means finishing sooner: an octave up plays the
    sample in half its length. The honest sampler tradeoff, asserted so
    nobody later 'fixes' it into a time-stretch without noticing."""
    up = render(voice(a440, 81), 2.0)
    down = render(voice(a440, 57), 3.0)
    assert np.count_nonzero(up) == pytest.approx(a440.frames / 2, rel=0.02)
    assert np.count_nonzero(down) == pytest.approx(a440.frames * 2, rel=0.02)


@pytest.mark.parametrize("source_rate", [22050, 48000, 96000])
def test_sample_rate_mismatch_folds_into_the_ratio_and_keeps_the_pitch(tmp_path, source_rate):
    path = wav_io.write_wav(str(tmp_path / f"a_{source_rate}.wav"), sine(440.0, source_rate, 1.0), source_rate)
    sample = wav_io.read_wav(path)
    v = voice(sample, 69, engine_rate=SR)
    assert v.ratio == pytest.approx(source_rate / SR)
    out = render(v, 0.5)
    assert peak_hz(out, SR) == pytest.approx(440.0, rel=0.002)
    # ...and the sample lasts its real one second at the engine rate.
    full = render(voice(sample, 69, engine_rate=SR), 2.0)
    assert np.count_nonzero(full) == pytest.approx(SR, rel=0.01)


def test_stereo_source_plays_mono_at_the_right_pitch(tmp_path):
    """The engine is mono today: a stereo file is averaged on read and
    plays as one channel at its recorded pitch."""
    import wave

    path = str(tmp_path / "stereo.wav")
    mono = sine(440.0, 22050, 1.0)
    inter = np.empty(mono.shape[0] * 2, dtype="<i2")
    inter[0::2] = inter[1::2] = (mono * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(inter.tobytes())
    sample = wav_io.read_wav(path)
    assert sample.data.ndim == 1
    assert peak_hz(render(voice(sample, 69), 0.5), SR) == pytest.approx(440.0, rel=0.002)


def test_rendering_is_additive_and_carries_position_across_blocks(a440):
    v = voice(a440, 69)
    whole = render(voice(a440, 69), 0.1, block=4410)
    piecewise = render(v, 0.1, block=97)
    assert piecewise == pytest.approx(whole, abs=1e-6)
    buf = np.ones(64, dtype=np.float32)
    voice(a440, 69).render(buf, 64)
    assert np.all(buf != 1.0) or np.allclose(buf[1:], 1.0 + a440.data[1:64])


# --- SamplerVoice: one-shot vs. loop, note-off, attack ----------------------

def test_one_shot_ignores_note_off_and_finishes_at_its_natural_end(a440):
    v = voice(a440, 69)
    v.note_off()
    assert v.released and not v.finished
    out = render(v, 3.0)
    assert v.finished
    assert np.count_nonzero(out) == pytest.approx(a440.frames, rel=0.01)
    v.note_off()  # idempotent
    assert v.finished


def test_one_shot_that_ends_mid_block_marks_finished_in_that_block(tmp_path):
    sample = wav_io.read_wav(wav_io.write_wav(str(tmp_path / "short.wav"), np.ones(300, dtype=np.float32) * 0.5, SR))
    v = voice(sample, 60, root=60)
    buf = np.zeros(512, dtype=np.float32)
    v.render(buf, 512)
    assert v.finished
    assert np.count_nonzero(buf) == 300
    assert buf[299] == pytest.approx(0.5, abs=1e-3) and buf[300] == 0.0


def test_loop_sustains_past_the_sample_end_and_is_continuous_across_the_seam(tmp_path):
    """Loop 20 whole cycles of a 441Hz sine (period exactly 100 frames):
    if the seam interpolates correctly, the biggest sample-to-sample step
    anywhere in a second of output is no bigger than the sine's own
    slope. A discontinuity at the seam would show as a step near 1.0."""
    data = sine(441.0, SR, 0.1)  # 4410 frames
    path = wav_io.write_wav(str(tmp_path / "loop.wav"), data, SR, loop=(1000, 3000))
    sample = wav_io.read_wav(path)
    assert sample.loops
    v = voice(sample, 69)
    out = render(v, 1.0)
    assert not v.finished  # still looping, ten times past the file's own length
    assert np.count_nonzero(out) > 0.97 * out.shape[0]  # a 100-frame-period sine has an exact zero every 100th frame
    slope_bound = 0.5 * 2 * np.pi * 441.0 / SR
    assert np.abs(np.diff(out)).max() <= slope_bound * 1.02
    assert peak_hz(out, SR) == pytest.approx(441.0, rel=0.002)


def test_loop_note_off_fades_over_the_release_and_then_finishes(tmp_path):
    data = sine(441.0, SR, 0.1)
    sample = wav_io.read_wav(wav_io.write_wav(str(tmp_path / "loop.wav"), data, SR, loop=(1000, 3000)))
    v = voice(sample, 69, release_seconds=0.05)
    render(v, 0.3)
    v.note_off()
    tail = render(v, 1.0)
    assert v.finished
    sounding = np.count_nonzero(tail)
    assert sounding == pytest.approx(0.05 * SR, rel=0.05)
    # Envelope actually decays -- the last sounding frames are quieter than the first.
    assert np.abs(tail[:200]).max() > np.abs(tail[sounding - 200:sounding]).max()


def test_attack_ramp_suppresses_the_start_click(tmp_path):
    """A recording that starts on a non-zero sample would otherwise begin
    with a step; the ~2ms ramp turns that into a slope."""
    sample = wav_io.read_wav(wav_io.write_wav(str(tmp_path / "dc.wav"), np.full(4000, 0.8, dtype=np.float32), SR))
    ramped = render(SamplerVoice(sample, 60, 60, SR, attack_seconds=0.002), 0.05)
    assert ramped[0] < 0.02
    assert ramped[int(0.002 * SR) + 2] == pytest.approx(0.8, abs=1e-3)
    assert np.all(np.diff(ramped[:88]) > 0)
    instant = render(SamplerVoice(sample, 60, 60, SR, attack_seconds=0.0), 0.05)
    assert instant[0] == pytest.approx(0.8, abs=1e-3)
    assert config.SAMPLER_ATTACK_SECONDS < 0.01


# --- SamplerVoice: choke, gain, amplitude -------------------------------------

def test_choke_cuts_within_the_choke_window(a440):
    v = voice(a440, 69)
    render(v, 0.1)
    v.choke()
    assert v.released
    out = render(v, 0.5)
    assert v.finished
    assert np.count_nonzero(out) == pytest.approx(config.SAMPLER_CHOKE_SECONDS * SR, abs=2)
    assert config.SAMPLER_CHOKE_SECONDS < config.SAMPLER_RELEASE_SECONDS


def test_choke_during_a_release_shortens_it_never_lengthens_it(tmp_path):
    data = sine(441.0, SR, 0.1)
    sample = wav_io.read_wav(wav_io.write_wav(str(tmp_path / "loop.wav"), data, SR, loop=(1000, 3000)))
    v = voice(sample, 69, release_seconds=0.5)
    v.note_off()
    v.choke(fade_seconds=0.01)
    out = render(v, 1.0)
    assert np.count_nonzero(out) <= 0.01 * SR + 2
    w = voice(sample, 69)
    w.choke(fade_seconds=0.01)
    w.note_off()  # a later, longer release must not undo the choke
    assert np.count_nonzero(render(w, 1.0)) <= 0.01 * SR + 2


def test_gain_scales_output_amplitude(a440):
    reference = np.sqrt(np.mean(render(voice(a440, 69), 0.2) ** 2))
    for db, expected in [(-6.0, 0.501), (-20.0, 0.1), (6.0, 1.995)]:
        out = render(voice(a440, 69, amplitude=gain_to_linear(db)), 0.2)
        assert np.sqrt(np.mean(out ** 2)) / reference == pytest.approx(expected, abs=0.005)


def test_amplitude_reports_level_times_gain_for_the_stealing_policy(a440):
    v = voice(a440, 69, amplitude=0.25)
    assert v.amplitude() == pytest.approx(0.25)
    v.choke()
    render(v, 0.1)
    assert v.amplitude() == 0.0


def test_silent_voice_is_finished_from_birth():
    v = SilentVoice()
    assert v.finished and v.released and v.amplitude() == 0.0
    buf = np.ones(8, dtype=np.float32)
    v.render(buf, 8)
    v.note_off()
    assert np.all(buf == 1.0)


# --- SampleCache ------------------------------------------------------------

def test_cache_reads_once_and_reloads_when_the_file_changes(samples_dir, a440):
    cache = SampleCache(str(samples_dir))
    first = cache.get("a440.wav")
    assert first is not None and cache.get("a440.wav") is first
    path = samples_dir / "a440.wav"
    wav_io.write_wav(str(path), sine(880.0, seconds=0.2), SR)
    os.utime(path, (time.time() + 5, time.time() + 5))  # force a distinct mtime
    second = cache.get("a440.wav")
    assert second is not first and second.frames == int(0.2 * SR)


def test_cache_missing_and_undecodable_are_none_and_recover_when_the_file_appears(samples_dir):
    cache = SampleCache(str(samples_dir))
    assert cache.get("nope.wav") is None
    assert cache.get("") is None
    (samples_dir / "bad.wav").write_bytes(b"definitely not audio")
    assert cache.get("bad.wav") is None
    wav_io.write_wav(str(samples_dir / "nope.wav"), sine(), SR)
    assert cache.get("nope.wav") is not None
    assert cache.path_for("../../escape.wav") == str(samples_dir / "escape.wav")


def test_cache_preload_reports_every_unavailable_zone_once(samples_dir, a440):
    (samples_dir / "bad.wav").write_bytes(b"nope")
    patch = Patch(engine="sampler", zones=[
        Zone(sample="a440.wav"), Zone(sample="gone.wav"), Zone(sample="gone.wav"),
        Zone(sample="bad.wav"), Zone(sample=""),
    ])
    assert SampleCache(str(samples_dir)).preload(patch) == ["gone.wav", "bad.wav"]
    assert patch_format.missing_samples(patch, str(samples_dir)) == ["gone.wav"]  # exists-but-broken is this module's addition


# --- SamplerEngine ----------------------------------------------------------

def kit(samples_dir):
    """A three-pad kit plus a velocity-layered snare and an open/closed
    hi-hat pair. Every zone is one key wide, so `is_kit()` -- and there
    is no other code path for it to take."""
    for name, freq in [("kick.wav", 60.0), ("snare_soft.wav", 200.0), ("snare_hard.wav", 400.0),
                       ("hat_closed.wav", 3000.0), ("hat_open.wav", 2000.0)]:
        wav_io.write_wav(str(samples_dir / name), sine(freq, seconds=1.0), SR)
    patch = Patch(name="Kit", engine="sampler", zones=[
        Zone(sample="kick.wav", low_key=36, high_key=36, root_key=36),
        Zone(sample="snare_soft.wav", low_key=38, high_key=38, root_key=38, low_vel=0, high_vel=95),
        Zone(sample="snare_hard.wav", low_key=38, high_key=38, root_key=38, low_vel=96, high_vel=127),
        Zone(sample="hat_closed.wav", low_key=42, high_key=42, root_key=42, choke_group=1),
        Zone(sample="hat_open.wav", low_key=46, high_key=46, root_key=46, choke_group=1),
        Zone(sample="missing.wav", low_key=49, high_key=49, root_key=49, gain=-6.0),
    ])
    assert patch.is_kit()
    return patch


def test_engine_selects_by_key_and_velocity_layer(samples_dir):
    engine = SamplerEngine(kit(samples_dir), samples_directory=str(samples_dir))
    hard = engine.note_on(NoteOn(38, velocity=1.0), SR)
    soft = engine.note_on(NoteOn(38, velocity=0.5), SR)
    assert hard.zone.sample == "snare_hard.wav" and soft.zone.sample == "snare_soft.wav"
    assert peak_hz(render(hard, 0.3), SR) == pytest.approx(400.0, rel=0.003)
    assert engine.zone_for(36).sample == "kick.wav"
    assert engine.zone_for(37) is None


def test_engine_plays_a_zone_pitch_shifted_from_its_root_key(samples_dir, a440):
    patch = Patch(engine="sampler", zones=[Zone(sample="a440.wav", low_key=48, high_key=84, root_key=69)])
    engine = SamplerEngine(patch, samples_directory=str(samples_dir))
    for pitch, expected in [(69, 440.0), (72, 523.25), (57, 220.0)]:
        v = engine.note_on(NoteOn(pitch), SR)
        assert peak_hz(render(v, 0.4), SR) == pytest.approx(expected, rel=0.003)


def test_engine_choke_group_cuts_the_open_hat_when_the_closed_one_is_struck(samples_dir):
    engine = SamplerEngine(kit(samples_dir), samples_directory=str(samples_dir))
    open_hat = engine.note_on(NoteOn(46), SR)
    kick = engine.note_on(NoteOn(36), SR)  # group 0: chokes nothing
    render(open_hat, 0.05)
    assert not open_hat.released
    closed = engine.note_on(NoteOn(42), SR)
    assert open_hat.released and not kick.released and not closed.released
    tail = render(open_hat, 0.5)
    assert open_hat.finished
    assert np.count_nonzero(tail) == pytest.approx(config.SAMPLER_CHOKE_SECONDS * SR, abs=2)
    # And the other direction: an open hat cuts a sounding closed one too.
    closed2 = engine.note_on(NoteOn(42), SR)
    engine.note_on(NoteOn(46), SR)
    assert closed2.released
    assert all(v.zone.sample != "hat_open.wav" or not v.released or v is open_hat for v in engine.active_voices())


def test_engine_missing_sample_is_silent_and_unavailable_but_the_kit_still_loads(samples_dir):
    engine = SamplerEngine(kit(samples_dir), samples_directory=str(samples_dir))
    assert engine.unavailable_samples() == ["missing.wav"]
    v = engine.note_on(NoteOn(49), SR)
    assert isinstance(v, SilentVoice)
    assert isinstance(engine.note_on(NoteOn(100), SR), SilentVoice)  # no zone at all
    assert not isinstance(engine.note_on(NoteOn(36), SR), SilentVoice)  # the rest of the kit sounds


def test_engine_applies_zone_gain_patch_volume_and_velocity_routing(samples_dir, a440):
    def rms_for(zone_gain, volume, velocity_to_amp, velocity):
        patch = Patch(engine="sampler", zones=[Zone(sample="a440.wav", root_key=69, gain=zone_gain)])
        patch.voice.volume = volume
        patch.voice.velocity_to_amp = velocity_to_amp
        engine = SamplerEngine(patch, samples_directory=str(samples_dir))
        v = engine.note_on(NoteOn(69, velocity=velocity), SR)
        return np.sqrt(np.mean(render(v, 0.2) ** 2))

    reference = rms_for(0.0, 1.0, 0.0, 1.0)
    assert rms_for(-6.0, 1.0, 0.0, 1.0) / reference == pytest.approx(0.501, abs=0.005)
    assert rms_for(0.0, 0.5, 0.0, 1.0) / reference == pytest.approx(0.5, abs=0.005)
    assert rms_for(0.0, 1.0, 1.0, 0.5) / reference == pytest.approx(0.5, abs=0.005)
    assert rms_for(0.0, 1.0, 0.0, 0.5) / reference == pytest.approx(1.0, abs=0.005)


def test_engine_loads_a_kit_from_a_saved_patch_file_bare_names_only(samples_dir, tmp_path):
    """The whole round trip a user actually performs: import a sample,
    write a kit referencing it by bare name, load the file, play it."""
    source = wav_io.write_wav(str(tmp_path / "Downloads" / "my kick.wav"), sine(110.0, seconds=0.5), SR)
    name = wav_io.import_sample(source)
    patch = Patch(name="Mine", engine="sampler", zones=[Zone(sample=name, low_key=36, high_key=36, root_key=36)])
    path = patch_format.save_patch(patch, str(tmp_path / "patches" / "mine.toml"))
    text = open(path).read()
    assert 'sample = "my kick.wav"' in text and "Downloads" not in text
    loaded = patch_format.load_patch(path)
    assert loaded.is_kit()
    engine = SamplerEngine(loaded)  # default directory = the (monkeypatched) samples_dir()
    assert engine.unavailable_samples() == []
    v = engine.note_on(NoteOn(36), SR)
    assert peak_hz(render(v, 0.3), SR) == pytest.approx(110.0, rel=0.005)


def test_engine_default_patch_is_an_empty_sampler_patch_that_stays_silent():
    engine = SamplerEngine()
    assert engine.patch.engine == "sampler" and engine.patch.zones == []
    assert isinstance(engine.note_on(NoteOn(60), SR), SilentVoice)
    assert engine.unavailable_samples() == []


def test_engine_set_patch_swaps_the_kit_without_cutting_sounding_voices(samples_dir, a440):
    engine = SamplerEngine(kit(samples_dir), samples_directory=str(samples_dir))
    hat = engine.note_on(NoteOn(46), SR)
    engine.set_patch(Patch(engine="sampler", zones=[Zone(sample="a440.wav", low_key=42, high_key=42, root_key=69, choke_group=1)]))
    v = engine.note_on(NoteOn(42), SR)
    assert not hat.released  # the old kit's group-1 zone isn't this kit's
    assert v.zone.sample == "a440.wav"
    assert engine.zone_for(46) is None


def test_engine_prunes_finished_voices_from_its_active_list(samples_dir):
    engine = SamplerEngine(kit(samples_dir), samples_directory=str(samples_dir))
    kick = engine.note_on(NoteOn(36), SR)
    render(kick, 2.0)
    assert kick.finished
    engine.note_on(NoteOn(36), SR)
    assert kick not in engine.active_voices() and len(engine.active_voices()) == 1


# --- through sound_engine.SoundEngine (no device opened) -------------------

def test_sampler_engine_plugs_into_the_sound_engine_seam(samples_dir):
    """Drive `SoundEngine._callback()` directly with a NumPy buffer, the
    way tests/test_sound_engine.py does -- the sampler is just another
    `Engine` to it."""
    engine = SoundEngine(engine=SamplerEngine(kit(samples_dir), samples_directory=str(samples_dir)),
                         sample_rate=SR, block_size=512)
    engine.note_on(46)  # open hat
    engine.note_on(49)  # missing sample: silent, reclaimed on the first block
    out = np.zeros((512, 1), dtype=np.float32)
    engine._callback(out, 512, None, None)
    assert np.abs(out).max() > 0.0
    assert engine.voices.active_count() == 1
    engine.note_on(42)  # closed hat chokes the open one
    for _ in range(40):
        engine._callback(out, 512, None, None)
    pitches = [r.pitch for r in engine.voices.snapshot()]
    assert pitches == [42]
