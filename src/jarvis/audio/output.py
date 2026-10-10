"""Speaker playback with a cancelable queue, an RMS level meter and a 24-band spectrum (both drive the HUD)."""

from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd
from loguru import logger

from jarvis.audio.spectrum import BandAnalyzer

LevelCB = Callable[[float], None]
SpectrumCB = Callable[[np.ndarray], None]
SPECTRUM_FRAME = 1024  # ~43 ms at 24 kHz → 23 Hz bins; analysed every 2nd 20 ms block (25 Hz)


class AudioPlayer:
    """Feed float32 mono chunks with `enqueue()`; they play back-to-back on a dedicated thread.

    `cancel()` drops everything queued and fades the current buffer out within ~20 ms (barge-in).
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        device: int | str | None = None,
        on_level: LevelCB | None = None,
        level_hz: int = 40,
        on_spectrum: SpectrumCB | None = None,
    ) -> None:
        self.sr = sample_rate
        self.device = device
        self.on_level = on_level
        self.on_spectrum = on_spectrum
        self._analyzer = BandAnalyzer(sample_rate, SPECTRUM_FRAME) if on_spectrum else None
        self._ring = np.zeros(SPECTRUM_FRAME, dtype=np.float32)  # last ~43 ms of what was written to the device
        self._level_interval = 1.0 / level_hz
        self._q: queue.Queue[np.ndarray | None] = queue.Queue()
        self._cancel = threading.Event()
        self._playing = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._gen = 0  # generation counter: chunks from a cancelled generation are dropped
        self._thread = threading.Thread(target=self._run, name="audio-out", daemon=True)
        self._thread.start()

    # ---- public --------------------------------------------------------------------------------
    @property
    def is_playing(self) -> bool:
        return self._playing.is_set()

    def enqueue(self, audio: np.ndarray, gen: int | None = None) -> None:
        if gen is not None and gen != self._gen:
            return
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        self._idle.clear()
        self._q.put((self._gen, audio))  # type: ignore[arg-type]

    def generation(self) -> int:
        return self._gen

    def cancel(self) -> None:
        """Stop immediately and drop queued audio."""
        self._gen += 1
        self._cancel.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    async def wait_idle(self, poll: float = 0.02) -> None:
        while not self._idle.is_set():
            await asyncio.sleep(poll)

    def wait_idle_blocking(self, timeout: float | None = None) -> None:
        self._idle.wait(timeout)

    # ---- worker --------------------------------------------------------------------------------
    def _run(self) -> None:
        block = int(self.sr * 0.02)  # 20 ms
        stream: sd.OutputStream | None = None
        last_level = 0.0
        try:
            stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", device=self.device, blocksize=block, latency="low")
            stream.start()
        except Exception as e:  # noqa: BLE001
            logger.error("audio output unavailable: {}", e)
            return
        n_block = 0
        while True:
            try:
                item = self._q.get(timeout=0.05)
            except queue.Empty:
                if self._playing.is_set():
                    self._playing.clear()
                    self._emit_level(0.0)
                    self._emit_spectrum(silence=True)
                self._idle.set()
                self._cancel.clear()
                continue
            gen, audio = item  # type: ignore[misc]
            if gen != self._gen:
                continue
            self._playing.set()
            self._idle.clear()
            try:
                for i in range(0, len(audio), block):
                    if self._cancel.is_set():
                        break
                    chunk = audio[i : i + block]
                    if len(chunk) < block:
                        chunk = np.pad(chunk, (0, block - len(chunk)))
                    stream.write(chunk)
                    now = time.perf_counter()
                    if now - last_level >= self._level_interval:
                        last_level = now
                        self._emit_level(float(np.sqrt(np.mean(chunk**2))))
                    if self._analyzer is not None:
                        self._ring = np.concatenate((self._ring[block:], chunk))
                        n_block += 1
                        if n_block % 2 == 0:
                            self._emit_spectrum()
                if self._cancel.is_set():
                    # tiny fade to avoid a click
                    stream.write(np.zeros(block, dtype=np.float32))
                    self._emit_level(0.0)
                    self._emit_spectrum(silence=True)
            except Exception as e:  # noqa: BLE001  (stream closed during interpreter shutdown, device unplugged…)
                logger.warning("audio output stopped: {}", e)
                self._playing.clear()
                self._idle.set()
                return

    def _emit_level(self, rms: float) -> None:
        if self.on_level:
            # Map RMS (~0..0.3 for speech) onto 0..1 with a soft knee.
            lvl = min(1.0, (rms * 4.0) ** 0.7)
            try:
                self.on_level(lvl)
            except Exception:  # noqa: BLE001
                pass

    def _emit_spectrum(self, silence: bool = False) -> None:
        if self.on_spectrum is None or self._analyzer is None:
            return
        if silence:
            self._ring[:] = 0.0
        with contextlib.suppress(Exception):  # a HUD-side failure must never stop playback
            self.on_spectrum(np.zeros(self._analyzer.n_bands, np.float32) if silence else self._analyzer.bands(self._ring))
