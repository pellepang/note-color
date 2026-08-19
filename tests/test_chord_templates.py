import numpy as np

from chord_templates import match

# Pitch classes, C=0 .. B=11 (chromatic order), independent of the
# implementation's own quality dictionary -- these are literal, hand-known
# chord spellings used as ground truth.
PITCH = {name: i for i, name in enumerate(
    ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
)}


def chroma_for(*note_names, weight=1.0):
    vec = np.zeros(12)
    for name in note_names:
        vec[PITCH[name]] = weight
    return vec


def test_major_triad_matches_root_position():
    chroma = chroma_for("C", "E", "G")
    result = match(chroma)
    assert result is not None
    assert result.name == "C"
    assert result.root == 0
    assert result.bass == 0


def test_minor7_matches_with_flat_biased_root_spelling():
    # A minor 7th (A C E G) has the exact same pitch-class set as C major 6
    # (C E G A) -- a well-known chord-theory ambiguity a pitch-class-set
    # match alone can't resolve. Bass chroma is what picks A as the root.
    chroma = chroma_for("A", "C", "E", "G")
    bass_chroma = chroma_for("A", weight=1.0)
    result = match(chroma, bass_chroma)
    assert result is not None
    assert result.name == "A-7"
    assert result.root == 9


def test_dominant7_matches():
    # G7: G B D F
    chroma = chroma_for("G", "B", "D", "F")
    result = match(chroma)
    assert result is not None
    assert result.name == "G7"


def test_slash_chord_uses_bass_chroma_for_inversion():
    # C major triad over an E bass -> C/E
    chroma = chroma_for("C", "E", "G")
    bass_chroma = chroma_for("E", weight=1.0)
    result = match(chroma, bass_chroma)
    assert result is not None
    assert result.name == "C/E"
    assert result.root == 0
    assert result.bass == 4


def test_root_position_omits_slash_even_with_bass_chroma_present():
    chroma = chroma_for("C", "E", "G")
    bass_chroma = chroma_for("C", weight=1.0)
    result = match(chroma, bass_chroma)
    assert result is not None
    assert result.name == "C"


def test_rotationally_symmetric_dim7_resolved_by_bass_chroma():
    # A fully-diminished 7th chord's pitch-class set is identical no matter
    # which of its four notes is called the root (C dim7 == D#/Eb dim7 ==
    # F#/Gb dim7 == A dim7, all the same {C, D#, F#, A} set) -- bass chroma
    # is the only signal that can pick a root among the tied candidates.
    chroma = chroma_for("C", "D#", "F#", "A")
    bass_chroma = chroma_for("D#", weight=1.0)
    result = match(chroma, bass_chroma)
    assert result is not None
    assert result.root == 3  # D#/Eb
    assert result.bass == 3
    assert result.name == "Eb°7"  # root-position, bass == root


def test_no_match_below_threshold_returns_none():
    # Uniform energy across all 12 pitch classes resembles no chord template.
    chroma = np.ones(12)
    assert match(chroma) is None


def test_silent_chroma_returns_none():
    chroma = np.zeros(12)
    assert match(chroma) is None
