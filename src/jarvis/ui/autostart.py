"""Start J.A.R.V.I.S. with Windows: a shortcut in the user's Startup folder.

Why a Startup shortcut (and not a scheduled task or registry Run key): it needs no admin rights, it shows up in
Task Manager → Startup apps where you can toggle it like any other program, and removing it is deleting one file.

The shortcut (built by ui/shortcuts.py, like the Start Menu / Desktop ones) targets the venv's
`pythonw.exe -m jarvis run` — no console window; the tray icon is the UI and everything is logged to `logs/jarvis.log`
(the console sink is skipped when there is no stderr).
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ui.shortcuts import (
    SHORTCUT_NAME,
    create_shortcut,
    describe_shortcut,
    remove_shortcut,
    start_menu_dir,
)


def startup_dir() -> Path:
    return start_menu_dir() / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def enable(mode: str | None = None) -> Path:
    return create_shortcut(shortcut_path(), mode, description="J.A.R.V.I.S. voice assistant (starts at sign-in)")


def disable() -> bool:
    return remove_shortcut(shortcut_path())


def status() -> str:
    lnk = shortcut_path()
    if not lnk.exists():
        return f"off (no {lnk})"
    return "on → " + describe_shortcut(lnk).split(" → ", 1)[-1]
