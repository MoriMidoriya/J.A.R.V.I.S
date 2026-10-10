"""PowerShell execution — tier `dangerous`: always confirmed, always written to logs/audit.log.

Disable entirely with `tools.allow_shell: false` (the tool then isn't even shown to the model).
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Annotated

from loguru import logger
from pydantic import Field

from jarvis.config import LOGS_DIR
from jarvis.tools.registry import ToolError, tool

AUDIT = LOGS_DIR / "audit.log"
MAX_OUT = 4000


def _audit(command: str, rc: int | None, out: str) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} rc={rc} cmd={command!r}\n{out[:1000]}\n---\n")


@tool(summary="PowerShell: {command}", confirm_prompt="Run this PowerShell command: {command}?")
def run_powershell(
    command: Annotated[str, Field(description="PowerShell command line to run, e.g. 'Get-Process | Sort CPU -desc | Select -First 5'")],
    timeout_s: Annotated[int, Field(ge=1, le=300, description="Kill after this many seconds")] = 30,
) -> str:
    """Run a PowerShell command and return its output. Only for things no other tool can do; the user must confirm."""
    logger.warning("run_powershell: {}", command)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=timeout_s, creationflags=subprocess.CREATE_NO_WINDOW, encoding="utf-8", errors="replace", check=False,
        )
    except subprocess.TimeoutExpired:
        _audit(command, None, "TIMEOUT")
        raise ToolError(f"The command did not finish within {timeout_s} seconds and was stopped.") from None
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    _audit(command, r.returncode, out or err)
    if r.returncode and not out:
        raise ToolError(f"Exit code {r.returncode}: {err[:500] or 'no output'}")
    text = out or "(no output)"
    if err:
        text += f"\n[stderr] {err[:500]}"
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + f"\n… [{len(text)} chars total]"
