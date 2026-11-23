"""Pinned inputs for the Apple Silicon scene benchmark."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    size: int
    sha256: str


def weights(repo, revision, name, size, digest):
    return Artifact(name, f"https://huggingface.co/{repo}/resolve/{revision}/{name}", size, digest)


ENGINE_REVISION = "master-969-8c442fb"
ENGINE = Artifact(
    "engine.zip",
    "https://github.com/leejet/stable-diffusion.cpp/releases/download/"
    f"{ENGINE_REVISION}/sd-master-8c442fb-bin-Darwin-macOS-26.6.2-arm64.zip",
    35_252_880,
    "129b62de61772f368fa9f9ee8130d62a4dadd6ad99e7243b9734cff97f539191",
)

_FLUX = "novelaide/FLUX.2-klein-4B-GGUF"
FLUX_REVISION = "3e0e2b84cb9f0d52e226de8ac4fc181f26c30456"
_WAN_EXTRAS = "off-grid-ai/Video-Companions-GGUF"
_WAN_EXTRAS_REV = "18b5ae5e93799acd6cd20819f898ff60f4b822d1"

IMAGE_WEIGHTS = (
    weights(_FLUX, FLUX_REVISION, "flux-2-klein-4b-Q4_0.gguf", 2_460_378_560,
            "d1023499ef3f2f82ff7c50e6778495195c1b6cc34835741778868428111f9ff4"),
    weights(_FLUX, FLUX_REVISION, "Qwen3-4B-Q4_K_M.gguf", 2_497_281_312,
            "f6f851777709861056efcdad3af01da38b31223a3ba26e61a4f8bf3a2195813a"),
    weights(_FLUX, FLUX_REVISION, "full_encoder_small_decoder.safetensors", 249_519_092,
            "ea4273f02d1fafbf8e1d1c2cf6018ed8748652eb0bf34f2dd91171f16f15ab62"),
)

VIDEO_WEIGHTS = (
    weights("off-grid-ai/Wan2.1-T2V-1.3B-GGUF", "1ba777b2426d5717dd0fddb1100078604a9d4667",
            "Wan2.1-T2V-1.3B-calibrated-Q4_K_S.gguf", 816_061_024,
            "82788a4a3ce6e206edd3e8e5fe512b2a75344a3d272d80f07cd4e48b60b7aaf7"),
    weights(_WAN_EXTRAS, _WAN_EXTRAS_REV, "umt5-xxl-encoder-Q4_K_M.gguf", 3_655_145_312,
            "17cf97a5bbbc60a646d6105b832b6f657ce904a8a1ad970e4b59df0c67584a40"),
    weights("ionden/taew2.1", "d6659884fcb62ff8d8beb15eda968b64ff70bc48",
            "taew2_1.safetensors", 22_642_902,
            "04766eac0221b5390b985ae3fdcca652cbb4b1e8b82b28ea7ff89dfad1b1a93f"),
)
