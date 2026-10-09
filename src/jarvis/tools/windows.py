"""Window management: list, focus, minimise, maximise, snap, show desktop.

Uses Win32 directly (pywin32). Matching is fuzzy on the window title AND the owning process name, so "switch to
chrome" works even though Chrome's title is the page title.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Annotated, Literal

import psutil
import win32con
import win32gui
import win32process
from loguru import logger
from pydantic import Field
from rapidfuzz import fuzz

from jarvis.tools.registry import ToolError, tool

# Windows we never want to list/focus by accident.
_IGNORED_TITLES = {"Program Manager", "Windows Input Experience", "Settings", "Click to Do"}
_IGNORED_CLASSES = {"Progman", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow_hidden"}


@dataclass
class Win:
    hwnd: int
    title: str
    process: str
    minimized: bool

    def label(self) -> str:
        return f"{self.title} ({self.process})"


def _is_cloaked(hwnd: int) -> bool:
    """UWP windows that are suspended/not on this virtual desktop report as visible but are 'cloaked'."""
    DWMWA_CLOAKED = 14
    val = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:  # noqa: BLE001
        return False
    return val.value != 0


def list_open_windows() -> list[Win]:
    out: list[Win] = []

    def cb(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetParent(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if not title or title in _IGNORED_TITLES or win32gui.GetClassName(hwnd) in _IGNORED_CLASSES:
            return True
        if _is_cloaked(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid).name().removesuffix(".exe")
        except Exception:  # noqa: BLE001
            proc = "?"
        out.append(Win(hwnd, title, proc, bool(win32gui.IsIconic(hwnd))))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def find_window(name: str) -> Win:
    wins = list_open_windows()
    if not wins:
        raise ToolError("There are no open windows.")
    q = name.lower().strip()
    best: tuple[float, Win] | None = None
    for w in wins:
        score = max(
            fuzz.partial_ratio(q, w.title.lower()),
            fuzz.ratio(q, w.process.lower()) + 5,  # process-name match is a strong signal ("chrome", "code")
            fuzz.token_set_ratio(q, w.title.lower()),
        )
        if best is None or score > best[0]:
            best = (score, w)
    assert best is not None
    if best[0] < 60:
        raise ToolError(f"No window matches '{name}'. Open windows: " + "; ".join(w.label() for w in wins[:10]))
    return best[1]


def _force_foreground(hwnd: int) -> None:
    """SetForegroundWindow is refused unless our thread is the foreground one; attach to it briefly."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    fg = win32gui.GetForegroundWindow()
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    attached = False
    if fg_thread and fg_thread != cur_thread:
        attached = bool(ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True))
    try:
        # The ALT-tap trick releases the foreground lock reliably.
        ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
        ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, 2, 0)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)


@tool(summary="List windows")
def list_windows() -> list[str]:
    """List the currently open windows (title and app). Use to pick a target for focus_window."""
    wins = list_open_windows()
    return [w.label() + (" [minimised]" if w.minimized else "") for w in wins]


@tool(summary="Focus {name}")
def focus_window(name: Annotated[str, Field(description="App or window title to bring to the front, e.g. 'chrome', 'spotify', 'the PDF'")]) -> str:
    """Switch to (bring to front) an already-open window. Use open_app if the app isn't running."""
    w = find_window(name)
    _force_foreground(w.hwnd)
    logger.debug("focused {}", w.label())
    return f"Switched to {w.label()}."


@tool(summary="{action} {name}")
def window_action(
    action: Annotated[Literal["minimize", "maximize", "restore", "close", "snap_left", "snap_right"], Field(description="What to do with the window")],
    name: Annotated[str, Field(description="App or window title; 'current' or 'this' = the active window")] = "current",
) -> str:
    """Minimise, maximise, restore, snap (left/right half) or close a window."""
    if name.lower() in {"current", "this", "active", "focused", ""}:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            raise ToolError("No active window.")
        label = win32gui.GetWindowText(hwnd) or "the active window"
    else:
        w = find_window(name)
        hwnd, label = w.hwnd, w.label()

    if action == "minimize":
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    elif action == "maximize":
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    elif action == "restore":
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    elif action == "close":
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    else:
        # Snap to half of the work area of the monitor the window is on.
        import win32api

        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(mon)["Work"]
        half = (right - left) // 2
        x = left if action == "snap_left" else left + half
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.MoveWindow(hwnd, x, top, half, bottom - top, True)
        _force_foreground(hwnd)
    verb = {"minimize": "Minimised", "maximize": "Maximised", "restore": "Restored", "close": "Closed", "snap_left": "Snapped left", "snap_right": "Snapped right"}[action]
    return f"{verb} {label}."


@tool(summary="Show desktop")
def show_desktop() -> str:
    """Minimise all windows and show the desktop."""
    import win32com.client

    import pythoncom

    pythoncom.CoInitialize()
    win32com.client.Dispatch("Shell.Application").MinimizeAll()
    return "Desktop shown."
