"""Measure the real cost and the real aliasing of a block-based subtractive
synth voice in NumPy -- the evidence behind
docs/research/subtractive-synth-numpy.md (research ticket #103, map #99).

Four experiments, all self-contained (no note-color imports, no audio
hardware -- everything is a NumPy buffer):

1. `aliasing()`   -- spurious (non-harmonic) energy produced by naive vs.
   PolyBLEP vs. mip-mapped-wavetable vs. per-note-additive sawtooths,
   in dBc relative to the fundamental. Frequencies are snapped to exact
   FFT bin centres and analysed with a rectangular window, so spectral
   leakage is identically zero and every non-harmonic bin measured IS
   aliasing rather than window skirt.
2. `osc_cost()`   -- per-voice wall-clock cost of each oscillator for one
   512-frame block at 44100Hz (`config.PLAYBACK_BLOCK_SIZE` /
   `config.PLAYBACK_SAMPLE_RATE`), whose real deadline is 11.61ms.
3. `filter_cost()` -- the per-sample-feedback problem: a hand-written
   state-variable filter as a Python loop, as a NumPy loop vectorized
   across voices, as `scipy.signal.lfilter`/`sosfilt`, and (if numba
   happens to be importable via librosa) as an njit'd loop. Plus the
   sub-block sweep that prices filter-coefficient modulation rate.
4. `voice_budget()` -- a whole 2-oscillator + resonant-filter + envelope
   voice engine run for 3000 consecutive blocks, reporting the tail of
   the per-block time distribution (p99/max), not just the mean --
   a real-time audio callback must meet its deadline on EVERY block.

Run: `.venv/bin/python scripts/synth_engine_bench.py`
"""

import time

import numpy as np
from scipy import signal

SR = 44100
BLOCK = 512
BUDGET = BLOCK / SR  # 11.61ms -- one OutputStream callback's real deadline


def timeit(fn, n=200):
    fn()  # warm up (numba compiles, scipy allocates)
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n


# --------------------------------------------------------------- oscillators
def naive_saw(f, n, sr=SR, phase=0.0):
    return 2.0 * ((phase + np.arange(n) * f / sr) % 1.0) - 1.0


def polyblep_saw(f, n, sr=SR, phase=0.0):
    """Two-point PolyBLEP residual (Valimaki & Huovilainen 2007): subtract a
    polynomial approximation of the band-limited step from the one or two
    samples adjacent to each wrap discontinuity."""
    dt = f / sr
    ph = (phase + np.arange(n) * dt) % 1.0
    y = 2.0 * ph - 1.0
    m1 = ph < dt
    t1 = ph[m1] / dt
    y[m1] -= 2.0 * t1 - t1 * t1 - 1.0
    m2 = ph > 1.0 - dt
    t2 = (ph[m2] - 1.0) / dt
    y[m2] -= t2 * t2 + 2.0 * t2 + 1.0
    return y


def additive_saw(f, n, sr=SR):
    """Exactly band-limited, but rebuilt per note: O(partials * n) sines."""
    t = np.arange(n) / sr
    y = np.zeros(n)
    k = 1
    while k * f < sr / 2:
        y -= (2.0 / np.pi) * np.sin(2 * np.pi * k * f * t) / k
        k += 1
    return y


def mip_table(top_hz, size=2048, sr=SR):
    """One band-limited saw table, safe up to `top_hz`."""
    k = np.arange(1, int((sr / 2) / top_hz) + 1)
    ph = np.arange(size) / size
    return -(2.0 / np.pi) * (np.sin(2 * np.pi * np.outer(ph, k)) / k).sum(axis=1)


_MIPS = {}


def mip_for(f):
    top = 2.0 ** np.ceil(np.log2(f))
    if top not in _MIPS:
        _MIPS[top] = mip_table(top)
    return _MIPS[top]


def wavetable_saw(table, f, n, sr=SR, phase=0.0):
    size = len(table)
    x = ((phase + np.arange(n) * f / sr) % 1.0) * size
    i0 = x.astype(np.int64)
    frac = x - i0
    return table[i0] * (1 - frac) + table[(i0 + 1) % size] * frac


def aliasing(n=32768):
    def bin_exact(f):
        return round(f * n / SR) * SR / n

    def dbc(y, f):
        sp = np.abs(np.fft.rfft(y))  # rectangular window + bin-exact f => no leakage
        kbin = round(f * n / SR)
        harm = np.zeros(len(sp), bool)
        harm[0] = True
        harm[kbin::kbin] = True
        nonharm = sp[~harm]
        return (20 * np.log10(nonharm.max() / sp[kbin]),
                20 * np.log10(np.sqrt((nonharm ** 2).sum()) / sp[kbin]))

    print(f"=== 1. saw aliasing (N={n}, bin-exact frequencies, rect window) ===")
    print(f"{'oscillator':<28}{'f (Hz)':>9}{'worst':>12}{'total':>12}")
    for target in (110.0, 440.0, 1174.66, 2093.0, 3520.0):
        f = bin_exact(target)
        for label, y in (
            ("naive saw", naive_saw(f, n)),
            ("PolyBLEP saw", polyblep_saw(f, n)),
            ("wavetable (per-note mip)", wavetable_saw(mip_for(f), f, n)),
            ("additive (per-note)", additive_saw(f, n)),
        ):
            worst, total = dbc(y, f)
            print(f"{label:<28}{f:9.1f}{worst:11.1f}dB{total:11.1f}dB")
        print()


def osc_cost():
    print(f"=== 2. oscillator cost, one {BLOCK}-frame block, per voice "
          f"(budget {BUDGET*1e3:.2f}ms) ===")
    tab = mip_for(440.0)
    cases = (
        ("np.sin, 1 partial", lambda: np.sin(2 * np.pi * 440 * np.arange(BLOCK) / SR)),
        ("harmonic stack x3 (today's playback.py)",
         lambda: sum(w * np.sin(2 * np.pi * 440 * k * np.arange(BLOCK) / SR)
                     for k, w in enumerate((1.0, 0.4, 0.15), 1))),
        ("naive saw", lambda: naive_saw(440.0, BLOCK)),
        ("PolyBLEP saw", lambda: polyblep_saw(440.0, BLOCK)),
        ("wavetable saw (linear interp)", lambda: wavetable_saw(tab, 440.0, BLOCK)),
    )
    for label, fn in cases:
        t = timeit(fn)
        print(f"  {label:<42}{t*1e6:8.1f} us  ({t/BUDGET*100:5.2f}% of budget)")
    print()


# ------------------------------------------------------------------- filters
def svf_python(x, g, k):
    """Zavalishin TPT state-variable filter, scalar Python loop."""
    ic1 = ic2 = 0.0
    out = np.empty_like(x)
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    for i in range(len(x)):
        v3 = x[i] - ic2
        v1 = a1 * ic1 + a2 * v3
        v2 = ic2 + a2 * ic1 + a3 * v3
        ic1 = 2 * v1 - ic1
        ic2 = 2 * v2 - ic2
        out[i] = v2
    return out


def svf_voices(X, g, k):
    """Same filter, loop over TIME but vectorized across V voices."""
    V, n = X.shape
    ic1 = np.zeros(V)
    ic2 = np.zeros(V)
    out = np.empty_like(X)
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    for i in range(n):
        v3 = X[:, i] - ic2
        v1 = a1 * ic1 + a2 * v3
        v2 = ic2 + a2 * ic1 + a3 * v3
        ic1 = 2 * v1 - ic1
        ic2 = 2 * v2 - ic2
        out[:, i] = v2
    return out


def mod_cost():
    """Envelope/LFO application cost, and the price of writing PolyBLEP's
    residual as a per-sample Python branch (as every reference C
    implementation writes it) instead of a masked NumPy op."""
    print("=== 2b. modulation cost, one 512-frame block ===")
    x = np.random.randn(BLOCK)

    def polyblep_scalar(f, n, sr=SR, phase=0.0):
        dt = f / sr
        out = np.empty(n)
        ph = phase
        for i in range(n):
            y = 2.0 * ph - 1.0
            if ph < dt:
                t = ph / dt
                y -= 2.0 * t - t * t - 1.0
            elif ph > 1.0 - dt:
                t = (ph - 1.0) / dt
                y -= t * t + 2.0 * t + 1.0
            out[i] = y
            ph = (ph + dt) % 1.0
        return out

    for label, fn in (
        ("per-block scalar gain (x * float)", lambda: x * 0.5),
        ("per-sample amp env ramp (linspace + mul)",
         lambda: x * np.linspace(0.2, 0.8, BLOCK)),
        ("per-sample sine LFO across block",
         lambda: np.sin(2 * np.pi * 5 * np.arange(BLOCK) / SR)),
        ("np.tanh soft clip", lambda: np.tanh(x)),
        ("PolyBLEP written as a per-sample Python branch",
         lambda: polyblep_scalar(440.0, BLOCK)),
    ):
        t = timeit(fn, 400)
        print(f"  {label:<46}{t*1e6:8.2f} us  ({t/BUDGET*100:6.3f}% of budget)")
    print()


def filter_cost():
    print(f"=== 3. resonant filter, one {BLOCK}-frame block ===")
    x = np.random.randn(BLOCK)
    g, k = np.tan(np.pi * 1000 / SR), 0.7
    b, a = signal.butter(2, 1000, btype="low", fs=SR)
    sos = signal.butter(2, 1000, btype="low", fs=SR, output="sos")
    for label, fn in (
        ("scalar Python SVF loop", lambda: svf_python(x, g, k)),
        ("scipy.signal.lfilter (biquad)", lambda: signal.lfilter(b, a, x, zi=np.zeros(2))),
        ("scipy.signal.sosfilt (1 section)",
         lambda: signal.sosfilt(sos, x, zi=np.zeros((sos.shape[0], 2)))),
    ):
        t = timeit(fn, 60)
        print(f"  {label:<42}{t*1e6:8.1f} us  ({t/BUDGET*100:5.2f}% of budget)")

    print("\n  V voices, each with its OWN cutoff (the real polyphonic case):")
    print(f"  {'V':>4}{'NumPy SVF (loop over time)':>30}{'scipy.lfilter x V':>22}"
          f"{'numba SVF':>14}")
    try:
        from numba import njit

        @njit(cache=True, fastmath=True)
        def svf_nb(X, g, k, out):
            V, n = X.shape
            for v in range(V):
                a1 = 1.0 / (1.0 + g[v] * (g[v] + k[v]))
                a2 = g[v] * a1
                a3 = g[v] * a2
                s1 = s2 = 0.0
                for i in range(n):
                    v3 = X[v, i] - s2
                    v1 = a1 * s1 + a2 * v3
                    v2 = s2 + a2 * s1 + a3 * v3
                    s1 = 2 * v1 - s1
                    s2 = 2 * v2 - s2
                    out[v, i] = v2
            return out
    except ImportError:
        svf_nb = None

    for V in (1, 8, 16, 32, 64):
        X = np.random.randn(V, BLOCK)
        f = 200.0 + 60.0 * np.arange(V)
        ga = np.tan(np.pi * f / SR)
        ka = np.full(V, 0.7)
        coef = [signal.butter(2, float(fi), btype="low", fs=SR) for fi in f]
        zi = [np.zeros(2) for _ in range(V)]

        def run_lf():
            for i, (bb, aa) in enumerate(coef):
                signal.lfilter(bb, aa, X[i], zi=zi[i])

        t_np = timeit(lambda: svf_voices(X, ga, ka), 20)
        t_lf = timeit(run_lf, 100)
        t_nb = timeit(lambda: svf_nb(X, ga, ka, np.empty_like(X)), 300) if svf_nb else float("nan")
        print(f"  {V:>4}{t_np*1e3:22.2f}ms{t_np/BUDGET*100:6.1f}%"
              f"{t_lf*1e3:14.2f}ms{t_lf/BUDGET*100:6.1f}%"
              f"{t_nb*1e3:7.2f}ms{t_nb/BUDGET*100:6.1f}%")

    print("\n  cost of the filter-coefficient MODULATION rate (V=16, "
          "scipy.lfilter, one 512-frame block split into sub-blocks):")
    V = 16
    X = np.random.randn(V, BLOCK)
    coef = [signal.butter(2, 300 + i * 80, btype="low", fs=SR) for i in range(V)]
    for sub in (512, 256, 128, 64, 32, 16):
        zi = [np.zeros(2) for _ in range(V)]

        def run():
            for s in range(0, BLOCK, sub):
                for i in range(V):
                    bb, aa = coef[i]
                    signal.lfilter(bb, aa, X[i, s:s + sub], zi=zi[i])

        t = timeit(run, 60)
        print(f"    sub-block {sub:4d} ({SR/sub:7.1f} Hz mod rate): {t*1e3:6.2f} ms "
              f"({t/BUDGET*100:5.1f}% of budget)")
    print()


# -------------------------------------------------------------- voice budget
def voice_budget(blocks=3000):
    print("=== 4. whole-engine per-block time distribution (tail matters) ===")
    print("    2 PolyBLEP oscillators -> mix -> per-voice resonant biquad "
          "(scipy.lfilter,\n    persistent zi) -> per-sample amp envelope -> "
          "sum -> tanh. GC left enabled.")
    print(f"  {'V':>4}{'mean':>10}{'p99':>10}{'max':>10}{'over budget':>14}")
    for V in (8, 16, 32, 48, 64):
        f = 110.0 * 2 ** (np.arange(V) % 36 / 12.0)
        ph1, ph2 = np.random.rand(V), np.random.rand(V)
        dt1, dt2 = f / SR, f * 1.005 / SR
        coef = [signal.butter(2, float(np.clip(fi * 4, 80, SR * 0.45)),
                              btype="low", fs=SR) for fi in f]
        zi = [np.zeros(2) for _ in range(V)]
        env = np.linspace(0.9, 0.85, BLOCK)[None, :]

        def blep(phase, dt, n):
            ph = (phase[:, None] + np.arange(n)[None, :] * dt[:, None]) % 1.0
            y = 2 * ph - 1
            d = dt[:, None]
            m = ph < d
            t = np.where(m, ph / d, 0.0)
            y -= np.where(m, 2 * t - t * t - 1, 0.0)
            m = ph > 1 - d
            t = np.where(m, (ph - 1) / d, 0.0)
            y -= np.where(m, t * t + 2 * t + 1, 0.0)
            return y, (phase + n * dt) % 1.0

        def one_block():
            nonlocal ph1, ph2
            o1, ph1 = blep(ph1, dt1, BLOCK)
            o2, ph2 = blep(ph2, dt2, BLOCK)
            x = 0.5 * (o1 + o2)
            out = np.empty_like(x)
            for i in range(V):
                bb, aa = coef[i]
                out[i], zi[i] = signal.lfilter(bb, aa, x[i], zi=zi[i])
            return np.tanh((out * env).sum(axis=0) * 0.2)

        for _ in range(50):
            one_block()
        ts = np.empty(blocks)
        for i in range(blocks):
            t0 = time.perf_counter()
            one_block()
            ts[i] = time.perf_counter() - t0
        ts *= 1e3
        over = int((ts > BUDGET * 1e3).sum())
        print(f"  {V:>4}{ts.mean():9.2f}ms{np.percentile(ts,99):9.2f}ms"
              f"{ts.max():9.2f}ms{over:9d}/{blocks}")
    print()


if __name__ == "__main__":
    print(f"sample rate {SR}, block {BLOCK} frames, deadline {BUDGET*1e3:.2f} ms\n")
    aliasing()
    osc_cost()
    mod_cost()
    filter_cost()
    voice_budget()
