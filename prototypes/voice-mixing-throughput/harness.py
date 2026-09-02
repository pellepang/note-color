"""Ticket #100: how much polyphony survives a *real* `sounddevice.OutputStream`
callback, with a filter and an effects bus in the path?

#103 and #104 priced the pieces in synthetic timing loops. This runs the
assembled engine inside an actual audio callback, against an actual audio
device, and reports what only a real callback can tell you: **driver-reported
underruns**, not just wall-clock timings.

Four experiments (~1 minute total, and it makes sound -- turn the volume down):

1. voice sweep, full path, 512-frame blocks -- where polyphony breaks
2. stage breakdown at 32 voices -- what the filter and the bus each cost
3. block-size / latency tradeoff at 32 voices
4. a linear fit for per-voice cost and fixed overhead

Run: `.venv/bin/python prototypes/voice-mixing-throughput/harness.py`
"""

import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sounddevice as sd                     # noqa: E402
from engine import Engine, SAMPLE_RATE       # noqa: E402

WARMUP_BLOCKS = 20
SECONDS = 3.0


class Result:
    def __init__(self, label, block, times_ms, underflows, other_status,
                 latency, blocks):
        self.label = label
        self.block = block
        self.budget_ms = block / SAMPLE_RATE * 1000.0
        self.t = times_ms
        self.underflows = underflows
        self.other_status = other_status
        self.latency = latency
        self.blocks = blocks

    @property
    def over(self):
        return int((self.t > self.budget_ms).sum())

    def row(self):
        p = np.percentile
        return (f"{self.label:<30}{self.t.mean():8.2f}{p(self.t,50):8.2f}"
                f"{p(self.t,95):8.2f}{p(self.t,99):8.2f}{self.t.max():8.2f}"
                f"{self.budget_ms:9.2f}"
                f"{self.t.mean()/self.budget_ms*100:8.1f}%"
                f"{self.over:>6}/{len(self.t):<6}{self.underflows:>7}")


HEADER = (f"{'configuration':<30}{'mean':>8}{'p50':>8}{'p95':>8}{'p99':>8}"
          f"{'max':>8}{'budget':>9}{'load':>9}{'over-budget':>14}{'xruns':>7}")


class AnalysisLoad:
    """Stand-in for note-color's own analysis thread: ~86 hops/second of
    2048-point FFT work, holding the GIL in exactly the way the live views
    would while the synth is sounding."""

    def __init__(self, threads=1):
        self.threads = threads
        self._stop = threading.Event()
        self._ts = []

    def __enter__(self):
        buf = np.random.rand(2048)
        weights = np.random.rand(12, 1025)

        def work():
            while not self._stop.is_set():
                spec = np.abs(np.fft.rfft(buf))
                weights @ spec
                np.fft.irfft(np.fft.rfft(buf))
                time.sleep(0.0116)          # ~86 hops/s, the real hop rate

        for _ in range(self.threads):
            t = threading.Thread(target=work, daemon=True)
            t.start()
            self._ts.append(t)
        return self

    def __exit__(self, *exc):
        self._stop.set()
        for t in self._ts:
            t.join(timeout=1.0)


def measure(label, voices, block=512, use_filter=True, use_effects=True,
            sub=64, seconds=SECONDS, osc="wavetable"):
    """Run the engine inside a real OutputStream for `seconds` and report the
    per-callback time distribution plus PortAudio's own underflow count."""
    engine = Engine(voices, use_filter=use_filter, use_effects=use_effects,
                    control_sub_block=sub, osc=osc)
    target = int(seconds * SAMPLE_RATE / block) + WARMUP_BLOCKS
    times = np.zeros(target)
    state = {"i": 0, "under": 0, "other": 0}
    done = threading.Event()

    def callback(outdata, frames, time_info, status):
        i = state["i"]
        if status:
            if status.output_underflow:
                state["under"] += 1 if i >= WARMUP_BLOCKS else 0
            elif i >= WARMUP_BLOCKS:
                state["other"] += 1
        t0 = time.perf_counter()
        outdata[:, 0] = engine.render(frames)
        times[i] = (time.perf_counter() - t0) * 1000.0
        state["i"] = i + 1
        if state["i"] >= target:
            raise sd.CallbackStop

    stream = sd.OutputStream(samplerate=SAMPLE_RATE, blocksize=block,
                             channels=1, dtype="float32", callback=callback,
                             finished_callback=done.set)
    with stream:
        latency = stream.latency
        done.wait(timeout=seconds * 4 + 5)
    n = min(state["i"], target)
    return Result(label, block, times[WARMUP_BLOCKS:n], state["under"],
                  state["other"], latency, n)


def main():
    print(__doc__.split("Run:")[0].strip())
    print()
    try:
        dev = sd.query_devices(kind="output")
        print(f"output device : {dev['name']}  "
              f"(default sr {dev['default_samplerate']:.0f}Hz)")
    except Exception as exc:
        print(f"no usable output device: {exc}")
        return
    print(f"engine        : 2 mip-wavetable oscillators -> per-voice biquad "
          f"(scipy.lfilter, 64-sample\n                control sub-blocks) -> "
          f"audio-rate ADSR -> sum -> delay -> chorus -> tanh")
    print(f"numpy {np.__version__}   sounddevice {sd.__version__}   "
          f"sample rate {SAMPLE_RATE}Hz")
    print()

    # ---------------------------------------------------------------- 1
    print("=== 1. voice sweep, full path, 512-frame blocks (11.61ms deadline) ===")
    print(HEADER)
    sweep = []
    for v in (1, 8, 16, 24, 32, 40, 48, 64):
        r = measure(f"{v} voices", v)
        sweep.append((v, r))
        print(r.row())
        sys.stdout.flush()
    print()

    # ---------------------------------------------------------------- 2
    print("=== 2. what each stage costs, at 32 voices, 512-frame blocks ===")
    print(HEADER)
    stages = [
        ("effects bus only (0 voices)", dict(voices=0, use_filter=False,
                                             use_effects=True)),
        ("oscillators only", dict(voices=32, use_filter=False,
                                  use_effects=False)),
        ("+ filter @ block rate", dict(voices=32, use_filter=True,
                                       use_effects=False, sub=None)),
        ("+ filter @ 64-sample sub", dict(voices=32, use_filter=True,
                                          use_effects=False, sub=64)),
        ("+ effects bus (full path)", dict(voices=32, use_filter=True,
                                           use_effects=True, sub=64)),
    ]
    for label, kw in stages:
        print(measure(label, **kw).row())
        sys.stdout.flush()
    print()

    # ---------------------------------------------------------------- 3
    print("=== 3. block size / latency tradeoff, 32 voices, full path ===")
    print(HEADER)
    for block in (128, 256, 512, 1024):
        r = measure(f"block {block}", 32, block=block)
        lat = r.latency
        print(r.row() + f"   stream latency {lat*1000:.1f}ms"
              if isinstance(lat, float) else r.row())
        sys.stdout.flush()
    print()

    # ---------------------------------------------------------------- 3b
    print("=== 4. oscillator choice, 32 voices, oscillators only ===")
    print(HEADER)
    for kind in ("wavetable", "polyblep"):
        print(measure(f"{kind} saw x2", 32, use_filter=False,
                      use_effects=False, osc=kind).row())
        sys.stdout.flush()
    print()

    # ---------------------------------------------------------------- 3c
    print("=== 5. GIL contention: the app's own analysis thread alongside ===")
    print(HEADER)
    for extra in (0, 1, 2):
        label = f"full path + {extra} analysis thr"
        if extra == 0:
            print(measure(label, 32).row())
        else:
            with AnalysisLoad(extra):
                print(measure(label, 32).row())
        sys.stdout.flush()
    print()

    # ---------------------------------------------------------------- 4
    print("=== 6. per-voice cost (least squares over experiment 1) ===")
    vs = np.array([v for v, _ in sweep], dtype=float)
    for stat, fn in (("mean", np.mean),
                     ("p99", lambda t: np.percentile(t, 99)),
                     ("max", np.max)):
        ys = np.array([fn(r.t) for _, r in sweep])
        slope, intercept = np.polyfit(vs, ys, 1)
        budget = 512 / SAMPLE_RATE * 1000.0
        room = (budget - intercept) / slope
        print(f"  {stat:>5}: {slope*1000:7.1f}us/voice + {intercept:5.2f}ms fixed"
              f"   -> deadline reached at {room:5.1f} voices")
    print()
    print("Sustainable polyphony is the largest voice count in experiment 1 with")
    print("zero driver-reported xruns AND zero over-budget callbacks.")


if __name__ == "__main__":
    main()
