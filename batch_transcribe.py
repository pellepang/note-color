"""Offline, whole-file rhythm/tempo transcription (issue #55).

The only module in this codebase permitted to import `librosa` -- every
live-path module (main.py included) stays librosa-free, per the ticket.

Runs the exact same per-hop analysis pipeline analysis_loop() (main.py)
drives live -- pitch_detect.compute_spectrum()/detect_pitch(),
chroma.fold()/fold_bass(), multipitch.detect() -- but iterating over fixed
config.BLOCK_SIZE slices of an in-memory array via plain slicing instead of
AudioCapture.get_block(), and reusing NoteSmoother/ChordSmoother for the
exact same debounce/hysteresis behavior live mode gets rather than
inventing new smoothing logic for batch.

Design choices left open by the ticket (documented here rather than
scattered across the function):

- Both the monophonic path (NoteSmoother, one note at a time, matches the
  live app's fill/wheel default) and the polyphonic path (multipitch.
  detect() + ChordSmoother, matches tab's chord-mode-on-by-default) are
  run and non-causally refined, mirroring analysis_loop()'s own
  "always run both, display one" shape. TranscriptionResult exposes both
  (`notes` = polyphonic, `mono_notes` = monophonic) so a caller can pick
  whichever fits -- main.run_batch_transcribe() uses the polyphonic list,
  since a single note is just a one-note "chord" there, so it alone
  already covers both monophonic and chord material without double-
  counting the same audio into two separate note lists.
- Per-note-slot magnitude history is a full recording-length array (zero
  where that pitch/octave key wasn't sounding), keyed by (pitch_class,
  octave) -- not a compacted "only this key's own hops" array. This keeps
  onset_index/hop_index perfectly aligned with the recording's real hop
  clock (no re-indexing needed to convert an onset_index from
  finalize_noncausal() back into an onset_time), at the cost of some
  wasted zero-fill for keys that only sound briefly -- a fine trade at
  batch/offline scale, not the live path's per-hop budget.
- Polyphonic onset detection has the same bounded scope-narrowing
  analysis_loop() documents for its own live chord-mode duration
  tracking: multipitch.detect() has no persistent per-note identity
  across hops, so a key only counts as a fresh onset when it reappears
  after being *absent* from the previous hop's ChordSmoother note stack,
  not on every same-key re-attack mid-sustain (e.g. a fast strummed
  repeat with no gap). The monophonic path doesn't share this limitation
  -- NoteSmoother's own onset gate (note-change / RMS jump / spectral
  flux) already detects a genuine re-attack mid-sustain.
"""

from collections import namedtuple

import librosa
import numpy as np

import chroma
import config
import multipitch
from chord_smoother import ChordSmoother
from duration_tracker import DurationTracker
from note_smoother import NoteSmoother
from pitch_detect import compute_spectrum, detect_pitch

# One transcribed note event: `onset_hop`/`onset_time` locate it in the
# recording, `duration_hops` comes from DurationTracker.finalize_noncausal()'s
# non-causal refinement, `chord_name` is the polyphonic path's recognized
# chord name at that onset (None for the monophonic path, or when nothing
# in chord_templates cleared the match threshold).
NoteEvent = namedtuple(
    "NoteEvent", ["onset_hop", "onset_time", "pitch_class", "octave", "duration_hops", "chord_name"]
)

TranscriptionResult = namedtuple(
    "TranscriptionResult", ["notes", "mono_notes", "bpm", "hop_seconds", "chroma_histogram"]
)


def load_audio(path, sample_rate=None):
    """Load an audio file as a mono float array at `sample_rate` (defaults
    to config.SAMPLE_RATE, matching the live pipeline's assumed rate)."""
    audio, _sr = librosa.load(path, sr=sample_rate or config.SAMPLE_RATE, mono=True)
    return audio


def transcribe(audio, sample_rate, time_signature=config.DEFAULT_TIME_SIGNATURE):
    """Whole-file offline transcription. `time_signature` is accepted for
    interface symmetry with the live CLI path but unused here -- bar
    placement (units_per_bar) is the caller's job (main.run_batch_
    transcribe()), not transcribe()'s; nothing about note/tempo detection
    depends on it. Returns a TranscriptionResult; see the module docstring
    for why both `notes` (polyphonic) and `mono_notes` (monophonic) are
    computed and returned."""
    audio = np.asarray(audio, dtype=np.float64)
    hop_seconds = config.BLOCK_SIZE / sample_rate
    n_hops = len(audio) // config.BLOCK_SIZE

    smoother = NoteSmoother(config)
    chord_smoother = ChordSmoother(config)
    ring = np.zeros(config.WINDOW_SIZE, dtype=np.float64)
    low_ring = np.zeros(config.MULTIPITCH_LOW_WINDOW_SIZE, dtype=np.float64)

    # Per-(pitch_class, octave) key: a full n_hops-length magnitude array
    # (zero where that key isn't sounding) plus the hop indices where a
    # fresh onset was detected for it -- see the module docstring for why
    # full-length rather than compacted-per-key.
    mono_magnitude = {}
    mono_onsets = {}
    chord_magnitude = {}
    chord_onsets = {}
    chord_name_by_hop = [None] * n_hops
    prev_chord_keys = set()
    # Whole-recording chroma histogram (summed, not averaged -- only
    # relative pitch-class weight matters for key-guessing, so an
    # unnormalized sum avoids a needless divide) -- issue #65's
    # score_writer.guess_key_signature() input, fed by whichever
    # pitch-class content sounded across the entire file, mono or chord.
    chroma_histogram = np.zeros(12)

    for hop_index in range(n_hops):
        block = audio[hop_index * config.BLOCK_SIZE: (hop_index + 1) * config.BLOCK_SIZE]
        ring = np.concatenate([ring[len(block):], block])
        low_ring = np.concatenate([low_ring[len(block):], block])
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0

        spectrum = compute_spectrum(ring)
        freq, confidence = detect_pitch(
            ring, sample_rate, spectrum, config.FMIN, config.FMAX, config.YIN_THRESHOLD,
            config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
        )
        pitch_class, octave, is_onset = smoother.update(freq, confidence, rms, spectrum)

        if pitch_class is not None:
            key = (pitch_class, octave)
            if key not in mono_magnitude:
                mono_magnitude[key] = np.zeros(n_hops)
                mono_onsets[key] = []
            mono_magnitude[key][hop_index] = rms
            if is_onset:
                mono_onsets[key].append(hop_index)

        main_chroma = chroma.fold(spectrum, sample_rate)
        bass_chroma = chroma.fold_bass(spectrum, sample_rate)
        chroma_histogram += main_chroma
        multipitch_window = multipitch.select_window(
            ring, low_ring, main_chroma, bass_chroma, gate_ratio=config.MULTIPITCH_BASS_GATE_RATIO
        )
        raw_notes = multipitch.detect(
            multipitch_window,
            sample_rate,
            max_notes=config.CHORD_MAX_NOTES,
            min_mag_ratio=config.CHORD_PEAK_MIN_MAG_RATIO,
            harmonic_tolerance_cents=config.CHORD_HARMONIC_TOLERANCE_CENTS,
            max_peak_candidates=config.CHORD_MAX_PEAK_CANDIDATES,
            harmonic_max_number=config.CHORD_HARMONIC_MAX_NUMBER,
        )
        chord_name, raw_stack = chord_smoother.update(main_chroma, bass_chroma, raw_notes)
        chord_name_by_hop[hop_index] = chord_name

        active_keys = set()
        for entry in raw_stack:
            key = (entry["pitch_class"], entry["octave"])
            active_keys.add(key)
            if key not in chord_magnitude:
                chord_magnitude[key] = np.zeros(n_hops)
                chord_onsets[key] = []
            chord_magnitude[key][hop_index] = entry["confidence"]
            if key not in prev_chord_keys:
                chord_onsets[key].append(hop_index)
        prev_chord_keys = active_keys

    mono_notes = _finalize_events(mono_magnitude, mono_onsets, hop_seconds, chord_name_by_hop=None)
    notes = _finalize_events(chord_magnitude, chord_onsets, hop_seconds, chord_name_by_hop=chord_name_by_hop)

    bpm = _estimate_bpm(audio, sample_rate)

    return TranscriptionResult(
        notes=notes, mono_notes=mono_notes, bpm=bpm, hop_seconds=hop_seconds, chroma_histogram=chroma_histogram
    )


def _finalize_events(magnitude_by_key, onsets_by_key, hop_seconds, chord_name_by_hop):
    events = []
    for key, magnitude_history in magnitude_by_key.items():
        pitch_class, octave = key
        pairs = DurationTracker.finalize_noncausal(
            magnitude_history, onsets_by_key[key], config.DURATION_DECAY_RATIO
        )
        for onset_index, duration_hops in pairs:
            chord_name = chord_name_by_hop[onset_index] if chord_name_by_hop is not None else None
            events.append(
                NoteEvent(
                    onset_hop=onset_index,
                    onset_time=onset_index * hop_seconds,
                    pitch_class=pitch_class,
                    octave=octave,
                    duration_hops=duration_hops,
                    chord_name=chord_name,
                )
            )
    events.sort(key=lambda e: e.onset_hop)
    return events


def _estimate_bpm(audio, sample_rate):
    """librosa.beat.beat_track()'s tempo estimate, or None if it can't
    produce one (e.g. an empty/near-silent clip) -- batch's tempo signal
    is otherwise unrelated to tempo_tracker.TempoTracker, which is
    live-only (see that module's docstring)."""
    if len(audio) == 0:
        return None
    try:
        tempo, _beat_frames = librosa.beat.beat_track(y=audio, sr=sample_rate)
    except Exception:
        return None
    tempo = np.ravel(tempo)
    if tempo.size == 0:
        return None
    return float(tempo[0])
