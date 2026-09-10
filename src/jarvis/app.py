"""Application wiring: builds every subsystem and runs the voice loop (and, from Phase 4, the HUD server)."""

from __future__ import annotations

import asyncio
import sys
import time

from loguru import logger

from jarvis.audio.input import MicInput
from jarvis.audio.vad import SileroVAD
from jarvis.audio.wakeword import WakeWord
from jarvis.config import Settings, get_settings
from jarvis.events import EventBus, Notice, State, StateChanged
from jarvis.llm.agent import Agent
from jarvis.llm.context import day_part
from jarvis.llm.router import LLMRouter
from jarvis.logging_setup import setup_logging
from jarvis.stt import make_stt
from jarvis.tools.all import registry
from jarvis.tts import make_tts
from jarvis.tts.speaker import Speaker
from jarvis.voice import VoicePipeline


class JarvisApp:
    def __init__(self, settings: Settings, ui: bool = True) -> None:
        self.s = settings
        self.ui = ui
        self.bus = EventBus()
        self.router = LLMRouter(settings)
        registry.bus = self.bus
        self.agent = Agent(settings, self.router, registry, self.bus)
        self.pipeline: VoicePipeline | None = None

    async def start(self) -> None:
        t0 = time.perf_counter()
        await self.bus.emit(StateChanged(state=State.BOOTING))
        loop = asyncio.get_running_loop()

        # Load the heavy models in parallel threads.
        tts_f = loop.run_in_executor(None, self._load_tts)
        stt_f = loop.run_in_executor(None, self._load_stt)
        wake_f = loop.run_in_executor(None, lambda: WakeWord(self.s.assistant.wake_word, self.s.assistant.wake_threshold))
        vad_f = loop.run_in_executor(None, lambda: SileroVAD(self.s.audio.vad_threshold, self.s.audio.vad_silence_ms, max_utterance_s=self.s.audio.max_utterance_s))
        llm_f = loop.create_task(self._warm_llm())
        tts, stt, wake, vad, _ = await asyncio.gather(tts_f, stt_f, wake_f, vad_f, llm_f)

        speaker = Speaker(tts, self.bus, loop, output_device=self.s.audio.output_device)
        mic = MicInput(loop, sample_rate=self.s.audio.sample_rate, frame_ms=self.s.audio.frame_ms, device=self.s.audio.input_device)
        self.pipeline = VoicePipeline(self.s, self.bus, self.agent, stt, speaker, mic, wake, vad)
        registry.confirm_handler = self.pipeline.confirm

        logger.info("boot complete in {:.1f}s", time.perf_counter() - t0)
        await self.bus.emit(Notice(text="J.A.R.V.I.S. online"))
        await speaker.say(f"Systems online. Good {day_part() if day_part() != 'night' else 'evening'}, {self.s.assistant.user_title}.")
        await self.pipeline.run()

    def _load_tts(self):  # noqa: ANN202
        tts = make_tts(self.s)
        tts.warmup()
        return tts

    def _load_stt(self):  # noqa: ANN202
        stt = make_stt(self.s)
        stt.warmup()
        return stt

    async def _warm_llm(self) -> None:
        """Pull the primary model into VRAM so the first real turn is fast."""
        try:
            await self.router.complete([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=5)
            logger.info("llm {} warm", self.s.llm.primary)
        except Exception as e:  # noqa: BLE001
            logger.warning("llm warmup failed: {}", e)


def main(ui: bool = True, debug: bool = False) -> None:
    s = get_settings()
    setup_logging(s, console_level="DEBUG" if debug else s.logging.level)
    app = JarvisApp(s, ui=ui)
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        logger.info("shutting down")
        sys.exit(0)
