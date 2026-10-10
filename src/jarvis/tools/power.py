"""Power actions. All but `cancel_shutdown` are tier `confirm` (see config/permissions.yaml)."""

from __future__ import annotations

import ctypes
import subprocess
from typing import Annotated

from loguru import logger
from pydantic import Field

from jarvis.tools.registry import ToolError, tool


def _shutdown(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["shutdown", *args], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False)


@tool(summary="Lock PC", confirm_prompt="Lock the PC now?")
def lock_pc() -> str:
    """Lock the workstation (Win+L)."""
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("Windows refused to lock the workstation.")
    return "Locked."


@tool(summary="Sleep PC", confirm_prompt="Put the PC to sleep?")
def sleep_pc() -> str:
    """Put the PC to sleep (suspend to RAM). Jarvis resumes listening when it wakes."""
    logger.info("sleeping PC")
    # SetSuspendState(bHibernate=False, bForce=True, bWakeupEventsDisabled=False) → sleep, not hibernate.
    ok = ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    if not ok:
        raise ToolError("Sleep request was refused.")
    return "Going to sleep."


@tool(summary="Shutdown in {delay_s}s", confirm_prompt="Shut down the PC in {delay_s} seconds?")
def shutdown_pc(delay_s: Annotated[int, Field(ge=0, le=86400, description="Seconds to wait first; 0 = immediately. 'in 10 minutes' = 600.")] = 10) -> str:
    """Shut the PC down, optionally after a delay. Can be undone with cancel_shutdown while the delay runs."""
    r = _shutdown(["/s", "/t", str(delay_s), "/c", "Shutdown requested via Jarvis"])
    if r.returncode:
        raise ToolError(r.stderr.strip() or r.stdout.strip() or "shutdown failed")
    return f"Shutting down in {delay_s} seconds." if delay_s else "Shutting down now."


@tool(summary="Restart in {delay_s}s", confirm_prompt="Restart the PC in {delay_s} seconds?")
def restart_pc(delay_s: Annotated[int, Field(ge=0, le=86400, description="Seconds to wait first; 0 = immediately")] = 10) -> str:
    """Restart the PC, optionally after a delay. Can be undone with cancel_shutdown while the delay runs."""
    r = _shutdown(["/r", "/t", str(delay_s), "/c", "Restart requested via Jarvis"])
    if r.returncode:
        raise ToolError(r.stderr.strip() or r.stdout.strip() or "restart failed")
    return f"Restarting in {delay_s} seconds." if delay_s else "Restarting now."


@tool(summary="Cancel shutdown")
def cancel_shutdown() -> str:
    """Abort a pending shutdown or restart."""
    r = _shutdown(["/a"])
    if r.returncode:
        raise ToolError("There is no pending shutdown to cancel.")
    return "Shutdown cancelled."
