"""Windows hardware probes using Win32 memory APIs and CIM inventory."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import accelerator_inventory, nvidia_inventory, run_text
from .models import GPUInfo

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def _global_memory_status() -> Optional[Dict[str, int]]:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        call = kernel32.GlobalMemoryStatusEx
        call.argtypes = [ctypes.POINTER(MemoryStatusEx)]
        call.restype = ctypes.c_int
        result = MemoryStatusEx()
        result.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not call(ctypes.byref(result)):
            return None
    except (AttributeError, OSError):
        return None
    return {"total": int(result.ullTotalPhys), "available": int(result.ullAvailPhys)}


def _cim_inventory() -> Dict[str, Any]:
    if not Path(POWERSHELL).is_file():
        return {}
    script = (
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name; "
        "$gpus = @(Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterCompatibility, DriverVersion); "
        "@{ cpu_model = $cpu; gpus = $gpus } | ConvertTo-Json -Compress -Depth 4"
    )
    output = run_text(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout_seconds=12.0,
    )
    if not output:
        return {}
    try:
        value = json.loads(output)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def collect() -> Dict[str, Any]:
    memory = _global_memory_status() or {}
    inventory = _cim_inventory()
    wmi_gpus = inventory.get("gpus", [])
    if isinstance(wmi_gpus, dict):
        wmi_gpus = [wmi_gpus]

    gpus: List[GPUInfo] = []
    for item in wmi_gpus if isinstance(wmi_gpus, list) else []:
        if not isinstance(item, dict) or not item.get("Name"):
            continue
        gpus.append(
            GPUInfo(
                vendor=item.get("AdapterCompatibility"),
                name=item["Name"],
                driver_version=item.get("DriverVersion"),
                detection_source="Win32_VideoController via CIM",
            )
        )

    nvidia_gpus, cuda = nvidia_inventory()
    if nvidia_gpus:
        nvidia_names = {gpu.name.casefold() for gpu in nvidia_gpus}
        gpus = [gpu for gpu in gpus if gpu.name.casefold() not in nvidia_names]
        gpus.extend(nvidia_gpus)

    cpu_model = inventory.get("cpu_model") or os.environ.get("PROCESSOR_IDENTIFIER")
    return {
        "cpu_model": cpu_model,
        "memory_total_bytes": memory.get("total"),
        "memory_available_bytes": memory.get("available"),
        "memory_available_method": "Win32 GlobalMemoryStatusEx" if memory else None,
        "gpus": gpus,
        "accelerators": accelerator_inventory(
            metal_status="not_detected",
            metal_evidence="Metal is a macOS-only graphics API.",
            cuda=cuda,
        ),
    }
