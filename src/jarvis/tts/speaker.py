"""Speaker: turns streamed text into speech sentence-by-sentence, with cancellation (barge-in)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time

from loguru import logger

from jarvis.audio.output import AudioPlayer
from jarvis.events import AudioLevel, EventBus
from jarvis.llm.chunker import SentenceChunker
from jarvis.tts.base import TTSEngine


class Speaker:
    def __init__(self, tts: TTSEngine, bus: EventBus, loop: asyncio.AbstractEventLoop, output_device: int | str | None = None) -> None:
        self.tts = tts
        self.bus = bus
        self.loop = loop
        self.player = AudioPlayer(sample_rate=tts.sample_rate, device=output_device, on_level=self._on_level)
        # Single worker so synthesis stays ordered and the model isn't hit concurrently.
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._gen = 0
        self._pending: list[asyncio.Future] = []
        self._first_audio_ms: int | None = None
        self._t0: float | None = None
        self._last_level = 0.0

    # ---- levels → HUD --------------------------------------------------------------------------
    def _on_level(self, lvl: float) -> None:
        if abs(lvl - self._last_level) < 0.02 and lvl > 0:
            return
        self._last_level = lvl
        self.bus.emit_threadsafe(self.loop, AudioLevel(level=lvl))

    # ---- speaking ------------------------------------------------------------------------------
    @property
    def is_speaking(self) -> bool:
        return self.player.is_playing or any(not f.done() for f in self._pending)

    def begin(self) -> Utterance:
        self._t0 = time.perf_counter()
        self._first_audio_ms = None
        return Utterance(self)

    async def say(self, text: str) -> None:
        """Speak a complete string and wait until playback finishes."""
        u = self.begin()
        u.feed(text)
        await u.finish()

    def cancel(self) -> None:
        self._gen += 1
        self.player.cancel()
        for f in self._pending:
            f.cancel()
        self._pending.clear()

    def _synth_and_queue(self, sentence: str, gen: int) -> None:
        try:
            for chunk in self.tts.synthesize(sentence):
                if gen != self._gen:
                    return
                if self._first_audio_ms is None and self._t0 is not None:
                    self._first_audio_ms = int((time.perf_counter() - self._t0) * 1000)
                    logger.debug("first audio in {} ms", self._first_audio_ms)
                self.player.enqueue(chunk, gen=self.player.generation())
        except Exception:  # noqa: BLE001
            logger.exception("tts failed for {!r}", sentence[:60])

    @property
    def first_audio_ms(self) -> int | None:
        return self._first_audio_ms


class Utterance:
    """One spoken reply, possibly streamed in as text deltas."""

    def __init__(self, speaker: Speaker) -> None:
        self.sp = speaker
        self.gen = speaker._gen
        self.chunker = SentenceChunker()
        self.text = ""

    def feed(self, delta: str) -> None:
        if self.gen != self.sp._gen:
            return
        self.text += delta
        for sentence in self.chunker.feed(delta):
            self._schedule(sentence)

    def _schedule(self, sentence: str) -> None:
        fut = self.sp.loop.run_in_executor(self.sp._pool, self.sp._synth_and_queue, sentence, self.gen)
        self.sp._pending.append(fut)
        fut.add_done_callback(lambda f: self.sp._pending.remove(f) if f in self.sp._pending else None)

    async def finish(self) -> None:
        """Flush remaining text, wait for synthesis and playback to complete (or cancellation)."""
        if self.gen == self.sp._gen:
            for sentence in self.chunker.flush():
                self._schedule(sentence)
        pend = [f for f in self.sp._pending]
        if pend:
            await asyncio.gather(*pend, return_exceptions=True)
        if self.gen == self.sp._gen:
            await asyncio.sleep(0.05)
            await self.sp.player.wait_idle()

    @property
    def cancelled(self) -> bool:
        return self.gen != self.sp._gen
