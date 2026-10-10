"""Jarvis controlling his own display and his own power — by voice.

    "Hey Jarvis, hide yourself"            → hud_display(hide)      the overlay disappears; he keeps listening
    "show yourself" / "come back"          → hud_display(show)
    "show the full interface"              → hud_display(cinematic)
    "back to the corner"                   → hud_display(compact)
    "power down" / "shut yourself down"    → quit_jarvis            tier `confirm`: he asks first, says goodbye, then quits

Both only *emit* a `UiCommand`; the Qt shell (through the HUD page's bridge, see ui/window.py) is what actually hides,
shows, switches mode or quits. `quit_jarvis` additionally stops the backend itself when no window is attached (browser-tab
or `--no-ui` use), after the farewell has been spoken.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Annotated, Literal

from loguru import logger
from pydantic import Field

from jarvis.events import Event, UiCommand
from jarvis.tools.registry import ToolError, registry, tool

_stop_backend: Callable[[], None] | None = None
_window_attached: Callable[[], bool] = lambda: False


def set_hooks(stop_backend: Callable[[], None], window_attached: Callable[[], bool]) -> None:
    """Wired by the app: how to stop this process, and whether the Qt window is connected (then *it* quits us)."""
    global _stop_backend, _window_attached
    _stop_backend = stop_backend
    _window_attached = window_attached


Action = Literal["show", "hide", "compact", "panel", "cinematic"]


@tool(summary="HUD: {action}")
async def hud_display(
    action: Annotated[
        Action,
        Field(
            description="hide = make the overlay disappear (Jarvis keeps listening); show = bring it back; compact = small reactor "
            "in the corner; panel = console with the transcript and a text box; cinematic = the full-screen interface with "
            "all instruments ('full interface', 'full screen', 'show me everything')."
        ),
    ],
) -> str:
    """Control Jarvis' OWN on-screen display (the arc-reactor HUD overlay) — not the PC's screen or other windows.
    Use when the user talks about Jarvis' display/interface/HUD/reactor: hide it, show it, go full screen, back to the corner."""
    bus = registry.bus
    if bus is None:
        raise ToolError("The HUD is not connected.")
    if action in ("show", "hide"):
        await bus.emit(UiCommand(action=action))
        return "The HUD is hidden; I am still listening." if action == "hide" else "The HUD is back."
    await bus.emit(UiCommand(action="mode", mode=action))
    return {"compact": "Back in the corner.", "panel": "Panel open.", "cinematic": "Full interface up."}[action]


@tool(summary="Shut down Jarvis", confirm_prompt="Shut me down, sir — the assistant, not the PC?", speak_result=True)
async def quit_jarvis() -> str:
    """Quit J.A.R.V.I.S. itself — the assistant program and its HUD — NOT the computer. Use for 'shut yourself down',
    'power down', 'go offline', 'quit', 'exit', 'that's all for today, you can switch off'. For the PC use shutdown_pc.
    You MUST call this tool to power down — saying goodbye without calling it does nothing. It asks for confirmation."""
    bus = registry.bus
    if bus is None:
        raise ToolError("Cannot reach the HUD bus.")
    asyncio.get_running_loop().create_task(_shutdown_after_farewell(bus))
    return "Powering down, sir. Goodbye."


async def _shutdown_after_farewell(bus) -> None:
    """Let the farewell be spoken, then quit through the window if there is one, else stop the backend directly.

    The turn that called us goes THINKING → SPEAKING (the farewell) → LISTENING/IDLE; by the time this task first runs
    the SPEAKING transition has usually already happened, so wait for the first *post-speech* state rather than for a
    speaking→x edge."""
    done = asyncio.Event()

    def on_state(ev: Event) -> None:
        if getattr(ev, "state", None) in ("listening", "idle", "error"):
            done.set()

    bus.on("state", on_state)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(done.wait(), timeout=15.0)
    finally:
        bus.off("state", on_state)
    await asyncio.sleep(0.3)
    await bus.emit(UiCommand(action="shutdown"))
    if _window_attached():
        logger.info("quit requested by voice; the HUD window will stop us")
        return
    await asyncio.sleep(0.5)
    logger.info("quit requested by voice; no window attached — stopping the backend")
    if _stop_backend is not None:
        _stop_backend()
