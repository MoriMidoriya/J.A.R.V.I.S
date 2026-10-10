"""SQLite-backed store. One short-lived connection per call keeps it trivially thread-safe (tools run in worker
threads, the reminder scheduler on the event loop)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from jarvis.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'reminder',
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(done_at, due_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT
);
"""


@dataclass
class Fact:
    id: int
    text: str
    created_at: datetime


@dataclass
class Reminder:
    id: int
    text: str
    kind: str  # "reminder" | "timer"
    due_at: datetime
    created_at: datetime
    done_at: datetime | None = None

    def human_due(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        delta = (self.due_at - now).total_seconds()
        if abs(delta) < 60:
            return "now"
        if 0 < delta < 3600:
            return f"in {round(delta / 60)} min"
        if self.due_at.date() == now.date():
            return self.due_at.strftime("at %I:%M %p").replace(" 0", " ")
        return self.due_at.strftime("%a %d %b at %I:%M %p")


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # ---- facts ---------------------------------------------------------------------------------
    def add_fact(self, text: str) -> Fact:
        now = datetime.now()
        with self._conn() as c:
            cur = c.execute("INSERT INTO facts(text, created_at) VALUES (?, ?)", (text.strip(), now.isoformat()))
            return Fact(cur.lastrowid, text.strip(), now)

    def list_facts(self, limit: int = 50) -> list[Fact]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Fact(r["id"], r["text"], _dt(r["created_at"])) for r in rows]

    def search_facts(self, query: str, limit: int = 8) -> list[Fact]:
        facts = self.list_facts(500)
        q = query.lower()
        scored = sorted(facts, key=lambda f: -max(fuzz.partial_ratio(q, f.text.lower()), fuzz.token_set_ratio(q, f.text.lower())))
        return [f for f in scored[:limit] if fuzz.partial_ratio(q, f.text.lower()) >= 55]

    def delete_fact(self, fact_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    # ---- reminders -----------------------------------------------------------------------------
    def add_reminder(self, text: str, due_at: datetime, kind: str = "reminder") -> Reminder:
        now = datetime.now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO reminders(text, kind, due_at, created_at) VALUES (?, ?, ?, ?)",
                (text.strip(), kind, due_at.replace(microsecond=0).isoformat(), now.isoformat()),
            )
            return Reminder(cur.lastrowid, text.strip(), kind, due_at.replace(microsecond=0), now)

    def _rows_to_reminders(self, rows: list[sqlite3.Row]) -> list[Reminder]:
        return [Reminder(r["id"], r["text"], r["kind"], _dt(r["due_at"]), _dt(r["created_at"]), _dt(r["done_at"])) for r in rows]

    def pending_reminders(self) -> list[Reminder]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM reminders WHERE done_at IS NULL ORDER BY due_at").fetchall()
        return self._rows_to_reminders(rows)

    def due_reminders(self, now: datetime | None = None) -> list[Reminder]:
        now = now or datetime.now()
        with self._conn() as c:
            rows = c.execute("SELECT * FROM reminders WHERE done_at IS NULL AND due_at <= ? ORDER BY due_at", (now.isoformat(),)).fetchall()
        return self._rows_to_reminders(rows)

    def mark_done(self, reminder_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE reminders SET done_at = ? WHERE id = ?", (datetime.now().isoformat(), reminder_id))

    def cancel_reminder(self, reminder_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM reminders WHERE id = ? AND done_at IS NULL", (reminder_id,)).rowcount > 0

    # ---- conversation log ----------------------------------------------------------------------
    def log_message(self, role: str, text: str, meta: str | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO messages(ts, role, text, meta) VALUES (?, ?, ?, ?)", (datetime.now().isoformat(), role, text, meta))

    def recent_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT ts, role, text FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store(get_settings().db_path)
        return _store
