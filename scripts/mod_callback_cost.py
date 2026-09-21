"""Decision 55's revisit trigger, measured against the modulation layer
(#208 stage 2, decision 67), re-measured for issue #229.

Decision 67's own "62%" figure was a *projection*: raw numbers taken on a
machine under load average 8.4 (four cores, contended by concurrent agent
work) were calibrated against decision 66's recorded baseline by ratio,
because a direct reading on that machine was not trustworthy. That is not
what decision 55's trigger asks for -- **sustained callback cost above ~70%
of the block budget at the view's voice cap, measured in a real
`sounddevice` callback** -- so this script takes the direct measurement
instead, using exactly `scripts/graph_callback_cost.py`'s harness (issue
#229 says to reuse it rather than invent a second method): the whole
`SoundEngine._callback` is timed with `time.perf_counter()` around the real
call, against a real output stream on the real default device. Nothing
here is a synthetic loop around `PolyGraph.process()` alone.

Three patches, all sixteen voices, all held for the whole run so a run
that quietly lost voices to envelope decay cannot be mistaken for a
16-voice reading:

  * ``baseline``   -- Osc -> Filter -> Amp Env -> Mix, no modulation cable
                      at all (the same patch `graph_callback_cost.py`'s
                      default already measures; reproduced here so all
                      three rows come from one script and one run).
  * ``realistic``  -- the same chain plus two modulation cables a person
                      might actually patch: an LFO onto the filter cutoff,
                      and a Mod Envelope onto the filter resonance.
  * ``worst``      -- decision 67's own stress-test patch, verbatim:
                      `tests/test_synth_graph_mod_stage2.py::_wide_patch`
                      (every newly-wired destination modulated at once --
                      cutoff, key_tracking, pulse_width, resonance, noise
                      level, delay feedback, delay mix). No Amp Envelope in
                      that patch (matching the test fixture exactly), so
                      notes never finish and all sixteen voices stay
                      sounding for the whole run without needing the
                      long-release trick `graph_callback_cost.py` uses.

Usage:
    .venv/bin/python scripts/mod_callback_cost.py
    .venv/bin/python scripts/mod_callback_cost.py --seconds 10
    .venv/bin/python scripts/mod_callback_cost.py --patch worst
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from notecolor.settings import config                                    # noqa: E402
from notecolor.audio import sound_engine                                 # noqa: E402
from notecolor.audio.graph.contract import Activation                    # noqa: E402
from notecolor.audio.graph.graph import ModuleGraph                      # noqa: E402
from notecolor.audio.graph.modules.delay import Delay                    # noqa: E402
from notecolor.audio.graph.modules.envelope import AmpEnvelope, ModEnvelope  # noqa: E402
from notecolor.audio.graph.modules.filter import StateVariableFilter      # noqa: E402
from notecolor.audio.graph.modules.lfo import Lfo                        # noqa: E402
from notecolor.audio.graph.modules.noise import Noise                    # noqa: E402
from notecolor.audio.graph.modules.oscillator import WavetableOscillator  # noqa: E402
from notecolor.audio.graph.modules.short_delay import ShortDelay         # noqa: E402
from notecolor.audio.graph.poly import MixModule, PolyGraph               # noqa: E402


def percentile(values, q):
    return float(np.percentile(np.asarray(values), q)) if values else float("nan")


def build_baseline(voices):
    """Osc -> Filter -> Amp Env -> Mix, no modulation cable -- the same
    shape `graph_callback_cost.py`'s default patch measures."""
    graph = ModuleGraph()
    graph.add("mix", MixModule())
    graph.add("osc1", WavetableOscillator("saw"))
    graph.add("filter", StateVariableFilter())
    graph.add("amp_env", AmpEnvelope())
    for source, dest in (("osc1", "filter"), ("filter", "amp_env"), ("amp_env", "mix")):
        graph.force_connect(source, "out", dest, "in")
    return graph, ("amp_env",)


def build_realistic(voices):
    """The same chain plus two modulation cables: LFO -> cutoff (stage 1's
    own proof-of-concept shape) and Mod Envelope -> resonance -- what
    decision 67 §2 calls "a patch modulating one or two of these", not the
    full stress test."""
    graph = ModuleGraph()
    graph.add("mix", MixModule())
    graph.add("osc1", WavetableOscillator("saw"))
    graph.add("lfo", Lfo("per_note"))
    graph.add("modenv", ModEnvelope())
    graph.add("filter", StateVariableFilter())
    graph.add("amp_env", AmpEnvelope())
    for source, dest in (("osc1", "filter"), ("filter", "amp_env"), ("amp_env", "mix")):
        graph.force_connect(source, "out", dest, "in")
    assert graph.connect_modulation("lfo", "mod", "filter", "cutoff", depth=0.6).ok
    assert graph.connect_modulation("modenv", "mod", "filter", "resonance", depth=0.5).ok
    return graph, ("amp_env",)


def build_worst(voices):
    """Decision 67's own stress-test patch, verbatim from
    `tests/test_synth_graph_mod_stage2.py::_wide_patch`: every destination
    stage 2 wired, modulated at once. No Amp Envelope, matching the test
    fixture exactly -- notes drone forever, so all sixteen voices stay
    sounding for the whole run without any long-release trick."""
    graph = ModuleGraph()
    graph.add("osc", WavetableOscillator("saw"))
    graph.add("lfo", Lfo("per_note"))
    graph.add("modenv", ModEnvelope())
    graph.add("noise", Noise())
    graph.add("filter", StateVariableFilter())
    graph.add("delay", ShortDelay())
    graph.add("mix", MixModule())
    assert graph.connect("osc", "out", "filter", "in").ok
    assert graph.connect("noise", "out", "filter", "in").ok
    assert graph.connect("filter", "out", "delay", "in").ok
    assert graph.connect("delay", "out", "mix", "in").ok
    assert graph.connect_modulation("lfo", "mod", "filter", "cutoff", depth=0.6).ok
    assert graph.connect_modulation("lfo", "mod", "filter", "key_tracking", depth=0.3).ok
    assert graph.connect_modulation("lfo", "mod", "osc", "pulse_width", depth=0.4).ok
    assert graph.connect_modulation("modenv", "mod", "filter", "resonance", depth=0.5).ok
    assert graph.connect_modulation("modenv", "mod", "noise", "level", depth=0.3).ok
    assert graph.connect_modulation("lfo", "mod", "delay", "feedback", depth=0.3).ok
    assert graph.connect_modulation("lfo", "mod", "delay", "mix", depth=0.3).ok
    return graph, ()


#: Depth for the `chorus` patch's `time` cable, as a fraction of the knob's
#: range (`ModRoute.apply()` scales linearly by the range, not
#: logarithmically). `ShortDelay`'s range is 0.1ms..50ms, so 0.1 is a
#: +/-5ms sweep -- a chorus's actual depth. Raise it on the command line
#: (`--chorus-depth 0.4`) to reach the stress case described below.
CHORUS_DEPTH = 0.1


def build_chorus(voices, depth=CHORUS_DEPTH):
    """`worst`, plus the destination #228 added: an LFO on the short
    delay's `time`.

    Deliberately `worst` + one cable rather than a patch of its own, so the
    difference between the two rows is the interpolated read and nothing
    else. Note what that read replaces rather than adds to: on such a block
    the module takes its per-sample gather *instead of* the two slice
    copies `_copy_out()` would have made, so the honest number is the
    delta, not the row.

    **The chunk count is the variable that matters, not the cable.**
    `ShortDelay` cuts its block into chunks no longer than its own delay
    (`effects.py`'s invariant 3), so its cost is dominated by *how short
    the delay is*, modulated or not: at the 12ms default one chunk covers
    a 512-frame block, and at the knob's 0.1ms floor it takes ~170. A
    `time` cable deep enough to sweep into that floor therefore buys the
    floor's chunk count, which is why `--chorus-depth` is exposed --
    `--set delay:time=0.0005` measures the same cliff with no modulation
    at all, and the two together separate "what #228 added" from "what
    `ShortDelay` has always cost at short settings".
    """
    graph, long_release = build_worst(voices)
    assert graph.connect_modulation("lfo", "mod", "delay", "time", depth=depth).ok
    return graph, long_release


PATCHES = {
    "baseline": build_baseline,
    "realistic": build_realistic,
    "worst": build_worst,
    "chorus": build_chorus,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", choices=sorted(PATCHES), default="worst")
    parser.add_argument("--voices", type=int, default=config.POLYPHONY_SYNTH_VIEW,
                        help="notes to hold (default: the Synth View's cap)")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--chorus-depth", type=float, default=CHORUS_DEPTH,
                        help="`chorus` only: depth of the `time` cable")
    parser.add_argument("--set", action="append", default=[], metavar="NODE:PARAM=VALUE",
                        help="knob value applied to every voice before the run, "
                             "e.g. --set delay:time=0.0005 (repeatable)")
    args = parser.parse_args()

    engine = sound_engine.SoundEngine(detection_active=False)
    if args.patch == "chorus":
        graph, long_release_nodes = build_chorus(args.voices, depth=args.chorus_depth)
    else:
        graph, long_release_nodes = PATCHES[args.patch](args.voices)
    poly = PolyGraph(graph, voices=args.voices)
    poly.activate(Activation(engine.sample_rate, engine.block_size))
    # Same trick `graph_callback_cost.py` uses: a long release so every
    # note is still sounding at the end of the run, for patches that have
    # an Amp Envelope to close them. The worst-case patch has none, so
    # `long_release_nodes` is empty there and nothing to do.
    for voice in poly.voices:
        for node_id in long_release_nodes:
            voice.graph.node(node_id).module.params.set("release", 30.0)
            voice.graph.node(node_id).module.params.set("decay", 30.0)
            voice.graph.node(node_id).module.params.set("sustain", 0.8)
    # `--set` is applied after activation and to every voice, because a
    # `PolyGraph` clones its modules per voice -- setting the canvas
    # module's knob would reach none of them.
    for spec in args.set:
        target, _, value = spec.partition("=")
        node_id, _, param_id = target.partition(":")
        for voice in poly.voices:
            voice.graph.node(node_id).module.params.set_immediate(param_id, float(value))
    engine.set_graph(poly, activate=False)

    durations = []
    sounding = [0]
    inner = engine._callback

    def timed(outdata, frames, time_info, status):
        start = time.perf_counter()
        inner(outdata, frames, time_info, status)
        durations.append(time.perf_counter() - start)
        sounding[0] = max(sounding[0], len(poly.active_voices))

    engine._callback = timed

    deadline_ms = engine.block_size / engine.sample_rate * 1000
    print(f"patch={args.patch}  voices={args.voices}  "
          f"block={engine.block_size} frames @ {engine.sample_rate}Hz  "
          f"(deadline {deadline_ms:.2f}ms)")
    engine.ensure_started()
    try:
        for i in range(args.voices):
            poly.note_on(48 + (i * 7) % 40, velocity=0.6)
        time.sleep(args.seconds)
    finally:
        engine.stop()

    # First few blocks include PortAudio thread warm-up and first-touch
    # page faults -- decision 55's trigger is about sustained cost, so
    # they are dropped rather than averaged in, exactly as
    # `graph_callback_cost.py` does.
    warm = durations[10:] or durations
    arr_ms = np.asarray(warm) * 1000
    mean_ms = float(np.mean(arr_ms))
    p50_ms = percentile(warm, 50) * 1000
    p95_ms = percentile(warm, 95) * 1000
    p99_ms = percentile(warm, 99) * 1000
    max_ms = float(np.max(arr_ms))
    over_budget = int(np.sum(arr_ms > deadline_ms))
    print(f"blocks rendered      : {len(durations)}  (first 10 dropped as warm-up)")
    print(f"callback ms mean/p50/p95/p99/max : "
          f"{mean_ms:.3f} / {p50_ms:.3f} / {p95_ms:.3f} / {p99_ms:.3f} / {max_ms:.3f}")
    print(f"% of block budget    : {mean_ms / deadline_ms * 100:.1f}% mean / "
          f"{p50_ms / deadline_ms * 100:.1f}% p50 / "
          f"{p95_ms / deadline_ms * 100:.1f}% p95 / "
          f"{p99_ms / deadline_ms * 100:.1f}% p99 / "
          f"{max_ms / deadline_ms * 100:.1f}% max"
          f"   <- decision 55 revisits above ~70% sustained")
    print(f"blocks over deadline  : {over_budget} / {len(warm)}")
    print(f"driver status flags  : {engine.callback_status_count}   <- xruns; must be 0")
    print(f"graph voices sounding: {sounding[0]}  (requested {args.voices})")
    print(f"old-engine voices    : {engine.voices.active_count()}   <- graph notes spend none of that budget")


if __name__ == "__main__":
    main()
