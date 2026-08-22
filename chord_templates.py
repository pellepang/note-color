"""Chord template dictionary and cosine-similarity matching against an
observed chroma vector, per docs spec (wayfinder #1, issues #3/#4/#7).

30 chord qualities (root-relative binary pitch-class masks) x 12 roots =
360 templates. Root spelling is this project's flat-biased convention
(color_map.NOTE_NAMES_FIFTHS), applied uniformly to every chord-name root.
"""

from collections import namedtuple

import numpy as np

from color_map import NOTE_NAMES_FIFTHS

DEFAULT_THRESHOLD = 0.80
# fold_bass() (chroma.py) restricts itself to <~250Hz -- for a chord voiced
# entirely above that (no genuine bass note sounding), its output is just
# spectral-leakage noise, not a real detection. Empirically, that noise
# floor's peak sits at roughly 0.15x the main chroma's peak, while a real
# sounding bass note (even the chord's own root, played low) sits at
# 0.35x+ -- gate on a value between the two so silence in the bass register
# doesn't get misread as a slash-chord bass note.
DEFAULT_BASS_CONFIDENCE_RATIO = 0.25

# name -> (root-relative semitone offsets, jazz-symbol suffix)
QUALITIES = {
    "maj": ({0, 4, 7}, ""),
    "min": ({0, 3, 7}, "-"),
    "dim": ({0, 3, 6}, "°"),
    "aug": ({0, 4, 8}, "+"),
    "maj6": ({0, 4, 7, 9}, "6"),
    "min6": ({0, 3, 7, 9}, "-6"),
    "dom7": ({0, 4, 7, 10}, "7"),
    "maj7": ({0, 4, 7, 11}, "Δ7"),
    "min7": ({0, 3, 7, 10}, "-7"),
    "dim7": ({0, 3, 6, 9}, "°7"),
    "half-dim7": ({0, 3, 6, 10}, "ø7"),
    "sus2": ({0, 2, 7}, "sus2"),
    "sus4": ({0, 5, 7}, "sus4"),
    "add9": ({0, 2, 4, 7}, "add9"),
    "dom9": ({0, 2, 4, 7, 10}, "9"),
    "maj9": ({0, 2, 4, 7, 11}, "Δ9"),
    "min9": ({0, 2, 3, 7, 10}, "-9"),
    "dom11": ({0, 2, 4, 5, 7, 10}, "11"),
    "min11": ({0, 2, 3, 5, 7, 10}, "-11"),
    "dom13": ({0, 2, 4, 5, 7, 9, 10}, "13"),
    "maj13": ({0, 2, 4, 7, 9, 11}, "Δ13"),
    "min13": ({0, 2, 3, 5, 7, 9, 10}, "-13"),
    "dom7sharp9": ({0, 3, 4, 7, 10}, "7#9"),
    "dom7flat9": ({0, 1, 4, 7, 10}, "7b9"),
    "dom7sharp5": ({0, 4, 8, 10}, "7#5"),
    "dom7flat5": ({0, 4, 6, 10}, "7b5"),
    "dom13flat9": ({0, 1, 4, 7, 9, 10}, "13b9"),
    "six_nine": ({0, 2, 4, 7, 9}, "6/9"),
    "maj7sharp5": ({0, 4, 8, 11}, "Δ7#5"),
    "dim_maj7": ({0, 3, 6, 11}, "°Δ7"),
}

Template = namedtuple("Template", ["root", "quality", "symbol", "vector", "norm"])
ChordMatch = namedtuple("ChordMatch", ["name", "root", "quality", "bass", "similarity"])

_quality_norms = {
    quality: float(np.sqrt(len(offsets))) for quality, (offsets, _symbol) in QUALITIES.items()
}

TEMPLATES = [
    Template(
        root=root,
        quality=quality,
        symbol=symbol,
        vector=np.array(
            [1.0 if pc in {(offset + root) % 12 for offset in offsets} else 0.0 for pc in range(12)]
        ),
        norm=_quality_norms[quality],
    )
    for quality, (offsets, symbol) in QUALITIES.items()
    for root in range(12)
]


def _format_name(root, symbol, bass):
    name = NOTE_NAMES_FIFTHS[root] + symbol
    if bass != root:
        name += "/" + NOTE_NAMES_FIFTHS[bass]
    return name


def _detect_bass(bass_chroma, fallback_root):
    if bass_chroma is None:
        return fallback_root
    bass_chroma = np.asarray(bass_chroma)
    if np.max(bass_chroma) <= 0:
        return fallback_root
    return int(np.argmax(bass_chroma))


def _resolve_tie(candidates, bass_chroma, lowest_pc=None):
    if len(candidates) == 1:
        return candidates[0]
    if bass_chroma is not None:
        bass_chroma = np.asarray(bass_chroma)
        if np.max(bass_chroma) > 0:
            detected_bass = int(np.argmax(bass_chroma))
            for template in candidates:
                if template.root == detected_bass:
                    return template
    # No confident sub-bass-cutoff note to disambiguate (issue #67): a
    # symmetric/rotationally-ambiguous chord (aug, dim7, min7/maj6, etc.)
    # voiced entirely above the bass register has no genuine "slash chord"
    # bass signal at all, so the old fallback here (always the lowest
    # root-index template, a fixed but musically arbitrary tiebreak)
    # answered wrong whenever the actual lowest note played wasn't that
    # template's root -- e.g. an F#/A#/D augmented triad, close-voiced
    # with F# lowest, was named "D+" every time. `lowest_pc` -- the pitch
    # class of whichever detected note is lowest in frequency this hop,
    # regardless of register -- is a much better guess for an ambiguous
    # chord's root than an arbitrary index: root-position voicing (root in
    # the bass) is the common case this app's own acoustic test fixtures
    # and ordinary playing both default to, so preferring the template
    # whose root matches the lowest note actually sounding is right far
    # more often than picking whichever root happens to sort first.
    if lowest_pc is not None:
        for template in candidates:
            if template.root == lowest_pc:
                return template
    return min(candidates, key=lambda t: t.root)


def match(chroma, bass_chroma=None, threshold=DEFAULT_THRESHOLD, bass_confidence_ratio=DEFAULT_BASS_CONFIDENCE_RATIO, lowest_pc=None):
    """Return a `ChordMatch` for the best-fitting template against
    `chroma`, or `None` if nothing clears `threshold` (cosine similarity).

    `lowest_pc` (optional): pitch class of the lowest-frequency note
    actually detected this hop, regardless of register/bass-cutoff -- used
    only as a last-resort tiebreak among rotationally-ambiguous templates
    when `bass_chroma` doesn't confidently pick a root (see
    `_resolve_tie`)."""
    chroma = np.asarray(chroma, dtype=np.float64)
    chroma_norm = np.linalg.norm(chroma)
    if chroma_norm == 0:
        return None

    confident_bass_chroma = None
    if bass_chroma is not None:
        bass_chroma_arr = np.asarray(bass_chroma, dtype=np.float64)
        chroma_peak = np.max(chroma)
        if chroma_peak > 0 and np.max(bass_chroma_arr) >= bass_confidence_ratio * chroma_peak:
            confident_bass_chroma = bass_chroma_arr
    bass_chroma = confident_bass_chroma

    best_similarity = -1.0
    best_candidates = []
    for template in TEMPLATES:
        similarity = np.dot(chroma, template.vector) / (chroma_norm * template.norm)
        if similarity > best_similarity + 1e-9:
            best_similarity = similarity
            best_candidates = [template]
        elif abs(similarity - best_similarity) <= 1e-9:
            best_candidates.append(template)

    if best_similarity < threshold:
        return None

    template = _resolve_tie(best_candidates, bass_chroma, lowest_pc)
    bass = _detect_bass(bass_chroma, fallback_root=template.root)
    name = _format_name(template.root, template.symbol, bass)
    return ChordMatch(name=name, root=template.root, quality=template.quality, bass=bass, similarity=best_similarity)
