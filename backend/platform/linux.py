"""Linux probes using procfs, DRM sysfs, PCI inventory, and NVIDIA tooling."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import accelerator_inventory, nvidia_inventory, run_text
from .models import GPUInfo

VENDORS = {
    "0x1002": "AMD",
    "0x10de": "NVIDIA",
    "0x8086": "Intel",
    "0x106b": "Apple",
    "0x13b5": "Arm",
    "0x5143": "Qualcomm",
    "0x1af4": "Virtio",
}
GPU_CLASSES = ("vga compatible controller", "3d controller", "display controller")


def parse_meminfo(text: str) -> Tuple[Optional[int], Optional[int]]:
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        pieces = value.split()
        if pieces and pieces[0].isdigit():
            multiplier = 1024 if len(pieces) > 1 and pieces[1].lower() == "kb" else 1
            values[key] = int(pieces[0]) * multiplier
    return values.get("MemTotal"), values.get("MemAvailable")


def parse_lspci(text: str) -> Dict[str, str]:
    inventory = {}
    pattern = re.compile(
        r"^(\S+)\s+.*?(?:VGA compatible controller|3D controller|Display controller).*?:\s*(.*?)\s+\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]"
    )
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            address, name, vendor_id, device_id = match.groups()
            inventory[address.lower()] = {
                "name": name.strip(),
                "vendor_id": vendor_id.lower(),
                "device_id": device_id.lower(),
            }
    return inventory


def _pci_inventory() -> Dict[str, Dict[str, str]]:
    executable = shutil.which("lspci", path="/usr/bin:/usr/sbin:/bin:/sbin")
    if not executable:
        return {}
    output = run_text([executable, "-D", "-nn"], timeout_seconds=4.0)
    return parse_lspci(output) if output else {}


def _address_from_sysfs(path: Path) -> Optional[str]:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    for parent in (resolved, *resolved.parents):
        if re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", parent.name):
            return parent.name.lower()
    return None


def _gpu_inventory() -> List[GPUInfo]:
    drm = Path("/sys/class/drm")
    pci = _pci_inventory()
    found = []
    if not drm.is_dir():
        return found
    try:
        entries = sorted(drm.iterdir())
    except OSError:
        return found

    seen = set()
    for card in entries:
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device_path = card / "device"
        try:
            vendor_id = (device_path / "vendor").read_text().strip().lower()
            device_id = (device_path / "device").read_text().strip().lower()
        except OSError:
            continue
        address = _address_from_sysfs(device_path)
        item = pci.get(address or "", {})
        name = item.get("name") or "PCI device {}:{}".format(vendor_id.replace("0x", ""), device_id.replace("0x", ""))
        key = (vendor_id, device_id, name)
        if key in seen:
            continue
        seen.add(key)
        memory_bytes = None
        memory_path = device_path / "mem_info_vram_total"
        try:
            raw_memory = memory_path.read_text().strip()
            if raw_memory.isdigit():
                memory_bytes = int(raw_memory)
        except OSError:
            pass
        driver = None
        try:
            driver = (device_path / "driver").resolve(strict=True).name
        except OSError:
            pass
        vendor = VENDORS.get(vendor_id)
        if vendor is None and item.get("vendor_id"):
            vendor = VENDORS.get("0x" + item["vendor_id"])
        found.append(
            GPUInfo(
                vendor=vendor,
                name=name,
                memory_bytes=memory_bytes,
                memory_kind="dedicated VRAM (DRM sysfs)" if memory_bytes is not None else None,
                driver_version=driver,
                detection_source="DRM sysfs" + (" with lspci names" if item else "; PCI model name unavailable"),
            )
        )
    return found


def collect() -> Dict[str, object]:
    cpu_model = None
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(errors="replace")
        for line in cpuinfo.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() in ("model name", "hardware"):
                cpu_model = value.strip()
                if cpu_model:
                    break
    except OSError:
        pass

    total_memory = available_memory = None
    memory_method = None
    try:
        total_memory, available_memory = parse_meminfo(Path("/proc/meminfo").read_text())
        if total_memory is not None:
            memory_method = "Linux /proc/meminfo MemAvailable estimate"
    except OSError:
        pass

    sysfs_gpus = _gpu_inventory()
    nvidia_gpus, cuda = nvidia_inventory()
    if nvidia_gpus:
        nvidia_names = {gpu.name.casefold() for gpu in nvidia_gpus}
        sysfs_gpus = [gpu for gpu in sysfs_gpus if gpu.vendor != "NVIDIA" and gpu.name.casefold() not in nvidia_names]
        sysfs_gpus.extend(nvidia_gpus)

    return {
        "cpu_model": cpu_model,
        "memory_total_bytes": total_memory,
        "memory_available_bytes": available_memory,
        "memory_available_method": memory_method,
        "gpus": sysfs_gpus,
        "accelerators": accelerator_inventory(
            metal_status="not_detected",
            metal_evidence="Metal is a macOS-only graphics API.",
            cuda=cuda,
        ),
    }
