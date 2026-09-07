"""Score playback (map #24, subsystem 3 of rhythm -> score writer ->
**playback** -> editor -> theory analysis, per issue #35's build order).

Decision #32 (`gh issue view 32`) already settled the approach: a NumPy
oscillator+ADSR synth (no soundfont/FluidSynth dependency, per #28's
research -- that stays a future upgrade, not rejected outright), reusing
`sounddevice.OutputStream` the same way `audio_capture.py` reuses
`InputStream` (already a dependency, zero new package). #32 made both an
offline whole-buffer pre-render mode and a live per-event scheduling
mode first-class ("both, not a single pick"); map #99's decision #105
later moved the live half out of here into `sound_engine.py` -- see
below. Color sonification is explicitly out of scope for v1 (#32).

Never imported by `analysis_loop()`/`SessionState`/any live-capture code
path -- playback is strictly an opt-in offline/replay-time feature, same
isolation convention `batch_transcribe.py` (librosa) and `score_writer.py`
(music21) already establish for this project's other "real but
live-path-irrelevant" dependencies. Unlike those two, `sounddevice` is
already a live-path dependency (via `audio_capture.py`), so there's no
import-cost concern here -- the isolation is about *usage* (never called
from the capture/analysis thread), not import weight.

**Which mode fits which caller, and why (not a free choice per call
site):** offline pre-render is the natural fit for `virtualnote
transcribe --play` -- a whole `TranscriptionResult` already exists in
full before playback starts, so there is nothing to schedule against;
pre-rendering once up front is strictly simpler and gives sample-accurate
timing for free, since timing is a buffer-index computation rather than a
wall-clock one.

**What used to live here, and where it went (map #99, decision #105).**
This module also carried a `LiveScheduler`: an `OutputStream` callback
mixing a lock-protected list of pre-synthesized notes, driven by
`trigger_note(pitch_class, octave, duration_seconds)`. It is gone,
superseded by `sound_engine.py`'s note-on/note-off voice manager, and its
one caller (`main.run_replay_session()`, `virtualnote replay --play`) has
moved over. Two reasons, both from map #99: two independent voice-mixing
callbacks in one process would compete for the same device and the same
GIL that prototype #100 showed is the binding constraint on polyphony;
and `trigger_note()`'s duration-carrying shape is exactly the second
primitive decision #105 ruled out, since every later feature (voice
stealing, sustain pedal, held-note transpose, aftertouch) would then have
to be reasoned about twice. `render_offline()`/`play_offline()` below are
deliberately untouched by that change -- a fully-known transcription has
no reason to be routed through a real-time voice manager.
"""

import numpy as np
import sounddevice as sd

import config


def note_frequency(pitch_class, octave):
    """Hz for `pitch_class` (0-11)/`octave` under standard MIDI tuning
    (A4=440Hz, C4=midi 60) -- the same formula this repo's test suite
    already assumes independently (see e.g. tests/test_chroma.py's
    freq_for()). Kept local rather than factored into a shared module:
    no other non-test code needs pitch-class/octave -> frequency
    conversion until this module."""
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _adsr_envelope(num_samples, sample_rate, attack, decay, sustain_level, release):
    """A linear-segment ADSR envelope, `num_samples` long. `attack`/`decay`/
    `release` are seconds; `sustain_level` is 0..1. The sustain segment
    fills whatever's left after attack+decay+release are subtracted from
    `num_samples` (clamped to >=0) -- for a note shorter than
    attack+decay+release, attack/decay/release are scaled down
    proportionally so the envelope still reaches zero exactly at
    `num_samples` rather than clipping mid-release (an audible click a
    fixed-length envelope would otherwise produce on short/staccato
    notes, which duration-tracked notes as short as ~50ms, per this
    project's own duration_tracker.py doc, are a real, not hypothetical,
    case here)."""
    total = attack + decay + release
    if total > 0 and (attack + decay + release) * sample_rate > num_samples:
        scale = num_samples / (total * sample_rate)
        attack, decay, release = attack * scale, decay * scale, release * scale

    n_attack = int(attack * sample_rate)
    n_decay = int(decay * sample_rate)
    n_release = int(release * sample_rate)
    n_sustain = max(0, num_samples - n_attack - n_decay - n_release)

    segments = []
    if n_attack:
        segments.append(np.linspace(0.0, 1.0, n_attack, endpoint=False))
    if n_decay:
        segments.append(np.linspace(1.0, sustain_level, n_decay, endpoint=False))
    if n_sustain:
        segments.append(np.full(n_sustain, sustain_level))
    if n_release:
        segments.append(np.linspace(sustain_level, 0.0, n_release, endpoint=True))

    envelope = np.concatenate(segments) if segments else np.zeros(0)
    # Rounding attack/decay/release*sample_rate to ints can leave the
    # concatenated envelope a sample or two short of num_samples --
    # pad with silence (zeros) rather than repeat/stretch a segment,
    # since any such gap is sub-millisecond and inaudible either way.
    if len(envelope) < num_samples:
        envelope = np.concatenate([envelope, np.zeros(num_samples - len(envelope))])
    return envelope[:num_samples]


def synthesize_note(pitch_class, octave, duration_seconds, sample_rate=None, velocity=1.0):
    """One ADSR-enveloped note as a mono float32 NumPy array, covering
    `duration_seconds` plus config.PLAYBACK_RELEASE_SECONDS of release
    tail (so the note actually fades out audibly rather than being cut
    off at the exact instant it was detected as ending).

    Waveform: a small fixed harmonic stack (fundamental + 2nd + 3rd
    partials at descending weight), not a bare sine -- a pure sine at
    these ADSR settings reads as an audibly thin "test tone," while a
    handful of harmonics gets meaningfully closer to "an instrument" for
    negligible extra NumPy cost (issue #28's research flagged this exact
    tradeoff -- "a few adjustable waveforms is the ceiling without real
    modelling work" -- this is that low-effort ceiling, not an attempt at
    realism). Weights/ratios are `config.PLAYBACK_HARMONIC_WEIGHTS`, a
    judgment call left open by #32 -- picked empirically (by ear) rather
    than measured, documented here rather than treated as load-bearing:
    revisit freely if the timbre doesn't hold up in practice."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    total_seconds = duration_seconds + config.PLAYBACK_RELEASE_SECONDS
    num_samples = max(1, int(total_seconds * sample_rate))

    freq = note_frequency(pitch_class, octave)
    t = np.arange(num_samples) / sample_rate
    wave = np.zeros(num_samples, dtype=np.float64)
    for harmonic_number, weight in enumerate(config.PLAYBACK_HARMONIC_WEIGHTS, start=1):
        wave += weight * np.sin(2.0 * np.pi * freq * harmonic_number * t)
    wave /= sum(config.PLAYBACK_HARMONIC_WEIGHTS)  # keep peak amplitude <=1 regardless of stack size

    envelope = _adsr_envelope(
        num_samples, sample_rate,
        attack=config.PLAYBACK_ATTACK_SECONDS,
        decay=config.PLAYBACK_DECAY_SECONDS,
        sustain_level=config.PLAYBACK_SUSTAIN_LEVEL,
        release=config.PLAYBACK_RELEASE_SECONDS,
    )
    return (wave * envelope * velocity).astype(np.float32)


def render_offline(notes, sample_rate=None, effects=None):
    """Pre-renders `notes` (an iterable of `(onset_seconds, pitch_class,
    octave, duration_seconds)` tuples -- a `velocity` 5th element is
    optional, defaulting to 1.0) into one mono float32 NumPy buffer,
    additively mixed (simultaneous notes -- e.g. a chord's tones sharing
    one onset -- simply sum at the same buffer positions) and soft-clipped
    via `np.tanh` to avoid harsh digital clipping if several notes'
    peaks happen to coincide. Buffer length is exactly long enough for the
    latest-ending note (its own onset + duration + release tail).

    Sample-accurate by construction: unlike live scheduling, timing here
    is entirely a buffer-index computation, not a wall-clock one, so
    there is no scheduling jitter to reason about at all -- see this
    module's docstring for why offline pre-render is the right mode for
    `virtualnote transcribe --play` specifically (a whole
    TranscriptionResult already exists before playback starts).

    `effects` (ticket #114) is an optional `effects.Effect` -- normally
    an `EffectsChain` -- applied once to the whole summed mix, after the
    notes are added together and before the `np.tanh` soft-clip: the
    same shared-bus placement `sound_engine.SoundEngine._callback()`
    uses live, so a patch's chain sounds the same offline as it does
    under the keys. The buffer is extended by `effects.tail_seconds()`
    so a delay's repeats ring out instead of being cut at the last
    note's end. `None` (the default) renders exactly as before."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    notes = list(notes)
    if not notes:
        return np.zeros(0, dtype=np.float32)

    rendered = []
    end_samples = []
    for note in notes:
        onset_seconds, pitch_class, octave, duration_seconds = note[:4]
        velocity = note[4] if len(note) > 4 else 1.0
        samples = synthesize_note(pitch_class, octave, duration_seconds, sample_rate, velocity)
        onset_sample = max(0, int(round(onset_seconds * sample_rate)))
        rendered.append((onset_sample, samples))
        end_samples.append(onset_sample + len(samples))

    buffer = np.zeros(max(end_samples), dtype=np.float64)
    for onset_sample, samples in rendered:
        buffer[onset_sample:onset_sample + len(samples)] += samples
    if effects is not None:
        from effects import tail_seconds

        tail = int(round(tail_seconds(effects) * sample_rate))
        mix = np.zeros(len(buffer) + tail, dtype=np.float32)
        mix[:len(buffer)] = buffer
        effects.prepare(sample_rate, config.PLAYBACK_BLOCK_SIZE)
        buffer = effects.process(mix)
    return np.tanh(buffer).astype(np.float32)


def play_offline(notes, sample_rate=None, blocking=True):
    """Renders `notes` via `render_offline()` then plays the whole buffer
    through one `sd.OutputStream.write()` call -- a single write, not a
    per-note stream, since the entire buffer is already sample-accurate
    on its own. `blocking=True` (default) waits for playback to finish
    before returning, matching `virtualnote transcribe`'s existing
    one-shot-then-exit shape; `blocking=False` returns immediately for a
    caller that wants to do something else while it plays (no current
    caller needs this, exposed for symmetry with `sd.play()`'s own API)."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    buffer = render_offline(notes, sample_rate)
    if len(buffer) == 0:
        return
    sd.play(buffer, sample_rate, blocking=blocking)
