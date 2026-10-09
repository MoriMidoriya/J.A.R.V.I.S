"""Clipboard, typing and key presses — lets Jarvis operate whatever app is in front."""

from __future__ import annotations

import time
from typing import Annotated

import pyperclip
from loguru import logger
from pydantic import Field

from jarvis.tools.registry import ToolError, tool

_KEY_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "win": "cmd", "windows": "cmd", "super": "cmd", "cmd": "cmd",
    "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc", "tab": "tab", "space": "space", "backspace": "backspace",
    "delete": "delete", "del": "delete", "home": "home", "end": "end", "pageup": "page_up", "pagedown": "page_down",
    "up": "up", "down": "down", "left": "left", "right": "right", "insert": "insert", "printscreen": "print_screen",
    "capslock": "caps_lock", "menu": "menu", "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4", "f5": "f5", "f6": "f6",
    "f7": "f7", "f8": "f8", "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}


@tool(summary="Read clipboard")
def get_clipboard() -> str:
    """Read the current clipboard text."""
    text = pyperclip.paste() or ""
    if not text.strip():
        return "The clipboard is empty (or holds non-text content)."
    return text if len(text) <= 2000 else text[:2000] + f"… [{len(text)} chars total]"


@tool(summary="Copy to clipboard")
def set_clipboard(text: Annotated[str, Field(description="Text to place on the clipboard")]) -> str:
    """Put text on the clipboard."""
    pyperclip.copy(text)
    return f"Copied {len(text)} characters to the clipboard."


@tool(summary="Type text")
def type_text(text: Annotated[str, Field(description="Exact text to type into the active window")], press_enter: Annotated[bool, Field(description="Press Enter afterwards")] = False) -> str:
    """Type text into whatever window is currently focused (as if on the keyboard)."""
    from pynput.keyboard import Controller, Key

    kb = Controller()
    time.sleep(0.15)  # let any focus change settle
    kb.type(text)
    if press_enter:
        kb.press(Key.enter)
        kb.release(Key.enter)
    return f"Typed {len(text)} characters" + (" and pressed Enter." if press_enter else ".")


@tool(summary="Press {combo}")
def press_keys(combo: Annotated[str, Field(description="Key or shortcut, e.g. 'ctrl+s', 'alt+tab', 'win+d', 'enter', 'f5', 'ctrl+shift+esc'")]) -> str:
    """Press a keyboard shortcut in the active window."""
    from pynput.keyboard import Controller, Key

    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ToolError("No keys given.")
    keys: list[object] = []
    for p in parts:
        name = _KEY_ALIASES.get(p, p)
        if len(name) == 1:
            keys.append(name)
        elif hasattr(Key, name):
            keys.append(getattr(Key, name))
        else:
            raise ToolError(f"Unknown key '{p}'.")
    kb = Controller()
    time.sleep(0.1)
    for k in keys:
        kb.press(k)
    for k in reversed(keys):
        kb.release(k)
    logger.debug("pressed {}", combo)
    return f"Pressed {'+'.join(parts)}."
