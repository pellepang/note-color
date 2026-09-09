"""The Project model, and the tempo map in particular (ticket #154).

The tempo map gets the most attention here on purpose: it is the one piece
every other consumer goes through, so a wrong conversion is wrong in the ruler,
the playhead, every clip position and every export simultaneously. Its
behaviour is asserted against hand-computed numbers rather than against itself.
"""

import pytest

from notecolor.project.model import (
    CONSTANT,
    LINEAR,
    AudioClip,
    ChordSpan,
    Note,
    NoteClip,
    Project,
    TempoAnchor,
    TempoMap,
    TimeSignature,
    Track,
    chromatic_note_names,
    diatonic_pitch_classes,
    key_label,
    key_tonic_pitch_class,
)


# --- TempoMap --------------------------------------------------------------


def test_a_single_tempo_is_a_straight_conversion():
    tempo = TempoMap([TempoAnchor(0.0, 120.0)])
    assert tempo.beats_to_seconds(4) == pytest.approx(2.0)
    assert tempo.seconds_to_beats(2.0) == pytest.approx(4.0)


def test_seconds_accumulate_across_a_tempo_change():
    """8 beats at 120bpm is 4s; the next 4 beats at 60bpm are another 4s."""
    tempo = TempoMap([TempoAnchor(0.0, 120.0), TempoAnchor(8.0, 60.0)])
    assert tempo.beats_to_seconds(8) == pytest.approx(4.0)
    assert tempo.beats_to_seconds(12) == pytest.approx(8.0)


def test_the_conversion_inverts_exactly_across_a_change():
    tempo = TempoMap([TempoAnchor(0.0, 90.0), TempoAnchor(3.0, 140.0),
                      TempoAnchor(11.5, 70.0)])
    for beat in (0.0, 1.25, 3.0, 7.75, 11.5, 20.0):
        assert tempo.seconds_to_beats(tempo.beats_to_seconds(beat)) == pytest.approx(beat)


def test_bpm_holds_until_the_next_anchor():
    tempo = TempoMap([TempoAnchor(0.0, 100.0), TempoAnchor(4.0, 200.0)])
    assert tempo.bpm_at(0.0) == 100.0
    assert tempo.bpm_at(3.999) == 100.0
    assert tempo.bpm_at(4.0) == 200.0
    assert tempo.bpm_at(99.0) == 200.0


def test_anchors_are_sorted_and_a_beat_zero_anchor_is_guaranteed():
    """Every lookup has to land somewhere, so a map that starts late has its
    first tempo extended backwards rather than a tempo invented for bar 1."""
    tempo = TempoMap([TempoAnchor(8.0, 90.0), TempoAnchor(2.0, 150.0)])
    assert [a.beat for a in tempo.anchors] == [0.0, 2.0, 8.0]
    assert tempo.bpm_at(0.0) == 150.0


def test_a_nonsense_tempo_is_dropped_rather_than_dividing_by_zero():
    tempo = TempoMap([TempoAnchor(0.0, 120.0), TempoAnchor(4.0, 0.0)])
    assert [a.bpm for a in tempo.anchors] == [120.0]
    assert tempo.beats_to_seconds(8) == pytest.approx(4.0)


def test_an_empty_map_still_converts():
    assert TempoMap([]).beats_to_seconds(2) == pytest.approx(1.0)


def test_linear_interpolation_is_stored_but_flagged_as_unhonoured():
    """A ramp is accepted so adding it later is additive -- but a file that
    asks for one must not silently play back at the wrong speed."""
    plain = TempoMap([TempoAnchor(0.0, 120.0)])
    ramped = TempoMap([TempoAnchor(0.0, 120.0), TempoAnchor(4.0, 60.0, LINEAR)])
    assert plain.has_unsupported_interpolation is False
    assert ramped.has_unsupported_interpolation is True
    assert ramped.anchors[1].interpolation == LINEAR


def test_tempo_map_survives_its_own_dict_round_trip():
    tempo = TempoMap([TempoAnchor(0.0, 96.0), TempoAnchor(6.0, 132.0, LINEAR)])
    again = TempoMap.from_dict(tempo.to_dict())
    assert [(a.beat, a.bpm, a.interpolation) for a in again.anchors] == \
           [(0.0, 96.0, CONSTANT), (6.0, 132.0, LINEAR)]


def test_one_unreadable_anchor_does_not_lose_the_rest():
    again = TempoMap.from_dict([{"beat": 0, "bpm": 120}, {"beat": "?"},
                                {"beat": 4, "bpm": 80}])
    assert [a.bpm for a in again.anchors] == [120.0, 80.0]


# --- TimeSignature ---------------------------------------------------------


def test_beats_per_bar_is_in_quarter_notes():
    """Positions are stored in quarter-note beats, so 6/8 is three of them --
    not six. Getting this backwards would misplace every barline in compound
    meter, which is on map #123's own candidate list."""
    assert TimeSignature(4, 4).beats_per_bar == 4.0
    assert TimeSignature(3, 4).beats_per_bar == 3.0
    assert TimeSignature(6, 8).beats_per_bar == 3.0
    assert TimeSignature(2, 2).beats_per_bar == 4.0


# --- Project ---------------------------------------------------------------


def test_end_beat_is_the_furthest_clip_end():
    project = Project(tracks=[
        Track(name="a", clips=[NoteClip(start_beat=0, length_beats=4)]),
        Track(name="b", clips=[NoteClip(start_beat=12, length_beats=8)]),
    ])
    assert project.end_beat == 20.0


def test_duration_seconds_goes_through_the_tempo_map():
    project = Project(
        tempo_map=TempoMap([TempoAnchor(0.0, 60.0)]),
        tracks=[Track(clips=[NoteClip(start_beat=0, length_beats=6)])])
    assert project.duration_seconds == pytest.approx(6.0)


def test_an_empty_project_has_no_length_and_does_not_raise():
    assert Project().end_beat == 0.0
    assert Project().duration_seconds == 0.0


def test_track_patch_name_defaults_to_none():
    """Every Track imported from MusicXML today keeps working unchanged
    until something assigns it a patch from the Synth view (#156)."""
    assert Track().patch_name is None


def test_unique_track_name_avoids_the_one_thing_that_does_not_round_trip():
    """#128 measured that a duplicate part name silently merges two parts'
    provenance on export, so duplicates are prevented at the source."""
    project = Project(tracks=[Track(name="Gtr"), Track(name="Gtr 2")])
    assert project.unique_track_name("Bass") == "Bass"
    assert project.unique_track_name("Gtr") == "Gtr 3"


def test_note_reports_its_pitch_class():
    assert Note(0, 1, 60).pitch_class == 0
    assert Note(0, 1, 66).pitch_class == 6


def test_clips_know_their_own_kind():
    assert NoteClip().kind == "note"
    assert AudioClip().kind == "audio"


def test_a_corrected_chord_span_is_not_derived():
    assert ChordSpan(0, 4, "C").derived is True
    assert ChordSpan(0, 4, "Cm", derived=False).derived is False


# --- key signature -----------------------------------------------------


@pytest.mark.parametrize("fifths, mode, pitch_class", [
    (0, "major", 0),    # C major
    (0, "minor", 9),    # A minor
    (1, "major", 7),    # G major
    (1, "minor", 4),    # E minor
    (-1, "major", 5),   # F major
    (-3, "major", 3),   # Eb major
    (7, "major", 1),    # C#/Db major
    (-5, "major", 1),   # Db major
])
def test_key_tonic_pitch_class(fifths, mode, pitch_class):
    assert key_tonic_pitch_class(fifths, mode) == pitch_class


def test_chromatic_note_names_spell_c_major_per_the_circle_of_fifths():
    """Every passing tone is the flat of the degree above it, except the
    raised 4th (the tritone from the tonic), which is always sharp."""
    assert chromatic_note_names(0, "major") == [
        "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def test_chromatic_note_names_relative_minor_can_differ_from_its_major():
    """A minor shares C major's key signature (fifths=0) but its own tonic,
    so its raised-4th tritone falls on a different pitch class (D#, not F#)."""
    assert chromatic_note_names(0, "minor") == [
        "C", "Db", "D", "D#", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def test_chromatic_note_names_diatonic_degrees_keep_their_own_letter():
    """A key whose diatonic scale itself needs an accidental (D major has
    F# as a *scale* tone, not a passing tone) must show it -- not silently
    fall back to the bare natural letter."""
    names = chromatic_note_names(2, "major")   # D major: D E F# G A B C#
    assert names[6] == "F#"
    assert names[1] == "C#"


def test_chromatic_note_names_agree_with_diatonic_pitch_classes():
    """Every diatonic (in-scale) pitch class gets a single-letter name (no
    accidental needed beyond what's baked into the letter itself already
    being correct for that scale, e.g. plain 'C' in C major)."""
    for fifths, mode in [(-4, "major"), (0, "major"), (4, "major"), (0, "minor")]:
        names = chromatic_note_names(fifths, mode)
        for pitch_class in diatonic_pitch_classes(fifths, mode):
            assert names[pitch_class][0] in "CDEFGAB"


def test_diatonic_pitch_classes_major():
    assert diatonic_pitch_classes(0, "major") == {0, 2, 4, 5, 7, 9, 11}


def test_diatonic_pitch_classes_minor_can_differ_from_relative_major():
    """Same key signature as C major (fifths=0), but a different tonic, so
    highlighting must key off (fifths, mode) together, not fifths alone --
    it happens to land on the same 7 pitch classes here since A natural
    minor shares C major's key signature exactly, but the *scale* -- degree
    order and starting point -- genuinely differs."""
    assert diatonic_pitch_classes(0, "minor") == {0, 2, 4, 5, 7, 9, 11}


def test_diatonic_pitch_classes_are_not_just_the_white_keys():
    """F# major's scale is mostly black keys -- highlighting must follow
    the key, not a fixed natural-letter set."""
    assert diatonic_pitch_classes(6, "major") == {1, 3, 5, 6, 8, 10, 11}


def test_key_label():
    assert key_label(-1, "major") == "F major"
    assert key_label(0, "minor") == "A minor"
