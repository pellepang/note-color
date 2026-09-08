# Chroma Gaussian sigma narrowed to 0.25 semitones; bass detection gated on a confidence ratio

Two bugs found via a live speaker→mic round-trip test with a real C major
triad (C-E-G), after all four new modules' own unit tests (built from
hand-constructed pitch-class vectors, not real audio) already passed:

1. **Wrong chord entirely.** `chroma.fold()`'s Gaussian log-frequency
   weighting matrix originally used a 0.5-semitone sigma (per #2's
   resolution). At that width, each candidate pitch class's Gaussian tail
   picked up enough of its neighbors' energy that the resulting chroma
   vector had a non-trivial "noise floor" across most of the 12 bins, not
   just the 3 real notes. A large chord template (e.g. a 7-note `min13`)
   accumulates more of that spread noise floor than a small, correct
   3-note `maj` template loses by comparison, so cosine similarity
   sometimes favored the wrong, larger template outright (`D-13/B` instead
   of `C` for a plain C major triad). Narrowing to 0.25 semitones (still
   wide enough to pass the low-frequency-discrimination test in
   `test_chroma.py`) fixed this on both the synthetic and live-audio case.
2. **Spurious slash chords.** Even after fix 1, a triad voiced entirely
   above `fold_bass()`'s ~250Hz cutoff (nothing below it) still got
   labeled with a wrong bass (e.g. `C/B` for a plain C-E-G triad) — because
   `fold_bass()`'s output in that case is pure spectral-leakage noise, and
   `chord_templates.match()` unconditionally trusted `argmax(bass_chroma)`
   whenever it was nonzero. Measured that noise floor's peak sits around
   0.15x the main chroma's peak, while a genuine sounding bass note sits
   at 0.35x+; added `DEFAULT_BASS_CONFIDENCE_RATIO = 0.25` in
   `chord_templates.py` as the gate between the two.

Both fixes are implementation-level corrections to #2/#4's originally
resolved constants, not new architectural decisions — the mechanism
(Gaussian-weighted harmonic summing, bass-chroma-driven slash naming) is
unchanged.
