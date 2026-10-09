"""Microphone capture: 16 kHz mono int16 frames pushed onto an asyncio queue from the PortAudio callback thread."""

from __future__ import annotations

import asyncio
from collections import deque

import numpy as np
import sounddevice as sd
from loguru import logger


class MicInput:
    def __init__(self, loop: asyncio.AbstractEventLoop, sample_rate: int = 16000, frame_ms: int = 80, device: int | str | None = None, preroll_ms: int = 400) -> None:
        self.loop = loop
        self.sr = sample_rate
        self.frame = int(sample_rate * frame_ms / 1000)
        self.device = device
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // frame_ms))
        self._stream: sd.InputStream | None = None
        self.muted = False

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sr,
            channels=1,
            dtype="int16",
            blocksize=self.frame,
            device=self.device,
            callback=self._cb,
            latency="low",
        )
        self._stream.start()
        info = sd.query_devices(self._stream.device, "input")
        logger.info("mic: {} @ {} Hz, {} ms frames", info["name"], self.sr, self.frame * 1000 // self.sr)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def preroll(self) -> np.ndarray:
        """Recent audio preceding the current moment (so the first syllable after the wake word isn't lost)."""
        return np.concatenate(list(self._preroll)) if self._preroll else np.zeros(0, dtype=np.int16)

    def _cb(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.debug("mic status: {}", status)
        frame = indata[:, 0].copy()
        self._preroll.append(frame)
        if self.muted:
            return

        def put() -> None:
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self.queue.put_nowait(frame)

        try:
            self.loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass  # loop closed


def rms_level(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    f = frame.astype(np.float32) / 32768.0
    return float(min(1.0, (np.sqrt(np.mean(f * f)) * 6.0) ** 0.7))
