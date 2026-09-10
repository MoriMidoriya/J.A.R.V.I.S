"""Thin ctypes wrapper over the voidtools Everything SDK (instant NTFS-indexed file search).

The DLL is fetched on first use from voidtools.com into models/everything/. Requires Everything.exe running.
"""

from __future__ import annotations

import ctypes
import io
import zipfile
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger

from jarvis.config import MODELS_DIR

SDK_URL = "https://www.voidtools.com/Everything-SDK.zip"
DLL_DIR = MODELS_DIR / "everything"
DLL_PATH = DLL_DIR / "Everything64.dll"

REQUEST_FILE_NAME = 0x1
REQUEST_PATH = 0x2
REQUEST_SIZE = 0x10
REQUEST_DATE_MODIFIED = 0x40

SORT = {
    "name": 1,
    "path": 3,
    "largest": 6,
    "recent": 14,
    "run_count": 22,
}

ERRORS = {
    0: "ok",
    1: "out of memory",
    2: "Everything IPC window not found — is Everything running?",
    3: "failed to register window class",
    4: "failed to create window",
    5: "failed to create thread",
    6: "invalid index",
    7: "invalid call",
}


@dataclass
class Hit:
    path: str
    is_folder: bool
    size: int | None
    modified: datetime | None


_dll: ctypes.WinDLL | None = None


def _ensure_dll() -> Path:
    if DLL_PATH.exists():
        return DLL_PATH
    DLL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("downloading Everything SDK from {}", SDK_URL)
    r = httpx.get(SDK_URL, follow_redirects=True, timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        member = next(n for n in z.namelist() if n.lower().endswith("dll/everything64.dll"))
        DLL_PATH.write_bytes(z.read(member))
    return DLL_PATH


def _lib() -> ctypes.WinDLL:
    global _dll
    if _dll is None:
        d = ctypes.WinDLL(str(_ensure_dll()))
        d.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
        d.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
        d.Everything_SetSort.argtypes = [wintypes.DWORD]
        d.Everything_SetMax.argtypes = [wintypes.DWORD]
        d.Everything_SetMatchPath.argtypes = [wintypes.BOOL]
        d.Everything_QueryW.argtypes = [wintypes.BOOL]
        d.Everything_QueryW.restype = wintypes.BOOL
        d.Everything_GetNumResults.restype = wintypes.DWORD
        d.Everything_GetLastError.restype = wintypes.DWORD
        d.Everything_GetResultFullPathNameW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD]
        d.Everything_GetResultFullPathNameW.restype = wintypes.DWORD
        d.Everything_IsFolderResult.argtypes = [wintypes.DWORD]
        d.Everything_IsFolderResult.restype = wintypes.BOOL
        d.Everything_GetResultSize.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_longlong)]
        d.Everything_GetResultSize.restype = wintypes.BOOL
        d.Everything_GetResultDateModified.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.FILETIME)]
        d.Everything_GetResultDateModified.restype = wintypes.BOOL
        _dll = d
    return _dll


def _filetime_to_dt(ft: wintypes.FILETIME) -> datetime | None:
    v = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    if v == 0 or v == 0xFFFFFFFFFFFFFFFF:
        return None
    return datetime.fromtimestamp((v - 116444736000000000) / 10_000_000, tz=UTC).astimezone()


def available() -> bool:
    try:
        _lib()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Everything SDK unavailable: {}", e)
        return False


def ipc_ready() -> bool:
    """True if a user-session Everything.exe (the IPC host) is running. The indexing service alone is not enough."""
    user32 = ctypes.windll.user32
    return bool(user32.FindWindowW("EVERYTHING_TASKBAR_NOTIFICATION", None) or user32.FindWindowW("EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)", None))


def ensure_running(timeout_s: float = 6.0) -> bool:
    """Start Everything.exe minimised to tray if its IPC window is absent (e.g. only the service is up)."""
    if ipc_ready():
        return True
    from jarvis.config import get_settings

    exe = Path(get_settings().tools.everything_path)
    if not exe.exists():
        return False
    import subprocess
    import time

    logger.info("starting Everything ({})", exe)
    subprocess.Popen([str(exe), "-startup"], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if ipc_ready():
            time.sleep(0.5)  # let it finish loading the DB
            return True
        time.sleep(0.25)
    return False


def search(query: str, *, max_results: int = 200, sort: str = "recent", match_path: bool = False) -> list[Hit]:
    d = _lib()
    if not ipc_ready() and not ensure_running():
        raise RuntimeError("Everything is not running and could not be started.")
    d.Everything_SetSearchW(query)
    d.Everything_SetMatchPath(match_path)
    d.Everything_SetRequestFlags(REQUEST_FILE_NAME | REQUEST_PATH | REQUEST_SIZE | REQUEST_DATE_MODIFIED)
    d.Everything_SetSort(SORT.get(sort, SORT["recent"]))
    d.Everything_SetMax(max_results)
    if not d.Everything_QueryW(True):
        err = d.Everything_GetLastError()
        raise RuntimeError(f"Everything query failed: {ERRORS.get(err, err)}")
    n = d.Everything_GetNumResults()
    buf = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_longlong()
    ft = wintypes.FILETIME()
    hits: list[Hit] = []
    for i in range(n):
        d.Everything_GetResultFullPathNameW(i, buf, len(buf))
        folder = bool(d.Everything_IsFolderResult(i))
        sz = size.value if (not folder and d.Everything_GetResultSize(i, ctypes.byref(size))) else None
        dm = _filetime_to_dt(ft) if d.Everything_GetResultDateModified(i, ctypes.byref(ft)) else None
        hits.append(Hit(buf.value, folder, sz, dm))
    return hits
