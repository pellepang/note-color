"""Scoring transcription output against known ground truth (issue #132).

Implements the metric triple #132 settled on, against `mir_eval`'s
note-matching. The headline is deliberately **not** F1.

Why not F1: it weights a missed note and a hallucinated one equally, and
for output a human corrects in this repo's own score editor they are not
equal. A missing note surfaces during the listening pass that was
happening anyway and costs one keystroke to place. A wrong note has to be
*found* first, which means reading every bar of every track against the
recording. Ycart et al. (TISMIR 2020, 4,501 ratings over 1,552 excerpts)
measured F-measure disagreeing with human judgement nearly **40% of the
time** when two transcriptions sit within 10% F1 of each other -- which
is exactly the regime this project would be choosing backends in.

So three numbers, reported together and never averaged:

1. **F0.5** -- precision weighted twice as heavily as recall. Beta is a
   *stated editorial choice, not a measurement*: this project holds a
   ghost note to cost about twice a miss. If a real editing session says
   otherwise, beta moves.
2. **Ghost rate and miss rate per bar, separately.** Per bar because that
   is the unit a human scans; a 2-note-per-bar ballad and a
   16-note-per-bar riff at equal F1 are not equal work.
3. **Editor operations** -- see `editor_operations()`.
"""

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Scores:
    precision: float
    recall: float
    f1: float
    f_half: float
    matched: int
    ghosts: int
    misses: int
    reference_count: int
    estimate_count: int
    bars: float = 0.0

    @property
    def ghost_rate_per_bar(self):
        return self.ghosts / self.bars if self.bars else float("nan")

    @property
    def miss_rate_per_bar(self):
        return self.misses / self.bars if self.bars else float("nan")

    def summary(self):
        return (
            f"F0.5 {self.f_half:.3f}  (P {self.precision:.3f} / R {self.recall:.3f}, "
            f"F1 {self.f1:.3f})\n"
            f"  ghosts {self.ghosts:3d} = {self.ghost_rate_per_bar:.2f}/bar   "
            f"misses {self.misses:3d} = {self.miss_rate_per_bar:.2f}/bar   "
            f"matched {self.matched}/{self.reference_count}"
        )


def f_beta(precision, recall, beta=0.5):
    """The van Rijsbergen F-measure. beta<1 favours precision."""
    if precision <= 0 and recall <= 0:
        return 0.0
    b2 = beta * beta
    denominator = b2 * precision + recall
    return 0.0 if denominator <= 0 else (1 + b2) * precision * recall / denominator


def score_notes(reference, estimate, onset_tolerance=0.05, pitch_tolerance=0.5, bars=0.0):
    """Score estimated notes against reference notes.

    Both are iterables of `(onset_seconds, offset_seconds, pitch_midi)`.
    Matching is `mir_eval.transcription.match_notes` at its documented
    defaults with `offset_ratio=None` -- onset-and-pitch only, which is
    the convention every published number in #124 used, so a number here
    is comparable rather than merely internally consistent.
    """
    from mir_eval.transcription import match_notes

    reference = list(reference)
    estimate = list(estimate)
    if not reference or not estimate:
        matched = 0
        precision = 0.0 if estimate else (1.0 if not reference else 0.0)
        recall = 0.0 if reference else 1.0
        return Scores(precision, recall, 0.0, 0.0, matched,
                      len(estimate), len(reference), len(reference), len(estimate), bars)

    ref_intervals = np.array([[r[0], max(r[1], r[0] + 1e-4)] for r in reference])
    ref_pitches = np.array([_midi_to_hz(r[2]) for r in reference])
    est_intervals = np.array([[e[0], max(e[1], e[0] + 1e-4)] for e in estimate])
    est_pitches = np.array([_midi_to_hz(e[2]) for e in estimate])

    matching = match_notes(
        ref_intervals, ref_pitches, est_intervals, est_pitches,
        onset_tolerance=onset_tolerance, pitch_tolerance=pitch_tolerance,
        offset_ratio=None,
    )
    matched = len(matching)
    precision = matched / len(estimate)
    recall = matched / len(reference)
    return Scores(
        precision=precision,
        recall=recall,
        f1=f_beta(precision, recall, beta=1.0),
        f_half=f_beta(precision, recall, beta=0.5),
        matched=matched,
        ghosts=len(estimate) - matched,
        misses=len(reference) - matched,
        reference_count=len(reference),
        estimate_count=len(estimate),
        bars=bars,
    )


def _midi_to_hz(midi):
    return 440.0 * 2.0 ** ((float(midi) - 69.0) / 12.0)


def editor_operations(reference, estimate, onset_tolerance=0.05):
    """Minimum editor keystrokes to turn `estimate` into `reference`.

    The metric only this project can compute, and #132's product-level
    bar is stated in it: correcting a converted score must cost fewer
    than half the operations of entering it from scratch.

    Counted in `score_editor_display.py`'s own closed mutation set, which
    is what makes it a real measure of effort rather than a proxy:

      * a note at the right time but the wrong pitch  -> 1 transpose
      * a note that should not be there               -> 1 note_toggle
      * a note that is missing                        -> 1 note_toggle
      * entering one from scratch                     -> 1 note_toggle

    Deliberately ignores duration for now. `cycle_duration` is one press
    per step through DURATION_CLASS_ORDER, so a wrong duration costs
    between one and nine, and counting it honestly needs the durations to
    have been quantized against the same grid -- which is true of a
    written score and not of raw model output. Recorded as a gap rather
    than approximated, since an approximated keystroke count would be
    exactly the kind of number that gets quoted as if measured.
    """
    reference = sorted(reference, key=lambda n: (n[0], n[2]))
    estimate = sorted(estimate, key=lambda n: (n[0], n[2]))
    used = [False] * len(estimate)
    operations = 0
    matched_exact = 0
    transposed = 0

    for ref_onset, _ref_offset, ref_pitch in reference:
        best, best_delta = None, None
        for index, (est_onset, _o, est_pitch) in enumerate(estimate):
            if used[index] or abs(est_onset - ref_onset) > onset_tolerance:
                continue
            delta = abs(est_pitch - ref_pitch)
            if best_delta is None or delta < best_delta:
                best, best_delta = index, delta
        if best is None:
            operations += 1          # place a missing note
            continue
        used[best] = True
        if best_delta == 0:
            matched_exact += 1
        else:
            operations += 1          # transpose it into place
            transposed += 1

    ghosts = used.count(False)
    operations += ghosts             # remove each spurious note
    from_scratch = len(reference)
    return {
        "operations": operations,
        "from_scratch": from_scratch,
        "ratio": operations / from_scratch if from_scratch else float("nan"),
        "exact": matched_exact,
        "transposed": transposed,
        "removed": ghosts,
    }


def truth_notes(track):
    """A `synth_corpus.SynthTrack`'s notes as
    `(onset_seconds, offset_seconds, pitch_midi)` -- the reference side."""
    seconds_per_beat = 60.0 / track.tempo_bpm
    notes = []
    for part in track.parts:
        for onset_b, pitch_class, octave, duration_b, _velocity in part.notes:
            onset = onset_b * seconds_per_beat
            notes.append((onset, onset + duration_b * seconds_per_beat,
                          (octave + 1) * 12 + pitch_class))
    return sorted(notes)
