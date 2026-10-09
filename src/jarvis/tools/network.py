"""Wi-Fi / Bluetooth radios and network status.

Radios are toggled through the WinRT `Windows.Devices.Radios` API (via Windows PowerShell 5.1), which — unlike
`netsh interface set interface` — needs no administrator rights. Both toggles are tier `confirm`.
"""

from __future__ import annotations

import re
import subprocess
from typing import Annotated, Literal

import httpx
from loguru import logger
from pydantic import Field

from jarvis.tools.registry import ToolError, tool

_RADIO_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]
Function Await($WinRtTask, $ResultType) {{
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
if ($access -ne 'Allowed') {{ Write-Output "ACCESS:$access"; exit 2 }}
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | Where-Object {{ $_.Kind -eq '{kind}' }} | Select-Object -First 1
if (-not $r) {{ Write-Output "NORADIO"; exit 3 }}
if ('{state}' -eq 'query') {{ Write-Output "STATE:$($r.State)"; exit 0 }}
$res = Await ($r.SetStateAsync('{state}')) ([Windows.Devices.Radios.RadioAccessStatus])
Write-Output "RESULT:$res STATE:$($r.State)"
"""


def _radio(kind: Literal["WiFi", "Bluetooth"], state: Literal["On", "Off", "query"]) -> str:
    script = _RADIO_PS.format(kind=kind, state=state)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    out = (r.stdout or "").strip()
    logger.debug("radio {} {} -> rc={} {}", kind, state, r.returncode, out or r.stderr.strip()[:200])
    if "NORADIO" in out:
        raise ToolError(f"No {kind} radio found on this PC.")
    if out.startswith("ACCESS:"):
        raise ToolError(f"Windows denied radio access ({out}). Check Settings → Privacy → Radios.")
    if r.returncode and not out:
        raise ToolError(f"Radio control failed: {r.stderr.strip()[:200] or 'unknown error'}")
    return out


@tool(summary="Wi-Fi {state}", confirm_prompt="Turn Wi-Fi {state}?")
def set_wifi(state: Annotated[Literal["on", "off"], Field(description="on or off")]) -> str:
    """Turn the Wi-Fi radio on or off."""
    out = _radio("WiFi", "On" if state == "on" else "Off")
    if "RESULT:Allowed" not in out:
        raise ToolError(f"Wi-Fi did not change: {out}")
    return f"Wi-Fi is now {state}."


@tool(summary="Bluetooth {state}", confirm_prompt="Turn Bluetooth {state}?")
def set_bluetooth(state: Annotated[Literal["on", "off"], Field(description="on or off")]) -> str:
    """Turn the Bluetooth radio on or off."""
    out = _radio("Bluetooth", "On" if state == "on" else "Off")
    if "RESULT:Allowed" not in out:
        raise ToolError(f"Bluetooth did not change: {out}")
    return f"Bluetooth is now {state}."


def _wlan_interface() -> dict[str, str]:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except Exception:  # noqa: BLE001
        return {}
    info: dict[str, str] = {}
    for key, label in (("SSID", "ssid"), ("State", "state"), ("Signal", "signal"), ("Receive rate (Mbps)", "rx_mbps")):
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+)$", out, re.MULTILINE)
        if m:
            info[label] = m.group(1).strip()
    return info


@tool(summary="Network status")
def network_status() -> dict[str, object]:
    """Wi-Fi connection (SSID, signal), Bluetooth state and whether the internet is reachable."""
    wifi = _wlan_interface()
    try:
        bt = _radio("Bluetooth", "query").replace("STATE:", "").lower()
    except ToolError as e:
        bt = str(e)
    try:  # Windows' own connectivity probe (plain http; returns "Microsoft Connect Test")
        online = httpx.get("http://www.msftconnecttest.com/connecttest.txt", timeout=3).status_code == 200
    except Exception:  # noqa: BLE001
        online = False
    return {"wifi": wifi or "not connected", "bluetooth": bt, "internet": "reachable" if online else "unreachable"}
