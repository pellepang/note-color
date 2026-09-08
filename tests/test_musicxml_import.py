"""MusicXML -> Project (milestone 1, ticket #153).

Fixtures are written by this repo's own `score_writer`/`score_editor_state`,
so the import is checked against files the project actually produces rather
than against hand-rolled XML that might not resemble them.
"""

import pytest

from notecolor.notation import musicxml_import as mxi

pytest.importorskip("music21")


def _write(tmp_path, columns, tempo=90.0, signature=(4, 4), key=0):
    from notecolor.notation.score_editor_state import (
        EditorColumn, EditorNote, EditorScore, save_score,
    )
    score = EditorScore(columns=columns, tempo_bpm=tempo,
                        time_signature=signature, key_fifths=key)
    path = tmp_path / "s.musicxml"
    save_score(score, path)
    return path


def _column(pitches, duration="quarter"):
    from notecolor.notation.score_editor_state import EditorColumn, EditorNote
    return EditorColumn(notes=[EditorNote(pitch_class=p, octave=o) for p, o in pitches],
                        duration_class=duration)


def test_a_single_staff_file_becomes_one_track(tmp_path):
    path = _write(tmp_path, [_column([(0, 4)]), _column([(4, 4)]), _column([(7, 4)])])
    project = mxi.import_musicxml(path)
    assert len(project.tracks) == 1
    assert [n.pitch for n in project.tracks[0].clips[0].notes] == [60, 64, 67]


def test_a_grand_staff_is_one_instrument_not_two_tracks(tmp_path):
    """music21 exposes each staff as its own PartStaff. They are one piano
    played with two hands -- two Tracks would be independently mutable, which
    a piano cannot do."""
    path = _write(tmp_path, [_column([(0, 2), (0, 5)]), _column([(7, 2), (4, 5)])])
    project = mxi.import_musicxml(path)
    assert len(project.tracks) == 1
    pitches = sorted(n.pitch for n in project.tracks[0].clips[0].notes)
    assert pitches == [36, 43, 72, 76]   # C2 G2 (bass), C5 E5 (treble)


def test_score_properties_are_carried_over(tmp_path):
    path = _write(tmp_path, [_column([(0, 4)])], tempo=132.0, signature=(3, 4), key=-2)
    project = mxi.import_musicxml(path)
    assert project.tempo_map.bpm_at(0) == pytest.approx(132.0)
    assert (project.time_signature.numerator,
            project.time_signature.denominator) == (3, 4)
    assert project.key_fifths == -2


def test_a_chord_becomes_simultaneous_notes(tmp_path):
    path = _write(tmp_path, [_column([(0, 4), (4, 4), (7, 4)])])
    project = mxi.import_musicxml(path)
    notes = project.tracks[0].clips[0].notes
    assert len(notes) == 3
    assert len({n.start_beat for n in notes}) == 1


def test_durations_survive_as_beats(tmp_path):
    path = _write(tmp_path, [_column([(0, 4)], "half"), _column([(2, 4)], "quarter")])
    project = mxi.import_musicxml(path)
    notes = sorted(project.tracks[0].clips[0].notes, key=lambda n: n.start_beat)
    assert notes[0].duration_beats == pytest.approx(2.0)
    assert notes[1].duration_beats == pytest.approx(1.0)


def test_the_project_is_named_after_the_file(tmp_path):
    path = _write(tmp_path, [_column([(0, 4)])])
    assert mxi.import_musicxml(path).name == "s"
    assert mxi.import_musicxml(path, name="Given").name == "Given"


def test_a_part_with_no_name_does_not_get_a_music21_object_id(tmp_path):
    """music21 fills `id` with the object's memory address as a decimal
    string, which is how '140237127173520' ends up as a track name."""
    path = _write(tmp_path, [_column([(0, 4)])])
    name = mxi.import_musicxml(path).tracks[0].name
    assert not name.isdigit()


def test_the_clip_spans_the_music(tmp_path):
    path = _write(tmp_path, [_column([(0, 4)], "whole"), _column([(4, 4)], "whole")])
    clip = mxi.import_musicxml(path).tracks[0].clips[0]
    assert clip.start_beat == 0.0
    assert clip.length_beats == pytest.approx(8.0)


def test_importing_the_repos_own_project_writer_output(tmp_path):
    """`score_writer.write_project()` is the other file this has to read --
    #123's converter writes it, and those become Tracks here."""
    from notecolor.convert.convert import ConversionResult, Part
    from notecolor.notation.score_writer import write_project
    from notecolor.convert.transcribe_backends import BeatGrid, TranscribedNote

    result = ConversionResult(
        parts=[Part(name="bass", notes=[TranscribedNote(0.0, 1.0, 40)]),
               Part(name="vocals", notes=[TranscribedNote(0.0, 1.0, 67)])],
        beats=BeatGrid(beat_seconds=(0.0, 0.5, 1.0, 1.5, 2.0),
                       downbeat_seconds=(0.0, 2.0)),
        beats_per_bar=4, duration_seconds=2.0)
    path = tmp_path / "p.musicxml"
    write_project(result, path)

    project = mxi.import_musicxml(path)
    assert sorted(t.name for t in project.tracks) == ["bass", "vocals"]
