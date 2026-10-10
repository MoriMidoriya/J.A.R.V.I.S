"""Launch / close installed applications (Start Menu, UWP, common Windows utilities)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any

import psutil
from loguru import logger
from pydantic import Field
from rapidfuzz import fuzz, process

from jarvis.config import DATA_DIR
from jarvis.tools.registry import ToolError, tool

INDEX_PATH = DATA_DIR / "apps_index.json"
INDEX_TTL_S = 6 * 3600

# Spoken name → canonical Start-Menu name or a direct launch target.
ALIASES: dict[str, str] = {
    "chrome": "Google Chrome",
    "google": "Google Chrome",
    "edge": "Microsoft Edge",
    "browser": "Microsoft Edge",
    "firefox": "Firefox",
    "code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "cursor": "Cursor",
    "terminal": "Terminal",
    "powershell": "Windows PowerShell",
    "cmd": "cmd:",
    "command prompt": "cmd:",
    "explorer": "explorer:",
    "file explorer": "explorer:",
    "files": "explorer:",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "task manager": "taskmgr:",
    "control panel": "control:",
    "calculator": "Calculator",
    "calc": "Calculator",
    "notepad": "Notepad",
    "paint": "Paint",
    "camera": "Camera",
    "photos": "Photos",
    "store": "Microsoft Store",
    "mail": "Mail",
    "outlook": "Outlook",
    "teams": "Microsoft Teams",
    "word": "Word",
    "excel": "Excel",
    "powerpoint": "PowerPoint",
    "spotify": "Spotify",
    "discord": "Discord",
    "steam": "Steam",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "vlc": "VLC media player",
    "obs": "OBS Studio",
    "snipping tool": "Snipping Tool",
}

DIRECT: dict[str, list[str]] = {
    "cmd:": ["cmd.exe"],
    "explorer:": ["explorer.exe"],
    "ms-settings:": ["explorer.exe", "ms-settings:"],
    "taskmgr:": ["taskmgr.exe"],
    "control:": ["control.exe"],
}

# Process-name hints for close_app when the app name differs from the exe.
PROCESS_HINTS: dict[str, list[str]] = {
    "google chrome": ["chrome"],
    "microsoft edge": ["msedge"],
    "visual studio code": ["Code"],
    "windows powershell": ["powershell"],
    "terminal": ["WindowsTerminal"],
    "file explorer": ["explorer"],
    "vlc media player": ["vlc"],
    "microsoft teams": ["ms-teams", "Teams"],
    "spotify": ["Spotify"],
    "discord": ["Discord"],
    "steam": ["steam"],
    "whatsapp": ["WhatsApp"],
    "telegram": ["Telegram"],
    "obs studio": ["obs64"],
    "cursor": ["Cursor"],
}


def _load_index(force: bool = False) -> list[dict[str, str]]:
    if not force and INDEX_PATH.exists() and time.time() - INDEX_PATH.stat().st_mtime < INDEX_TTL_S:
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    apps = _scan_start_apps()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(apps, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("indexed {} apps", len(apps))
    return apps


def _scan_start_apps() -> list[dict[str, str]]:
    """`Get-StartApps` lists every app pinned into the Start Menu: desktop, UWP, Store — with a launchable AppID."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW, check=False).stdout
        data = json.loads(out) if out.strip() else []
        if isinstance(data, dict):
            data = [data]
    except Exception as e:  # noqa: BLE001
        logger.warning("Get-StartApps failed: {}", e)
        data = []
    apps: list[dict[str, str]] = []
    seen: set[str] = set()
    for d in data:
        n, i = d.get("Name"), d.get("AppID")
        if n and i and n.lower() not in seen:
            seen.add(n.lower())
            apps.append({"name": n, "id": i})

    # Also scan .lnk shortcuts (catches apps not surfaced by Get-StartApps).
    for root in (
        Path(os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")),
        Path(os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")),
    ):
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            n = lnk.stem
            if n.lower() in seen or n.lower().startswith(("uninstall", "readme", "help")):
                continue
            seen.add(n.lower())
            apps.append({"name": n, "id": str(lnk)})
    return apps


def resolve_app(name: str) -> tuple[str, str, float] | None:
    """Return (display_name, launch_target, score) or None."""
    q = name.strip().lower()
    if q in ALIASES:
        target = ALIASES[q]
        if target in DIRECT:
            return (q.title(), target, 100.0)
        q = target.lower()
    apps = _load_index()
    names = [a["name"] for a in apps]
    match = process.extractOne(q, names, scorer=fuzz.WRatio, processor=str.lower)
    if not match:
        return None
    best_name, score, idx = match
    # Prefer exact/prefix matches when several apps share words (e.g. "Word" vs "WordPad").
    for a in apps:
        if a["name"].lower() == q:
            return (a["name"], a["id"], 100.0)
    return (best_name, apps[idx]["id"], float(score))


def _launch(target: str) -> None:
    if target in DIRECT:
        subprocess.Popen(DIRECT[target], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        return
    if target.lower().endswith(".lnk") or os.path.exists(target):
        os.startfile(target)
        return
    # AppID from Get-StartApps → shell:AppsFolder works for UWP and desktop apps alike.
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


@tool(summary="Open {name}")
def open_app(name: Annotated[str, Field(description="Application name as the user said it, e.g. 'Spotify', 'Chrome', 'settings'")]) -> str:
    """Launch an installed application, Store app, or Windows utility by name. Use search_files + open_path for files."""
    r = resolve_app(name)
    if r is None or r[2] < 60:
        best = f" Closest match was '{r[0]}'." if r else ""
        raise ToolError(f"No installed app matching '{name}'.{best}")
    display, target, score = r
    _launch(target)
    note = "" if score >= 85 else f" (matched from '{name}')"
    return f"Launched {display}{note}."


@tool(summary="Close {name}", confirm_prompt="Close {name}? Unsaved work may be lost.")
def close_app(name: Annotated[str, Field(description="Application name, e.g. 'Chrome'")]) -> str:
    """Close a running application gracefully (terminates all its processes)."""
    q = name.strip().lower()
    canon = ALIASES.get(q, name).lower()
    hints = {h.lower() for h in PROCESS_HINTS.get(canon, [])} | {q, canon, q.replace(" ", ""), canon.replace(" ", "")}
    killed: list[str] = []
    for p in psutil.process_iter(["name", "pid"]):
        pn = (p.info["name"] or "").lower().removesuffix(".exe")
        if pn and (pn in hints or any(fuzz.ratio(pn, h) >= 90 for h in hints)):
            try:
                p.terminate()
                killed.append(pn)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    if not killed:
        raise ToolError(f"'{name}' does not appear to be running.")
    return f"Closed {name} ({len(killed)} process{'es' if len(killed) != 1 else ''})."


@tool(summary="List installed apps")
def list_apps(filter: Annotated[str, Field(description="Optional substring to filter app names")] = "") -> list[str]:
    """List installed application names (optionally filtered). Use only if the user asks what is installed."""
    apps = _load_index()
    f = filter.lower()
    return sorted(a["name"] for a in apps if f in a["name"].lower())[:60]


def refresh_index() -> int:
    return len(_load_index(force=True))


def _debug() -> list[dict[str, Any]]:  # pragma: no cover
    return _load_index()
