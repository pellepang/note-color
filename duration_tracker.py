"""Per-note duration measurement, live (causal) and batch (non-causal)
(issue #55).

Mirrors chord_smoother.ChordSmoother's note_states dict-of-state pattern
directly -- same (pitch_class, octave) keying, same per-slot dict-of-state
shape, since it's tracking the same kind of per-note lifecycle. This is a
distinct mechanism from ChordSmoother's attack/release hysteresis, though:
that hysteresis exists to prevent *display* flicker; DurationTracker
exists to *measure* how long a note actually sounded.
"""

import numpy as np

# (beats, name) pairs used by duration_class_for_beats() to snap a
# measured duration to the nearest standard note value. Dotted values are
# 1.5x their plain counterpart, standard Western-notation convention.
_DURATION_CLASSES = [
    (4.0, "whole"),
    (3.0, "dotted-half"),
    (2.0, "half"),
    (1.5, "dotted-quarter"),
    (1.0, "quarter"),
    (0.75, "dotted-eighth"),
    (0.5, "eighth"),
    (0.375, "dotted-sixteenth"),
    (0.25, "sixteenth"),
    (0.125, "thirtysecond"),
]

# Fallback for a note still sounding when the process quits -- it never
# crosses its decay threshold, so never gets a finalized duration_class.
# A small, explicitly-accepted edge case (#55), not worth a separate ticket.
DEFAULT_DURATION_CLASS = "quarter"

# Public, importable-without-the-underscore ordering of duration_class
# names, longest to shortest -- issue #98's score_editor_state.py cycles a
# column's duration_class through this list (','/'.' duration_shorten/
# lengthen), and score_writer.py's own quarter-length lookup shares the
# same name set. Derived from _DURATION_CLASSES itself rather than
# hand-duplicated, so the two can never drift apart.
DURATION_CLASS_ORDER = [name for _, name in _DURATION_CLASSES]

# The inverse of duration_class_for_beats(): a duration_class name -> how
# many beats (quarter notes) it stands for. Derived from
# _DURATION_CLASSES itself for the same reason DURATION_CLASS_ORDER is --
# so the two can never drift. `tab_playback.py` (ticket #121) needs it to
# turn an already-finalized note's duration_class back into real seconds
# against a tempo; `score_audition.py` (ticket #120) needs the same thing
# for the score editor's own playback. score_writer.QUARTER_LENGTHS is
# the same table but lives behind a music21 import, which no live-path
# module may pay for.
BEATS_BY_DURATION_CLASS = {name: beats for beats, name in _DURATION_CLASSES}


def beats_for_duration_class(name):
    """Length in beats of a standard duration class name, with
    DEFAULT_DURATION_CLASS's own length as the fallback for an unknown or
    None name -- the same "never raise on a duration lookup" posture
    duration_class_for_beats() already takes for a missing measurement.
    Both playback paths (tab_playback.note_duration_seconds(),
    score_audition.build_schedule()) go through this rather than reaching
    into BEATS_BY_DURATION_CLASS with their own fallback expression."""
    if name is None:
        name = DEFAULT_DURATION_CLASS
    return BEATS_BY_DURATION_CLASS.get(name, BEATS_BY_DURATION_CLASS[DEFAULT_DURATION_CLASS])


def duration_class_for_beats(beats):
    """Nearest standard note-value name (including dotted variants) for a
    measured duration in beats. None or non-positive input (no live
    bpm_estimate was available yet when the duration finalized) falls
    back to DEFAULT_DURATION_CLASS rather than raising or guessing."""
    if beats is None or beats <= 0:
        return DEFAULT_DURATION_CLASS
    return min(_DURATION_CLASSES, key=lambda pair: abs(pair[0] - beats))[1]


class DurationTracker:
    def __init__(self, cfg, require_onset_for_new_note=False):
        self.decay_ratio = cfg.DURATION_DECAY_RATIO
        # Issue #70: mono's caller (NoteSmoother-driven) always carries a
        # trustworthy is_onset -- a key with no existing state should only
        # ever start a NEW tracked note when is_onset is True for it.
        # Without this gate, a key whose state was just finalized (decay
        # or absence) this same hop, but which the caller still happens to
        # report present-with-is_onset=False on a LATER hop (mono's
        # concrete case: NoteSmoother echoing the just-finalized pitch for
        # SILENCE_HOPS-1 more hops before it actually reports silence, its
        # own deliberate grace period against display flicker -- see
        # note_smoother.py), gets misread as a brand-new note attack,
        # producing a spurious ~1-hop "ghost" note once that echo itself
        # goes absent. Chord mode has no reliable per-note onset signal
        # (see main.py's chord_duration_tracker wiring) and intentionally
        # always passes is_onset=False even for genuinely new notes, so it
        # must keep this off (default) and rely on appear/absence alone.
        self.require_onset_for_new_note = require_onset_for_new_note
        # key -> {"onset_hop": int, "peak_magnitude": float, "last_magnitude": float}
        self.states = {}

    def update(self, notes, hop_index, onset_backdate=0):
        """One hop update. `notes` is an iterable of
        (pitch_class, octave, magnitude, is_onset) for the notes sounding
        THIS hop -- monophonic callers pass at most one entry
        (magnitude=rms); chord-mode callers pass one entry per raw
        multipitch.detect() NoteCandidate (magnitude=confidence).
        `is_onset=True` marks a fresh attack rather than a continuation of
        whatever was already sounding at that key -- this is what lets a
        quick repeated note be distinguished from one held note (#55's
        story 3): a same-key re-onset finalizes the old slot immediately
        (as if it had just decayed) before a brand-new slot starts,
        instead of silently continuing to accumulate the old one.

        `onset_backdate` (issue #70): hops to subtract from `hop_index`
        when stamping a brand-new state's onset_hop this call -- lets a
        caller with known, fixed onset-detection latency (mono's
        NoteSmoother debounce delay -- see note_smoother.py's
        onset_backdate_hops) correct for it at the source rather than
        every downstream duration measurement carrying the same
        systematic undercount. Applies uniformly to every note opened
        this call; mono only ever opens at most one, so this is a single
        scalar rather than a per-note field.

        Returns a list of (pitch_class, octave, duration_hops) for every
        slot that finalized this hop: its current/peak magnitude ratio
        dropped to/below decay_ratio, its key was simply absent from
        `notes` this hop after having been active, or it was preempted by
        a same-key re-onset as above."""
        finalized = []
        seen = set()

        for pitch_class, octave, magnitude, is_onset in notes:
            key = (pitch_class, octave)
            seen.add(key)
            state = self.states.get(key)

            if state is not None and is_onset:
                finalized.append((pitch_class, octave, hop_index - state["onset_hop"]))
                state = None

            if state is None:
                if self.require_onset_for_new_note and not is_onset:
                    continue
                self.states[key] = {
                    "onset_hop": hop_index - onset_backdate,
                    "peak_magnitude": magnitude,
                    "last_magnitude": magnitude,
                }
                continue

            state["peak_magnitude"] = max(state["peak_magnitude"], magnitude)
            state["last_magnitude"] = magnitude
            ratio = magnitude / state["peak_magnitude"] if state["peak_magnitude"] > 0 else 0.0
            if ratio <= self.decay_ratio:
                finalized.append((pitch_class, octave, hop_index - state["onset_hop"]))
                del self.states[key]

        for key in [k for k in self.states if k not in seen]:
            state = self.states.pop(key)
            finalized.append((key[0], key[1], hop_index - state["onset_hop"]))

        return finalized

    @staticmethod
    def finalize_noncausal(magnitude_history, onset_indices, decay_ratio, smooth_window=5):
        """Batch's non-causal refinement of the same off-threshold
        definition update() uses, applied to one note-slot's full
        magnitude-over-time history at once. `magnitude_history`: 1D
        array/sequence, one magnitude value per hop, covering that slot's
        entire lifetime in the recording. `onset_indices`: sorted hop
        indices (into magnitude_history) where a new onset was detected
        for this slot.

        Before threshold-crossing detection runs, the envelope is
        smoothed with a small centered window (uses both past and future
        samples -- batch has the whole array up front, unlike the causal
        update() path) to reduce false-early/false-late boundary calls
        from a single noisy dip a live causal pass can't see past. Returns
        a list of (onset_index, duration_hops), one entry per
        onset_index, in the same order given."""
        history = np.asarray(magnitude_history, dtype=np.float64)
        n = len(history)
        if n == 0 or not onset_indices:
            return [(idx, 0) for idx in onset_indices]

        if smooth_window > 1:
            kernel = np.ones(smooth_window) / smooth_window
            padded = np.pad(history, smooth_window // 2, mode="edge")
            smoothed = np.convolve(padded, kernel, mode="valid")[:n]
        else:
            smoothed = history

        results = []
        sorted_onsets = sorted(onset_indices)
        for i, onset in enumerate(sorted_onsets):
            segment_end = sorted_onsets[i + 1] if i + 1 < len(sorted_onsets) else n
            segment = smoothed[onset:segment_end]
            if len(segment) == 0:
                results.append((onset, 0))
                continue
            peak = float(np.max(segment))
            duration_hops = len(segment)  # default: sounds through the whole segment
            if peak > 0:
                ratios = segment / peak
                below = np.nonzero(ratios <= decay_ratio)[0]
                if len(below):
                    duration_hops = int(below[0])
            results.append((onset, duration_hops))
        return results
