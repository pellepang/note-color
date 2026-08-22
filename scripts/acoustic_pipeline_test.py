"""Extensive acoustic round-trip test suite against note-color's REAL,
unmodified live pipeline (`main.SessionState` + `main.analysis_loop()` --
the exact code every `virtualnote` terminal view runs). Not a pytest test:
it drives real audio hardware and takes several minutes to run, so it's a
manual verification tool, same convention as the "real speaker->mic
acoustic round-trip test" already used to validate this pipeline (see
CLAUDE.md's Status section) -- this script is that test, made repeatable,
quantitative, and broader than a single ad hoc smoke check.

`--source loopback` (the default) plays the synthesized test audio through
the system's default output and captures it back via that output's
PipeWire/PulseAudio monitor (`audio_capture.resolve_loopback_device()`) --
a real round trip through the actual audio stack (real playback device,
real capture device, real resampling, real PortAudio buffering/timing) but
with no physical air gap, so it needs no speaker/mic in the same room, no
quiet room, and produces no audible sound: the script mutes the default
sink for the run (restoring whatever mute state it found afterward) so it
can run unattended. This does NOT reproduce room acoustics (reflections,
comb filtering, mic frequency response) that a real speaker->mic round
trip would -- it's a stronger check than pure synthetic array-slicing
tests (real hardware/OS audio path, real timing jitter) but a weaker one
than an actual room test. `--source mic` restores the original physical
speaker->mic behavior for when that's what's actually wanted.

What it measures, one suite at a time:

- `chromatic`  -- monophonic pitch-detection accuracy and attack-to-display
                  latency, swept across every pitch class in every octave
                  this app's YIN range (`config.FMIN`/`FMAX`) covers.
- `tempo`      -- how fast a legato (no-gap) note sequence can move before
                  the pipeline starts missing notes or lagging behind --
                  the concrete "how fast can it go" question.
- `chords`     -- chord-name accuracy and phantom/missing note-stack pitch
                  classes across a broad sample of qualities, roots, and
                  registers (triads, 7ths, sus/add chords, one slash chord).
- `density`    -- how detection quality (recall/phantom rate) degrades as
                  the number of simultaneously-sounding notes rises from 1
                  to `config.CHORD_MAX_NOTES`.
- `sustain`    -- long (6s) held single notes and chords, to precisely
                  quantify onset-gate misfire rate (issue #66) and
                  duration-tracking fragmentation (issues #64/#67) with a
                  large per-note sample size.
- `rhythm`     -- live tempo-tracking convergence (`TempoTracker`) against
                  an isochronous pulse train of known BPM, and live
                  duration-class snapping (`duration_class_for_beats()`)
                  against notes of known standard-value length at that
                  BPM -- issue #55's rhythm pipeline has so far only been
                  verified via synthetic array-slicing unit tests and one
                  `virtualnote transcribe` batch run (see CLAUDE.md's
                  Status section); this is its first check against the
                  live per-hop pipeline through a real audio round trip.
- `noise`      -- a reduced chromatic + chord sweep with additive
                  broadband noise mixed in at a few SNR levels, to check
                  onset-gate/sensitivity-threshold robustness beyond the
                  effectively-silent-room conditions every other suite
                  tests under.

Usage:
    .venv/bin/python scripts/acoustic_pipeline_test.py                 # run everything
    .venv/bin/python scripts/acoustic_pipeline_test.py --suites chords,tempo
    .venv/bin/python scripts/acoustic_pipeline_test.py --report OUTDIR # re-analyze a past run, no audio needed

Run output (raw per-hop JSON logs + a generated report.md) is written under
`--outdir` (default `acoustic_test_results/<timestamp>/`, gitignored --
these are machine/room-specific measurements, not something to commit,
same convention as this repo's `note_history_*.txt` dumps). The script
itself is the thing worth keeping in version control.
"""

import argparse
import contextlib
import itertools
import json
import os
import queue
import statistics
import subprocess
import sys
import time
from datetime import datetime

import numpy as np
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import main
from color_map import NOTE_NAMES_FIFTHS
from duration_tracker import duration_class_for_beats

HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE


@contextlib.contextmanager
def muted_default_sink():
    """Mutes the system's default output sink for the duration of the
    `with` block, restoring whatever mute state it found (muted or not)
    on exit -- even on an exception. Used by `--source loopback` so the
    round trip through the real audio stack produces no audible sound and
    the script can run unattended. `resolve_loopback_device()`'s own
    docstring notes this is confirmed safe on PipeWire (the monitor taps
    upstream of the mute point); if `pactl` is unavailable at all, this
    silently no-ops rather than failing the whole run -- worst case the
    test is audible, not broken."""
    original = None
    try:
        out = subprocess.run(
            ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.strip()
        original = "yes" in out.lower()
        subprocess.run(
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
            capture_output=True, text=True, timeout=2, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        original = None
    try:
        yield
    finally:
        if original is not None:
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if original else "0"],
                capture_output=True, text=True, timeout=2,
            )

PLAYBACK_SR = 44100
POLL_INTERVAL_S = 0.005          # busy-poll rate against the single-slot result_queue
LATENCY_SEARCH_SLOP_S = 0.15     # config's own "comfortably under 150ms" end-to-end latency target

NOTE_INDEX = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
              "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

# Same quality->(offsets, jazz-symbol) definitions as chord_templates.QUALITIES,
# reproduced independently here (not imported) so this test's "expected"
# chord names are an independent ground truth, not a self-check against the
# app's own table -- these are standard jazz-notation chord definitions, not
# app-specific, so duplicating the handful this suite actually exercises is
# fine.
QUALITY_DEFS = {
    "maj": ({0, 4, 7}, ""),
    "min": ({0, 3, 7}, "-"),
    "dim": ({0, 3, 6}, "°"),
    "aug": ({0, 4, 8}, "+"),
    "dom7": ({0, 4, 7, 10}, "7"),
    "maj7": ({0, 4, 7, 11}, "Δ7"),
    "min7": ({0, 3, 7, 10}, "-7"),
    "dim7": ({0, 3, 6, 9}, "°7"),
    "half-dim7": ({0, 3, 6, 10}, "ø7"),
    "sus2": ({0, 2, 7}, "sus2"),
    "sus4": ({0, 5, 7}, "sus4"),
    "add9": ({0, 2, 4, 7}, "add9"),
}


def freq_of(name, octave):
    pc = NOTE_INDEX[name]
    midi = (octave + 1) * 12 + pc
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def voice_chord(root_name, root_octave, quality):
    """(offsets, symbol) from QUALITY_DEFS -> a close-position note list
    [(name, octave), ...] stacked upward from root_octave, plus the
    expected (pitch_class_set, chord_name) ground truth (root-position;
    slash-chord ground truth is built by the caller instead, see
    build_chords())."""
    offsets, symbol = QUALITY_DEFS[quality]
    root_pc = NOTE_INDEX[root_name]
    notes = []
    pcs = set()
    for offset in sorted(offsets):
        pc = (root_pc + offset) % 12
        octave = root_octave + (root_pc + offset) // 12
        # freq_of() only needs a NOTE_INDEX-compatible (sharp-spelled) name --
        # pitch-class math is spelling-independent, so any enharmonic works.
        notes.append((_fifths_to_sharp(pc), octave))
        pcs.add(pc)
    name = NOTE_NAMES_FIFTHS[root_pc] + symbol
    return notes, pcs, name


def _fifths_to_sharp(pc):
    """voice_chord() needs a NOTE_INDEX-compatible (sharp-spelled) name for
    freq_of(); NOTE_NAMES_FIFTHS spells some pitch classes with flats
    (Db/Eb/Ab/Bb), which aren't in NOTE_INDEX. Pitch class math is spelling-
    independent, so any enharmonic spelling gives the identical frequency --
    this only exists to satisfy freq_of()'s lookup."""
    sharp_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return sharp_names[pc]


def synth_notes(notes, duration, sr=PLAYBACK_SR, base_amp=0.30, fade_s=0.02, peak_cap=0.85):
    """Additive synthesis, harmonics 1-4 weighted like chroma.py's
    HARMONIC_WEIGHTS (realistic overtone content, not a bare sine -- see
    the earlier acoustic-test writeup for why that matters to multipitch/
    chroma). Each note is synthesized at full base_amp independently, then
    the sum is peak-normalized down (never up) to peak_cap -- this keeps a
    dense 6-note stack from being drowned out relative to a single note,
    without ever clipping."""
    n = max(int(duration * sr), 1)
    t = np.arange(n) / sr
    out = np.zeros(n)
    weights = {1: 1.0, 2: 0.5, 3: 1.0 / 3, 4: 0.25}
    wsum = sum(weights.values())
    for name, octave in notes:
        f0 = freq_of(name, octave)
        tone = sum(w * np.sin(2 * np.pi * f0 * h * t) for h, w in weights.items()) / wsum
        out += tone * base_amp
    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > peak_cap:
        out *= peak_cap / peak
    fade = int(fade_s * sr)
    if notes and fade * 2 < n:
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        out *= env
    return out.astype(np.float32)


# --------------------------------------------------------------------------
# Playback + recording harness
# --------------------------------------------------------------------------

def record_session(session, audio, total_runtime_s, warmup_s=1.5):
    """Plays `audio` (already at PLAYBACK_SR) out the speakers while
    draining `session.result_queue` (the same single-slot queue every
    run_terminal_* loop reads) as fast as POLL_INTERVAL_S allows, for
    `total_runtime_s` seconds measured from when playback actually starts.
    Returns a list of per-hop dicts, one per RenderItem observed, each
    tagged with its own wall-clock offset `t` from playback start."""
    time.sleep(warmup_s)  # let the mic stream settle before the clock below starts
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
            time.sleep(POLL_INTERVAL_S)
            continue
        log.append({
            "t": round(now, 4),
            "pitch_class": item.pitch_class,
            "octave": item.octave,
            "is_onset": item.is_onset,
            "confidence": item.confidence,
            "duration_hops": item.duration_hops,
            "bpm_estimate": item.bpm_estimate,
            "chord_name": item.chord_name,
            "note_stack": [
                {"pitch_class": e["pitch_class"], "octave": e["octave"],
                 "duration_hops": e.get("duration_hops")}
                for e in item.note_stack
            ],
        })
    sd.stop()
    return log


def build_timed_audio(entries, gap_s, warmup_lead_s=0.0):
    """entries: list of (notes, duration_s, meta_dict). Concatenates
    silence-separated segments and returns (audio, timeline) where
    timeline is entries with 'start'/'end' wall-clock offsets (relative to
    when playback starts, i.e. matching record_session()'s `t` clock)
    filled in."""
    chunks = []
    if warmup_lead_s > 0:
        chunks.append(np.zeros(int(warmup_lead_s * PLAYBACK_SR), dtype=np.float32))
    timeline = []
    t = warmup_lead_s
    for notes, duration, meta in entries:
        chunks.append(synth_notes(notes, duration))
        entry = dict(meta)
        entry["notes"] = notes
        entry["start"] = t
        entry["end"] = t + duration
        timeline.append(entry)
        t += duration
        chunks.append(np.zeros(int(gap_s * PLAYBACK_SR), dtype=np.float32))
        t += gap_s
    return np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32), timeline, t


# --------------------------------------------------------------------------
# Suite: chromatic accuracy + latency
# --------------------------------------------------------------------------

def build_chromatic():
    sharp_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    entries = []
    for octave in (2, 3, 4, 5):
        for name in sharp_names:
            entries.append(([(name, octave)], 1.4, {"pitch_class": NOTE_INDEX[name], "octave": octave}))
    return build_timed_audio(entries, gap_s=0.4, warmup_lead_s=1.5)


def analyze_chromatic(log, timeline):
    rows = []
    hits = 0
    latencies = []
    for seg in timeline:
        expected = (seg["pitch_class"], seg["octave"])
        window_end = seg["end"] + LATENCY_SEARCH_SLOP_S
        match_ts = [h["t"] for h in log
                    if seg["start"] <= h["t"] <= window_end
                    and (h["pitch_class"], h["octave"]) == expected]
        steady = [h for h in log if seg["start"] + 0.3 <= h["t"] <= seg["end"] - 0.1]
        steady_correct = sum(1 for h in steady if (h["pitch_class"], h["octave"]) == expected)
        steady_acc = steady_correct / len(steady) if steady else 0.0
        detected = bool(match_ts)
        latency_ms = round((match_ts[0] - seg["start"]) * 1000, 1) if detected else None
        if detected:
            hits += 1
            latencies.append(latency_ms)
        rows.append({
            "note": f"{sharp_names_display(expected[0])}{expected[1]}",
            "detected": detected, "latency_ms": latency_ms, "steady_state_accuracy": round(steady_acc, 3),
        })
    summary = {
        "n_notes": len(timeline),
        "recall": round(hits / len(timeline), 3) if timeline else 0.0,
        "latency_ms_median": round(statistics.median(latencies), 1) if latencies else None,
        "latency_ms_p90": round(sorted(latencies)[int(len(latencies) * 0.9)], 1) if len(latencies) >= 10 else None,
        "mean_steady_state_accuracy": round(statistics.mean(r["steady_state_accuracy"] for r in rows), 3) if rows else 0.0,
    }
    misses = [r["note"] for r in rows if not r["detected"]]
    low_acc = [r["note"] for r in rows if r["detected"] and r["steady_state_accuracy"] < 0.7]
    return {"summary": summary, "rows": rows, "misses": misses, "low_accuracy": low_acc}


def sharp_names_display(pc):
    return ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][pc]


# --------------------------------------------------------------------------
# Suite: tempo / speed (legato note runs, no gap between notes)
# --------------------------------------------------------------------------

SCALE = [("C", 4), ("D", 4), ("E", 4), ("F", 4), ("G", 4), ("A", 4), ("B", 4), ("C", 5)]
TEMPI_BPM = [90, 140, 200, 280]
REPS_PER_TEMPO = 3


def build_tempo():
    chunks = [np.zeros(int(1.5 * PLAYBACK_SR), dtype=np.float32)]
    timeline = []
    t = 1.5
    for bpm in TEMPI_BPM:
        note_dur = 60.0 / bpm / 2.0  # eighth notes
        for rep in range(REPS_PER_TEMPO):
            for name, octave in SCALE:
                chunks.append(synth_notes([(name, octave)], note_dur, fade_s=min(0.01, note_dur / 4)))
                timeline.append({
                    "bpm": bpm, "rep": rep, "pitch_class": NOTE_INDEX[name], "octave": octave,
                    "start": t, "end": t + note_dur, "note_dur": note_dur,
                })
                t += note_dur
            gap = 0.3
            chunks.append(np.zeros(int(gap * PLAYBACK_SR), dtype=np.float32))
            t += gap
        gap = 1.0
        chunks.append(np.zeros(int(gap * PLAYBACK_SR), dtype=np.float32))
        t += gap
    return np.concatenate(chunks), timeline, t


def analyze_tempo(log, timeline):
    by_tempo = {}
    for seg in timeline:
        bpm = seg["bpm"]
        by_tempo.setdefault(bpm, []).append(seg)

    results = {}
    for bpm, segs in by_tempo.items():
        hits, latencies = 0, []
        for seg in segs:
            expected = (seg["pitch_class"], seg["octave"])
            window_end = seg["end"] + LATENCY_SEARCH_SLOP_S
            matches = [h["t"] for h in log
                       if seg["start"] <= h["t"] <= window_end
                       and (h["pitch_class"], h["octave"]) == expected]
            if matches:
                hits += 1
                latencies.append((matches[0] - seg["start"]) * 1000)
        results[bpm] = {
            "n_notes": len(segs),
            "recall": round(hits / len(segs), 3) if segs else 0.0,
            "latency_ms_median": round(statistics.median(latencies), 1) if latencies else None,
            "note_duration_ms": round(segs[0]["note_dur"] * 1000, 1),
        }
    return results


# --------------------------------------------------------------------------
# Suite: chord accuracy + phantom notes
# --------------------------------------------------------------------------

def build_chords():
    entries = []

    for quality in ("maj", "min", "dim", "aug"):
        for root in ("C", "F#", "A"):
            for octave in (3, 4):
                notes, pcs, name = voice_chord(root, octave, quality)
                entries.append((notes, 3.0, {"expected_pcs": sorted(pcs), "expected_name": name,
                                              "quality": quality, "root": root, "register": octave}))

    for quality in ("dom7", "maj7", "min7", "dim7", "half-dim7"):
        for root in ("C", "F", "A"):
            notes, pcs, name = voice_chord(root, 4, quality)
            entries.append((notes, 3.0, {"expected_pcs": sorted(pcs), "expected_name": name,
                                          "quality": quality, "root": root, "register": 4}))
        notes, pcs, name = voice_chord("C", 3, quality)
        entries.append((notes, 3.0, {"expected_pcs": sorted(pcs), "expected_name": name,
                                      "quality": quality, "root": "C", "register": 3}))

    for quality in ("sus2", "sus4", "add9"):
        notes, pcs, name = voice_chord("C", 4, quality)
        entries.append((notes, 3.0, {"expected_pcs": sorted(pcs), "expected_name": name,
                                      "quality": quality, "root": "C", "register": 4}))

    # Slash chords: same triad, voiced with a non-root chord tone as the
    # lowest note -- chord_templates.match() should detect the true bass
    # via chroma.fold_bass()/multipitch's lowest candidate and name it
    # "<root><quality>/<bass>".
    entries.append(([("E", 3), ("C", 4), ("G", 4)], 3.0,
                     {"expected_pcs": sorted({0, 4, 7}), "expected_name": "C/E",
                      "quality": "maj/slash", "root": "C", "register": 4}))
    entries.append(([("B", 3), ("D", 4), ("G", 4)], 3.0,
                     {"expected_pcs": sorted({7, 11, 2}), "expected_name": "G/B",
                      "quality": "maj/slash", "root": "G", "register": 4}))

    return build_timed_audio(entries, gap_s=0.7, warmup_lead_s=1.5)


def analyze_chords(log, timeline):
    rows = []
    for seg in timeline:
        steady = [h for h in log if seg["start"] + 0.6 <= h["t"] <= seg["end"] - 0.15]
        if not steady:
            rows.append({**{k: seg[k] for k in ("quality", "root", "register", "expected_name")},
                         "name_correct": False, "phantom_rate": None, "missing_rate": None, "n_hops": 0})
            continue
        expected_pcs = set(seg["expected_pcs"])
        name_votes = {}
        phantom_counts, missing_counts = [], []
        for h in steady:
            name_votes[h["chord_name"]] = name_votes.get(h["chord_name"], 0) + 1
            got_pcs = {n["pitch_class"] for n in h["note_stack"]}
            phantom_counts.append(len(got_pcs - expected_pcs))
            missing_counts.append(len(expected_pcs - got_pcs))
        top_name = max(name_votes.items(), key=lambda kv: kv[1])[0]
        rows.append({
            "quality": seg["quality"], "root": seg["root"], "register": seg["register"],
            "expected_name": seg["expected_name"], "detected_name": top_name,
            "name_correct": top_name == seg["expected_name"],
            "phantom_rate": round(statistics.mean(phantom_counts), 2),
            "missing_rate": round(statistics.mean(missing_counts), 2),
            "n_hops": len(steady),
        })
    n = len(rows)
    name_correct = sum(1 for r in rows if r["name_correct"])
    phantom_rates = [r["phantom_rate"] for r in rows if r["phantom_rate"] is not None]
    summary = {
        "n_chords": n,
        "name_accuracy": round(name_correct / n, 3) if n else 0.0,
        "mean_phantom_pcs_per_hop": round(statistics.mean(phantom_rates), 3) if phantom_rates else None,
        "mean_missing_pcs_per_hop": round(statistics.mean(r["missing_rate"] for r in rows if r["missing_rate"] is not None), 3) if rows else None,
        "worst_phantom": sorted(rows, key=lambda r: -(r["phantom_rate"] or 0))[:5],
    }
    return {"summary": summary, "rows": rows}


# --------------------------------------------------------------------------
# Suite: polyphonic note-count scaling (density -> phantom/missing rate)
# --------------------------------------------------------------------------

DENSITY_VOICINGS = {
    1: [[("C", 4)], [("F#", 3)], [("A", 5)]],
    2: [[("C", 3), ("F#", 4)], [("D", 4), ("A", 2)], [("G", 5), ("D", 2)]],
    3: [[("C", 3), ("F#", 4), ("D", 5)], [("A", 2), ("E", 4), ("C", 5)], [("F", 3), ("B", 4), ("G", 2)]],
    4: [[("C", 2), ("F#", 3), ("D", 4), ("A", 5)], [("E", 2), ("A", 3), ("C", 4), ("F#", 5)],
        [("G", 2), ("D", 3), ("A", 4), ("E", 5)]],
    5: [[("C", 2), ("D#", 3), ("F#", 3), ("A", 4), ("D", 5)],
        [("F", 2), ("A", 2), ("C", 3), ("E", 4), ("G", 5)],
        [("D", 2), ("F#", 2), ("A", 3), ("C", 4), ("E", 5)]],
    6: [[("C", 2), ("D#", 2), ("F#", 3), ("A", 3), ("D", 4), ("G", 5)],
        [("E", 2), ("G", 2), ("B", 3), ("D", 4), ("F#", 4), ("A", 5)],
        [("F", 2), ("A", 2), ("C", 3), ("D#", 3), ("G", 4), ("B", 5)]],
}


def build_density():
    entries = []
    for count in sorted(DENSITY_VOICINGS):
        for voicing in DENSITY_VOICINGS[count]:
            pcs = sorted({NOTE_INDEX[n] for n, _o in voicing})
            entries.append((voicing, 3.0, {"count": count, "expected_pcs": pcs}))
    return build_timed_audio(entries, gap_s=0.6, warmup_lead_s=1.5)


def analyze_density(log, timeline):
    rows = []
    for seg in timeline:
        steady = [h for h in log if seg["start"] + 0.6 <= h["t"] <= seg["end"] - 0.15]
        expected_pcs = set(seg["expected_pcs"])
        if not steady:
            continue
        phantom, missing, sizes = [], [], []
        for h in steady:
            got = {n["pitch_class"] for n in h["note_stack"]}
            phantom.append(len(got - expected_pcs))
            missing.append(len(expected_pcs - got))
            sizes.append(len(got))
        rows.append({
            "count": seg["count"], "expected_pcs": seg["expected_pcs"],
            "mean_phantom": round(statistics.mean(phantom), 2),
            "mean_missing": round(statistics.mean(missing), 2),
            "mean_stack_size": round(statistics.mean(sizes), 2),
        })
    by_count = {}
    for r in rows:
        by_count.setdefault(r["count"], []).append(r)
    summary = {}
    for count, rs in sorted(by_count.items()):
        summary[count] = {
            "mean_phantom": round(statistics.mean(r["mean_phantom"] for r in rs), 2),
            "mean_missing": round(statistics.mean(r["mean_missing"] for r in rs), 2),
            "mean_stack_size_vs_expected": round(statistics.mean(r["mean_stack_size"] for r in rs), 2),
        }
    return {"summary": summary, "rows": rows}


# --------------------------------------------------------------------------
# Suite: long sustain -- onset-misfire rate + duration fragmentation
# --------------------------------------------------------------------------

def build_sustain():
    mono_entries = [
        ([("A", 2)], 6.0, {"kind": "mono", "label": "A2"}),
        ([("C", 4)], 6.0, {"kind": "mono", "label": "C4"}),
        ([("E", 5)], 6.0, {"kind": "mono", "label": "E5"}),
    ]
    chord_entries = [
        ([("C", 4), ("E", 4), ("G", 4)], 6.0, {"kind": "chord", "label": "Cmaj"}),
        ([("A", 3), ("C", 4), ("E", 4), ("G", 4)], 6.0, {"kind": "chord", "label": "Am7"}),
        ([("D", 3), ("F#", 3), ("A", 3), ("C", 4)], 6.0, {"kind": "chord", "label": "D7"}),
    ]
    return build_timed_audio(mono_entries + chord_entries, gap_s=1.0, warmup_lead_s=1.5)


def analyze_sustain(log, timeline):
    rows = []
    for seg in timeline:
        window = [h for h in log if seg["start"] <= h["t"] <= seg["end"]]
        if seg["kind"] == "mono":
            onset_rate = round(statistics.mean(1.0 if h["is_onset"] else 0.0 for h in window), 3) if window else None
            mono_finalizes = [h for h in window if h["duration_hops"] is not None]
            rows.append({"kind": "mono", "label": seg["label"], "n_hops": len(window),
                         "is_onset_rate": onset_rate, "n_duration_finalize_events": len(mono_finalizes)})
        else:
            finalize_events = []
            for h in window:
                for n in h["note_stack"]:
                    if n.get("duration_hops") is not None:
                        finalize_events.append((h["t"], n["pitch_class"], n["octave"], n["duration_hops"]))
            rows.append({"kind": "chord", "label": seg["label"], "n_hops": len(window),
                         "n_duration_finalize_events": len(finalize_events),
                         "finalize_events_sample": finalize_events[:15]})
    return {"rows": rows}


# --------------------------------------------------------------------------
# Suite: rhythm -- live tempo-tracking convergence + duration-class snapping
# --------------------------------------------------------------------------

RHYTHM_TRUE_BPM = 100.0
RHYTHM_PULSE_ON_S = 0.30      # active tone per pulse
RHYTHM_PULSE_GAP_S = 0.30     # silence per pulse -- period = ON+GAP = 0.6s = one quarter note @ 100bpm
RHYTHM_N_PULSES = 40          # 24s total: several x TEMPO_HISTORY_SECONDS (8s) to reach convergence
RHYTHM_TEMPO_SETTLE_S = 10.0  # ignore bpm_estimate readings before this point (still converging)

# (beats, class-name) pairs to stress-test duration snapping at
# RHYTHM_TRUE_BPM -- includes the same standard values duration_tracker.py
# itself snaps to, down through the shortest (thirtysecond), which is
# expected to be at or past this pipeline's real hop-resolution limit
# (~23ms/hop at config.BLOCK_SIZE/SAMPLE_RATE) -- included deliberately as
# an informational stress point, not an assumed-passable case.
RHYTHM_DURATION_CLASSES = [
    (4.0, "whole"), (3.0, "dotted-half"), (2.0, "half"), (1.5, "dotted-quarter"),
    (1.0, "quarter"), (0.75, "dotted-eighth"), (0.5, "eighth"),
    (0.375, "dotted-sixteenth"), (0.25, "sixteenth"), (0.125, "thirtysecond"),
]
RHYTHM_DURATION_GAP_S = 0.35


def build_rhythm():
    beat_s = 60.0 / RHYTHM_TRUE_BPM

    # Part 1: isochronous single-pitch pulse train (clean periodic onsets,
    # no pitch changes) -- tests TempoTracker.update()'s convergence in
    # isolation from any note-identity confound.
    pulse_entries = []
    for i in range(RHYTHM_N_PULSES):
        pulse_entries.append(([("C", 4)], RHYTHM_PULSE_ON_S, {"kind": "tempo_pulse", "pulse_index": i}))
        pulse_entries.append(([], RHYTHM_PULSE_GAP_S, {"kind": "silence"}))
    pulse_audio, pulse_timeline, pulse_total = build_timed_audio(pulse_entries, gap_s=0.0, warmup_lead_s=1.5)

    # Part 2: single notes at each standard duration value, same BPM
    # (tempo_tracker's estimate should already be locked in from part 1,
    # continuous audio, no reset) -- tests duration_class_for_beats()
    # snapping against the live per-hop pipeline.
    dur_entries = []
    for beats, cls in RHYTHM_DURATION_CLASSES:
        dur_entries.append(([("C", 4)], beats * beat_s, {"kind": "duration_note", "expected_class": cls, "expected_beats": beats}))
    dur_audio, dur_timeline, dur_total = build_timed_audio(dur_entries, gap_s=RHYTHM_DURATION_GAP_S, warmup_lead_s=0.0)

    for entry in dur_timeline:
        entry["start"] += pulse_total
        entry["end"] += pulse_total

    audio = np.concatenate([pulse_audio, dur_audio])
    timeline = pulse_timeline + dur_timeline
    return audio, timeline, pulse_total + dur_total


def analyze_rhythm(log, timeline):
    tempo_segs = [s for s in timeline if s.get("kind") == "tempo_pulse"]
    dur_segs = [s for s in timeline if s.get("kind") == "duration_note"]

    # --- Tempo convergence ---
    converged = [h for h in log if h["t"] >= RHYTHM_TEMPO_SETTLE_S
                 and tempo_segs and h["t"] <= tempo_segs[-1]["end"]
                 and h["bpm_estimate"] is not None]
    estimates = [h["bpm_estimate"] for h in converged]
    if estimates:
        median_bpm = statistics.median(estimates)
        error_pct = round(abs(median_bpm - RHYTHM_TRUE_BPM) / RHYTHM_TRUE_BPM * 100, 1)
        octave_ratio = None
        for mult, label in ((2.0, "2x"), (0.5, "0.5x")):
            if abs(median_bpm - RHYTHM_TRUE_BPM * mult) / (RHYTHM_TRUE_BPM * mult) < 0.1:
                octave_ratio = label
    else:
        median_bpm, error_pct, octave_ratio = None, None, None
    tempo_result = {
        "true_bpm": RHYTHM_TRUE_BPM, "n_readings": len(estimates),
        "median_bpm_estimate": round(median_bpm, 1) if median_bpm is not None else None,
        "error_pct": error_pct, "octave_error": octave_ratio,
    }

    # --- Duration-class snapping ---
    # A mono duration_hops finalize event at log[i] belongs to the PREVIOUS
    # hop's (pitch_class, octave) -- main.py's own convention (see its
    # run_terminal_tab finalize handling) -- since the CURRENT hop has
    # already moved on (new note, or silence).
    finalize_events = []
    for i in range(1, len(log)):
        h = log[i]
        if h["duration_hops"] is None:
            continue
        prev = log[i - 1]
        if prev["pitch_class"] is None:
            continue
        beats = (h["duration_hops"] * HOP_SECONDS * h["bpm_estimate"] / 60.0) if h["bpm_estimate"] else None
        detected_class = duration_class_for_beats(beats)
        finalize_events.append({
            "t": h["t"], "pitch_class": prev["pitch_class"], "octave": prev["octave"],
            "beats": round(beats, 3) if beats is not None else None, "detected_class": detected_class,
        })

    dur_rows = []
    for seg in dur_segs:
        window_end = seg["end"] + LATENCY_SEARCH_SLOP_S + 0.2
        candidates = [e for e in finalize_events if seg["start"] < e["t"] <= window_end and e["pitch_class"] == 0]
        if candidates:
            ev = min(candidates, key=lambda e: abs(e["t"] - seg["end"]))
            dur_rows.append({
                "expected_class": seg["expected_class"], "expected_beats": seg["expected_beats"],
                "detected_class": ev["detected_class"], "detected_beats": ev["beats"],
                "correct": ev["detected_class"] == seg["expected_class"], "finalized": True,
            })
        else:
            dur_rows.append({
                "expected_class": seg["expected_class"], "expected_beats": seg["expected_beats"],
                "detected_class": None, "detected_beats": None, "correct": False, "finalized": False,
            })

    n = len(dur_rows)
    correct = sum(1 for r in dur_rows if r["correct"])
    finalized = sum(1 for r in dur_rows if r["finalized"])
    duration_result = {
        "n_classes": n,
        "finalize_rate": round(finalized / n, 3) if n else 0.0,
        "class_accuracy": round(correct / n, 3) if n else 0.0,
        "rows": dur_rows,
    }

    return {"tempo": tempo_result, "duration": duration_result}


# --------------------------------------------------------------------------
# Suite: noise robustness -- chromatic + chord sweep under additive noise
# --------------------------------------------------------------------------

NOISE_SNR_LEVELS = [("clean", 0.0), ("light", 0.05), ("moderate", 0.15)]  # noise amplitude relative to base_amp
NOISE_TEST_NOTES = [("C", 2), ("F#", 3), ("A", 4), ("D#", 5)]  # spot-check across the octave range, not exhaustive
NOISE_TEST_CHORDS = [(("C", 4), "maj"), (("A", 3), "min7")]


def _add_noise(audio, amplitude, seed, peak_cap=0.9):
    """Additive Gaussian noise at `amplitude` relative to the clean
    signal's own base_amp, then a final peak-renormalize (down only) to
    peak_cap -- synth_notes() already peak-caps the clean signal on its
    own, but stacking noise on top can still push instantaneous peaks
    past 1.0, which would clip on playback and confound "noise
    robustness" with "digital clipping distortion", a different and
    uncontrolled artifact. Renormalizing preserves the noise:signal
    ratio (both scaled together) while staying safely below clipping."""
    if amplitude <= 0:
        return audio
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(len(audio)).astype(np.float32) * amplitude
    out = audio + noise
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > peak_cap:
        out *= peak_cap / peak
    return out


def build_noise():
    entries_by_level = {}
    for level_name, amp in NOISE_SNR_LEVELS:
        entries = []
        for name, octave in NOISE_TEST_NOTES:
            entries.append(([(name, octave)], 1.4, {"kind": "note", "level": level_name,
                                                       "pitch_class": NOTE_INDEX[name], "octave": octave}))
        for root_octave, quality in NOISE_TEST_CHORDS:
            root_name, octave = root_octave
            notes, pcs, name = voice_chord(root_name, octave, quality)
            entries.append((notes, 1.8, {"kind": "chord", "level": level_name,
                                          "expected_pcs": sorted(pcs), "expected_name": name}))
        entries_by_level[level_name] = entries

    all_audio, all_timeline, t = [], [], 0.0
    warmup = 1.5
    for level_name, amp in NOISE_SNR_LEVELS:
        seg_audio, seg_timeline, seg_total = build_timed_audio(
            entries_by_level[level_name], gap_s=0.4, warmup_lead_s=(warmup if not all_audio else 0.5))
        seg_audio = _add_noise(seg_audio, amp, seed=hash(level_name) % (2**31))
        for entry in seg_timeline:
            entry["start"] += t
            entry["end"] += t
        all_audio.append(seg_audio)
        all_timeline.extend(seg_timeline)
        t += seg_total
    return np.concatenate(all_audio), all_timeline, t


def analyze_noise(log, timeline):
    by_level = {}
    for seg in timeline:
        level = seg["level"]
        by_level.setdefault(level, {"notes": [], "chords": []})
        if seg["kind"] == "note":
            expected = (seg["pitch_class"], seg["octave"])
            steady = [h for h in log if seg["start"] + 0.3 <= h["t"] <= seg["end"] - 0.1]
            correct = sum(1 for h in steady if (h["pitch_class"], h["octave"]) == expected)
            acc = correct / len(steady) if steady else 0.0
            by_level[level]["notes"].append(acc)
        else:
            steady = [h for h in log if seg["start"] + 0.5 <= h["t"] <= seg["end"] - 0.15]
            expected_pcs = set(seg["expected_pcs"])
            name_votes = {}
            for h in steady:
                name_votes[h["chord_name"]] = name_votes.get(h["chord_name"], 0) + 1
            top_name = max(name_votes.items(), key=lambda kv: kv[1])[0] if name_votes else None
            by_level[level]["chords"].append(1.0 if top_name == seg["expected_name"] else 0.0)

    summary = {}
    for level, data in by_level.items():
        summary[level] = {
            "mean_note_accuracy": round(statistics.mean(data["notes"]), 3) if data["notes"] else None,
            "chord_name_accuracy": round(statistics.mean(data["chords"]), 3) if data["chords"] else None,
        }
    return {"summary": summary}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

SUITES = {
    "chromatic": (build_chromatic, analyze_chromatic),
    "tempo": (build_tempo, analyze_tempo),
    "chords": (build_chords, analyze_chords),
    "density": (build_density, analyze_density),
    "sustain": (build_sustain, analyze_sustain),
    "rhythm": (build_rhythm, analyze_rhythm),
    "noise": (build_noise, analyze_noise),
}


def run_suites(names, outdir, sensitivity, source="loopback"):
    os.makedirs(outdir, exist_ok=True)
    session = main.SessionState(color_scheme="chromatic", sensitivity_value=sensitivity, source_value=source)
    ctx = muted_default_sink() if source == "loopback" else contextlib.nullcontext()
    results = {}
    with ctx:
        session.ensure_started()
        try:
            for name in names:
                build_fn, analyze_fn = SUITES[name]
                print(f"=== {name}: building audio ===")
                audio, timeline, total_runtime = build_fn()
                print(f"=== {name}: playing/recording ({total_runtime:.1f}s) ===")
                log = record_session(session, audio, total_runtime)
                with open(os.path.join(outdir, f"{name}_raw.json"), "w") as f:
                    json.dump({"timeline": timeline, "log": log}, f)
                result = analyze_fn(log, timeline)
                results[name] = result
                print(f"=== {name}: done, {len(log)} hops logged ===")
        finally:
            session.stop()
    return results


def load_and_analyze(names, outdir):
    results = {}
    for name in names:
        path = os.path.join(outdir, f"{name}_raw.json")
        if not os.path.exists(path):
            print(f"(skipping {name}: no {path})")
            continue
        with open(path) as f:
            data = json.load(f)
        _build_fn, analyze_fn = SUITES[name]
        results[name] = analyze_fn(data["log"], data["timeline"])
    return results


def write_report(results, outdir):
    lines = [f"# Acoustic pipeline test report", "", f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]

    if "chromatic" in results:
        s = results["chromatic"]["summary"]
        lines += ["## Chromatic monophonic accuracy + latency", "",
                   f"- Notes tested: {s['n_notes']}",
                   f"- Recall (ever correctly detected): {s['recall']*100:.1f}%",
                   f"- Attack-to-display latency: median {s['latency_ms_median']}ms, p90 {s['latency_ms_p90']}ms",
                   f"- Mean steady-state accuracy: {s['mean_steady_state_accuracy']*100:.1f}%", ""]
        if results["chromatic"]["misses"]:
            lines += [f"- Never detected: {', '.join(results['chromatic']['misses'])}", ""]
        if results["chromatic"]["low_accuracy"]:
            lines += [f"- Detected but unstable (<70% steady-state accuracy): {', '.join(results['chromatic']['low_accuracy'])}", ""]

    if "tempo" in results:
        lines += ["## Tempo / speed (legato scale, no gap between notes)", "",
                   "| BPM | note dur (ms) | recall | median latency (ms) |", "|---|---|---|---|"]
        for bpm, r in sorted(results["tempo"].items()):
            lines.append(f"| {bpm} | {r['note_duration_ms']} | {r['recall']*100:.0f}% | {r['latency_ms_median']} |")
        lines.append("")

    if "chords" in results:
        s = results["chords"]["summary"]
        lines += ["## Chord accuracy + phantom notes", "",
                   f"- Chords tested: {s['n_chords']}",
                   f"- Chord-name accuracy: {s['name_accuracy']*100:.1f}%",
                   f"- Mean phantom pitch classes/hop: {s['mean_phantom_pcs_per_hop']}",
                   f"- Mean missing pitch classes/hop: {s['mean_missing_pcs_per_hop']}", "",
                   "Worst phantom-rate chords:", "",
                   "| quality | root | register | expected | detected | phantom/hop |", "|---|---|---|---|---|---|"]
        for r in s["worst_phantom"]:
            lines.append(f"| {r['quality']} | {r['root']} | {r['register']} | {r['expected_name']} | "
                          f"{r.get('detected_name')} | {r['phantom_rate']} |")
        lines.append("")

    if "density" in results:
        lines += ["## Polyphonic density scaling", "",
                   "| notes played | mean phantom pcs/hop | mean missing pcs/hop | mean stack size |",
                   "|---|---|---|---|"]
        for count, r in sorted(results["density"]["summary"].items()):
            lines.append(f"| {count} | {r['mean_phantom']} | {r['mean_missing']} | {r['mean_stack_size_vs_expected']} |")
        lines.append("")

    if "sustain" in results:
        lines += ["## Long-hold onset misfire / duration fragmentation", "",
                   "| kind | note/chord | hops | is_onset rate (mono only) | duration-finalize events |",
                   "|---|---|---|---|---|"]
        for r in results["sustain"]["rows"]:
            lines.append(f"| {r['kind']} | {r['label']} | {r['n_hops']} | "
                          f"{r.get('is_onset_rate', '-')} | {r['n_duration_finalize_events']} |")
        lines.append("")

    if "rhythm" in results:
        t = results["rhythm"]["tempo"]
        d = results["rhythm"]["duration"]
        lines += ["## Rhythm: live tempo convergence + duration-class snapping", "",
                   f"- True BPM: {t['true_bpm']}, converged median estimate: {t['median_bpm_estimate']} "
                   f"({t['n_readings']} readings after settle time)",
                   f"- Tempo error: {t['error_pct']}%" + (f" (looks like {t['octave_error']} the true tempo)" if t['octave_error'] else ""),
                   f"- Duration-class finalize rate: {d['finalize_rate']*100:.1f}%, "
                   f"accuracy (of finalized): {d['class_accuracy']*100:.1f}%", "",
                   "| expected class | expected beats | finalized | detected class | detected beats | correct |",
                   "|---|---|---|---|---|---|"]
        for r in d["rows"]:
            lines.append(f"| {r['expected_class']} | {r['expected_beats']} | {r['finalized']} | "
                          f"{r['detected_class']} | {r['detected_beats']} | {r['correct']} |")
        lines.append("")

    if "noise" in results:
        lines += ["## Noise robustness (additive broadband noise)", "",
                   "| level | mean note accuracy | chord-name accuracy |", "|---|---|---|"]
        for level, r in results["noise"]["summary"].items():
            lines.append(f"| {level} | {r['mean_note_accuracy']} | {r['chord_name_accuracy']} |")
        lines.append("")

    report_path = os.path.join(outdir, "report.md")
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\nWrote {report_path}")
    print("\n".join(lines))


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suites", default=",".join(SUITES), help="comma-separated subset of: " + ",".join(SUITES))
    parser.add_argument("--outdir", default=None, help="default: acoustic_test_results/<timestamp>/")
    parser.add_argument("--sensitivity", type=float, default=config.DEFAULT_SENSITIVITY,
                         help="matches the app's own --sensitivity; default tests out-of-box tuning")
    parser.add_argument("--source", choices=("loopback", "mic"), default="loopback",
                         help="loopback (default): mutes the sink and round-trips via the output "
                              "monitor, no speaker/mic/quiet-room needed, runs unattended. "
                              "mic: the original physical speaker->mic round trip.")
    parser.add_argument("--report", metavar="OUTDIR", default=None,
                         help="re-analyze raw JSON logs already in OUTDIR instead of running live audio")
    args = parser.parse_args()

    names = [n.strip() for n in args.suites.split(",") if n.strip()]
    for n in names:
        if n not in SUITES:
            parser.error(f"unknown suite '{n}' -- choose from {','.join(SUITES)}")

    if args.report:
        results = load_and_analyze(names, args.report)
        write_report(results, args.report)
        return

    outdir = args.outdir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "acoustic_test_results", datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    results = run_suites(names, outdir, args.sensitivity, args.source)
    write_report(results, outdir)


if __name__ == "__main__":
    main_cli()
