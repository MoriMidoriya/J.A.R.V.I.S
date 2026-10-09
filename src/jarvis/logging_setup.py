"""Loguru configuration: console + rotating file.

Note: the `kokoro` package calls `logger.remove()` when imported, wiping every sink (console AND file). Anything that
imports it must call `reapply()` afterwards — see `jarvis.tts.kokoro`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

from loguru import logger

from jarvis.config import Settings

_current: tuple[Settings, str, str | None] | None = None
CONSOLE_FMT = "<green>{time:HH:mm:ss.SSS}</green> <level>{level: <7}</level> <cyan>{name}</cyan> {message}"


def setup_logging(settings: Settings, console_level: str | None = None, name: str | None = None) -> None:
    """Console (if there is one) + rotating file. `name` picks a separate file (logs/<name>.log) — the HUD window
    process uses "hud" so two processes never rotate the same file under each other."""
    global _current
    level = console_level or settings.logging.level
    _current = (settings, level, name)
    logger.remove()
    if sys.stderr is not None:  # None under pythonw.exe (Windows autostart) — file log only
        logger.add(sys.stderr, level=level, format=CONSOLE_FMT, enqueue=True)
    path = settings.log_path if name is None else settings.log_path.with_name(f"{name}.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(path, level="DEBUG", rotation="10 MB", retention=5, encoding="utf-8", enqueue=True)


def reapply() -> None:
    """Re-install our sinks after a third-party import reset loguru. No-op if logging was never configured."""
    if _current is not None:
        setup_logging(*_current)


@contextmanager
def sinks_frozen() -> Iterator[None]:
    """Temporarily make `logger.remove()` / `logger.add()` no-ops (for imports that reconfigure loguru globally)."""
    orig_remove, orig_add = logger.remove, logger.add
    logger.remove = lambda *a, **k: None  # type: ignore[method-assign]
    logger.add = lambda *a, **k: 0  # type: ignore[method-assign]
    try:
        yield
    finally:
        logger.remove, logger.add = orig_remove, orig_add
