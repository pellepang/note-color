# Bass-register chord garbling fixed with a second, gated ring buffer (issue #63)

Issue #63's own root-cause hypothesis (FFT bin density too coarse at low
absolute frequencies) turned out to be a red herring: reproducing the bug
and inspecting the raw magnitude spectrum directly showed that zero-padding
the same 2048-sample window up to 32x denser bins converged on the exact
same wrong peak locations. The real cause is that a 2048-sample (~93ms)
Hann window's mainlobe half-width (~21.5Hz at 22050Hz) is physically wider
than the gap between an ordinary low triad's fundamentals (C2→E2 is only
~17Hz) — the two peaks' mainlobes overlap and merge into one
wrong-frequency peak no amount of interpolation can separate, since the
information needed to tell them apart was never captured by that short a
window in the first place.

Confirmed empirically that a longer window resolves this correctly: 2x
`WINDOW_SIZE` (4096 samples, ~186ms) was already enough to detect both
test chords from the issue's repro perfectly; 4x added no further
improvement. Given the choice between three tradeoffs (widen harmonic
pruning tolerance at low frequencies — cheap but leaves the merged
fundamental itself wrong; grow the live window unconditionally — fixes it
but adds ~93ms of latency to every hop, including ordinary mid/treble
playing that never needed it; or a dedicated low-band estimator — most
surgical but real new-module work), the user chose the middle path: keep
`WINDOW_SIZE` as the default and add a second, longer ring buffer
(`config.MULTIPITCH_LOW_WINDOW_SIZE = 4096`) maintained alongside it in
`analysis_loop()`/`batch_transcribe.transcribe()`, and swap
`multipitch.detect()` onto it only for hops that actually have low content
to resolve.

That gate (`multipitch.select_window()`) reuses the same confidence-ratio
check `chord_templates.match()` already applies to `chroma.fold_bass()`'s
output for slash-chord bass detection (bass-chroma peak ≥
`MULTIPITCH_BASS_GATE_RATIO` (0.25) × main-chroma peak) rather than
inventing a new signal — `fold_bass()` already isolates the <~250Hz band
this problem lives in, and its existing gate already distinguishes real
low content from spectral-leakage noise. `select_window()` lives in
`multipitch.py` (not `main.py`/`batch_transcribe.py`) and takes plain
chroma arrays as arguments rather than importing `chroma.py` itself, so it
stays a pure, cheaply-unit-testable function — same "pure logic
unit-tested, real I/O smoke-tested" convention as the rest of this
codebase — while both call sites keep computing `main_chroma`/
`bass_chroma` themselves (they already did, for the chord-mode pipeline).
