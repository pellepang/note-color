"""Measure the real cost and the real artifacts of a block-based delay/
chorus effects chain -- the evidence behind
docs/research/effects-chain-delay-chorus.md (research ticket #104, map
#99).

Three experiments, all self-contained (no note-color imports, no audio
hardware -- everything is a NumPy buffer):

1. `bench_*`  -- per-block wall-clock cost of a hand-rolled NumPy delay
   line and modulated (chorus) delay line at 512 frames / 44100Hz, i.e.
   one `config.PLAYBACK_BLOCK_SIZE` callback block, whose real deadline
   is 512/44100 = 11.6ms. Also benchmarks `pedalboard` (Spotify's
   JUCE-backed C++ effects) if it happens to be importable, for the
   hand-roll-versus-dependency comparison the ticket asked for.
2. `artifacts()` -- the two block-boundary/interpolation mistakes that
   make a modulated delay sound mechanical, measured as sideband level
   relative to the carrier (dBc) on a 440Hz sine: reading the delay line
   at the nearest whole sample instead of interpolating, and restarting
   the LFO's phase every block instead of carrying it across.
3. `per_voice_vs_bus()` -- whether running the chain per synth voice
   differs from running it once on the summed bus. Both effects are
   linear, so with identical modulation they do not (the check prints
   the max abs difference); only deliberately decorrelated per-voice LFO
   phase produces a genuinely different signal.

Dev-tooling only, like scripts/terminal_screenshot.py -- not imported by
the app, and `pedalboard` is never a runtime dependency.

Usage:
    .venv/bin/python scripts/effects_chain_bench.py
"""

import time

import numpy as np

SAMPLE_RATE = 44100
BLOCK = 512  # config.PLAYBACK_BLOCK_SIZE


# --------------------------------------------------------------------------
# The two primitives under test. Both are the same circular buffer; the
# difference is only how the read index is computed (fixed vs. LFO-modulated,
# integer vs. fractional).
# --------------------------------------------------------------------------

class Delay:
    """Fixed-time feedback delay over a circular buffer.

    NOTE: reads the whole block's worth of delayed samples *before*
    writing the block, which is only correct while `delay_seconds *
    sample_rate >= block_size` -- otherwise a sample's own feedback
    would need to be read back inside the same block. See the research
    doc's "short delays" note.
    """

    def __init__(self, sample_rate, max_seconds=2.0, delay_seconds=0.25,
                 feedback=0.35, mix=0.3):
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
    """Chorus = short delay whose length is modulated by an LFO, read with
    fractional (linear-interpolated) indexing. `voices` taps share one
    delay line at evenly spread LFO phases."""

    def __init__(self, sample_rate, rate_hz=1.0, depth_ms=2.0,
                 centre_delay_ms=7.0, mix=0.5, voices=1):
        self.sample_rate = sample_rate
        self.size = int(sample_rate * 0.1)  # 100ms, JUCE's own ceiling
        self.buf = np.zeros(self.size, dtype=np.float32)
        self.write = 0
        self.rate_hz = rate_hz
        self.depth = depth_ms * sample_rate / 1000.0
        self.centre = centre_delay_ms * sample_rate / 1000.0
        self.mix = mix
        self.voices = voices
        self.phase = 0.0

    def process(self, block, interpolate=True, keep_phase=True):
        n = len(block)
        size = self.size
        offsets = np.arange(n)
        phase = (self.phase if keep_phase else 0.0)
        lfo = phase + 2.0 * np.pi * self.rate_hz * offsets / self.sample_rate
        self.buf[(self.write + offsets) % size] = block

        wet = np.zeros(n, dtype=np.float32)
        for voice in range(self.voices):
            spread = 2.0 * np.pi * voice / self.voices
            delay = self.centre + self.depth * np.sin(lfo + spread)
            pos = (self.write + offsets - delay) % size
            if interpolate:
                lo = np.floor(pos).astype(np.int64)
                frac = (pos - lo).astype(np.float32)
                wet += (1.0 - frac) * self.buf[lo] + frac * self.buf[(lo + 1) % size]
            else:
                wet += self.buf[np.round(pos).astype(np.int64) % size]
        wet /= self.voices

        self.phase = (self.phase + 2.0 * np.pi * self.rate_hz * n / self.sample_rate) % (2.0 * np.pi)
        self.write = (self.write + n) % size
        return (1.0 - self.mix) * block + self.mix * wet


# --------------------------------------------------------------------------

def _tone(freq, seconds=4.0, amplitude=0.3):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


def _bench(label, fn, signal, blocks=400):
    times = []
    for i in range(blocks):
        start = (i * BLOCK) % (len(signal) - BLOCK)
        block = signal[start:start + BLOCK]
        t0 = time.perf_counter()
        fn(block)
        times.append(time.perf_counter() - t0)
    us = np.array(times) * 1e6
    budget = BLOCK / SAMPLE_RATE * 1e6
    print(f"{label:38s} mean {us.mean():6.0f}us  median {np.median(us):6.0f}us  "
          f"p99 {np.percentile(us, 99):6.0f}us  max {us.max():6.0f}us  "
          f"({us.mean() / budget * 100:.1f}% of the {budget / 1000:.1f}ms block budget)")


def bench_numpy():
    print("\n== hand-rolled NumPy, one 512-frame block at 44100Hz ==")
    signal = _tone(220.0)
    delay = Delay(SAMPLE_RATE)
    chorus1 = Chorus(SAMPLE_RATE)
    chorus3 = Chorus(SAMPLE_RATE, voices=3)
    _bench("delay only", delay.process, signal)
    _bench("chorus, 1 voice", chorus1.process, signal)
    _bench("chorus, 3 voices", chorus3.process, signal)
    d2, c2 = Delay(SAMPLE_RATE), Chorus(SAMPLE_RATE)
    _bench("delay -> chorus chain", lambda b: c2.process(d2.process(b)), signal)
    per_voice = [Chorus(SAMPLE_RATE) for _ in range(8)]
    _bench("8x per-voice chorus", lambda b: [c.process(b) for c in per_voice], signal)


def bench_pedalboard():
    try:
        import pedalboard
    except Exception as exc:  # ImportError, or a native crash on import
        print(f"\n== pedalboard not benchmarked: {exc!r} ==")
        return
    print(f"\n== pedalboard {pedalboard.__version__}, same 512-frame block ==")
    signal = _tone(220.0)

    def board():
        return pedalboard.Pedalboard([
            pedalboard.Delay(delay_seconds=0.25, feedback=0.35, mix=0.3),
            pedalboard.Chorus(rate_hz=1.0, depth=0.25, centre_delay_ms=7.0,
                              feedback=0.0, mix=0.5),
        ])

    # State continuity across block boundaries: reset=False block-wise must
    # equal one-shot processing of the same signal.
    one_shot = board()(signal, SAMPLE_RATE, buffer_size=BLOCK, reset=True)
    streamed = board()
    chunks = [streamed(signal[i:i + BLOCK], SAMPLE_RATE, buffer_size=BLOCK, reset=False)
              for i in range(0, len(signal), BLOCK)]
    blockwise = np.concatenate(chunks)
    n = min(len(one_shot), len(blockwise))
    print(f"blockwise(reset=False) vs one-shot max abs diff: "
          f"{float(np.max(np.abs(one_shot[:n] - blockwise[:n]))):.3e}")

    chain = board()
    chain(signal[:BLOCK], SAMPLE_RATE, buffer_size=BLOCK, reset=True)
    _bench("delay -> chorus chain", lambda b: chain(b, SAMPLE_RATE, buffer_size=BLOCK, reset=False), signal)
    for name, plugin in [("delay only", pedalboard.Delay(delay_seconds=0.25, feedback=0.35, mix=0.3)),
                         ("chorus only", pedalboard.Chorus()),
                         ("reverb only", pedalboard.Reverb())]:
        plugin(signal[:BLOCK], SAMPLE_RATE, buffer_size=BLOCK, reset=True)
        _bench(name, lambda b, p=plugin: p(b, SAMPLE_RATE, buffer_size=BLOCK, reset=False), signal)


def _sideband_dbc(signal, carrier_hz=440.0):
    """Worst and RMS spectral content at least 50Hz away from the carrier,
    relative to the carrier peak. A clean modulated delay puts almost
    nothing there; zipper noise and per-block clicks do."""
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / SAMPLE_RATE)
    band = (np.abs(freqs - carrier_hz) > 50.0) & (freqs > 50.0) & (freqs < 5000.0)
    peak = spectrum.max()
    worst = 20.0 * np.log10(spectrum[band].max() / peak)
    rms = 20.0 * np.log10(np.sqrt((spectrum[band] ** 2).mean()) / peak)
    return worst, rms


def artifacts():
    print("\n== modulated-delay artifacts, 440Hz sine through a 1Hz/2ms chorus ==")
    signal = _tone(440.0, seconds=2.0, amplitude=0.5)
    cases = [
        ("linear interpolation, LFO phase carried", dict(interpolate=True, keep_phase=True)),
        ("nearest whole sample (no interpolation)", dict(interpolate=False, keep_phase=True)),
        ("LFO phase reset every block", dict(interpolate=True, keep_phase=False)),
    ]
    for label, kwargs in cases:
        chorus = Chorus(SAMPLE_RATE, mix=1.0)
        wet = np.concatenate([chorus.process(signal[i:i + BLOCK], **kwargs)
                              for i in range(0, len(signal), BLOCK)])
        worst, rms = _sideband_dbc(wet)
        step = float(np.abs(np.diff(wet)).max())
        print(f"{label:42s} worst {worst:7.1f} dBc   rms {rms:7.1f} dBc   max |sample step| {step:.4f}")


def per_voice_vs_bus():
    print("\n== per-voice vs. shared-bus routing (max abs difference) ==")
    a, b = _tone(261.63, seconds=1.0), _tone(329.63, seconds=1.0)

    def run(effect, signal):
        return np.concatenate([effect.process(signal[i:i + BLOCK])
                               for i in range(0, len(signal), BLOCK)])

    bus_delay = run(Delay(SAMPLE_RATE), a + b)
    pv_delay = run(Delay(SAMPLE_RATE), a) + run(Delay(SAMPLE_RATE), b)
    print(f"delay,  identical settings          : {float(np.max(np.abs(pv_delay - bus_delay))):.3e}")

    bus_chorus = run(Chorus(SAMPLE_RATE), a + b)
    pv_chorus = run(Chorus(SAMPLE_RATE), a) + run(Chorus(SAMPLE_RATE), b)
    print(f"chorus, identical LFO phase         : {float(np.max(np.abs(pv_chorus - bus_chorus))):.3e}")

    c1, c2 = Chorus(SAMPLE_RATE), Chorus(SAMPLE_RATE)
    c2.phase = np.pi
    detuned = run(c1, a) + run(c2, b)
    print(f"chorus, decorrelated LFO phases     : {float(np.max(np.abs(detuned - bus_chorus))):.3e}"
          f"   (peak signal {float(np.max(np.abs(bus_chorus))):.3f})")


if __name__ == "__main__":
    bench_numpy()
    bench_pedalboard()
    artifacts()
    per_voice_vs_bus()
