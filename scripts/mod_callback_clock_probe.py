"""Issue #231, second pass: separate *how much work a block does* from *how
fast the machine was running while it did it* -- and then test the causal
claim directly, without root.

#231's first diagnosis (commit `32bfca3`, `mod_callback_freq_probe.py`)
correlated each block's wall-clock duration against the executing core's
`scaling_cur_freq` and found r = -0.66 to -0.89 under this machine's
`powersave` governor. That is a correlation between two numbers the kernel
reports, and decision 70 §5/§7 recorded the gap it left: the one experiment
that would settle it -- pin the governor to `performance`, re-run, watch the
spike collapse -- needs root, which no session has had here.

This probe closes most of that gap two ways, both root-free:

**1. A per-block speed yardstick.** Immediately after each audio block, the
callback runs a *fixed* amount of arithmetic (a pre-sized, allocation-free
NumPy kernel repeated a calibrated number of times) and times it. The work is
identical on every block by construction, so its duration measures nothing but
the machine's instantaneous speed. Dividing each block's wall time by that
yardstick gives **normalised cost** -- what the block would have cost at the
run's median machine speed. Three outcomes, and they are mutually exclusive:

  - normalised cost is flat while raw cost spreads 4x  -> the spread is the
    machine's speed, not the DSP's work (DVFS, or contention);
  - normalised cost spreads too                        -> some blocks really
    do more work (the algorithmic story #231 listed as a candidate);
  - wall time exceeds the thread's own CPU time        -> the thread was off
    the CPU, i.e. preempted, not slowed.

All three are recorded every block: `wall` (`perf_counter`), `cpu`
(`thread_time`, which counts CPU *time* and therefore rises when the clock
falls), and `calib`.

**2. The A/B decision 70 said needed root: a SCHED_IDLE clock keeper.**
`--clock-keeper` starts one spinning thread per logical CPU at `SCHED_IDLE`
-- the *lowest* scheduling class Linux has, which an unprivileged process is
allowed to move itself into (only raising priority is restricted). A
`SCHED_IDLE` spinner is preempted the instant any normal thread becomes
runnable, so it costs the audio callback essentially no scheduling latency,
but it keeps the cores out of deep idle states and keeps the governor's
utilisation estimate high -- an approximation of what the `performance`
governor would do, obtainable without touching a system-wide setting
(decision 70 §4: never touch it). Run the same patch with and without it and
compare the spread.

Its one confound is stated up front rather than discovered later: this is a
2-core/4-thread part, so a spinner on an SMT sibling steals real execution
resources even while yielding scheduler-wise. Expect the *mean* to rise
slightly under `--clock-keeper`; the question it answers is what happens to
the *spread*.

Usage:
    .venv/bin/python scripts/mod_callback_clock_probe.py --patch worst
    .venv/bin/python scripts/mod_callback_clock_probe.py --patch worst --clock-keeper
    .venv/bin/python scripts/mod_callback_clock_probe.py --patch baseline --seconds 15
"""

import argparse
import gc
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notecolor.settings import config                  # noqa: E402
from notecolor.audio import sound_engine               # noqa: E402
from notecolor.audio.graph.contract import Activation   # noqa: E402
from notecolor.audio.graph.poly import PolyGraph       # noqa: E402
import mod_callback_cost as base                       # noqa: E402


# --------------------------------------------------------------------------
# The speed yardstick: fixed work, no allocation, small enough to stay in L1
# so it measures clock speed rather than memory bandwidth.
# --------------------------------------------------------------------------

_CALIB_BUF = np.ones(512, dtype=np.float64)


def _calibration_kernel(reps):
    buf = _CALIB_BUF
    for _ in range(reps):
        np.multiply(buf, 1.0000001, out=buf)
        np.add(buf, 1e-9, out=buf)


def calibrate_reps(target_us=200.0):
    """Pick a repetition count whose kernel costs roughly `target_us`, timed
    on a warm core so the figure is the machine's *fast* speed."""
    reps = 8
    for _ in range(30):
        _calibration_kernel(reps)          # warm
        t0 = time.perf_counter()
        _calibration_kernel(reps)
        took_us = (time.perf_counter() - t0) * 1e6
        if took_us >= target_us * 0.8:
            return reps, took_us
        reps = max(reps + 1, int(reps * max(1.5, target_us / max(took_us, 1.0))))
    return reps, took_us


# --------------------------------------------------------------------------
# /proc/self/stat: executing CPU, and minor/major fault counters, in one read
# --------------------------------------------------------------------------

def proc_stat_fields():
    """(cpu, minflt, majflt). Read from `/proc/thread-self/stat`, not
    `/proc/self/stat`: the latter reports the *thread group leader's* last
    scheduled CPU, which is not the audio thread's -- the earlier probe in
    `mod_callback_freq_probe.py` used it, and that is worth knowing when
    comparing the two. The `comm` field can contain spaces and parentheses,
    so split after its closing ')' rather than on whitespace."""
    try:
        with open("/proc/thread-self/stat") as handle:
            raw = handle.read()
        rest = raw[raw.rindex(")") + 2:].split()
        # `rest[0]` is `state`, i.e. field 3 in the man page's 1-based
        # numbering -- so man-page field N is rest[N - 3].
        return int(rest[36]), int(rest[7]), int(rest[9])
    except Exception:
        return -1, -1, -1


def read_freq_khz(cpu):
    try:
        with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq") as handle:
            return int(handle.read())
    except Exception:
        return -1


# --------------------------------------------------------------------------
# The clock keeper
# --------------------------------------------------------------------------

class ClockKeeper:
    """One spinning thread per logical CPU at SCHED_IDLE. Unprivileged: Linux
    lets a thread lower itself into SCHED_IDLE freely, and any normal thread
    preempts it immediately."""

    def __init__(self):
        self._stop = threading.Event()
        self._threads = []
        self.policy_ok = None

    def _spin(self, index):
        try:
            os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
            if index == 0:
                self.policy_ok = True
        except (OSError, AttributeError) as exc:      # pragma: no cover - env dependent
            if index == 0:
                self.policy_ok = f"denied: {exc}"
        try:
            os.sched_setaffinity(0, {index})
        except OSError:
            pass
        acc = 1.0
        while not self._stop.is_set():
            for _ in range(20000):
                acc = acc * 1.0000001 + 1e-9
            if acc > 1e300:
                acc = 1.0

    def start(self):
        for index in range(os.cpu_count() or 1):
            thread = threading.Thread(target=self._spin, args=(index,), daemon=True)
            thread.start()
            self._threads.append(thread)
        time.sleep(0.5)

    def stop(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)


# --------------------------------------------------------------------------

def loadavg():
    with open("/proc/loadavg") as handle:
        return handle.read().split()[:3]


def spread(values):
    """max / p50 -- the '4x' number #231 is about, in one figure."""
    p50 = float(np.percentile(values, 50))
    return float(np.max(values)) / p50 if p50 else float("nan")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", choices=sorted(base.PATCHES), default="worst")
    parser.add_argument("--voices", type=int, default=config.POLYPHONY_SYNTH_VIEW)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--clock-keeper", action="store_true",
                        help="run SCHED_IDLE spinners on every core to hold the clock up")
    parser.add_argument("--calib-us", type=float, default=200.0)
    args = parser.parse_args()

    reps, calib_us = calibrate_reps(args.calib_us)
    print(f"calibration kernel: {reps} reps ~ {calib_us:.1f}us on a warm core")

    keeper = None
    if args.clock_keeper:
        keeper = ClockKeeper()
        keeper.start()
        print(f"clock keeper: {os.cpu_count()} SCHED_IDLE spinners, policy={keeper.policy_ok}")

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

    wall, cpu_time, calib, freq_in, freq_out, cpus, faults = [], [], [], [], [], [], []
    gc_blocks = []
    block_index = [0]
    inner = engine._callback

    def on_gc(phase, info):
        if phase == "stop":
            gc_blocks.append(block_index[0])

    gc.callbacks.append(on_gc)

    def timed(outdata, frames, time_info, status):
        cpu0, minflt0, majflt0 = proc_stat_fields()
        f_in = read_freq_khz(cpu0)
        c0 = time.thread_time()
        w0 = time.perf_counter()
        inner(outdata, frames, time_info, status)
        w1 = time.perf_counter()
        c1 = time.thread_time()
        k0 = time.perf_counter()
        _calibration_kernel(reps)
        k1 = time.perf_counter()
        cpu1, minflt1, majflt1 = proc_stat_fields()
        wall.append(w1 - w0)
        cpu_time.append(c1 - c0)
        calib.append(k1 - k0)
        freq_in.append(f_in)
        freq_out.append(read_freq_khz(cpu1))
        cpus.append(cpu1)
        faults.append((minflt1 - minflt0) + (majflt1 - majflt0) * 1000)
        block_index[0] += 1

    engine._callback = timed
    deadline_ms = engine.block_size / engine.sample_rate * 1000
    print(f"patch={args.patch}  voices={args.voices}  deadline={deadline_ms:.3f}ms  "
          f"loadavg before={loadavg()}")
    engine.ensure_started()
    try:
        for i in range(args.voices):
            poly.note_on(48 + (i * 7) % 40, velocity=0.6)
        time.sleep(args.seconds)
    finally:
        engine.stop()
        if keeper:
            keeper.stop()
        gc.callbacks.remove(on_gc)

    drop = 10
    wall_ms = np.asarray(wall[drop:]) * 1000
    cpu_ms = np.asarray(cpu_time[drop:]) * 1000
    calib_ms = np.asarray(calib[drop:]) * 1000
    freq_in_a = np.asarray(freq_in[drop:], dtype=float)
    freq_out_a = np.asarray(freq_out[drop:], dtype=float)
    faults_a = np.asarray(faults[drop:], dtype=float)

    pct = wall_ms / deadline_ms * 100
    print(f"\nblocks={len(wall_ms)}  xruns={engine.callback_status_count}  "
          f"loadavg after={loadavg()}")
    print("                       mean     p50     p95     p99     max")
    for name, arr in (("callback ms", wall_ms), ("% of budget", pct)):
        print(f"{name:>14}  {np.mean(arr):8.2f}{np.percentile(arr, 50):8.2f}"
              f"{np.percentile(arr, 95):8.2f}{np.percentile(arr, 99):8.2f}{np.max(arr):8.2f}")

    # --- the three-way discriminator -------------------------------------
    speed = calib_ms / np.median(calib_ms)        # >1 == machine was slow
    normalised_ms = wall_ms / speed
    off_cpu_ms = wall_ms - cpu_ms

    print("\n-- what varies --")
    print(f"raw wall          spread (max/p50) = {spread(wall_ms):5.2f}x")
    print(f"normalised wall   spread (max/p50) = {spread(normalised_ms):5.2f}x   "
          f"(mean {np.mean(normalised_ms):.2f}ms, p99 {np.percentile(normalised_ms, 99):.2f}ms, "
          f"max {np.max(normalised_ms):.2f}ms)")
    print(f"calibration       spread (max/p50) = {spread(calib_ms):5.2f}x   "
          f"(p50 {np.median(calib_ms) * 1000:.0f}us, max {np.max(calib_ms) * 1000:.0f}us)")
    print(f"corr(calib, wall)                  = {np.corrcoef(calib_ms, wall_ms)[0, 1]:+.3f}"
          "   <- machine speed explains the block")
    valid = freq_in_a > 0
    if valid.sum() > 10:
        print(f"corr(freq_at_entry, wall)          = "
              f"{np.corrcoef(freq_in_a[valid], wall_ms[valid])[0, 1]:+.3f}")
        print(f"corr(freq_at_entry, calib)         = "
              f"{np.corrcoef(freq_in_a[valid], calib_ms[valid])[0, 1]:+.3f}"
              "   <- the yardstick sees the same governor")
    print(f"off-CPU (wall-cpu) p99/max         = "
          f"{np.percentile(off_cpu_ms, 99):.3f} / {np.max(off_cpu_ms):.3f} ms"
          "   <- preemption, if any")

    slow = np.argsort(wall_ms)[::-1][:20]
    fast = np.argsort(wall_ms)[:20]
    print("\n-- slowest 20 vs fastest 20 blocks --")
    print(f"{'':16}{'wall ms':>9}{'cpu ms':>9}{'calib us':>10}{'MHz in':>9}{'MHz out':>9}{'minflt':>8}")
    for label, idx in (("slowest", slow), ("fastest", fast)):
        print(f"{label:16}{np.mean(wall_ms[idx]):9.2f}{np.mean(cpu_ms[idx]):9.2f}"
              f"{np.mean(calib_ms[idx]) * 1000:10.0f}{np.mean(freq_in_a[idx]) / 1000:9.0f}"
              f"{np.mean(freq_out_a[idx]) / 1000:9.0f}{np.mean(faults_a[idx]):8.2f}")

    # How much of the slow/fast gap the yardstick alone accounts for. Taking
    # the mean of twenty blocks on each end rather than single blocks keeps
    # the yardstick's own per-block noise out of the ratio.
    wall_ratio = np.mean(wall_ms[slow]) / np.mean(wall_ms[fast])
    calib_ratio = np.mean(calib_ms[slow]) / np.mean(calib_ms[fast])
    print(f"\nslow/fast wall ratio  = {wall_ratio:5.2f}x")
    print(f"slow/fast speed ratio = {calib_ratio:5.2f}x  "
          f"-> machine speed accounts for {min(calib_ratio / wall_ratio, 1.0) * 100:.0f}% of it; "
          f"residual extra work = {max(wall_ratio / calib_ratio, 1.0):.2f}x")

    print(f"\npage faults during measured blocks: {faults_a.sum():.0f} "
          f"(blocks with any: {int((faults_a > 0).sum())})")
    gc_in_run = [b for b in gc_blocks if b >= drop]
    print(f"gc collections during measured blocks: {len(gc_in_run)} "
          f"(blocks: {gc_in_run[:10]}{'...' if len(gc_in_run) > 10 else ''})")
    if gc_in_run:
        gc_cost = wall_ms[[b - drop for b in gc_in_run if b - drop < len(wall_ms)]]
        print(f"  their wall ms: mean {np.mean(gc_cost):.2f} vs run mean {np.mean(wall_ms):.2f}")
    print(f"cpus used: {sorted(set(cpus[drop:]))}")


if __name__ == "__main__":
    main()
