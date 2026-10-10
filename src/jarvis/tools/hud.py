"""Jarvis controlling his own display, his own settings and his own power — by voice.

    "Hey Jarvis, hide yourself"            → hud_display(hide)      the overlay disappears; he keeps listening
    "show yourself" / "come back"          → hud_display(show)
    "show the full interface"              → hud_display(cinematic)
    "back to the corner"                   → hud_display(compact)
    "text mode" / "stop talking"           → text_mode(True)        replies on screen only, nothing spoken (he still listens)
    "speak again" / "voice mode"           → text_mode(False)
    "move to the top left", "bigger",
    "hide the clock", "auto-hide on"…      → hud_settings(...)      the same preferences as the settings drawer
    "power down" / "shut yourself down"    → quit_jarvis            tier `confirm`: he asks first, says goodbye, then quits

hud_display and quit_jarvis only *emit* a `UiCommand`; the Qt shell (through the HUD page's bridge, see ui/window.py) is
what actually hides, shows, switches mode or quits. `quit_jarvis` additionally stops the backend itself when no window is
attached (browser-tab or `--no-ui` use), after the farewell has been spoken. text_mode / hud_settings go through the
hub's `set_prefs` — exactly what the settings drawer does — so they are validated, persisted in data/ui.json and
broadcast to every HUD.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import Field, ValidationError

from jarvis.config import TILES, Corner, UICfg
from jarvis.events import Event, UiCommand
from jarvis.tools.registry import ToolError, registry, tool

_stop_backend: Callable[[], None] | None = None
_window_attached: Callable[[], bool] = lambda: False
_set_prefs: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
_current_prefs: Callable[[], UICfg] | None = None


def set_hooks(
    stop_backend: Callable[[], None],
    window_attached: Callable[[], bool],
    set_prefs: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    current_prefs: Callable[[], UICfg],
) -> None:
    """Wired by the app: how to stop this process, whether the Qt window is connected (then *it* quits us), and the
    hub's prefs setter/getter (the same path the settings drawer uses)."""
    global _stop_backend, _window_attached, _set_prefs, _current_prefs
    _stop_backend = stop_backend
    _window_attached = window_attached
    _set_prefs = set_prefs
    _current_prefs = current_prefs


async def _apply(patch: dict[str, Any]) -> None:
    if _set_prefs is None:
        raise ToolError("Settings are not available right now.")
    try:
        await _set_prefs(patch)
    except ValidationError as e:
        msg = e.errors()[0].get("msg", "invalid value") if e.errors() else "invalid value"
        raise ToolError(f"I can't set that: {msg}.") from None


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


@tool(summary="Text-only mode", speak_result=True)
async def text_mode(
    enabled: Annotated[
        bool,
        Field(description="true = stop speaking: replies appear as text on the HUD only (Jarvis still listens); false = speak again"),
    ],
) -> str:
    """Switch Jarvis' OWN voice output off or on — text-only mode. Use for 'text mode', 'text only', 'stop talking',
    'go silent', 'be quiet from now on', 'mute yourself' (enabled=true) and 'speak again', 'voice mode', 'unmute yourself',
    'talk to me again' (enabled=false). This is about Jarvis speaking, NOT the PC's volume — for that use the volume tools."""
    await _apply({"text_only": enabled})
    return "Text-only mode, sir — I'll keep my replies on screen." if enabled else "Voice back on, sir."


TILE_NAMES = {"wave": "audio", "spectrum": "spectrum", "system": "system gauges", "radar": "telemetry", "clock": "clock", "globe": "globe", "pipeline": "pipeline"}
# What people call the instruments → tile id (the 8B model also likes to pass "the clock widget", so match by word).
TILE_WORDS = {
    "wave": ("wave", "waveform", "audio", "oscilloscope", "sound"),
    "spectrum": ("spectrum", "analyzer", "analyser", "frequencies", "frequency"),
    "system": ("system", "gauges", "gauge", "cpu", "memory", "vram", "gpu", "stats"),
    "radar": ("radar", "telemetry", "sweep"),
    "clock": ("clock", "time", "date"),
    "globe": ("globe", "earth", "world", "map", "planet"),
    "pipeline": ("pipeline", "latency", "latencies", "timings", "timing"),
}
SIZE_STEP = 60
BRIGHTNESS_STEP = 15


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _parse_corner(text: str) -> Corner | None:
    """'top-left', 'top left', 'the upper left corner', 'lower right' → a Corner; None if it isn't one."""
    w = _words(text)
    v = "top" if w & {"top", "upper"} else "bottom" if w & {"bottom", "lower"} else None
    h = "left" if "left" in w else "right" if "right" in w else None
    return f"{v}-{h}" if v and h else None  # type: ignore[return-value]


def _parse_adjust(text: str) -> set[str]:
    w = _words(text)
    out: set[str] = set()
    if w & {"bigger", "larger", "big", "large", "enlarge", "grow", "increase", "huge"}:
        out.add("bigger")
    if w & {"smaller", "shrink", "small", "tiny", "reduce", "decrease"}:
        out.add("smaller")
    if w & {"brighter", "bright", "brighten"}:
        out.add("brighter")
    if w & {"dimmer", "dim", "darker", "dark", "fainter", "faint"}:
        out.add("dimmer")
    return out


def _parse_tiles(value: list[str] | str | None) -> list[str]:
    if not value:
        return []
    items = [value] if isinstance(value, str) else [str(v) for v in value]
    found: list[str] = []
    for item in items:
        w = _words(item)
        hits = [t for t, names in TILE_WORDS.items() if w & set(names)]
        if not hits:
            raise ToolError(f"I don't have an instrument called '{item}'. The instruments are: {', '.join(TILE_NAMES.values())}.")
        found += [h for h in hits if h not in found]
    return found


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _onoff(v: bool) -> str:
    return "on" if v else "off"


def _describe(cur: UICfg) -> str:
    off = [TILE_NAMES[t] for t in TILES if not cur.tiles.get(t, True)]
    inst = "off" if not cur.widgets else ("all on" if not off else f"on except {_join(off)}")
    return (
        f"Reactor in the {cur.corner.replace('-', ' ')}, {cur.reactor_size} pixels, idle brightness {round(cur.idle_opacity * 100)} percent; "
        f"conversation {_onoff(cur.show_convo)}, lingers {cur.linger_s:g} seconds; status word {_onoff(cur.show_status)}; "
        f"auto-hide {_onoff(cur.auto_hide)}; instruments {inst}; desktop dim {round(cur.cinematic_dim * 100)} percent; "
        f"scanlines {_onoff(cur.scanlines)}; text-only mode {_onoff(cur.text_only)}."
    )


@tool(summary="HUD settings", speak_result=True)
async def hud_settings(
    corner: Annotated[str | None, Field(description="Corner the reactor docks in: top-left, top-right, bottom-left or bottom-right")] = None,
    adjust: Annotated[
        str | None,
        Field(description="Relative change ('a bit bigger', 'dimmer') — one of: bigger, smaller (reactor size ±60 px), brighter, dimmer (idle brightness ±15 points)"),
    ] = None,
    reactor_size: Annotated[
        int | None, Field(ge=200, le=400, description="Reactor size in pixels, 200–400 — only when the user names a number; otherwise use adjust")
    ] = None,
    idle_brightness: Annotated[
        int | None,
        Field(ge=10, le=100, description="Brightness of the resting reactor, percent 10–100 — only when the user names a number; otherwise use adjust"),
    ] = None,
    linger_s: Annotated[float | None, Field(ge=0, le=120, description="Seconds the conversation stays unfolded after a turn, 0–120")] = None,
    show_conversation: Annotated[bool | None, Field(description="Show the last lines of conversation beside the reactor")] = None,
    show_status: Annotated[bool | None, Field(description="Show the STANDBY / LISTENING status word under the reactor")] = None,
    auto_hide: Annotated[bool | None, Field(description="Hide the HUD completely while idle; it comes back on 'Hey Jarvis'")] = None,
    instruments: Annotated[bool | None, Field(description="All side instruments (panel / full interface) on or off")] = None,
    tiles_on: Annotated[
        list[str] | str | None,
        Field(description="Instruments to turn on: wave (audio I/O), spectrum, system (CPU/MEM/VRAM gauges), radar (telemetry), clock, globe, pipeline (latencies)"),
    ] = None,
    tiles_off: Annotated[list[str] | str | None, Field(description="Instruments to turn off (same names)")] = None,
    desktop_dim: Annotated[int | None, Field(ge=0, le=90, description="How much the full interface darkens the desktop, percent 0–90")] = None,
    scanlines: Annotated[bool | None, Field(description="Scanline overlay in the full interface")] = None,
) -> str:
    """Change Jarvis' OWN HUD preferences — the same ones as his settings drawer: which corner the reactor sits in, its
    size and idle brightness, how long the conversation lingers, conversation / status word on or off, auto-hide, which
    instruments show, desktop dim and scanlines. Pass only what should change: 'move to the top left' → corner=top-left;
    'bigger' / 'a bit dimmer' → adjust=bigger / adjust=dimmer (never invent a number — reactor_size and idle_brightness
    are for when the user says one); 'hide the clock' → tiles_off=[clock]; 'turn on auto-hide' → auto_hide=true. With
    no arguments it reports the current settings. For switching modes or hiding the HUD use hud_display; for text-only
    mode use text_mode."""
    if _current_prefs is None:
        raise ToolError("Settings are not available right now.")
    cur = _current_prefs()
    patch: dict[str, Any] = {}
    said: list[str] = []

    # The 8B model sometimes passes phrases ("move to the top left", "the upper right corner", "a bit bigger") — read them.
    adjustments: set[str] = set()
    if adjust:
        adjustments = _parse_adjust(adjust)
        if corner is None:
            corner = _parse_corner(adjust)
        if not adjustments and corner is None:
            raise ToolError(f"I didn't understand '{adjust}' — I can go bigger, smaller, brighter or dimmer, or move to a corner.")
    already: list[str] = []  # requested values that are the current ones — say so instead of "Done"

    def want(key: str, value: Any, phrase: str, current: Any) -> None:
        (already if value == current else said).append(phrase)
        if value != current:
            patch[key] = value

    if corner is not None:
        parsed = _parse_corner(corner)
        if parsed is None:
            raise ToolError(f"'{corner}' isn't a corner I know — top-left, top-right, bottom-left or bottom-right.")
        want("corner", parsed, f"reactor in the {parsed.replace('-', ' ')}", cur.corner)
    size_adj = next((a for a in ("bigger", "smaller") if a in adjustments), None)
    if size_adj and reactor_size is None:
        reactor_size = max(200, min(400, cur.reactor_size + (SIZE_STEP if size_adj == "bigger" else -SIZE_STEP)))
        if reactor_size == cur.reactor_size:
            already.append(f"the reactor as {'big' if size_adj == 'bigger' else 'small'} as it goes")
            reactor_size = None
    if reactor_size is not None:
        want("reactor_size", reactor_size, f"reactor at {reactor_size} pixels", cur.reactor_size)
    light_adj = next((a for a in ("brighter", "dimmer") if a in adjustments), None)
    cur_brightness = round(cur.idle_opacity * 100)
    if light_adj and idle_brightness is None:
        idle_brightness = max(10, min(100, cur_brightness + (BRIGHTNESS_STEP if light_adj == "brighter" else -BRIGHTNESS_STEP)))
        if idle_brightness == cur_brightness:
            already.append(f"the reactor as {'bright' if light_adj == 'brighter' else 'dim'} as it goes")
            idle_brightness = None
    if idle_brightness is not None:
        want("idle_opacity", idle_brightness / 100, f"idle brightness {idle_brightness} percent", cur_brightness / 100)
    if linger_s is not None:
        want("linger_s", float(linger_s), f"conversation lingers {linger_s:g} seconds", cur.linger_s)
    if show_conversation is not None:
        want("show_convo", show_conversation, f"conversation {_onoff(show_conversation)}", cur.show_convo)
    if show_status is not None:
        want("show_status", show_status, f"status word {_onoff(show_status)}", cur.show_status)
    if auto_hide is not None:
        want("auto_hide", auto_hide, f"auto-hide {_onoff(auto_hide)}", cur.auto_hide)
    if instruments is not None:
        want("widgets", instruments, f"instruments {_onoff(instruments)}", cur.widgets)
    on, off = _parse_tiles(tiles_on), _parse_tiles(tiles_off)
    tiles = {t: True for t in on if not cur.tiles.get(t, True)} | {t: False for t in off if cur.tiles.get(t, True)}
    if on and not cur.widgets and instruments is None:
        patch["widgets"] = True  # turning one instrument on while the master is off would show nothing
        said.append("instruments on")
    if tiles:
        patch["tiles"] = tiles
    for group, state in ((on, True), (off, False)):
        changed = [TILE_NAMES[t] for t in group if t in tiles]
        same = [TILE_NAMES[t] for t in group if t not in tiles]
        if changed:
            said.append(f"{_join(changed)} {_onoff(state)}")
        if same:
            already.append(f"{_join(same)} {_onoff(state)}")
    if desktop_dim is not None:
        want("cinematic_dim", desktop_dim / 100, f"desktop dim {desktop_dim} percent", cur.cinematic_dim)
    if scanlines is not None:
        want("scanlines", scanlines, f"scanlines {_onoff(scanlines)}", cur.scanlines)

    if not patch and not already:
        return _describe(cur)
    if patch:
        await _apply(patch)
    parts = []
    if said:
        parts.append("Done, sir: " + "; ".join(said) + ".")
    if already:
        parts.append(("Already so, sir: " if not said else "The rest was already so: ") + "; ".join(already) + ".")
    return " ".join(parts)


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
