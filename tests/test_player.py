"""Playing a Project through the sound engine (milestone 1, ticket #153).

Driven with a fake engine and no audio device, the split this repo applies
everywhere: the scheduling decisions are unit-tested, the device is smoke-
tested by hand.
"""

import pytest

from notecolor.audio import player as pl
from notecolor.audio.transport import Transport
from notecolor.project.model import (
    AUDIO_TRACK, AudioClip, Note, NoteClip, Project, TempoAnchor, TempoMap, Track,
)

RATE, BLOCK = 48000, 512


class FakeEngine:
    def __init__(self):
        self.started, self.offs, self.patches = [], [], []
        self._next = 0

    def note_on(self, event, velocity=1.0, channel=0, patch=None):
        self._next += 1
        self.started.append((event.pitch, round(velocity, 3), channel))
        self.patches.append(event.patch)
        return self._next

    def schedule_note_off(self, voice_id, delay_seconds):
        self.offs.append((voice_id, round(delay_seconds, 4)))


def _project(bpm=120.0, **kwargs):
    defaults = dict(
        tempo_map=TempoMap([TempoAnchor(0.0, bpm)]),
        tracks=[Track(name="a", clips=[NoteClip(length_beats=8, notes=[
            Note(0.0, 1.0, 60), Note(2.0, 0.5, 64), Note(4.0, 2.0, 67)])])])
    defaults.update(kwargs)
    return Project(**defaults)


def _run(project, blocks=400):
    engine = FakeEngine()
    transport = Transport(project.tempo_map, sample_rate=RATE)
    player = pl.ProjectPlayer(project, engine, transport)
    transport.play()
    for _ in range(blocks):
        player.on_block(BLOCK)
    return engine, transport, player


# --- flattening ------------------------------------------------------------


def test_clip_relative_positions_become_project_relative():
    project = Project(tracks=[Track(clips=[
        NoteClip(start_beat=8.0, notes=[Note(1.0, 1.0, 60)])])])
    assert [n.start_beat for n in pl.flatten(project)] == [9.0]


def test_notes_come_out_time_ordered_across_clips():
    project = Project(tracks=[Track(clips=[
        NoteClip(start_beat=8.0, notes=[Note(0.0, 1.0, 60)]),
        NoteClip(start_beat=0.0, notes=[Note(0.0, 1.0, 62)])])])
    assert [n.start_beat for n in pl.flatten(project)] == [0.0, 8.0]


def test_audio_tracks_are_skipped_visibly_rather_than_failing_at_playback():
    project = Project(tracks=[
        Track(name="audio", kind=AUDIO_TRACK,
              clips=[AudioClip(source="x.wav", length_beats=4)]),
        Track(name="notes", clips=[NoteClip(notes=[Note(0, 1, 60)])])])
    assert [n.pitch for n in pl.flatten(project)] == [60]


# --- mute and solo ---------------------------------------------------------


def test_a_muted_track_is_silent():
    project = _project()
    project.tracks[0].muted = True
    engine, _t, _p = _run(project)
    assert engine.started == []


def test_solo_wins_over_everything_not_soloed():
    project = _project()
    project.tracks.append(Track(name="b", soloed=True,
                                clips=[NoteClip(notes=[Note(0.0, 1.0, 72)])]))
    engine, _t, _p = _run(project)
    assert [pitch for pitch, _v, _c in engine.started] == [72]


def test_a_track_both_soloed_and_muted_stays_silent():
    """What every DAW does, and what a player expects after hitting both."""
    project = _project()
    project.tracks[0].soloed = True
    project.tracks[0].muted = True
    engine, _t, _p = _run(project)
    assert engine.started == []


# --- firing ----------------------------------------------------------------


def test_every_note_fires_exactly_once():
    engine, _t, _p = _run(_project())
    assert sorted(pitch for pitch, _v, _c in engine.started) == [60, 64, 67]


def test_nothing_fires_while_stopped():
    project = _project()
    engine = FakeEngine()
    transport = Transport(project.tempo_map, sample_rate=RATE)
    player = pl.ProjectPlayer(project, engine, transport)
    for _ in range(400):
        player.on_block(BLOCK)
    assert engine.started == []


def test_note_length_is_scheduled_in_seconds_from_the_tempo_map():
    """A 2-beat note at 120bpm is one second, and the tempo map is the only
    thing that gets to decide that."""
    engine, _t, _p = _run(_project(bpm=120.0))
    assert (3, 1.0) in engine.offs           # the third note, 2 beats long


def test_a_slower_tempo_makes_the_same_note_longer():
    engine, _t, _p = _run(_project(bpm=60.0), blocks=900)
    assert (3, 2.0) in engine.offs


def test_notes_route_to_a_channel_per_track():
    project = _project()
    project.tracks.append(Track(name="b", clips=[NoteClip(notes=[Note(0.0, 1.0, 72)])]))
    engine, _t, _p = _run(project)
    channels = {pitch: channel for pitch, _v, channel in engine.started}
    assert channels[60] == 0 and channels[72] == 1


def test_velocity_is_carried_through():
    project = Project(tempo_map=TempoMap([TempoAnchor(0.0, 120.0)]),
                      tracks=[Track(clips=[NoteClip(notes=[Note(0, 1, 60, velocity=0.4)])])])
    engine, _t, _p = _run(project, blocks=50)
    assert engine.started == [(60, 0.4, 0)]


def test_a_note_at_the_very_start_is_not_missed():
    """Beat 0 is the position most likely to fall outside a half-open window
    if the first block is handled differently from the rest."""
    engine, _t, _p = _run(_project(), blocks=4)
    assert [pitch for pitch, _v, _c in engine.started] == [60]


# --- looping ---------------------------------------------------------------


def test_notes_at_the_loop_seam_are_not_dropped():
    """A block that straddles the loop point covers two disjoint beat ranges.
    Firing only one of them silently loses notes exactly where a listener is
    most likely to notice."""
    project = _project()
    engine = FakeEngine()
    transport = Transport(project.tempo_map, sample_rate=RATE)
    player = pl.ProjectPlayer(project, engine, transport)
    transport.set_loop(0.0, 4.0, enabled=True)
    transport.play()
    for _ in range(2000):
        player.on_block(BLOCK)
    # Two notes inside the loop, played many times over.
    assert engine.started.count((60, 1.0, 0)) > 3
    assert engine.started.count((64, 1.0, 0)) > 3


# --- refresh ---------------------------------------------------------------


def test_refresh_picks_up_an_edit_without_rebuilding_the_player():
    project = _project()
    engine = FakeEngine()
    transport = Transport(project.tempo_map, sample_rate=RATE)
    player = pl.ProjectPlayer(project, engine, transport)
    project.tracks[0].muted = True
    player.refresh()
    transport.play()
    for _ in range(400):
        player.on_block(BLOCK)
    assert engine.started == []


# --- track patch (#156) -----------------------------------------------------


class FakeSynth:
    """Stands in for `synth_engine.SynthEngine`'s name -> `Patch` map."""

    def __init__(self):
        self.patches = {}


def test_unset_patch_name_plays_the_engines_default():
    engine, _t, _p = _run(_project())
    assert engine.patches == [None, None, None]


def test_a_tracks_patch_name_is_passed_on_every_note_on():
    project = _project(tracks=[Track(name="a", patch_name="Warm Pad", clips=[
        NoteClip(length_beats=8, notes=[Note(0.0, 1.0, 60)])])])
    engine, _t, _p = _run(project, blocks=50)
    assert engine.patches == ["Warm Pad"]


def test_a_named_patch_is_registered_with_the_process_wide_engine(monkeypatch, tmp_path):
    """`ProjectPlayer` resolves `patch_name` lazily (#156): a bare name in
    the project turns into a loaded `Patch` in the engine's own name map,
    found in `patch_format.patches_dir()` at playback time, not stashed on
    the model."""
    from notecolor.settings import patch_format

    monkeypatch.setattr(patch_format, "patches_dir", lambda: str(tmp_path))
    sentinel = object()
    monkeypatch.setattr(patch_format, "load_patch", lambda path: sentinel)
    (tmp_path / "Warm Pad.toml").write_text("")

    engine = FakeEngine()
    engine.engine = FakeSynth()
    project = _project(tracks=[Track(name="a", patch_name="Warm Pad", clips=[
        NoteClip(length_beats=8, notes=[Note(0.0, 1.0, 60)])])])
    transport = Transport(project.tempo_map, sample_rate=RATE)
    player = pl.ProjectPlayer(project, engine, transport)

    assert engine.engine.patches == {"Warm Pad": sentinel}


def test_a_patch_name_with_no_file_on_disk_is_not_registered(monkeypatch, tmp_path):
    """No file, no crash, no fabricated patch: `SynthEngine.patch_for()`'s
    existing unknown-name fallback to the engine's default handles it."""
    from notecolor.settings import patch_format

    monkeypatch.setattr(patch_format, "patches_dir", lambda: str(tmp_path))

    engine = FakeEngine()
    engine.engine = FakeSynth()
    project = _project(tracks=[Track(name="a", patch_name="Missing", clips=[
        NoteClip(length_beats=8, notes=[Note(0.0, 1.0, 60)])])])
    transport = Transport(project.tempo_map, sample_rate=RATE)
    pl.ProjectPlayer(project, engine, transport)

    assert engine.engine.patches == {}


def test_registration_is_a_no_op_when_the_engine_has_no_patch_map():
    """A `FakeEngine` with no `.engine` attribute at all (like every other
    test in this file) must not raise."""
    project = _project(tracks=[Track(name="a", patch_name="Warm Pad", clips=[
        NoteClip(length_beats=8, notes=[Note(0.0, 1.0, 60)])])])
    engine, _t, _p = _run(project, blocks=1)
    assert engine.patches == ["Warm Pad"]
