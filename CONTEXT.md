# note-color

Real-time audio-to-color visualizer. This glossary covers the vocabulary introduced by the chord-mode effort (see wayfinder map [#1](https://github.com/pellepang/note-color/issues/1)) — the monophonic pipeline's terms (note, pitch, onset) are covered by code comments and `docs/DECISIONS.md`, not repeated here.

## Language

**Chroma vector**:
A 12-element vector folding all spectral energy in an analysis window into the 12 pitch classes (C, C♯, D, ... B), discarding octave. The input to chord matching.
_Avoid_: Pitch-class profile (PCP) — same concept, but this project uses "chroma vector" consistently.

**Pitch-class set**:
The set of pitch classes actually detected as sounding at a given moment (a chroma vector thresholded into present/absent), before any chord-name is assigned to it. What the "no match" fallback reports when nothing in the chord dictionary matches well enough.

**Chord quality**:
A root-relative pattern of semitone intervals (e.g. maj = 0,4,7) that defines a family of chords across all 12 roots. The chord dictionary stores one binary pitch-class mask per quality, not per individual chord.
_Avoid_: Chord type — "quality" is the standard music-theory term and what the dictionary is keyed on.

**Chord template**:
A chord quality rotated to a specific root — one of the ~360 entries (30 qualities × 12 roots) tested against the observed chroma vector during matching.

**Chord mode**:
The opt-in detection mode (toggled via `P` in terminal views) that runs chroma-vector chord recognition instead of monophonic YIN pitch detection. Coexists with the existing default mode; does not replace it.
