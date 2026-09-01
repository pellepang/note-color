"""Minimal practice-mode scorer (feature idea 3 in
docs/research/notation-and-feature-ideas.md, scoped down to its "even a
naive nearest-expected-beat match, no full dynamic-time-warping, would
cover a first version" starting point).

Given (a) a target note sequence -- a plain list of
`{"label": "C4", "t": expected_seconds}` dicts, in order (a tiny fixture
file is exactly this list dumped as JSON -- see target_melody.json) -- and
(b) a `.jsonl` session log of an attempt at playing it (SessionPlayer-
readable), computes:

  - pitch_accuracy: fraction of target notes matched to a played note of
    the same label, in order, within `time_tolerance_seconds` of the
    target's expected time.
  - rhythm_mean_abs_deviation_seconds: mean |actual_t - expected_t| over
    only the *matched* notes -- a measure of timing looseness independent
    of whether the pitch itself was right.
  - a per-target-note detail list (hit / wrong_pitch / missed) for a
    human-readable report.

Alignment algorithm: for each target note in order, pick the nearest-in-
time not-yet-used recorded note within the tolerance window; if none
exists, it's a miss. This is intentionally the simple "nearest expected
beat" version the research doc calls out as sufficient for a first cut,
not a full alignment/DTW algorithm -- good enough for a short target
phrase where notes aren't so densely packed that nearest-in-time
ambiguity becomes a real problem.
"""

from session_player import SessionPlayer

DEFAULT_TIME_TOLERANCE_SECONDS = 0.75


def score_session(target, log_path, time_tolerance_seconds=DEFAULT_TIME_TOLERANCE_SECONDS):
    recorded = [e for e in SessionPlayer(log_path).load_events() if e.get("kind") == "note"]
    used = [False] * len(recorded)

    details = []
    matched = 0
    deviations = []

    for target_note in target:
        best_idx = None
        best_dt = None
        for i, event in enumerate(recorded):
            if used[i]:
                continue
            dt = event["t"] - target_note["t"]
            if abs(dt) <= time_tolerance_seconds and (best_dt is None or abs(dt) < abs(best_dt)):
                best_idx, best_dt = i, dt

        if best_idx is None:
            details.append(
                {
                    "target": target_note["label"],
                    "expected_t": target_note["t"],
                    "result": "missed",
                }
            )
            continue

        used[best_idx] = True
        event = recorded[best_idx]
        pitch_ok = event["label"] == target_note["label"]
        if pitch_ok:
            matched += 1
            deviations.append(best_dt)
        details.append(
            {
                "target": target_note["label"],
                "expected_t": target_note["t"],
                "played": event["label"],
                "played_t": event["t"],
                "timing_deviation_s": round(best_dt, 3),
                "result": "hit" if pitch_ok else "wrong_pitch",
            }
        )

    total = len(target)
    rhythm_mean_abs_dev = sum(abs(d) for d in deviations) / len(deviations) if deviations else None

    return {
        "pitch_accuracy": matched / total if total else 0.0,
        "notes_matched": matched,
        "notes_total": total,
        "rhythm_mean_abs_deviation_seconds": rhythm_mean_abs_dev,
        "rhythm_sample_count": len(deviations),
        "details": details,
    }


def print_report(report):
    print(f"Pitch accuracy: {report['notes_matched']}/{report['notes_total']} "
          f"({report['pitch_accuracy'] * 100:.0f}%)")
    if report["rhythm_mean_abs_deviation_seconds"] is not None:
        print(f"Rhythm accuracy: mean timing deviation "
              f"{report['rhythm_mean_abs_deviation_seconds'] * 1000:.0f}ms "
              f"(over {report['rhythm_sample_count']} correctly-pitched, matched notes)")
    else:
        print("Rhythm accuracy: n/a (no notes matched)")
    print()
    print(f"{'target':<8}{'expected_t':<12}{'played':<8}{'played_t':<12}{'dev_ms':<10}result")
    for d in report["details"]:
        played = d.get("played", "-")
        played_t = f"{d['played_t']:.2f}" if "played_t" in d else "-"
        dev_ms = f"{d['timing_deviation_s'] * 1000:+.0f}" if "timing_deviation_s" in d else "-"
        print(f"{d['target']:<8}{d['expected_t']:<12.2f}{played:<8}{played_t:<12}{dev_ms:<10}{d['result']}")
