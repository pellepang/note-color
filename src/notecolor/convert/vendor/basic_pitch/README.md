# Vendored: `nmp.onnx` from Spotify's Basic Pitch

## What this is

The trained neural-network graph behind
[spotify/basic-pitch](https://github.com/spotify/basic-pitch), the
instrument-agnostic polyphonic note detector map
[#123](https://github.com/pellepang/note-color/issues/123) uses for
per-stem transcription.

| | |
|---|---|
| File | `nmp.onnx`, 230,444 bytes |
| SHA-256 | `2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec` |
| Source | `spotify/basic-pitch`, `basic_pitch/saved_models/icassp_2022/nmp.onnx` |
| Licence | **Apache-2.0**, code *and* weights (verified against the repo's own `LICENSE` and the GitHub API's `spdxId`) |
| Accuracy | 0.709 note F1 on MAESTRO; 16,782 parameters |

`LICENSE` and `NOTICE` are Basic Pitch's own, vendored beside the model as
Apache-2.0 §4 requires.

## Why it is committed rather than downloaded

Two decisions meet here.

Issue [#125](https://github.com/pellepang/note-color/issues/125) found
that **`pip install basic-pitch` does not work on Python 3.14**: version
0.4.0 (2024-08-16, no release since) declares `python_requires` 3.8–3.11,
and its platform-conditional dependencies route Linux to
`tflite-runtime` only for `python_version < "3.11"`. The weights were
never the problem — this is 230 KB of Apache-2.0 data that
`onnxruntime` 1.29 loads without complaint. So the route to using it at
all is to vendor the graph and drive it directly.

Issue [#139](https://github.com/pellepang/note-color/issues/139) then set
the rule that makes that allowed: permissively licensed weights **may be
committed to this repo under a 1 MB ceiling**, and anything larger is
downloaded at runtime. At 225 KB this sits comfortably under it, against
a git pack that was 1.62 MiB when the rule was written. The ceiling
exists because a binary committed once cannot be removed from history
without rewriting it.

## What is *not* vendored

Basic Pitch's Python pre- and post-processing. `transcribe_backends.py`
implements the input windowing and output unwrapping itself, against the
model's real signature rather than a description of it:

```
in   serving_default_input_2:0   (batch, 43844, 1)   float32
out  StatefulPartitionedCall:1   (batch, 172,  88)   note posteriorgram
out  StatefulPartitionedCall:2   (batch, 172,  88)   onset posteriorgram
out  StatefulPartitionedCall:0   (batch, 172, 264)   contour posteriorgram
```

43,844 samples is 2 seconds at 22,050 Hz less one 256-sample FFT hop;
172 frames is 86 fps × 2 s; 88 bins is one per MIDI note from A0; 264 is
three contour bins per semitone.

## Updating it

Re-download from the pinned path above and check the SHA-256 recorded
here. If Spotify retrains the model the hash changes, and that should be
a deliberate, reviewed commit — a silently swapped model would invalidate
every accuracy number this project has measured against it.
