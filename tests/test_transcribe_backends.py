"""Tests for map #123's offline converter seam (issue #129).

Per this repo's convention, the pure shapes and decisions are unit-tested
here; nothing in this file installs, downloads or runs a model. The one
concrete backend that exists so far (`DspNoteTranscriber`) wraps code this
repo already ships, so it is exercised against a synthesized signal rather
than a fixture -- same "synthesize the signal, no binary fixtures" rule
`test_chroma.py`'s `make_tone()` set.
"""

import numpy as np
import pytest

import config
from sound_engine import midi_pitch
from transcribe_backends import (
    BeatGrid,
    ChordSpan,
    ConversionUnavailable,
    DspNoteTranscriber,
    TranscribedNote,
)


def test_conversion_unavailable_carries_the_install_line():
    """#129: the converter refuses rather than degrading, so the message a
    user sees has to be actionable. Same shape as SynthUnavailable (#111)."""
    exc = ConversionUnavailable("Separation needs Demucs.", "pip install -e .[convert]")
    assert exc.install_hint == "pip install -e .[convert]"
    assert "pip install -e .[convert]" in str(exc)
    assert "Separation needs Demucs." in str(exc)


def test_conversion_unavailable_without_a_hint_is_still_a_plain_message():
    exc = ConversionUnavailable("No soundfont found.")
    assert exc.install_hint is None
    assert str(exc) == "No soundfont found."


def test_transcribed_note_duration_is_never_negative():
    """A model emitting an offset before its onset is a model bug, not a
    reason for arithmetic downstream to go negative."""
    assert TranscribedNote(1.0, 2.5, 60).duration_seconds == pytest.approx(1.5)
    assert TranscribedNote(2.0, 1.0, 60).duration_seconds == 0.0


def test_beat_grid_infers_the_modal_beats_per_bar():
    """#130's meter numerator inference in its simplest form: the mode of
    beats-between-downbeats."""
    beats = tuple(i * 0.5 for i in range(17))          # 0.0 .. 8.0
    downbeats = (0.0, 2.0, 4.0, 6.0)                   # every 4 beats
    assert BeatGrid(beats, downbeats).beats_per_bar() == 4


def test_beat_grid_infers_three_four():
    beats = tuple(i * 0.5 for i in range(19))
    downbeats = (0.0, 1.5, 3.0, 4.5, 6.0)              # every 3 beats
    assert BeatGrid(beats, downbeats).beats_per_bar() == 3


def test_beat_grid_takes_the_mode_not_the_first_or_the_mean():
    """One ragged bar must not move the answer -- which is the whole
    reason #130 specifies the mode rather than a division."""
    beats = tuple(i * 0.5 for i in range(21))
    downbeats = (0.0, 2.0, 4.0, 5.5, 7.5, 9.5)         # 4,4,3,4,4
    assert BeatGrid(beats, downbeats).beats_per_bar() == 4


def test_beat_grid_reports_nothing_when_it_cannot_say():
    """Too few downbeats to have an opinion. #130 gates the guess on
    enough observed bars; reporting None is how this half says so."""
    assert BeatGrid((), ()).beats_per_bar() is None
    assert BeatGrid((0.0, 0.5, 1.0), (0.0,)).beats_per_bar() is None
    assert BeatGrid((0.0, 0.5, 1.0), (0.0, 1.0)).beats_per_bar() is None


def test_beat_grid_defaults_are_empty_not_none():
    grid = BeatGrid()
    assert grid.beat_seconds == () and grid.downbeat_seconds == ()


def test_chord_span_is_a_span_not_an_event():
    """#131 puts chord symbols in MusicXML <harmony>, which is span-shaped;
    a lead sheet needs a duration, not an instant."""
    span = ChordSpan(0.0, 2.0, "CΔ7", confidence=0.8)
    assert span.end_seconds > span.start_seconds
    assert span.name == "CΔ7"


def _tone(midi, seconds, sample_rate, amplitude=0.5):
    freq = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    # A couple of harmonics, so YIN and the multipitch path both have
    # something realistic to lock onto (test_chroma.make_tone()'s approach).
    wave = np.sin(2 * np.pi * freq * t)
    wave += 0.5 * np.sin(2 * np.pi * 2 * freq * t)
    wave += 0.25 * np.sin(2 * np.pi * 3 * freq * t)
    return amplitude * wave / np.max(np.abs(wave))


def test_dsp_backend_returns_seconds_and_midi_not_hops_and_pitch_classes():
    """The conversion this backend exists to perform. batch_transcribe
    speaks onset_hop/duration_hops/pitch_class/octave; every metric #132
    adopts and every neural model #129 selects speaks seconds and MIDI."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    audio = _tone(69, 1.5, sample_rate)  # A4

    notes = DspNoteTranscriber().transcribe(audio, sample_rate)

    assert notes, "expected at least one note from a 1.5s sustained tone"
    for note in notes:
        assert isinstance(note, TranscribedNote)
        assert isinstance(note.pitch_midi, int)
        assert 0 <= note.pitch_midi <= 127
        assert note.onset_seconds >= 0.0
        assert note.offset_seconds >= note.onset_seconds
        # Seconds, not hop indices: a 1.5s clip cannot have an onset at
        # hop 40-odd interpreted as 40 seconds.
        assert note.onset_seconds <= 1.5


def test_dsp_backend_uses_the_repos_own_midi_tuning():
    """Rather than restating the convention -- the drift this seam should
    not introduce."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    notes = DspNoteTranscriber().transcribe(_tone(69, 1.5, sample_rate), sample_rate)
    pitches = {note.pitch_midi for note in notes}
    assert midi_pitch(9, 4) == 69
    assert 69 in pitches or 57 in pitches or 81 in pitches, (
        f"expected A in some octave, got {sorted(pitches)}"
    )


def test_dsp_backend_reports_no_confidence_and_flat_velocity():
    """This pipeline never measured a per-note attack strength, the same
    reason tab_playback.py uses one fixed velocity (#121). Inventing a
    dynamic would be dishonest, and #143 wants a real confidence signal
    from a model that actually has one."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    notes = DspNoteTranscriber().transcribe(_tone(69, 1.5, sample_rate), sample_rate)
    assert notes
    assert all(note.velocity == 1.0 for note in notes)
    assert all(note.confidence is None for note in notes)


def test_dsp_backend_returns_notes_in_time_order():
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    audio = np.concatenate([_tone(60, 0.6, sample_rate), _tone(67, 0.6, sample_rate)])
    notes = DspNoteTranscriber().transcribe(audio, sample_rate)
    onsets = [note.onset_seconds for note in notes]
    assert onsets == sorted(onsets)


def test_dsp_backend_mono_and_polyphonic_are_a_selection_not_a_mode():
    """Both lists are always computed by batch_transcribe.transcribe();
    this backend picks one. Asserting both run keeps that documented
    behaviour honest."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    audio = _tone(60, 1.2, sample_rate)
    poly = DspNoteTranscriber(polyphonic=True).transcribe(audio, sample_rate)
    mono = DspNoteTranscriber(polyphonic=False).transcribe(audio, sample_rate)
    assert isinstance(poly, list) and isinstance(mono, list)


# --- TemplateChordEstimator (map #123, #141's no-extra tier) ---------------


def _chord_audio(midis, seconds, sample_rate, amplitude=0.4):
    """A sustained chord built from harmonic-rich tones -- the same
    synthesize-don't-fixture rule the rest of this suite follows."""
    total = np.zeros(int(seconds * sample_rate))
    for midi in midis:
        total += _tone(midi, seconds, sample_rate, amplitude=amplitude)
    return total / max(np.max(np.abs(total)), 1e-9) * amplitude


C_MAJOR = (48, 52, 55)      # C3 E3 G3
F_MAJOR = (53, 57, 60)      # F3 A3 C4


def test_template_estimator_names_a_sustained_major_triad():
    from transcribe_backends import TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = _chord_audio(C_MAJOR, 1.0, sample_rate)
    spans = TemplateChordEstimator().estimate(audio, sample_rate)

    assert spans, "expected at least one chord span"
    assert spans[0].name.startswith("C"), f"expected a C chord, got {spans[0].name}"


def test_template_estimator_merges_a_held_chord_into_one_span():
    """Four beats of C must read as one two-second C, not four spans --
    what <harmony> (#131) and a human reader both want."""
    from transcribe_backends import BeatGrid, TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = _chord_audio(C_MAJOR, 2.0, sample_rate)
    grid = BeatGrid(beat_seconds=(0.0, 0.5, 1.0, 1.5, 2.0), downbeat_seconds=(0.0, 2.0))

    spans = TemplateChordEstimator().estimate(audio, sample_rate, beats=grid)
    assert len(spans) == 1, [s.name for s in spans]
    assert spans[0].start_seconds == pytest.approx(0.0)
    assert spans[0].end_seconds == pytest.approx(2.0)


def test_template_estimator_segments_on_the_beat_grid_when_given_one():
    """A chord change is found at the beat it happens on."""
    from transcribe_backends import BeatGrid, TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = np.concatenate(
        [_chord_audio(C_MAJOR, 1.0, sample_rate), _chord_audio(F_MAJOR, 1.0, sample_rate)]
    )
    grid = BeatGrid(beat_seconds=(0.0, 0.5, 1.0, 1.5, 2.0), downbeat_seconds=(0.0, 1.0))

    spans = TemplateChordEstimator().estimate(audio, sample_rate, beats=grid)
    names = [s.name for s in spans]
    assert len(spans) >= 2, names
    assert names[0].startswith("C"), names
    assert any(n.startswith("F") for n in names[1:]), names
    # The change lands on the beat where it actually happens.
    change = next(s for s in spans if s.name.startswith("F"))
    assert change.start_seconds == pytest.approx(1.0, abs=0.51)


def test_template_estimator_falls_back_to_fixed_windows_without_a_grid():
    from transcribe_backends import TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = _chord_audio(C_MAJOR, 1.0, sample_rate)
    spans = TemplateChordEstimator(window_seconds=0.25).estimate(audio, sample_rate)
    assert spans
    assert spans[0].end_seconds <= 1.0 + 1e-6


def test_template_estimator_covers_audio_past_the_last_detected_beat():
    """A beat grid ends at the last detected beat, not at the end of the
    file, and the tail can hold the final chord."""
    from transcribe_backends import BeatGrid, TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = _chord_audio(C_MAJOR, 2.0, sample_rate)
    grid = BeatGrid(beat_seconds=(0.0, 0.5), downbeat_seconds=(0.0,))
    spans = TemplateChordEstimator().estimate(audio, sample_rate, beats=grid)
    assert spans
    assert spans[-1].end_seconds == pytest.approx(2.0, abs=0.01)


def test_template_estimator_returns_nothing_for_silence():
    """Blank rather than a guess -- the posture chord_templates.match()
    already takes, carried through rather than papered over."""
    from transcribe_backends import TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    assert TemplateChordEstimator().estimate(np.zeros(sample_rate), sample_rate) == []


def test_template_estimator_handles_empty_audio():
    from transcribe_backends import TemplateChordEstimator

    assert TemplateChordEstimator().estimate(np.array([]), config.SAMPLE_RATE) == []


def test_template_estimator_min_span_filter_runs_after_merging():
    """Documented ordering that matters: four beats of C merge into one
    2s span which then survives a 1s minimum, where each individual beat
    would have failed it."""
    from transcribe_backends import BeatGrid, TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = _chord_audio(C_MAJOR, 2.0, sample_rate)
    grid = BeatGrid(beat_seconds=(0.0, 0.5, 1.0, 1.5, 2.0), downbeat_seconds=(0.0,))
    spans = TemplateChordEstimator(min_span_seconds=1.0).estimate(audio, sample_rate, beats=grid)
    assert len(spans) == 1
    assert spans[0].end_seconds - spans[0].start_seconds >= 1.0


def test_template_estimator_spans_are_ordered_and_non_overlapping():
    from transcribe_backends import TemplateChordEstimator

    sample_rate = config.SAMPLE_RATE
    audio = np.concatenate(
        [_chord_audio(C_MAJOR, 1.0, sample_rate), _chord_audio(F_MAJOR, 1.0, sample_rate)]
    )
    spans = TemplateChordEstimator(window_seconds=0.25).estimate(audio, sample_rate)
    for a, b in zip(spans, spans[1:]):
        assert a.end_seconds <= b.start_seconds + 1e-6
        assert a.start_seconds < a.end_seconds


# --- BeatThisTracker (map #123, #130's beat/downbeat source) --------------
#
# Beat This! is not installed here (it needs ~1.1 GB of CPU torch), so
# these drive the adapter against an injected fake module -- the same
# "test the decisions without the library" split `test_sf2_playback.py`
# uses for FluidSynth. What is verified is the wiring: that the adapter
# calls the API `beat_this` 1.1.0 actually exposes (read from its wheel),
# converts the result correctly, and refuses with an install line when the
# library is absent.


class _FakeAudio2Beats:
    instances = []

    def __init__(self, checkpoint_path="final0", device="cpu", float16=False, dbn=False):
        self.kwargs = dict(
            checkpoint_path=checkpoint_path, device=device, float16=float16, dbn=dbn
        )
        self.calls = []
        _FakeAudio2Beats.instances.append(self)

    def __call__(self, signal, sr):
        self.calls.append((len(signal), sr))
        return np.array([0.0, 0.5, 1.0, 1.5]), np.array([0.0, 1.0])


@pytest.fixture
def fake_beat_this(monkeypatch):
    import sys
    import types

    _FakeAudio2Beats.instances = []
    pkg = types.ModuleType("beat_this")
    inference = types.ModuleType("beat_this.inference")
    inference.Audio2Beats = _FakeAudio2Beats
    pkg.inference = inference
    monkeypatch.setitem(sys.modules, "beat_this", pkg)
    monkeypatch.setitem(sys.modules, "beat_this.inference", inference)
    return _FakeAudio2Beats


def test_beat_this_returns_a_beat_grid(fake_beat_this):
    from transcribe_backends import BeatThisTracker

    grid = BeatThisTracker().track(np.zeros(1000), 22050)
    assert grid.beat_seconds == (0.0, 0.5, 1.0, 1.5)
    assert grid.downbeat_seconds == (0.0, 1.0)
    # Two downbeats is one bar -- not enough to take a mode over, so the
    # grid correctly declines to guess a meter here.
    assert grid.beats_per_bar() is None


def test_beat_this_passes_the_sample_rate_through_rather_than_resampling(fake_beat_this):
    """Beat This! resamples internally in Audio2Frames.signal2spect();
    doing it here too would be strictly worse."""
    from transcribe_backends import BeatThisTracker

    BeatThisTracker().track(np.zeros(4321), 44100)
    assert fake_beat_this.instances[0].calls == [(4321, 44100)]


def test_beat_this_does_not_use_the_madmom_dbn(fake_beat_this):
    """#130 declined madmom (git pin on 3.14), and Beat This!'s own
    argument against the DBN is that its 55-215 BPM and constant-meter
    priors break on real material -- exactly the tempo-drift case here."""
    from transcribe_backends import BeatThisTracker

    BeatThisTracker().track(np.zeros(1000), 22050)
    assert fake_beat_this.instances[0].kwargs["dbn"] is False


def test_beat_this_defaults_to_cpu(fake_beat_this):
    """No GPU on the target machine (#123)."""
    from transcribe_backends import BeatThisTracker

    BeatThisTracker().track(np.zeros(1000), 22050)
    assert fake_beat_this.instances[0].kwargs["device"] == "cpu"


def test_beat_this_accepts_and_ignores_a_drum_stem(fake_beat_this):
    """The finding that qualifies #130: beat_this 1.1.0's Audio2Beats
    takes exactly one signal, with no extra-channel input anywhere. #127's
    measured drum-stem gain came from Beat Transformer, a multi-channel
    architecture, and does not transfer. Ignoring it is the Protocol's
    documented path, not an oversight -- and mixing a boosted drum stem in
    would be an unmeasured heuristic."""
    from transcribe_backends import BeatThisTracker

    drums = np.ones(1000)
    grid = BeatThisTracker().track(np.zeros(1000), 22050, drum_stem=drums)
    assert grid.beat_seconds  # still tracked
    # The stem never reached the model: only the mix did.
    assert fake_beat_this.instances[0].calls == [(1000, 22050)]


def test_beat_this_loads_the_model_once_across_calls(fake_beat_this):
    """Loading weights per call would dominate the runtime budget."""
    from transcribe_backends import BeatThisTracker

    tracker = BeatThisTracker()
    tracker.track(np.zeros(1000), 22050)
    tracker.track(np.zeros(1000), 22050)
    assert len(fake_beat_this.instances) == 1


def test_beat_this_refuses_with_an_install_line_when_absent(monkeypatch):
    """#129's refuse-don't-degrade, at the backend boundary."""
    import builtins

    from transcribe_backends import BeatThisTracker

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("beat_this"):
            raise ImportError("no beat_this")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ConversionUnavailable) as excinfo:
        BeatThisTracker().track(np.zeros(1000), 22050)
    assert excinfo.value.install_hint == "pip install -e .[convert]"


def test_conversion_unavailable_keeps_the_reason_separate_from_the_hint():
    """So a caller laying the two out itself isn't left splitting str(exc)
    on a newline."""
    exc = ConversionUnavailable("Beat tracking needs Beat This!.", "pip install -e .[convert]")
    assert exc.message == "Beat tracking needs Beat This!."
    assert "pip install" not in exc.message
    assert exc.install_hint in str(exc)


# --- BasicPitchModel (map #123, #129's per-stem note detector) ------------
#
# These run the REAL vendored graph -- it is committed to the repo (225 KB,
# Apache-2.0, under #139's 1 MB ceiling), so there is nothing to download
# and no reason to fake it. They skip only when onnxruntime is absent,
# since that lives behind the [convert] extra.

ort = pytest.importorskip("onnxruntime", reason="onnxruntime lives behind the [convert] extra")


def _midi_tone(midi, seconds, sample_rate=22050, harmonics=(1.0, 0.4)):
    freq = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    x = np.arange(int(seconds * sample_rate)) / sample_rate
    return sum(a * np.sin(2 * np.pi * freq * n * x) for n, a in enumerate(harmonics, start=1))


def test_basic_pitch_model_loads_the_vendored_graph():
    """No download: #125 found the pip package will not install on 3.14,
    so the graph is committed and driven directly."""
    from transcribe_backends import BasicPitchModel

    import os

    path = BasicPitchModel()._resolve_path()
    assert os.path.exists(path), f"vendored model missing at {path}"
    assert os.path.getsize(path) == 230444


def test_basic_pitch_posteriorgram_shapes_and_frame_count():
    """Frame count must follow the *original* audio duration, not the
    zero-padded final window -- padding read as real frames would be
    trailing phantom silence for a decoder to chew on."""
    from transcribe_backends import BASIC_PITCH_FPS, BasicPitchModel

    sample_rate = 22050
    seconds = 3.0
    audio = 0.5 * _midi_tone(69, seconds)
    grams = BasicPitchModel().posteriorgrams(audio, sample_rate)

    expected = int(np.floor(seconds * sample_rate * (BASIC_PITCH_FPS / sample_rate)))
    assert grams.note.shape == (expected, 88)
    assert grams.onset.shape == (expected, 88)
    assert grams.contour.shape == (expected, 264)


def test_basic_pitch_identifies_a_sustained_a4():
    """The end-to-end check that the windowing, output-name mapping and
    unwrapping are all right: get any of them wrong and the peak bin moves."""
    from transcribe_backends import BASIC_PITCH_MIDI_OFFSET, BasicPitchModel

    grams = BasicPitchModel().posteriorgrams(0.5 * _midi_tone(69, 3.0), 22050)
    peak_bin = int(np.argmax(grams.note.mean(axis=0)))
    assert peak_bin + BASIC_PITCH_MIDI_OFFSET == 69


def test_basic_pitch_is_polyphonic():
    """The reason this model is here rather than a monophonic detector."""
    from transcribe_backends import BASIC_PITCH_MIDI_OFFSET, BasicPitchModel

    sample_rate = 22050
    audio = sum(_midi_tone(m, 2.5) for m in (60, 64, 67))
    audio = audio / np.max(np.abs(audio)) * 0.6
    grams = BasicPitchModel().posteriorgrams(audio, sample_rate)

    mean = grams.note.mean(axis=0)
    top3 = {int(b) + BASIC_PITCH_MIDI_OFFSET for b in np.argsort(mean)[-3:]}
    assert top3 == {60, 64, 67}


def test_basic_pitch_reports_onsets_where_a_note_starts():
    from transcribe_backends import BASIC_PITCH_MIDI_OFFSET, BasicPitchModel

    grams = BasicPitchModel().posteriorgrams(0.5 * _midi_tone(69, 2.0), 22050)
    onset_column = grams.onset[:, 69 - BASIC_PITCH_MIDI_OFFSET]
    assert onset_column.max() > 0.5
    # The attack is near the start, not scattered through the sustain.
    assert int(np.argmax(onset_column)) < len(onset_column) // 4


def test_basic_pitch_frame_times_line_up_with_the_grid():
    from transcribe_backends import BASIC_PITCH_FPS, BasicPitchModel

    grams = BasicPitchModel().posteriorgrams(0.5 * _midi_tone(69, 2.0), 22050)
    times = grams.frame_times()
    assert times[0] == 0.0
    assert times[1] == pytest.approx(1.0 / BASIC_PITCH_FPS)
    assert len(times) == grams.note.shape[0]


def test_basic_pitch_handles_empty_audio():
    from transcribe_backends import BasicPitchModel

    grams = BasicPitchModel().posteriorgrams(np.array([]), 22050)
    assert grams.note.shape == (0, 88) and grams.contour.shape == (0, 264)


def test_basic_pitch_handles_audio_shorter_than_one_window():
    """A clip under 2 seconds still has to produce frames -- the final
    window is zero-padded, and the unwrap has to trim that padding back off."""
    from transcribe_backends import BasicPitchModel

    grams = BasicPitchModel().posteriorgrams(0.5 * _midi_tone(69, 0.5), 22050)
    assert 0 < grams.note.shape[0] <= int(0.5 * 86) + 1


def test_basic_pitch_spans_several_windows_continuously():
    """Longer than one 2s window, so unwrapping and overlap-trimming are
    actually exercised: a sustained note must stay detected across the
    seam rather than dropping out at it."""
    from transcribe_backends import BASIC_PITCH_MIDI_OFFSET, BasicPitchModel

    grams = BasicPitchModel().posteriorgrams(0.5 * _midi_tone(69, 6.0), 22050)
    column = grams.note[:, 69 - BASIC_PITCH_MIDI_OFFSET]
    # No sustained dropout anywhere in the middle of the note.
    middle = column[10:-10]
    assert middle.min() > 0.1, f"dropout at a window seam: min {middle.min():.3f}"


def test_basic_pitch_resamples_a_non_native_rate():
    """44.1 kHz is the common real case; the model is trained at 22050."""
    from transcribe_backends import BASIC_PITCH_MIDI_OFFSET, BasicPitchModel

    audio = 0.5 * _midi_tone(69, 3.0, sample_rate=44100)
    grams = BasicPitchModel().posteriorgrams(audio, 44100)
    peak_bin = int(np.argmax(grams.note.mean(axis=0)))
    assert peak_bin + BASIC_PITCH_MIDI_OFFSET == 69


def test_basic_pitch_reuses_one_session_across_calls():
    """InferenceSession construction is not free and a converter runs this
    per stem."""
    from transcribe_backends import BasicPitchModel

    model = BasicPitchModel()
    model.posteriorgrams(0.5 * _midi_tone(69, 0.5), 22050)
    first = model._session
    model.posteriorgrams(0.5 * _midi_tone(69, 0.5), 22050)
    assert model._session is first


def test_basic_pitch_refuses_clearly_when_the_model_file_is_missing(tmp_path):
    from transcribe_backends import BasicPitchModel

    missing = tmp_path / "not_here.onnx"
    with pytest.raises(ConversionUnavailable) as excinfo:
        BasicPitchModel(model_path=missing).posteriorgrams(np.zeros(1000), 22050)
    assert "incomplete checkout" in str(excinfo.value)
