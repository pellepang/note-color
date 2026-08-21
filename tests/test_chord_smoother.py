import numpy as np

import chroma
import config
import multipitch
import pitch_detect
from chord_smoother import ChordSmoother
from multipitch import NoteCandidate

SAMPLE_RATE = 22050

PITCH = {name: i for i, name in enumerate(
    ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
)}


def note(pitch_class, octave, freq, confidence=0.9):
    return NoteCandidate(pitch_class=pitch_class, octave=octave, freq=freq, confidence=confidence)


def notes_for(*note_names, octave=4, confidence=0.9):
    """NoteCandidate list standing in for what multipitch.detect() would
    report for these pitch classes -- chord-name matching is driven by
    this (see chord_smoother._update_chord_name), not by a raw chroma
    vector, so this is the fixture that actually exercises it now."""
    return [
        NoteCandidate(pitch_class=PITCH[name], octave=octave, freq=100.0 + PITCH[name], confidence=confidence)
        for name in note_names
    ]


def feed(smoother, chroma, bass_chroma, notes, count):
    result = None
    for _ in range(count):
        result = smoother.update(chroma, bass_chroma, notes)
    return result


def test_chord_name_locks_in_after_debounce_hops():
    s = ChordSmoother(config)
    c_major = notes_for("C", "E", "G")
    silent_bass = np.zeros(12)
    name, _stack = feed(s, None, silent_bass, c_major, config.CHORD_DEBOUNCE_HOPS)
    assert name == "C"


def test_single_hop_candidate_blip_does_not_flicker_display():
    s = ChordSmoother(config)
    c_major = notes_for("C", "E", "G")
    g_major = notes_for("G", "B", "D")
    silent_bass = np.zeros(12)

    feed(s, None, silent_bass, c_major, config.CHORD_DEBOUNCE_HOPS + config.CHORD_MEDIAN_WINDOW)
    name, _stack = s.update(None, silent_bass, g_major)  # single differing hop
    assert name == "C"

    name, _stack = feed(s, None, silent_bass, c_major, 3)
    assert name == "C"


def test_no_match_reported_as_none_after_debounce():
    s = ChordSmoother(config)
    cluster = notes_for("C", "C#", "D", "D#", "E", "F")  # resembles no chord template
    silent_bass = np.zeros(12)
    name, _stack = feed(s, None, silent_bass, cluster, config.CHORD_DEBOUNCE_HOPS)
    assert name is None


def test_note_stack_entry_needs_attack_hops_before_appearing():
    s = ChordSmoother(config)
    silence_chroma = np.zeros(12)
    n = note(0, 4, 261.6)

    # fewer than ATTACK_HOPS detections: not yet in the stack
    _name, stack = feed(s, silence_chroma, silence_chroma, [n], config.NOTE_STACK_ATTACK_HOPS - 1)
    assert stack == []

    # one more detection reaches ATTACK_HOPS: now active
    _name, stack = s.update(silence_chroma, silence_chroma, [n])
    assert {(e["pitch_class"], e["octave"]) for e in stack} == {(0, 4)}


def test_note_stack_entry_survives_a_brief_gap_under_release_hops():
    s = ChordSmoother(config)
    silence_chroma = np.zeros(12)
    n = note(0, 4, 261.6)

    feed(s, silence_chroma, silence_chroma, [n], config.NOTE_STACK_ATTACK_HOPS)
    for _ in range(config.NOTE_STACK_RELEASE_HOPS - 1):
        _name, stack = s.update(silence_chroma, silence_chroma, [])
        assert {(e["pitch_class"], e["octave"]) for e in stack} == {(0, 4)}


def test_note_stack_entry_drops_after_release_hops_of_silence():
    s = ChordSmoother(config)
    silence_chroma = np.zeros(12)
    n = note(0, 4, 261.6)

    feed(s, silence_chroma, silence_chroma, [n], config.NOTE_STACK_ATTACK_HOPS)
    _name, stack = feed(s, silence_chroma, silence_chroma, [], config.NOTE_STACK_RELEASE_HOPS)
    assert stack == []


def test_lowest_active_note_is_marked_bass():
    s = ChordSmoother(config)
    silence_chroma = np.zeros(12)
    low = note(0, 3, 130.8)
    mid = note(4, 4, 329.6)
    high = note(7, 5, 784.0)

    _name, stack = feed(s, silence_chroma, silence_chroma, [low, mid, high], config.NOTE_STACK_ATTACK_HOPS)
    by_pc = {e["pitch_class"]: e for e in stack}
    assert by_pc[0]["is_bass"] is True
    assert by_pc[4]["is_bass"] is False
    assert by_pc[7]["is_bass"] is False


def test_chord_change_overflowing_max_notes_shows_incoming_notes_not_stale_ones():
    # A chord change can briefly push more than CHORD_MAX_NOTES slots into
    # "active" at once: the outgoing chord's notes are still coasting
    # through their release hysteresis while the incoming chord's notes
    # have just cleared attack_hops. Regression for a bug where the
    # overflow was trimmed by pitch (always keep the lowest notes), which
    # silently hid an entire freshly-attacked chord above it until the old
    # one's release window fully timed out -- confidence-based trimming
    # should surface the newly-attacked notes instead.
    s = ChordSmoother(config)
    silence_chroma = np.zeros(12)
    old_notes = [note(pc, 2, 100.0 + pc, confidence=0.9) for pc in (0, 2, 4, 5, 7, 9)]
    feed(s, silence_chroma, silence_chroma, old_notes, config.NOTE_STACK_ATTACK_HOPS + 2)

    new_notes = [note(pc, 6, 1500.0 + pc, confidence=0.9) for pc in (1, 3, 6, 8, 10, 11)]
    _name, stack = feed(s, silence_chroma, silence_chroma, new_notes, config.NOTE_STACK_ATTACK_HOPS)

    assert len(stack) == config.CHORD_MAX_NOTES
    assert {e["pitch_class"] for e in stack} == {1, 3, 6, 8, 10, 11}


def _freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def _make_tone(freq, sample_rate=SAMPLE_RATE, duration=0.4, harmonics=(1.0, 0.5, 0.3, 0.15)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal


def _real_pipeline_update(smoother, pitch_classes, octave=3):
    """Routes real, harmonically-rich (not idealized one-hot) tones
    through the actual live pipeline -- compute_spectrum -> chroma.fold()/
    fold_bass() -> multipitch.detect() -- into one ChordSmoother.update()
    call, the same shape main.py's analysis_loop() and
    batch_transcribe.transcribe() both use."""
    mix = sum(_make_tone(_freq_for(pc, octave)) for pc in pitch_classes)
    window = mix[-2048:]
    spectrum = pitch_detect.compute_spectrum(window, SAMPLE_RATE)
    main_chroma = chroma.fold(spectrum, SAMPLE_RATE)
    bass_chroma = chroma.fold_bass(spectrum, SAMPLE_RATE)
    raw_notes = multipitch.detect(window, SAMPLE_RATE, max_notes=config.CHORD_MAX_NOTES)
    return smoother.update(main_chroma, bass_chroma, raw_notes)


def test_real_pipeline_identifies_plain_triad_not_an_extended_chord():
    # Regression for issue #56: chord-name matching used to run directly
    # against chroma.fold()'s raw harmonic-summed spectrum energy, which
    # has no way to tell one note's overtone bleeding into a different
    # pitch class apart from a real chord tone -- a plain C major triad
    # (C-E-G) with ordinary instrument-like harmonics (not pure sine
    # tones) misidentified as "G13" before this fix, because E's 3rd
    # harmonic (a B) and G's 3rd harmonic (a D) inflated the chroma vector
    # into looking like a much richer chord. Routing chord-name matching
    # through multipitch.detect()'s already harmonic-pruned note
    # candidates instead of the raw chroma fixes this.
    s = ChordSmoother(config)
    name = None
    for _ in range(config.CHORD_DEBOUNCE_HOPS + config.CHORD_MEDIAN_WINDOW):
        name, _stack = _real_pipeline_update(s, [0, 4, 7])  # C major triad
    assert name == "C"


def test_real_pipeline_identifies_dominant_seventh_not_an_extended_chord():
    s = ChordSmoother(config)
    name = None
    for _ in range(config.CHORD_DEBOUNCE_HOPS + config.CHORD_MEDIAN_WINDOW):
        name, _stack = _real_pipeline_update(s, [0, 4, 7, 10])  # C7
    assert name == "C7"


def test_real_pipeline_retains_all_six_notes_of_a_dense_non_colliding_chord():
    # issue #68: note_stack's effective size plateaued around ~4.2-4.3
    # under real acoustic capture regardless of whether 3-6 real notes
    # were sounding. Confirms which layer(s) still lose notes after
    # #67's ordering fix, using the SAME real wiring
    # (compute_spectrum -> chroma.fold()/fold_bass() ->
    # multipitch.detect() -> ChordSmoother.update()) main.py's
    # analysis_loop() and batch_transcribe.transcribe() both use --
    # a single-octave 6-note cluster (all pitch classes within one
    # octave, so no pair is close to a harmonic-collision ratio; see
    # test_multipitch.test_dense_six_note_chord_all_survive_when_not_
    # harmonically_colliding for why that distinction matters) must
    # survive both multipitch.detect()'s pruning and ChordSmoother's
    # max_notes trim intact.
    s = ChordSmoother(config)
    pcs = [0, 1, 3, 6, 8, 10]
    stack = []
    for _ in range(config.NOTE_STACK_ATTACK_HOPS + 3):
        _name, stack = _real_pipeline_update(s, pcs)
    assert {e["pitch_class"] for e in stack} == set(pcs)
    assert len(stack) == 6


def test_note_stack_trimming_converges_to_new_chord_after_a_few_hops():
    # Second hypothesis from issue #68: that ChordSmoother._update_note_
    # stack()'s confidence-based trimming to CHORD_MAX_NOTES could itself
    # be evicting real, currently-sounding notes in favor of stale/
    # decaying ones once more than a few notes compete for slots. A
    # worst-case transient (a full CHORD_MAX_NOTES-note chord change,
    # 6 outgoing + 6 incoming = 12 candidates briefly competing for 6
    # slots) is exercised directly here (bypassing multipitch.detect()
    # entirely, so this isolates the trimming logic on its own): the
    # stack should fully settle onto the new chord's 6 notes within a
    # handful of hops, not get stuck showing a mix of stale and fresh
    # notes indefinitely.
    s = ChordSmoother(config)
    silence_chroma = np.zeros(12)
    old_notes = [note(pc, 2, 100.0 + pc, confidence=0.9) for pc in (0, 2, 4, 5, 7, 9)]
    feed(s, silence_chroma, silence_chroma, old_notes, config.NOTE_STACK_ATTACK_HOPS + 2)

    new_notes = [note(pc, 5, 800.0 + pc, confidence=0.9) for pc in (1, 3, 6, 8, 10, 11)]
    stack = []
    for _ in range(config.NOTE_STACK_ATTACK_HOPS + config.NOTE_STACK_RELEASE_HOPS):
        _name, stack = s.update(silence_chroma, silence_chroma, new_notes)
    assert {e["pitch_class"] for e in stack} == {1, 3, 6, 8, 10, 11}
    assert len(stack) == 6
