"""Start J.A.R.V.I.S. with Windows: a shortcut in the user's Startup folder.

Why a Startup shortcut (and not a scheduled task or registry Run key): it needs no admin rights, it shows up in
Task Manager → Startup apps where you can toggle it like any other program, and removing it is deleting one file.

The shortcut targets the venv's `pythonw.exe -m jarvis run` — no console window; the tray icon is the UI and
everything is logged to `logs/jarvis.log` (the console sink is skipped when there is no stderr).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from jarvis.config import ROOT

SHORTCUT_NAME = "JARVIS.lnk"


def startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def _pythonw() -> Path:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def enable(mode: str | None = None) -> Path:
    args = "-m jarvis run" + (f" --mode {mode}" if mode else "")
    target = _pythonw()
    lnk = shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    # WScript.Shell is the only shortcut API Windows ships; drive it through PowerShell to avoid a pywin32 dependency.
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
        "$s.TargetPath = '{target}'; $s.Arguments = '{args}'; $s.WorkingDirectory = '{root}'; "
        "$s.WindowStyle = 7; $s.Description = 'J.A.R.V.I.S. voice assistant'; $s.Save()"
    ).format(lnk=lnk, target=target, args=args, root=ROOT)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, capture_output=True, text=True, timeout=30)
    return lnk


def disable() -> bool:
    lnk = shortcut_path()
    if lnk.exists():
        lnk.unlink()
        return True
    return False


def status() -> str:
    lnk = shortcut_path()
    if not lnk.exists():
        return f"off (no {lnk})"
    ps = f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); $s.TargetPath + ' ' + $s.Arguments"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        out = "?"
    return f"on → {out}"
