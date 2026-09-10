"""Loguru configuration: console + rotating file."""

from __future__ import annotations

import sys

from loguru import logger

from jarvis.config import Settings


def setup_logging(settings: Settings, console_level: str | None = None) -> None:
    logger.remove()
    level = console_level or settings.logging.level
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss.SSS}</green> <level>{level: <7}</level> <cyan>{name}</cyan> {message}",
        enqueue=True,
    )
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        settings.log_path,
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        enqueue=True,
    )
