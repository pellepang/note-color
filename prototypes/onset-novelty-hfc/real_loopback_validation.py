"""Real `--source loopback` validation of this prototype's novelty.py
functions, closing the honest-downside gap README.md's finding 3/downside
3 flagged: the original synthesized-array harness.py was "not tested
against scripts/acoustic_pipeline_test.py's real-loopback suites at all."

This does NOT run the full acoustic_pipeline_test.py suite machinery (that
scores the whole app's pitch/chord/rhythm pipeline output, not raw onset
novelty) -- it reuses that script's playback/mute/synthesis helpers
directly (muted_default_sink, synth_notes, build_timed_audio,
resolve_loopback_device) to drive a REAL PortAudio round trip through
the system's actual PipeWire/PulseAudio monitor, then computes
spectral_flux/hfc_novelty/complex_domain_novelty per hop straight from
pitch_detect.compute_spectrum() over the REAL captured ring buffer --
same "real hardware, real timing jitter, muted/unattended" methodology
acoustic_pipeline_test.py itself documents, applied to novelty-function
comparison instead of full-pipeline note/chord scoring.

Usage:
    .venv/bin/python prototypes/onset-novelty-hfc/real_loopback_validation.py
"""

import os
import statistics
import sys
import time

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from audio_capture import AudioCapture, resolve_loopback_device
from pitch_detect import compute_spectrum
from onset_detect import spectral_flux

from novelty import hfc_novelty, complex_domain_novelty

from acoustic_pipeline_test import muted_default_sink, PLAYBACK_SR

import sounddevice as sd


def build_ground_truth():
    """Same note sequence as harness.py's synthetic test (silence -> A3 ->
    legato E4, no gap -> silence -> A2 (issue #69's low register) ->
    silence -> quiet A4) so this real-loopback run is directly comparable
    to the earlier synthetic-array result, not a different test."""
    from acoustic_pipeline_test import synth_notes

    sr = PLAYBACK_SR
    warmup = 1.5
    gap = 0.6
    chunks = [np.zeros(int(warmup * sr), dtype=np.float32)]
    t = warmup
    truth = []

    a3 = synth_notes([("A", 3)], 0.5, base_amp=0.30)
    e4 = synth_notes([("E", 4)], 0.5, base_amp=0.28)
    legato = np.concatenate([a3, e4])
    chunks.append(legato)
    truth.append({"t": t, "label": "A3 (onset 1)"})
    truth.append({"t": t + 0.5, "label": "E4 legato transition (onset 2)"})
    t += len(legato) / sr
    chunks.append(np.zeros(int(gap * sr), dtype=np.float32))
    t += gap

    a2 = synth_notes([("A", 2)], 0.5, base_amp=0.30)
    chunks.append(a2)
    truth.append({"t": t, "label": "A2 low-register (onset 3)"})
    t += len(a2) / sr
    chunks.append(np.zeros(int(gap * sr), dtype=np.float32))
    t += gap

    a4_quiet = synth_notes([("A", 4)], 0.5, base_amp=0.08)
    chunks.append(a4_quiet)
    truth.append({"t": t, "label": "A4 quiet attack (onset 4)"})
    t += len(a4_quiet) / sr
    chunks.append(np.zeros(int(1.0 * sr), dtype=np.float32))
    t += 1.0

    audio = np.concatenate(chunks)
    return audio, truth, t


def record_novelty(audio, total_runtime_s, warmup_s=1.5):
    """Real round trip: plays `audio` out the (muted) default sink, opens a
    REAL AudioCapture on the loopback monitor at config.SAMPLE_RATE/
    config.BLOCK_SIZE (identical constants main.analysis_loop() uses),
    maintains the exact same ring-buffer-shift pattern as
    main.analysis_loop() (main.py:338), and computes all three novelty
    measures every hop straight off compute_spectrum(ring)."""
    device = resolve_loopback_device()
    cap = AudioCapture(config.SAMPLE_RATE, config.BLOCK_SIZE, device=device)
    cap.start()
    time.sleep(warmup_s)

    ring = np.zeros(config.WINDOW_SIZE, dtype=np.float64)
    prev_spectrum = None
    prev_prev_spectrum = None

    rows = []
    sd.play(audio, PLAYBACK_SR, blocking=False)
    start = time.monotonic()
    try:
        while True:
            now = time.monotonic() - start
            if now > total_runtime_s:
                break
            try:
                block = cap.get_block(timeout=0.2)
            except Exception:
                continue
            ring = np.concatenate([ring[len(block):], block])
            spectrum = compute_spectrum(ring)
            sf = spectral_flux(spectrum, prev_spectrum)
            hfc = hfc_novelty(spectrum, prev_spectrum)
            cdx = complex_domain_novelty(spectrum, prev_spectrum, prev_prev_spectrum)
            rows.append({"t": now, "spectral_flux": sf, "hfc": hfc, "complex_domain": cdx})
            prev_prev_spectrum = prev_spectrum
            prev_spectrum = spectrum
    finally:
        sd.stop()
        cap.stop()
    return rows


def peak_pick(rows, key, k_mad, min_gap_s=0.08):
    values = np.array([r[key] for r in rows])
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med))) or 1e-9
    threshold = med + k_mad * mad
    picks = []
    last_t = -1e9
    for r in rows:
        if r[key] >= threshold and r["t"] - last_t >= min_gap_s:
            picks.append(r["t"])
            last_t = r["t"]
    return picks, threshold


def score(picks, truth_times, tolerance_s=0.20):
    hits, errs = 0, []
    matched_truth = set()
    for p in picks:
        best = None
        for i, gt in enumerate(truth_times):
            if i in matched_truth:
                continue
            d = abs(p - gt)
            if d <= tolerance_s and (best is None or d < best[1]):
                best = (i, d)
        if best is not None:
            matched_truth.add(best[0])
            hits += 1
            errs.append(best[1])
    misses = len(truth_times) - len(matched_truth)
    false_pos = len(picks) - hits
    mean_err_ms = statistics.mean(errs) * 1000 if errs else float("nan")
    return hits, misses, false_pos, mean_err_ms


def main():
    print("Building ground-truth audio (real loopback round trip, muted, unattended)...")
    audio, truth, total_s = build_ground_truth()
    truth_times = [g["t"] for g in truth]

    with muted_default_sink():
        rows = record_novelty(audio, total_runtime_s=total_s + 1.0)

    print(f"\nCaptured {len(rows)} real hops over a {total_s:.2f}s real loopback round trip.")
    print("Ground truth onsets (s):", [f"{t:.3f}" for t in truth_times])
    print()

    print("== Best-of-grid adaptive MAD threshold, per method (comparison methodology, NOT how production picks a threshold) ==")
    header = f"{'method':<18}{'best_k_mad':>10}{'threshold':>12}{'hits':>6}{'misses':>8}{'false_pos':>10}{'mean_err_ms':>14}"
    print(header)
    print("-" * len(header))
    for key in ("spectral_flux", "hfc", "complex_domain"):
        best = None
        for k in (3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40):
            picks, thr = peak_pick(rows, key, k)
            hits, misses, fp, err = score(picks, truth_times)
            score_val = (hits, -fp, -(err if err == err else 1e9))
            if best is None or score_val > best[0]:
                best = (score_val, k, thr, hits, misses, fp, err, picks)
        _, k, thr, hits, misses, fp, err, picks = best
        print(f"{key:<18}{k:>10}{thr:>12.4f}{hits:>6}{misses:>8}{fp:>10}{err:>14.1f}")
        print(f"{'':18}detected onsets (s): {[f'{p:.3f}' for p in picks]}")

    print()
    print("== Fixed threshold = config.ONSET_FLUX_THRESHOLD (0.3), applied identically to all three ==")
    print("   (this is the realistic comparison -- production uses ONE tuned constant, not a")
    print("    per-run adaptive MAD search; all three measures share spectral_flux()'s exact")
    print("    self-relative normalization convention, so 0.3 is meaningfully comparable across them)")
    print(header)
    print("-" * len(header))
    fixed_threshold = config.ONSET_FLUX_THRESHOLD
    for key in ("spectral_flux", "hfc", "complex_domain"):
        picks = []
        last_t = -1e9
        for r in rows:
            if r[key] >= fixed_threshold and r["t"] - last_t >= 0.08:
                picks.append(r["t"])
                last_t = r["t"]
        hits, misses, fp, err = score(picks, truth_times)
        print(f"{key:<18}{'--':>10}{fixed_threshold:>12.4f}{hits:>6}{misses:>8}{fp:>10}{err:>14.1f}")
        print(f"{'':18}detected onsets (s): {[f'{p:.3f}' for p in picks]}")


if __name__ == "__main__":
    main()
