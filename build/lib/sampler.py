"""The sampler engine (map #99, build ticket #116): the second of the
three engines behind `sound_engine.py`'s `Engine`/`Voice` seam, and the
one that makes both "import my own samples" and the drum pad real --
per map #99's standing decision those are the same feature, because a
**kit** is just a sampler patch whose zones are one key wide.

There is deliberately **no separate kit code path** anywhere in this
module. A drum kit and a multisampled piano differ only in the shape of
their `[[zones]]`, which `patch_format.select_zone()` already resolves.

What this module owns:

* `SamplerVoice` -- one sounding sample: fractional-rate reading (the
  pitch shift), `smpl`-chunk loop points, a click-free attack/release
  ramp, and choke cutoff.
* `SamplerEngine` -- `sound_engine.Engine` over a sampler `Patch`: pick
  the zone matching key **and** velocity, load its sample, cut anything
  it chokes, hand back a voice.
* `SampleCache` -- decoded samples kept in memory, invalidated by mtime.

What it deliberately does **not** own: zone selection, velocity bands,
choke-group membership, bare-name resolution and availability all live in
`patch_format.py` already (`select_zone()`, `choked_zones()`,
`sample_path()`, `zone_available()`) and are called, not reimplemented.
WAV decoding and sample import live in `wav_io.py`.

**Pitch shifting is playback-rate resampling**, the same thing a hardware
sampler does: reading the recording at `2**((pitch - root_key)/12)` frames
per output frame transposes it by exactly that many semitones (and
stretches it in time, which is the honest sampler tradeoff, not a bug).
A source recorded at a different sample rate folds into the same ratio as
`sample_rate / engine_rate`, so a 22050Hz sample and a 44100Hz sample of
the same note sound at the same pitch with no separate resampling pass --
see `wav_io.Sample`.

**One-shots ignore note-off.** A zone whose sample carries no loop plays
to its natural end even if the key is released immediately -- that is what
makes a drum pad work at all (a kick is ~200ms and a QWERTY tap is
shorter), and it is what every drum machine does. A *looping* zone
instead fades out over `config.SAMPLER_RELEASE_SECONDS` on note-off,
since it would otherwise sustain forever. Either way the note-off is
recorded, so `sound_engine`'s stealing policy still prefers a released
voice.

**Missing sample = silence, never a crash** (decision #106): the zone
renders as unavailable, `note_on()` returns a `SilentVoice`, and the rest
of the kit sounds normally.

Per this repo's "pure logic unit-tested, real I/O smoke-tested"
convention, everything here is verified numerically against synthesized
WAVs -- rendered-buffer assertions, an FFT check that a shifted sample's
fundamental really lands where the ratio says, loop-point continuity, and
choke cutoff -- with no audio device involved.
"""

from __future__ import annotations

import os

import numpy as np

import config
import patch_format
import wav_io


def velocity_amplitude(velocity, velocity_to_amp):
    """How loudly a note of this velocity sounds, given the patch's
    `velocity_to_amp` routing (#106's concrete form of map #99's
    "velocity is the tell"). At 0 the patch ignores velocity entirely
    (a drum kit whose layers already encode dynamics); at 1 amplitude
    scales straight with it. Pure, so the routing is unit-tested without
    rendering anything."""
    velocity = min(max(float(velocity), 0.0), 1.0)
    amount = min(max(float(velocity_to_amp), 0.0), 1.0)
    return (1.0 - amount) + amount * velocity


def gain_to_linear(gain_db):
    """A zone's `gain` (dB, decision #106's unit) as a linear multiplier.
    -60dB (the schema's floor) is 0.001, i.e. effectively silent."""
    return float(10.0 ** (float(gain_db) / 20.0))


def playback_ratio(pitch, root_key, sample_rate, engine_rate):
    """Frames of source read per frame of output: the semitone transpose
    `2**((pitch - root_key)/12)` times any sample-rate mismatch. One
    number carries both, which is why `wav_io` deliberately does not
    resample on load."""
    semitones = float(pitch) - float(root_key)
    rate_ratio = float(sample_rate) / float(engine_rate or sample_rate or 1)
    return (2.0 ** (semitones / 12.0)) * rate_ratio


class SilentVoice:
    """The `sound_engine.Voice` returned when a note has no sound to
    make -- no zone matches the key, or the zone's sample is missing or
    unreadable. Finished from birth, so the voice manager reclaims its
    slot on the very next block and a kit full of missing samples costs
    no polyphony at all.

    A real object rather than `None` because `Engine.note_on()`'s
    Protocol return type is `Voice`; making the failure case a null
    return would push a `None` check into every caller of the seam."""

    released = True
    finished = True

    def render(self, out, frames):
        return None

    def note_off(self):
        return None

    def amplitude(self):
        return 0.0


class SamplerVoice:
    """One sounding sample. Implements `sound_engine.Voice`.

    State carried across blocks: the fractional read position, the
    amplitude-ramp level, and whether a fade-out is in progress -- the
    sampler's equivalent of #103's per-voice oscillator phase and
    envelope stage."""

    def __init__(self, sample, pitch, root_key, engine_rate, amplitude=1.0,
                 zone=None, attack_seconds=None, release_seconds=None):
        self.sample = sample
        self.zone = zone
        self.pitch = int(pitch)
        self.engine_rate = int(engine_rate)
        self.ratio = playback_ratio(pitch, root_key, sample.sample_rate, engine_rate)
        self._amp = float(amplitude)
        self._data = sample.data
        self._last_index = max(self._data.shape[0] - 1, 0)

        self.looping = sample.loops
        self.loop_start = int(sample.loop_start) if self.looping else 0
        self.loop_end = int(sample.loop_end) if self.looping else 0

        attack = config.SAMPLER_ATTACK_SECONDS if attack_seconds is None else attack_seconds
        release = config.SAMPLER_RELEASE_SECONDS if release_seconds is None else release_seconds
        self._attack_rate = _ramp_rate(attack, engine_rate)
        self._release_seconds = max(0.0, float(release))

        self._position = 0.0
        self._level = 0.0 if self._attack_rate > 0.0 else 1.0
        self._fade_rate = 0.0
        self._fading = False
        self._released = False
        self._done = self._data.shape[0] < 2

    # -- Voice Protocol ----------------------------------------------------

    @property
    def released(self):
        return self._released

    @property
    def finished(self):
        return self._done

    def amplitude(self):
        """The stealing policy's 'quietest among the released' ranking
        signal -- current ramp level times this note's own gain."""
        return self._level * self._amp

    def note_off(self):
        """Idempotent. A looping zone starts its release fade here; a
        one-shot only records the release and keeps playing to its
        natural end (see the module docstring)."""
        if self._released:
            return
        self._released = True
        if self.looping:
            self._begin_fade(self._release_seconds)

    def choke(self, fade_seconds=None):
        """Cut by another zone in the same non-zero `choke_group` -- the
        open/closed hi-hat case. A very short fade rather than an
        instant stop, because dropping a waveform mid-cycle to zero is an
        audible click; `config.SAMPLER_CHOKE_SECONDS` is short enough to
        read as a cutoff and long enough not to click."""
        self._released = True
        seconds = config.SAMPLER_CHOKE_SECONDS if fade_seconds is None else fade_seconds
        self._begin_fade(seconds)

    def render(self, out, frames):
        """Add this voice's next `frames` samples into `out`, reading the
        source at `self.ratio` frames per output frame with linear
        interpolation between neighbouring source frames."""
        if self._done or frames <= 0 or self._data.shape[0] < 2:
            return
        positions = self._position + np.arange(frames, dtype=np.float64) * self.ratio
        if self.looping:
            span = self.loop_end - self.loop_start
            past = positions >= self.loop_end
            if past.any():
                positions = np.where(
                    past,
                    self.loop_start + np.mod(positions - self.loop_start, span),
                    positions,
                )
            usable = frames
        else:
            usable = int(np.count_nonzero(positions <= self._last_index))
            if usable <= 0:
                self._done = True
                return
            positions = positions[:usable]

        indices = positions.astype(np.int64)
        np.clip(indices, 0, self._last_index, out=indices)
        fraction = (positions - indices).astype(np.float32)
        if self.looping:
            # Interpolate *across* the loop seam rather than into the
            # frame before it: at the last frame of the loop the next
            # source frame is the loop's first, not a clamped repeat.
            # Without this a loop reads as a tiny flat spot once per
            # cycle -- audible as a buzz at the loop rate on a short
            # loop, exactly the artefact #104 found for per-block LFO
            # resets.
            next_indices = np.where(indices >= self.loop_end - 1, self.loop_start, indices + 1)
        else:
            next_indices = np.minimum(indices + 1, self._last_index)
        block = self._data[indices] * (1.0 - fraction) + self._data[next_indices] * fraction

        block *= self._gain_block(usable)
        out[:usable] += (block * self._amp).astype(out.dtype)

        self._position = float(positions[-1]) + self.ratio
        if not self.looping and (usable < frames or self._position > self._last_index):
            self._done = True

    # -- amplitude ramp ----------------------------------------------------

    def _begin_fade(self, seconds):
        """Start (or shorten) a fade to silence from wherever the ramp
        currently is. A second, shorter fade request wins -- a choke
        arriving during a release must cut faster, never slower."""
        rate = _ramp_rate(seconds, self.engine_rate)
        if rate <= 0.0:
            self._level = 0.0
            self._fading = True
            self._done = True
            return
        if self._fading:
            rate = max(rate, self._fade_rate)
        self._fading = True
        self._fade_rate = rate

    def _gain_block(self, frames):
        """`frames` amplitude-ramp samples, continuing from the last
        block. Segment-filled (at most one state change per segment)
        rather than sample-by-sample in Python, the same shape
        `tone_engine.ToneVoice._envelope_block()` uses."""
        gains = np.empty(frames, dtype=np.float32)
        filled = 0
        while filled < frames:
            remaining = frames - filled
            if self._fading:
                target, step = 0.0, -self._fade_rate
            elif self._level < 1.0 and self._attack_rate > 0.0:
                target, step = 1.0, self._attack_rate
            else:
                gains[filled:] = 1.0
                break
            needed = int(np.ceil(abs(target - self._level) / abs(step)))
            take = min(remaining, max(needed, 1))
            gains[filled:filled + take] = self._level + step * np.arange(1, take + 1)
            self._level += step * take
            filled += take
            if take >= needed:
                self._level = target
                if self._fading:
                    self._done = True
                    gains[filled:] = 0.0
                    break
        np.clip(gains, 0.0, 1.0, out=gains)
        return gains


def _ramp_rate(seconds, sample_rate):
    """Per-sample step for a 0->1 ramp of `seconds`. Zero (or negative)
    means 'instant', which callers treat as no ramp at all."""
    frames = float(seconds) * float(sample_rate)
    if frames <= 0.0:
        return 0.0
    return 1.0 / frames


# --- Sample loading -------------------------------------------------------

class SampleCache:
    """Decoded samples kept in memory, keyed by bare name and invalidated
    by the file's mtime+size -- the same "stat it, don't watch it"
    hot-reload shape `config_store.ConfigStore` already uses, so replacing
    a WAV on disk takes effect on the next note without a restart.

    A name that cannot be read caches its own failure (as `None`) so a
    kit with a missing sample doesn't re-stat and re-fail on every single
    note-on; the stat itself is what re-validates it once the file
    appears."""

    def __init__(self, directory=None):
        self.directory = directory
        self._entries = {}

    def path_for(self, name):
        return patch_format.sample_path(name, self.directory)

    def get(self, name):
        """The decoded `Sample` for a zone's bare sample name, or `None`
        when it is absent or unreadable."""
        path = self.path_for(name)
        if not path:
            return None
        try:
            stat = os.stat(path)
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        cached = self._entries.get(name)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        sample = wav_io.read_wav(path) if stamp is not None else None
        self._entries[name] = (stamp, sample)
        return sample

    def clear(self):
        self._entries = {}

    def preload(self, patch):
        """Decode every zone's sample up front. Worth doing when a kit is
        selected rather than on the first hit of each pad: the first
        note-on of a sample would otherwise decode a file inside the
        audio callback's own deadline. Returns the bare names that could
        not be loaded (the same list `patch_format.missing_samples()`
        reports, plus any file that exists but doesn't decode)."""
        unavailable = []
        for zone in patch.zones:
            if not zone.sample:
                continue
            if self.get(zone.sample) is None and zone.sample not in unavailable:
                unavailable.append(zone.sample)
        return unavailable


# --- The engine -----------------------------------------------------------

class SamplerEngine:
    """`sound_engine.Engine` over a sampler `Patch`.

    Choke groups are handled *here* rather than in `sound_engine`'s voice
    manager on purpose: a choke group is a property of the patch's
    zones, and the voice manager deliberately knows nothing about
    patches (it counts voices and enforces a budget). So this engine
    keeps a list of the voices it has handed out, prunes the finished
    ones each note-on, and cuts the ones whose zone
    `patch_format.choked_zones()` names. A voice the manager has already
    stolen is simply never rendered again, so a stale entry here is
    harmless -- it is pruned on the next note-on either way."""

    def __init__(self, patch=None, samples_directory=None, cache=None):
        self.patch = patch if patch is not None else patch_format.new_patch(engine="sampler")
        self.cache = cache if cache is not None else SampleCache(samples_directory)
        self._active = []

    # -- patch management --------------------------------------------------

    def set_patch(self, patch):
        """Swap the kit. Already-sounding voices are left alone to finish
        -- cutting them would make every patch change a click, and a
        drum hit is short enough that it is over before it matters."""
        self.patch = patch
        return self.patch

    def unavailable_samples(self):
        """Bare names of this patch's zones whose sample is missing or
        undecodable -- what a browser/pad UI greys out. Built on
        `patch_format.missing_samples()` plus the cache's own read
        result, so a file that exists but is (say) an MP3 renamed to
        `.wav` also reports as unavailable rather than silently never
        sounding."""
        return self.cache.preload(self.patch)

    def zone_for(self, pitch, velocity=1.0):
        """The zone a note-on at this pitch/velocity would sound, or
        `None`. Delegates straight to `patch_format.select_zone()` --
        exposed so a pad grid can render which pad maps to what without
        playing a note."""
        return patch_format.select_zone(self.patch.zones, int(pitch), midi_velocity(velocity))

    # -- Engine Protocol ---------------------------------------------------

    def note_on(self, event, sample_rate):
        zone = self.zone_for(event.pitch, event.velocity)
        if zone is None:
            return SilentVoice()
        sample = self.cache.get(zone.sample)
        if sample is None or sample.frames < 2:
            return SilentVoice()
        self._choke_for(zone)
        amplitude = (
            gain_to_linear(zone.gain)
            * float(self.patch.voice.volume)
            * velocity_amplitude(event.velocity, self.patch.voice.velocity_to_amp)
        )
        voice = SamplerVoice(
            sample, event.pitch, zone.root_key, sample_rate,
            amplitude=amplitude, zone=zone,
        )
        self._active.append(voice)
        return voice

    # -- choke groups ------------------------------------------------------

    def _choke_for(self, zone):
        """Cut every still-sounding voice whose zone shares this zone's
        non-zero choke group. Returns how many were cut (a testable
        answer, and a cheap status signal)."""
        self._active = [v for v in self._active if not v.finished]
        targets = patch_format.choked_zones(self.patch.zones, zone)
        if not targets:
            return 0
        cut = 0
        for voice in self._active:
            if voice.zone is not None and any(voice.zone is t for t in targets):
                voice.choke()
                cut += 1
        return cut

    def active_voices(self):
        """The voices this engine has handed out that are still sounding
        -- for tests and status lines, never for mutation."""
        return [v for v in self._active if not v.finished]


def midi_velocity(velocity):
    """A `sound_engine.NoteOn`'s 0..1 velocity as the 0..127 integer
    `patch_format`'s velocity bands are written in (decision #106's
    schema is MIDI-shaped; the event model normalizes, see #105). Full
    velocity maps to 127 exactly, so a zone banded `low_vel = 96` is
    reachable from QWERTY's full-velocity playing (#107)."""
    return int(round(min(max(float(velocity), 0.0), 1.0) * 127))
