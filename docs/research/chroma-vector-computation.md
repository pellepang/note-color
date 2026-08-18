# Chroma vector computation approach (research for chord mode)

Ticket: [pellepang/note-color#2](https://github.com/pellepang/note-color/issues/2) — "Chroma vector computation approach"

Question: what's the best way to compute a 12-bin chroma vector from note-color's
existing audio pipeline (buffer size, windowing, harmonic weighting), staying
pure-NumPy, and what does it cost in buffer size / latency relative to the
current 2048-sample YIN window?

## Recommendation for note-color

**Reuse the existing 2048-sample analysis window and its FFT; fold STFT bins into
12 chroma bins with a precomputed Gaussian log-frequency weighting matrix
(librosa's `chroma_stft`/`filters.chroma` approach); add explicit harmonic
summation (2nd–4th harmonic) to compensate for that window's poor bass
resolution. Do not adopt CQT or NNLS.**

Concretely:

1. **Window/FFT: reuse, don't duplicate.** `config.WINDOW_SIZE = 2048` at
   `SAMPLE_RATE = 22050` already gets FFT'd every hop for YIN's autocorrelation
   step (`yin.py` computes ACF via FFT, i.e. `rfft` of the padded window — see
   note below on padding). The magnitude spectrum `|rfft(window * hann)|`
   needed for chroma is the same array shape computed from the same windowed
   buffer. Chroma should be computed from that same spectrum, not a second
   independent FFT. This means **chord mode adds no extra latency and no
   extra FFT cost** beyond one small matmul per hop — the chroma filterbank
   is a fixed `(12, 1025)` matrix built once at import time from constants,
   and each hop's chroma vector is `chroma_fb @ magnitude_spectrum`
   (`np.dot`, O(12·1025) ≈ 12k multiply-adds, negligible next to the FFT
   itself).

   One caveat, confirmed by reading `pitch_detect.py`'s
   `_difference_function()`: YIN's FFT-based autocorrelation zero-pads the
   2048-sample window to the next power of two ≥ `2*WINDOW_SIZE` (i.e. 4096)
   before `np.fft.rfft`, to get *linear* rather than circular
   autocorrelation. Chroma should either reuse that same 4096-point
   `rfft(x, 4096)` output directly (simplest — one shared FFT call, and the
   filterbank matrix is just built for `n_fft=4096`'s bin frequencies
   instead of 2048's), or compute its own unpadded `rfft(x, 2048)` if kept
   separate. Reusing the already-computed 4096-point transform is strictly
   better: it's zero extra FFT cost (same call YIN already makes) and, since
   zero-padding oversamples the same underlying spectrum, it gives the
   Gaussian filterbank more/smoother bin samples to weight over — cosmetic
   for accuracy (no new information beyond what the 93 ms window contains)
   but slightly friendlier for the soft-binning matrix in step 2. Either
   way, the filterbank matrix must be built against whatever `n_fft` is
   actually reused, not assumed to be 2048.

2. **Binning/weighting method: Gaussian log-frequency folding, à la
   librosa's `filters.chroma()`.** For each FFT bin frequency `f`, compute
   its position in octave-of-chroma space and assign it to all 12 chroma
   bins with a Gaussian weight peaked at the nearest pitch class and decaying
   with fractional-semitone distance (wrapped mod 12 across octaves), rather
   than Fujishima's original hard/nearest-bin assignment. This is a ~15-line
   NumPy function, computed once (not per-hop) since `sr` and `n_fft` are
   fixed:
   ```
   freqs = rfftfreq(n_fft, 1/sr)                      # bin center frequencies
   octs  = 12 * log2(freqs / f_ref)                    # bin position in "chroma octaves"
   dist  = wrap_to_[-6, 6]( octs[None,:] - arange(12)[:,None] )  # signed distance to each of 12 classes, mod 12
   wts   = exp(-0.5 * (dist / bin_width)**2)            # Gaussian weight per (chroma, bin)
   wts  /= wts.sum(axis=0, keepdims=True)               # normalize columns (energy-preserving)
   ```
   This is strictly better than nearest-bin assignment at no extra cost, and
   it's exactly what `librosa.filters.chroma()` builds internally (see
   Survey below) — no need to depend on librosa, just port the ~15-line
   matrix construction into note-color once, as a pure-NumPy helper.

3. **Harmonic weighting: needed, and specifically needed to fix a real
   resolution problem in the bottom of the pitch range, not just as a nice-to-have.**
   At `SAMPLE_RATE=22050`, `WINDOW_SIZE=2048`, FFT bin spacing is
   `22050/2048 ≈ 10.77 Hz`. Semitone spacing at `FMIN=65 Hz` (≈C2) is only
   `65 × (2^{1/12}−1) ≈ 3.9 Hz` — **smaller than one FFT bin**. A 2048-sample
   window at 22050 Hz *cannot* resolve adjacent semitones near C2 by
   fundamental frequency alone; two neighboring low notes' fundamentals can
   land in or smear across the same bin. (By contrast, at 1000 Hz, semitone
   spacing is ≈59.5 Hz, well above the 10.77 Hz bin width — resolution is
   fine for the upper half of the range.) Zero-padding the FFT does not fix
   this: it interpolates the same information, it doesn't add frequency
   resolution (resolution is set by the ~93 ms window duration, not FFT
   size).

   The standard fix (used by Fujishima's own later "Enhanced PCP" work and
   by HPCP/Gómez-style chroma) is **harmonic summation**: fold energy from a
   note's 2nd, 3rd, and (optionally) 4th harmonic into its fundamental's
   pitch class too, with decreasing weight per harmonic (e.g. weights
   `1, 0.5, 0.33` or a geometric falloff). A note at C2 (65 Hz) is
   under-resolved at its fundamental, but its 2nd harmonic (130 Hz, C3) and
   3rd harmonic (196 Hz, ≈G3) sit at frequencies with much better relative
   bin resolution, and summing them back into C2's chroma bin recovers
   pitch-class discrimination the fundamental alone can't provide. This is
   cheap: it's just adding a few more weighted matmuls (or extending the
   filterbank matrix to also route energy at `2f`, `3f`, `4f` into chroma
   class `f`'s bin), no window-size change required.

4. **Latency impact: none, if the 2048-sample/93 ms window and its FFT are
   reused as in (1).** Chroma updates at the same ~23 ms hop
   (`BLOCK_SIZE=512`) and ~93 ms look-back window as pitch detection does
   today — chord mode would feel exactly as responsive as monophonic mode.

   If, after building this, low-register chord recognition (chords built on
   bass notes near C2–C3) still isn't accurate enough even with harmonic
   summation, the fallback is a **separate, larger window used only for
   chroma** (e.g. 4096 samples ≈ 186 ms, still hopped every 512 samples,
   independent of YIN's 2048-sample window) — this roughly doubles the
   look-back latency for chord recognition specifically (chords would lag
   detected pitch changes by up to ~186 ms instead of ~93 ms) while leaving
   monophonic pitch-detection latency untouched. This should be a last
   resort, not the starting design — try (1)+(3) first.

5. **What NOT to do:** don't implement a constant-Q transform (chroma_cqt)
   or an NNLS-based approach (NNLS Chroma). Both give better bass resolution
   in principle, but both cost real implementation and runtime complexity
   this project's constraints don't justify — see Survey below for why.

## Survey of established techniques considered

### Fujishima (1999) — original PCP / real-time chord recognition

Fujishima, T. "Realtime Chord Recognition of Musical Sound: a System Using
Common Lisp Music," *Proc. ICMC 1999*, pp. 464–467.
([CiNii record](https://cir.nii.ac.jp/crid/1572261549989697408?lang=en);
[Semantic Scholar](https://www.semanticscholar.org/paper/Realtime-Chord-Recognition-of-Musical-Sound%3A-a-Lisp-Fujishima/c9a84645f0e9f3498bf8e4ebfdc1150a86faf78c))
— full PDF paywalled/not freely hosted; description here is corroborated
across multiple secondary sources that summarize it consistently, including
Lee, K. "Automatic Chord Recognition from Audio Using Enhanced Pitch Class
Profile," *Proc. ICMC 2006*
([full text](https://silo.tips/download/automatic-chord-recognition-from-audio-using-enhanced-pitch-class-profile)),
which is itself a direct extension of Fujishima's method and states: "Fujishima
developed a realtime chord recognition system, where he derived a
12-dimensional pitch class profile from the DFT of the audio signal, and
performed pattern matching using the binary chord type templates."

Fujishima's PCP is the original chroma vector: take the DFT of a frame, map
each frequency bin directly to one of 12 pitch classes via
`p = round(12·log2(f/f_ref)) mod 12` (nearest-bin, hard assignment, not
Gaussian-weighted), sum squared magnitude per class, then pattern-match the
resulting 12-vector against binary chord templates (e.g. major triad =
1 at root/major-third/fifth, 0 elsewhere). This is the ancestor of every
technique below. Its known weaknesses, cited by essentially all follow-up
work (Lee 2006, Gómez's HPCP, etc.): hard nearest-bin assignment is brittle
to tuning drift and to the uneven bin-to-semitone resolution problem
described in the recommendation above (bins are linearly spaced, semitones
are logarithmically spaced, so low notes are systematically under-resolved
relative to high notes in a fixed-size DFT). note-color's recommended
approach keeps Fujishima's core idea (bin → pitch class folding from a plain
FFT) but replaces hard assignment with Gaussian soft-weighting and adds
harmonic summation to address exactly this known weakness.

### NNLS Chroma (Mauch & Dixon, 2010)

Mauch, M. & Dixon, S. "Approximate Note Transcription for the Improved
Identification of Difficult Chords," *Proc. ISMIR 2010*.
([Semantic Scholar](https://www.semanticscholar.org/paper/Approximate-Note-Transcription-for-the-Improved-of-Mauch-Dixon/d6d65865b60877c2a49c9d80b6a9194033a26381);
implementation: [c4dm/nnls-chroma](https://github.com/c4dm/nnls-chroma) Vamp
plugin, described at
[isophonics.net/nnls-chroma](https://isophonics.net/nnls-chroma) and
[vamp-plugins.org/rdf/plugins/nnls-chroma](https://vamp-plugins.org/rdf/plugins/nnls-chroma))

Pipeline, per the isophonics/vamp-plugins descriptions: (1) transform to a
**log-frequency spectrum with 3 bins per semitone** (not a plain linear
STFT); (2) **tuning correction**, so the center bin of each semitone-triplet
lines up with the true pitch even if the recording is detuned from 440 Hz;
(3) **spectral whitening** via running-mean subtraction and running-std
division over the log-frequency spectrum; (4) fit this whitened spectrum
against a **dictionary of harmonic note profiles** (one column per
semitone/note, geometrically decaying harmonic magnitudes) using
**non-negative least squares (NNLS)** — i.e. solve for the non-negative
combination of note-templates that best reconstructs the observed spectrum,
which is a much better-conditioned way to disentangle overlapping harmonics
than simple bin summation; (5) the resulting semitone-spaced "note
activation" vector is multiplied by a chroma profile and folded to 12 bins.

Why not chosen for note-color: this is explicitly built to solve the "difficult
chords" problem — telling apart chords whose pitch classes overlap heavily in
raw harmonic content (e.g. distinguishing a chord from its relative minor, or
resolving dense/jazz voicings) by deconvolving harmonics via a per-frame NNLS
solve. That's real algorithmic complexity (an iterative NNLS solver run every
hop, plus building/maintaining the harmonic dictionary and the log-frequency
resampling step) for a benefit note-color's stated scope (up to 6
simultaneous notes, real-time visual feedback, not offline MIR-grade chord
transcription) doesn't need. It's pure-NumPy-implementable in principle
(`scipy.optimize.nnls` is the standard solver, but the project avoids scipy
too as far as this ticket's constraint goes — a NumPy-only iterative NNLS
would need to be hand-rolled), but it's a meaningfully bigger engineering
lift for accuracy gains aimed at a harder problem than chord-mode currently
has. Worth revisiting only if the simple harmonic-summation approach (§3 of
the recommendation) proves inadequate in practice on real chord recordings.

### librosa `chroma_stft` and `chroma_cqt`

Source read directly:
[`librosa/feature/spectral.py`](https://github.com/librosa/librosa/blob/main/librosa/feature/spectral.py)
(the `chroma_stft`/`chroma_cqt` functions) and
[`librosa/filters.py`](https://github.com/librosa/librosa/blob/main/librosa/filters.py)
(the `chroma()` and `cq_to_chroma()` filterbank builders they call).

`chroma_stft`: computes a standard STFT (`n_fft=2048` default — same order
of magnitude as note-color's existing window — `hop_length=512`, Hann
window; note-color's own `BLOCK_SIZE=512`/`WINDOW_SIZE=2048` line up with
librosa's defaults almost exactly), builds a `(12, n_fft/2+1)` filterbank via
`filters.chroma()`, and contracts spectrum → chroma with
`np.einsum("cf,...ft->...ct", chromafb, S)`, then column-normalizes
(`norm=inf` by default — max-normalize each frame).

`filters.chroma()` (the part actually worth porting): converts bin
frequencies to octave-of-chroma position via `hz_to_octs`, computes each
bin's signed distance to each of the 12 chroma centers wrapped mod 12
(`np.remainder(D + n_chroma/2 + 10*n_chroma, n_chroma) - n_chroma/2`), and
applies a Gaussian weight `exp(-0.5·(2·D/bin_width)^2)` per (chroma, bin)
pair — i.e. exactly the Gaussian soft-binning described in the
recommendation above. It optionally also applies a second, broader Gaussian
window in log-frequency (`octwidth`/`ctroct` params) to down-weight octaves
far from a target "center octave" — not needed for note-color since we want
all octaves in `FMIN..FMAX` treated equally, not weighted toward one octave.
Final matrix is column-normalized (L2 or L1 depending on `norm`).

`chroma_cqt`: computes a constant-Q transform first (`cqt()`, log-spaced
bins, typically several dozen bins per octave for adequate resolution
everywhere including the bass), then folds those CQT bins to 12 chroma via
`filters.cq_to_chroma()`, same einsum-contraction pattern. CQT bins are
log-spaced by construction, so every octave — including the bass register
where a linear-FFT approach is under-resolved — gets equal semitone
resolution.

Why `chroma_stft`'s approach (adapted, not the CQT one) was chosen for
note-color: `chroma_stft`'s Gaussian filterbank is a direct, cheap adaptation
of the FFT note-color already computes for YIN — a fixed matrix, one matmul
per hop, no new window or transform. `chroma_cqt` is the "correct" answer to
the bass-resolution problem in principle (that's exactly why CQT exists —
log-spaced bins natively match musical pitch spacing at every register), but
implementing a constant-Q transform from scratch in pure NumPy for a
streaming/real-time pipeline is substantially more work than the STFT
route: naive CQT implementations use per-bin variable-length windows (long
windows for low notes, short for high notes), which doesn't map cleanly onto
note-color's fixed-hop block-processing model, and efficient recursive/matrix
CQT algorithms (e.g. the Brown & Puckette approach, or librosa's own
FFT-based CQT approximation) add real implementation complexity for a
hobbyist real-time visualizer. The harmonic-summation fix in the
recommendation is a lighter-weight way to claw back most of CQT's
bass-register benefit within the existing STFT/window architecture, without
taking on a second transform type.

## Summary table

| Approach | Bass resolution | Extra latency | Implementation cost | Verdict |
|---|---|---|---|---|
| Reuse 2048 FFT + Gaussian bin folding + harmonic summation (recommended) | Fixed via harmonic summation, not fundamental resolution | None (reuses existing window/FFT) | Low — one filterbank matrix + a few extra matmuls | **Adopt** |
| Fujishima original (hard nearest-bin, no harmonic weighting) | Poor at low pitches, tuning-brittle | None | Very low | Superseded by the above at near-zero extra cost |
| NNLS Chroma (Mauch & Dixon) | Best — deconvolves overlapping harmonics | Depends on log-freq resample window | High — per-frame NNLS solve, hand-rolled without scipy | Reference only; revisit if simple approach proves insufficient |
| librosa `chroma_cqt` (CQT-based) | Best — log-spaced bins native to pitch | Variable per-register in a naive implementation | High — new transform type, awkward fit to fixed-hop streaming | Reference only |
