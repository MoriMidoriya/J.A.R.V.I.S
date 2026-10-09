"""Reminders and timers — stored in SQLite, announced by voice when due (see memory/scheduler.py).

Time parsing is done here in code rather than trusting the model with arithmetic: it passes the user's words
("in 20 minutes", "at 7 pm", "tomorrow at 9") and we compute the datetime.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import Field

from jarvis.llm.context import register_context_provider
from jarvis.memory.store import Reminder, get_store
from jarvis.tools.registry import ToolError, tool

_UNIT_S = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "d": 86400, "day": 86400, "w": 604800, "week": 604800}
_DUR_RX = re.compile(r"(\d+(?:\.\d+)?|an?|half an?|a half|quarter of an?)\s*(seconds?|secs?|s\b|minutes?|mins?|m\b|hours?|hrs?|h\b|days?|d\b|weeks?|w\b)", re.IGNORECASE)
_TIME_RX = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|o'?clock)?\b", re.IGNORECASE)


def parse_duration(text: str) -> timedelta | None:
    """'10 minutes', '1 hour 30 minutes', 'an hour and a half', '90 sec' → timedelta."""
    total = 0.0
    t = text.lower().replace("and a half", "30 min").replace("half an hour", "30 min").replace("a quarter of an hour", "15 min")
    for num, unit in _DUR_RX.findall(t):
        n = 1.0 if num.strip() in {"a", "an"} else 0.5 if "half" in num else 0.25 if "quarter" in num else float(num)
        key = unit.lower().rstrip(".").rstrip("s") if len(unit) > 1 else unit.lower()
        key = {"sec": "sec", "min": "min", "hr": "hr"}.get(key, key)
        total += n * _UNIT_S.get(key, _UNIT_S.get(key[:1], 60))
    return timedelta(seconds=total) if total > 0 else None


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Natural-language time → datetime. Returns None if not understood."""
    now = now or datetime.now()
    t = text.strip().lower()
    if not t:
        return None
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        pass
    if t.startswith("in ") or re.match(r"^\d+\s*(s|sec|min|m|h|hr|hour)", t):
        d = parse_duration(t)
        return now + d if d else None

    day_offset = 0
    if "day after tomorrow" in t:
        day_offset = 2
    elif "tomorrow" in t:
        day_offset = 1
    base = (now + timedelta(days=day_offset)).replace(second=0, microsecond=0)

    if "noon" in t or "midday" in t:
        cand = base.replace(hour=12, minute=0)
    elif "midnight" in t:
        cand = base.replace(hour=0, minute=0) + timedelta(days=1)
    else:
        m = _TIME_RX.search(re.sub(r"\btomorrow\b|\btoday\b|\btonight\b|\bthis (morning|afternoon|evening)\b", " ", t))
        if not m:
            if day_offset:  # "tomorrow" alone
                return base.replace(hour=9, minute=0)
            if "tonight" in t or "this evening" in t:
                return base.replace(hour=20, minute=0)
            if "this afternoon" in t:
                return base.replace(hour=15, minute=0)
            if "this morning" in t:
                return base.replace(hour=9, minute=0)
            return None
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        suffix = (m.group(3) or "").replace(".", "").lower()
        if hour > 23 or minute > 59:
            return None
        if suffix.startswith("p") and hour < 12:
            hour += 12
        elif suffix.startswith("a") and hour == 12:
            hour = 0
        elif not suffix and hour <= 12:
            # Ambiguous "at 7": honour hints, else pick whichever of 07:00 / 19:00 comes up sooner.
            if "tonight" in t or "evening" in t or "afternoon" in t:
                hour = hour + 12 if hour < 12 else hour
            elif "morning" in t:
                pass
            elif day_offset == 0:
                options = []
                for h in (hour, hour + 12):
                    if h <= 23:
                        c = base.replace(hour=h, minute=minute)
                        options.append(c if c > now else c + timedelta(days=1))
                return min(options)
        cand = base.replace(hour=hour, minute=minute)
    if cand <= now and day_offset == 0:
        cand += timedelta(days=1)
    return cand


def _reminders_context() -> str | None:
    pend = get_store().pending_reminders()
    if not pend:
        return None
    nxt = pend[:3]
    return "pending reminders: " + ", ".join(f"'{r.text}' {r.human_due()}" for r in nxt) + (f" (+{len(pend) - 3} more)" if len(pend) > 3 else "")


register_context_provider(_reminders_context)


def _fmt(r: Reminder) -> str:
    return f"#{r.id} {r.kind}: {r.text} — {r.human_due()} ({r.due_at:%a %d %b %H:%M})"


@tool(summary="Remind: {text} ({when})")
def set_reminder(
    text: Annotated[str, Field(description="What to remind about, phrased for later: 'call mum', 'take the laundry out'")],
    when: Annotated[str, Field(description="When, in the user's words: 'in 20 minutes', 'at 7 pm', 'tomorrow at 9', '18:30', 'tonight'")],
) -> str:
    """Set a reminder; Jarvis will say it aloud when due. For plain countdowns use set_timer."""
    due = parse_when(when)
    if not due:
        raise ToolError(f"I couldn't understand the time '{when}'. Try 'in 10 minutes' or 'at 7 pm'.")
    if due <= datetime.now():
        raise ToolError(f"'{when}' is in the past.")
    r = get_store().add_reminder(text, due, "reminder")
    return f"Reminder #{r.id} set for {r.human_due()} ({due:%a %H:%M}): {r.text}"


@tool(summary="Timer: {duration}")
def set_timer(
    duration: Annotated[str, Field(description="Length of the timer: '10 minutes', '1 hour 30', '45 seconds'")],
    label: Annotated[str, Field(description="What the timer is for, e.g. 'pasta' (optional)")] = "",
) -> str:
    """Start a countdown timer; Jarvis announces when it ends."""
    d = parse_duration(duration)
    if not d or d.total_seconds() < 1:
        raise ToolError(f"I couldn't understand the duration '{duration}'.")
    r = get_store().add_reminder(label or "timer", datetime.now() + d, "timer")
    mins = d.total_seconds() / 60
    human = f"{round(d.total_seconds())} seconds" if mins < 1 else f"{mins:g} minutes" if mins < 60 else f"{mins / 60:g} hours"
    return f"Timer #{r.id} set for {human}" + (f" ({label})" if label else "") + "."


@tool(summary="List reminders")
def list_reminders() -> list[str]:
    """List pending reminders and timers."""
    pend = get_store().pending_reminders()
    return [_fmt(r) for r in pend] or ["No pending reminders or timers."]


@tool(summary="Cancel reminder {which}")
def cancel_reminder(which: Annotated[str, Field(description="Reminder id like '#3', 'all', or words from its text")]) -> str:
    """Cancel a pending reminder or timer."""
    st = get_store()
    pend = st.pending_reminders()
    if not pend:
        raise ToolError("There are no pending reminders.")
    w = which.strip().lower().lstrip("#")
    if w == "all":
        for r in pend:
            st.cancel_reminder(r.id)
        return f"Cancelled {len(pend)} reminder(s)."
    if w.isdigit():
        if st.cancel_reminder(int(w)):
            return f"Cancelled #{w}."
        raise ToolError(f"No pending reminder #{w}.")
    from rapidfuzz import fuzz

    # Blend full-string and partial similarity so "test reminder" prefers "test reminder" over a timer called "test".
    def score(r: Reminder) -> float:
        return 0.5 * fuzz.ratio(w, r.text.lower()) + 0.5 * fuzz.partial_ratio(w, r.text.lower())

    best = max(pend, key=score)
    if score(best) < 55:
        if len(pend) == 1:
            best = pend[0]
        else:
            raise ToolError("Which one? " + "; ".join(_fmt(r) for r in pend))
    st.cancel_reminder(best.id)
    return f"Cancelled: {best.text}"
