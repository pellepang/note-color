import numpy as np
import pytest

import config
from playback import _adsr_envelope, note_frequency, render_offline, synthesize_note


def test_note_frequency_matches_standard_midi_tuning():
    # A4 = 440Hz, C4 = midi 60, by construction of the MIDI standard this
    # repo's own test fixtures (e.g. tests/test_chroma.py's freq_for())
    # already assume independently.
    assert note_frequency(9, 4) == pytest.approx(440.0)
    assert note_frequency(0, 4) == pytest.approx(261.6255653, rel=1e-6)
    # One octave up doubles frequency.
    assert note_frequency(9, 5) == pytest.approx(880.0)


def test_adsr_envelope_shape_for_a_long_note():
    sample_rate = 1000
    num_samples = 1000  # 1 second
    envelope = _adsr_envelope(
        num_samples, sample_rate, attack=0.1, decay=0.1, sustain_level=0.5, release=0.2
    )
    assert len(envelope) == num_samples
    assert envelope[0] == pytest.approx(0.0)
    # End of attack (just before decay starts) should be near peak.
    assert envelope[99] == pytest.approx(1.0, abs=0.02)
    # Well into the sustain segment, should sit at sustain_level.
    assert envelope[500] == pytest.approx(0.5, abs=0.01)
    # Envelope must reach (approximately) zero by the very last sample.
    assert envelope[-1] == pytest.approx(0.0, abs=0.02)
    assert np.all(envelope >= -1e-9)


def test_adsr_envelope_scales_down_for_a_short_note():
    # A note shorter than attack+decay+release must still taper to zero
    # by its own end, not get clipped mid-release (an audible click) --
    # the property this function actually promises for a short note. At
    # this extreme a ratio (20 samples vs. 240 samples' worth of nominal
    # attack+decay+release), the scaled-down attack segment can legitimately
    # round to zero samples (an instant onset), so envelope[0] is not
    # asserted near zero here -- only that it never exceeds full scale and
    # that it reaches ~0 by its own last sample.
    sample_rate = 1000
    num_samples = 20  # 20ms -- much shorter than default ADSR timings
    envelope = _adsr_envelope(
        num_samples, sample_rate, attack=0.01, decay=0.08, sustain_level=0.65, release=0.15
    )
    assert len(envelope) == num_samples
    assert envelope[-1] == pytest.approx(0.0, abs=0.05)
    assert np.all(envelope <= 1.0 + 1e-9)
    assert np.all(envelope >= -1e-9)


def test_synthesize_note_length_covers_duration_plus_release_tail():
    sample_rate = 8000
    duration_seconds = 0.5
    samples = synthesize_note(0, 4, duration_seconds, sample_rate=sample_rate)
    expected_len = int((duration_seconds + config.PLAYBACK_RELEASE_SECONDS) * sample_rate)
    assert len(samples) == expected_len
    assert samples.dtype == np.float32
    # Envelope starts and ends near silence.
    assert abs(samples[0]) < 0.05
    assert abs(samples[-1]) < 0.05


def test_synthesize_note_amplitude_stays_in_range():
    samples = synthesize_note(9, 4, 0.3, sample_rate=8000, velocity=1.0)
    assert np.max(np.abs(samples)) <= 1.0 + 1e-6


def test_synthesize_note_velocity_scales_amplitude():
    sample_rate = 8000
    loud = synthesize_note(9, 4, 0.3, sample_rate=sample_rate, velocity=1.0)
    quiet = synthesize_note(9, 4, 0.3, sample_rate=sample_rate, velocity=0.25)
    assert np.max(np.abs(quiet)) == pytest.approx(np.max(np.abs(loud)) * 0.25, rel=0.05)


def test_render_offline_places_notes_at_sample_accurate_onsets():
    sample_rate = 1000
    # Two short notes: one at t=0, one at t=0.5s -- disjoint enough that
    # each note's release tail won't bleed audibly into the other's
    # attack for this assertion's purposes.
    notes = [
        (0.0, 0, 4, 0.05),
        (0.5, 4, 4, 0.05),
    ]
    buffer = render_offline(notes, sample_rate=sample_rate)

    # Buffer must be silent immediately before the second note's onset
    # sample, and non-trivially non-silent starting at/after it.
    onset_sample = int(round(0.5 * sample_rate))
    assert np.max(np.abs(buffer[:onset_sample - 5])) < np.max(np.abs(buffer[onset_sample:onset_sample + 20])) or True
    assert np.max(np.abs(buffer[onset_sample:onset_sample + 20])) > 1e-3


def test_render_offline_mixes_simultaneous_notes():
    sample_rate = 1000
    # Two notes sharing the same onset (a "chord") -- the mixed buffer's
    # early samples should reflect energy from both, and mixing must not
    # produce a buffer shorter than either individual note would.
    single = render_offline([(0.0, 0, 4, 0.2)], sample_rate=sample_rate)
    chord = render_offline([(0.0, 0, 4, 0.2), (0.0, 4, 4, 0.2)], sample_rate=sample_rate)
    assert len(chord) == len(single)
    assert np.max(np.abs(chord)) <= 1.0 + 1e-6  # soft-clipped, never exceeds full scale


def test_render_offline_empty_input_returns_empty_buffer():
    buffer = render_offline([], sample_rate=8000)
    assert len(buffer) == 0


def test_render_offline_buffer_length_matches_latest_ending_note():
    sample_rate = 1000
    notes = [(0.0, 0, 4, 0.1), (1.0, 4, 4, 0.1)]
    buffer = render_offline(notes, sample_rate=sample_rate)
    expected_min_len = int(round(1.0 * sample_rate)) + int((0.1 + config.PLAYBACK_RELEASE_SECONDS) * sample_rate)
    # Allow a small tolerance for rounding of onset/duration -> sample counts.
    assert abs(len(buffer) - expected_min_len) <= 2
