"""Pure non-causal rhythm recompute for the `tab` view's `R`-key feature
(issue #77): re-runs `duration_tracker.DurationTracker.finalize_noncausal()`
and `librosa.beat.beat_track()` -- the same non-causal machinery
`batch_transcribe.py` already uses offline -- against a rolling *live*
buffer of cheap per-hop derived values instead of a fully preloaded
recording. See `docs/research/live-noncausal-rhythm-reanalysis.md` for the
feasibility research this implements; its findings (derived per-hop
values suffice, no raw audio needed; `chroma_flux()`'s novelty signal
already matches librosa's own default `hop_length`/`sr`; neither target
function has a whole-file precondition) are assumed here, not re-derived.

`recompute()` is the pure function this module exists for -- given a
snapshot of buffered `HopRecord`s (oldest first, as `main.py`'s
`ReanalysisBuffer.snapshot()` returns them), it returns corrected per-note
duration classes, corrected barline timestamps, and a corrected tempo
estimate, with no side effects and no thread/queue awareness of its own --
`main.py`'s `_handle_reanalysis_key()` is what runs this off a throwaway
thread and hands the result back to the render loop.

This is the one deliberate exception to this codebase's "librosa lives
only in batch_transcribe.py" rule (see CLAUDE.md's Key design decisions):
`recompute()` is triggered live (the `R` key, while frozen) but never runs
on `analysis_loop()`'s own per-hop path -- only on a throwaway thread, at
explicit user request, mirroring batch's already-accepted offline use of
librosa rather than adding a second one.

Reconstructing per-key magnitude/onset arrays from a flat HopRecord
sequence mirrors `batch_transcribe.transcribe()`'s own per-hop loop
(`_finalize_events`) almost exactly -- the one structural difference is
that a HopRecord's `hop_index` isn't guaranteed contiguous or 0-based (the
buffer only ever holds however many recent hops fit in the configured
window), so onset/duration results are mapped back to real timestamps via
each HopRecord's own `hop_index`, not the local array position."""

from collections import namedtuple

import librosa
import numpy as np

import config
from duration_tracker import DurationTracker, duration_class_for_beats

# One hop's worth of cheap, already-computed derived values -- exactly what
# finalize_noncausal()/beat_track() need (see the module docstring), not
# raw audio. `mono`: (pitch_class, octave, rms, is_onset) or None when no
# monophonic note was sounding that hop. `chord_notes`: a tuple of
# (pitch_class, octave, confidence) tuples, mirroring chord_smoother's
# already-debounced raw_stack (analysis_loop() feeds chord_duration_tracker
# from the same source -- see main.py). `chroma_novelty`: the scalar
# chroma_flux() novelty value for this hop, fed to librosa.beat.beat_track()
# as onset_envelope=.
HopRecord = namedtuple("HopRecord", ["hop_index", "mono", "chord_notes", "chroma_novelty"])

# One corrected note: identifies the existing displayed note to overwrite
# via TabDisplay.correct_duration()'s closest-timestamp matching.
CorrectedNote = namedtuple("CorrectedNote", ["pitch_class", "octave", "onset_time", "duration_class"])

# `barline_times`: corrected barline column timestamps within the recomputed
# window (already walked through the beat-accumulator below, not raw beat
# times from librosa). `bpm_estimate`: None if no tempo estimate could be
# produced (e.g. no novelty energy at all in the window) -- callers should
# treat that as "recompute couldn't do better than the live estimate" and
# leave any existing barlines in the window alone rather than erasing them
# with nothing to replace them (see main.py's _handle_reanalysis_key
# docstring). `window_start_time`/`window_end_time`: the real timestamps
# (hop_index * hop_seconds) of the first/last buffered hop -- the range a
# caller should erase_barlines() over before inserting corrected ones.
RecomputeResult = namedtuple(
    "RecomputeResult",
    ["corrected_notes", "barline_times", "bpm_estimate", "window_start_time", "window_end_time"],
)


def recompute(hop_records, hop_seconds, beats_per_bar, decay_ratio=None):
    """Pure function: given `hop_records` (a list of HopRecord, oldest
    first -- typically ReanalysisBuffer.snapshot()'s return value), returns
    a RecomputeResult. Returns None if `hop_records` is empty (nothing
    buffered yet, e.g. R pressed before any hop was ever appended) -- a
    real, non-error "there's nothing to reanalyze" case a caller should
    treat as a no-op, not as "reanalysis found zero corrections"."""
    if not hop_records:
        return None

    decay_ratio = config.DURATION_DECAY_RATIO if decay_ratio is None else decay_ratio
    n = len(hop_records)
    window_start_time = hop_records[0].hop_index * hop_seconds
    window_end_time = hop_records[-1].hop_index * hop_seconds

    mono_magnitude, mono_onsets = {}, {}
    chord_magnitude, chord_onsets = {}, {}
    novelty = np.zeros(n, dtype=np.float64)
    prev_chord_keys = set()

    for i, record in enumerate(hop_records):
        novelty[i] = record.chroma_novelty or 0.0

        if record.mono is not None:
            pitch_class, octave, rms, is_onset = record.mono
            key = (pitch_class, octave)
            mono_magnitude.setdefault(key, np.zeros(n))[i] = rms
            onset_list = mono_onsets.setdefault(key, [])
            if is_onset:
                onset_list.append(i)

        active_keys = set()
        for pitch_class, octave, confidence in record.chord_notes:
            key = (pitch_class, octave)
            active_keys.add(key)
            chord_magnitude.setdefault(key, np.zeros(n))[i] = confidence
            onset_list = chord_onsets.setdefault(key, [])
            # A key only counts as a fresh onset when it reappears after
            # being absent from the *previous* hop's stack -- multipitch
            # has no persistent per-note identity across hops to detect a
            # genuine re-attack mid-sustain, same bounded scope-narrowing
            # analysis_loop()'s live chord_duration_tracker wiring already
            # documents (and batch_transcribe.transcribe()'s own
            # prev_chord_keys tracking already implements identically).
            if key not in prev_chord_keys:
                onset_list.append(i)
        prev_chord_keys = active_keys

    bpm_estimate = _estimate_tempo(novelty, hop_seconds)

    mono_events = _finalize_events(mono_magnitude, mono_onsets, hop_records, hop_seconds, decay_ratio)
    chord_events = _finalize_events(chord_magnitude, chord_onsets, hop_records, hop_seconds, decay_ratio)

    corrected_notes = []
    beats_by_hop = {}
    for pitch_class, octave, onset_hop, onset_time, duration_hops in mono_events + chord_events:
        beats = (duration_hops * hop_seconds * bpm_estimate / 60.0) if bpm_estimate else None
        duration_class = duration_class_for_beats(beats)
        corrected_notes.append(CorrectedNote(pitch_class, octave, onset_time, duration_class))
        # Same max-across-trackers convention issue #76 fixed live
        # (main.py's run_terminal_tab) -- mono and chord duration tracking
        # both always run and routinely finalize the same underlying
        # acoustic event, so summing both into one beat position would
        # double-count it again here.
        beats_by_hop[onset_hop] = max(beats_by_hop.get(onset_hop, 0.0), beats or 0.0)

    barline_times = _barline_times(beats_by_hop, beats_per_bar, hop_seconds) if bpm_estimate is not None else []

    return RecomputeResult(corrected_notes, barline_times, bpm_estimate, window_start_time, window_end_time)


def _finalize_events(magnitude_by_key, onsets_by_key, hop_records, hop_seconds, decay_ratio):
    """Mirrors batch_transcribe.py's _finalize_events(), but maps each
    local onset_index back to its real (hop_index, onset_time) via
    hop_records, since a rolling buffer's local array positions aren't
    themselves real hop indices the way batch's whole-recording arrays
    are."""
    events = []
    for key, magnitude_history in magnitude_by_key.items():
        pitch_class, octave = key
        onset_indices = onsets_by_key.get(key, [])
        if not onset_indices:
            continue  # no known onset for this key within the window -- can't place a corrected duration
        pairs = DurationTracker.finalize_noncausal(magnitude_history, onset_indices, decay_ratio)
        for onset_index, duration_hops in pairs:
            onset_hop = hop_records[onset_index].hop_index
            onset_time = onset_hop * hop_seconds
            events.append((pitch_class, octave, onset_hop, onset_time, duration_hops))
    return events


def _estimate_tempo(novelty, hop_seconds):
    """librosa.beat.beat_track() fed the buffered chroma_flux() novelty
    history directly as onset_envelope= -- no raw audio needed (see the
    module docstring/research doc). None if there's no novelty energy at
    all (a silent/near-silent window) or beat_track() itself can't produce
    an estimate, same "no error, just no answer" convention
    batch_transcribe._estimate_bpm() already uses."""
    if not np.any(novelty):
        return None
    try:
        tempo, _beat_frames = librosa.beat.beat_track(
            onset_envelope=novelty, sr=config.SAMPLE_RATE, hop_length=config.BLOCK_SIZE
        )
    except Exception:
        return None
    tempo = np.ravel(tempo)
    if tempo.size == 0:
        return None
    return float(tempo[0])


def _barline_times(beats_by_hop, beats_per_bar, hop_seconds):
    """Walks corrected note events in onset order, accumulating beats the
    same way main.py's live beat-accumulator (and run_batch_transcribe()'s
    offline one) do, emitting a barline timestamp each time beats_per_bar
    is crossed. A `while`, not an `if`, for the same reason as both of
    those: a hop that crosses more than one bar boundary at once shouldn't
    lose barlines, and the remainder (not a reset to zero) is what carries
    forward to avoid compounding drift."""
    barlines = []
    beats_accumulated = 0.0
    for onset_hop in sorted(beats_by_hop):
        beats_accumulated += beats_by_hop[onset_hop]
        while beats_accumulated >= beats_per_bar:
            barlines.append(onset_hop * hop_seconds)
            beats_accumulated -= beats_per_bar
    return barlines
