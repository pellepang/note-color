"""Audio capture: sounddevice InputStream feeding a bounded, drop-oldest
queue so the real-time audio callback never blocks. Source is either the
microphone (default) or, via `resolve_loopback_device`, the system's
audio-output monitor."""

import os
import queue
import subprocess

import sounddevice as sd


def resolve_loopback_device():
    """Find the current default output's PipeWire/PulseAudio monitor
    source, so AudioCapture can listen to what the computer is playing
    instead of the microphone -- e.g. for testing without playing audio
    out loud through a speaker/mic round-trip. Sets PULSE_SOURCE (read by
    PortAudio's 'pulse' host device at stream-open time) and returns the
    device name to pass to AudioCapture(device=...).

    Linux + PipeWire/PulseAudio only -- there's no equivalent PortAudio
    loopback device on macOS/Windows without installing a virtual audio
    driver (e.g. BlackHole), which is out of scope here.

    Confirmed on a PipeWire system that the monitor keeps producing signal
    even while the sink is muted -- muting is applied on the path to the
    physical output, downstream of where the monitor taps -- but that is a
    property of the audio server/hardware, not guaranteed by this code, so
    it's worth a quick check on other machines.
    """
    try:
        sink = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "Couldn't determine the system audio monitor via `pactl` -- "
            "--source loopback needs PipeWire or PulseAudio (Linux only)."
        ) from exc
    os.environ["PULSE_SOURCE"] = f"{sink}.monitor"
    return "pulse"


class AudioCapture:
    def __init__(self, sample_rate, block_size, queue_size=8, device=None):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device = device
        self.q = queue.Queue(maxsize=queue_size)
        self.stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] status: {status}")
        block = indata[:, 0].copy()
        try:
            self.q.put_nowait(block)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(block)
            except queue.Full:
                pass

    def start(self):
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            channels=1,
            dtype="float32",
            latency="low",
            device=self.device,
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()

    def restart(self, device):
        """Swap the input device (e.g. mic <-> loopback monitor) without
        touching self.q, so a thread already calling get_block() on this
        same AudioCapture instance is unaffected -- it just sees a gap of a
        few blocks while the old stream tears down and the new one spins
        up."""
        self.stop()
        self.device = device
        self.start()

    def get_block(self, timeout=None):
        return self.q.get(timeout=timeout)
