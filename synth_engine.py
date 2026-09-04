"""The subtractive synth engine (map #99, build ticket #113, implementing
research #103's recommended signal path and decision #111's `[synth]`
extra): mip-mapped wavetable oscillators, a 2-pole state-variable filter
run by `scipy.signal.lfilter`, DAHDSR envelopes and one LFO, assembled
into a concrete `sound_engine.Engine`/`Voice` pair.

    osc1 (mip wavetable)  \\
    osc2 (mip wavetable)   >-- mix --> 2-pole SVF --> amp env x velocity x volume
    noise (white/pink)    /              ^  ^
                                         |  |
                        filter env (control rate) + LFO + key tracking

This replaces `tone_engine.py` as `sound_engine._default_engine()`'s
choice; the `Engine` Protocol is exactly what let that be a one-line
swap. Its parameters arrive as `patch_format.Patch`'s dataclasses
(#115), one field for one knob, so a hand-edited patch TOML drives this
module with no translation layer in between.

**Three rates, as #103 found is conventional** (torchsynth and
FluidSynth independently):

- *event rate* -- note-on/note-off, owned by `sound_engine.VoiceManager`;
- *control rate* -- one value per `config.SYNTH_CONTROL_SUB_BLOCK` (64)
  samples for everything that becomes a *filter coefficient* or an
  *oscillator frequency*: the filter envelope, the LFO, key tracking,
  velocity, glide. 64 samples is #103's measured price knee (+1.98ms at
  32 voices, ~17% of the block budget, versus +0.40ms for block-rate
  coefficients -- the extra ~1.5ms buys 689Hz of modulation rate instead
  of 86Hz, which is the difference between a smooth filter sweep and an
  audibly stepped one), and is independently corroborated by
  FluidSynth's own `FLUID_BUFSIZE = 64`;
- *audio rate* -- the amp envelope and the amp LFO, which are plain
  elementwise gains and therefore cost ~0.13% of a block, while a
  per-block-constant gain is audibly steppy on a fast attack.

**Why wavetables rather than PolyBLEP.** #103 measured mip-mapped
wavetables 60-90 dB cleaner than PolyBLEP (-86..-118 dBc of alias
energy versus -25..-53), and #100 corrected the accompanying cost claim:
they are cheaper per *voice* but ~10% slower per *block* once vectorized
across voices, because each voice gathers from its own mip band. The
cleanliness is why they stay. The tables are built once per
(waveform, sample rate) by inverse FFT of an exact harmonic series --
not by summing sine arrays in a Python loop as the #100 prototype did --
so the startup cost #103 flagged as unmeasured is ~1ms for a whole
band set, not the hundreds of milliseconds a per-partial loop would
have cost.

**Why the filter is an SVF run through `lfilter`.** An IIR recurrence
cannot be vectorized along time by any arrangement of NumPy ops (#103):
a scalar Python loop costs 594us/block, and the obvious escape --
loop over time, vectorize across voices -- has a ~5ms floor at *one*
voice, 43% of the budget. `scipy.signal.lfilter` with a persistent
per-voice `zi` runs the same recurrence in C at 10.9us. The coefficients
are the bilinear transform of the analog 2-pole state-variable filter
(`svf_coefficients()`), which yields lowpass, highpass and bandpass from
one denominator -- the "one extra output tap" property that makes
`filter.type` free. **The nonlinear Moog ladder is unavailable** and is
recorded as a known limitation rather than an oversight: its saturation
sits inside the feedback loop, so it is not a fixed-coefficient LTI
recurrence and `lfilter` cannot run it at all.

**SciPy is not a core dependency (#111).** It arrives with the `[synth]`
extra, isolated to this module exactly as `librosa` is isolated to
`batch_transcribe.py`/`rhythm_reanalysis.py` and `music21` to
`score_writer.py`/`score_editor_state.py`: imported lazily inside
`_signal()`, never at module import, so importing this module (and
unit-testing everything in it that is not the filter) costs nothing on
an install that never asked for a synth. When SciPy is missing,
`require_scipy()` raises `SynthUnavailable` carrying the install line --
`SynthEngine()` calls it in its constructor, so the synth refuses to
*open* rather than opening filterless, which is `synthplayer`'s failure
mode and precisely why the closest prior art reads as a toy (#111).

Per this repo's "pure logic unit-tested, real I/O smoke-tested"
convention, every part of this module is numerically testable with the
machine muted and is tested that way (`tests/test_synth_engine.py`):
oscillator cleanliness by FFT against the alias floor, filter behaviour
against the analytic response of its own coefficients, envelope stage
timings sample by sample, and render-in-one-block versus render-in-two
as the direct test that voice state really does survive a block
boundary.
"""

import math
import threading

import numpy as np

import config
import patch_format
from sound_engine import frequency_for


# --------------------------------------------------------------------------
# SciPy isolation (#111) -- the librosa/music21 idiom
# --------------------------------------------------------------------------

INSTALL_HINT = "pip install -e '.[synth]'"

_SIGNAL = None


class SynthUnavailable(RuntimeError):
    """Raised when the subtractive synth cannot run because SciPy is not
    installed. Carries the install line, so a caller can print it
    verbatim -- the synth tool's refusal message (#111)."""


def _signal():
    """`scipy.signal`, imported on first use. Lazy for the same reason
    `batch_transcribe.py` keeps `librosa` out of module scope: a real,
    one-time import cost with no business being paid by an install that
    never opens a synth."""
    global _SIGNAL
    if _SIGNAL is None:
        try:
            from scipy import signal
        except ImportError as exc:
            raise SynthUnavailable(
                "The subtractive synth needs SciPy for its resonant filter "
                "(there is no pure-NumPy substitute for a per-sample IIR "
                f"recurrence). Install it with: {INSTALL_HINT}"
            ) from exc
        _SIGNAL = signal
    return _SIGNAL


def scipy_available():
    """True if the synth can actually run. Cheap to call repeatedly (the
    module caches the import), and never raises -- a menu entry asking
    'can I offer this?' should not have to catch anything."""
    try:
        _signal()
    except SynthUnavailable:
        return False
    return True


def require_scipy():
    """Raise `SynthUnavailable` unless the synth can run. The one call a
    surface makes *before* opening (#111: refuse to open, never open
    filterless)."""
    _signal()
    return True


# --------------------------------------------------------------------------
# Mip-mapped wavetables (#103 recommendation 2)
# --------------------------------------------------------------------------

#: Lowest fundamental any band must serve -- MIDI note 0, so the whole
#: MIDI range is covered by `config.SYNTH_MIP_BANDS` octave bands.
MIP_BASE_HZ = frequency_for(0)

#: Waveforms that have a table of their own. A square/pulse is read from
#: the saw table (see `pulse_from_saw()`).
TABLE_WAVEFORMS = ("saw", "triangle", "sine")

_TABLE_CACHE = {}
_TABLE_LOCK = threading.Lock()


def table_waveform(waveform):
    """The table a patch waveform reads: itself, except that a square
    reads the saw table."""
    return "saw" if waveform == "square" else waveform


def _harmonic_amplitudes(waveform, partials):
    """The ideal (unbandlimited) harmonic series of `waveform`, truncated
    to `partials` harmonics: amplitude of the k-th sine partial, index 0
    unused. Sine/saw/triangle only -- a square is synthesized at read
    time from two saw reads (see `pulse_from_saw()`), which is what lets
    `pulse_width` be a continuous parameter without a table per width."""
    amps = np.zeros(partials + 1)
    if partials < 1:
        return amps
    if waveform == "sine":
        amps[1] = 1.0
        return amps
    k = np.arange(1, partials + 1)
    if waveform == "triangle":
        odd = k % 2 == 1
        signs = np.where(((k - 1) // 2) % 2 == 0, 1.0, -1.0)
        amps[1:] = np.where(odd, (8.0 / np.pi ** 2) * signs / k ** 2, 0.0)
    else:  # saw (2p - 1 on [0,1)) -- also the source table a pulse is built from
        amps[1:] = -(2.0 / np.pi) / k
    return amps


def band_top_hz(band):
    """The highest fundamental band `band` serves."""
    return MIP_BASE_HZ * (2.0 ** (band + 1))


def band_partials(band, sample_rate, size):
    """How many partials band `band`'s table holds: every one that stays
    under Nyquist at the *top* of the band, capped at `size // 4` so the
    highest partial still has >= 4 table samples per cycle -- past that,
    linear interpolation's own error (not aliasing) starts to dominate
    the read."""
    nyquist = sample_rate * 0.5
    partials = int(nyquist / band_top_hz(band))
    return max(1, min(partials, size // 4))


def build_tables(waveform, sample_rate, size=None, bands=None):
    """One band-limited table per octave band, stacked `(bands, size)`.

    Band `b` serves fundamentals up to `band_top_hz(b)` and therefore
    holds only the partials that stay under Nyquist *at the top of that
    band* -- which is the standard wavetable tradeoff #103 describes: the
    top note of each band is slightly duller than ideal, and that is
    inaudible next to PolyBLEP's -25 dBc of inharmonic grit.

    Built by `np.fft.irfft` of the exact harmonic series rather than by
    summing `partials` sine arrays in a Python loop (what the #100
    prototype did): identical output, but ~1ms for the whole set instead
    of the hundreds of milliseconds #103 flagged as unmeasured startup
    latency. Tables are *not* peak-normalized -- normalizing would make
    the sparse high bands louder than the dense low ones, since Gibbs
    overshoot shrinks with partial count, and a level that changes as you
    play up the keyboard is worse than 9% of headroom."""
    size = size or config.SYNTH_TABLE_SIZE
    bands = bands or config.SYNTH_MIP_BANDS
    tables = np.zeros((bands, size), dtype=np.float64)
    for band in range(bands):
        partials = band_partials(band, sample_rate, size)
        amps = _harmonic_amplitudes(waveform, partials)
        spectrum = np.zeros(size // 2 + 1, dtype=np.complex128)
        # irfft's x[j] = (1/n)(X0 + 2*sum Re(X_k e^{i2*pi*k*j/n})), so a
        # sine partial of amplitude A_k needs X_k = -i*n*A_k/2.
        spectrum[1:partials + 1] = -1j * size * amps[1:] / 2.0
        tables[band] = np.fft.irfft(spectrum, n=size)
    return tables


def tables_for(waveform, sample_rate):
    """The cached mip set for one waveform. Built once per
    (waveform, sample rate) for the process's whole life -- a voice must
    never build a table at note-on."""
    key = (table_waveform(waveform), int(sample_rate))
    with _TABLE_LOCK:
        if key not in _TABLE_CACHE:
            _TABLE_CACHE[key] = build_tables(key[0], sample_rate)
        return _TABLE_CACHE[key]


def mip_level_for(freq_hz, bands=None):
    """Which band a fundamental of `freq_hz` must read: the lowest band
    whose own top note is at or above it, clamped to the table set."""
    bands = bands or config.SYNTH_MIP_BANDS
    freq = max(float(freq_hz), MIP_BASE_HZ)
    level = math.ceil(math.log2(freq / MIP_BASE_HZ) - 1e-9) - 1
    return min(max(level, 0), bands - 1)


def phase_array(phase, dts):
    """Per-sample phases (cycles) for a run of per-sample increments
    `dts`, starting at `phase`; returns `(phases, next_phase)`. Wrapped
    to `[0,1)` rather than left to grow: float64 loses sub-sample phase
    resolution over a minutes-long hold otherwise."""
    steps = np.cumsum(dts)
    phases = phase + steps - dts
    return phases % 1.0, (phase + steps[-1]) % 1.0


def table_lookup(table, phases):
    """Linearly-interpolated reads of one band's table at `phases`
    (cycles, any real values -- wrapped here)."""
    size = table.shape[0]
    pos = (phases % 1.0) * size
    i0 = pos.astype(np.int64)
    frac = pos - i0
    a = table[i0]
    b = table[(i0 + 1) % size]
    return a + frac * (b - a)


def read_table(table, phase, dt, frames):
    """`frames` samples from one band's table at a fixed increment `dt`
    (cycles per sample), starting at `phase`. Returns
    `(samples, next_phase)`. The fixed-frequency special case of
    `table_lookup()` over `phase_array()`."""
    phases, next_phase = phase_array(phase, np.full(frames, dt, dtype=np.float64))
    return table_lookup(table, phases), next_phase


def pulse_from_saw(table, phases, width):
    """A band-limited pulse of arbitrary duty cycle, built from two reads
    of the *saw* table a duty cycle apart:

        pulse(p, d) = saw(p - d) - saw(p) + (2d - 1)

    (an ideal saw being `2p-1`, the two ramps cancel to a flat +1 for
    `p < d` and a flat -1 above it). Exactly as band-limited as the saw
    table it reads, and `width` is continuous -- which is why the square
    waveform has no table of its own: a table set per pulse width would
    be one set per patch parameter value, and PWM would be impossible."""
    return table_lookup(table, phases - width) - table_lookup(table, phases) + (2.0 * width - 1.0)


# --------------------------------------------------------------------------
# The 2-pole state-variable filter (#103 recommendation 3)
# --------------------------------------------------------------------------

def resonance_to_damping(resonance):
    """A patch's `resonance` (0..1) -> the SVF's damping `k = 1/Q`,
    linear from `SYNTH_DAMPING_MAX` (Butterworth, flat) down to
    `SYNTH_DAMPING_MIN` (Q = 10). Linear in `k` rather than in Q, so the
    knob's *audible* effect (how peaked the response is at cutoff, which
    is `1/k` in dB terms) is roughly even across the range instead of
    doing nothing for the first three quarters of the travel."""
    r = min(max(float(resonance), 0.0), 1.0)
    return config.SYNTH_DAMPING_MAX + (config.SYNTH_DAMPING_MIN - config.SYNTH_DAMPING_MAX) * r


def svf_damping_coefficients(cutoff_hz, damping, sample_rate, filter_type="lp"):
    """`(b, a)` (plain 3-tuples, already normalized so `a[0] == 1`) for
    one 2-pole state-variable filter section, as the bilinear transform of the analog SVF `1 / (s^2 + k*s + 1)` with the
    standard `g = tan(pi*fc/sr)` prewarp -- so the -3dB/peak point lands
    at exactly `cutoff_hz`, not at a bilinear-warped approximation of it.
    `damping` is `k = 1/Q` directly; `svf_coefficients()` takes a patch's
    0..1 resonance instead.

    All three outputs share one denominator and differ only in the
    numerator (`g^2 (1+z^-1)^2` lowpass, `(1-z^-1)^2` highpass,
    `g (1-z^-2)` bandpass, the last being `s/(s^2+ks+1)` so its peak
    gain at cutoff is `1/k`, the same as the lowpass's own peak) -- the
    "one structure, one extra output tap" property that makes
    `filter.type` a free parameter (#103 s5).

    Cutoff is clamped into `[SYNTH_CUTOFF_MIN_HZ, 0.45*sr]`: the prewarp
    diverges as `fc` approaches Nyquist, and a modulated cutoff routinely
    tries to go there."""
    fc = min(max(float(cutoff_hz), config.SYNTH_CUTOFF_MIN_HZ), sample_rate * 0.45)
    k = max(float(damping), 1e-4)
    g = math.tan(math.pi * fc / sample_rate)
    inv = 1.0 / (1.0 + k * g + g * g)
    a = (1.0, 2.0 * (g * g - 1.0) * inv, (1.0 - k * g + g * g) * inv)
    if filter_type == "hp":
        b = (inv, -2.0 * inv, inv)
    elif filter_type == "bp":
        b = (g * inv, 0.0, -g * inv)
    else:
        gg = g * g * inv
        b = (gg, 2.0 * gg, gg)
    return b, a


def svf_coefficients(cutoff_hz, resonance, sample_rate, filter_type="lp"):
    """`svf_damping_coefficients()` for a patch's 0..1 `resonance`."""
    return svf_damping_coefficients(cutoff_hz, resonance_to_damping(resonance), sample_rate, filter_type)


#: A 3rd-order IIR approximation of a 1/f ("pink") spectrum, from Julius
#: O. Smith's CCRMA notes on pink noise (the classic `B`/`A` pair quoted
#: in *Spectral Audio Signal Processing*): measured -3.1 dB/octave from
#: 100Hz to 12.8kHz through `scipy.signal.freqz`. Runs in `lfilter` with
#: a persistent `zi` exactly like the voice filter, so pink noise costs
#: no Python loop.
PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])

_RNG = np.random.default_rng()


def seed_noise(seed):
    """Reseed the module's noise generator -- for deterministic tests
    only. Production never calls it."""
    global _RNG
    _RNG = np.random.default_rng(seed)


# --------------------------------------------------------------------------
# DAHDSR envelope (#103 s4/s5, SF2's generator set)
# --------------------------------------------------------------------------

DELAY, ATTACK, HOLD, DECAY, SUSTAIN, RELEASE, DONE = range(7)

STAGE_NAMES = {
    DELAY: "delay", ATTACK: "attack", HOLD: "hold", DECAY: "decay",
    SUSTAIN: "sustain", RELEASE: "release", DONE: "done",
}


class DahdsrEnvelope:
    """A resumable delay-attack-hold-decay-sustain-release envelope: SF2's
    two extra stages ahead of a conventional ADSR (#103 s5 -- cheap, and
    worth having since the same voice model is meant to host the SF2
    engine later).

    Resumable is the whole point: a note-off can arrive mid-block, so the
    envelope cannot be a function of "time since onset". It carries its
    stage and its current level between blocks and emits the next N
    samples from wherever it was (`block()`), or advances without
    materializing them at all (`advance()`, for the control-rate filter
    envelope, which is sampled once per sub-block and never needs an
    array). Both go through the same segment walk, so the two can never
    drift apart. Every ramp is linear, matching `tone_engine.ToneVoice`
    and `playback._adsr_envelope()`.

    `sustain <= 0` ends the note at the end of decay rather than sitting
    at silence forever -- a voice that can never be heard again should
    give its polyphony slot back, which is what a hardware synth and
    FluidSynth's own -100dB voice kill both do."""

    def __init__(self, spec, sample_rate):
        self.sample_rate = sample_rate
        self.delay_samples = int(round(spec.delay * sample_rate))
        self.hold_samples = int(round(spec.hold * sample_rate))
        self.attack_samples = max(1, int(round(spec.attack * sample_rate)))
        self.decay_samples = max(1, int(round(spec.decay * sample_rate)))
        self.release_samples = max(1, int(round(spec.release * sample_rate)))
        self.sustain = min(max(float(spec.sustain), 0.0), 1.0)

        self.level = 0.0
        self.stage = DELAY if self.delay_samples > 0 else ATTACK
        self._counter = self.delay_samples
        self._release_rate = 0.0

    # -- state ------------------------------------------------------------

    @property
    def finished(self):
        return self.stage == DONE

    @property
    def released(self):
        return self.stage in (RELEASE, DONE)

    def note_off(self):
        """Begin the release from wherever the envelope currently is, so
        the fade always takes exactly `release` seconds regardless of
        which stage it interrupted. Idempotent: a second note-off must
        not restart the fade (which would let repeated note-offs ring a
        note on indefinitely) -- the same rule `tone_engine.ToneVoice`
        follows."""
        if self.stage in (RELEASE, DONE):
            return
        self.stage = RELEASE
        self._release_rate = self.level / self.release_samples

    # -- rendering --------------------------------------------------------

    def block(self, frames):
        """The next `frames` envelope samples, audio rate."""
        out = np.empty(frames, dtype=np.float64)
        self._walk(frames, out)
        return out

    def advance(self, frames):
        """Advance `frames` samples without building an array, returning
        the level the envelope *started* from -- the control-rate value
        that holds for the sub-block just rendered."""
        value = self.level
        self._walk(frames, None)
        return value

    def _walk(self, frames, out):
        filled = 0
        while filled < frames:
            remaining = frames - filled
            if self.stage in (DELAY, HOLD):
                take = min(remaining, self._counter)
                if take <= 0:
                    self._leave_flat_stage()
                    continue
                if out is not None:
                    out[filled:filled + take] = self.level
                self._counter -= take
                filled += take
                if self._counter <= 0:
                    self._leave_flat_stage()
                continue
            if self.stage in (SUSTAIN, DONE):
                self.level = self.sustain if self.stage == SUSTAIN else 0.0
                if out is not None:
                    out[filled:] = self.level
                return
            target, rate, next_stage = self._segment()
            if rate <= 0.0:
                self.level = target
                self._enter(next_stage)
                continue
            needed = int(math.ceil(abs(target - self.level) / rate))
            take = min(remaining, max(needed, 0))
            if take == 0:
                self.level = target
                self._enter(next_stage)
                continue
            step = rate if target > self.level else -rate
            if out is not None:
                out[filled:filled + take] = self.level + step * np.arange(1, take + 1)
            self.level += step * take
            filled += take
            if take >= needed:
                self.level = target
                self._enter(next_stage)

    def _segment(self):
        """(target level, per-sample rate, stage entered on arrival) for
        the stage currently ramping."""
        if self.stage == ATTACK:
            return 1.0, 1.0 / self.attack_samples, HOLD
        if self.stage == DECAY:
            after = SUSTAIN if self.sustain > 0.0 else DONE
            return self.sustain, max(1.0 - self.sustain, 1e-12) / self.decay_samples, after
        return 0.0, self._release_rate, DONE

    def _enter(self, stage):
        self.stage = stage
        if stage == HOLD:
            self.level = 1.0
            self._counter = self.hold_samples
            if self._counter <= 0:
                self.stage = DECAY
        elif stage == DONE:
            self.level = 0.0

    def _leave_flat_stage(self):
        if self.stage == DELAY:
            self.level = 0.0
            self.stage = ATTACK
        else:  # HOLD
            self.level = 1.0
            self.stage = DECAY


# --------------------------------------------------------------------------
# LFO (#103 s4/s5, SF2's GEN_MODLFO*)
# --------------------------------------------------------------------------

def lfo_shape(waveform, phase):
    """One LFO waveform's bipolar value at `phase` (cycles).

    Sine and triangle start at 0, so an LFO with either fades in from no
    modulation rather than jumping to an extreme at note-on. Saw and
    square cannot: neither shape *has* a zero at phase 0 (a square that
    started at 0 would not be a square), so both begin at an extreme --
    -1 and +1 respectively. `Lfo.delay` is the parameter that holds off
    modulation for those two, not the waveform's own starting value."""
    p = phase % 1.0
    if waveform == "triangle":
        if p < 0.25:
            return 4.0 * p
        if p < 0.75:
            return 2.0 - 4.0 * p
        return 4.0 * p - 4.0
    if waveform == "saw":
        return 2.0 * p - 1.0
    if waveform == "square":
        return 1.0 if p < 0.5 else -1.0
    return math.sin(2.0 * math.pi * p)


class Lfo:
    """One low-frequency oscillator: rate, depth, delay, waveform and a
    single destination (pitch / filter / amp), which is the parameter set
    both MIDI CC 76-78 and SF2's `GEN_MODLFO*` generators describe.

    Evaluated at control rate -- once per sub-block, i.e. 689Hz at the
    defaults, three orders of magnitude above the few-Hz modulation
    itself. Its *phase persists across blocks* and its delay is counted
    per voice from note-on; restarting either at a block boundary
    produces a buzz at the block rate that is easy to mistake for a
    filter problem (#103 flags exactly this as having bitten both cited
    projects)."""

    def __init__(self, spec, sample_rate):
        self.rate = max(0.0, float(spec.rate))
        self.depth = min(max(float(spec.depth), 0.0), 1.0)
        self.waveform = spec.waveform
        self.destination = spec.destination
        self.delay_samples = int(round(spec.delay * sample_rate))
        self.sample_rate = sample_rate
        self.phase = 0.0
        self._elapsed = 0

    @property
    def active(self):
        """False while the per-voice delay is still counting down."""
        return self._elapsed >= self.delay_samples

    def value(self):
        """The current bipolar output, already scaled by `depth`. 0 while
        the delay is still counting down."""
        if not self.active:
            return 0.0
        return self.depth * lfo_shape(self.waveform, self.phase)

    def tremolo(self):
        """The current amp-destination gain: attenuates from unity by up
        to `depth` rather than boosting past it, so an amp LFO can never
        push a voice into the master soft-clip on its own. Unity during
        the delay."""
        if not self.active:
            return 1.0
        return 1.0 - self.depth * (1.0 - lfo_shape(self.waveform, self.phase)) * 0.5

    def step(self, frames):
        """`(value, tremolo)` holding for the sub-block about to be
        rendered, then advance the phase (and the delay countdown) by
        `frames`. Value first, then advance -- so a sub-block's
        coefficients are computed from the modulation state at its own
        start rather than from one sub-block into its future."""
        if self.depth == 0.0:
            value, gain = 0.0, 1.0
        else:
            value, gain = self.value(), self.tremolo()
        self._elapsed += frames
        if self.active:
            self.phase = (self.phase + self.rate * frames / self.sample_rate) % 1.0
        return value, gain


# --------------------------------------------------------------------------
# The voice
# --------------------------------------------------------------------------

def oscillator_frequency(base_hz, osc):
    """A patch oscillator's own frequency: the note's pitch shifted by
    `octave`/`semitones`/`fine` (cents). Detuning osc2 a few cents
    against osc1 is *the* standard way to get a fat sound and is why
    `fine` exists (#103 s5)."""
    return base_hz * 2.0 ** (osc.octave + osc.semitones / 12.0 + osc.fine / 1200.0)


def modulated_cutoff(patch, pitch, velocity, filter_env, lfo_filter):
    """The voice's cutoff for one control sub-block, in Hz.

    Everything modulates it in *octaves* rather than in Hz, because pitch
    is logarithmic and a fixed Hz offset means something completely
    different at C1 and at C7. The four contributions:

    - `filter.env_amount` x the filter envelope, up to
      `SYNTH_FILTER_ENV_OCTAVES` (bipolar: a negative amount closes the
      filter as the envelope opens, which is how a plucked-then-dulled
      sound is made);
    - the LFO, when its destination is the filter, up to
      `SYNTH_LFO_FILTER_OCTAVES`;
    - `filter.key_tracking` x how far this note is above middle C, at
      100% meaning "the cutoff follows the note exactly" -- without it
      high notes sound muffled relative to low ones (#103 s5);
    - `voice.velocity_to_filter`, which *closes* the filter at low
      velocity rather than opening it above nominal, so velocity 1.0 is
      always the patch's stated cutoff and never an over-bright surprise.
    """
    octaves = patch.filter.env_amount * filter_env * config.SYNTH_FILTER_ENV_OCTAVES
    octaves += lfo_filter * config.SYNTH_LFO_FILTER_OCTAVES
    octaves += patch.filter.key_tracking * (pitch - 60) / 12.0
    octaves -= patch.voice.velocity_to_filter * (1.0 - velocity) * config.SYNTH_VELOCITY_FILTER_OCTAVES
    return patch.filter.cutoff * (2.0 ** octaves)


class SynthVoice:
    """One sounding note of the subtractive synth. Satisfies
    `sound_engine.Voice`; see that module for the Protocol.

    Its persistent state is #103's recommendation 5 verbatim -- the two
    oscillator phases, the filter's `zi` (and the pink-noise filter's),
    both envelopes' stage and level, the LFO's phase and delay countdown,
    the glide's progress, and the note's own identity and velocity.
    Everything that makes a voice a *resumable* object rather than a
    rendered buffer lives here.

    `render()` walks the block in `sub_block`-sample control sub-blocks:
    per sub-block it steps the LFO, the filter envelope and the glide,
    derives one oscillator increment and one set of filter coefficients,
    and runs `lfilter` with the carried `zi`. The oscillators themselves
    are gathered for the *whole* block in one vectorized read per
    oscillator (their per-sample phase is just the cumulative sum of the
    per-sub-block increments), from the mip band of the block's highest
    modulated frequency -- so a deep pitch LFO or a glide cannot walk a
    note off the top of its band and alias. The amp envelope and the
    tremolo gain are applied at audio rate after the filter.

    `glide_from_hz`, when given, starts the note at that frequency and
    slides it to its own over `voice.glide` seconds (linear in semitones
    per sub-block); `SynthEngine` supplies the previous note's frequency.
    """

    def __init__(self, event, sample_rate, patch=None, sub_block=None, glide_from_hz=None):
        require_scipy()
        self.patch = patch if patch is not None else patch_format.new_patch()
        self.sample_rate = sample_rate
        self.sub_block = sub_block or config.SYNTH_CONTROL_SUB_BLOCK
        self.pitch = event.pitch
        self.velocity = min(max(float(event.velocity), 0.0), 1.0)
        self.base_frequency = frequency_for(event.pitch)

        p = self.patch
        self._osc_specs = (p.osc1, p.osc2)
        self._osc_freqs = [oscillator_frequency(self.base_frequency, o) for o in self._osc_specs]
        self._osc_levels = [o.level for o in self._osc_specs]
        self._osc_tables = [tables_for(o.waveform, sample_rate) for o in self._osc_specs]
        self._phases = [0.0, 0.0]
        self._noise_level = p.noise.level
        self._pink_zi = np.zeros(len(PINK_A) - 1)

        # Sum of the sources' own levels, so one oscillator at level 1.0
        # is unity and three sources at 1.0 do not sum to 3x -- gain
        # staging in the voice rather than leaving it to the master tanh,
        # which would otherwise distort a fat patch and not a thin one.
        self._source_scale = 1.0 / max(1.0, sum(self._osc_levels) + self._noise_level)

        self.amp_env = DahdsrEnvelope(p.amp_env, sample_rate)
        self.filter_env = DahdsrEnvelope(p.filter_env, sample_rate)
        self.lfo = Lfo(p.lfo, sample_rate)
        self._zi = np.zeros(2)
        self._released = False
        self._tremolo = 1.0
        self._sub_offset = 0

        # Glide: a semitone offset that decays linearly to zero.
        self._glide_samples = int(round(p.voice.glide * sample_rate))
        self._glide_elapsed = 0
        if glide_from_hz and self._glide_samples > 0 and glide_from_hz > 0.0:
            self._glide_offset = 12.0 * math.log2(glide_from_hz / self.base_frequency)
        else:
            self._glide_offset = 0.0

        # velocity -> amp: at `velocity_to_amp` 0 a patch ignores velocity
        # entirely (an organ), at 1.0 velocity 0 is silence.
        self._amp_gain = p.voice.volume * (1.0 - p.voice.velocity_to_amp * (1.0 - self.velocity))

    # -- Voice Protocol ----------------------------------------------------

    @property
    def released(self):
        return self._released

    @property
    def finished(self):
        return self.amp_env.finished

    def amplitude(self):
        return self.amp_env.level * self._amp_gain

    def note_off(self):
        if self._released:
            return
        self._released = True
        self.amp_env.note_off()
        self.filter_env.note_off()

    def render(self, out, frames):
        if frames <= 0 or self.finished:
            return
        signal = _signal()
        destination = self.lfo.destination
        patch = self.patch

        # -- control rate: one entry per sub-block --------------------------
        lengths = self._sub_block_lengths(frames)
        starts = np.cumsum([0] + lengths[:-1])
        pitch_ratios = np.empty(len(lengths))
        cutoffs = []
        amp_mod = np.empty(frames, dtype=np.float64) if destination == "amp" else None
        for i, (pos, n) in enumerate(zip(starts, lengths)):
            lfo_value, tremolo = self.lfo.step(n)
            env_value = self.filter_env.advance(n)
            semitones = self._glide_semitones(n)
            if destination == "pitch":
                semitones += lfo_value * config.SYNTH_LFO_PITCH_SEMITONES
            pitch_ratios[i] = 2.0 ** (semitones / 12.0) if semitones else 1.0
            if amp_mod is not None:
                amp_mod[pos:pos + n] = np.linspace(self._tremolo, tremolo, n, endpoint=False)
                self._tremolo = tremolo
            cutoffs.append(modulated_cutoff(
                patch, self.pitch, self.velocity, env_value,
                lfo_value if destination == "filter" else 0.0,
            ))

        # -- audio rate ------------------------------------------------------
        buf = self._sources(frames, lengths, pitch_ratios)
        # Consecutive sub-blocks with an identical cutoff (a held note
        # whose filter envelope has reached sustain, with no filter LFO --
        # most of a note's life) share one lfilter call: the coefficients
        # are what the sub-block granularity exists to update, so where
        # they do not change there is nothing to pay for.
        pos = 0
        run_start, run_cutoff = 0, cutoffs[0]
        for i, n in enumerate(lengths):
            if cutoffs[i] != run_cutoff:
                self._filter_run(signal, buf, run_start, pos, run_cutoff)
                run_start, run_cutoff = pos, cutoffs[i]
            pos += n
        self._filter_run(signal, buf, run_start, pos, run_cutoff)

        buf *= self.amp_env.block(frames)
        if amp_mod is not None:
            buf *= amp_mod
        buf *= self._amp_gain
        out[:frames] += buf.astype(out.dtype, copy=False)

    def _sub_block_lengths(self, frames):
        """The control sub-block lengths covering the next `frames`
        samples, *resuming* the grid where the previous `render()` left
        it rather than restarting it at every block boundary.

        With this app's own constant 512-sample block and a 64-sample
        sub-block the offset is always 0, so this is identity for the
        real audio path. It matters for any caller whose block size is
        not a multiple of `sub_block` -- a short final callback block, a
        reconfigured `SoundEngine.block_size`, a test rendering ragged
        chunks: restarting the grid there re-times every filter
        coefficient update against the block boundary instead of against
        the note, which makes the voice's output depend on how its
        samples happened to be cut up. Carrying the offset is what makes
        "one block of 1024 equals two blocks of 512" true for *every*
        split rather than only the aligned ones."""
        lengths = []
        remaining = frames
        take = min(self.sub_block - self._sub_offset, remaining)
        while remaining > 0:
            lengths.append(take)
            remaining -= take
            take = min(self.sub_block, remaining)
        self._sub_offset = (self._sub_offset + frames) % self.sub_block
        return lengths

    def _filter_run(self, signal, buf, start, stop, cutoff):
        """Runs the SVF over `buf[start:stop]` in place at one fixed
        cutoff, carrying `zi` across calls."""
        b, a = svf_coefficients(cutoff, self.patch.filter.resonance, self.sample_rate,
                                self.patch.filter.type)
        buf[start:stop], self._zi = signal.lfilter(b, a, buf[start:stop], zi=self._zi)

    # -- modulation helpers -------------------------------------------------

    def _glide_semitones(self, frames):
        """The glide's semitone offset for the sub-block about to render,
        then advance it."""
        if self._glide_offset == 0.0 or self._glide_elapsed >= self._glide_samples:
            return 0.0
        remaining = 1.0 - self._glide_elapsed / self._glide_samples
        self._glide_elapsed += frames
        return self._glide_offset * remaining

    # -- sources -----------------------------------------------------------

    def _sources(self, frames, lengths, pitch_ratios):
        """Osc1 + osc2 + noise for one block, already level-balanced.
        `pitch_ratios` holds one frequency multiplier per sub-block."""
        mix = np.zeros(frames, dtype=np.float64)
        for index, spec in enumerate(self._osc_specs):
            level = self._osc_levels[index]
            if level <= 0.0:
                continue
            freqs = self._osc_freqs[index] * pitch_ratios
            dts = np.repeat(freqs / self.sample_rate, lengths)
            phases, self._phases[index] = phase_array(self._phases[index], dts)
            band = self._osc_tables[index][mip_level_for(freqs.max())]
            if spec.waveform == "square":
                wave = pulse_from_saw(band, phases, spec.pulse_width)
            else:
                wave = table_lookup(band, phases)
            mix += level * wave
        if self._noise_level > 0.0:
            mix += self._noise_level * self._noise(frames)
        mix *= self._source_scale
        return mix

    def _noise(self, frames):
        white = _RNG.uniform(-1.0, 1.0, frames)
        if self.patch.noise.colour != "pink":
            return white
        pink, self._pink_zi = _signal().lfilter(PINK_B, PINK_A, white, zi=self._pink_zi)
        return pink * config.SYNTH_PINK_GAIN


class SynthEngine:
    """`sound_engine.Engine` over `SynthVoice`, one voice per note-on.

    Holds a default patch plus an optional name -> `Patch` mapping, which
    is how a `NoteOn`'s `patch` field is resolved; an unknown name falls
    back to the default rather than raising, the same
    degrade-don't-crash posture `patch_format.load_patch()` takes for a
    missing file. The only state the engine keeps between notes is the
    last note's frequency, for `voice.glide` -- everything else lives on
    the voices, so one instance serves the process-wide `SoundEngine`.

    Constructing one calls `require_scipy()`: without SciPy there is no
    filter, and #111's rule is that the synth then refuses to open with
    the install line rather than opening filterless."""

    def __init__(self, patch=None, patches=None, sub_block=None):
        require_scipy()
        self.patch = patch if patch is not None else default_patch()
        self.patches = dict(patches or {})
        self.sub_block = sub_block
        self._last_frequency = None

    def patch_for(self, name):
        if name is None:
            return self.patch
        return self.patches.get(name, self.patch)

    def note_on(self, event, sample_rate):
        patch = self.patch_for(event.patch)
        voice = SynthVoice(event, sample_rate, patch=patch, sub_block=self.sub_block,
                           glide_from_hz=self._last_frequency)
        self._last_frequency = voice.base_frequency
        return voice


def default_patch():
    """The synth's own starting sound: a detuned two-saw patch with a
    resonant lowpass opened by its filter envelope -- the sound every
    subtractive synth's init patch makes, chosen so that *every* stage of
    the signal path is audible in the default rather than having to be
    switched on before the engine proves it works. Built from
    `patch_format`'s own defaults so it is exactly what a hand-written
    patch file would produce."""
    patch = patch_format.new_patch(name="Init")
    patch.osc1.waveform = "saw"
    patch.osc2.waveform = "saw"
    patch.osc2.level = 0.6
    patch.osc2.fine = -7.0
    patch.filter.cutoff = 2200.0
    patch.filter.resonance = 0.35
    patch.filter.env_amount = 0.45
    patch.filter.key_tracking = 0.35
    patch.amp_env.attack = 0.008
    patch.amp_env.decay = 0.25
    patch.amp_env.sustain = 0.7
    patch.amp_env.release = 0.25
    patch.filter_env.attack = 0.005
    patch.filter_env.decay = 0.35
    patch.filter_env.sustain = 0.25
    patch.filter_env.release = 0.25
    patch.voice.volume = 0.7
    return patch
