"""Typed manifest contracts for the application-managed runtime catalog."""

from __future__ import annotations

from typing import Literal, List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _safe_relative_path(value: str) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("path must be a normalized relative POSIX path")
    return value


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LicenseInfo(ManifestModel):
    source_code: str = Field(min_length=1, max_length=80)
    model_weights: str = Field(min_length=1, max_length=80)
    commercial_use: Literal["permitted", "restricted", "unknown", "not_applicable"]
    attribution_required: bool
    license_file: str

    @field_validator("license_file")
    @classmethod
    def license_file_is_relative(cls, value: str) -> str:
        return _safe_relative_path(value)


class PlatformSupport(ManifestModel):
    os: Literal["macos", "windows", "linux"]
    architectures: List[Literal["arm64", "x86_64", "x86"]]
    verification: Literal["verified", "not_tested"]


class Artifact(ManifestModel):
    bundle_path: str
    target_path: str
    size_bytes: int = Field(ge=0, le=2_147_483_648)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("bundle_path", "target_path")
    @classmethod
    def paths_are_relative(cls, value: str) -> str:
        return _safe_relative_path(value)


class ComponentManifest(ManifestModel):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    category: Literal[
        "language-models",
        "text-to-speech",
        "voice-processing",
        "image-generation",
        "video-generation",
        "avatar-animation",
        "lip-synchronization",
        "speech-recognition",
        "video-processing",
        "utilities",
    ]
    description: str = Field(min_length=1, max_length=500)
    experimental: bool
    source: str = Field(min_length=1, max_length=200)
    license: LicenseInfo
    platforms: List[PlatformSupport]
    backends: List[Literal["cpu", "metal", "cuda"]]
    gpu_requirements: str = Field(min_length=1, max_length=120)
    minimum_ram_bytes: int = Field(ge=0)
    minimum_disk_bytes: int = Field(ge=0)
    dependencies: List[str]
    installation_adapter: Literal["python-script-v1"]
    entrypoint: str
    health_check: Literal["json-ready-v1"]
    artifacts: List[Artifact] = Field(min_length=1)

    @field_validator("entrypoint")
    @classmethod
    def entrypoint_is_relative(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def validate_artifact_contract(self) -> "ComponentManifest":
        targets = [artifact.target_path for artifact in self.artifacts]
        if len(targets) != len(set(targets)):
            raise ValueError("artifact target paths must be unique")
        if self.entrypoint not in targets:
            raise ValueError("entrypoint must be included in the artifact list")
        if self.license.license_file not in targets:
            raise ValueError("license_file must be included in the artifact list")
        if len(self.dependencies) != len(set(self.dependencies)) or self.id in self.dependencies:
            raise ValueError("dependencies must be unique and cannot include the component itself")
        return self

    @property
    def download_size_bytes(self) -> int:
        return sum(artifact.size_bytes for artifact in self.artifacts)

    @property
    def installed_size_bytes(self) -> int:
        return self.download_size_bytes


class Catalog(ManifestModel):
    catalog_version: int = Field(ge=1)
    components: List[ComponentManifest]

    @model_validator(mode="after")
    def validate_catalog_ids(self) -> "Catalog":
        ids = [component.id for component in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog component IDs must be unique")
        known = set(ids)
        for component in self.components:
            missing = set(component.dependencies) - known
            if missing:
                raise ValueError(f"unknown dependencies for {component.id}: {', '.join(sorted(missing))}")
        return self
