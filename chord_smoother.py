"""Turns noisy per-hop chroma vectors and raw multipitch note candidates
into a stable displayed chord name and note stack. Mirrors
note_smoother.NoteSmoother's shape: rolling-average + debounce for the
chord name (one shared stage for #5), asymmetric attack/release hysteresis
per note-slot for the note stack (#9) -- a different failure mode than the
chord-name's symmetric debounce, since the flicker being fixed there is a
single note briefly dipping below threshold mid-chord, not a categorical
chord-identity change.
"""

from collections import deque

import numpy as np

import chord_templates


class ChordSmoother:
    def __init__(self, cfg):
        self.median_window = cfg.CHORD_MEDIAN_WINDOW
        self.debounce_hops = cfg.CHORD_DEBOUNCE_HOPS
        self.match_threshold = cfg.CHORD_MATCH_THRESHOLD
        self.attack_hops = cfg.NOTE_STACK_ATTACK_HOPS
        self.release_hops = cfg.NOTE_STACK_RELEASE_HOPS
        self.max_notes = cfg.CHORD_MAX_NOTES

        self.chroma_history = deque(maxlen=self.median_window)
        self.bass_chroma_history = deque(maxlen=self.median_window)
        self.candidate_name = None
        self.candidate_count = 0
        self.current_chord_name = None

        self.note_states = {}

    def update(self, main_chroma, bass_chroma, note_candidates):
        """One hop update. Returns (chord_name, note_stack).
        `chord_name` is a formatted chord name string, or None (either
        "no match" or not yet debounced into a change). `note_stack` is a
        list of dicts: {pitch_class, octave, confidence, is_bass} for
        every currently-active note-stack slot, lowest note first."""
        chord_name = self._update_chord_name(main_chroma, bass_chroma)
        stack = self._update_note_stack(note_candidates)
        return chord_name, stack

    def _update_chord_name(self, main_chroma, bass_chroma):
        self.chroma_history.append(np.asarray(main_chroma, dtype=np.float64))
        self.bass_chroma_history.append(np.asarray(bass_chroma, dtype=np.float64))
        avg_chroma = np.mean(self.chroma_history, axis=0)
        avg_bass_chroma = np.mean(self.bass_chroma_history, axis=0)

        result = chord_templates.match(avg_chroma, avg_bass_chroma, threshold=self.match_threshold)
        candidate = result.name if result is not None else None

        if candidate == self.candidate_name:
            self.candidate_count += 1
        else:
            self.candidate_name = candidate
            self.candidate_count = 1

        if self.candidate_count >= self.debounce_hops:
            self.current_chord_name = candidate

        return self.current_chord_name

    def _update_note_stack(self, note_candidates):
        detected = {(nc.pitch_class, nc.octave): nc for nc in note_candidates}
        keys = set(self.note_states.keys()) | set(detected.keys())

        for key in keys:
            state = self.note_states.setdefault(
                key, {"active": False, "on_streak": 0, "off_streak": 0, "confidence": 0.0, "freq": 0.0}
            )
            if key in detected:
                nc = detected[key]
                state["on_streak"] += 1
                state["off_streak"] = 0
                state["confidence"] = nc.confidence
                state["freq"] = nc.freq
                if not state["active"] and state["on_streak"] >= self.attack_hops:
                    state["active"] = True
            else:
                state["off_streak"] += 1
                state["on_streak"] = 0
                if state["active"] and state["off_streak"] >= self.release_hops:
                    state["active"] = False

        # Drop states that are inactive and have been silent long enough
        # that they'd never re-attack from stale on_streak bookkeeping.
        self.note_states = {
            key: state
            for key, state in self.note_states.items()
            if state["active"] or state["off_streak"] < self.release_hops
        }

        active = [
            {"pitch_class": key[0], "octave": key[1], "confidence": state["confidence"], "freq": state["freq"]}
            for key, state in self.note_states.items()
            if state["active"]
        ]
        active.sort(key=lambda entry: entry["freq"])
        active = active[: self.max_notes]

        stack = []
        for i, entry in enumerate(active):
            stack.append(
                {
                    "pitch_class": entry["pitch_class"],
                    "octave": entry["octave"],
                    "confidence": entry["confidence"],
                    "is_bass": i == 0,
                }
            )
        return stack
