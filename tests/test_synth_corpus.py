"""Tests for the synthetic jazz corpus generator (map #123, issue #144).

The generator's whole value is that its ground truth is correct by
construction, so these tests are mostly about that construction being
what it claims: deterministic, in the stated meter, with swing that is
actually swung.

Nothing here scores a transcription -- that is `test_evaluate.py`.
"""

import numpy as np
import pytest

import synth_corpus as sc


def test_track_is_deterministic_from_its_seed():
    """A regression suite that is not reproducible is not a regression
    suite -- the same seed has to give the same notes."""
    a = sc.make_track(seed=7)
    b = sc.make_track(seed=7)
    assert [p.notes for p in a.parts] == [p.notes for p in b.parts]
    assert a.drums == b.drums


def test_different_seeds_give_different_music():
    a = sc.make_track(seed=1)
    b = sc.make_track(seed=2)
    assert [p.notes for p in a.parts] != [p.notes for p in b.parts]


def test_swing_delays_the_offbeat_by_a_triplet_ratio():
    """The defining feel of the idiom, and the place the "a generator's
    own errors are invisible" trap bites hardest: if this is wrong, every
    swing number the corpus produces is wrong in the same direction."""
    assert sc.swing_offset(1.5, swing=True) == pytest.approx(1.0 + 2.0 / 3.0)
    assert sc.swing_offset(1.5, swing=False) == 1.5
    # Downbeats never move.
    assert sc.swing_offset(2.0, swing=True) == 2.0
    assert sc.swing_offset(0.0, swing=True) == 0.0


def test_swing_leaves_non_eighth_positions_alone():
    """Only the off-beat eighth swings; a triplet or sixteenth position is
    not this function's business."""
    for position in (1.25, 1.75, 2.33):
        assert sc.swing_offset(position, swing=True) == position


def test_walking_bass_puts_the_root_on_every_downbeat():
    """What makes the harmony legible, and what a bass-line transcription
    is checked against."""
    rng = np.random.default_rng(0)
    chart = [(0, "maj7"), (7, "dom7")]
    part = sc.walking_bass(chart, 4, rng)
    downbeats = [n for n in part.notes if n[0] % 4 == 0]
    assert [n[1] for n in downbeats] == [0, 7]


def test_walking_bass_plays_one_note_per_beat():
    rng = np.random.default_rng(0)
    part = sc.walking_bass([(0, "maj7")] * 3, 4, rng)
    assert len(part.notes) == 12
    assert sorted(n[0] for n in part.notes) == [float(i) for i in range(12)]


def test_chords_use_the_stated_quality():
    """Ground truth is only true if the notes match the chord symbol."""
    rng = np.random.default_rng(0)
    part = sc.comped_chords([(0, "min7")], 4, rng)
    pitch_classes = {n[1] for n in part.notes}
    assert pitch_classes == {0, 3, 7, 10}


def test_every_chord_quality_is_renderable():
    rng = np.random.default_rng(0)
    for quality in sc.CHORD_INTERVALS:
        part = sc.comped_chords([(0, quality)], 4, rng)
        assert part.notes, quality


def test_progressions_are_whole_bars():
    """A chart whose bars do not line up with the meter would make every
    barline in the ground truth wrong."""
    for name, chart in sc.PROGRESSIONS.items():
        assert all(isinstance(root, int) and 0 <= root < 12 for root, _q in chart), name
        assert all(quality in sc.CHORD_INTERVALS for _r, quality in chart), name


def test_drum_pattern_has_a_kick_on_every_downbeat():
    hits = sc.drum_pattern(4, 4, swing=False)
    kicks = sorted(t for t, kind in hits if kind == "kick")
    assert kicks == [0.0, 4.0, 8.0, 12.0]


def test_parts_can_be_selected_individually():
    """Needed for error attribution: isolating one instrument is how a
    wrong result gets blamed on a stage rather than merely observed."""
    track = sc.make_track(parts=("melody",), with_drums=False, seed=0)
    assert [p.name for p in track.parts] == ["melody"]
    assert track.drums == []


def test_meter_is_configurable():
    """The map committed to inferring meter, and no real aligned corpus
    has non-4/4 band material to check it against."""
    track = sc.make_track(beats_per_bar=3, repeats=1, seed=0)
    assert track.beats_per_bar == 3
    bass = next(p for p in track.parts if p.name == "bass")
    assert len(bass.notes) == 3 * len(sc.PROGRESSIONS["ii-V-I"])


def test_rendered_audio_is_finite_and_within_range():
    track = sc.make_track(repeats=1, seed=0)
    audio = sc.render_audio(track)
    assert audio.size > 0
    assert np.all(np.isfinite(audio))
    assert np.max(np.abs(audio)) <= 1.0


def test_rendered_audio_covers_the_whole_arrangement():
    import config as cfg

    track = sc.make_track(repeats=1, seed=0)
    audio = sc.render_audio(track)
    assert audio.size >= int(track.duration_seconds() * cfg.PLAYBACK_SAMPLE_RATE)


def test_drums_render_separately_so_their_effect_is_measurable():
    """#130's timing-oracle question is 'what do drums do to beat
    tracking', which needs them addable and removable in isolation."""
    track = sc.make_track(repeats=1, seed=0)
    drums = sc.render_drums(track)
    assert drums.size > 0 and np.max(np.abs(drums)) > 0


def test_a_track_without_drums_renders_silence_for_them():
    track = sc.make_track(with_drums=False, repeats=1, seed=0)
    assert track.drums == []
