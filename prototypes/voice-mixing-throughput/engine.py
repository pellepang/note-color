"""A throwaway, but *real*, block-based synth engine: the signal path
`docs/research/subtractive-synth-numpy.md` (ticket #103) recommends, plus
the shared effects bus `docs/research/effects-chain-delay-chorus.md`
(ticket #104) recommends, assembled so it can be driven from an actual
`sounddevice.OutputStream` callback.

Nothing here is production code -- it exists only so ticket #100 can
measure the recommended design under real audio-driver deadlines instead
of a synthetic timing loop. Structure, not polish:

    mip-wavetable osc 1 + detuned osc 2   (vectorised across all voices)
        -> per-voice resonant biquad, coefficients recomputed per
           64-sample control sub-block, run by scipy.signal.lfilter with
           persistent per-voice `zi`
        -> audio-rate resumable ADSR amp envelope
        -> sum to a mono bus
        -> shared Delay -> Chorus effects chain
        -> np.tanh soft clip

Every stage can be switched off independently (`Engine(..., use_filter=,
use_effects=, control_sub_block=)`) so the harness can price each one.
"""

import numpy as np
from scipy import signal

SAMPLE_RATE = 44100          # config.PLAYBACK_SAMPLE_RATE
CONTROL_SUB_BLOCK = 64       # FluidSynth's FLUID_BUFSIZE; #103's measured knee
TABLE_SIZE = 2048
MIP_BANDS = 10               # one per octave from ~27Hz up


# --------------------------------------------------------------------------
# Mip-mapped wavetables (#103 recommendation 2): built once, per octave band,
# by per-note additive synthesis; read with linear interpolation.
# --------------------------------------------------------------------------

def build_saw_tables(sample_rate=SAMPLE_RATE, size=TABLE_SIZE, bands=MIP_BANDS):
    """One band-limited saw table per octave band, stacked into (bands, size)
    so a whole voice array can gather from it with one fancy-index."""
    tables = np.zeros((bands, size), dtype=np.float32)
    phase = np.arange(size) / size
    for b in range(bands):
        top_hz = 27.5 * (2.0 ** (b + 1))          # highest note this band serves
        partials = max(1, int((sample_rate / 2) / top_hz))
        acc = np.zeros(size)
        for k in range(1, partials + 1):
            acc -= (2.0 / np.pi) * np.sin(2 * np.pi * k * phase) / k
        peak = np.max(np.abs(acc)) or 1.0
        tables[b] = (acc / peak).astype(np.float32)
    return tables


def mip_level_for(freq_hz, bands=MIP_BANDS):
    """Which band a note of this pitch must read (band b is safe up to
    27.5 * 2**(b+1) Hz)."""
    lvl = np.ceil(np.log2(np.maximum(freq_hz, 27.5) / 27.5)) - 1
    return np.clip(lvl, 0, bands - 1).astype(np.int64)


# --------------------------------------------------------------------------
# Effects (lifted from scripts/effects_chain_bench.py, ticket #104 -- the two
# non-negotiables kept: fractional interpolated read, LFO phase carried
# across blocks).
# --------------------------------------------------------------------------

class Delay:
    def __init__(self, sample_rate=SAMPLE_RATE, max_seconds=2.0,
                 delay_seconds=0.25, feedback=0.35, mix=0.25):
        self.buf = np.zeros(int(sample_rate * max_seconds), dtype=np.float32)
        self.write = 0
        self.delay_samples = int(delay_seconds * sample_rate)
        self.feedback = feedback
        self.mix = mix

    def process(self, block):
        n = len(block)
        size = len(self.buf)
        offsets = np.arange(n)
        read = (self.write - self.delay_samples + offsets) % size
        wet = self.buf[read]
        self.buf[(self.write + offsets) % size] = block + self.feedback * wet
        self.write = (self.write + n) % size
        return (1.0 - self.mix) * block + self.mix * wet


class Chorus:
    def __init__(self, sample_rate=SAMPLE_RATE, rate_hz=1.0, depth_ms=2.0,
                 centre_delay_ms=7.0, mix=0.4, voices=3):
        self.sample_rate = sample_rate
        self.size = int(sample_rate * 0.1)
        self.buf = np.zeros(self.size, dtype=np.float32)
        self.write = 0
        self.rate_hz = rate_hz
        self.depth = depth_ms * sample_rate / 1000.0
        self.centre = centre_delay_ms * sample_rate / 1000.0
        self.mix = mix
        self.voices = voices
        self.phase = 0.0

    def process(self, block):
        n = len(block)
        size = self.size
        offsets = np.arange(n)
        lfo = self.phase + 2.0 * np.pi * self.rate_hz * offsets / self.sample_rate
        self.buf[(self.write + offsets) % size] = block
        wet = np.zeros(n, dtype=np.float32)
        for v in range(self.voices):
            spread = 2.0 * np.pi * v / self.voices
            delay = self.centre + self.depth * np.sin(lfo + spread)
            pos = (self.write + offsets - delay) % size
            lo = np.floor(pos).astype(np.int64)
            frac = (pos - lo).astype(np.float32)
            wet += (1.0 - frac) * self.buf[lo] + frac * self.buf[(lo + 1) % size]
        wet /= self.voices
        self.phase = (self.phase + 2.0 * np.pi * self.rate_hz * n
                      / self.sample_rate) % (2.0 * np.pi)
        self.write = (self.write + n) % size
        return (1.0 - self.mix) * block + self.mix * wet


# --------------------------------------------------------------------------
# Biquad lowpass (RBJ cookbook), vectorised across voices. Only the lfilter
# call itself is a per-voice Python loop -- that is the constraint #103 found.
# --------------------------------------------------------------------------

def lowpass_coeffs(cutoff_hz, q, sample_rate=SAMPLE_RATE):
    fc = np.clip(cutoff_hz, 30.0, sample_rate * 0.45)
    w0 = 2.0 * np.pi * fc / sample_rate
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2.0 * q)
    b0 = (1.0 - cw) / 2.0
    b1 = 1.0 - cw
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cw
    a2 = 1.0 - alpha
    b = np.stack([b0, b1, b2], axis=-1) / a0[..., None]
    a = np.stack([np.ones_like(a0), a1 / a0, a2 / a0], axis=-1)
    return b, a


class Engine:
    """`voices` simultaneously-sounding voices, rendered one block at a time.

    `use_filter=False` bypasses the per-voice biquad entirely (so the
    harness can price it); `control_sub_block` sets how often filter
    coefficients are recomputed (64 = #103's recommendation, `None` =
    once per block, i.e. block-rate modulation).
    """

    def __init__(self, voices, sample_rate=SAMPLE_RATE, use_filter=True,
                 use_effects=True, control_sub_block=CONTROL_SUB_BLOCK,
                 gain=0.12, osc="wavetable"):
        self.n = voices
        self.osc_kind = osc
        self.sr = sample_rate
        self.use_filter = use_filter
        self.use_effects = use_effects
        self.sub = control_sub_block
        self.gain = gain
        self.tables = build_saw_tables(sample_rate)

        # A chord-ish spread across four octaves, so mip levels differ per
        # voice and the fancy-index gather is exercised for real.
        semis = (np.arange(voices) * 7) % 48
        self.freq = 65.41 * 2.0 ** (semis / 12.0)
        self.level = mip_level_for(self.freq)
        self.dt1 = self.freq / sample_rate
        self.dt2 = self.freq * 1.005 / sample_rate      # detuned osc 2
        self.ph1 = np.random.rand(voices)
        self.ph2 = np.random.rand(voices)
        self.zi = [np.zeros(2) for _ in range(voices)]

        # Amp envelope: linear segments, resumable, evaluated at audio rate.
        self.env = np.full(voices, 0.0)
        self.stage = np.zeros(voices, dtype=np.int64)   # 0 attack 1 decay 2 sustain
        self.attack_rate = 1.0 / (0.01 * sample_rate)
        self.decay_rate = (1.0 - 0.65) / (0.15 * sample_rate)
        self.sustain = 0.65
        # Filter envelope / LFO, control rate.
        self.mod_phase = np.random.rand(voices) * 2 * np.pi
        self.mod_rate = 0.7 + 0.3 * np.random.rand(voices)
        self.velocity = 0.4 + 0.6 * np.random.rand(voices)

        self.delay = Delay(sample_rate)
        self.chorus = Chorus(sample_rate)
        self._blocks = 0

    # -- oscillators, vectorised across every voice at once ----------------
    def _osc(self, phase, dt, n):
        if self.osc_kind == "polyblep":
            return self._osc_polyblep(phase, dt, n)
        ph = (phase[:, None] + np.arange(n)[None, :] * dt[:, None]) % 1.0
        pos = ph * TABLE_SIZE
        i0 = pos.astype(np.int64)
        frac = (pos - i0).astype(np.float32)
        lvl = self.level[:, None]
        a = self.tables[lvl, i0]
        b = self.tables[lvl, (i0 + 1) % TABLE_SIZE]
        y = a + frac * (b - a)
        return y, (phase + n * dt) % 1.0

    def _osc_polyblep(self, phase, dt, n):
        """#103's fully-vectorised PolyBLEP saw, kept for comparison: it needs
        no per-voice table gather, which turns out to matter (see README)."""
        ph = (phase[:, None] + np.arange(n)[None, :] * dt[:, None]) % 1.0
        y = 2.0 * ph - 1.0
        d = dt[:, None]
        m = ph < d
        t = np.where(m, ph / d, 0.0)
        y -= np.where(m, 2 * t - t * t - 1, 0.0)
        m = ph > 1 - d
        t = np.where(m, (ph - 1) / d, 0.0)
        y -= np.where(m, t * t + 2 * t + 1, 0.0)
        return y.astype(np.float32), (phase + n * dt) % 1.0

    # -- amp envelope, audio rate, resumable -------------------------------
    def _amp_env(self, n):
        rate = np.where(self.stage == 0, self.attack_rate,
                        np.where(self.stage == 1, -self.decay_rate, 0.0))
        ramp = self.env[:, None] + rate[:, None] * np.arange(n)[None, :]
        np.clip(ramp, 0.0, 1.0, out=ramp)
        end = ramp[:, -1]
        self.stage = np.where((self.stage == 0) & (end >= 1.0), 1, self.stage)
        self.stage = np.where((self.stage == 1) & (end <= self.sustain), 2,
                              self.stage)
        self.env = np.where(self.stage == 2, self.sustain, end)
        return ramp * self.velocity[:, None]

    def render(self, n):
        self._blocks += 1
        # A few voices retrigger every block, so envelopes are always moving
        # and the engine is never in a degenerate steady state.
        if self.n:
            k = self._blocks % max(1, self.n)
            self.stage[k] = 0
            self.env[k] = 0.0

        o1, self.ph1 = self._osc(self.ph1, self.dt1, n)
        o2, self.ph2 = self._osc(self.ph2, self.dt2, n)
        x = 0.5 * (o1 + o2)

        if self.use_filter:
            sub = self.sub or n
            out = np.empty_like(x)
            for start in range(0, n, sub):
                stop = min(start + sub, n)
                # control-rate modulation: filter env (tracks amp stage) + LFO
                self.mod_phase = (self.mod_phase + 2 * np.pi * self.mod_rate
                                  * (stop - start) / self.sr) % (2 * np.pi)
                cutoff = (self.freq * 4.0
                          * (1.0 + 0.6 * np.sin(self.mod_phase))
                          * (0.5 + self.velocity))
                b, a = lowpass_coeffs(cutoff, 1.6, self.sr)
                for i in range(self.n):
                    out[i, start:stop], self.zi[i] = signal.lfilter(
                        b[i], a[i], x[i, start:stop], zi=self.zi[i])
            x = out

        bus = (x * self._amp_env(n)).sum(axis=0).astype(np.float32)
        bus *= self.gain

        if self.use_effects:
            bus = self.chorus.process(self.delay.process(bus))

        return np.tanh(bus).astype(np.float32)
