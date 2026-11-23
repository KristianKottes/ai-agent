"""Safe shared helpers for native platform probes."""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .models import AcceleratorInfo, GPUInfo


def run_text(args: Sequence[str], timeout_seconds: float = 5.0) -> Optional[str]:
    """Run a fixed local probe with argument arrays and no shell expansion."""
    try:
        result = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            close_fds=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def nvidia_smi_path() -> Optional[str]:
    """Find NVIDIA's standard local utility without searching user-writable PATH entries."""
    fixed_search_path = os.pathsep.join(
        ["/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin"]
    )
    candidate = shutil.which("nvidia-smi", path=fixed_search_path)
    if candidate:
        return candidate
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
            Path(os.environ.get("SystemDrive", "C:"))
            / "Program Files" / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ]
        return next((str(path) for path in candidates if path.is_file()), None)
    return None


def nvidia_inventory() -> Tuple[List[GPUInfo], AcceleratorInfo]:
    executable = nvidia_smi_path()
    if not executable:
        return [], AcceleratorInfo(
            name="CUDA",
            detection_status="unknown",
            evidence="nvidia-smi was not found in standard driver locations; CUDA inference is untested.",
        )

    output = run_text(
        [
            executable,
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        timeout_seconds=8.0,
    )
    if output is None:
        return [], AcceleratorInfo(
            name="CUDA",
            detection_status="unknown",
            evidence="nvidia-smi exists but did not return a valid inventory; CUDA inference is untested.",
        )

    devices: List[GPUInfo] = []
    for row in csv.reader(io.StringIO(output), skipinitialspace=True):
        if not row or not row[0].strip():
            continue
        memory_bytes = None
        if len(row) > 1:
            try:
                memory_bytes = int(float(row[1].strip()) * 1024 * 1024)
            except ValueError:
                pass
        devices.append(
            GPUInfo(
                vendor="NVIDIA",
                name=row[0].strip(),
                memory_bytes=memory_bytes,
                memory_kind="dedicated VRAM (nvidia-smi MiB report)" if memory_bytes else None,
                driver_version=row[2].strip() if len(row) > 2 and row[2].strip() else None,
                detection_source="nvidia-smi",
            )
        )

    status = "detected" if devices else "not_detected"
    evidence = (
        "NVIDIA driver inventory returned one or more devices; model inference compatibility is untested."
        if devices
        else "nvidia-smi ran but reported no GPUs."
    )
    return devices, AcceleratorInfo(name="CUDA", detection_status=status, evidence=evidence)


def accelerator_inventory(
    metal_status: str = "unknown",
    metal_evidence: str = "Metal support was not queried on this platform.",
    cuda: Optional[AcceleratorInfo] = None,
) -> List[AcceleratorInfo]:
    return [
        AcceleratorInfo(
            name="CPU",
            detection_status="detected",
            evidence="The operating system reports a CPU; CPU model inference is not tested.",
        ),
        AcceleratorInfo(name="Metal", detection_status=metal_status, evidence=metal_evidence),
        cuda
        or AcceleratorInfo(
            name="CUDA",
            detection_status="unknown",
            evidence="CUDA driver/runtime detection was not performed on this platform.",
        ),
    ]
