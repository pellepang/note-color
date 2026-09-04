"""The interim concrete `Engine`/`Voice` pair behind `sound_engine.py`'s
seam (map #99, build ticket #112): map #24's existing harmonic-stack +
ADSR voice (`playback.synthesize_note()`, decision #32), reshaped from
"synthesize a whole note of known duration" into "render block by block
until someone sends a note-off".

This is the same relationship `detection_backends.YinBackend` has to
`pitch_detect.detect_pitch()`: an adapter that makes today's existing
sound-producing code satisfy the new Protocol, so the seam ships with a
real, audible implementation behind it instead of an abstraction with
nothing plugged in. Ticket #113's subtractive synth (mip wavetables,
resonant filter, filter envelope, LFO) replaces it as the default; the
`Engine` Protocol is exactly what lets that happen without touching any
caller.

**Why the waveform is regenerated per block rather than pre-rendered.**
`playback.synthesize_note()` cannot be reused as-is: it needs the note's
total length up front, and under a note-on/note-off model that length is
unknowable at note-on (#105 decision 1). So the oscillator carries its
phase across blocks and the ADSR carries its stage and current level --
which is precisely the per-voice state #103 identified as having to
survive between blocks (oscillator phase, envelope stage, velocity).
Sound-wise it is the same instrument: identical
`config.PLAYBACK_HARMONIC_WEIGHTS` stack, identical attack/decay/
sustain/release constants, so a note held for N seconds and then
released sounds the same as `synthesize_note(..., N)` did.

Pure NumPy, no new dependency -- `scipy` only arrives with #113's
resonant filter, behind its own `[synth]` extra (#111).
"""

import numpy as np

import config
from sound_engine import frequency_for

ATTACK, DECAY, SUSTAIN, RELEASE, DONE = range(5)


class ToneVoice:
    """One sounding note. Renders additively into the caller's block; see
    `sound_engine.Voice` for the Protocol this satisfies."""

    def __init__(self, event, sample_rate, harmonic_weights=None,
                 attack=None, decay=None, sustain_level=None, release=None):
        self.sample_rate = sample_rate
        self.velocity = max(0.0, min(1.0, event.velocity))
        self.pitch = event.pitch
        self.frequency = frequency_for(event.pitch)
        self.weights = tuple(harmonic_weights or config.PLAYBACK_HARMONIC_WEIGHTS)
        self._weight_sum = sum(self.weights) or 1.0

        self.attack_seconds = config.PLAYBACK_ATTACK_SECONDS if attack is None else attack
        self.decay_seconds = config.PLAYBACK_DECAY_SECONDS if decay is None else decay
        self.sustain_level = config.PLAYBACK_SUSTAIN_LEVEL if sustain_level is None else sustain_level
        self.release_seconds = config.PLAYBACK_RELEASE_SECONDS if release is None else release

        self._phase = 0.0                       # cycles, carried across blocks
        self._dt = self.frequency / sample_rate  # cycles per sample
        self._level = 0.0
        self._stage = ATTACK
        self._released = False
        self._release_rate = 0.0

    # -- Voice Protocol ----------------------------------------------------

    @property
    def released(self):
        return self._released

    @property
    def finished(self):
        return self._stage == DONE

    def amplitude(self):
        """Current envelope level scaled by velocity -- the stealing
        policy's 'quietest' ranking signal (`sound_engine.
        select_steal_index()`)."""
        return self._level * self.velocity

    def note_off(self):
        """Begins the release, from wherever the envelope currently is,
        so the fade always takes exactly `release_seconds` regardless of
        whether the note was cut during attack, decay or sustain.
        Idempotent -- a second note-off must not restart the fade (which
        would let a held-then-released note ring on indefinitely under
        repeated note-offs)."""
        if self._released:
            return
        self._released = True
        if self._stage == DONE:
            return
        self._stage = RELEASE
        samples = max(1.0, self.release_seconds * self.sample_rate)
        self._release_rate = self._level / samples

    def render(self, out, frames):
        if self._stage == DONE or frames <= 0:
            return
        envelope = self._envelope_block(frames)
        phases = self._phase + np.arange(frames, dtype=np.float64) * self._dt
        wave = np.zeros(frames, dtype=np.float64)
        for harmonic_number, weight in enumerate(self.weights, start=1):
            wave += weight * np.sin(2.0 * np.pi * harmonic_number * phases)
        wave /= self._weight_sum
        # Keep the phase in [0, 1) rather than letting it grow without
        # bound -- float64 loses sub-sample phase resolution over a long
        # sustain otherwise (a note held for minutes is a real case once
        # QWERTY key-hold lands, #118).
        self._phase = (self._phase + frames * self._dt) % 1.0
        out[:frames] += (wave * envelope * self.velocity).astype(out.dtype)

    # -- resumable ADSR ----------------------------------------------------

    def _envelope_block(self, frames):
        """`frames` envelope samples, continuing from wherever the last
        block left off. Filled segment by segment (at most one stage
        transition per segment, so at most a handful of iterations per
        block) rather than sample by sample in Python."""
        envelope = np.empty(frames, dtype=np.float64)
        filled = 0
        while filled < frames:
            remaining = frames - filled
            if self._stage == SUSTAIN:
                envelope[filled:] = self._level
                filled = frames
            elif self._stage == DONE:
                envelope[filled:] = 0.0
                filled = frames
            else:
                target, rate, next_stage = self._segment()
                if rate <= 0.0:
                    self._level = target
                    self._stage = next_stage
                    continue
                needed = int(np.ceil(abs(target - self._level) / rate))
                take = min(remaining, max(needed, 0))
                if take == 0:
                    self._level = target
                    self._stage = next_stage
                    continue
                step = rate if target > self._level else -rate
                envelope[filled:filled + take] = self._level + step * np.arange(1, take + 1)
                self._level += step * take
                filled += take
                if take >= needed:
                    self._level = target
                    self._stage = next_stage
        np.clip(envelope, 0.0, 1.0, out=envelope)
        return envelope

    def _segment(self):
        """(target level, per-sample rate, stage to enter on arrival) for
        the current ramping stage."""
        if self._stage == ATTACK:
            samples = max(1.0, self.attack_seconds * self.sample_rate)
            return 1.0, 1.0 / samples, DECAY
        if self._stage == DECAY:
            samples = max(1.0, self.decay_seconds * self.sample_rate)
            return self.sustain_level, max(1.0 - self.sustain_level, 0.0) / samples, SUSTAIN
        return 0.0, self._release_rate, DONE


class ToneEngine:
    """`sound_engine.Engine` over `ToneVoice` -- one voice per note-on,
    ignoring `patch` (it has exactly one sound). Stateless, so a single
    instance serves the process-wide `SoundEngine`."""

    def __init__(self, harmonic_weights=None):
        self.harmonic_weights = harmonic_weights

    def note_on(self, event, sample_rate):
        return ToneVoice(event, sample_rate, harmonic_weights=self.harmonic_weights)
