"""System information tools (control tools live in Phase 3 modules)."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from typing import Any

import psutil

from jarvis.tools.registry import tool


def gpu_status() -> dict[str, Any] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip()
        name, util, used, total, temp = [s.strip() for s in out.split(",")]
        return {"name": name, "util_pct": int(util), "vram_used_mb": int(used), "vram_total_mb": int(total), "temp_c": int(temp)}
    except Exception:  # noqa: BLE001
        return None


@tool(summary="Check system status")
def system_status() -> dict[str, Any]:
    """Report CPU, RAM, GPU, disk and battery status of this PC."""
    vm = psutil.virtual_memory()
    bat = psutil.sensors_battery()
    disks = {}
    for part in psutil.disk_partitions(all=False):
        try:
            u = shutil.disk_usage(part.mountpoint)
            disks[part.device.rstrip("\\")] = f"{u.free / 2**30:.0f} GB free of {u.total / 2**30:.0f} GB"
        except OSError:
            continue
    return {
        "cpu_pct": psutil.cpu_percent(interval=0.3),
        "ram": f"{vm.used / 2**30:.1f}/{vm.total / 2**30:.0f} GB ({vm.percent}%)",
        "gpu": gpu_status(),
        "disks": disks,
        "battery": {"percent": bat.percent, "plugged_in": bat.power_plugged} if bat else None,
        "uptime_hours": round((datetime.now().timestamp() - psutil.boot_time()) / 3600, 1),
    }


@tool(summary="Get date & time")
def get_datetime() -> str:
    """Current local date and time (only if the user explicitly asks; it's already in your context)."""
    return datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
