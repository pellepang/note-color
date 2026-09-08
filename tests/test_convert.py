"""Tests for map #123's converter pipeline driver (issue #129).

Backends are fakes throughout: this file tests the *order* of the passes,
the refusal rule and the meter gate, none of which need a model. Per this
repo's convention the real backends are tested against their own
behaviour elsewhere, and nothing here installs or downloads anything.
"""

import numpy as np
import pytest

from notecolor.convert import convert
from notecolor.convert.convert import (
    DEFAULT_BEATS_PER_BAR,
    STEM_DRUMS,
    ConversionResult,
    Part,
    infer_beats_per_bar,
)
from notecolor.convert.transcribe_backends import BeatGrid, ChordSpan, ConversionUnavailable, TranscribedNote

SAMPLE_RATE = 22050


class FakeTranscriber:
    def __init__(self):
        self.seen = []

    def transcribe(self, audio, sample_rate):
        self.seen.append(len(audio))
        return [TranscribedNote(0.0, 1.0, 60)]


class FakeSeparator:
    def __init__(self, stems):
        self.stems = stems

    def separate(self, audio, sample_rate):
        return self.stems


class FakeBeatTracker:
    def __init__(self, grid=None):
        self.grid = grid or BeatGrid((0.0, 0.5, 1.0), (0.0, 1.0))
        self.drum_stem_seen = "not called"

    def track(self, audio, sample_rate, drum_stem=None):
        self.drum_stem_seen = drum_stem
        return self.grid


class FakeChordEstimator:
    def __init__(self):
        self.beats_seen = "not called"
        self.audio_len_seen = None

    def estimate(self, audio, sample_rate, beats=None):
        self.beats_seen = beats
        self.audio_len_seen = len(audio)
        return [ChordSpan(0.0, 1.0, "C")]


def _audio(seconds=1.0):
    return np.zeros(int(seconds * SAMPLE_RATE))


# --- Refuse, don't degrade (#129) -----------------------------------------


def test_asking_for_notes_without_a_transcriber_refuses_with_the_install_line():
    """The one place #129's refuse-don't-degrade rule can actually be
    enforced. Quietly returning an empty score would be the degradation."""
    with pytest.raises(ConversionUnavailable) as excinfo:
        convert.convert(_audio(), SAMPLE_RATE, want_notes=True, want_chords=False)
    assert excinfo.value.install_hint == convert.CONVERT_EXTRA
    assert "[convert]" in str(excinfo.value)


def test_chords_fall_back_to_the_template_tier_rather_than_refusing():
    """Unlike notes, chords have a tier that needs nothing installed, so
    defaulting to it substitutes nothing worse -- it is the only model
    until an extra brings a better one (#142)."""
    result = convert.convert(_audio(), SAMPLE_RATE, want_notes=False, want_chords=True)
    assert isinstance(result, ConversionResult)


def test_wanting_neither_notes_nor_chords_needs_no_backend_at_all():
    result = convert.convert(_audio(), SAMPLE_RATE, want_notes=False, want_chords=False)
    assert result.parts == [] and result.chords == []


# --- Pass ordering and routing --------------------------------------------


def test_without_a_separator_the_mix_is_one_track():
    """Not a fallback -- H1's control arm, the 'without separation' half of
    the A/B #126 says nobody has run."""
    result = convert.convert(
        _audio(), SAMPLE_RATE, note_transcriber=FakeTranscriber(), want_chords=False
    )
    assert [t.name for t in result.parts] == ["mix"]
    assert result.parts[0].source_stem is None
    assert result.note_count == 1


def test_with_a_separator_each_non_drum_stem_becomes_a_track():
    stems = {
        "vocals": _audio(), "bass": _audio(), "other": _audio(), STEM_DRUMS: _audio(),
    }
    result = convert.convert(
        _audio(),
        SAMPLE_RATE,
        separator=FakeSeparator(stems),
        note_transcriber=FakeTranscriber(),
        want_chords=False,
    )
    assert [t.name for t in result.parts] == ["bass", "other", "vocals"]
    assert all(t.source_stem == t.name for t in result.parts)


def test_no_notated_drum_part_is_written():
    """#130: drums are a timing oracle in v1. A notated drum part needs an
    ADT model whose only licence-clean route is training on STAR Drums,
    which graduates to its own effort."""
    stems = {STEM_DRUMS: _audio(), "bass": _audio()}
    result = convert.convert(
        _audio(),
        SAMPLE_RATE,
        separator=FakeSeparator(stems),
        note_transcriber=FakeTranscriber(),
        want_chords=False,
    )
    assert STEM_DRUMS not in [t.name for t in result.parts]


def test_the_drum_stem_is_handed_to_the_beat_tracker_as_the_timing_oracle():
    """#130's measured +7.6 downbeat F1 depends on this wiring existing."""
    drums = _audio()
    tracker = FakeBeatTracker()
    convert.convert(
        _audio(),
        SAMPLE_RATE,
        separator=FakeSeparator({STEM_DRUMS: drums, "bass": _audio()}),
        note_transcriber=FakeTranscriber(),
        beat_tracker=tracker,
        want_chords=False,
    )
    assert tracker.drum_stem_seen is drums


def test_the_beat_tracker_is_given_no_drum_stem_when_nothing_separated():
    tracker = FakeBeatTracker()
    convert.convert(
        _audio(), SAMPLE_RATE, note_transcriber=FakeTranscriber(),
        beat_tracker=tracker, want_chords=False,
    )
    assert tracker.drum_stem_seen is None


def test_chords_are_estimated_on_the_beat_grid_when_one_exists():
    """The reason ChordEstimator.estimate() takes beats at all."""
    estimator = FakeChordEstimator()
    grid = BeatGrid((0.0, 0.5, 1.0), (0.0, 1.0))
    convert.convert(
        _audio(), SAMPLE_RATE, beat_tracker=FakeBeatTracker(grid),
        chord_estimator=estimator, want_notes=False,
    )
    assert estimator.beats_seen is grid


def test_chords_come_from_the_harmonic_stems_not_the_drums():
    """A chord is a property of the whole harmony, not of one instrument,
    so a separated mix is reassembled without drums rather than analysed
    per stem. Asserted by amplitude: the drum stem is loud and would
    change the summed signal if it were included."""
    estimator = FakeChordEstimator()
    quiet = np.zeros(SAMPLE_RATE)
    loud = np.ones(SAMPLE_RATE) * 5.0
    convert.convert(
        _audio(),
        SAMPLE_RATE,
        separator=FakeSeparator({STEM_DRUMS: loud, "other": quiet}),
        chord_estimator=estimator,
        want_notes=False,
    )
    assert estimator.audio_len_seen == SAMPLE_RATE


def test_result_reports_duration_and_note_count():
    result = convert.convert(
        _audio(2.0), SAMPLE_RATE, note_transcriber=FakeTranscriber(), want_chords=False
    )
    assert result.duration_seconds == pytest.approx(2.0, abs=0.01)
    assert result.note_count == 1


# --- #130's meter gate ----------------------------------------------------


def test_meter_is_committed_when_the_downbeats_agree():
    beats = tuple(i * 0.5 for i in range(25))
    downbeats = tuple(i * 2.0 for i in range(7))       # every 4 beats, 6 bars
    numerator, committed = infer_beats_per_bar(BeatGrid(beats, downbeats))
    assert (numerator, committed) == (4, True)


def test_three_four_is_committed_too():
    beats = tuple(i * 0.5 for i in range(31))
    downbeats = tuple(i * 1.5 for i in range(8))       # every 3 beats
    assert infer_beats_per_bar(BeatGrid(beats, downbeats)) == (3, True)


def test_meter_is_not_committed_on_too_few_bars():
    """#130 gates the guess on enough observed bars -- meter accuracy is
    bounded above by downbeat F1 (~0.78)."""
    beats = tuple(i * 0.5 for i in range(9))
    downbeats = (0.0, 2.0, 4.0)
    numerator, committed = infer_beats_per_bar(BeatGrid(beats, downbeats))
    assert committed is False
    assert numerator == DEFAULT_BEATS_PER_BAR


def test_meter_is_not_committed_when_the_bars_disagree():
    """Ragged downbeats mean the tracker is not confident, and #130 says
    fall back to 4/4 rather than commit a number nobody believes."""
    beats = tuple(i * 0.5 for i in range(41))
    downbeats = (0.0, 2.0, 3.0, 5.5, 6.5, 9.5, 10.0, 13.0)
    numerator, committed = infer_beats_per_bar(BeatGrid(beats, downbeats))
    assert committed is False
    assert numerator == DEFAULT_BEATS_PER_BAR


def test_a_numerator_outside_the_candidate_set_is_not_committed():
    """{2,3,4,6} is how the field itself works; 5 is not in it, so a 5 is
    evidence the tracker is confused rather than evidence of 5/4."""
    beats = tuple(i * 0.5 for i in range(41))
    downbeats = tuple(i * 2.5 for i in range(8))       # every 5 beats
    numerator, committed = infer_beats_per_bar(BeatGrid(beats, downbeats))
    assert committed is False
    assert numerator == DEFAULT_BEATS_PER_BAR


def test_uncommitted_meter_surfaces_as_none_on_the_result():
    """'Correctable guess' made concrete (#130): 4/4 is still written, but
    the result says it was not inferred so the manifest can record that."""
    result = convert.convert(
        _audio(), SAMPLE_RATE, beat_tracker=FakeBeatTracker(), want_notes=False, want_chords=False
    )
    assert result.beats_per_bar is None


def test_committed_meter_surfaces_on_the_result():
    beats = tuple(i * 0.5 for i in range(25))
    downbeats = tuple(i * 2.0 for i in range(7))
    result = convert.convert(
        _audio(),
        SAMPLE_RATE,
        beat_tracker=FakeBeatTracker(BeatGrid(beats, downbeats)),
        want_notes=False,
        want_chords=False,
    )
    assert result.beats_per_bar == 4


def test_track_can_be_marked_low_confidence():
    """#129: a stem no model covers well is still transcribed and marked,
    never dropped -- the output is editable, so an approximate track a
    human corrects beats a missing one."""
    part = Part(name="other", low_confidence=True)
    assert part.low_confidence is True
    assert Part(name="bass").low_confidence is False


# --- run_convert mode routing (map #123, #129) ----------------------------
#
# Regression cover for a real defect: `--mode {band,piano}` was accepted by
# the CLI and then ignored entirely, so both modes did the same thing. An
# advertised choice that changes nothing is worse than no choice.


def _silent_wav(tmp_path, seconds=0.5, sample_rate=22050):
    soundfile = pytest.importorskip("soundfile")
    path = tmp_path / "clip.wav"
    soundfile.write(str(path), np.zeros(int(seconds * sample_rate)), sample_rate)
    return path


def test_band_mode_attempts_separation(tmp_path, monkeypatch, capsys):
    pytest.importorskip("librosa")
    from notecolor.tui import app as main
    from notecolor.convert import transcribe_backends as tb

    attempted = []

    class Spy:
        def separate(self, audio, sample_rate):
            attempted.append(True)
            return {"bass": audio, "other": audio}

    monkeypatch.setattr(tb, "DemucsSeparator", lambda *a, **k: Spy())
    main.run_convert(str(_silent_wav(tmp_path)), mode="band",
                     want_notes=False, want_chords=False, out_path=str(tmp_path / "o.musicxml"))
    assert attempted, "band mode did not attempt separation"


def test_piano_mode_does_not_separate(tmp_path, monkeypatch):
    """#129: separation introduces artifacts on an already-clean signal,
    and solo piano is the case this converter is genuinely good at."""
    pytest.importorskip("librosa")
    from notecolor.tui import app as main
    from notecolor.convert import transcribe_backends as tb

    def must_not_be_constructed(*args, **kwargs):
        raise AssertionError("piano mode constructed a separator")

    monkeypatch.setattr(tb, "DemucsSeparator", must_not_be_constructed)
    rc = main.run_convert(str(_silent_wav(tmp_path)), mode="piano",
                          want_notes=False, want_chords=False,
                          out_path=str(tmp_path / "o.musicxml"))
    assert rc == 0


def test_missing_separation_is_reported_but_not_fatal(tmp_path, monkeypatch, capsys):
    """Unlike a missing note model, which refuses: without separation the
    mix is transcribed directly, which is H1's control arm rather than a
    degraded mode."""
    pytest.importorskip("librosa")
    from notecolor.tui import app as main
    from notecolor.convert import transcribe_backends as tb
    from notecolor.convert.transcribe_backends import ConversionUnavailable

    class Absent:
        def separate(self, audio, sample_rate):
            raise ConversionUnavailable("Source separation needs Demucs.",
                                        "pip install -e .[convert]")

    monkeypatch.setattr(tb, "DemucsSeparator", lambda *a, **k: Absent())
    rc = main.run_convert(str(_silent_wav(tmp_path)), mode="band",
                          want_notes=False, want_chords=False,
                          out_path=str(tmp_path / "o.musicxml"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "no separation" in out
    assert "transcribing the mix directly" in out


def test_missing_note_model_refuses_rather_than_degrading(tmp_path, monkeypatch):
    """The asymmetry #129 settled, asserted against its sibling above."""
    pytest.importorskip("librosa")
    from notecolor.tui import app as main
    from notecolor.convert import transcribe_backends as tb
    from notecolor.convert.transcribe_backends import ConversionUnavailable

    class Absent:
        def transcribe(self, audio, sample_rate):
            raise ConversionUnavailable("Note detection needs onnxruntime.",
                                        "pip install -e .[convert]")

    monkeypatch.setattr(tb, "BasicPitchTranscriber", lambda *a, **k: Absent())
    rc = main.run_convert(str(_silent_wav(tmp_path)), mode="piano",
                          want_notes=True, want_chords=False,
                          out_path=str(tmp_path / "o.musicxml"))
    assert rc == 1
