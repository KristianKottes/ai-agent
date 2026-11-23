"""macOS probes using System Profiler, sysctl, and vm_stat."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .common import accelerator_inventory, run_text
from .models import AcceleratorInfo, GPUInfo

SYSTEM_PROFILER = "/usr/sbin/system_profiler"
SYSCTL = "/usr/sbin/sysctl"
VM_STAT = "/usr/bin/vm_stat"


def _system_profiler(section: str) -> Optional[Dict[str, Any]]:
    output = run_text([SYSTEM_PROFILER, "-json", section], timeout_seconds=15.0)
    if not output:
        return None
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        return None


def _memory_size(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)\s*", value, re.IGNORECASE)
    if not match:
        return None
    factors = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(float(match.group(1)) * factors[match.group(2).upper()])


def parse_available_memory(output: str) -> Tuple[Optional[int], Optional[str]]:
    page = re.search(r"page size of (\d+) bytes", output)
    if not page:
        return None, None
    page_size = int(page.group(1))
    counters = {}
    for name in ("free", "inactive", "speculative", "purgeable"):
        match = re.search(r"Pages {}:\s*([0-9]+)".format(name), output, re.IGNORECASE)
        if match:
            counters[name] = int(match.group(1))
    if "free" not in counters:
        return None, None
    pages = sum(counters.values())
    return pages * page_size, "vm_stat estimate: free + inactive + speculative + purgeable pages"


def _available_memory() -> Tuple[Optional[int], Optional[str]]:
    output = run_text([VM_STAT], timeout_seconds=5.0)
    return parse_available_memory(output) if output else (None, None)


def collect() -> Dict[str, Any]:
    hardware = _system_profiler("SPHardwareDataType") or {}
    hardware_items = hardware.get("SPHardwareDataType", [])
    hardware_item = hardware_items[0] if hardware_items else {}

    total_memory = None
    sysctl_memory = run_text([SYSCTL, "-n", "hw.memsize"], timeout_seconds=2.0)
    if sysctl_memory:
        try:
            total_memory = int(sysctl_memory)
        except ValueError:
            pass
    if total_memory is None:
        total_memory = _memory_size(hardware_item.get("physical_memory"))
    available_memory, available_method = _available_memory()

    display_data = _system_profiler("SPDisplaysDataType") or {}
    display_items = display_data.get("SPDisplaysDataType", [])
    gpus: List[GPUInfo] = []
    metal_state = "unknown"
    metal_evidence = "System Profiler did not return a Metal support field."
    for item in display_items:
        model = item.get("sppci_model") or item.get("_name")
        if model:
            raw_vendor = item.get("spdisplays_vendor", "")
            vendor = raw_vendor.replace("sppci_vendor_", "").replace("spdisplays_vendor_", "") or None
            gpus.append(
                GPUInfo(
                    vendor=vendor,
                    name=model,
                    memory_kind="unified memory; dedicated GPU memory is not reported separately",
                    detection_source="system_profiler SPDisplaysDataType",
                )
            )
        metal = item.get("spdisplays_metal")
        if metal == "spdisplays_supported":
            metal_state = "detected"
            metal_evidence = "System Profiler reports Metal support; model inference compatibility is untested."
        elif metal:
            metal_state = "not_detected"
            metal_evidence = "System Profiler reports no Metal support for the detected display device."

    return {
        "cpu_model": hardware_item.get("chip_type") or hardware_item.get("cpu_type"),
        "memory_total_bytes": total_memory,
        "memory_available_bytes": available_memory,
        "memory_available_method": available_method,
        "gpus": gpus,
        "accelerators": accelerator_inventory(
            metal_status=metal_state,
            metal_evidence=metal_evidence,
        ),
    }
