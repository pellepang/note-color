"""Issue #231's diagnostic finding, as a rerunnable probe: correlates each
audio callback's wall-clock duration against the CPU-frequency-governor's
own reported clock speed for the logical CPU that block actually ran on.

Built while diagnosing why the modulated worst-case patch (#229's
measurement: 60.2% mean, 266.8% max, real xruns) spikes to ~4x its own
mean instead of running flat and high. Four candidates were ruled out by
direct instrumentation before this one was found, each reused from
`mod_callback_cost.py`'s exact patches (`--patch baseline|realistic|worst`):

  - `ParamBlock.dirty` never changes under continuous modulation (only a
    discrete `set()` bumps it), so per-block coefficient recompute is
    already deterministic every block, not something that fires on some
    blocks and not others.
  - GC: `gc.callbacks` instrumentation caught only ~2 collections across a
    10-second, ~850-block run, neither landing inside a measured block.
  - Denormal floats: every voice's filter `zi`, delay ring and pink-noise
    `zi` were walked after every block; the smallest nonzero magnitude
    seen across a whole run never dropped below 1e-5, nowhere near
    float64's ~2.2e-308 denormal floor.
  - NaN/Inf instability: same walk, `np.isfinite()`; never tripped.
  - Wall-clock-vs-thread-CPU-time (`time.thread_time()`) stayed within
    1-3% of each other even on the slowest blocks, ruling out an ordinary
    "this thread got preempted and had to wait" scheduling stall.

What *did* correlate, strongly and repeatably (r = -0.66 to -0.89 across
two patches and two independent measurement techniques): this machine's
`scaling_governor` is `powersave` (confirmed via
`/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`), and the
executing CPU's own `scaling_cur_freq` sampled at each slow block's own
end is consistently 800MHz-1800MHz -- versus a flat 3100-3500MHz (turbo)
on the fast blocks. The callback thread is ordinary `SCHED_OTHER` with no
realtime priority or CPU pin (grep confirms `sound_engine.py` requests
neither), so between callbacks the core is free to idle down, and a
compute-heavy block that lands before the governor has ramped back up
runs the identical arithmetic at a fraction of its top clock -- which is
exactly a ~3-4x slowdown on the same instruction count, no extra work
required. See issue #231's comment thread for the full write-up.

This script reads the callback thread's last-scheduled CPU from
`/proc/self/stat`'s `processor` field (index 38, 0-based) and that CPU's
`scaling_cur_freq`, as two plain file reads inside the callback wrapper
immediately after each block -- negligible next to the multi-millisecond
work being measured, and unlike a background sampler thread (tried first
and discarded), it adds no second thread to fight the callback for the
GIL.

Usage:
    .venv/bin/python scripts/mod_callback_freq_probe.py --patch worst
    .venv/bin/python scripts/mod_callback_freq_probe.py --patch baseline --seconds 15
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notecolor.settings import config                # noqa: E402
from notecolor.audio import sound_engine              # noqa: E402
from notecolor.audio.graph.contract import Activation  # noqa: E402
from notecolor.audio.graph.poly import PolyGraph      # noqa: E402
import mod_callback_cost as base                      # noqa: E402


def current_cpu_and_freq():
    """This thread's last-scheduled logical CPU and that CPU's own
    cpufreq-reported clock speed, in kHz. Two file reads, no allocation
    worth worrying about at this scale -- called once per block, not once
    per sample."""
    try:
        with open("/proc/self/stat") as f:
            fields = f.read().split()
        cpu = int(fields[38])
    except Exception:
        return -1, -1
    try:
        with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq") as f:
            freq = int(f.read().strip())
    except Exception:
        freq = -1
    return cpu, freq


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", choices=sorted(base.PATCHES), default="worst")
    parser.add_argument("--voices", type=int, default=config.POLYPHONY_SYNTH_VIEW)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    engine = sound_engine.SoundEngine(detection_active=False)
    graph, long_release_nodes = base.PATCHES[args.patch](args.voices)
    poly = PolyGraph(graph, voices=args.voices)
    poly.activate(Activation(engine.sample_rate, engine.block_size))
    for voice in poly.voices:
        for node_id in long_release_nodes:
            voice.graph.node(node_id).module.params.set("release", 30.0)
            voice.graph.node(node_id).module.params.set("decay", 30.0)
            voice.graph.node(node_id).module.params.set("sustain", 0.8)
    engine.set_graph(poly, activate=False)

    wall = []
    cpu_ids = []
    freqs = []
    inner = engine._callback

    def timed(outdata, frames, time_info, status):
        w0 = time.perf_counter()
        inner(outdata, frames, time_info, status)
        w1 = time.perf_counter()
        wall.append(w1 - w0)
        cpu, freq = current_cpu_and_freq()
        cpu_ids.append(cpu)
        freqs.append(freq)

    engine._callback = timed
    deadline_ms = engine.block_size / engine.sample_rate * 1000
    print(f"patch={args.patch}  voices={args.voices}  deadline={deadline_ms:.2f}ms")
    engine.ensure_started()
    try:
        for i in range(args.voices):
            poly.note_on(48 + (i * 7) % 40, velocity=0.6)
        time.sleep(args.seconds)
    finally:
        engine.stop()

    n_drop = 10
    wall_ms = np.asarray(wall[n_drop:]) * 1000
    freqs = np.asarray(freqs[n_drop:], dtype=float)
    cpu_ids = cpu_ids[n_drop:]
    print(f"blocks: {len(wall_ms)}  xruns: {engine.callback_status_count}")
    print(f"wall ms   mean/p99/max: {wall_ms.mean():.3f} / {np.percentile(wall_ms, 99):.3f} / {wall_ms.max():.3f}")
    print(f"% budget  mean/p99/max: {wall_ms.mean()/deadline_ms*100:.1f} / "
          f"{np.percentile(wall_ms, 99)/deadline_ms*100:.1f} / {wall_ms.max()/deadline_ms*100:.1f}")

    order = np.argsort(wall_ms)[::-1]
    print("slowest blocks: wall_ms  cpu  freq_MHz_at_block_end")
    for r in order[:15]:
        print(f"  wall={wall_ms[r]:8.3f}  cpu={cpu_ids[r]:2d}  freq={freqs[r]/1000:7.1f}MHz")
    print("fastest blocks:")
    for r in order[-10:]:
        print(f"  wall={wall_ms[r]:8.3f}  cpu={cpu_ids[r]:2d}  freq={freqs[r]/1000:7.1f}MHz")

    valid = freqs > 0
    if valid.sum() > 10:
        corr = np.corrcoef(freqs[valid], wall_ms[valid])[0, 1]
        print(f"corr(freq_at_block_end, wall_ms) = {corr:.3f}  "
              f"(strongly negative -> the governor's clock speed, not extra work, explains the spikes)")


if __name__ == "__main__":
    main()
