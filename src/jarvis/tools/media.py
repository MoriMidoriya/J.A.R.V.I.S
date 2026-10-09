"""Volume, mute, screen brightness and media keys.

Volume: Windows Core Audio via pycaw (master endpoint volume of the default speakers).
Brightness: screen_brightness_control (WMI for the laptop panel, DDC/CI for external monitors).
Media keys: synthesised VK_MEDIA_* key presses — they reach whichever player is active, no focus needed.
"""

from __future__ import annotations

import ctypes
import time
from typing import Annotated, Literal

from loguru import logger
from pydantic import Field

from jarvis.tools.registry import ToolError, tool

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF}
KEYEVENTF_KEYUP = 0x0002


def _endpoint():  # noqa: ANN202
    from comtypes import CoInitialize

    from pycaw.pycaw import AudioUtilities

    CoInitialize()  # tools run in worker threads; COM must be initialised per thread
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume_pct() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def _press_vk(code: int) -> None:
    ctypes.windll.user32.keybd_event(code, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)


@tool(summary="Volume → {level}%")
def set_volume(level: Annotated[int, Field(ge=0, le=100, description="Target volume 0–100")]) -> str:
    """Set the master speaker volume to an absolute percentage."""
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(level / 100, None)
    if level > 0 and ep.GetMute():
        ep.SetMute(0, None)
    return f"Volume set to {level}%."


@tool(summary="Volume {delta:+d}%")
def change_volume(delta: Annotated[int, Field(ge=-100, le=100, description="Relative change, e.g. +10 for 'louder', -20 for 'quieter'. Use ±15 if unspecified.")]) -> str:
    """Raise or lower the master volume by a relative amount."""
    ep = _endpoint()
    cur = round(ep.GetMasterVolumeLevelScalar() * 100)
    new = max(0, min(100, cur + delta))
    ep.SetMasterVolumeLevelScalar(new / 100, None)
    if new > 0 and ep.GetMute():
        ep.SetMute(0, None)
    return f"Volume {cur}% → {new}%."


@tool(summary="Mute: {state}")
def set_mute(state: Annotated[Literal["on", "off", "toggle"], Field(description="on = silence, off = unmute")] = "toggle") -> str:
    """Mute or unmute the speakers."""
    ep = _endpoint()
    muted = bool(ep.GetMute())
    new = (not muted) if state == "toggle" else (state == "on")
    ep.SetMute(1 if new else 0, None)
    return "Muted." if new else f"Unmuted, volume {round(ep.GetMasterVolumeLevelScalar() * 100)}%."


@tool(summary="Get volume")
def get_volume() -> str:
    """Current speaker volume and mute state."""
    ep = _endpoint()
    return f"Volume {round(ep.GetMasterVolumeLevelScalar() * 100)}%" + (", muted." if ep.GetMute() else ".")


def _sbc():  # noqa: ANN202
    import screen_brightness_control as sbc

    return sbc


@tool(summary="Brightness → {level}%")
def set_brightness(level: Annotated[int, Field(ge=0, le=100, description="Target brightness 0–100")]) -> str:
    """Set screen brightness to an absolute percentage (all displays)."""
    try:
        _sbc().set_brightness(level)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Brightness control failed: {e}") from e
    return f"Brightness set to {level}%."


@tool(summary="Brightness {delta:+d}%")
def change_brightness(delta: Annotated[int, Field(ge=-100, le=100, description="Relative change, e.g. -20 for 'dimmer'. Use ±20 if unspecified.")]) -> str:
    """Raise or lower screen brightness by a relative amount."""
    sbc = _sbc()
    try:
        cur = sbc.get_brightness()
        cur_v = int(cur[0]) if cur else 50
        new = max(0, min(100, cur_v + delta))
        sbc.set_brightness(new)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Brightness control failed: {e}") from e
    return f"Brightness {cur_v}% → {new}%."


@tool(summary="Media: {action}")
def media_control(action: Annotated[Literal["play_pause", "next", "previous", "stop"], Field(description="Transport control for whatever is playing (Spotify, YouTube, VLC…)")]) -> str:
    """Play/pause, skip or stop the currently playing media in any app."""
    _press_vk(VK[action])
    logger.debug("media key {}", action)
    return {"play_pause": "Toggled play/pause.", "next": "Skipped to the next track.", "previous": "Went to the previous track.", "stop": "Stopped playback."}[action]
