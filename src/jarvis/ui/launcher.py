"""Run the voice backend as a child process of the HUD window (D18).

Why two processes? Measured on this machine: with the backend *inside* the Qt process, the model loads take 1.6x
longer (16 s vs 10 s), because a visible Chromium view switches the Windows timer to 1 ms and Qt/Chromium threads
share the process with Python's GIL — the four model-loading threads get thrashed. In a child process the backend is
exactly as fast as `jarvis run --no-ui`, and a Chromium/GPU-driver crash in the window cannot take the voice
assistant down with it (or vice versa).

Lifecycle
  start()   spawn `python -m jarvis run --no-ui [--debug]`, put it in a Windows Job Object with KILL_ON_JOB_CLOSE so
            it can never outlive us (even if we are killed from Task Manager), inherit the console if we have one.
  poll()    called from a Qt timer; restarts the backend (with backoff, max 3 times) if it exited on its own.
  stop()    `POST /shutdown` (graceful: streams closed, tasks cancelled), wait up to 5 s, then terminate.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Self

import httpx
from loguru import logger

from jarvis.config import ROOT, Settings

MAX_RESTARTS = 3
RESTART_DELAY_S = 3.0


class BackendProcess:
    def __init__(self, settings: Settings, debug: bool = False) -> None:
        self.s = settings
        self.debug = debug
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self._stopping = False
        self._exited_at: float | None = None
        self._job = _make_kill_on_close_job()

    @property
    def url(self) -> str:
        return f"http://{self.s.ui.host}:{self.s.ui.port}"

    # ---- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        exe = _console_python()
        cmd = [str(exe), "-m", "jarvis", "run", "--no-ui"] + (["--debug"] if self.debug else [])
        has_console = sys.stdout is not None  # False under pythonw.exe (autostart) — then the backend logs to file only
        flags = 0 if has_console else subprocess.CREATE_NO_WINDOW
        io = None if has_console else subprocess.DEVNULL
        self.proc = subprocess.Popen(cmd, cwd=ROOT, creationflags=flags, stdin=subprocess.DEVNULL, stdout=io, stderr=io)
        self._exited_at = None
        if self._job:
            ctypes.windll.kernel32.AssignProcessToJobObject(self._job, wintypes.HANDLE(self.proc._handle))
        logger.info("backend started (pid {})", self.proc.pid)

    def poll(self) -> None:
        """Supervise: restart a backend that died on its own (crash, OOM, driver reset)."""
        if self.proc is None or self._stopping or self.proc.poll() is None:
            return
        now = time.monotonic()
        if self._exited_at is None:
            self._exited_at = now
            logger.error(
                "backend exited with code {} — restarting in {:.0f}s",
                self.proc.returncode,
                RESTART_DELAY_S,
            )
            return
        if now - self._exited_at < RESTART_DELAY_S:
            return
        if self.restarts >= MAX_RESTARTS:
            logger.error(
                "backend keeps dying ({} restarts); giving up — check logs/jarvis.log",
                self.restarts,
            )
            self._exited_at = None
            self.proc = None
            return
        self.restarts += 1
        self.start()

    def stop(self) -> None:
        self._stopping = True
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            httpx.post(f"{self.url}/shutdown", timeout=2.0)
        except Exception as e:  # noqa: BLE001 - server may already be gone
            logger.debug("shutdown request failed ({}); terminating", e)
        try:
            self.proc.wait(timeout=5.0)
            logger.info("backend stopped (code {})", self.proc.returncode)
        except subprocess.TimeoutExpired:
            logger.warning("backend did not stop in 5 s; terminating")
            self.proc.kill()
            self.proc.wait(timeout=3.0)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ---- helpers -------------------------------------------------------------------------------------
def _console_python() -> Path:
    """`python.exe` next to whatever interpreter we are (pythonw.exe when autostarted)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return candidate
    return exe


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_uint64)
        for n in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _make_kill_on_close_job() -> int | None:
    """A Job Object that kills its processes when the last handle to it closes — i.e. when *we* exit, however we exit."""
    if sys.platform != "win32":
        return None
    k32 = ctypes.windll.kernel32
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = JobObjectExtendedLimitInformation
    if not ok:
        logger.debug(
            "SetInformationJobObject failed ({}); backend will not be auto-killed with the window",
            ctypes.GetLastError(),
        )
        return None
    return job
