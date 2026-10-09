"""Polls the store once a second and emits `ReminderDue` for anything that has come due.

A poll loop (rather than APScheduler jobs) means reminders survive restarts for free — they live in SQLite — and
there is exactly one source of truth.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from loguru import logger

from jarvis.events import EventBus, ReminderDue
from jarvis.memory.store import get_store

MISSED_GRACE = timedelta(hours=12)  # reminders older than this when we boot are announced once as "missed"


class ReminderService:
    def __init__(self, bus: EventBus, poll_s: float = 1.0) -> None:
        self.bus = bus
        self.poll_s = poll_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="reminders")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        store = get_store()
        while True:
            try:
                now = datetime.now()
                for r in store.due_reminders(now):
                    store.mark_done(r.id)
                    missed = now - r.due_at > MISSED_GRACE
                    text = r.text if not missed else f"(missed while I was offline, due {r.due_at:%a %H:%M}) {r.text}"
                    logger.info("reminder due: #{} {} {!r}", r.id, r.kind, r.text)
                    await self.bus.emit(ReminderDue(id=r.id, text=text, kind=r.kind))  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("reminder poll failed")
            await asyncio.sleep(self.poll_s)
