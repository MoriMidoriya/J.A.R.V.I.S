"""Fire-and-forget asyncio tasks done right: referenced until they finish, failures logged.

`loop.create_task(coro)` with the result thrown away is a known trap — the loop only holds a *weak* reference, so a
task that is mid-await can be garbage-collected and silently vanish; and under pythonw an unhandled exception in such a
task has nowhere to go. Everything here used to be three slightly different copies of the same pattern.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task[Any]:
    """Schedule `coro` on the running loop; keep a reference until it is done; log if it fails."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_done)
    return task


def _done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.opt(exception=task.exception()).error("background task {} failed", task.get_name())
