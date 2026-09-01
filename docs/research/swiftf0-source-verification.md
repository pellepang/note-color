# SwiftF0 source verification (issue #80)

Read-and-report task: verify or refute SwiftF0's claimed "no ML
runtime, pure NumPy inference" property by reading its actual inference
source, not package metadata. Scope per issue #80: source verification
only — no Pi-class benchmarking (needs real Raspberry Pi hardware, not
available here) and no acoustic-suite trial. Both remain open follow-up
steps if a future session has Pi hardware available.

## Method

```
.venv/bin/pip install swift-f0
```

pulled in `onnxruntime` (23.1MB wheel) as a dependency — a first, direct
signal before reading a single line of `swift_f0`'s own code. Read
`swift_f0/core.py` (the package's only inference module,
`site-packages/swift_f0/core.py`, 14822 bytes) in full, plus its package
directory listing.

## Finding: the pure-NumPy claim does NOT hold up

**The package ships a bundled ONNX model file and requires ONNX Runtime
to run it.** Directly from the installed package:

```
$ ls site-packages/swift_f0/
core.py  __init__.py  model.onnx  music.py  __pycache__/
```

```python
# swift_f0/core.py, lines 1-2
import onnxruntime
import numpy as np
```

```python
# swift_f0/core.py, lines 122-128 (inside SwiftF0.__init__)
# Locate and verify the bundled ONNX model
model_path = os.path.join(os.path.dirname(__file__), "model.onnx")
if not os.path.exists(model_path):
    raise FileNotFoundError(f"Model file not found at: {model_path}")

# Initialize ONNX runtime session
session_options = onnxruntime.SessionOptions()
session_options.inter_op_num_threads = 1
session_options.intra_op_num_threads = 1
self.pitch_session = onnxruntime.InferenceSession(
    model_path, session_options, providers=["CPUExecutionProvider"]
)
```

Every pitch-detection call (`SwiftF0.detect_from_array()` /
`detect_from_file()`) routes through `self.pitch_session.run(...)` against
this `onnxruntime.InferenceSession` — there is no NumPy-only forward-pass
code path anywhere in `core.py`, no fallback, and no conditional import.
`onnxruntime` is a hard, unconditional dependency of the package as
published on PyPI (`swift-f0==0.1.2`'s own `install_requires` pulls it in
automatically, confirmed by the `pip install` output above pulling
`onnxruntime`/`protobuf`/`flatbuffers` transitively with zero flags asked
for).

**Conclusion: the "pure NumPy inference, no ML runtime" premise this
project's own research doc(s) floated as SwiftF0's key differentiator is
false.** It's an ONNX-Runtime-backed neural network (STFT frontend +
ONNX model, 16kHz/1024-sample-frame/256-hop internal parameters, per
`core.py`'s own docstring), architecturally in the same dependency class
as basic-pitch (also ONNX-based) — not a different, lighter class of
thing at all.

## What this changes about `detection-systems-survey.md`'s framing

The survey's own comparison table said: *"claims `numpy`-only inference,
**not independently confirmed by reading its source**"* and flagged this
exact verification as the reason recommendation 4 existed. That
independent read is now done, and the claim is refuted — this closes the
open question, but in the *negative* direction:

- SwiftF0 does **not** change the "no ML runtime on the live path"
  calculus the way the survey hoped it might. It needs the same class of
  dependency (`onnxruntime`) as basic-pitch, which the survey already
  scoped to **batch/offline-only** use (`virtualnote transcribe`, never
  live) specifically because of that dependency weight.
- The one differentiator that *does* still hold, independent of the
  runtime question: **`onnxruntime` ships official aarch64 manylinux
  wheels on PyPI** (the survey's own independently-verified finding,
  point 3 in `detection-systems-survey.md`'s introduction) — unlike
  aubio/essentia's ARM wheel situation. So SwiftF0 is not disqualified by
  the same hard wheel-risk wall aubio/essentia hit; it's disqualified (for
  the *live* path specifically) by the same "real dependency, real binary
  runtime, not yet worth it for the live/Pi-constrained path" reasoning
  already applied to basic-pitch.
- SwiftF0's actual accuracy numbers (94.07% clean / 91.80% noisy HM,
  96.75%/93.52% octave accuracy — best-in-class in its own paper's
  benchmark) are unaffected by this finding and remain a real, if
  unconfirmed-on-this-project's-own-signal, data point. What changes is
  *where* it could plausibly be adopted: not as a live-path YIN
  replacement without accepting a real new ONNX Runtime dependency on the
  Pi-constrained path (the exact tradeoff `docs/DECISIONS.md`'s "hand-
  rolled YIN, not aubio/librosa" founding decision already weighed and
  rejected for a comparable-weight dependency), but potentially as:
  1. An **optional desktop-tier live backend** (survey recommendation 6,
     already gated on "no action without a concrete accuracy need" and
     "desktop-class hardware only") — this finding doesn't newly justify
     that gate opening, but doesn't close the door either, since
     onnxruntime's wheel story is genuinely fine on desktop.
  2. An **optional `virtualnote transcribe` backend**, the same shape
     already recommended for basic-pitch (survey recommendation 5) — this
     is the more natural fit given the now-confirmed shared dependency
     class, and could plausibly be evaluated alongside basic-pitch rather
     than as a separate investigation, since both need the identical
     `onnxruntime` isolation treatment `batch_transcribe.py`/
     `rhythm_reanalysis.py` already establish the pattern for (a third
     `librosa`-style isolated-import module, gated behind an explicit
     flag).

## What's still open (out of this task's scope)

1. **Real Raspberry Pi-class inference-latency benchmark.** Not run here
   (no Pi hardware in this environment) — the paper's own 132.6ms/5s-clip
   number is desktop-only, and `onnxruntime`'s aarch64 wheel existing
   doesn't tell you how fast the *specific* SwiftF0 model actually runs on
   a Pi Zero 2 W-class CPU. Needed before any live-desktop-tier or
   batch-backend adoption decision.
2. **Accuracy against this project's own acoustic test suite.** Not run
   here — the paper's benchmark (94.07%/91.80%) is against speech/singing
   eval sets, not played-instrument audio through this project's actual
   mic/room signal chain. `scripts/acoustic_pipeline_test.py`'s
   `chromatic`/`noise` suites would be the right harness once a Pi
   (or even just desktop-latency) number justifies spending the time.
3. Neither of the above is recommended to start without the other two
   survey items (recommendations 1-3, already independently ticketed as
   issues #78/#79/#81) landing first — this task's own scope was source
   verification only, per issue #80.

## Sources

- `swift-f0==0.1.2`'s installed package: `site-packages/swift_f0/core.py`,
  `site-packages/swift_f0/__init__.py`, `site-packages/swift_f0/model.onnx`
  (read directly, not via PyPI metadata/README).
- `pip install swift-f0` dependency resolution output (this session, shows
  `onnxruntime` pulled in automatically).
- `docs/research/detection-systems-survey.md` (this project's own prior
  synthesis, cross-checked against the above).
