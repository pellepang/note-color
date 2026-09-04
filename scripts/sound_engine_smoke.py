"""Real-audio-device smoke test for `sound_engine.SoundEngine` (map #99,
ticket #112). Not a pytest test: it opens an actual
`sounddevice.OutputStream` against the real default device, so it belongs
with `acoustic_pipeline_test.py`/`rhythm_accuracy_test.py` as a manual
verification tool rather than in `tests/`.

Nothing here asserts on *sound*. It reports numbers a muted machine can
still produce honestly:

  * PortAudio's own callback `status` flags (underflows/xruns), counted by
    `SoundEngine.callback_status_count` -- prototype #100's finding was
    that over-budget rendering is invisible until the driver actually
    xruns, so this counter is the only real pass/fail signal available.
  * measured wall-clock time spent inside the callback, mean and p99,
    against the block deadline (block_size / sample_rate).
  * the voice manager's own bookkeeping: peak simultaneous voices, how
    many note-ons were stolen, and whether every voice was reclaimed once
    released.

Usage:
    .venv/bin/python scripts/sound_engine_smoke.py [--voices N]
                                                   [--seconds S]
                                                   [--polyphony N]
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                     # noqa: E402
import sound_engine               # noqa: E402


def percentile(values, q):
    return float(np.percentile(np.asarray(values), q)) if values else float("nan")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voices", type=int, default=24, help="simultaneous notes to hold")
    parser.add_argument("--seconds", type=float, default=3.0, help="how long to hold them")
    parser.add_argument("--polyphony", type=int, default=None, help="override the hard cap for this run")
    args = parser.parse_args()

    engine = sound_engine.SoundEngine(detection_active=False)
    if args.polyphony is not None:
        engine.voices._polyphony = args.polyphony

    durations = []
    peak_voices = [0]
    inner_callback = engine._callback

    def timed_callback(outdata, frames, time_info, status):
        start = time.perf_counter()
        inner_callback(outdata, frames, time_info, status)
        durations.append(time.perf_counter() - start)
        peak_voices[0] = max(peak_voices[0], engine.voices.active_count())

    engine._callback = timed_callback

    print(f"cap={engine.voices.polyphony} voices  block={engine.block_size} frames @ {engine.sample_rate}Hz "
          f"(deadline {engine.block_size / engine.sample_rate * 1000:.2f}ms)")
    engine.ensure_started()
    try:
        voice_ids = [engine.note_on(48 + (i * 7) % 40, velocity=0.6) for i in range(args.voices)]
        time.sleep(args.seconds)
        held = engine.voices.active_count()
        for voice_id in voice_ids:
            engine.release_voice(voice_id)
        time.sleep(config.PLAYBACK_RELEASE_SECONDS + 0.5)
        remaining = engine.voices.active_count()
    finally:
        engine.stop()

    deadline_ms = engine.block_size / engine.sample_rate * 1000
    print(f"blocks rendered      : {len(durations)}")
    print(f"callback ms mean/p99 : {np.mean(durations) * 1000:.3f} / {percentile(durations, 99) * 1000:.3f}"
          f"  ({np.mean(durations) * 1000 / deadline_ms * 100:.1f}% / "
          f"{percentile(durations, 99) * 1000 / deadline_ms * 100:.1f}% of deadline)")
    print(f"driver status flags  : {engine.callback_status_count}   <- xruns; must be 0")
    print(f"peak voices in mix   : {peak_voices[0]}  (requested {args.voices}, held {held})")
    print(f"voices stolen        : {engine.voices.steal_count}")
    print(f"voices left after rel: {remaining}   <- must be 0")


if __name__ == "__main__":
    main()
