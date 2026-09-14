"""Decision 55's revisit trigger, measured against the patch graph (#207).

Decision 55 states the trigger so it can be checked rather than argued:
**sustained callback cost above ~70% of the block budget at the view's own
voice cap, measured in a real `sounddevice` callback rather than a
benchmark.** This script is that measurement for the graph engine, and it
is a script rather than a pytest test for the same reason
`sound_engine_smoke.py` is: it opens an actual output stream against the
real default device, and a test suite that needs a sound card is a test
suite that fails on the machine that has none.

It measures the whole callback, not the graph alone -- which is the point.
The number the trigger is about is what PortAudio's thread has to finish
inside 11.61 ms (512 frames at 44100 Hz), and that includes the voice
manager, the effects bus and the soft-clip whether or not a graph is
present. `--no-graph` runs the same patch of work with the graph
uninstalled, so the graph's own share is a subtraction rather than a guess.

Nothing here asserts on sound. The three numbers that matter are reported
and a muted machine produces all of them honestly:

  * **callback ms, mean and p99**, against the block deadline. p99 is the
    one to read: decision 55 says *sustained*, and a mean that hides a
    tail of over-budget blocks is how #100's finding (the ring buffer
    conceals overruns until the engine has already xrun) bites.
  * **PortAudio's own status flags**, counted by
    `SoundEngine.callback_status_count`. This is the only real pass/fail
    signal available -- an over-budget callback is invisible until the
    driver actually xruns. It must be 0.
  * **how many graph voices were actually sounding**, so a run that stole
    its own notes and measured four voices at the 16-voice cap cannot be
    mistaken for a clean result.

Usage:
    .venv/bin/python scripts/graph_callback_cost.py
    .venv/bin/python scripts/graph_callback_cost.py --voices 16 --seconds 5
    .venv/bin/python scripts/graph_callback_cost.py --no-graph      # baseline
    .venv/bin/python scripts/graph_callback_cost.py --with-delay    # + a feedback loop

The patch is the Synth View's default chain -- Osc -> Filter -> Amp Env ->
Mix -- because that is what the voice cap is a cap on. `--with-delay` adds
the once-only half a feedback patch has, which runs once however many notes
are held and so costs the same at one voice as at sixteen; it is there to
show that, not to change the headline number.
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from notecolor.settings import config                                   # noqa: E402
from notecolor.audio import sound_engine                                # noqa: E402
from notecolor.audio.graph.contract import Activation                   # noqa: E402
from notecolor.audio.graph.graph import ModuleGraph                     # noqa: E402
from notecolor.audio.graph.modules.delay import Delay                   # noqa: E402
from notecolor.audio.graph.modules.envelope import AmpEnvelope          # noqa: E402
from notecolor.audio.graph.modules.filter import StateVariableFilter    # noqa: E402
from notecolor.audio.graph.modules.oscillator import WavetableOscillator  # noqa: E402
from notecolor.audio.graph.modules.passthrough import Passthrough       # noqa: E402
from notecolor.audio.graph.poly import MixModule, PolyGraph             # noqa: E402


def percentile(values, q):
    return float(np.percentile(np.asarray(values), q)) if values else float("nan")


def build(voices, with_delay):
    """The Synth View's default patch, as the bridge builds it."""
    graph = ModuleGraph()
    graph.add("mix", MixModule())
    graph.add("osc1", WavetableOscillator("saw"))
    graph.add("filter", StateVariableFilter())
    graph.add("amp_env", AmpEnvelope())
    for source, dest in (("osc1", "filter"), ("filter", "amp_env"), ("amp_env", "mix")):
        graph.force_connect(source, "out", dest, "in")
    if with_delay:
        graph.add("delay", Delay())
        graph.add("chorus", Passthrough("Chorus"))
        for source, dest in (("mix", "delay"), ("delay", "chorus"), ("chorus", "delay")):
            graph.force_connect(source, "out", dest, "in")
    return PolyGraph(graph, voices=voices)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voices", type=int, default=config.POLYPHONY_SYNTH_VIEW,
                        help="notes to hold (default: the Synth View's cap)")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--no-graph", action="store_true",
                        help="baseline: the same callback with no graph installed")
    parser.add_argument("--with-delay", action="store_true",
                        help="add the once-only feedback loop after Mix")
    args = parser.parse_args()

    engine = sound_engine.SoundEngine(detection_active=False)
    poly = None
    if not args.no_graph:
        poly = build(args.voices, args.with_delay)
        poly.activate(Activation(engine.sample_rate, engine.block_size))
        # A long release, so every note is still sounding at the end of the
        # run. Measuring a cap of sixteen while twelve of them have faded
        # out is how a comfortable number gets reported for a patch that is
        # not comfortable.
        for voice in poly.voices:
            for node_id in ("amp_env",):
                voice.graph.node(node_id).module.params.set("release", 30.0)
                voice.graph.node(node_id).module.params.set("decay", 30.0)
                voice.graph.node(node_id).module.params.set("sustain", 0.8)
        engine.set_graph(poly, activate=False)

    durations = []
    sounding = [0]
    inner = engine._callback

    def timed(outdata, frames, time_info, status):
        start = time.perf_counter()
        inner(outdata, frames, time_info, status)
        durations.append(time.perf_counter() - start)
        if poly is not None:
            sounding[0] = max(sounding[0], len(poly.active_voices))

    engine._callback = timed

    deadline_ms = engine.block_size / engine.sample_rate * 1000
    print(f"patch={'none' if args.no_graph else ('default+delay' if args.with_delay else 'default')}  "
          f"voices={args.voices}  block={engine.block_size} frames @ {engine.sample_rate}Hz  "
          f"(deadline {deadline_ms:.2f}ms)")
    engine.ensure_started()
    try:
        if poly is not None:
            for i in range(args.voices):
                poly.note_on(48 + (i * 7) % 40, velocity=0.6)
        time.sleep(args.seconds)
    finally:
        engine.stop()

    # The first few blocks include PortAudio's own thread warm-up and the
    # first touch of every page these buffers live on. Decision 55's
    # trigger is about sustained cost, so they are dropped rather than
    # averaged in.
    warm = durations[10:] or durations
    mean_ms = float(np.mean(warm)) * 1000
    p99_ms = percentile(warm, 99) * 1000
    print(f"blocks rendered      : {len(durations)}  (first 10 dropped as warm-up)")
    print(f"callback ms mean/p99 : {mean_ms:.3f} / {p99_ms:.3f}")
    print(f"% of block budget    : {mean_ms / deadline_ms * 100:.1f}% mean / "
          f"{p99_ms / deadline_ms * 100:.1f}% p99"
          f"   <- decision 55 revisits above ~70% sustained")
    print(f"driver status flags  : {engine.callback_status_count}   <- xruns; must be 0")
    if poly is not None:
        print(f"graph voices sounding: {sounding[0]}  (requested {args.voices})")
    print(f"old-engine voices    : {engine.voices.active_count()}   <- graph notes spend none of that budget")


if __name__ == "__main__":
    main()
