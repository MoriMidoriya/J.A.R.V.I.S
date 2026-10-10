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


OPEN_RETRY_LOG_S = 30.0  # while the device is missing, complain at most this often (not once per sentence)


class AudioPlayer:
    """Feed float32 mono chunks with `enqueue()`; they play back-to-back on a dedicated thread.

    `cancel()` drops everything queued and fades the current buffer out within ~20 ms (barge-in).

    The output device is opened lazily and re-opened after any failure (headphones unplugged, driver gone — a real
    `MME error 6` cost a whole session once): a failed utterance is dropped and reported as finished so the voice
    pipeline moves on, and the next utterance simply tries the device again.

    Idle accounting is exact: `_pending` counts enqueued-but-unfinished chunks under a lock, so `wait_idle()` cannot
    return in the gap between the worker seeing an empty queue and a chunk landing in it.
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
        self._q: queue.Queue[tuple[int, np.ndarray]] = queue.Queue()
        self._lock = threading.Lock()  # guards _pending / _idle transitions
        self._pending = 0  # chunks enqueued and not yet played (or dropped)
        self._playing = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        # Generation counter — bumped by cancel(). Chunks carry the generation they were enqueued under; the worker
        # drops queued chunks from an older generation and stops mid-chunk as soon as the generation moves on.
        self._gen = 0
        self._last_open_error = 0.0
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
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._q.put((self._gen, audio))

    def generation(self) -> int:
        return self._gen

    def cancel(self) -> None:
        """Stop immediately and drop queued audio."""
        self._gen += 1
        self._drain()

    async def wait_idle(self, poll: float = 0.02) -> None:
        while not self._idle.is_set():
            await asyncio.sleep(poll)

    def wait_idle_blocking(self, timeout: float | None = None) -> None:
        self._idle.wait(timeout)

    # ---- bookkeeping ---------------------------------------------------------------------------
    def _drain(self) -> None:
        """Drop everything queued (counts as finished)."""
        n = 0
        with contextlib.suppress(queue.Empty):
            while True:
                self._q.get_nowait()
                n += 1
        if n:
            self._finished(n)

    def _finished(self, n: int = 1) -> None:
        with self._lock:
            self._pending = max(0, self._pending - n)
            if self._pending == 0:
                self._idle.set()

    def _open(self, block: int) -> sd.OutputStream | None:
        try:
            stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", device=self.device, blocksize=block, latency="low")
            stream.start()
            return stream
        except Exception as e:  # noqa: BLE001
            now = time.monotonic()
            if now - self._last_open_error > OPEN_RETRY_LOG_S:
                self._last_open_error = now
                logger.error("audio output unavailable ({}); will retry on the next utterance", e)
            return None

    # ---- worker --------------------------------------------------------------------------------
    def _run(self) -> None:
        block = int(self.sr * 0.02)  # 20 ms
        stream: sd.OutputStream | None = None
        last_level = 0.0
        n_block = 0
        while True:
            try:
                gen, audio = self._q.get(timeout=0.05)
            except queue.Empty:
                if self._playing.is_set():
                    self._playing.clear()
                    self._emit_level(0.0)
                    self._emit_spectrum(silence=True)
                continue
            if gen != self._gen:  # queued before a cancel()
                self._finished()
                continue
            if stream is None:
                stream = self._open(block)
                if stream is None:  # no device right now: drop this utterance so the pipeline doesn't wait on it
                    self._finished()
                    self._drain()
                    continue
            self._playing.set()
            try:
                for i in range(0, len(audio), block):
                    if gen != self._gen:  # cancelled mid-chunk (barge-in)
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
                if gen != self._gen:
                    # tiny fade to avoid a click
                    stream.write(np.zeros(block, dtype=np.float32))
                    self._emit_level(0.0)
                    self._emit_spectrum(silence=True)
            except Exception as e:  # noqa: BLE001  (device unplugged, driver reset, stream closed at interpreter exit…)
                logger.warning("audio output stopped: {} — the device will be re-opened for the next utterance", e)
                with contextlib.suppress(Exception):
                    stream.close()
                stream = None
                self._playing.clear()
                self._emit_level(0.0)
                self._emit_spectrum(silence=True)
                self._drain()  # the rest of this utterance would come out garbled on a fresh stream anyway
            finally:
                self._finished()

    def _emit_level(self, rms: float) -> None:
        if self.on_level:
            # Map RMS (~0..0.3 for speech) onto 0..1 with a soft knee.
            lvl = min(1.0, (rms * 4.0) ** 0.7)
            with contextlib.suppress(Exception):  # a HUD-side failure must never stop playback
                self.on_level(lvl)

    def _emit_spectrum(self, silence: bool = False) -> None:
        if self.on_spectrum is None or self._analyzer is None:
            return
        if silence:
            self._ring[:] = 0.0
        with contextlib.suppress(Exception):  # a HUD-side failure must never stop playback
            self.on_spectrum(np.zeros(self._analyzer.n_bands, np.float32) if silence else self._analyzer.bands(self._ring))
