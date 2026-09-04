"""Tests for wav_io.py (map #99, ticket #116): the hand-rolled RIFF/WAV
reader, the `smpl`-chunk loop points, and sample import.

Every WAV here is synthesized in the test (via `wav_io.write_wav()` or
the stdlib `wave` module) -- no binary fixtures, the same convention
`tests/test_chroma.py`'s `make_tone()` set. Every filesystem test uses
`tmp_path`; the real `~/.config/note-color/samples/` is never touched.
"""

import os
import struct
import wave

import numpy as np
import pytest

import patch_format
import wav_io
from wav_io import (
    Sample, SampleImportError, import_sample, imported_samples, parse_fmt, parse_smpl_loop,
    read_wav, read_wav_bytes, riff_chunks, write_wav,
)


@pytest.fixture
def samples_dir(tmp_path, monkeypatch):
    """Point `patch_format.samples_dir()` at a throwaway directory so
    import_sample()'s default-directory path never reaches the real
    config directory."""
    directory = tmp_path / "samples"
    monkeypatch.setattr(patch_format, "samples_dir", lambda: str(directory))
    return directory


def sine(freq=440.0, sample_rate=44100, seconds=0.1, amplitude=0.5):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# --- write_wav / read_wav round trip ---------------------------------------

def test_write_then_read_round_trips_samples_and_rate(tmp_path):
    data = sine()
    path = write_wav(str(tmp_path / "a.wav"), data, 44100)
    sample = read_wav(path)
    assert isinstance(sample, Sample)
    assert sample.sample_rate == 44100
    assert sample.frames == data.shape[0]
    assert sample.data.dtype == np.float32
    assert np.max(np.abs(sample.data - data)) < 1.0 / 32767 * 1.5  # 16-bit quantization
    assert sample.name == "a.wav"
    assert not sample.loops and sample.loop_start is None


def test_read_wav_of_a_missing_or_non_wav_file_is_none_not_an_exception(tmp_path):
    assert read_wav(str(tmp_path / "nope.wav")) is None
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"ID3\x03\x00\x00\x00 this is not a wav at all")
    assert read_wav(str(junk)) is None
    assert read_wav_bytes(b"") is None
    assert read_wav_bytes(b"RIFF\x00\x00\x00\x00WAVE") is None  # header only


def test_truncated_file_yields_whatever_frames_are_there(tmp_path):
    """A partially-written WAV -- even one cut mid-frame, at an odd byte
    -- decodes as far as it goes rather than raising."""
    path = write_wav(str(tmp_path / "a.wav"), sine(), 44100)
    raw = open(path, "rb").read()
    partial = read_wav_bytes(raw[:1001])
    assert partial is not None
    assert 0 < partial.frames < 1001
    header_only = read_wav_bytes(raw[:44])
    assert header_only is not None and header_only.frames == 0


# --- format coverage ---------------------------------------------------------

def test_reads_files_written_by_the_stdlib_wave_module_including_stereo(tmp_path):
    """Cross-check against the stdlib writer: a 16-bit stereo 22050Hz file
    is averaged down to mono at its own rate."""
    path = str(tmp_path / "stereo.wav")
    left = np.full(200, 0.5)
    right = np.full(200, -0.25)
    interleaved = np.empty(400, dtype="<i2")
    interleaved[0::2] = (left * 32767).astype("<i2")
    interleaved[1::2] = (right * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(interleaved.tobytes())
    sample = read_wav(path)
    assert sample.sample_rate == 22050
    assert sample.frames == 200
    assert sample.data == pytest.approx(np.full(200, 0.125), abs=1e-4)


def wav_bytes(fmt_payload, data_payload, extra_chunks=()):
    body = b"WAVE" + wav_io._chunk(b"fmt ", fmt_payload)
    for chunk_id, payload in extra_chunks:
        body += wav_io._chunk(chunk_id, payload)
    body += wav_io._chunk(b"data", data_payload)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def pcm_fmt(channels, rate, bits, audio_format=wav_io.WAVE_FORMAT_PCM):
    block = channels * bits // 8
    return struct.pack("<HHIIHH", audio_format, channels, rate, rate * block, block, bits)


def test_reads_ieee_float_32_which_stdlib_wave_refuses():
    data = sine().astype("<f4")
    raw = wav_bytes(pcm_fmt(1, 48000, 32, wav_io.WAVE_FORMAT_IEEE_FLOAT), data.tobytes())
    with pytest.raises(wave.Error):
        import io
        wave.open(io.BytesIO(raw))
    sample = read_wav_bytes(raw)
    assert sample.sample_rate == 48000
    assert sample.data == pytest.approx(data, abs=1e-7)


def test_reads_8_bit_unsigned_and_24_bit_and_32_bit_pcm():
    values = np.array([-1.0, -0.5, 0.0, 0.5, 0.999], dtype=np.float64)
    eight = (values * 128 + 128).clip(0, 255).astype(np.uint8).tobytes()
    assert read_wav_bytes(wav_bytes(pcm_fmt(1, 8000, 8), eight)).data == pytest.approx(values, abs=1 / 128)
    ints24 = (values * 8388607).astype(np.int32)
    packed = b"".join(int(v).to_bytes(3, "little", signed=True) for v in ints24)
    assert read_wav_bytes(wav_bytes(pcm_fmt(1, 8000, 24), packed)).data == pytest.approx(values, abs=1e-6)
    ints32 = (values * 2147483647).astype("<i4").tobytes()
    assert read_wav_bytes(wav_bytes(pcm_fmt(1, 8000, 32), ints32)).data == pytest.approx(values, abs=1e-7)


def test_wave_format_extensible_resolves_its_subformat():
    data = sine().astype("<f4")
    base = pcm_fmt(1, 44100, 32, wav_io.WAVE_FORMAT_EXTENSIBLE)
    # cbSize, validBitsPerSample, channelMask, then the 16-byte sub-format
    # GUID whose first two bytes carry the real format code.
    ext = base + struct.pack("<HHI", 22, 32, 0) + struct.pack("<H", wav_io.WAVE_FORMAT_IEEE_FLOAT) + b"\x00" * 14
    assert parse_fmt(ext)[0] == wav_io.WAVE_FORMAT_IEEE_FLOAT
    sample = read_wav_bytes(wav_bytes(ext, data.tobytes()))
    assert sample is not None and sample.data == pytest.approx(data, abs=1e-7)


def test_unsupported_encodings_are_none_not_exceptions():
    assert read_wav_bytes(wav_bytes(pcm_fmt(1, 44100, 16, audio_format=0x0055), b"\x00" * 100)) is None  # MP3-in-WAV
    assert read_wav_bytes(wav_bytes(pcm_fmt(1, 44100, 12), b"\x00" * 100)) is None  # odd bit depth
    assert read_wav_bytes(wav_bytes(pcm_fmt(0, 44100, 16), b"\x00" * 100)) is None  # zero channels
    assert read_wav_bytes(wav_bytes(pcm_fmt(1, 0, 16), b"\x00" * 100)) is None  # zero rate
    assert parse_fmt(b"\x00" * 10) is None


def test_riff_chunks_walks_word_aligned_chunks_in_file_order():
    raw = wav_bytes(pcm_fmt(1, 8000, 16), b"\x01\x02", extra_chunks=[(b"LIST", b"odd")])
    ids = [chunk_id for chunk_id, _ in riff_chunks(raw)]
    assert ids == [b"fmt ", b"LIST", b"data"]
    assert dict(riff_chunks(raw))[b"LIST"] == b"odd"  # 3-byte payload, padded to 4 on disk
    assert riff_chunks(b"RIFX" + raw[4:]) == []


# --- loop points -------------------------------------------------------------

def test_smpl_loop_round_trips_with_exclusive_end(tmp_path):
    path = write_wav(str(tmp_path / "loop.wav"), sine(seconds=0.2), 44100, loop=(1000, 3000))
    sample = read_wav(path)
    assert sample.loops
    assert (sample.loop_start, sample.loop_end) == (1000, 3000)
    assert sample.data[sample.loop_start:sample.loop_end].shape[0] == 2000


def test_smpl_chunk_edge_cases():
    header = struct.pack("<IIIIIIIII", 0, 0, 22675, 60, 0, 0, 0, 1, 0)
    assert parse_smpl_loop(header[:20]) is None  # too short
    assert parse_smpl_loop(header[:28] + struct.pack("<II", 0, 0)) is None  # zero loops
    assert parse_smpl_loop(header + struct.pack("<IIIIII", 0, 0, 500, 400, 0, 0)) is None  # end before start
    first_of_two = struct.pack("<IIIIIIIII", 0, 0, 22675, 60, 0, 0, 0, 2, 0)
    first_of_two += struct.pack("<IIIIII", 0, 0, 10, 19, 0, 0) + struct.pack("<IIIIII", 1, 0, 30, 39, 0, 0)
    assert parse_smpl_loop(first_of_two) == (10, 20)


def test_loop_past_the_end_of_the_data_is_dropped_not_read_out_of_bounds():
    header = struct.pack("<IIIIIIIII", 0, 0, 22675, 60, 0, 0, 0, 1, 0)
    smpl = header + struct.pack("<IIIIII", 0, 0, 10, 5000, 0, 0)
    raw = wav_bytes(pcm_fmt(1, 8000, 16), b"\x00" * 200, extra_chunks=[(b"smpl", smpl)])
    sample = read_wav_bytes(raw)
    assert sample.frames == 100
    assert not sample.loops and sample.loop_end is None


def test_sample_loops_property_needs_a_real_span():
    data = np.zeros(10, dtype=np.float32)
    assert not Sample(data, 8000, loop_start=4, loop_end=5).loops  # one frame: nothing to interpolate across
    assert Sample(data, 8000, loop_start=4, loop_end=6).loops
    assert not Sample(data, 8000, loop_start=-1, loop_end=6).loops
    assert Sample(data, 8000).duration_seconds == pytest.approx(10 / 8000)


# --- import (decision #106: copy in, reference by bare name) -----------------

def test_import_copies_into_the_samples_dir_and_returns_a_bare_name(tmp_path, samples_dir):
    source = write_wav(str(tmp_path / "downloads" / "kick_01.wav"), sine(), 44100)
    name = import_sample(source)
    assert name == "kick_01.wav"
    assert os.sep not in name
    assert os.path.isfile(samples_dir / "kick_01.wav")
    assert open(source, "rb").read() == open(samples_dir / "kick_01.wav", "rb").read()
    assert os.path.isfile(source)  # copied, not moved
    assert imported_samples() == ["kick_01.wav"]
    assert patch_format.zone_available(patch_format.Zone(sample=name))


def test_import_of_the_same_bytes_reuses_the_name_but_a_different_file_never_clobbers(tmp_path, samples_dir):
    first = write_wav(str(tmp_path / "a" / "snare.wav"), sine(440), 44100)
    second = write_wav(str(tmp_path / "b" / "snare.wav"), sine(880), 44100)
    assert import_sample(first) == "snare.wav"
    assert import_sample(first) == "snare.wav"  # identical bytes: idempotent
    assert import_sample(second) == "snare_1.wav"  # different bytes: new name, old file intact
    assert import_sample(second) == "snare_1.wav"
    assert sorted(imported_samples()) == ["snare.wav", "snare_1.wav"]
    assert read_wav(str(samples_dir / "snare.wav")).data == pytest.approx(read_wav(first).data)


def test_import_honours_an_explicit_name_and_forces_the_wav_extension(tmp_path, samples_dir):
    source = write_wav(str(tmp_path / "x.wav"), sine(), 44100)
    assert import_sample(source, name="/etc/../nested/hat.aiff") == "hat.wav"
    assert import_sample(source, name="closed hat") == "closed hat.wav"


def test_import_rejects_missing_and_undecodable_files_with_a_message(tmp_path, samples_dir):
    with pytest.raises(SampleImportError):
        import_sample(str(tmp_path / "absent.wav"))
    bad = tmp_path / "song.wav"
    bad.write_bytes(b"not a wav")
    with pytest.raises(SampleImportError, match="not a WAV"):
        import_sample(str(bad))
    assert imported_samples() == []  # nothing was copied


def test_imported_samples_lists_only_wavs_flat_and_sorted(tmp_path):
    directory = tmp_path / "s"
    directory.mkdir()
    (directory / "b.wav").write_bytes(b"")
    (directory / "a.WAV").write_bytes(b"")
    (directory / "notes.txt").write_bytes(b"")
    (directory / "sub").mkdir()
    (directory / "sub" / "c.wav").write_bytes(b"")
    assert imported_samples(str(directory)) == ["a.WAV", "b.wav"]
    assert imported_samples(str(tmp_path / "missing")) == []
