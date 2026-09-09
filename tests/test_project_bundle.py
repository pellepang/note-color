"""Reading and writing `.ncproj` bundles (ticket #154).

The versioning behaviour gets the most attention, because it is this repo's one
deliberate departure from the degrade-rather-than-raise posture `config_store`
and `patch_format` take (see `docs/adr/0001-ncproj-project-bundle.md`). The
distinction being asserted: a project from the *future* is refused, a project
with *missing content* still opens.
"""

import json
import os

import pytest

from notecolor.project import bundle
from notecolor.project.model import (
    PROJECT_VERSION,
    AudioClip,
    ChordSpan,
    Note,
    NoteClip,
    Project,
    TempoAnchor,
    TempoMap,
    TimeSignature,
    Track,
)


def _project():
    return Project(
        name="Demo",
        tempo_map=TempoMap([TempoAnchor(0.0, 96.0), TempoAnchor(8.0, 132.0)]),
        time_signature=TimeSignature(3, 4),
        key_fifths=-2,
        key_mode="minor",
        sample_rate=44100,
        tracks=[
            Track(name="Bass", color_pitch_class=0, gain_db=-3.0, pan=-0.25,
                  clips=[NoteClip(name="verse", start_beat=0, length_beats=8,
                                  notes=[Note(0.0, 1.0, 40, 0.8, 0.91),
                                         Note(2.0, 0.5, 47)])]),
            Track(name="Vox", kind="audio", muted=True,
                  clips=[AudioClip(name="take 1", start_beat=4, length_beats=16,
                                   source="take1.wav", source_offset_samples=128,
                                   source_length_samples=44100, gain_db=1.5,
                                   fade_in_beats=0.25)]),
        ],
        chords=[ChordSpan(0.0, 4.0, "Cm7"), ChordSpan(4.0, 8.0, "F7", derived=False)],
    )


# --- round trip ------------------------------------------------------------


def test_saving_creates_the_bundle_layout(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    assert os.path.basename(path) == "Demo.ncproj"
    assert sorted(os.listdir(path)) == ["audio", "autosave", "peaks", "project.json"]


def test_the_suffix_is_added_once_and_only_once(tmp_path):
    first = bundle.save_project(_project(), tmp_path / "A")
    second = bundle.save_project(_project(), tmp_path / "B.ncproj")
    assert first.endswith("A.ncproj") and second.endswith("B.ncproj")
    assert not second.endswith(".ncproj.ncproj")


def test_everything_survives_a_round_trip(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    loaded = bundle.load_project(path)

    assert (loaded.name, loaded.key_fifths, loaded.key_mode, loaded.sample_rate) == \
           ("Demo", -2, "minor", 44100)
    assert (loaded.time_signature.numerator, loaded.time_signature.denominator) == (3, 4)
    assert [(a.beat, a.bpm) for a in loaded.tempo_map.anchors] == [(0.0, 96.0), (8.0, 132.0)]

    bass, vox = loaded.tracks
    assert (bass.name, bass.color_pitch_class, bass.gain_db, bass.pan) == ("Bass", 0, -3.0, -0.25)
    note = bass.clips[0].notes[0]
    assert (note.pitch, note.velocity, note.confidence) == (40, 0.8, 0.91)
    assert bass.clips[0].notes[1].confidence is None

    assert vox.muted is True
    clip = vox.clips[0]
    assert isinstance(clip, AudioClip)
    assert (clip.source, clip.source_offset_samples, clip.source_length_samples) == \
           ("take1.wav", 128, 44100)
    assert clip.fade_in_beats == 0.25

    assert [(c.name, c.derived) for c in loaded.chords] == [("Cm7", True), ("F7", False)]


def test_a_manifest_with_no_key_mode_defaults_to_major(tmp_path):
    """Older manifests predate `key_mode` -- they should still load, as C
    major's ambiguous relative-minor-free default."""
    path = bundle.save_project(_project(), tmp_path / "Demo")
    manifest = os.path.join(path, "project.json")
    data = json.loads(open(manifest).read())
    del data["key_mode"]
    open(manifest, "w").write(json.dumps(data))

    assert bundle.load_project(path).key_mode == "major"


def test_a_missing_confidence_stays_missing_rather_than_becoming_zero(tmp_path):
    """`None` and `0.0` are different claims -- nobody measured, versus measured
    and found worthless."""
    path = bundle.save_project(
        Project(tracks=[Track(clips=[NoteClip(notes=[Note(0, 1, 60)])])]), tmp_path / "P")
    manifest = json.loads((tmp_path / "P.ncproj" / "project.json").read_text())
    assert "confidence" not in manifest["tracks"][0]["clips"][0]["notes"][0]
    assert bundle.load_project(path).tracks[0].clips[0].notes[0].confidence is None


# --- versioning: refuse rather than degrade --------------------------------


def test_a_project_from_the_future_is_refused_by_name(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    manifest = os.path.join(path, "project.json")
    data = json.loads(open(manifest).read())
    data["version"] = PROJECT_VERSION + 5
    open(manifest, "w").write(json.dumps(data))

    with pytest.raises(bundle.ProjectVersionError) as caught:
        bundle.load_project(path)
    # The message has to say what happened, since refusing is the whole point.
    assert str(PROJECT_VERSION + 5) in str(caught.value)
    assert "newer" in str(caught.value).lower()


def test_a_manifest_with_no_version_is_refused(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    manifest = os.path.join(path, "project.json")
    data = json.loads(open(manifest).read())
    del data["version"]
    open(manifest, "w").write(json.dumps(data))
    with pytest.raises(bundle.ProjectReadError):
        bundle.load_project(path)


def test_a_directory_that_is_not_a_bundle_is_refused(tmp_path):
    (tmp_path / "NotAProject.ncproj").mkdir()
    with pytest.raises(bundle.ProjectReadError):
        bundle.load_project(tmp_path / "NotAProject.ncproj")


def test_unparseable_json_is_refused_not_silently_emptied(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    open(os.path.join(path, "project.json"), "w").write("{ this is not json")
    with pytest.raises(bundle.ProjectReadError):
        bundle.load_project(path)


def test_migrate_is_called_on_every_read_and_is_the_identity_today(tmp_path):
    """It exists before it is needed on purpose: the first real migration
    should be a new branch in a tested function, not a mechanism invented under
    pressure."""
    data = {"version": PROJECT_VERSION, "name": "x"}
    assert bundle.migrate(dict(data)) == data


# --- content that is merely missing still opens ----------------------------


def test_a_missing_audio_file_degrades_rather_than_raising(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    loaded = bundle.load_project(path)          # take1.wav was never copied in
    clip = loaded.tracks[1].clips[0]
    assert bundle.resolve_audio(path, clip) is None
    assert bundle.missing_audio(path, loaded) == ["take1.wav"]


def test_one_unreadable_note_does_not_lose_the_others(tmp_path):
    path = bundle.save_project(
        Project(tracks=[Track(clips=[NoteClip(notes=[Note(0, 1, 60)])])]), tmp_path / "P")
    manifest = os.path.join(path, "project.json")
    data = json.loads(open(manifest).read())
    notes = data["tracks"][0]["clips"][0]["notes"]
    notes.insert(0, {"pitch": "not a pitch"})
    open(manifest, "w").write(json.dumps(data))
    loaded = bundle.load_project(path)
    assert [n.pitch for n in loaded.tracks[0].clips[0].notes] == [60]


# --- audio import: copy in, bare names -------------------------------------


def test_importing_copies_the_file_in_and_returns_a_bare_name(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    source = tmp_path / "elsewhere" / "loop.wav"
    source.parent.mkdir()
    source.write_bytes(b"RIFF....fake")

    name = bundle.import_audio(path, source)
    assert name == "loop.wav"                       # bare, not a path
    assert os.path.exists(os.path.join(path, "audio", "loop.wav"))


def test_importing_the_same_bytes_twice_reuses_the_file(tmp_path):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    source = tmp_path / "loop.wav"
    source.write_bytes(b"same")
    assert bundle.import_audio(path, source) == "loop.wav"
    assert bundle.import_audio(path, source) == "loop.wav"
    assert os.listdir(os.path.join(path, "audio")) == ["loop.wav"]


def test_a_different_file_with_the_same_name_does_not_clobber(tmp_path):
    """Another clip may already point at the first one."""
    path = bundle.save_project(_project(), tmp_path / "Demo")
    first, second = tmp_path / "a" / "loop.wav", tmp_path / "b" / "loop.wav"
    for f, data in ((first, b"one"), (second, b"two-different")):
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(data)

    assert bundle.import_audio(path, first) == "loop.wav"
    assert bundle.import_audio(path, second) == "loop_1.wav"
    assert open(os.path.join(path, "audio", "loop.wav"), "rb").read() == b"one"


def test_a_source_path_in_a_manifest_is_reduced_to_its_basename(tmp_path):
    """A project must not be able to reference outside its own bundle --
    `patch_format` takes the same precaution with sample names."""
    path = bundle.save_project(_project(), tmp_path / "Demo")
    manifest = os.path.join(path, "project.json")
    data = json.loads(open(manifest).read())
    data["tracks"][1]["clips"][0]["source"] = "../../../etc/passwd"
    open(manifest, "w").write(json.dumps(data))
    assert bundle.load_project(path).tracks[1].clips[0].source == "passwd"


# --- saving is not destructive ---------------------------------------------


def test_an_interrupted_save_leaves_the_previous_manifest_intact(tmp_path, monkeypatch):
    path = bundle.save_project(_project(), tmp_path / "Demo")
    before = open(os.path.join(path, "project.json")).read()

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bundle.json, "dump", explode)
    with pytest.raises(OSError):
        bundle.save_project(_project(), path)
    assert open(os.path.join(path, "project.json")).read() == before
