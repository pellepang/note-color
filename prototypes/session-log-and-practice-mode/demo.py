"""End-to-end demo: fabricate a short monophonic "performance" as a
sequence of RenderItem-shaped hop dicts, record it with SessionRecorder,
replay it with SessionPlayer, then score it against a hand-written target
melody (target_melody.json) with practice_scorer.

Run: .venv/bin/python prototypes/session-log-and-practice-mode/demo.py

The fabricated performance deliberately contains a mix of correct,
mistimed, wrong-pitch, and skipped notes against the target, so the
printed report has something real to say (see the walkthrough comments
below for the exact intended outcome of each target note).
"""

import json
import math
import os

import _repo_paths  # noqa: F401  (sys.path bootstrap side effect)
import color_map
import config

from practice_scorer import print_report, score_session
from session_player import SessionPlayer
from session_recorder import SessionRecorder

HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE  # ~0.0232s, matches the live pipeline's hop rate
LOG_PATH = os.path.join(os.path.dirname(__file__), "demo_session.jsonl")

# The "performance": what was actually played, as (pitch_class, octave,
# start_t, end_t) intervals -- monophonic, so non-overlapping by
# construction, with realistic small silence gaps between notes. This
# deliberately diverges from target_melody.json in four different ways,
# one per note (see the target-vs-performance walkthrough below):
#
#   target C4@0.0  -> played C4@0.00  : correct pitch, on time      -> hit
#   target D4@0.5  -> played D4@0.65  : correct pitch, late +0.15s  -> hit (with deviation)
#   target E4@1.0  -> played F4@1.15  : WRONG pitch                 -> wrong_pitch
#   target G4@2.0  -> not played at all                             -> missed
#   target A4@2.5  -> played A4@2.45  : correct pitch, early -0.05s -> hit
PERFORMANCE = [
    ("C4", 0, 4, 0.00, 0.45),
    ("D4", 2, 4, 0.65, 1.10),
    ("F4", 5, 4, 1.15, 2.05),  # wrong note -- target expected E4 (pc=4) here
    ("A4", 9, 4, 2.45, 2.90),
]

BPM = 120.0
SCORE_TOLERANCE_SECONDS = 0.3  # tighter than practice_scorer's 0.75s default, chosen for this demo
BARLINE_AT_T = 1.20  # roughly one bar in at 120bpm/4-4 -- demonstrates record_barline()


def _active_note_at(t):
    for _label, pc, octave, start, end in PERFORMANCE:
        if start <= t < end:
            return pc, octave
    return None, None


def generate_hops(tail_seconds=0.3):
    """Build the full per-hop RenderItem-shaped dict stream for
    PERFORMANCE, including the duration_hops finalization semantics
    documented in session_recorder.py: duration_hops is set on the exact
    hop the active note *changes* (including into/out of silence), and
    describes the note that was active up to (not including) that hop."""
    end_t = max(end for *_rest, end in PERFORMANCE) + tail_seconds
    n_hops = int(math.ceil(end_t / HOP_SECONDS))

    hops = []
    prev_pc_octave = (None, None)
    onset_hop = None
    for hop_index in range(n_hops):
        t = hop_index * HOP_SECONDS
        pc, octave = _active_note_at(t)

        duration_hops = None
        if (pc, octave) != prev_pc_octave:
            if prev_pc_octave[0] is not None:
                duration_hops = hop_index - onset_hop
            if pc is not None:
                onset_hop = hop_index

        item = {
            # Fields SessionRecorder actually reads:
            "pitch_class": pc,
            "octave": octave,
            "note_stack": [],       # mono-only demo -- chord path already exercised by SessionRecorder's own logic
            "chord_name": None,
            "duration_hops": duration_hops,
            "bpm_estimate": BPM,
            # Remaining RenderItem fields, filled in for shape-realism
            # (SessionRecorder ignores these, but a real RenderItem always
            # carries them -- see main.py's RenderItem definition):
            "target_rgb": (0, 0, 0),
            "is_onset": pc is not None and (pc, octave) != prev_pc_octave,
            "label": f"{color_map.NOTE_NAMES[pc]}{octave}" if pc is not None else "-",
            "freq": None,
            "confidence": 0.9 if pc is not None else 0.0,
            "rms": 0.05 if pc is not None else 0.0,
            "fifths_idx": color_map.fifths_index(pc) if pc is not None else None,
        }
        hops.append((t, item))
        prev_pc_octave = (pc, octave)
    return hops


def main():
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)

    # --- 1. Record ---------------------------------------------------
    hops = generate_hops()
    with SessionRecorder(LOG_PATH) as recorder:
        for t, item in hops:
            recorder.record_hop(item, t)
        recorder.record_barline(BARLINE_AT_T)
    print(f"Recorded {recorder.events_written} events from {len(hops)} synthesized hops "
          f"({HOP_SECONDS * 1000:.1f}ms/hop) -> {LOG_PATH}\n")

    print("=== Raw JSONL log contents ===")
    with open(LOG_PATH, encoding="utf-8") as fh:
        for line in fh:
            print(line.rstrip())
    print()

    # --- 2. Replay -----------------------------------------------------
    print("=== SessionPlayer replay ===")
    SessionPlayer(LOG_PATH).replay()
    print()

    # --- 3. Score against the target melody -----------------------------
    print("=== Practice-mode score vs target_melody.json ===")
    target_path = os.path.join(os.path.dirname(__file__), "target_melody.json")
    with open(target_path, encoding="utf-8") as fh:
        target = json.load(fh)
    report = score_session(target, LOG_PATH, time_tolerance_seconds=SCORE_TOLERANCE_SECONDS)
    print_report(report)


if __name__ == "__main__":
    main()
