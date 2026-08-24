"""Rhythm/duration-detection accuracy suite (issue #77) -- measures how
well this pipeline's rhythm layer (live/causal duration_tracker.py +
tempo_tracker.py, and the R-key non-causal rhythm_reanalysis.recompute())
actually recovers ground-truth note *rhythm* (onset placement, duration
class, tempo) from real audio, over a real `--source loopback` round trip
(muted, unattended -- same convention as scripts/acoustic_pipeline_test.py,
whose synth/playback/loopback-mute helpers this script imports and reuses
rather than reimplementing).

This is NOT a pitch/chord-name accuracy test (see acoustic_pipeline_test.py
for that) -- ground truth here is built with known note values (quarter,
eighth, sixteenth, dotted, triplet, ...) at known tempos, and the score is
purely "did the pipeline report the right note-value / onset timing /
tempo", using the app's own duration_class_for_beats() to compute each
ground-truth note's expected class so scoring is apples-to-apples with
what the app can possibly report (a duration the app itself would round
differently than a human transcriber is not a scoring bug).

Two parallel measurements per phrase, both against REAL pipeline internals
(never a reimplementation of the rhythm logic):

- causal ("live"): reconstructed straight from the real per-hop
  RenderItem stream a live terminal view would see (mono duration_hops on
  the hop AFTER a note's last hop, keyed to the PREVIOUS hop's
  pitch_class/octave -- main.py's own convention; chord duration_hops
  lives inside each hop's note_stack entries) -- same reconstruction
  acoustic_pipeline_test.py's analyze_rhythm() already uses, generalized
  here to a real melody instead of one repeated pitch.
- non-causal ("R-key"): after playback, this script snapshots the SAME
  `main.SessionState.reanalysis_buffer` the live `tab` view's R key reads
  (see main.py/rhythm_reanalysis.py) and calls
  `rhythm_reanalysis.recompute()` directly -- it's a pure function, so no
  terminal/keypress simulation is needed, and this is the exact code path
  R would run.

Difficulty ladder (0 = trivial sanity check, 11 = deliberately impossible):
  0  silence only (false-positive check)
  1  single sustained whole notes, mono, ~60 BPM
  2  steady quarter-note melody, mono, ~80 BPM
  3  quarter+eighth mix with rests, mono, ~100 BPM
  4  eighth+sixteenth with syncopation, mono, ~100 BPM
  5  varied note values + real phrase-gap silences, mono, ~110 BPM
  6  held chords only (whole/half notes), no melody, slow, chord-mode-only
  7  chord progression changing in quarter notes, no melody
  8  chord progression (whole-note pads) + eighth-note melody, ~100 BPM
  9  chords + 16th-note melody with syncopation, ~130 BPM
  10 dotted/triplet/32nd mix, mono + light pad, ~168 BPM
  11 dense 16th/32nd runs over fast-moving 7th chords, ~200 BPM ("too hard")

Usage:
    .venv/bin/python scripts/rhythm_accuracy_test.py                  # all tiers
    .venv/bin/python scripts/rhythm_accuracy_test.py --tiers 0,1,2    # subset
    .venv/bin/python scripts/rhythm_accuracy_test.py --report OUTDIR # re-score saved raw data, no audio

Output contract (what a report-generating script/fork should read):

Each run writes `--outdir` (default `rhythm_test_results/<timestamp>/`,
gitignored under the same convention as acoustic_test_results/):
  - `<tier_id>_<name>_raw.json` -- {"ground_truth": [...], "log": [...],
    "hop_records": [...], "bpm": float, "beats_per_bar": int,
    "hop_seconds": float, "r0": int} -- enough to re-score without
    re-running audio (see --report).
  - `<tier_id>_<name>_scored.json` -- the TierResult shape (see
    score_tier()'s docstring) with causal/non-causal accuracy numbers.
  - `all_results.json` -- {"tiers": [TierResult, ...]}, the single file
    an HTML report generator should actually read.

TierResult shape (one dict per tier, see score_tier()):
{
  "tier": int, "name": str, "description": str, "bpm": float,
  "n_ground_truth": int, "n_mono": int, "n_chord": int,
  "causal": {
    "onset_recall": float|None, "onset_precision": float|None,
    "class_accuracy": float|None,        # of matched notes only
    "n_matched": int, "n_missed": int, "n_phantom": int,
    "tempo_true_bpm": float, "tempo_median_estimate": float|None,
    "tempo_error_pct": float|None,
    "matched": [ {pitch_class, octave, kind, expected_class, detected_class,
                  expected_beats, detected_beats, correct, gt_time,
                  detected_time} ... ],
    "missed": [ {pitch_class, octave, kind, expected_class, gt_time} ... ],
    "phantom": [ {pitch_class, octave, kind, detected_class, t} ... ],
  },
  "noncausal": { same shape as "causal", plus "tempo_estimate" (single
    value from recompute(), not a converged-median like causal's) },
}
"""

import argparse
import contextlib
import json
import os
import queue
import statistics
import sys
import time
from datetime import datetime

import numpy as np
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import acoustic_pipeline_test as apt  # reuses synth_notes/voice_chord/_silence/_overlay/_peak_cap/muted_default_sink
import config
import main
import rhythm_reanalysis
from config_store import store
from duration_tracker import duration_class_for_beats

PLAYBACK_SR = apt.PLAYBACK_SR
NOTE_INDEX = apt.NOTE_INDEX
HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE

# Generous vs. acoustic_pipeline_test.py's own LATENCY_SEARCH_SLOP_S (0.15s)
# -- a duration-class *finalize* event only fires once magnitude decays
# below DURATION_DECAY_RATIO, which lags the note's true end by however
# long that decay takes (silence-gate + fade + smoothing), not just
# detection latency. Widened empirically loose on purpose: this suite's
# job is measuring how far off the pipeline lands, not enforcing a
# pre-guessed tight window that would just turn "detected late" into
# "counted as missed" instead of scoring it as an inaccurate hit.
CAUSAL_MATCH_TOLERANCE_BEFORE_S = 0.20
CAUSAL_MATCH_TOLERANCE_AFTER_S = 0.70
NONCAUSAL_MATCH_TOLERANCE_S = 0.30
REANALYSIS_WINDOW_OVERRIDE_S = 150.0  # comfortably longer than any single tier's own duration


@contextlib.contextmanager
def temporary_reanalysis_window(seconds):
    """Widens config.toml's `rhythm_reanalysis_window_seconds` for the
    duration of the run so `main.ReanalysisBuffer` (bounded by that same
    preference, hot-reloaded every append -- see main.py) never evicts a
    tier's own early hops before this script gets to snapshot them, then
    restores whatever was there before -- never leaves the user's real
    config.toml file changed. `store` is the module-level ConfigStore
    singleton every other module already imports; `set_preference()`
    persists to disk by design (issue #43's settings screen depends on
    that), so restoring afterward matters here in a way a purely in-memory
    override wouldn't need to worry about."""
    had_key = "rhythm_reanalysis_window_seconds" in store._data.get("preferences", {})
    original = store.preference("rhythm_reanalysis_window_seconds", config.RHYTHM_REANALYSIS_WINDOW_SECONDS)
    store.set_preference("rhythm_reanalysis_window_seconds", seconds)
    try:
        yield
    finally:
        if had_key:
            store.set_preference("rhythm_reanalysis_window_seconds", original)
        else:
            store._data.get("preferences", {}).pop("rhythm_reanalysis_window_seconds", None)
            store._write()


# --------------------------------------------------------------------------
# Phrase construction -- reuses apt.synth_notes()/apt.voice_chord() for
# actual audio synthesis (harmonic-weighted additive tones, same as every
# other acoustic suite), but times/labels events by (start_beat,
# duration_beats) at a given BPM instead of apt's flat gap-separated
# sequencing, and records ground truth in beats so
# duration_class_for_beats() -- the app's own snapping function -- can
# compute each note's expected class directly.
# --------------------------------------------------------------------------

def mono(note, start_beat, duration_beats, amp=0.30):
    name, octave = note
    return {"notes": [(name, octave)], "start_beat": start_beat, "duration_beats": duration_beats,
            "kind": "mono", "amp": amp}


def chord_event(notes, start_beat, duration_beats, amp=0.22):
    return {"notes": notes, "start_beat": start_beat, "duration_beats": duration_beats,
            "kind": "chord", "amp": amp}


def build_phrase(events, bpm, lead_s=0.7, trail_s=1.3, min_duration_s=0.0, articulation=0.90):
    """events: list of mono()/chord_event() dicts. Returns (audio float32,
    ground_truth list, total_duration_s). `articulation` shortens each
    note's actual sounding time to a fraction of its notated duration
    (natural note-off gap, same convention as acoustic_pipeline_test.py's
    REALISTIC_MELODY 0.85 factor) -- ground truth still records the full
    notated duration_beats/expected_class, since that's the rhythmic value
    a transcriber (or this app) is actually being asked to recover, not
    the articulation gap."""
    beat_s = 60.0 / bpm
    end_beat = max((e["start_beat"] + e["duration_beats"] for e in events), default=0.0)
    total = max(lead_s + end_beat * beat_s + trail_s, min_duration_s)
    audio = apt._silence(total)
    ground_truth = []
    for e in events:
        note_dur_s = e["duration_beats"] * beat_s * articulation
        t0 = lead_s + e["start_beat"] * beat_s
        if note_dur_s > 0:
            apt._overlay(audio, apt.synth_notes(e["notes"], note_dur_s,
                                                 fade_s=min(0.015, note_dur_s / 4), base_amp=e["amp"]), t0)
        for name, octave in e["notes"]:
            ground_truth.append({
                "pitch_class": NOTE_INDEX[name], "octave": octave,
                "start_time": t0, "end_time": t0 + e["duration_beats"] * beat_s,
                # The causal decay-based finalize mechanism fires close to
                # when the note actually stops SOUNDING, not its full
                # notated value -- articulation<1.0 means real audio ends
                # `note_dur_s` after onset, not the nominal duration_beats
                # later. Matching causal events against the notated
                # end_time instead of this would misreport every
                # articulated note as "detected late" by the articulation
                # gap itself (up to ~10% of the note's own length), which
                # is a scoring-tolerance bug, not a pipeline finding.
                "audible_end_time": t0 + note_dur_s,
                "duration_beats": e["duration_beats"],
                "expected_class": duration_class_for_beats(e["duration_beats"]),
                "kind": e["kind"],
            })
    return apt._peak_cap(audio), ground_truth, total


# --------------------------------------------------------------------------
# The difficulty ladder
# --------------------------------------------------------------------------

def tier_0_silence():
    return [], 90.0, {"min_duration_s": 8.0}


def tier_1_whole_notes():
    events = [mono(("C", 4), 0, 4), mono(("F", 4), 5, 4), mono(("A", 3), 10, 4)]
    return events, 60.0, {}


def tier_2_quarter_melody():
    seq = [("C", 4), ("D", 4), ("E", 4), ("F", 4), ("G", 4), None, ("E", 4), ("C", 4),
           ("D", 4), ("F", 4), ("A", 4), ("G", 4), None, ("E", 4), ("D", 4), ("C", 4)]
    events = [mono(n, i, 1) for i, n in enumerate(seq) if n is not None]
    return events, 80.0, {}


def tier_3_quarter_eighth_mix():
    seq = [(("C", 4), 0, 1), (("E", 4), 1, 0.5), (("G", 4), 1.5, 0.5), (("C", 5), 2, 1),
           (("B", 4), 4, 0.5), (("A", 4), 4.5, 0.5), (("G", 4), 5, 1), (("E", 4), 6, 1),
           (("D", 4), 7, 0.5), (("C", 4), 7.5, 0.5)]
    return [mono(n, s, d) for n, s, d in seq], 100.0, {}


def tier_4_eighth_sixteenth_syncopation():
    seq = [(("C", 4), 0, 0.25), (("D", 4), 0.25, 0.25), (("E", 4), 0.5, 0.5),
           (("G", 4), 1.25, 0.25), (("A", 4), 1.5, 0.5), (("G", 4), 2, 0.25), (("E", 4), 2.25, 0.25),
           (("C", 4), 2.5, 0.5), (("D", 4), 3.5, 0.25), (("E", 4), 3.75, 0.25),
           (("F", 4), 4.25, 0.75), (("E", 4), 5, 0.25), (("D", 4), 5.25, 0.25), (("C", 4), 5.5, 1)]
    return [mono(n, s, d) for n, s, d in seq], 100.0, {}


def tier_5_phrasing_with_rests():
    seq = [(("E", 4), 0, 1), (("G", 4), 1, 0.5), (("A", 4), 1.5, 0.5), (("G", 4), 2, 2),
           (("C", 5), 6, 1), (("B", 4), 7, 0.5), (("A", 4), 7.5, 0.5), (("G", 4), 8, 1), (("E", 4), 9, 1),
           (("F", 4), 12, 0.75), (("G", 4), 12.75, 0.25), (("A", 4), 13, 2)]
    return [mono(n, s, d) for n, s, d in seq], 110.0, {}


def tier_6_held_chords_only():
    n1, _, _ = apt.voice_chord("C", 4, "maj")
    n2, _, _ = apt.voice_chord("A", 3, "min")
    n3, _, _ = apt.voice_chord("F", 3, "maj7")
    events = [chord_event(n1, 0, 4), chord_event(n2, 5, 2), chord_event(n3, 8, 2)]
    return events, 70.0, {}


def tier_7_chord_progression_quarters():
    prog = [("C", 4, "maj"), ("A", 3, "min"), ("F", 3, "maj"), ("G", 3, "maj")] * 2
    events = []
    for i, (root, octave, quality) in enumerate(prog):
        notes, _, _ = apt.voice_chord(root, octave, quality)
        events.append(chord_event(notes, i, 1))
    return events, 90.0, {}


def tier_8_progression_plus_eighth_melody():
    chords = [("C", 4, "maj"), ("A", 3, "min"), ("F", 3, "maj"), ("G", 3, "maj")]
    events = []
    for i, (root, octave, quality) in enumerate(chords):
        notes, _, _ = apt.voice_chord(root, octave, quality)
        events.append(chord_event(notes, i * 4, 4, amp=0.20))
    melody = [(("E", 5), 0, 1), (("G", 5), 1.5, 0.5), (("E", 5), 2, 1.5),
              (("A", 4), 4, 0.75), (("C", 5), 4.75, 0.25), (("A", 4), 5, 2),
              (("F", 4), 8, 1), (("A", 4), 9.5, 0.5), (("C", 5), 10, 1.5),
              (("G", 4), 12, 1), (("B", 4), 13.5, 0.5), (("D", 5), 14, 2)]
    events += [mono(n, s, d, amp=0.28) for n, s, d in melody]
    return events, 100.0, {}


def tier_9_chords_plus_sixteenth_syncopation():
    chords = [("D", 4, "min7"), ("G", 3, "dom7"), ("C", 4, "maj7"), ("A", 3, "min7")]
    events = []
    for i, (root, octave, quality) in enumerate(chords):
        notes, _, _ = apt.voice_chord(root, octave, quality)
        events.append(chord_event(notes, i * 4, 4, amp=0.18))
    melody = [(("A", 4), 0, 0.25), (("C", 5), 0.25, 0.25), (("D", 5), 0.5, 0.5),
              (("E", 5), 1.25, 0.25), (("F", 5), 1.5, 0.25), (("D", 5), 1.75, 0.25), (("C", 5), 2, 0.75),
              (("B", 4), 3.5, 0.25), (("A", 4), 3.75, 0.25),
              (("G", 4), 4, 0.5), (("A", 4), 4.5, 0.25), (("B", 4), 4.75, 0.25), (("D", 5), 5, 1),
              (("C", 5), 6.5, 0.25), (("B", 4), 6.75, 0.25), (("A", 4), 7, 1),
              (("E", 5), 8, 0.25), (("D", 5), 8.25, 0.25), (("C", 5), 8.5, 0.5),
              (("A", 4), 9.25, 0.25), (("G", 4), 9.5, 0.75),
              (("F", 4), 10.5, 0.25), (("G", 4), 10.75, 0.25), (("A", 4), 11, 1),
              (("G", 4), 12.5, 0.25), (("F", 4), 12.75, 0.25), (("E", 4), 13, 1),
              (("D", 4), 14.5, 0.25), (("C", 4), 14.75, 0.25), (("D", 4), 15, 1)]
    events += [mono(n, s, d, amp=0.26) for n, s, d in melody]
    return events, 130.0, {}


def tier_10_dotted_triplet_thirtysecond():
    third = 1.0 / 3.0
    seq = [(("C", 4), 0, 0.75), (("D", 4), 0.75, 0.25), (("E", 4), 1, 1.5), (("F", 4), 2.5, 0.5),
           (("G", 4), 3, third), (("A", 4), 3 + third, third), (("B", 4), 3 + 2 * third, third),
           (("C", 5), 4, 3.0), (("A", 4), 7, 0.5), (("G", 4), 7.5, 0.25), (("F", 4), 7.75, 0.25),
           (("E", 4), 8, 0.375), (("D", 4), 8.375, 0.125), (("C", 4), 8.5, 1.5)]
    events = [mono(n, s, d) for n, s, d in seq]
    pad, _, _ = apt.voice_chord("C", 3, "maj")
    events.append(chord_event(pad, 0, 10, amp=0.10))
    return events, 168.0, {}


def tier_11_impossible():
    roots = [("C", 4), ("D", 4), ("E", 4), ("F", 4), ("G", 4), ("A", 4), ("B", 4), ("C", 5)]
    step = 0.125  # thirty-second note at this tempo
    melody = []
    t = 0.0
    for name, octave in roots * 8:
        melody.append(((name, octave), t, step))
        t += step
    events = [mono(n, s, d, amp=0.25) for n, s, d in melody]
    chord_defs = [("C", 3, "dom7"), ("D", 3, "min7"), ("E", 3, "half-dim7"), ("F", 3, "maj7"),
                  ("G", 3, "dom7"), ("A", 3, "min7"), ("B", 3, "dim7"), ("C", 4, "dom7")]
    for i, (root, octave, quality) in enumerate(chord_defs):
        notes, _, _ = apt.voice_chord(root, octave, quality)
        events.append(chord_event(notes, i, 1, amp=0.14))
    return events, 200.0, {}


TIERS = [
    (0, "silence", "Silence only -- false-positive check", tier_0_silence),
    (1, "whole_notes", "Single sustained whole notes, mono, ~60 BPM", tier_1_whole_notes),
    (2, "quarter_melody", "Steady quarter-note melody, mono, ~80 BPM", tier_2_quarter_melody),
    (3, "quarter_eighth_mix", "Quarter+eighth mix with rests, mono, ~100 BPM", tier_3_quarter_eighth_mix),
    (4, "eighth_sixteenth_syncopation", "Eighth+sixteenth with syncopation, mono, ~100 BPM",
     tier_4_eighth_sixteenth_syncopation),
    (5, "phrasing_with_rests", "Varied note values + phrase-gap silences, mono, ~110 BPM",
     tier_5_phrasing_with_rests),
    (6, "held_chords_only", "Held chords only (whole/half notes), no melody, slow", tier_6_held_chords_only),
    (7, "chord_progression_quarters", "Chord progression changing in quarter notes, no melody",
     tier_7_chord_progression_quarters),
    (8, "progression_plus_eighth_melody", "Chord progression (whole-note pads) + eighth-note melody, ~100 BPM",
     tier_8_progression_plus_eighth_melody),
    (9, "chords_plus_sixteenth_syncopation", "Chords + sixteenth-note melody with syncopation, ~130 BPM",
     tier_9_chords_plus_sixteenth_syncopation),
    (10, "dotted_triplet_thirtysecond", "Dotted/triplet/32nd mix, mono + light pad, ~168 BPM",
     tier_10_dotted_triplet_thirtysecond),
    (11, "impossible", "Dense 16th/32nd runs over fast-moving 7th chords, ~200 BPM -- deliberately too hard",
     tier_11_impossible),
]


# --------------------------------------------------------------------------
# Playback + dual (causal + non-causal) capture
# --------------------------------------------------------------------------

def record_phrase(session, audio, total_runtime_s, warmup_s=1.0):
    """Plays `audio` while draining session.result_queue (causal log, same
    shape as acoustic_pipeline_test.record_session()'s), and separately
    anchors + snapshots session.reanalysis_buffer around the same window
    so this phrase's HopRecords can be fed straight into
    rhythm_reanalysis.recompute() afterward -- the exact function/data
    shape the live R key uses, just driven directly instead of via a
    simulated keypress. Returns (log, hop_records, r0) where r0 is the
    hop_index observed immediately before playback started -- callers
    convert a HopRecord's real hop_index to phrase-relative time via
    `(hop_index - r0) * HOP_SECONDS`, matching log entries' own `t` clock."""
    time.sleep(warmup_s)
    anchor = session.reanalysis_buffer.snapshot()
    r0 = anchor[-1].hop_index if anchor else -1

    sd.play(audio, PLAYBACK_SR, blocking=False)
    start = time.monotonic()
    log = []
    while True:
        now = time.monotonic() - start
        if now > total_runtime_s:
            break
        try:
            item = session.result_queue.get_nowait()
        except queue.Empty:
            time.sleep(apt.POLL_INTERVAL_S)
            continue
        log.append({
            "t": round(now, 4), "pitch_class": item.pitch_class, "octave": item.octave,
            "is_onset": item.is_onset, "duration_hops": item.duration_hops, "bpm_estimate": item.bpm_estimate,
            "note_stack": [{"pitch_class": e["pitch_class"], "octave": e["octave"],
                             "duration_hops": e.get("duration_hops")} for e in item.note_stack],
        })
    sd.stop()
    time.sleep(0.3)  # let the last couple of hops land before snapshotting

    max_hop_index = r0 + int((total_runtime_s + 1.0) / HOP_SECONDS) + 5
    hop_records = [r for r in session.reanalysis_buffer.snapshot() if r0 < r.hop_index <= max_hop_index]
    return log, hop_records, r0


# --------------------------------------------------------------------------
# Scoring -- causal (live) event reconstruction, mirroring
# acoustic_pipeline_test.analyze_rhythm()'s convention but generalized to
# more than one repeated pitch, plus non-causal scoring via
# rhythm_reanalysis.recompute() directly.
# --------------------------------------------------------------------------

def _causal_events(log):
    events = []
    for i in range(1, len(log)):
        h = log[i]
        if h["duration_hops"] is not None:
            prev = log[i - 1]
            if prev["pitch_class"] is not None:
                beats = (h["duration_hops"] * HOP_SECONDS * h["bpm_estimate"] / 60.0) if h["bpm_estimate"] else None
                events.append({"pitch_class": prev["pitch_class"], "octave": prev["octave"], "t": h["t"],
                                "beats": beats, "detected_class": duration_class_for_beats(beats), "kind": "mono"})
        for n in h["note_stack"]:
            if n.get("duration_hops") is not None:
                beats = (n["duration_hops"] * HOP_SECONDS * h["bpm_estimate"] / 60.0) if h["bpm_estimate"] else None
                events.append({"pitch_class": n["pitch_class"], "octave": n["octave"], "t": h["t"],
                                "beats": beats, "detected_class": duration_class_for_beats(beats), "kind": "chord"})
    return events


def _match(ground_truth, events, ref_key, event_time_key, tol_before, tol_after):
    """Greedy nearest-time matching per (pitch_class, octave), one event
    per ground-truth note. Returns (matched, missed, phantom)."""
    by_key = {}
    for idx, ev in enumerate(events):
        by_key.setdefault((ev["pitch_class"], ev["octave"]), []).append(idx)
    used = set()
    matched, missed = [], []
    for gt in ground_truth:
        key = (gt["pitch_class"], gt["octave"])
        ref_time = gt[ref_key]
        candidates = [idx for idx in by_key.get(key, [])
                      if idx not in used and ref_time - tol_before <= events[idx][event_time_key] <= ref_time + tol_after]
        if candidates:
            best = min(candidates, key=lambda idx: abs(events[idx][event_time_key] - ref_time))
            used.add(best)
            matched.append((gt, events[best]))
        else:
            missed.append(gt)
    phantom = [events[idx] for idx in range(len(events)) if idx not in used]
    return matched, missed, phantom


def _summarize(matched, missed, phantom, true_bpm, tempo_estimates):
    n_matched, n_missed, n_phantom = len(matched), len(missed), len(phantom)
    n_truth = n_matched + n_missed
    correct = sum(1 for gt, ev in matched if ev["detected_class"] == gt["expected_class"])
    estimates = [e for e in tempo_estimates if e is not None]
    median_bpm = statistics.median(estimates) if estimates else None
    error_pct = round(abs(median_bpm - true_bpm) / true_bpm * 100, 1) if median_bpm else None
    return {
        "onset_recall": round(n_matched / n_truth, 3) if n_truth else None,
        "onset_precision": round(n_matched / (n_matched + n_phantom), 3) if (n_matched + n_phantom) else None,
        "class_accuracy": round(correct / n_matched, 3) if n_matched else None,
        "n_matched": n_matched, "n_missed": n_missed, "n_phantom": n_phantom,
        "tempo_true_bpm": true_bpm, "tempo_median_estimate": median_bpm, "tempo_error_pct": error_pct,
        "matched": [{"pitch_class": gt["pitch_class"], "octave": gt["octave"], "kind": gt["kind"],
                     "expected_class": gt["expected_class"], "detected_class": ev["detected_class"],
                     "expected_beats": gt["duration_beats"], "detected_beats": ev.get("beats"),
                     "correct": ev["detected_class"] == gt["expected_class"],
                     "gt_time": gt.get(_REF_KEY, gt.get("end_time")), "detected_time": ev.get(_EVT_KEY)}
                    for gt, ev in matched],
        "missed": [{"pitch_class": gt["pitch_class"], "octave": gt["octave"], "kind": gt["kind"],
                     "expected_class": gt["expected_class"], "gt_time": gt.get(_REF_KEY, gt.get("end_time"))}
                   for gt in missed],
        "phantom": [{"pitch_class": ev["pitch_class"], "octave": ev["octave"], "kind": ev.get("kind"),
                     "detected_class": ev["detected_class"], "t": ev.get(_EVT_KEY)} for ev in phantom],
    }


# _REF_KEY/_EVT_KEY are set per-call by score_tier() below (module-level so
# _summarize()'s dict comprehensions -- shared by both causal and
# non-causal scoring -- can read whichever ground-truth/event time field
# this call is scoring against, without threading two more parameters
# through every helper).
_REF_KEY = "end_time"
_EVT_KEY = "t"


def score_tier(tier_id, name, description, bpm, ground_truth, log, hop_records, r0, beats_per_bar=4):
    global _REF_KEY, _EVT_KEY

    causal_events = _causal_events(log)
    _REF_KEY, _EVT_KEY = "audible_end_time", "t"
    matched, missed, phantom = _match(ground_truth, causal_events, "audible_end_time", "t",
                                       CAUSAL_MATCH_TOLERANCE_BEFORE_S, CAUSAL_MATCH_TOLERANCE_AFTER_S)
    settle = max(h["t"] for h in log) / 2 if log else 0
    tempo_estimates = [h["bpm_estimate"] for h in log if h["t"] >= settle]
    causal_result = _summarize(matched, missed, phantom, bpm, tempo_estimates)

    recompute_result = rhythm_reanalysis.recompute(hop_records, HOP_SECONDS, beats_per_bar) if hop_records else None
    if recompute_result is not None:
        nc_events = [{"pitch_class": n.pitch_class, "octave": n.octave,
                       "t": n.onset_time - r0 * HOP_SECONDS, "detected_class": n.duration_class,
                       "beats": None, "kind": None} for n in recompute_result.corrected_notes]
    else:
        nc_events = []
    _REF_KEY, _EVT_KEY = "start_time", "t"
    nc_matched, nc_missed, nc_phantom = _match(ground_truth, nc_events, "start_time", "t",
                                                NONCAUSAL_MATCH_TOLERANCE_S, NONCAUSAL_MATCH_TOLERANCE_S)
    nc_tempo = [recompute_result.bpm_estimate] if recompute_result is not None else []
    noncausal_result = _summarize(nc_matched, nc_missed, nc_phantom, bpm, nc_tempo)
    noncausal_result["tempo_estimate"] = recompute_result.bpm_estimate if recompute_result is not None else None

    n_mono = sum(1 for g in ground_truth if g["kind"] == "mono")
    n_chord = sum(1 for g in ground_truth if g["kind"] == "chord")
    return {
        "tier": tier_id, "name": name, "description": description, "bpm": bpm,
        "n_ground_truth": len(ground_truth), "n_mono": n_mono, "n_chord": n_chord,
        "causal": causal_result, "noncausal": noncausal_result,
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_tiers(tier_ids, outdir, sensitivity, source="loopback"):
    os.makedirs(outdir, exist_ok=True)
    session = main.SessionState(color_scheme="chromatic", sensitivity_value=sensitivity, source_value=source)
    mute_ctx = apt.muted_default_sink() if source == "loopback" else contextlib.nullcontext()
    results = []
    with mute_ctx, temporary_reanalysis_window(REANALYSIS_WINDOW_OVERRIDE_S):
        session.ensure_started()
        try:
            for tier_id, name, description, build_fn in TIERS:
                if tier_id not in tier_ids:
                    continue
                events, bpm, kwargs = build_fn()
                audio, ground_truth, total = build_phrase(events, bpm, **kwargs)
                print(f"=== tier {tier_id} ({name}): playing/recording ({total:.1f}s) ===")
                log, hop_records, r0 = record_phrase(session, audio, total)
                raw = {"ground_truth": ground_truth, "log": log,
                       "hop_records": [r._asdict() for r in hop_records],
                       "bpm": bpm, "beats_per_bar": 4, "hop_seconds": HOP_SECONDS, "r0": r0}
                with open(os.path.join(outdir, f"{tier_id:02d}_{name}_raw.json"), "w") as f:
                    json.dump(raw, f)
                scored = score_tier(tier_id, name, description, bpm, ground_truth, log, hop_records, r0)
                with open(os.path.join(outdir, f"{tier_id:02d}_{name}_scored.json"), "w") as f:
                    json.dump(scored, f, indent=2)
                results.append(scored)
                print(f"    causal recall={scored['causal']['onset_recall']} "
                      f"class_acc={scored['causal']['class_accuracy']} | "
                      f"noncausal recall={scored['noncausal']['onset_recall']} "
                      f"class_acc={scored['noncausal']['class_accuracy']}")
        finally:
            session.stop()
    with open(os.path.join(outdir, "all_results.json"), "w") as f:
        json.dump({"tiers": results, "generated": datetime.now().isoformat(timespec="seconds")}, f, indent=2)
    print(f"\nWrote {os.path.join(outdir, 'all_results.json')}")
    return results


def load_and_rescore(tier_ids, outdir):
    results = []
    for tier_id, name, description, _build_fn in TIERS:
        if tier_id not in tier_ids:
            continue
        path = os.path.join(outdir, f"{tier_id:02d}_{name}_raw.json")
        if not os.path.exists(path):
            print(f"(skipping tier {tier_id}: no {path})")
            continue
        with open(path) as f:
            raw = json.load(f)
        hop_records = [rhythm_reanalysis.HopRecord(**r) for r in raw["hop_records"]]
        for i, r in enumerate(hop_records):
            hop_records[i] = r._replace(chord_notes=tuple(tuple(x) for x in r.chord_notes))
        scored = score_tier(tier_id, name, description, raw["bpm"], raw["ground_truth"], raw["log"],
                             hop_records, raw["r0"], raw.get("beats_per_bar", 4))
        results.append(scored)
    with open(os.path.join(outdir, "all_results.json"), "w") as f:
        json.dump({"tiers": results, "generated": datetime.now().isoformat(timespec="seconds")}, f, indent=2)
    return results


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tiers", default=",".join(str(t[0]) for t in TIERS),
                         help="comma-separated tier IDs, default: all (0-11)")
    parser.add_argument("--outdir", default=None, help="default: rhythm_test_results/<timestamp>/")
    parser.add_argument("--sensitivity", type=float, default=config.DEFAULT_SENSITIVITY)
    parser.add_argument("--source", choices=("loopback", "mic"), default="loopback")
    parser.add_argument("--report", metavar="OUTDIR", default=None,
                         help="re-score raw JSON already in OUTDIR instead of running live audio")
    args = parser.parse_args()

    tier_ids = {int(x.strip()) for x in args.tiers.split(",") if x.strip()}
    valid_ids = {t[0] for t in TIERS}
    if not tier_ids <= valid_ids:
        parser.error(f"unknown tier(s) {sorted(tier_ids - valid_ids)} -- choose from {sorted(valid_ids)}")

    if args.report:
        load_and_rescore(tier_ids, args.report)
        return

    outdir = args.outdir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "rhythm_test_results", datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    run_tiers(tier_ids, outdir, args.sensitivity, args.source)


if __name__ == "__main__":
    main_cli()
