"""Windows shortcuts for starting J.A.R.V.I.S. without a terminal: Start Menu, Desktop, and the Startup folder
(autostart — see ui/autostart.py, which builds on this).

All of them are ordinary .lnk files pointing at the venv's `pythonw.exe -m jarvis run` (no console window; the tray
icon is the UI and everything is logged to logs/). WScript.Shell is the only shortcut API Windows ships; we drive it
through PowerShell to avoid a pywin32 dependency. The icon is assets/jarvis.ico, drawn with Pillow on first use.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

from jarvis.config import ROOT

ICON_PATH = ROOT / "assets" / "jarvis.ico"
SHORTCUT_NAME = "JARVIS.lnk"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def start_menu_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    # OneDrive may redirect the Desktop; ask the shell rather than guessing ~/Desktop
    with contextlib.suppress(Exception):
        out = _powershell("[Environment]::GetFolderPath('Desktop')").strip()
        if out:
            return Path(out)
    return Path.home() / "Desktop"


def pythonw() -> Path:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def _powershell(script: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, capture_output=True, text=True, timeout=30).stdout


def _q(s: object) -> str:
    """Single-quote for PowerShell (the only escape inside '...' is doubling the quote)."""
    return "'" + str(s).replace("'", "''") + "'"


# ---- icon -------------------------------------------------------------------------------------
def draw_icon(size: int = 256, color: tuple[int, int, int] = (52, 228, 245)):
    """The arc reactor as an RGBA image: three rings, ten segments, a bright core — same look as the HUD."""
    import math

    from PIL import Image, ImageDraw, ImageFilter

    ss = 4  # supersample for smooth edges
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n / 2
    r, g, b = color

    def ring(radius: float, width: float, alpha: int = 255) -> None:
        d.ellipse([c - radius, c - radius, c + radius, c + radius], outline=(r, g, b, alpha), width=max(1, round(width)))

    # dark disc so the icon reads on light and dark backgrounds
    d.ellipse([c - 0.49 * n, c - 0.49 * n, c + 0.49 * n, c + 0.49 * n], fill=(4, 14, 24, 235))
    # outer glow
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([c - 0.42 * n, c - 0.42 * n, c + 0.42 * n, c + 0.42 * n], outline=(r, g, b, 160), width=round(0.06 * n))
    glow = glow.filter(ImageFilter.GaussianBlur(0.04 * n))
    img.alpha_composite(glow)
    ring(0.43 * n, 0.018 * n)
    ring(0.355 * n, 0.012 * n, 200)
    # ten segments between the middle and inner rings
    for i in range(10):
        a0 = i * 36 + 4
        a1 = a0 + 28
        box = [c - 0.31 * n, c - 0.31 * n, c + 0.31 * n, c + 0.31 * n]
        d.arc(box, a0, a1, fill=(r, g, b, 255), width=round(0.06 * n))
    ring(0.2 * n, 0.012 * n, 220)
    # spokes
    for i in range(3):
        a = math.radians(i * 120 + 90)
        x0, y0 = c + 0.2 * n * math.cos(a), c + 0.2 * n * math.sin(a)
        x1, y1 = c + 0.27 * n * math.cos(a), c + 0.27 * n * math.sin(a)
        d.line([x0, y0, x1, y1], fill=(r, g, b, 255), width=round(0.014 * n))
    # core
    core = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    cd.ellipse([c - 0.16 * n, c - 0.16 * n, c + 0.16 * n, c + 0.16 * n], fill=(r, g, b, 150))
    core = core.filter(ImageFilter.GaussianBlur(0.03 * n))
    img.alpha_composite(core)
    d.ellipse([c - 0.1 * n, c - 0.1 * n, c + 0.1 * n, c + 0.1 * n], fill=(236, 252, 255, 255))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def ensure_icon(path: Path = ICON_PATH) -> Path:
    """Write assets/jarvis.ico (multi-size) if it does not exist yet."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    img = draw_icon(256)
    img.save(path, format="ICO", sizes=[(s, s) for s in ICON_SIZES])
    return path


# ---- shortcuts --------------------------------------------------------------------------------
def create_shortcut(lnk: Path, mode: str | None = None, description: str = "J.A.R.V.I.S. voice assistant") -> Path:
    """Create/overwrite a .lnk that starts Jarvis (no console window), with the reactor icon."""
    args = "-m jarvis run" + (f" --mode {mode}" if mode else "")
    lnk.parent.mkdir(parents=True, exist_ok=True)
    try:
        icon = ensure_icon()
    except Exception:  # noqa: BLE001 — a missing icon must never block creating the shortcut
        icon = None
    ps = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_q(lnk)}); "
        f"$s.TargetPath = {_q(pythonw())}; $s.Arguments = {_q(args)}; $s.WorkingDirectory = {_q(ROOT)}; "
        f"$s.WindowStyle = 7; $s.Description = {_q(description)}; "
        + (f"$s.IconLocation = {_q(f'{icon},0')}; " if icon else "")
        + "$s.Save()"
    )
    _powershell(ps)
    return lnk


def remove_shortcut(lnk: Path) -> bool:
    if lnk.exists():
        lnk.unlink()
        return True
    return False


def describe_shortcut(lnk: Path) -> str:
    if not lnk.exists():
        return f"missing ({lnk})"
    ps = f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_q(lnk)}); $s.TargetPath + ' ' + $s.Arguments"
    try:
        out = _powershell(ps).strip()
    except Exception:  # noqa: BLE001
        out = "?"
    return f"{lnk} → {out}"


def start_menu_shortcut() -> Path:
    return start_menu_dir() / SHORTCUT_NAME


def desktop_shortcut() -> Path:
    return desktop_dir() / SHORTCUT_NAME
