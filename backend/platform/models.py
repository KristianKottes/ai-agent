"""Validated hardware and platform report contracts."""

from typing import List, Literal, Optional

from pydantic import BaseModel


class GPUInfo(BaseModel):
    vendor: Optional[str] = None
    name: str
    memory_bytes: Optional[int] = None
    memory_kind: Optional[str] = None
    driver_version: Optional[str] = None
    detection_source: str


class AcceleratorInfo(BaseModel):
    name: str
    detection_status: Literal["detected", "not_detected", "unknown"]
    evidence: str
    inference_compatibility: Literal["not_tested"] = "not_tested"


class StorageInfo(BaseModel):
    data_directory: str
    cache_directory: str
    config_directory: str
    temp_directory: str
    total_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    available_bytes: Optional[int] = None


class SystemReport(BaseModel):
    os_name: str
    os_version: Optional[str] = None
    architecture: str
    cpu_model: Optional[str] = None
    logical_cpu_count: Optional[int] = None
    memory_total_bytes: Optional[int] = None
    memory_available_bytes: Optional[int] = None
    memory_available_method: Optional[str] = None
    gpus: List[GPUInfo]
    accelerators: List[AcceleratorInfo]
    storage: StorageInfo
    compatibility_note: str = (
        "Hardware and acceleration detection do not establish model compatibility. "
        "Local inference has not been tested on this system."
    )
