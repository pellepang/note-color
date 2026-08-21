"""Turns raw multipitch note candidates into a stable displayed chord name
and note stack. Mirrors note_smoother.NoteSmoother's shape: rolling-average
+ debounce for the chord name (one shared stage for #5), asymmetric
attack/release hysteresis per note-slot for the note stack (#9) -- a
different failure mode than the chord-name's symmetric debounce, since the
flicker being fixed there is a single note briefly dipping below threshold
mid-chord, not a categorical chord-identity change.

Chord-name matching builds its own main/bass chroma vectors from
`note_candidates` (multipitch.detect()'s already harmonic-pruned output)
rather than from the caller's raw `chroma.fold()`/`fold_bass()` vectors --
see `_update_chord_name`'s docstring/comments (issue #56) for why the raw
harmonic-summed spectrum systematically over-called extended/slash chords.
"""

from collections import deque

import numpy as np

import chord_templates
from chroma import DEFAULT_BASS_CUTOFF_HZ


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

    def update(self, _main_chroma, _bass_chroma, note_candidates):
        """One hop update. Returns (chord_name, note_stack).
        `chord_name` is a formatted chord name string, or None (either
        "no match" or not yet debounced into a change). `note_stack` is a
        list of dicts: {pitch_class, octave, confidence, is_bass} for
        every currently-active note-stack slot, lowest note first.

        `_main_chroma`/`_bass_chroma` are accepted-but-unused: kept so
        this stays a drop-in call for main.py/batch_transcribe.py, both
        of which still need their own `chroma.fold()`/`fold_bass()`
        result for tempo/onset novelty tracking regardless of what
        chord-name matching does with it -- see `_update_chord_name` for
        why chord-name matching itself now ignores both in favor of
        `note_candidates`."""
        chord_name = self._update_chord_name(note_candidates)
        stack = self._update_note_stack(note_candidates)
        return chord_name, stack

    def _update_chord_name(self, note_candidates):
        # Chord-name matching used to run directly against chroma.fold()'s
        # raw harmonic-summed spectrum energy (the caller's main_chroma).
        # fold()'s harmonic summing is tuned to recover a single note's
        # *own* overtones back into its own pitch-class bin, but it has no
        # way to tell that a *different*, simultaneously-sounding note's
        # 3rd/4th harmonic landing on a third, unplayed pitch class isn't a
        # real chord tone -- e.g. in a C major triad, E's 3rd harmonic is a
        # B and G's 3rd harmonic is a D, so the raw chroma vector reads as
        # a 5-note chord even though only C-E-G is sounding. That
        # systematically biased cosine-similarity matching toward larger
        # extended/slash-chord templates (issue #56: ~45% mismatch across
        # all 360 templates under realistic harmonic content).
        #
        # multipitch.detect() already solves exactly this problem for
        # individual note identification, via its own Hann-windowed FFT
        # and harmonic-consistency pruning -- reusing its output here
        # (one-hot per detected pitch class, weighted by confidence)
        # avoids re-solving the same problem worse a second time. Measured
        # after this change: 10/360 genuine mismatches (chords denser than
        # CHORD_MAX_NOTES, an already-documented separate cap), the rest
        # were resolved; the remaining ~80 apparent "mismatches" in the
        # issue #56 sweep turned out to be chords that are genuinely
        # pitch-class-set-identical to a different valid name (augmented
        # triads, diminished 7ths, sus2/sus4 pairs) -- the same kind of
        # inherent, undecidable-from-pitch-classes-alone ambiguity this
        # file's docs already call out for minor7/major6.
        main_chroma = np.zeros(12)
        for nc in note_candidates:
            main_chroma[nc.pitch_class] = max(main_chroma[nc.pitch_class], nc.confidence)

        # Bass detection has the same raw-energy-sum problem as the main
        # chroma did (see above): chroma.fold_bass() sums every pitch
        # class's harmonic energy below DEFAULT_BASS_CUTOFF_HZ with no
        # per-note pruning, so a close-position chord voiced entirely
        # under that cutoff (root, third, and fifth all sub-250Hz) blurs
        # together and its raw peak isn't reliably the actual bass note.
        # The lowest of multipitch's own already-pruned note candidates
        # -- if it's genuinely down in the bass register -- is a more
        # trustworthy single signal than that raw low-passed sum.
        bass_chroma = np.zeros(12)
        bass_candidates = [nc for nc in note_candidates if nc.freq < DEFAULT_BASS_CUTOFF_HZ]
        if bass_candidates:
            lowest = min(bass_candidates, key=lambda nc: nc.freq)
            bass_chroma[lowest.pitch_class] = lowest.confidence

        self.chroma_history.append(main_chroma)
        self.bass_chroma_history.append(bass_chroma)
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
        # More than max_notes slots can be simultaneously active during a
        # chord change: outgoing notes stay "active" through their release
        # hysteresis while incoming notes are already ramping up, so the
        # overlap can briefly exceed max_notes. Trim by confidence (not
        # pitch) so the freshest/loudest notes -- almost always the
        # just-attacked ones, since a releasing note's confidence is a
        # stale, decaying reading from its last real detection -- win the
        # slots, instead of always keeping whichever notes happen to sit
        # lowest in pitch and silently hiding a brand-new chord until the
        # old one's release window fully times out.
        active.sort(key=lambda entry: (entry["confidence"], entry["freq"]), reverse=True)
        active = active[: self.max_notes]
        active.sort(key=lambda entry: entry["freq"])

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
