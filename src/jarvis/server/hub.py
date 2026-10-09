"""WebSocket hub: fans every EventBus event out to connected HUD clients and routes client commands back.

Protocol (JSON objects, one per message)
  server → client : every `jarvis.events.Event` as `event.to_json()` (type = "state", "wake", "transcript.final",
                    "assistant.token", "assistant.final", "audio.level", "mic.level", "tool.call", "tool.result",
                    "confirm.request", "confirm.resolved", "notice", "reminder.due", "metrics"),
                    plus `{"type": "hello", ...}` once on connect with the current state and settings.
  client → server : {"type": "text", "text": "..."}                       typed command
                    {"type": "image", "text": "...", "image_b64": "...", "mime": "image/png"}
                    {"type": "confirm", "id": "...", "accepted": true}    confirm button
                    {"type": "wake"}                                      click the reactor
                    {"type": "interrupt"}                                 stop talking
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from jarvis.config import Settings
from jarvis.events import Event, EventBus

HIGH_RATE = {"audio.level", "mic.level"}  # droppable when a client lags; everything else is delivered in order
HISTORY_TYPES = {"transcript.final", "assistant.final", "tool.call", "tool.result", "notice", "reminder.due"}
QUEUE_MAX = 256


class Hub:
    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self.s = settings
        self.bus = bus
        self.clients: dict[WebSocket, asyncio.Queue[str]] = {}
        self.state = "booting"
        self.history: deque[dict[str, Any]] = deque(maxlen=40)  # replayed to late-joining clients
        self.pending_confirm: dict[str, Any] | None = None  # so a (re)connecting HUD can still show the buttons
        self.pipeline = None  # set by the app once the voice pipeline exists
        self.on_shutdown: Callable[[], None] | None = None  # set by the app; lets `POST /shutdown` stop the process
        bus.on(None, self._on_event)

    # ---- bus → clients -------------------------------------------------------------------------
    def _on_event(self, ev: Event) -> None:
        """Sync handler (runs inside bus.emit). Only enqueues — one writer task per client keeps the order strict."""
        data = ev.to_json()
        if ev.type == "state":
            self.state = data["state"]
        elif ev.type == "confirm.request":
            self.pending_confirm = data
        elif ev.type == "confirm.resolved":
            self.pending_confirm = None
        if ev.type in HISTORY_TYPES:
            self.history.append(data)
        if not self.clients:
            return
        msg = json.dumps(data)
        for q in self.clients.values():
            if q.full() and ev.type in HIGH_RATE:
                continue  # client is behind: skip this level sample, never skip transcript/state/tool events
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def _writer(self, ws: WebSocket, q: asyncio.Queue[str]) -> None:
        while True:
            msg = await q.get()
            await ws.send_text(msg)

    def hello(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "state": self.state,
            "model": self.s.llm.primary,
            "user_title": self.s.assistant.user_title,
            "mode": self.s.ui.mode,
            "history": list(self.history),
            "pending_confirm": self.pending_confirm,
        }

    # ---- clients → pipeline --------------------------------------------------------------------
    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAX)
        q.put_nowait(json.dumps(self.hello()))
        self.clients[ws] = q
        writer = asyncio.create_task(self._writer(ws, q))
        logger.info("HUD connected ({} client(s))", len(self.clients))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    # Commands may take seconds (a whole spoken turn); never block this client's receive loop on them.
                    asyncio.create_task(self._handle_logged(json.loads(raw)))
                except Exception:  # noqa: BLE001
                    logger.exception("bad HUD message: {}", raw[:200])
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("HUD socket error: {}", e)
        finally:
            writer.cancel()
            self.clients.pop(ws, None)
            logger.info("HUD disconnected ({} left)", len(self.clients))

    async def _handle_logged(self, msg: dict[str, Any]) -> None:
        try:
            await self.handle(msg)
        except Exception:  # noqa: BLE001
            logger.exception("HUD command failed: {}", msg.get("type"))

    async def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        p = self.pipeline
        if p is None:
            logger.warning("HUD message {} before the pipeline is ready", kind)
            return
        if kind == "text":
            await p.submit_text(str(msg.get("text", "")))
        elif kind == "image":
            await p.submit_text(str(msg.get("text", "")), image_b64=msg.get("image_b64"), image_mime=msg.get("mime", "image/png"))
        elif kind == "confirm":
            p.resolve_confirm_external(bool(msg.get("accepted")))
        elif kind == "wake":
            await p.wake_external()
        elif kind == "interrupt":
            await p.interrupt()
        else:
            logger.debug("unknown HUD message type {}", kind)
