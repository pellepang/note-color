"""Microphone capture: sounddevice InputStream feeding a bounded,
drop-oldest queue so the real-time audio callback never blocks."""

import queue

import sounddevice as sd


class AudioCapture:
    def __init__(self, sample_rate, block_size, queue_size=8):
        self.sample_rate = sample_rate
        self.block_size = block_size
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
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()

    def get_block(self, timeout=None):
        return self.q.get(timeout=timeout)
