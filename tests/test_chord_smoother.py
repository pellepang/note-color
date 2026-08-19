import numpy as np

import config
from chord_smoother import ChordSmoother
from multipitch import NoteCandidate

PITCH = {name: i for i, name in enumerate(
    ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
)}


def chroma_for(*note_names):
    vec = np.zeros(12)
    for name in note_names:
        vec[PITCH[name]] = 1.0
    return vec


def note(pitch_class, octave, freq, confidence=0.9):
    return NoteCandidate(pitch_class=pitch_class, octave=octave, freq=freq, confidence=confidence)


def feed(smoother, chroma, bass_chroma, notes, count):
    result = None
    for _ in range(count):
        result = smoother.update(chroma, bass_chroma, notes)
    return result


def test_chord_name_locks_in_after_debounce_hops():
    s = ChordSmoother(config)
    c_major = chroma_for("C", "E", "G")
    silent_bass = np.zeros(12)
    name, _stack = feed(s, c_major, silent_bass, [], config.CHORD_DEBOUNCE_HOPS)
    assert name == "C"


def test_single_hop_candidate_blip_does_not_flicker_display():
    s = ChordSmoother(config)
    c_major = chroma_for("C", "E", "G")
    g_major = chroma_for("G", "B", "D")
    silent_bass = np.zeros(12)

    feed(s, c_major, silent_bass, [], config.CHORD_DEBOUNCE_HOPS + config.CHORD_MEDIAN_WINDOW)
    name, _stack = s.update(g_major, silent_bass, [])  # single differing hop
    assert name == "C"

    name, _stack = feed(s, c_major, silent_bass, [], 3)
    assert name == "C"


def test_no_match_reported_as_none_after_debounce():
    s = ChordSmoother(config)
    uniform = np.ones(12)  # resembles no chord template
    silent_bass = np.zeros(12)
    name, _stack = feed(s, uniform, silent_bass, [], config.CHORD_DEBOUNCE_HOPS)
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
