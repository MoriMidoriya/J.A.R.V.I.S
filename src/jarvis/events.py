"""Typed events flowing between the audio pipeline, agent, tools and HUD.

Every event serialises to a small JSON object {"type": ..., ...} for the WebSocket.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field


class State(StrEnum):
    BOOTING = "booting"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CONFIRMING = "confirming"
    ERROR = "error"


class Event(BaseModel):
    type: str

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class StateChanged(Event):
    type: Literal["state"] = "state"
    state: State


class WakeDetected(Event):
    type: Literal["wake"] = "wake"
    score: float


class TranscriptPartial(Event):
    type: Literal["transcript.partial"] = "transcript.partial"
    text: str


class TranscriptFinal(Event):
    type: Literal["transcript.final"] = "transcript.final"
    text: str
    source: Literal["voice", "text"] = "voice"


class AssistantToken(Event):
    type: Literal["assistant.token"] = "assistant.token"
    text: str


class AssistantFinal(Event):
    type: Literal["assistant.final"] = "assistant.final"
    text: str


class AudioLevel(Event):
    """RMS 0..1 of what Jarvis is currently saying (drives the HUD pulse)."""

    type: Literal["audio.level"] = "audio.level"
    level: float


class MicLevel(Event):
    type: Literal["mic.level"] = "mic.level"
    level: float


class Spectrum(Event):
    """24 log-spaced bands (80 Hz–8 kHz) in 0..1 — `out` = Jarvis' voice at 25 Hz, `mic` = the room while listening
    at ~12 Hz. Drives the HUD's bar ring (bars lengthen per band instead of the whole reactor flashing)."""

    type: Literal["spectrum"] = "spectrum"
    source: Literal["out", "mic"]
    bands: list[float]


class SystemStats(Event):
    """Every 2 s: what the side widgets show (CPU, RAM, VRAM, network rate, battery)."""

    type: Literal["system.stats"] = "system.stats"
    cpu_pct: float
    mem_pct: float
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    net_up_kbps: float = 0.0
    net_down_kbps: float = 0.0
    battery_pct: int | None = None
    plugged: bool | None = None


class ToolCall(Event):
    type: Literal["tool.call"] = "tool.call"
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(Event):
    type: Literal["tool.result"] = "tool.result"
    id: str
    name: str
    ok: bool
    summary: str


class ConfirmRequest(Event):
    type: Literal["confirm.request"] = "confirm.request"
    id: str
    prompt: str


class ConfirmResolved(Event):
    type: Literal["confirm.resolved"] = "confirm.resolved"
    id: str
    accepted: bool


class Notice(Event):
    type: Literal["notice"] = "notice"
    level: Literal["info", "warn", "error"] = "info"
    text: str


class ReminderDue(Event):
    """A reminder/timer has come due; the voice pipeline announces it (and the HUD shows it)."""

    type: Literal["reminder.due"] = "reminder.due"
    id: int
    text: str
    kind: Literal["reminder", "timer"] = "reminder"


class Metrics(Event):
    type: Literal["metrics"] = "metrics"
    model: str
    stt_ms: int | None = None
    llm_first_token_ms: int | None = None
    tts_first_audio_ms: int | None = None
    total_ms: int | None = None


class UiPrefs(Event):
    """UI preferences changed (from a HUD's settings drawer); every connected HUD applies them."""

    type: Literal["ui.prefs"] = "ui.prefs"
    prefs: dict[str, Any]


class UiCommand(Event):
    """Something asked the HUD to do something with itself (voice: "hide yourself", "show the full interface",
    "power down"). The HUD executes it through its window bridge; a browser tab does what it can (layout only)."""

    type: Literal["ui.command"] = "ui.command"
    action: Literal["show", "hide", "mode", "shutdown"]
    mode: Literal["compact", "panel", "cinematic"] | None = None


Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """Minimal async pub/sub. Handlers may be sync or async; exceptions are logged, never raised."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._any: list[Handler] = []

    def on(self, event_type: str | None, handler: Handler) -> None:
        if event_type is None:
            self._any.append(handler)
        else:
            self._handlers[event_type].append(handler)

    def off(self, event_type: str | None, handler: Handler) -> None:
        target = self._any if event_type is None else self._handlers[event_type]
        if handler in target:
            target.remove(handler)

    async def emit(self, event: Event) -> None:
        for h in [*self._handlers.get(event.type, []), *self._any]:
            try:
                r = h(event)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # noqa: BLE001
                logger.exception("event handler failed for {}", event.type)

    def emit_nowait(self, event: Event) -> None:
        """Fire-and-forget from sync code running inside the loop thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.emit(event))

    def emit_threadsafe(self, loop: asyncio.AbstractEventLoop, event: Event) -> None:
        """Fire from a non-loop thread (audio callbacks)."""
        asyncio.run_coroutine_threadsafe(self.emit(event), loop)
