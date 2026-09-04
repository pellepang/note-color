"""WAV reading and sample import for the sampler engine (map #99, build
ticket #116).

Two jobs, both of them the *file* side of the sampler (the audio side
lives in `sampler.py`):

1. **Read a WAV into a mono float32 NumPy array** -- with its own sample
   rate and its loop points -- adding no dependency. This is a small
   hand-rolled RIFF chunk walk rather than the stdlib `wave` module,
   for two concrete reasons, not for the fun of it: `wave` refuses
   IEEE-float WAVs outright (`Error: unknown format: 3`), which modern
   editors emit by default, and it exposes no way at all to reach the
   `smpl` chunk where a sample's loop points live. Both are load-bearing
   for a sampler. `soundfile` is present in this environment only as a
   transitive `librosa` dependency (the `[batch]` extra) and the sampler
   must work on a base install, so it is deliberately not used -- same
   reasoning that keeps `librosa`/`music21` out of the live path.

2. **Import a sample by copying it** into `~/.config/note-color/samples/`
   (decision #106), returning the bare name a zone then references. A
   patch containing `/home/pelle/...` is not shareable; audio samples are
   small enough that duplication is not a real cost.

**Loop points come from the WAV file, not from the patch** -- the `smpl`
chunk's first loop, the standard place every sampler and sample library
already puts them. Decision #106's zone schema has no loop fields and
adding some would put one recording's internal structure into the
mapping document that references it: move the sample to another kit and
you would have to re-enter its loop by hand. See docs/DECISIONS.md.

Every reader here degrades rather than raising *on playback paths*: an
unreadable or unsupported file yields `None` from `read_wav()`, so a
zone whose sample is broken behaves exactly like a zone whose sample is
missing (silent, unavailable, kit still loads). `import_sample()` is the
one exception and raises `SampleImportError`, because import is a
deliberate user action at which silent failure is worse than a message.
"""

from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

import patch_format

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

#: File extensions `import_sample()` and `imported_samples()` recognise.
#: WAV only in v1 -- reading anything else without a dependency means
#: hand-rolling a decoder, which is a different ticket.
SAMPLE_EXTENSIONS = (".wav",)


class SampleImportError(Exception):
    """Raised by `import_sample()` only. Playback-path readers return
    `None` instead; import is the one place a user is standing there
    waiting to be told what went wrong."""


@dataclass
class Sample:
    """One decoded recording: mono float32 in the range [-1, 1], at its
    **own** sample rate, plus optional loop points in its own frames.

    Deliberately *not* resampled to `config.PLAYBACK_SAMPLE_RATE` on
    load. `sampler.SamplerVoice` already reads the sample at a
    fractional rate to pitch-shift it, so a sample-rate mismatch folds
    into that one ratio for free and exactly (`sample_rate /
    engine_rate`); resampling on load would be a second, lossier
    interpolation pass on top of the one that has to happen anyway, and
    would make loop points land between frames. See docs/DECISIONS.md."""

    data: np.ndarray
    sample_rate: int
    loop_start: Optional[int] = None
    loop_end: Optional[int] = None
    name: str = ""

    @property
    def frames(self):
        return int(self.data.shape[0])

    @property
    def duration_seconds(self):
        return self.frames / float(self.sample_rate or 1)

    @property
    def loops(self):
        """True when this sample carries a usable loop (`smpl` chunk).
        A loop needs at least two frames to interpolate across."""
        return (
            self.loop_start is not None
            and self.loop_end is not None
            and 0 <= self.loop_start < self.loop_end <= self.frames
            and self.loop_end - self.loop_start >= 2
        )


# --- RIFF parsing ---------------------------------------------------------

def riff_chunks(raw):
    """Every top-level chunk of a RIFF/WAVE byte string, as a list of
    `(chunk_id, payload)` in file order. Tolerant on purpose: a truncated
    final chunk yields whatever bytes are actually there rather than
    raising, so a partially-written file still plays as far as it goes.
    Returns `[]` for anything that isn't RIFF/WAVE at all.

    Pure (bytes in, list out) and therefore directly unit-testable
    against a synthesized WAV, per this repo's convention."""
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return []
    chunks = []
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset:offset + 4]
        (size,) = struct.unpack_from("<I", raw, offset + 4)
        start = offset + 8
        payload = raw[start:start + size]
        chunks.append((chunk_id, payload))
        offset = start + size + (size & 1)  # chunks are word-aligned
    return chunks


def parse_fmt(payload):
    """`fmt ` chunk -> (audio_format, channels, sample_rate,
    bits_per_sample), or None if it is too short to be a WAV format
    chunk. `WAVE_FORMAT_EXTENSIBLE` is resolved to whatever real format
    its sub-format GUID names, since that is how many 24-bit and float
    files declare themselves."""
    if len(payload) < 16:
        return None
    audio_format, channels, sample_rate, _byte_rate, _align, bits = struct.unpack_from(
        "<HHIIHH", payload, 0
    )
    if audio_format == WAVE_FORMAT_EXTENSIBLE and len(payload) >= 26:
        (audio_format,) = struct.unpack_from("<H", payload, 24)
    return audio_format, channels, sample_rate, bits


def parse_smpl_loop(payload):
    """The **first** loop of a `smpl` chunk as `(start, end)` frames, or
    `None`. Only the first: multi-loop samples exist but a sampler voice
    can only be in one loop at a time, and picking any but the first
    would be a guess.

    The chunk's `end` is the last frame *inside* the loop (inclusive),
    which is turned into a Python-style exclusive end here so every
    consumer downstream can slice with it directly."""
    if len(payload) < 36:
        return None
    (loop_count,) = struct.unpack_from("<I", payload, 28)
    if loop_count < 1 or len(payload) < 36 + 24:
        return None
    start, end = struct.unpack_from("<II", payload, 36 + 8)
    if end <= start:
        return None
    return int(start), int(end) + 1


def _decode_samples(payload, audio_format, bits, channels):
    """Raw `data` chunk bytes -> a mono float32 array in [-1, 1].

    Stereo (and any higher channel count) is **averaged down to mono**,
    not left-channel-only: the whole engine is mono today (map #99 lists
    stereo as unspecified), and dropping a channel would silently lose
    whatever was panned into it."""
    if channels < 1:
        return None
    bytes_per_sample = bits // 8
    if bytes_per_sample < 1:
        return None
    # A truncated file (or one whose `data` chunk header lies) can leave a
    # payload that isn't a whole number of frames; `np.frombuffer` raises
    # on that, so trim to whole frames first -- a partially-written
    # sample plays as far as it goes rather than failing to load.
    frame_bytes = bytes_per_sample * channels
    payload = payload[:len(payload) - (len(payload) % frame_bytes)]
    if audio_format == WAVE_FORMAT_IEEE_FLOAT:
        if bits == 32:
            data = np.frombuffer(payload, dtype="<f4").astype(np.float32)
        elif bits == 64:
            data = np.frombuffer(payload, dtype="<f8").astype(np.float32)
        else:
            return None
    elif audio_format == WAVE_FORMAT_PCM:
        if bits == 8:
            # 8-bit WAV is *unsigned*, centred on 128 -- the one PCM
            # width that isn't two's complement.
            raw = np.frombuffer(payload, dtype=np.uint8).astype(np.float32)
            data = (raw - 128.0) / 128.0
        elif bits == 16:
            data = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
        elif bits == 24:
            usable = len(payload) - (len(payload) % 3)
            triples = np.frombuffer(payload[:usable], dtype=np.uint8).reshape(-1, 3)
            packed = (
                triples[:, 0].astype(np.int32)
                | (triples[:, 1].astype(np.int32) << 8)
                | (triples[:, 2].astype(np.int32) << 16)
            )
            packed = np.where(packed >= 1 << 23, packed - (1 << 24), packed)
            data = packed.astype(np.float32) / 8388608.0
        elif bits == 32:
            data = np.frombuffer(payload, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            return None
    else:
        return None
    if channels > 1:
        usable = data.shape[0] - (data.shape[0] % channels)
        if usable <= 0:
            return np.zeros(0, dtype=np.float32)
        data = data[:usable].reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def read_wav_bytes(raw, name=""):
    """Decode a whole WAV file's bytes into a `Sample`, or `None` if it
    isn't a WAV this reader understands. Never raises -- see the module
    docstring."""
    chunks = riff_chunks(raw)
    if not chunks:
        return None
    fmt = data_payload = None
    loop = None
    for chunk_id, payload in chunks:
        if chunk_id == b"fmt " and fmt is None:
            fmt = parse_fmt(payload)
        elif chunk_id == b"data" and data_payload is None:
            data_payload = payload
        elif chunk_id == b"smpl" and loop is None:
            loop = parse_smpl_loop(payload)
    if fmt is None or data_payload is None:
        return None
    audio_format, channels, sample_rate, bits = fmt
    try:
        data = _decode_samples(data_payload, audio_format, bits, channels)
    except (ValueError, struct.error):
        return None
    if data is None or sample_rate <= 0:
        return None
    loop_start, loop_end = loop if loop else (None, None)
    if loop_end is not None and loop_end > data.shape[0]:
        # A loop pointing past the end of a truncated file: drop it
        # rather than reading out of bounds at render time.
        loop_start = loop_end = None
    return Sample(
        data=data,
        sample_rate=int(sample_rate),
        loop_start=loop_start,
        loop_end=loop_end,
        name=name or "",
    )


def read_wav(path):
    """Read a WAV file into a `Sample`, or `None` if it is missing,
    unreadable or not a supported WAV. A `None` here is what makes a
    broken sample behave exactly like a missing one: silent zone,
    unavailable, kit still loads (decision #106)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    return read_wav_bytes(raw, name=os.path.basename(path))


# --- Writing (tests and, later, any export path) --------------------------

def write_wav(path, data, sample_rate, loop=None):
    """Write a mono float32 array as a 16-bit PCM WAV, optionally with a
    `smpl` loop chunk (`loop=(start, end)`, end exclusive, matching
    `parse_smpl_loop()`'s convention).

    Exists so tests can *synthesize* their fixtures rather than commit
    binary WAVs -- this repo's established convention (see
    `tests/test_chroma.py`'s `make_tone()`), and the reason no `.wav`
    file is checked in anywhere in this change."""
    samples = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2").tobytes()
    fmt = struct.pack("<HHIIHH", WAVE_FORMAT_PCM, 1, int(sample_rate),
                      int(sample_rate) * 2, 2, 16)
    body = b"WAVE" + _chunk(b"fmt ", fmt)
    if loop is not None:
        start, end = loop
        smpl = struct.pack(
            "<IIIIIIIII", 0, 0, int(1e9 // max(sample_rate, 1)), 60, 0, 0, 0, 1, 0
        ) + struct.pack("<IIIIII", 0, 0, int(start), int(end) - 1, 0, 0)
        body += _chunk(b"smpl", smpl)
    body += _chunk(b"data", pcm)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _chunk(chunk_id, payload):
    padding = b"\x00" if len(payload) & 1 else b""
    return chunk_id + struct.pack("<I", len(payload)) + payload + padding


# --- Sample import (decision #106) ----------------------------------------

def import_sample(source_path, directory=None, name=None):
    """Copy a WAV into the samples directory and return the **bare name**
    a zone should reference.

    Copying (rather than referencing in place) is decision #106's rule:
    a patch pointing at one machine's filesystem is not shareable, and a
    sample that lives outside the samples directory silently stops
    working the moment the user tidies their downloads folder.

    Collisions are resolved by content, not by clobbering: importing the
    exact same bytes under an existing name reuses that name, while a
    *different* file wanting an already-taken name gets `name_1.wav`,
    `name_2.wav`, ... . Overwriting would break every other patch already
    referencing that name -- the one failure mode a shared-by-bare-name
    scheme has to defend against.

    Raises `SampleImportError` if the file is missing, unreadable, or not
    a WAV this reader can decode -- import is a deliberate user action
    (see the module docstring)."""
    if not source_path or not os.path.isfile(source_path):
        raise SampleImportError(f"no such file: {source_path}")
    try:
        with open(source_path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise SampleImportError(f"could not read {source_path}: {exc}") from exc
    if read_wav_bytes(raw) is None:
        raise SampleImportError(
            f"{os.path.basename(source_path)} is not a WAV file this build can read "
            f"(supported: PCM 8/16/24/32-bit and IEEE float)"
        )
    directory = directory or patch_format.samples_dir()
    os.makedirs(directory, exist_ok=True)
    target_name = os.path.basename(name or source_path)
    stem, extension = os.path.splitext(target_name)
    if extension.lower() not in SAMPLE_EXTENSIONS:
        extension = ".wav"
    stem = stem or "sample"
    candidate = stem + extension
    index = 0
    while True:
        destination = os.path.join(directory, candidate)
        if not os.path.exists(destination):
            break
        if _same_bytes(destination, raw):
            return candidate  # already imported; reuse rather than duplicate
        index += 1
        candidate = f"{stem}_{index}{extension}"
    shutil.copyfile(source_path, destination)
    return candidate


def _same_bytes(path, raw):
    try:
        with open(path, "rb") as f:
            return f.read() == raw
    except OSError:
        return False


def imported_samples(directory=None):
    """Bare names of every importable sample already in the samples
    directory, sorted -- what a sample-picker UI lists. Flat and
    non-recursive, mirroring `patch_format.patch_paths()` and
    `score_editor_picker.score_file_paths()`."""
    directory = directory or patch_format.samples_dir()
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    return sorted(
        entry for entry in entries
        if os.path.splitext(entry)[1].lower() in SAMPLE_EXTENSIONS
        and os.path.isfile(os.path.join(directory, entry))
    )
