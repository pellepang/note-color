"""PROTOTYPE -- throwaway. Answers question 1 from the practice-mode task:
"does a reasonable note-matching algorithm actually produce sensible-
feeling scores against realistic synthesized test data?"

Deliberately does NOT reimplement scoring/alignment logic -- it imports
and calls the real thing, `practice_scorer.score_session()` /
`print_report()`, from the sibling prototype
`prototypes/session-log-and-practice-mode/`. That prototype already built
a first-cut "nearest-expected-beat" matcher and exercised it once, against
one 5-note melody with one deviation type per note (a mixed bag: on-time
hit, late hit, wrong pitch, missed, early hit -- see its own README).

What's NEW here: instead of one melody mixing several deviation types
across different notes, this runs FOUR separate whole-melody variants of
the SAME 9-note target, each deliberately isolating one failure mode --
"perfectly correct", "one wrong note", "rushed timing" (a whole
performance drifting increasingly early against a steady tempo), and "a
dropped note" -- exactly the four scenarios the task brief calls out by
name. Isolating one variable per run makes it much easier to eyeball
"does this specific failure mode score the way a human would expect",
rather than reading a single report where several things went wrong at
once and disentangling which score movement came from which cause.

Run:
    cd ~/note-color
    .venv/bin/python prototypes/practice-mode-concept/score_feel_check.py
"""

import json
import os
import tempfile

import _repo_paths  # noqa: F401  (sys.path bootstrap side effect -- see that file)

from practice_scorer import DEFAULT_TIME_TOLERANCE_SECONDS, print_report, score_session  # noqa: E402

# A short, recognizable target melody -- a C-major scale up and back down,
# quarter notes at 100bpm (0.6s/note). Kept in the same
# `{"label": ..., "t": ...}` shape as the sibling prototype's own
# target_melody.json, just longer (9 notes vs. 5) so a whole-melody
# variant has enough room to show a trend (rushing) rather than just one
# isolated event.
TARGET_MELODY = [
    {"label": "C4", "t": 0.0},
    {"label": "D4", "t": 0.6},
    {"label": "E4", "t": 1.2},
    {"label": "F4", "t": 1.8},
    {"label": "G4", "t": 2.4},
    {"label": "F4", "t": 3.0},
    {"label": "E4", "t": 3.6},
    {"label": "D4", "t": 4.2},
    {"label": "C4", "t": 4.8},
]

# Tighter than practice_scorer's own 0.75s default -- a whole-note-value
# window is generous even for a beginner at this melody's tempo (0.6s
# between notes); 0.35s is close to "within a sixteenth note or so either
# side" at 100bpm, which is closer to what actually reads as "on time" vs
# "late" to a human ear at this speed. Documented here rather than reusing
# the sibling's default so each variant below produces a report where the
# intended pass/fail line is actually crossed, not swamped by an overly
# forgiving window.
TIME_TOLERANCE_SECONDS = 0.35


def _write_log(events, path):
    with open(path, "w", encoding="utf-8") as fh:
        for t, label in events:
            fh.write(json.dumps({"t": t, "kind": "note", "label": label}) + "\n")


def _variant_perfect():
    """Every note correct pitch, small realistic human jitter (+/-20ms) --
    the control case. Expect near-100% pitch accuracy, low mean timing
    deviation."""
    jitter = [0.00, 0.01, -0.02, 0.015, -0.01, 0.00, 0.02, -0.015, 0.01]
    return [(note["t"] + j, note["label"]) for note, j in zip(TARGET_MELODY, jitter)]


def _variant_one_wrong_note():
    """Every note on time and correct pitch except one (index 4, the
    melody's peak note G4, played as A4 instead) -- isolates pure pitch
    error from any timing error at all. Expect exactly 8/9 pitch accuracy,
    still-low timing deviation (the wrong note is still ON TIME, just the
    wrong pitch -- practice_scorer's own report format should make that
    combination legible: a 'wrong_pitch' row with a *small* timing
    deviation column, not lumped in with the missed/late cases)."""
    events = []
    for i, note in enumerate(TARGET_MELODY):
        label = "A4" if i == 4 else note["label"]
        events.append((note["t"], label))
    return events


def _variant_rushed_timing():
    """Every pitch correct, but the whole performance drifts increasingly
    early against the target's steady tempo -- a nervous player speeding
    up, not a single mistimed note. Deviation grows linearly from 0 to
    -0.48s across the 9 notes. Deliberately picked so the LAST couple of
    notes fall outside TIME_TOLERANCE_SECONDS (0.35s) while the first few
    stay inside it -- the interesting question this variant is really
    asking the scorer: does 'gradually rushing' correctly show up as a
    late-run of MISSES once the drift exceeds tolerance, rather than
    either (a) never triggering a miss at all (tolerance effectively
    meaningless) or (b) the whole melody missing outright (tolerance far
    too tight)?"""
    events = []
    for i, note in enumerate(TARGET_MELODY):
        drift = -0.06 * i  # 0, -0.06, -0.12, ... -0.48
        events.append((note["t"] + drift, note["label"]))
    return events


def _variant_dropped_note():
    """Every other note correct and on time; one note (index 6, the
    second E4) simply never played -- a clean skip, not a mistimed or
    mis-pitched attempt. Expect exactly one 'missed' row and otherwise a
    clean report, isolating "silently skipping a note" from every other
    failure mode."""
    events = []
    for i, note in enumerate(TARGET_MELODY):
        if i == 6:
            continue
        events.append((note["t"], note["label"]))
    return events


VARIANTS = [
    ("1. Perfectly correct (small human jitter)", _variant_perfect),
    ("2. One wrong note (pitch only, on time)", _variant_one_wrong_note),
    ("3. Rushed timing (whole performance drifts early)", _variant_rushed_timing),
    ("4. A dropped note (clean skip, no attempt)", _variant_dropped_note),
]


def main():
    print(f"Target: 9-note C-major scale up/down, 0.6s/note (100bpm quarters), "
          f"tolerance=+-{TIME_TOLERANCE_SECONDS * 1000:.0f}ms")
    print(f"(practice_scorer's own default tolerance is "
          f"{DEFAULT_TIME_TOLERANCE_SECONDS * 1000:.0f}ms -- tightened here, see module docstring)\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        for title, build_events in VARIANTS:
            print("=" * 78)
            print(title)
            print("=" * 78)
            log_path = os.path.join(tmpdir, "attempt.jsonl")
            _write_log(build_events(), log_path)
            report = score_session(TARGET_MELODY, log_path, time_tolerance_seconds=TIME_TOLERANCE_SECONDS)
            print_report(report)
            print()


if __name__ == "__main__":
    main()
