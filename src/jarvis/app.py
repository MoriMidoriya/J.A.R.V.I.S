"""Application wiring: builds every subsystem and runs the voice loop (and, from Phase 4, the HUD server).

Boot order matters for perceived latency:
  1. Audio stack (TTS, STT, wake word, VAD) loads in parallel threads (~10-20 s warm, ~60 s on first run).
  2. Jarvis says "Systems online" and starts listening. Fast-path intents ("daddy's home") work immediately.
  3. The LLM warms in the background (cold load after a reboot can take 60-90 s). If you ask something before
     it's ready, Jarvis says so and answers as soon as the model is up.
  4. A keep-alive pings Ollama every few minutes so the model never unloads while Jarvis is running.
"""

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
from jarvis.llm import ollama
from jarvis.llm.agent import Agent
from jarvis.llm.context import day_part
from jarvis.llm.router import LLMRouter
from jarvis.logging_setup import setup_logging
from jarvis.stt import make_stt
from jarvis.tools.all import registry
from jarvis.tts import make_tts
from jarvis.tts.speaker import Speaker
from jarvis.voice import VoicePipeline

LLM_WARMUP_TIMEOUT_S = 300
KEEPALIVE_EVERY_S = 600


class JarvisApp:
    def __init__(self, settings: Settings, ui: bool = True) -> None:
        self.s = settings
        self.ui = ui
        self.bus = EventBus()
        self.router = LLMRouter(settings)
        registry.bus = self.bus
        self.agent = Agent(settings, self.router, registry, self.bus)
        self.pipeline: VoicePipeline | None = None
        self.llm_ready = asyncio.Event()
        self._bg: list[asyncio.Task] = []

    async def start(self) -> None:
        t0 = time.perf_counter()
        await self.bus.emit(StateChanged(state=State.BOOTING))
        loop = asyncio.get_running_loop()

        # Kick the LLM warmup off first; it overlaps with the audio model loads.
        self._bg.append(loop.create_task(self._warm_llm()))
        # Pre-build the Start Menu app index so the first "open X" doesn't pay the ~1 s scan mid-turn.
        self._bg.append(loop.run_in_executor(None, self._warm_apps_index))

        tts_f = loop.run_in_executor(None, self._load_tts)
        stt_f = loop.run_in_executor(None, self._load_stt)
        wake_f = loop.run_in_executor(None, lambda: WakeWord(self.s.assistant.wake_word, self.s.assistant.wake_threshold))
        vad_f = loop.run_in_executor(None, lambda: SileroVAD(self.s.audio.vad_threshold, self.s.audio.vad_silence_ms, max_utterance_s=self.s.audio.max_utterance_s))
        tts, stt, wake, vad = await asyncio.gather(tts_f, stt_f, wake_f, vad_f)

        speaker = Speaker(tts, self.bus, loop, output_device=self.s.audio.output_device)
        mic = MicInput(loop, sample_rate=self.s.audio.sample_rate, frame_ms=self.s.audio.frame_ms, device=self.s.audio.input_device)
        self.pipeline = VoicePipeline(self.s, self.bus, self.agent, stt, speaker, mic, wake, vad, llm_ready=self.llm_ready)
        registry.confirm_handler = self.pipeline.confirm

        logger.info("audio stack ready in {:.1f}s", time.perf_counter() - t0)
        await self.bus.emit(Notice(text="J.A.R.V.I.S. online"))
        greeting_part = day_part() if day_part() != "night" else "evening"
        tail = "" if self.llm_ready.is_set() else " Language model is still loading."
        await speaker.say(f"Systems online. Good {greeting_part}, {self.s.assistant.user_title}.{tail}")
        await self.pipeline.run()

    def _load_tts(self):  # noqa: ANN202
        tts = make_tts(self.s)
        tts.warmup()
        return tts

    def _load_stt(self):  # noqa: ANN202
        stt = make_stt(self.s)
        stt.warmup()
        return stt

    @staticmethod
    def _warm_apps_index() -> None:
        """Background: build the Start Menu index and make sure Everything's search instance is up."""
        try:
            from jarvis.tools import apps, everything

            t0 = time.perf_counter()
            n = len(apps._load_index())
            logger.debug("apps index ready: {} apps in {:.0f} ms", n, (time.perf_counter() - t0) * 1000)
            everything.ensure_running()
        except Exception as e:  # noqa: BLE001
            logger.warning("tool warmup failed: {}", e)

    async def _warm_llm(self) -> None:
        """Ensure Ollama is up (start it if needed), load the primary model into VRAM, then keep it alive."""
        t0 = time.perf_counter()
        if self.router.is_local(self.s.llm.primary):
            ok = await asyncio.to_thread(ollama.ensure_running, self.s.llm.ollama_base_url)
            if not ok:
                await self.bus.emit(Notice(level="error", text="Ollama is not reachable; local models unavailable"))
                self.llm_ready.set()  # don't gate forever; turns will surface the error
                return
        try:
            await self.router.complete([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=5, timeout=LLM_WARMUP_TIMEOUT_S)
            logger.info("llm {} warm in {:.1f}s", self.s.llm.primary, time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001
            logger.warning("llm warmup failed: {}", e)
        self.llm_ready.set()
        if self.router.is_local(self.s.llm.primary):
            self._bg.append(asyncio.get_running_loop().create_task(self._keepalive()))

    async def _keepalive(self) -> None:
        """Re-arm Ollama's keep_alive timer so the model stays in VRAM while Jarvis runs (cheap no-op request)."""
        name = self.s.llm.primary.split("/", 1)[1]
        while True:
            await asyncio.sleep(KEEPALIVE_EVERY_S)
            try:
                # Through the router's pooled client so the HTTP connection itself stays warm too.
                await self.router.ollama._http().post("/api/generate", json={"model": name, "keep_alive": self.s.llm.ollama_keep_alive}, timeout=10)
                logger.debug("ollama keep-alive sent for {}", name)
            except Exception as e:  # noqa: BLE001
                logger.debug("keep-alive failed: {}", e)


def main(ui: bool = True, debug: bool = False) -> None:
    s = get_settings()
    setup_logging(s, console_level="DEBUG" if debug else s.logging.level)
    app = JarvisApp(s, ui=ui)
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        logger.info("shutting down")
        sys.exit(0)
