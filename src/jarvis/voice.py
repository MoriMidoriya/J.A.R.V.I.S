"""Voice pipeline: mic → wake word → VAD → STT → (fast-path intent | agent) → TTS, with barge-in and a hot window.

State machine (see events.State):

    IDLE ──wake──► LISTENING ──utterance──► THINKING ──first token──► SPEAKING ──done──► LISTENING (hot window)
      ▲                │ silence                                          │ wake (barge-in)        │ hot window expires
      └────────────────┴──────────────────────────────────────────────────┴────────────────────────┘

    CONFIRMING: a tool needs a yes/no; we speak the question and listen for the answer.

Design rules:
- The frame loop (`run`) must never block on speech; anything that speaks runs in a separate task (`_turn`).
- Frames captured while Jarvis is speaking are never fed to the VAD/STT (half-duplex); the mic queue is drained
  whenever we start listening so Jarvis does not transcribe his own voice.
- The wake word is detected in every state, so "Hey Jarvis" always interrupts (barge-in), with a slightly higher
  threshold while speaking to avoid false triggers from Jarvis's own audio.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
from loguru import logger

from jarvis.audio.input import MicInput, rms_level
from jarvis.audio.vad import SileroVAD, Utterance
from jarvis.audio.wakeword import WakeWord
from jarvis.config import Settings
from jarvis.events import (
    AssistantFinal,
    EventBus,
    Metrics,
    MicLevel,
    Notice,
    State,
    StateChanged,
    TranscriptFinal,
    WakeDetected,
)
from jarvis.llm import intents
from jarvis.llm.agent import Agent
from jarvis.stt.base import STTEngine
from jarvis.tts.speaker import Speaker

WAKE_ONLY_AFTER_S = 1.6  # "Yes, sir?" if nothing is said this long after the wake word
NO_SPEECH_GIVE_UP_S = 7.0  # stop listening if still nothing this long after the prompt
PREROLL_S = 0.15  # audio kept from just before the wake word fired (so the first syllable isn't lost)


class VoicePipeline:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        agent: Agent,
        stt: STTEngine,
        speaker: Speaker,
        mic: MicInput,
        wake: WakeWord,
        vad: SileroVAD,
        llm_ready: asyncio.Event | None = None,
    ) -> None:
        self.s = settings
        self.bus = bus
        self.agent = agent
        self.stt = stt
        self.speaker = speaker
        self.mic = mic
        self.wake = wake
        self.vad = vad
        self.llm_ready = llm_ready or asyncio.Event()
        if llm_ready is None:
            self.llm_ready.set()
        self.state = State.BOOTING
        self._turn: asyncio.Task | None = None
        self._listen_since = 0.0
        self._awaiting_first = False  # waiting for the first utterance right after a wake word
        self._prompted = False  # already said "Yes, sir?" for this wake
        self._hot_window = False  # listening without a wake word, right after a reply
        self._confirm_fut: asyncio.Future[str] | None = None
        self._frame_i = 0
        self._wake_threshold = wake.threshold
        self._wake_threshold_speaking = min(0.9, wake.threshold + 0.2)
        self.last_stt_ms: int | None = None
        self._announce_queue: list[str] = []  # reminders that came due while we were busy
        self.bus.on("reminder.due", self._on_reminder_due)

    # ---- state ---------------------------------------------------------------------------------
    async def set_state(self, st: State) -> None:
        if st != self.state:
            logger.debug("state {} -> {}", self.state, st)
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
            # Saying the wake word again while we listen restarts the capture.
            if not self.vad.speaking and self.wake.detect(frame) is not None:
                await self._on_wake(1.0)
            return

        # IDLE / THINKING / SPEAKING: only the wake word matters (barge-in while speaking/thinking).
        self.wake.threshold = self._wake_threshold_speaking if st in (State.SPEAKING, State.THINKING) else self._wake_threshold
        score = self.wake.detect(frame)
        if score is not None:
            await self._on_wake(score)

    # ---- wake ----------------------------------------------------------------------------------
    async def _on_wake(self, score: float) -> None:
        interrupting = self.state in (State.SPEAKING, State.THINKING, State.CONFIRMING)
        logger.info("wake word ({:.2f}){}", score, " [barge-in]" if interrupting else "")
        await self.bus.emit(WakeDetected(score=score))
        self._cancel_turn()
        self.speaker.cancel()
        self._resolve_confirm("")  # an interrupted confirmation counts as "no"
        await self._start_listening(first=True)

    async def _start_listening(self, first: bool, hot: bool = False, prompted: bool = False) -> None:
        self._drain_mic()
        pre = self.mic.preroll()
        self.vad.reset(preroll=pre[-int(16000 * PREROLL_S) :] if first and not prompted else None)
        self._listen_since = time.monotonic()
        self._awaiting_first = first
        self._prompted = prompted
        self._hot_window = hot
        await self.set_state(State.LISTENING)
        if hot:
            await self._flush_announcements()  # a reminder came due mid-turn: say it now rather than after the window

    def _drain_mic(self) -> None:
        """Drop frames captured while we were speaking/thinking so Jarvis never transcribes himself."""
        n = 0
        while True:
            try:
                self.mic.queue.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                break
        if n:
            logger.debug("drained {} stale mic frames", n)

    async def _check_listen_timeouts(self) -> None:
        if self.vad.speaking:
            return
        waited = time.monotonic() - self._listen_since
        if self.state == State.CONFIRMING:
            if waited > self.s.tools.confirm_timeout_s:
                self._resolve_confirm("")
            return
        if self._hot_window:
            if waited > self.s.assistant.hot_window_seconds:
                await self._go_idle()
            return
        if self._awaiting_first and not self._prompted and waited > WAKE_ONLY_AFTER_S:
            self._prompted = True
            reply = intents.match("", self.s)
            self._run_turn(self._prompt_turn(reply.reply if reply else f"Yes, {self.s.assistant.user_title}?"))
            return
        if waited > NO_SPEECH_GIVE_UP_S:
            logger.info("nothing heard; back to idle")
            await self._go_idle()

    async def _go_idle(self) -> None:
        self.vad.reset()
        self.wake.reset()
        self._hot_window = False
        self._awaiting_first = False
        await self.set_state(State.IDLE)
        await self._flush_announcements()

    # ---- utterance -----------------------------------------------------------------------------
    async def _on_utterance(self, utt: Utterance) -> None:
        t0 = time.perf_counter()
        was_confirming = self.state == State.CONFIRMING
        await self.set_state(State.THINKING)
        tr = await asyncio.to_thread(self.stt.transcribe, utt.audio)
        stt_ms = int((time.perf_counter() - t0) * 1000)
        self.last_stt_ms = stt_ms
        logger.info("heard ({:.1f}s audio, {} ms stt): {!r}", utt.duration_s, stt_ms, tr.text)

        if was_confirming:
            self._resolve_confirm(tr.text)
            return
        if tr.empty:
            # Nothing intelligible (e.g. just the wake word). Keep listening; the 1.6 s prompt still applies.
            await self._start_listening(first=self._awaiting_first, hot=self._hot_window, prompted=self._prompted)
            return
        await self.bus.emit(TranscriptFinal(text=tr.text, source="voice"))
        await self.handle_text(tr.text, stt_ms=stt_ms)

    async def handle_text(self, text: str, *, stt_ms: int | None = None) -> None:
        """Route a user utterance (voice or typed) to a fast-path intent or the LLM agent. Never blocks."""
        intent = intents.match(text, self.s)
        if intent is not None and intent.kind in ("greeting", "stop", "thanks"):
            logger.info("fast-path intent: {}", intent.kind)
            self._run_turn(self._fast_turn(intent, stt_ms))
            return
        self._run_turn(self._agent_turn(text, stt_ms))

    # ---- entry points for the HUD / API (typed text, images, buttons) ---------------------------
    async def submit_text(self, text: str, image_b64: str | None = None, image_mime: str = "image/png") -> None:
        """Typed input from the HUD or REST. Behaves like a spoken utterance (same intents, same agent, spoken reply)."""
        text = text.strip()
        if not text and not image_b64:
            return
        if self.state == State.CONFIRMING:
            self._resolve_confirm(text)
            return
        await self.bus.emit(TranscriptFinal(text=text or "(image)", source="text"))
        if image_b64:
            self._run_turn(self._agent_turn(text or "What do you see?", None, image_b64=image_b64, image_mime=image_mime))
        else:
            await self.handle_text(text)

    async def wake_external(self) -> None:
        """HUD click / hotkey: behave as if the wake word was heard."""
        if self.state in (State.LISTENING, State.CONFIRMING):
            return
        logger.info("wake requested by HUD (click / hotkey)")
        await self._on_wake(1.0)

    async def interrupt(self) -> None:
        """HUD 'stop' button: cancel speech and go idle."""
        self._cancel_turn()
        self.speaker.cancel()
        self._resolve_confirm("")
        await self._go_idle()

    # ---- turns (anything that speaks runs here, never in the frame loop) ------------------------
    def _run_turn(self, coro) -> None:
        self._cancel_turn()
        self.speaker.cancel()
        self._turn = asyncio.create_task(coro)

    def _cancel_turn(self) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
        self._turn = None

    async def _fast_turn(self, intent: intents.Intent, stt_ms: int | None) -> None:
        t0 = time.perf_counter()
        reply = intent.reply or ""
        await self.bus.emit(AssistantFinal(text=reply))
        await self.set_state(State.SPEAKING)
        await self.speaker.say(reply)
        await self.bus.emit(Metrics(model="fast-path", stt_ms=stt_ms, llm_first_token_ms=0, tts_first_audio_ms=self.speaker.first_audio_ms, total_ms=int((time.perf_counter() - t0) * 1000)))
        if intent.kind == "stop":
            await self._go_idle()
        else:
            await self._start_listening(first=False, hot=True)

    async def _prompt_turn(self, text: str) -> None:
        await self.set_state(State.SPEAKING)
        await self.speaker.say(text)
        await self._start_listening(first=True, prompted=True)

    async def _agent_turn(self, text: str, stt_ms: int | None, image_b64: str | None = None, image_mime: str = "image/png") -> None:
        await self.set_state(State.THINKING)
        if not self.llm_ready.is_set():
            await self.set_state(State.SPEAKING)
            await self.speaker.say(f"One moment, {self.s.assistant.user_title} — my language model is still loading.")
            await self.set_state(State.THINKING)
            await self.llm_ready.wait()
        utter = self.speaker.begin()
        spoke = False

        async def on_text(delta: str) -> None:
            nonlocal spoke
            if not spoke:
                spoke = True
                await self.set_state(State.SPEAKING)
            utter.feed(delta)

        try:
            await self.agent.respond(text, on_text=on_text, image_b64=image_b64, image_mime=image_mime)
            await self.set_state(State.SPEAKING)
            await utter.finish()
            logger.info("voice turn: stt {} ms, first audio {} ms after LLM start", stt_ms, self.speaker.first_audio_ms)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("agent turn failed")
            await self.bus.emit(Notice(level="error", text=str(e)))
            self.speaker.cancel()
            await self.speaker.say(f"I'm afraid something went wrong on my end, {self.s.assistant.user_title}.")
        if not utter.cancelled:
            await self._start_listening(first=False, hot=True)

    # ---- announcements (reminders / timers) ------------------------------------------------------
    async def _on_reminder_due(self, ev) -> None:
        title = self.s.assistant.user_title
        if ev.kind == "timer":
            text = f"{title.capitalize()}, your timer is up." if ev.text in ("", "timer") else f"{title.capitalize()}, your {ev.text} timer is up."
        else:
            text = f"{title.capitalize()}, a reminder: {ev.text}"
        await self.announce(text)

    async def announce(self, text: str) -> None:
        """Speak something unprompted. Immediately when idle or in the hot window; otherwise after the current turn."""
        busy = self.state in (State.THINKING, State.SPEAKING, State.CONFIRMING) or (self.state == State.LISTENING and not self._hot_window)
        if busy:
            self._announce_queue.append(text)
            logger.debug("announcement queued (state {}): {!r}", self.state, text)
            return
        self._run_turn(self._announce_turn(text))

    async def _announce_turn(self, text: str) -> None:
        await self.bus.emit(AssistantFinal(text=text))
        await self.set_state(State.SPEAKING)
        await self.speaker.say(text)
        await self._start_listening(first=False, hot=True)  # "thanks" / "snooze"… without the wake word

    async def _flush_announcements(self) -> bool:
        if not self._announce_queue:
            return False
        text = " ".join(self._announce_queue)
        self._announce_queue.clear()
        # call_soon: this may run from inside the finishing turn task; starting the new turn after it returns avoids
        # a task cancelling itself.
        asyncio.get_running_loop().call_soon(lambda: self._run_turn(self._announce_turn(text)))
        return True

    # ---- confirmations (voice yes/no) ----------------------------------------------------------
    async def confirm(self, _id: str, prompt: str) -> bool:
        """Registry confirm handler: speak the question, listen for yes/no (two attempts)."""
        for attempt in range(2):
            self.speaker.cancel()
            await self.set_state(State.SPEAKING)
            await self.speaker.say(prompt if attempt == 0 else f"Sorry, {self.s.assistant.user_title} — was that a yes or a no?")
            self._drain_mic()
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
            verdict = intents.classify_yes_no(answer) if answer else None
            logger.info("confirmation answer {!r} -> {}", answer, verdict)
            if verdict is not None:
                return verdict
            if not answer:
                return False  # silence / interrupted = no
        return False

    def _resolve_confirm(self, text: str) -> None:
        if self._confirm_fut and not self._confirm_fut.done():
            self._confirm_fut.set_result(text)

    def resolve_confirm_external(self, accepted: bool) -> None:
        """HUD button / typed answer."""
        self._resolve_confirm("yes" if accepted else "no")
