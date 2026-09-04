"""Tests for `sf2_playback.py` (map #99, ticket #117, research #102).

Two tiers, per this repo's "pure logic unit-tested, real I/O
smoke-tested" convention:

* Everything above the `real library` marker runs everywhere -- the
  availability probe (against injected importers), the fd-2 redirect,
  the downmix arithmetic, soundfont discovery/resolution on `tmp_path`,
  and every `SF2Engine`/`SF2Voice` decision (program selection, the
  velocity edge, the generation-checked note-off, stolen-voice
  finalization, the one-pull-per-block rule under both of its signals)
  against a `FakeSynth` with no FluidSynth involved.
* The real-library tests skip cleanly when `pyfluidsynth`/`libfluidsynth`
  is absent, and the rendering ones additionally when no soundfont can be
  found (`NOTE_COLOR_TEST_SOUNDFONT=<path>` points them at one that isn't
  in a standard location -- nothing is bundled). They verify numerically,
  with the machine muted: a silent block before any note, a non-silent
  correctly-shaped block the very next block after a note-on, decay
  after a note-off, exactly one pull per `SoundEngine._callback`, no
  driver status flags, and the per-block render cost at 64 voices
  against the callback budget.
"""

import ctypes
import gc
import os
import tempfile
import time

import numpy as np
import pytest

import config
import sf2_playback as sp
from patch_format import new_patch
from sound_engine import NoteOn, SoundEngine, VoiceManager


SR = 1000  # small sample rate so release-tail arithmetic stays readable


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeSynth:
    """Records every call `SF2Engine` makes and writes a recognisable
    int16 stereo block on `write_s16`: left = +1000, right = +3000 while
    any key is held, zeros otherwise."""

    def __init__(self, sfload_result=1, preset_names=None):
        self.calls = []
        self.held = set()
        self.sfload_result = sfload_result
        self.preset_names = preset_names or {}
        self.deleted = False
        self.writes = 0

    def sfload(self, path):
        self.calls.append(("sfload", path))
        return self.sfload_result

    def program_select(self, chan, sfid, bank, preset):
        self.calls.append(("program_select", chan, sfid, bank, preset))

    def sfpreset_name(self, sfid, bank, preset):
        return self.preset_names.get((bank, preset))

    def noteon(self, chan, key, vel):
        self.calls.append(("noteon", chan, key, vel))
        self.held.add((chan, key))

    def noteoff(self, chan, key):
        self.calls.append(("noteoff", chan, key))
        self.held.discard((chan, key))

    def all_notes_off(self, chan):
        self.calls.append(("all_notes_off", chan))
        self.held = {k for k in self.held if k[0] != chan}

    def get_active_voice_count(self):
        return len(self.held)

    def write_s16(self, frames, buffer):
        self.writes += 1
        value = (1000, 3000) if self.held else (0, 0)
        samples = np.tile(np.array(value, dtype=np.int16), frames)
        ctypes.memmove(buffer, samples.ctypes.data, frames * 4)

    def delete(self):
        self.deleted = True

    def of(self, name):
        return [c for c in self.calls if c[0] == name]


def make_engine(tmp_path, synth=None, **kwargs):
    bank = tmp_path / "bank.sf2"
    bank.write_bytes(b"RIFF")
    synth = synth or FakeSynth()
    kwargs.setdefault("sample_rate", SR)
    kwargs.setdefault("release_seconds", 0.1)
    return sp.SF2Engine(str(bank), synth=synth, **kwargs), synth


# --------------------------------------------------------------------------
# Availability probe
# --------------------------------------------------------------------------

def test_probe_reports_missing_package_with_install_hint():
    def importer():
        raise ModuleNotFoundError("No module named 'fluidsynth'")
    result = sp.sf2_availability(_importer=importer)
    assert result.available is False
    assert "pyfluidsynth is not installed" in result.reason
    assert "[sf2]" in result.reason


def test_probe_reports_missing_system_library_distinctly():
    def importer():
        raise ImportError("Couldn't find the FluidSynth library.")
    result = sp.sf2_availability(_importer=importer)
    assert result.available is False
    assert "libfluidsynth" in result.reason


def test_probe_treats_oserror_as_unavailable_not_a_crash():
    def importer():
        raise OSError("bad ELF header")
    result = sp.sf2_availability(_importer=importer)
    assert result == (False, "FluidSynth could not be loaded (bad ELF header)")


def test_probe_reports_available_with_empty_reason():
    assert sp.sf2_availability(_importer=lambda: None) == (True, "")


def test_probe_is_cached_and_resettable(monkeypatch):
    calls = []

    def importer():
        calls.append(1)
        raise ImportError("Couldn't find the FluidSynth library.")

    monkeypatch.setattr(sp, "_import_fluidsynth", importer)
    sp.reset_availability_cache()
    try:
        first = sp.sf2_availability()
        second = sp.sf2_availability()
        assert first == second and first.available is False
        assert len(calls) == 1
        assert sp.sf2_available() is False
    finally:
        sp.reset_availability_cache()


def test_engine_construction_degrades_to_sf2unavailable_when_probe_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "sf2_availability", lambda: sp.Sf2Availability(False, "nope"))
    with pytest.raises(sp.SF2Unavailable, match="nope"):
        sp.SF2Engine(str(tmp_path / "x.sf2"))


# --------------------------------------------------------------------------
# stderr redirect and downmix
# --------------------------------------------------------------------------

def test_silence_stderr_swallows_fd2_writes_and_restores():
    with tempfile.TemporaryFile() as capture:
        saved = os.dup(2)
        try:
            os.dup2(capture.fileno(), 2)
            with sp.silence_stderr():
                os.write(2, b"noise from a C library\n")
            os.write(2, b"visible\n")
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        capture.seek(0)
        assert capture.read() == b"visible\n"


def test_silence_stderr_restores_after_an_exception():
    with tempfile.TemporaryFile() as capture:
        saved = os.dup(2)
        try:
            os.dup2(capture.fileno(), 2)
            with pytest.raises(ValueError):
                with sp.silence_stderr():
                    raise ValueError
            os.write(2, b"after\n")
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        capture.seek(0)
        assert capture.read() == b"after\n"


def test_silence_fluidsynth_log_installs_null_handlers_once():
    """The runtime-warning mitigation: one call per process, against the
    `CDLL` `pyfluidsynth` already holds, for the chatty levels only."""
    sp.reset_availability_cache()
    calls = []

    class FakeLib:
        def fluid_set_log_function(self, level, fun, data):
            calls.append((level.value, fun.value, data.value))

    class FakeModule:
        _fl = FakeLib()

    assert sp.silence_fluidsynth_log(FakeModule()) is True
    assert [c[0] for c in calls] == list(sp.FLUID_LOG_LEVELS_TO_SILENCE)
    assert all(fun is None and data is None for _, fun, data in calls)
    # PANIC (0) and ERR (1) are deliberately left reporting.
    assert 0 not in [c[0] for c in calls] and 1 not in [c[0] for c in calls]
    assert sp.silence_fluidsynth_log(FakeModule()) is True
    assert len(calls) == len(sp.FLUID_LOG_LEVELS_TO_SILENCE)   # cached, not repeated
    sp.reset_availability_cache()


def test_silence_fluidsynth_log_degrades_when_the_symbol_is_missing():
    """A libfluidsynth build not exporting the symbol keeps its warnings
    -- cosmetic, never an error."""
    sp.reset_availability_cache()

    class NoSymbolLib:
        def __getattr__(self, name):
            raise AttributeError(name)

    class FakeModule:
        _fl = NoSymbolLib()

    assert sp.silence_fluidsynth_log(FakeModule()) is False
    sp.reset_availability_cache()


def test_downmix_averages_channels_and_scales_to_unit_float32():
    block = np.array([32767, 32767, -32768, 0, 1000, 3000, 9, 9], dtype=np.int16)
    mono = sp.downmix(block, 3)
    assert mono.dtype == np.float32 and mono.shape == (3,)
    assert mono[0] == pytest.approx(32767 / 32768)
    assert mono[1] == pytest.approx(-0.5)
    assert mono[2] == pytest.approx(2000 / 32768)


def test_downmix_of_zeros_is_silence():
    assert not np.any(sp.downmix(np.zeros(1024, dtype=np.int16), 512))


# --------------------------------------------------------------------------
# Soundfont discovery (nothing bundled)
# --------------------------------------------------------------------------

def test_discover_orders_by_dir_priority_then_sf2_before_sf3_then_name(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(); second.mkdir()
    for name in ("zeta.sf3", "beta.sf2", "Alpha.SF2", "readme.txt"):
        (first / name).write_bytes(b"x")
    (second / "other.sf2").write_bytes(b"x")
    found = sp.discover_soundfonts([str(first), str(second), str(tmp_path / "missing")])
    assert [os.path.basename(p) for p in found] == ["Alpha.SF2", "beta.sf2", "zeta.sf3", "other.sf2"]


def test_discover_skips_directories_and_dedupes(tmp_path):
    (tmp_path / "dir.sf2").mkdir()
    (tmp_path / "real.sf2").write_bytes(b"x")
    found = sp.discover_soundfonts([str(tmp_path), str(tmp_path)])
    assert [os.path.basename(p) for p in found] == ["real.sf2"]


def test_search_dirs_put_samples_dir_first_and_include_system_paths(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg")
    monkeypatch.setenv("HOMEBREW_PREFIX", "/brew")
    dirs = sp.soundfont_search_dirs(samples_dir="/samples")
    assert dirs[0] == "/samples"
    assert dirs[1] == "/xdg/soundfonts"
    assert "/usr/share/soundfonts" in dirs and "/usr/share/sounds/sf2" in dirs
    assert dirs[-1] == "/brew/share/soundfonts"


def test_resolve_named_bank_never_substitutes_another(tmp_path):
    (tmp_path / "piano.sf2").write_bytes(b"x")
    assert sp.resolve_soundfont("piano.sf2", preference="", dirs=[str(tmp_path)]) == str(tmp_path / "piano.sf2")
    assert sp.resolve_soundfont("/evil/../piano.sf2", preference="", dirs=[str(tmp_path)]) == str(tmp_path / "piano.sf2")
    assert sp.resolve_soundfont("missing.sf2", preference="", dirs=[str(tmp_path)]) is None


def test_resolve_unnamed_prefers_preference_then_first_discovered(tmp_path):
    (tmp_path / "b.sf2").write_bytes(b"x")
    pref = tmp_path / "pref.sf2"
    assert sp.resolve_soundfont("", preference=str(pref), dirs=[str(tmp_path)]) == str(tmp_path / "b.sf2")
    pref.write_bytes(b"x")
    assert sp.resolve_soundfont("", preference=str(pref), dirs=[str(tmp_path)]) == str(pref)
    assert sp.resolve_soundfont("", preference="", dirs=[str(tmp_path / "empty")]) is None


def test_preference_is_read_through_config_store(monkeypatch, tmp_path):
    monkeypatch.setattr(sp.store, "preference", lambda name, default: "~/banks/x.sf2" if name == "soundfont_path" else default)
    assert sp.preferred_soundfont_path() == os.path.expanduser("~/banks/x.sf2")
    monkeypatch.setattr(sp.store, "preference", lambda name, default: default)
    assert sp.preferred_soundfont_path() == ""


def test_sf2_status_phrases(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "sf2_availability", lambda: sp.Sf2Availability(False, "no lib"))
    assert sp.sf2_status(dirs=[str(tmp_path)], preference="") == "unavailable (no lib)"
    monkeypatch.setattr(sp, "sf2_availability", lambda: sp.Sf2Availability(True, ""))
    assert sp.sf2_status(dirs=[str(tmp_path)], preference="") == "no soundfont found"
    (tmp_path / "gm.sf2").write_bytes(b"x")
    assert sp.sf2_status(dirs=[str(tmp_path)], preference="") == "gm.sf2"


# --------------------------------------------------------------------------
# SF2Voice
# --------------------------------------------------------------------------

class RecordingEngine:
    def __init__(self):
        self.released = []
        self.rendered = []

    def release(self, voice):
        self.released.append(voice)

    def render_voice(self, voice, out, frames):
        self.rendered.append((voice, frames))


def test_voice_release_tail_timing_and_idempotent_note_off():
    engine = RecordingEngine()
    voice = sp.SF2Voice(engine, 0, 60, 0.8, SR, release_seconds=0.1)
    assert not voice.released and not voice.finished
    assert voice.amplitude() == pytest.approx(0.8)
    voice.note_off()
    voice.note_off()
    assert engine.released == [voice]
    assert voice.released and not voice.finished
    out = np.zeros(50, dtype=np.float32)
    voice.render(out, 50)
    assert voice.amplitude() == pytest.approx(0.4)
    assert not voice.finished
    voice.render(out, 50)
    assert voice.finished and voice.amplitude() == 0.0
    assert engine.rendered == [(voice, 50), (voice, 50)]


def test_voice_render_pulls_before_advancing_the_tail():
    """A lone releasing voice must still pull the block it finishes on."""
    engine = RecordingEngine()
    voice = sp.SF2Voice(engine, 0, 60, 1.0, SR, release_seconds=0.1)
    voice.note_off()
    voice.render(np.zeros(100, dtype=np.float32), 100)
    assert engine.rendered and voice.finished


def test_voice_default_release_tail_comes_from_config():
    voice = sp.SF2Voice(RecordingEngine(), 0, 60, 1.0, SR)
    assert voice.release_seconds == config.SF2_RELEASE_TAIL_SECONDS


# --------------------------------------------------------------------------
# SF2Engine against the fake
# --------------------------------------------------------------------------

def test_load_selects_program_on_channel_0(tmp_path):
    engine, synth = make_engine(tmp_path, bank=8, preset=42)
    assert synth.of("sfload") == [("sfload", str(tmp_path / "bank.sf2"))]
    assert synth.of("program_select") == [("program_select", 0, 1, 8, 42)]
    assert engine.sfid == 1 and engine.soundfont_name == "bank.sf2"


def test_load_failure_raises_sf2error_and_missing_file_soundfontnotfound(tmp_path):
    with pytest.raises(sp.SoundfontNotFound):
        sp.SF2Engine(str(tmp_path / "absent.sf2"), synth=FakeSynth())
    with pytest.raises(sp.SF2Error, match="could not load"):
        make_engine(tmp_path, synth=FakeSynth(sfload_result=-1))


def test_program_name_blank_when_not_in_bank(tmp_path):
    engine, _ = make_engine(tmp_path, synth=FakeSynth(preset_names={(0, 0): "Piano"}))
    assert engine.program_name() == "Piano"
    assert engine.program_name(0, 99) == ""


def test_select_program_without_a_bank_raises(tmp_path):
    engine = sp.SF2Engine(synth=FakeSynth(), sample_rate=SR)
    with pytest.raises(sp.SF2Error, match="no soundfont"):
        engine.select_program(0)


def test_note_on_converts_velocity_to_midi_and_programs_new_channels_once(tmp_path):
    engine, synth = make_engine(tmp_path, bank=1, preset=2)
    engine.note_on(NoteOn(60, 1.0))
    engine.note_on(NoteOn(200, 0.0, channel=3))
    engine.note_on(NoteOn(62, 0.5, channel=3))
    assert synth.of("noteon") == [("noteon", 0, 60, 127), ("noteon", 3, 127, 1), ("noteon", 3, 62, 64)]
    assert synth.of("program_select") == [("program_select", 0, 1, 1, 2), ("program_select", 3, 1, 1, 2)]


def test_note_off_reaches_fluidsynth_once(tmp_path):
    engine, synth = make_engine(tmp_path)
    voice = engine.note_on(NoteOn(60))
    voice.note_off()
    voice.note_off()
    assert synth.of("noteoff") == [("noteoff", 0, 60)]


def test_older_note_off_cannot_silence_a_newer_note_on_the_same_key(tmp_path):
    engine, synth = make_engine(tmp_path)
    old = engine.note_on(NoteOn(60))
    new = engine.note_on(NoteOn(60))
    old.note_off()
    assert synth.of("noteoff") == []
    new.note_off()
    assert synth.of("noteoff") == [("noteoff", 0, 60)]


def test_stolen_voice_is_finalized_with_a_note_off(tmp_path):
    """The voice manager drops a stolen voice without a note-off; the
    weakref finalizer sends it so the note doesn't ring forever."""
    engine, synth = make_engine(tmp_path)
    manager = VoiceManager(polyphony=1)
    manager.allocate(engine.note_on(NoteOn(60)), 60)
    manager.allocate(engine.note_on(NoteOn(62)), 62)   # steals the held 60
    gc.collect()
    assert manager.steal_count == 1
    assert synth.of("noteoff") == [("noteoff", 0, 60)]
    assert [v.key for v in engine.live_voices()] == [62]


def test_one_pull_per_block_across_many_voices(tmp_path):
    engine, synth = make_engine(tmp_path)
    manager = VoiceManager(polyphony=16)
    for pitch in (60, 64, 67):
        manager.allocate(engine.note_on(NoteOn(pitch)), pitch)
    for _ in range(3):
        out = np.zeros(8, dtype=np.float32)
        manager.render_block(out, 8)
        assert np.allclose(out, 2000 / 32768)       # mixed once, not three times
    assert synth.writes == 3 and engine.blocks_rendered == 3


def test_new_buffer_signal_covers_a_voice_held_outside_the_manager(tmp_path):
    engine, synth = make_engine(tmp_path)
    held = engine.note_on(NoteOn(36))                # never given to a manager
    manager = VoiceManager(polyphony=16)
    manager.allocate(engine.note_on(NoteOn(60)), 60)
    for _ in range(2):
        out = np.zeros(8, dtype=np.float32)
        manager.render_block(out, 8)
        assert np.allclose(out, 2000 / 32768)
    assert synth.writes == 2
    assert held.key == 36


def test_primary_signal_covers_a_reused_buffer(tmp_path):
    engine, synth = make_engine(tmp_path)
    manager = VoiceManager(polyphony=16)
    manager.allocate(engine.note_on(NoteOn(60)), 60)
    manager.allocate(engine.note_on(NoteOn(64)), 64)
    out = np.zeros(8, dtype=np.float32)
    for _ in range(3):
        out[:] = 0
        manager.render_block(out, 8)
        assert np.allclose(out, 2000 / 32768)
    assert synth.writes == 3


def test_release_tail_keeps_pulling_until_the_last_voice_finishes(tmp_path):
    engine, synth = make_engine(tmp_path, release_seconds=0.016)   # 16 frames at SR=1000
    manager = VoiceManager(polyphony=16)
    manager.allocate(engine.note_on(NoteOn(60)), 60)
    manager.all_notes_off()
    for expected_left in (1, 0):
        out = np.zeros(8, dtype=np.float32)
        assert manager.render_block(out, 8) == expected_left
    assert synth.writes == 2
    out = np.zeros(8, dtype=np.float32)
    manager.render_block(out, 8)
    assert synth.writes == 2 and not np.any(out)


def test_render_block_shape_and_silence_with_nothing_held(tmp_path):
    engine, _ = make_engine(tmp_path)
    block = engine.render_block(512)
    assert block.dtype == np.float32 and block.shape == (512,) and not np.any(block)
    assert engine.render_block(0).shape == (0,)


def test_all_notes_off_and_close_are_safe(tmp_path):
    engine, synth = make_engine(tmp_path)
    # Bound, not discarded: an unreferenced voice handle is finalized
    # immediately under CPython refcounting and sends its own note-off
    # (the stolen-voice path), which is exactly what the test above this
    # one asserts -- so holding the handles is what keeps these notes on.
    held = [engine.note_on(NoteOn(60)), engine.note_on(NoteOn(62, channel=2))]
    assert engine.active_voice_count() == 2
    assert engine.all_notes_off() == 2
    assert sorted(synth.of("all_notes_off")) == [("all_notes_off", 0), ("all_notes_off", 2)]
    engine.close(); engine.close()
    assert synth.deleted and engine.closed and engine.active_voice_count() == 0
    assert not np.any(engine.render_block(4))
    with pytest.raises(sp.SF2Error, match="closed"):
        engine.note_on(NoteOn(60))


def test_engine_through_sound_engine_callback(tmp_path):
    engine, synth = make_engine(tmp_path, sample_rate=44100)
    sound = SoundEngine(engine=engine, sample_rate=44100)
    sound.note_on(60)
    out = np.zeros((16, 1), dtype=np.float32)
    sound._callback(out, 16, None, None)
    assert np.allclose(out[:, 0], np.tanh(2000 / 32768))
    assert synth.writes == 1 and sound.callback_status_count == 0


# --------------------------------------------------------------------------
# Patch -> engine
# --------------------------------------------------------------------------

def test_engine_for_patch_resolves_named_bank_and_program(tmp_path):
    (tmp_path / "piano.sf2").write_bytes(b"x")
    patch = new_patch("P", "sf2")
    patch.sf2.soundfont, patch.sf2.bank, patch.sf2.preset = "piano.sf2", 0, 5
    synth = FakeSynth()
    engine = sp.engine_for_patch(patch, dirs=[str(tmp_path)], preference="", synth=synth, sample_rate=SR)
    assert engine.soundfont_name == "piano.sf2"
    assert synth.of("program_select") == [("program_select", 0, 1, 0, 5)]


def test_engine_for_patch_errors_are_all_sf2error(monkeypatch, tmp_path):
    patch = new_patch("P", "sf2")
    monkeypatch.setattr(sp, "sf2_availability", lambda: sp.Sf2Availability(False, "no lib"))
    with pytest.raises(sp.SF2Unavailable, match="no lib"):
        sp.engine_for_patch(patch, dirs=[str(tmp_path)], preference="")
    monkeypatch.setattr(sp, "sf2_availability", lambda: sp.Sf2Availability(True, ""))
    with pytest.raises(sp.SoundfontNotFound, match="no soundfont found"):
        sp.engine_for_patch(patch, dirs=[str(tmp_path)], preference="", synth=FakeSynth())
    patch.sf2.soundfont = "gone.sf2"
    with pytest.raises(sp.SoundfontNotFound, match="gone.sf2"):
        sp.engine_for_patch(patch, dirs=[str(tmp_path)], preference="", synth=FakeSynth())
    assert issubclass(sp.SF2Unavailable, sp.SF2Error) and issubclass(sp.SoundfontNotFound, sp.SF2Error)


# --------------------------------------------------------------------------
# Real library (skips cleanly without pyfluidsynth/libfluidsynth or a bank)
# --------------------------------------------------------------------------

real_library = pytest.mark.skipif(not sp.sf2_available(),
                                  reason=f"SF2 unavailable: {sp.sf2_availability().reason}")


def _test_soundfont():
    explicit = os.environ.get("NOTE_COLOR_TEST_SOUNDFONT")
    if explicit and os.path.isfile(explicit):
        return explicit
    return sp.resolve_soundfont("", preference=None)


@pytest.fixture
def real_engine():
    path = _test_soundfont()
    if path is None:
        pytest.skip("no soundfont found (set NOTE_COLOR_TEST_SOUNDFONT=<path>)")
    engine = sp.SF2Engine(path)
    yield engine
    engine.close()


@real_library
def test_real_synth_construction_writes_nothing_to_stderr():
    with tempfile.TemporaryFile() as capture:
        saved = os.dup(2)
        try:
            os.dup2(capture.fileno(), 2)
            engine = sp.SF2Engine()
            engine.close()
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        capture.seek(0)
        assert capture.read() == b""


@real_library
def test_real_synth_never_starts_an_audio_driver():
    engine = sp.SF2Engine()
    try:
        assert engine.synth.audio_driver is None
        assert engine.synth.get_setting("synth.polyphony") == config.SF2_POLYPHONY
        assert engine.synth.get_setting("synth.sample-rate") == float(config.PLAYBACK_SAMPLE_RATE)
    finally:
        engine.close()


@real_library
def test_real_render_silent_then_audible_next_block_then_decays(real_engine):
    frames = config.PLAYBACK_BLOCK_SIZE
    before = real_engine.render_block(frames)
    assert before.shape == (frames,) and before.dtype == np.float32
    assert np.abs(before).max() < 1e-3
    voice = real_engine.note_on(NoteOn(60, 0.9))
    first = real_engine.render_block(frames)
    assert np.abs(first).max() > 1e-3                 # audible in the very next block
    assert np.abs(first).max() <= 1.0
    assert real_engine.active_voice_count() >= 1
    for _ in range(20):
        real_engine.render_block(frames)
    held_rms = float(np.sqrt(np.mean(real_engine.render_block(frames) ** 2)))
    voice.note_off()
    for _ in range(int(2.0 * config.PLAYBACK_SAMPLE_RATE / frames)):
        real_engine.render_block(frames)
    tail_rms = float(np.sqrt(np.mean(real_engine.render_block(frames) ** 2)))
    assert tail_rms < held_rms * 0.5


@real_library
def test_real_render_through_sound_engine_callback_once_per_block(real_engine):
    sound = SoundEngine(engine=real_engine)
    sound.note_on(64); sound.note_on(67)
    peaks = []
    for _ in range(4):
        out = np.zeros((config.PLAYBACK_BLOCK_SIZE, 1), dtype=np.float32)
        pulls = real_engine.blocks_rendered
        sound._callback(out, config.PLAYBACK_BLOCK_SIZE, None, None)
        assert real_engine.blocks_rendered == pulls + 1
        peaks.append(float(np.abs(out).max()))
    assert max(peaks) > 1e-3 and sound.callback_status_count == 0


@real_library
def test_real_note_on_past_polyphony_writes_nothing_to_stderr(real_engine):
    """FluidSynth logs one "Failed to allocate a synthesis process" line
    per stolen voice, from inside `noteon()`, long after construction --
    the runtime half of #102's TUI hazard. Deliberately overruns
    `synth.polyphony` and asserts fd 2 stays clean."""
    voices = []
    with tempfile.TemporaryFile() as capture:
        saved = os.dup(2)
        try:
            os.dup2(capture.fileno(), 2)
            for i in range(config.SF2_POLYPHONY + 32):
                voices.append(real_engine.note_on(NoteOn(24 + (i % 84))))
            real_engine.render_block(config.PLAYBACK_BLOCK_SIZE)
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        capture.seek(0)
        assert capture.read() == b""
    assert real_engine.active_voice_count() <= config.SF2_POLYPHONY


@real_library
def test_real_render_cost_at_64_voices_fits_the_block_budget(real_engine):
    """Reproduces #102's headroom measurement (0.951 ms/block mean at 64
    voices, reverb+chorus on) against whatever bank is installed. The
    assertion is loose on purpose -- a CI box is not the measurement
    machine -- the number itself is what gets reported."""
    frames = config.PLAYBACK_BLOCK_SIZE
    budget_ms = 1000.0 * frames / config.PLAYBACK_SAMPLE_RATE
    voices = [real_engine.note_on(NoteOn(36 + i)) for i in range(64)]
    real_engine.render_block(frames)
    timings = []
    for _ in range(300):
        start = time.perf_counter()
        real_engine.render_block(frames)
        timings.append((time.perf_counter() - start) * 1000.0)
    mean = float(np.mean(timings))
    print(f"\nSF2 render: mean {mean:.3f} ms/block, p95 {np.percentile(timings, 95):.3f}, "
          f"max {max(timings):.3f} ({100 * mean / budget_ms:.1f}% of {budget_ms:.2f} ms), "
          f"{real_engine.active_voice_count()} FluidSynth voices for {len(voices)} keys")
    assert mean < budget_ms * 0.5
