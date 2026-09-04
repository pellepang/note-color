"""The shared effects bus (map #99, build ticket #114, implementing #104's
research): the `Effect` Protocol every effect satisfies, the two effects
that ship first (`Delay` and `Chorus` -- both the same circular delay
line read differently), and `EffectsChain`, which is itself an `Effect`
so chains nest.

**One shared bus, not per-voice (#104, settled by arithmetic).** Both
effects are linear, so `effect(a) + effect(b)` and `effect(a + b)` are
the same signal -- measured identical to float32 epsilon (5.96e-08).
Per-voice routing therefore produces *literally the same output* at N
times the cost (736us/block for 8 per-voice choruses vs. 120us for one
shared delay->chorus chain), and it additionally destroys a delay tail
the moment its voice is released, which is the entire point of a delay.
So the chain attaches once, after voice mixing and before the existing
`np.tanh` soft-clip: `sound_engine.SoundEngine._callback()` live (the
engine owns one chain, swapped with `set_effects()`), and
`playback.render_offline(effects=...)` offline (which also appends
`tail_seconds()` of silence so the repeats are not cut off). The one thing a bus genuinely
cannot reproduce is *decorrelated* per-voice LFO phase, which is unison
detune and belongs to the voice layer, not here.

**Hand-rolled, not `pedalboard` (#104, evidenced not reflexed).**
GPL-3.0 on a repo with no LICENSE file at all; ~12MB of JUCE for ~90
lines of NumPy costing ~1% of the block budget; a non-interpolating
integer-only `Delay` and a single-voice `Chorus` (i.e. *less* control
than this module); and v0.9.23/0.9.24 measured dying with SIGILL on
import on the project owner's own CPU. See docs/DECISIONS.md.

**Three block-boundary invariants**, all of them bookkeeping rather than
DSP:

1. *State survives across calls.* The buffer, the write index and the
   LFO's sample counter are the effect; an effect rebuilt or reset per
   block is a click generator.
2. *The buffer is longer than `max_delay + block`,* so a block's read
   window can never overtake its own write window.
3. *Read before write, or sub-chunk.* Reading a whole block before
   writing it is only correct while the delay is at least one block
   long. Rather than special-casing that, every effect here splits its
   input into internal chunks no longer than its own shortest delay and
   processes those -- so a feedback delay shorter than one block, or a
   single one-shot call covering a whole recording, are the same code
   path. Chunking is *transparent*: each chunk computes exactly the
   recursion's true value, so any partitioning gives the same answer.

That last property is what makes this module's acceptance test cheap and
exact, and it is asserted in `tests/test_effects.py`: **block-wise
processing is bit-identical to one-shot processing**, for any block size,
not merely close. `Chorus` earns it by deriving its LFO phase from an
absolute int64 sample counter rather than accumulating a float phase per
block -- an accumulated phase is chunking-dependent in its last bits.

**Two decisions carry chorus's entire quality story, both measured**
(440Hz sine, 1Hz/+-2ms fully-wet chorus, worst out-of-band content
relative to the carrier):

| implementation | worst artifact |
|---|---|
| linear interpolation, LFO phase carried across blocks | **-88.9 dBc** |
| nearest whole sample (no interpolation) | -48.4 dBc |
| LFO phase reset every block | -23.7 dBc |

Reading the nearest whole sample is 40dB worse -- classic zipper noise.
Resetting the LFO phase each block is 65dB worse and injects a
discontinuity at exactly the block rate (an 86Hz buzz at these
settings). Both are avoided here, and `tests/test_effects.py` asserts
the -80 dBc floor by FFT so that dropping either fails loudly.

Everything in this module is **mono**, matching `playback.py`'s
`channels=1` and `SoundEngine`'s own output stream. Stereo is deliberate
map fog (#99) and is not invented here; where it would attach is noted
on `Chorus` (JOS's advice is that each chorus tap should be individually
spatialized, which is where chorus earns its keep).

Reverb is deliberately **not** in this module yet (#99's fog). It slots
in as one more class plus one `EFFECT_TYPES` entry, with no patch-format
change -- which is exactly what "the effects-chain seam" was for.
"""

import math
from typing import Protocol

import numpy as np

import config

TWO_PI = 2.0 * math.pi


# --------------------------------------------------------------------------
# The seam -- mirrors detection_backends.py's Protocol convention
# --------------------------------------------------------------------------

class Effect(Protocol):
    """One processing stage. Deliberately the same three-method shape
    pedalboard, JUCE's `dsp::ProcessorChain` and Faust all converge on:
    an ordered sequence of objects with one uniform method, plus
    `prepare`/`reset` for sample rate and state.

    `prepare()` is where sample-rate-dependent state (buffer lengths,
    delay times in samples) is built -- the same "capture the
    algorithm-specific config once instead of threading it through every
    call" property `detection_backends.YinBackend` has, moved to a
    method because a sample rate is not known at construction time when
    an effect comes out of a patch file."""

    def prepare(self, sample_rate: float, block_size: int) -> None:
        """(Re)builds sample-rate-dependent state. Idempotent; calling it
        again with the same arguments is allowed and clears state."""
        ...

    def process(self, block: np.ndarray) -> np.ndarray:
        """float32 in, float32 out, same length. Never modifies `block`
        in place -- the caller's mix buffer may be the audio device's own
        memory."""
        ...

    def reset(self) -> None:
        """Clears state without changing parameters (silence the tail)."""
        ...


def _clamp(value, low, high, default):
    """A patch value -> a usable number, with `patch_format.py`'s own
    degradation posture: the wrong type or a missing value falls back to
    the default, an out-of-range one is clamped rather than rejected."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return min(high, max(low, number))


def _first(params, names, default):
    """The first of `names` present in `params`, else `default`. Effects
    accept a couple of spellings per parameter (`time` as documented in
    `patch_format.py`'s schema, `delay_seconds` as pedalboard and #104's
    research spell it) because a hand-edited TOML file is exactly where
    a plausible synonym gets typed."""
    for name in names:
        if name in params:
            return params[name]
    return default


# --------------------------------------------------------------------------
# Delay
# --------------------------------------------------------------------------

class Delay:
    """A fixed-time feedback delay: one circular float32 buffer, a
    persistent write index, and a whole chunk's read indices computed as
    one vectorized expression -- never a per-sample Python loop.

    `feedback` is clamped below 1.0 for stability (|g| < 1 is the
    stability condition; the repeat count is roughly
    log(threshold)/log(feedback)). `damping` optionally rolls the high
    end off a little further on every pass round the loop, so repeats
    decay into the background instead of ringing on identically forever.
    It is a one-*zero* averaging filter (Karplus-Strong's, blended by
    `damping`), not the one-*pole* lowpass the research doc named as
    convention: a one-pole is a per-sample recursion with no vectorized
    NumPy form and no `scipy` in this project's dependencies, whereas a
    one-zero is two shifted arrays and one carried sample. Defaults to
    0.0, i.e. off, since #104 measured nothing about damping at all."""

    def __init__(self, time=None, feedback=None, mix=None, damping=None,
                 sample_rate=None, block_size=None):
        self.time = _clamp(time, config.EFFECT_DELAY_MIN_SECONDS, config.EFFECT_DELAY_MAX_SECONDS,
                           config.EFFECT_DELAY_TIME_SECONDS)
        self.feedback = _clamp(feedback, 0.0, config.EFFECT_DELAY_MAX_FEEDBACK,
                               config.EFFECT_DELAY_FEEDBACK)
        self.mix = _clamp(mix, 0.0, 1.0, config.EFFECT_DELAY_MIX)
        self.damping = _clamp(damping, 0.0, 1.0, config.EFFECT_DELAY_DAMPING)
        self.prepare(sample_rate or config.PLAYBACK_SAMPLE_RATE,
                     block_size or config.PLAYBACK_BLOCK_SIZE)

    @classmethod
    def from_params(cls, params, sample_rate=None, block_size=None):
        return cls(
            time=_first(params, ("time", "time_seconds", "delay_seconds"), None),
            feedback=params.get("feedback"),
            mix=params.get("mix"),
            damping=params.get("damping"),
            sample_rate=sample_rate, block_size=block_size,
        )

    def prepare(self, sample_rate, block_size):
        self.sample_rate = float(sample_rate)
        self.block_size = max(1, int(block_size))
        self.delay_samples = max(1, int(round(self.time * self.sample_rate)))
        # Invariant 2: strictly longer than max_delay + one chunk.
        self.size = self.delay_samples + self.block_size + 2
        # Invariant 3: a chunk never longer than the delay itself, so a
        # chunk's reads can only ever touch samples written by an
        # *earlier* chunk -- which is what makes read-before-write correct
        # even for a feedback delay shorter than one block.
        self.chunk = max(1, min(self.delay_samples, self.block_size))
        self.reset()

    def reset(self):
        self.buffer = np.zeros(self.size, dtype=np.float32)
        self.write = 0
        self._damp_prev = np.float32(0.0)

    def process(self, block):
        block = np.asarray(block, dtype=np.float32)
        if block.size == 0:
            return block.copy()
        out = np.empty(block.size, dtype=np.float32)
        for start in range(0, block.size, self.chunk):
            stop = min(start + self.chunk, block.size)
            out[start:stop] = self._process_chunk(block[start:stop])
        return out

    def _process_chunk(self, chunk):
        n = chunk.size
        size = self.size
        offsets = np.arange(n)
        read = (self.write - self.delay_samples + offsets) % size
        wet = self.buffer[read]
        self.buffer[(self.write + offsets) % size] = chunk + self.feedback * self._damped(wet)
        self.write = (self.write + n) % size
        return ((1.0 - self.mix) * chunk + self.mix * wet).astype(np.float32)

    def _damped(self, wet):
        """One-zero averaging filter in the feedback path, blended by
        `damping` (0 = untouched, 1 = a plain two-point average). One
        carried sample of state, so it is chunking-transparent like
        everything else here."""
        if self.damping <= 0.0:
            return wet
        previous = np.empty(wet.size, dtype=np.float32)
        previous[0] = self._damp_prev
        previous[1:] = wet[:-1]
        self._damp_prev = wet[-1]
        half = np.float32(self.damping * 0.5)
        return (1.0 - half) * wet + half * previous

    def __repr__(self):
        return (f"Delay(time={self.time:.3f}, feedback={self.feedback:.2f}, "
                f"mix={self.mix:.2f}, damping={self.damping:.2f})")


# --------------------------------------------------------------------------
# Chorus
# --------------------------------------------------------------------------

class Chorus:
    """A chorus is the same delay line with an LFO-modulated *fractional*
    read index: moving the read position is itself the pitch effect (the
    tap Doppler-shifts as it moves), which is what makes one source sound
    like several playing in unison. `voices` taps share one buffer at
    evenly spread LFO phases (`2*pi*v/voices`).

    Parameter ranges are taken from `juce::dsp::Chorus` (what pedalboard
    wraps) rather than folklore: `rate_hz` < 100, `centre_delay_ms`
    1..100 with 7-8ms the classic chorus, `feedback` -1..1 (negative is a
    real variant, not a bug), `mix` 0..1. Depth and rate are not
    perceptually independent -- together they set detune, peak
    `1200*log2(1 +- 2*pi*rate*depth_seconds)` cents, i.e. about +-22
    cents at the 1Hz/+-2ms defaults.

    The two non-negotiables, both measured in #104 (see this module's
    docstring): **linear interpolation** of the fractional read index,
    and **LFO phase carried across blocks**. Phase here is derived from
    `self._elapsed`, an absolute int64 sample counter, rather than
    accumulated per block -- same continuity, but also exactly
    reproducible for a given absolute sample index regardless of how the
    caller happened to chunk its audio, which is what upgrades the
    block-wise-equals-one-shot test from "close" to "bit-identical".

    Mono, deliberately. This is the one class where stereo would visibly
    attach: JOS's advice is that each tap should be individually
    spatialized, so a stereo build would pan tap `v` rather than summing
    the taps here. #99 leaves stereo as fog and nothing is invented for
    it."""

    def __init__(self, rate_hz=None, depth_ms=None, centre_delay_ms=None, mix=None,
                 voices=None, feedback=None, sample_rate=None, block_size=None):
        self.rate_hz = _clamp(rate_hz, 0.0, config.EFFECT_CHORUS_MAX_RATE_HZ,
                              config.EFFECT_CHORUS_RATE_HZ)
        self.centre_delay_ms = _clamp(centre_delay_ms, config.EFFECT_CHORUS_MIN_DELAY_MS,
                                      config.EFFECT_CHORUS_MAX_DELAY_MS,
                                      config.EFFECT_CHORUS_CENTRE_DELAY_MS)
        depth = _clamp(depth_ms, 0.0, config.EFFECT_CHORUS_MAX_DELAY_MS,
                       config.EFFECT_CHORUS_DEPTH_MS)
        # A depth wider than the centre delay would swing the read index
        # past the write head (a "negative" delay, i.e. reading samples
        # that do not exist yet), so it is clamped rather than rejected --
        # same posture as every other out-of-range patch value.
        self.depth_ms = min(depth, self.centre_delay_ms - config.EFFECT_CHORUS_MIN_DELAY_MS)
        self.mix = _clamp(mix, 0.0, 1.0, config.EFFECT_CHORUS_MIX)
        self.feedback = _clamp(feedback, -config.EFFECT_DELAY_MAX_FEEDBACK,
                               config.EFFECT_DELAY_MAX_FEEDBACK, config.EFFECT_CHORUS_FEEDBACK)
        try:
            count = int(voices)
        except (TypeError, ValueError):
            count = config.EFFECT_CHORUS_VOICES
        self.voices = min(config.EFFECT_CHORUS_MAX_VOICES, max(1, count))
        self.prepare(sample_rate or config.PLAYBACK_SAMPLE_RATE,
                     block_size or config.PLAYBACK_BLOCK_SIZE)

    @classmethod
    def from_params(cls, params, sample_rate=None, block_size=None):
        return cls(
            rate_hz=_first(params, ("rate_hz", "rate"), None),
            depth_ms=_first(params, ("depth_ms", "depth"), None),
            centre_delay_ms=_first(params, ("centre_delay_ms", "center_delay_ms", "delay_ms"), None),
            mix=params.get("mix"),
            voices=params.get("voices"),
            feedback=params.get("feedback"),
            sample_rate=sample_rate, block_size=block_size,
        )

    def prepare(self, sample_rate, block_size):
        self.sample_rate = float(sample_rate)
        self.block_size = max(1, int(block_size))
        per_ms = self.sample_rate / 1000.0
        self.centre_samples = self.centre_delay_ms * per_ms
        self.depth_samples = self.depth_ms * per_ms
        self.min_delay = self.centre_samples - self.depth_samples
        self.max_delay = self.centre_samples + self.depth_samples
        self.size = int(math.ceil(self.max_delay)) + self.block_size + 2
        # Same invariant 3 as Delay: never longer than the shortest delay
        # the LFO can swing to, so the fractional read (which needs both
        # floor(pos) and floor(pos)+1) only ever touches already-written
        # samples, feedback or not.
        # `- 1` because the fractional read needs floor(pos)+1 as well as
        # floor(pos), and the shortest delay must leave room for both.
        self.chunk = max(1, min(int(self.min_delay) - 1, self.block_size))
        self._phase_step = TWO_PI * self.rate_hz / self.sample_rate
        self._spreads = np.array([TWO_PI * v / self.voices for v in range(self.voices)])
        self.reset()

    def reset(self):
        self.buffer = np.zeros(self.size, dtype=np.float32)
        self.write = 0
        self._elapsed = 0  # absolute sample index; the LFO's only clock

    @property
    def phase(self):
        """The LFO's current phase in radians -- derived, never stored.
        Exposed for status/debug and for the per-voice-decorrelation
        experiment #104 ran; nothing in the audio path reads it."""
        return (self._elapsed * self._phase_step) % TWO_PI

    def peak_detune_cents(self):
        """Peak detune this rate/depth pair produces, in cents:
        `1200*log2(1 + 2*pi*rate*depth_seconds)`. About +-22 cents at the
        defaults; much past that stops sounding like several players and
        starts sounding like warped tape."""
        swing = TWO_PI * self.rate_hz * self.depth_ms / 1000.0
        if swing >= 1.0:
            return float("inf")
        return 1200.0 * math.log2(1.0 + swing)

    def process(self, block):
        block = np.asarray(block, dtype=np.float32)
        if block.size == 0:
            return block.copy()
        out = np.empty(block.size, dtype=np.float32)
        for start in range(0, block.size, self.chunk):
            stop = min(start + self.chunk, block.size)
            out[start:stop] = self._process_chunk(block[start:stop])
        return out

    def _process_chunk(self, chunk):
        n = chunk.size
        size = self.size
        offsets = np.arange(n)
        # Absolute sample index -> phase, so the LFO is identical for a
        # given sample no matter how the caller chunked its audio.
        lfo = (self._elapsed + offsets) * self._phase_step
        # The integer write position is reduced mod `size` *before* the
        # fractional delay is subtracted: `(base - delay) % size` is then
        # computed from bit-identical inputs for a given absolute sample
        # however the caller chunked, whereas `(write + offsets - delay)`
        # can differ by exactly `size` between two chunkings, and
        # `(x - d) + size` is not `(x + size) - d` in float64.
        base = (self.write + offsets) % size
        wet = np.zeros(n, dtype=np.float64)
        for spread in self._spreads:
            delay = self.centre_samples + self.depth_samples * np.sin(lfo + spread)
            pos = (base - delay) % size
            low = np.floor(pos).astype(np.int64)
            frac = pos - low
            wet += (1.0 - frac) * self.buffer[low] + frac * self.buffer[(low + 1) % size]
        wet /= self.voices
        self.buffer[base] = chunk + self.feedback * wet
        self.write = (self.write + n) % size
        self._elapsed += n
        return ((1.0 - self.mix) * chunk + self.mix * wet).astype(np.float32)

    def __repr__(self):
        return (f"Chorus(rate_hz={self.rate_hz:.2f}, depth_ms={self.depth_ms:.2f}, "
                f"centre_delay_ms={self.centre_delay_ms:.2f}, voices={self.voices}, "
                f"mix={self.mix:.2f}, feedback={self.feedback:.2f})")


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------

class EffectsChain:
    """An ordered list of effects, applied in order -- and itself an
    `Effect`, which is pedalboard's own trick and the reason chains
    nest (a patch-level chain inside a global one, once #105's voice
    manager settles whether effects are per-patch or global; that
    question is still open, see docs/DECISIONS.md).

    An empty chain is a valid chain and is the identity: it copies its
    input and returns it, so a caller never needs to special-case
    "patch declared no effects".

    `skipped` records `[[effects]]` entries whose `type` this build does
    not know -- kept as data, never an error, because `patch_format.py`
    deliberately preserves unknown effect types across a save round trip
    so a patch written for a build that grew reverb is not silently
    stripped of it by one that has not."""

    def __init__(self, effects=(), skipped=()):
        self.effects = list(effects)
        self.skipped = list(skipped)

    def prepare(self, sample_rate, block_size):
        for effect in self.effects:
            effect.prepare(sample_rate, block_size)

    def process(self, block):
        block = np.asarray(block, dtype=np.float32)
        if not self.effects:
            return block.copy()
        for effect in self.effects:
            block = effect.process(block)
        return block

    def reset(self):
        for effect in self.effects:
            effect.reset()

    def append(self, effect):
        self.effects.append(effect)
        return self

    def __len__(self):
        return len(self.effects)

    def __iter__(self):
        return iter(self.effects)

    def __getitem__(self, index):
        return self.effects[index]

    def __repr__(self):
        return f"EffectsChain({self.effects!r})"


#: `type` string -> class, the whole registry. Reverb later is one more
#: entry here plus one class, with no patch-format change.
EFFECT_TYPES = {
    "delay": Delay,
    "chorus": Chorus,
}


def build_effect(spec, sample_rate=None, block_size=None):
    """One `patch_format.EffectSpec` (or any object with `.type`/`.params`,
    or a plain dict) -> an `Effect`, or `None` for a type this build does
    not know. `None` is the honest answer for an unknown type, not an
    exception: the format's whole posture is that a newer patch degrades
    on an older build rather than failing to open."""
    if isinstance(spec, dict):
        kind = str(spec.get("type", "")).strip().lower()
        params = {k: v for k, v in spec.items() if k != "type"}
    else:
        kind = str(getattr(spec, "type", "")).strip().lower()
        params = dict(getattr(spec, "params", {}) or {})
    cls = EFFECT_TYPES.get(kind)
    if cls is None:
        return None
    return cls.from_params(params, sample_rate=sample_rate, block_size=block_size)


def chain_from_specs(specs, sample_rate=None, block_size=None):
    """A patch's `[[effects]]` list -> an `EffectsChain`. File order is
    chain order (TOML guarantees array-of-tables ordering); unknown types
    are collected into `chain.skipped` and otherwise ignored."""
    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    block_size = block_size or config.PLAYBACK_BLOCK_SIZE
    built, skipped = [], []
    for spec in specs or ():
        effect = build_effect(spec, sample_rate, block_size)
        if effect is None:
            kind = spec.get("type", "") if isinstance(spec, dict) else getattr(spec, "type", "")
            skipped.append(str(kind))
        else:
            built.append(effect)
    return EffectsChain(built, skipped)


def chain_from_patch(patch, sample_rate=None, block_size=None):
    """Convenience for the common case -- a `patch_format.Patch` straight
    to the chain its `[[effects]]` declares."""
    return chain_from_specs(getattr(patch, "effects", ()), sample_rate, block_size)


def tail_seconds(chain):
    """How long a chain keeps ringing after its input goes silent, as a
    rough upper bound -- what an *offline* render must append so the
    delay's own repeats are not cut off mid-tail (a live stream has no
    such edge; it just keeps calling `process()`).

    Deliberately an estimate, not a measurement: a delay's repeats decay
    geometrically, so the honest bound is "how many repeats until
    inaudible" -- `log(EFFECT_TAIL_FLOOR)/log(feedback)` repeats of
    `time` seconds each, capped at `EFFECT_MAX_TAIL_SECONDS`."""
    total = 0.0
    for effect in getattr(chain, "effects", chain) or ():
        time_seconds = getattr(effect, "time", None)
        if time_seconds is None:
            continue
        feedback = getattr(effect, "feedback", 0.0)
        if feedback <= 0.0:
            repeats = 1.0
        else:
            repeats = math.log(config.EFFECT_TAIL_FLOOR) / math.log(feedback)
        total += time_seconds * (repeats + 1.0)
    return min(config.EFFECT_MAX_TAIL_SECONDS, total)
