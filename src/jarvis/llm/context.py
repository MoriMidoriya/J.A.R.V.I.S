"""Live context injected into the system prompt each turn (time, battery, active window, reminders)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import psutil
from loguru import logger

# Providers can register extra context lines (e.g. reminders in Phase 5).
_providers: list[Callable[[], str | None]] = []


def register_context_provider(fn: Callable[[], str | None]) -> None:
    _providers.append(fn)


def day_part(now: datetime | None = None) -> str:
    h = (now or datetime.now()).hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def active_window_title() -> str | None:
    try:
        import pygetwindow as gw

        w = gw.getActiveWindow()
        title = w.title if w else None
        return title.strip() if title else None
    except Exception:  # noqa: BLE001
        return None


def battery_line() -> str | None:
    b = psutil.sensors_battery()
    if not b:
        return None
    state = "charging" if b.power_plugged else "on battery"
    return f"Battery {int(b.percent)}% ({state})"


def build_stable_context() -> str:
    """Slow-changing facts → system prompt (keeps the LLM prompt cache valid across turns)."""
    now = datetime.now()
    return f"- Today: {now.strftime('%A %d %B %Y')}\n- Machine: Windows 11 laptop (ASUS ROG Zephyrus G14)"


def build_context() -> str:
    """Fast-changing facts → prepended to the user message each turn."""
    now = datetime.now()
    parts = [f"{now.strftime('%H:%M')} {day_part(now)}"]
    if bl := battery_line():
        parts.append(bl.lower())
    if title := active_window_title():
        parts.append(f"active window: {title[:100]}")
    for p in _providers:
        try:
            if s := p():
                parts.append(s)
        except Exception:  # noqa: BLE001
            logger.exception("context provider failed")
    return "; ".join(parts)
