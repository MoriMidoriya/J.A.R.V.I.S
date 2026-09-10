"""Voice pipeline: mic → wake word → VAD → STT → (intent | agent) → TTS, with barge-in and a hot window."""

from __future__ import annotations

import asyncio
import time

import numpy as np
from loguru import logger

from jarvis.audio.input import MicInput, rms_level
from jarvis.audio.vad import SileroVAD, Utterance
from jarvis.audio.wakeword import WakeWord
from jarvis.config import Settings
from jarvis.events import EventBus, Metrics, MicLevel, Notice, State, StateChanged, TranscriptFinal, WakeDetected
from jarvis.llm import intents
from jarvis.llm.agent import Agent
from jarvis.stt.base import STTEngine
from jarvis.tts.speaker import Speaker

WAKE_ONLY_AFTER_S = 1.6  # "Yes, sir?" if nothing is said this long after the wake word
NO_SPEECH_GIVE_UP_S = 7.0  # stop listening if still nothing after the prompt


class VoicePipeline:
    def __init__(self, settings: Settings, bus: EventBus, agent: Agent, stt: STTEngine, speaker: Speaker, mic: MicInput, wake: WakeWord, vad: SileroVAD) -> None:
        self.s = settings
        self.bus = bus
        self.agent = agent
        self.stt = stt
        self.speaker = speaker
        self.mic = mic
        self.wake = wake
        self.vad = vad
        self.state = State.BOOTING
        self._turn: asyncio.Task | None = None
        self._listen_since = 0.0
        self._awaiting_first = False  # first utterance right after a wake word
        self._prompted = False
        self._hot_window = False
        self._confirm_fut: asyncio.Future[str] | None = None
        self._frame_i = 0

    # ---- state ---------------------------------------------------------------------------------
    async def set_state(self, st: State) -> None:
        if st != self.state:
            self.state = st
            await self.bus.emit(StateChanged(state=st))

    # ---- main loop -----------------------------------------------------------------------------
    async def run(self) -> None:
        self.mic.start()
        await self.set_state(State.IDLE)
        logger.info("voice pipeline ready — say 'Hey Jarvis'")
        while True:
            frame = await self.mic.queue.get()
            self._frame_i += 1
            try:
                await self._on_frame(frame)
            except Exception:  # noqa: BLE001
                logger.exception("frame handling failed")

    async def _on_frame(self, frame: np.ndarray) -> None:
        st = self.state
        if st in (State.LISTENING, State.CONFIRMING):
            if self._frame_i % 2 == 0:
                await self.bus.emit(MicLevel(level=rms_level(frame)))
            utt = self.vad.feed(frame)
            if utt is not None:
                await self._on_utterance(utt)
                return
            await self._check_listen_timeouts()
            # Wake word while listening (user restarts): treat as a fresh start.
            if not self.vad.speaking and self.wake.detect(frame):
                await self._on_wake(0.0, restart=True)
            return

        # IDLE / THINKING / SPEAKING: only the wake word matters (barge-in while speaking/thinking).
        score = self.wake.detect(frame)
        if score is not None:
            await self._on_wake(score)

    # ---- wake ----------------------------------------------------------------------------------
    async def _on_wake(self, score: float, restart: bool = False) -> None:
        logger.info("wake word ({:.2f}){}", score, " [barge-in]" if self.state in (State.SPEAKING, State.THINKING) else "")
        await self.bus.emit(WakeDetected(score=score))
        self._cancel_turn()
        self.speaker.cancel()
        await self._start_listening(first=True)

    async def _start_listening(self, first: bool, hot: bool = False) -> None:
        pre = self.mic.preroll()
        self.vad.reset(preroll=pre[-int(16000 * 0.25) :] if first else None)
        self._listen_since = time.monotonic()
        self._awaiting_first = first
        self._prompted = False
        self._hot_window = hot
        await self.set_state(State.LISTENING)

    async def _check_listen_timeouts(self) -> None:
        if self.vad.speaking or self.state == State.CONFIRMING:
            if self.state == State.CONFIRMING and not self.vad.speaking and time.monotonic() - self._listen_since > self.s.tools.confirm_timeout_s:
                self._resolve_confirm("")
            return
        waited = time.monotonic() - self._listen_since
        if self._hot_window:
            if waited > self.s.assistant.hot_window_seconds:
                await self._go_idle()
            return
        if self._awaiting_first and not self._prompted and waited > WAKE_ONLY_AFTER_S:
            self._prompted = True
            reply = intents.match("", self.s)
            await self._speak_then_listen(reply.reply if reply else "Yes, sir?", first=True)
            return
        if waited > NO_SPEECH_GIVE_UP_S:
            await self._go_idle()

    async def _go_idle(self) -> None:
        self.vad.reset()
        self.wake.reset()
        await self.set_state(State.IDLE)

    # ---- utterance -----------------------------------------------------------------------------
    async def _on_utterance(self, utt: Utterance) -> None:
        t0 = time.perf_counter()
        was_confirming = self.state == State.CONFIRMING
        await self.set_state(State.THINKING)
        tr = await asyncio.to_thread(self.stt.transcribe, utt.audio)
        stt_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("heard ({:.1f}s audio, {} ms stt): {!r}", utt.duration_s, stt_ms, tr.text)

        if was_confirming:
            self._resolve_confirm(tr.text)
            return
        if tr.empty:
            await self._start_listening(first=self._awaiting_first, hot=self._hot_window)
            return
        await self.bus.emit(TranscriptFinal(text=tr.text, source="voice"))
        await self.handle_text(tr.text, stt_ms=stt_ms)

    async def handle_text(self, text: str, *, stt_ms: int | None = None) -> None:
        """Route a user utterance (voice or typed) to a fast-path intent or the agent."""
        intent = intents.match(text, self.s)
        if intent is not None and intent.kind in ("greeting", "stop", "thanks"):
            logger.info("fast-path intent: {}", intent.kind)
            self._cancel_turn()
            self.speaker.cancel()
            if intent.kind == "stop":
                await self._speak_then_listen(intent.reply or "", first=False, hot=False)
                await self._go_idle()
            else:
                await self.bus.emit(Metrics(model="fast-path", stt_ms=stt_ms, llm_first_token_ms=0))
                await self._speak_then_listen(intent.reply or "", first=False, hot=True)
            return

        self._cancel_turn()
        self._turn = asyncio.create_task(self._agent_turn(text, stt_ms))

    async def _agent_turn(self, text: str, stt_ms: int | None) -> None:
        await self.set_state(State.THINKING)
        utter = self.speaker.begin()
        spoke = False

        async def on_text(delta: str) -> None:
            nonlocal spoke
            if not spoke:
                spoke = True
                await self.set_state(State.SPEAKING)
            utter.feed(delta)

        try:
            await self.agent.respond(text, on_text=on_text)
            await self.set_state(State.SPEAKING)
            await utter.finish()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("agent turn failed")
            await self.bus.emit(Notice(level="error", text=str(e)))
            self.speaker.cancel()
            await self.speaker.say("I'm afraid something went wrong on my end, sir.")
        if not utter.cancelled:
            await self._start_listening(first=False, hot=True)

    def _cancel_turn(self) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
        self._turn = None

    async def _speak_then_listen(self, text: str, first: bool, hot: bool = True) -> None:
        if text:
            await self.set_state(State.SPEAKING)
            await self.speaker.say(text)
        await self._start_listening(first=first, hot=hot)

    # ---- confirmations (voice yes/no) ----------------------------------------------------------
    async def confirm(self, _id: str, prompt: str) -> bool:
        """Registry confirm handler: speak the question, listen for yes/no."""
        for attempt in range(2):
            self.speaker.cancel()
            await self.set_state(State.SPEAKING)
            await self.speaker.say(prompt if attempt == 0 else "Sorry, sir — was that a yes or a no?")
            self.vad.reset()
            self._listen_since = time.monotonic()
            self._confirm_fut = asyncio.get_running_loop().create_future()
            await self.set_state(State.CONFIRMING)
            try:
                answer = await asyncio.wait_for(self._confirm_fut, timeout=self.s.tools.confirm_timeout_s + 2)
            except TimeoutError:
                answer = ""
            finally:
                self._confirm_fut = None
            await self.set_state(State.THINKING)
            verdict = intents.classify_yes_no(answer) if answer else False
            logger.info("confirmation answer {!r} -> {}", answer, verdict)
            if verdict is not None:
                return verdict
        return False

    def _resolve_confirm(self, text: str) -> None:
        if self._confirm_fut and not self._confirm_fut.done():
            self._confirm_fut.set_result(text)

    def resolve_confirm_external(self, accepted: bool) -> None:
        """HUD button / typed answer."""
        self._resolve_confirm("yes" if accepted else "no")
