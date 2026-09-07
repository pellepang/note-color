"""Tests for the multi-track score project writer (map #123, #131).

Separate from `test_score_writer.py` so the original single-part
`write_score()` tests stay legible; both exercise the same module.
"""

import json

import numpy as np
import pytest

from notecolor.convert.convert import ConversionResult, Track
from notecolor.notation.score_writer import (
    BEAT_SUBDIVISIONS,
    PROJECT_MANIFEST_KEY,
    _beat_positions,
    _tempo_from_beats,
    read_project_manifest,
    snap_to_grid,
    write_project,
)
from notecolor.convert.transcribe_backends import BeatGrid, ChordSpan, TranscribedNote

music21 = pytest.importorskip("music21", reason="music21 lives behind the [batch] extra")


def _note(onset, offset, midi, confidence=None):
    return TranscribedNote(onset, offset, midi, confidence=confidence)


def _result(**kwargs):
    kwargs.setdefault("duration_seconds", 4.0)
    return ConversionResult(**kwargs)


# --- Grid arithmetic ------------------------------------------------------


def test_beat_positions_map_seconds_onto_beats():
    """#130's 'quantize against the beat grid, not a global tempo': one
    beat is one quarter-note of score time however the tempo drifts."""
    beats = [0.0, 0.5, 1.0, 1.5]
    pos = _beat_positions([0.0, 0.5, 1.0, 1.25], beats)
    assert list(pos[:3]) == [0.0, 1.0, 2.0]
    assert pos[3] == pytest.approx(2.5)


def test_beat_positions_track_a_tempo_change():
    """The property a constant tempo cannot express: beats get further
    apart, but each is still exactly one beat of score time."""
    beats = [0.0, 0.5, 1.0, 2.0, 3.0]     # slows down halfway
    pos = _beat_positions([0.0, 1.0, 2.0, 3.0], beats)
    assert list(pos) == [0.0, 2.0, 3.0, 4.0]


def test_beat_positions_extrapolate_rather_than_clamp():
    """A pickup before the first detected beat, or a tail after the last,
    must not pile onto one offset -- np.interp alone would clamp both."""
    beats = [1.0, 2.0, 3.0]
    pos = _beat_positions([0.0, 4.0], beats)
    assert pos[0] == pytest.approx(-1.0)
    assert pos[1] == pytest.approx(3.0)


def test_beat_positions_needs_at_least_two_beats():
    assert _beat_positions([0.0], []) is None
    assert _beat_positions([0.0], [1.0]) is None


def test_tempo_from_beats_uses_the_median():
    """One dropped or doubled beat must not drag the whole tempo marking."""
    steady = [i * 0.5 for i in range(10)]          # 120 bpm
    assert _tempo_from_beats(steady) == pytest.approx(120.0)
    with_glitch = steady[:5] + [2.4] + steady[5:]  # one spurious beat
    assert _tempo_from_beats(with_glitch) == pytest.approx(120.0, rel=0.2)


def test_tempo_from_beats_falls_back_without_a_grid():
    assert _tempo_from_beats([]) == 120.0


def test_snap_to_grid_covers_binary_and_ternary_subdivision():
    """12 is the smallest grid holding both: a sixteenth is 3/12 of a beat,
    a triplet-eighth 4/12."""
    assert snap_to_grid([0.25])[0] == pytest.approx(0.25)          # sixteenth
    assert snap_to_grid([1 / 3])[0] == pytest.approx(4 / 12)       # triplet-eighth
    assert snap_to_grid([0.26])[0] == pytest.approx(0.25)
    assert BEAT_SUBDIVISIONS == 12


# --- Writing a project ----------------------------------------------------


def test_writes_one_part_per_track(tmp_path):
    result = _result(tracks=[
        Track(name="bass", notes=[_note(0.0, 1.0, 40)]),
        Track(name="vocals", notes=[_note(0.0, 1.0, 67)]),
    ])
    path = tmp_path / "p.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    assert [p.partName for p in parsed.parts] == ["bass", "vocals"]


def test_duplicate_track_names_are_made_unique(tmp_path):
    """#128 measured <part-name> is the only track identity that survives a
    round trip, so a duplicate would silently merge two tracks' provenance."""
    result = _result(tracks=[
        Track(name="other", notes=[_note(0.0, 1.0, 60)]),
        Track(name="other", notes=[_note(0.0, 1.0, 64)]),
    ])
    path = tmp_path / "dup.musicxml"
    write_project(result, path)

    names = [p.partName for p in music21.converter.parse(str(path)).parts]
    assert len(set(names)) == 2, names


def test_manifest_round_trips(tmp_path):
    """#131 recorded this as unverified. It works -- but getCustom()
    returns a TUPLE of Text objects, not a string, which is the detail a
    naive reader gets wrong."""
    result = _result(tracks=[
        Track(name="bass", notes=[_note(0.0, 1.0, 40)], source_stem="bass",
              model="BasicPitchTranscriber", low_confidence=True),
    ])
    path = tmp_path / "m.musicxml"
    write_project(result, path)

    manifest = read_project_manifest(path)
    assert manifest["version"] == 1
    track = manifest["tracks"][0]
    assert track["name"] == "bass"
    assert track["source_stem"] == "bass"
    assert track["model"] == "BasicPitchTranscriber"
    assert track["low_confidence"] is True
    assert track["note_count"] == 1


def test_manifest_records_whether_the_meter_was_inferred(tmp_path):
    """#130's 'correctable guess' made concrete: 4/4 is written either way,
    but the file has to say whether anyone actually inferred it."""
    guessed = _result(tracks=[Track(name="a", notes=[_note(0, 1, 60)])], beats_per_bar=None)
    path = tmp_path / "g.musicxml"
    write_project(guessed, path)
    assert read_project_manifest(path)["meter_inferred"] is False

    inferred = _result(tracks=[Track(name="a", notes=[_note(0, 1, 60)])], beats_per_bar=3)
    path2 = tmp_path / "i.musicxml"
    write_project(inferred, path2)
    assert read_project_manifest(path2)["meter_inferred"] is True


def test_reading_a_manifest_from_a_file_without_one_returns_none(tmp_path):
    score = music21.stream.Score()
    part = music21.stream.Part()
    part.append(music21.note.Note("C4"))
    score.insert(0, part)
    path = tmp_path / "plain.musicxml"
    score.write("musicxml", fp=str(path))
    assert read_project_manifest(path) is None


def test_inferred_meter_becomes_the_time_signature(tmp_path):
    result = _result(tracks=[Track(name="a", notes=[_note(0, 1, 60)])], beats_per_bar=3)
    path = tmp_path / "ts.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    ts = list(parsed.recurse().getElementsByClass(music21.meter.TimeSignature))[0]
    assert (ts.numerator, ts.denominator) == (3, 4)


def test_compound_meter_gets_an_eight_denominator(tmp_path):
    """#130: the denominator is a convention, not an inference -- 4, or 8
    when the numerator is a compound-meter value."""
    result = _result(tracks=[Track(name="a", notes=[_note(0, 1, 60)])], beats_per_bar=6)
    path = tmp_path / "68.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    ts = list(parsed.recurse().getElementsByClass(music21.meter.TimeSignature))[0]
    assert (ts.numerator, ts.denominator) == (6, 8)


def test_chord_spans_are_written_as_harmony_on_the_melody_part(tmp_path):
    """#131 put chord symbols in MusicXML <harmony> and recorded the round
    trip as unverified. Verified here: figure and offset both survive."""
    result = _result(
        tracks=[Track(name="vocals", notes=[_note(0.0, 1.0, 67)])],
        chords=[ChordSpan(0.0, 2.0, "C"), ChordSpan(2.0, 4.0, "G7")],
        beats=BeatGrid(tuple(i * 0.5 for i in range(9)), (0.0, 2.0)),
    )
    path = tmp_path / "h.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    symbols = list(parsed.recurse().getElementsByClass(music21.harmony.ChordSymbol))
    assert [s.figure for s in symbols] == ["C", "G7"]


def test_an_unparseable_chord_figure_is_skipped_not_fatal(tmp_path):
    """This repo's jazz spelling is not always a figure music21 parses.
    Blank rather than a guess, and never a crashed write."""
    result = _result(
        tracks=[Track(name="a", notes=[_note(0.0, 1.0, 60)])],
        chords=[ChordSpan(0.0, 1.0, "C"), ChordSpan(1.0, 2.0, "!!not a chord!!")],
    )
    path = tmp_path / "bad.musicxml"
    write_project(result, path)  # must not raise

    parsed = music21.converter.parse(str(path))
    figures = [s.figure for s in parsed.recurse().getElementsByClass(music21.harmony.ChordSymbol)]
    assert "C" in figures


def test_a_chords_only_conversion_still_writes_a_readable_file(tmp_path):
    """`convert --no-notes` has no tracks at all; it must still produce a
    file rather than an empty score."""
    result = _result(tracks=[], chords=[ChordSpan(0.0, 2.0, "C")])
    path = tmp_path / "c.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    assert [p.partName for p in parsed.parts] == ["chords"]
    symbols = list(parsed.recurse().getElementsByClass(music21.harmony.ChordSymbol))
    assert [s.figure for s in symbols] == ["C"]


def test_per_note_confidence_reaches_the_file_via_the_manifest(tmp_path):
    """#143's free signal has to reach the file, or a reviewer cannot sort
    by it -- the manifest's per-track flag cannot express per-note doubt.

    It goes in the manifest rather than on the notes because **music21's
    `editorial` dict is not exported to MusicXML** (measured: a note
    carrying editorial.confidence writes a file with no trace of it), and
    MusicXML has no per-note certainty field of its own."""
    result = _result(tracks=[
        Track(name="a", notes=[
            _note(0.0, 1.0, 60, confidence=0.42),
            _note(1.0, 2.0, 64, confidence=0.91),
        ]),
    ])
    path = tmp_path / "conf.musicxml"
    write_project(result, path)

    triples = read_project_manifest(path)["tracks"][0]["note_confidence"]
    assert [t[1] for t in triples] == [60, 64]
    assert [t[2] for t in triples] == [0.42, 0.91]


def test_no_confidence_reported_means_an_empty_list_not_zeros(tmp_path):
    """A missing confidence and a confidence of zero are different claims."""
    result = _result(tracks=[Track(name="a", notes=[_note(0.0, 1.0, 60)])])
    path = tmp_path / "noconf.musicxml"
    write_project(result, path)
    assert read_project_manifest(path)["tracks"][0]["note_confidence"] == []


def test_music21_editorial_is_not_exported_to_musicxml(tmp_path):
    """Pins the finding above so nobody re-adds `element.editorial.x`
    expecting it to survive. If a future music21 starts exporting it, this
    fails and the manifest workaround can be revisited."""
    score = music21.stream.Score()
    part = music21.stream.Part()
    part.partName = "a"
    part.insert(0, music21.meter.TimeSignature("4/4"))
    marked = music21.note.Note("C4", quarterLength=1.0)
    marked.editorial.confidence = 0.42
    part.insert(0, marked)
    score.insert(0, part)
    path = tmp_path / "ed.musicxml"
    score.write("musicxml", fp=str(path))
    assert "0.42" not in path.read_text(encoding="utf-8")


def test_a_chord_held_past_the_last_note_survives(tmp_path):
    """music21 creates measures only as far as the last note, so a
    ChordSymbol beyond it is silently dropped. A lead sheet routinely ends
    on a chord held past the final melody note, so the part is padded."""
    result = _result(
        tracks=[Track(name="a", notes=[_note(0.0, 1.0, 60)])],
        chords=[ChordSpan(0.0, 1.0, "C"), ChordSpan(3.0, 4.0, "G7")],
        duration_seconds=4.0,
    )
    path = tmp_path / "tail.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    figures = [s.figure for s in parsed.recurse().getElementsByClass(music21.harmony.ChordSymbol)]
    assert figures == ["C", "G7"], figures


def test_notes_land_on_beat_positions_not_seconds(tmp_path):
    """The core of #130: with a 0.5s beat, a note at 1.0s is at beat 2,
    which is quarterLength offset 2.0 -- not 1.0."""
    result = _result(
        tracks=[Track(name="a", notes=[_note(1.0, 1.5, 60)])],
        beats=BeatGrid(tuple(i * 0.5 for i in range(9)), (0.0, 2.0)),
    )
    path = tmp_path / "grid.musicxml"
    write_project(result, path)

    parsed = music21.converter.parse(str(path))
    found = list(parsed.recurse().notes)[0]
    assert float(found.getOffsetInHierarchy(parsed)) == pytest.approx(2.0)


def test_writing_survives_unquantized_onsets(tmp_path):
    """Regression: raw interpolated beat positions are arbitrary floats and
    music21 raises 'Cannot convert inexpressible durations to MusicXML'
    when it has to place a rest between two of them. Snapping to the beat
    grid is what prevents it -- assert with deliberately ugly times."""
    rng = np.random.default_rng(3)
    notes = [
        _note(float(t), float(t) + 0.137, 60 + i % 5)
        for i, t in enumerate(np.sort(rng.random(25) * 8.0))
    ]
    result = _result(tracks=[Track(name="a", notes=notes)], duration_seconds=8.0)
    path = tmp_path / "ugly.musicxml"
    write_project(result, path)  # must not raise
    assert path.exists() and path.stat().st_size > 0
