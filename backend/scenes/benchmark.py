"""Generate and validate one local image or short video on Apple Silicon."""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

from backend.runtime.downloader import sha256_file
from backend.scenes.profiles import ENGINE, ENGINE_REVISION, IMAGE_WEIGHTS, VIDEO_WEIGHTS, Artifact

DISK_RESERVE = 3 * 1024**3
VIDEO_FRAMES = 49
VIDEO_FPS = 16
VIDEO_WIDTH = 384
VIDEO_HEIGHT = 224
SEED = 137
IMAGE_PROMPT = "An editorial landscape photograph of a white lighthouse above a rocky coast at blue hour, warm light in the tower, realistic ocean and clouds"
VIDEO_PROMPT = "A steady low camera watches successive ocean waves roll in and spill into white foam across a black pebble beach at dawn, realistic coastal footage"


class BenchmarkError(RuntimeError):
    pass


class SceneCancelled(BenchmarkError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise SceneCancelled("Scene generation cancelled.")


def _check_file(path: Path, artifact: Artifact) -> bool:
    return path.is_file() and path.stat().st_size == artifact.size and sha256_file(path) == artifact.sha256


def prepare(directory: Path, artifact: Artifact, download: bool, *, cancel=None, progress=None) -> Path:
    target = directory / artifact.name
    partial = target.with_name(target.name + ".part")
    if (target.is_symlink() or partial.is_symlink()
            or (target.exists() and not target.is_file()) or (partial.exists() and not partial.is_file())):
        raise BenchmarkError(f"Use a regular working directory for {artifact.name}.")
    check_cancel(cancel)
    if _check_file(target, artifact):
        return target
    if not download:
        raise BenchmarkError(f"{artifact.name} is missing or invalid. Use --download after reviewing the model sources.")

    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > artifact.size:
        raise BenchmarkError(f"Remove the oversized partial file before retrying: {partial}")
    if shutil.disk_usage(directory).free < artifact.size - offset + DISK_RESERVE:
        raise BenchmarkError(f"Insufficient disk space for {artifact.name} with a 3 GiB system reserve.")
    if offset < artifact.size:
        if progress:
            progress(offset, artifact.size)
        print(f"Downloading {artifact.name} ({artifact.size / 10**9:.2f} GB, {offset / 10**9:.2f} GB cached)", flush=True)
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with urlopen(Request(artifact.url, headers=headers), timeout=30) as response:
            if response.status == 200:
                offset = 0
                if shutil.disk_usage(directory).free + (partial.stat().st_size if partial.exists() else 0) < artifact.size + DISK_RESERVE:
                    raise BenchmarkError("The server requires a fresh download and there is insufficient disk space.")
            elif response.status != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{artifact.size - 1}/{artifact.size}":
                raise BenchmarkError(f"The server returned an unexpected range for {artifact.name}.")
            with partial.open("ab" if offset else "wb") as writer:
                interval = 256 * 1024**2
                next_update = offset + interval
                while block := response.read(1024 * 1024):
                    check_cancel(cancel)
                    if writer.tell() + len(block) > artifact.size:
                        raise BenchmarkError(f"The server exceeded the pinned size of {artifact.name}.")
                    writer.write(block)
                    if writer.tell() >= next_update:
                        print(f"{artifact.name}: {writer.tell() / artifact.size:.0%} downloaded", flush=True)
                        next_update += interval
                    if progress:
                        progress(writer.tell(), artifact.size)
    if partial.stat().st_size != artifact.size:
        raise BenchmarkError(f"The download ended early. Rerun to resume {artifact.name}.")
    if sha256_file(partial) != artifact.sha256:
        partial.unlink()
        raise BenchmarkError(f"The download failed SHA-256 verification: {artifact.name}.")
    check_cancel(cancel)
    partial.replace(target)
    return target


def run_engine(command: list[str], log: Path, *, cancel=None, progress=None) -> tuple[float, int]:
    import os
    import resource

    start = time.monotonic()
    print(f"Generating locally. Runtime log: {log}", flush=True)
    with log.open("wb") as output:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                              start_new_session=True) as process:
            try:
                deadline = start + 1800
                while True:
                    check_cancel(cancel)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command[0], 1800)
                    try:
                        code = process.wait(timeout=min(0.75, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if progress:
                            with log.open("rb") as current:
                                current.seek(max(0, log.stat().st_size - 8192))
                                tail = current.read().decode(errors="replace")
                            # Denoising progress lines report seconds/iteration. Other engine counters
                            # report weight loading or decoding and should not move this step backward.
                            matches = re.findall(r"(\d+)/(\d+)\s+-\s+[\d.]+s/it", tail)
                            if matches:
                                done, total = map(int, matches[-1])
                                if total:
                                    progress(done / total)
            except BaseException:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=5)
                    except ProcessLookupError:
                        pass
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                raise
    if code:
        raise BenchmarkError(f"Engine exited with status {code}. See {log}.")
    return time.monotonic() - start, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def encode_video(frames: Path, output: Path, ffmpeg: str, ffprobe: str) -> dict:
    expected = [frames / f"frame-{number:03d}.png" for number in range(VIDEO_FRAMES)]
    if sorted(frames.glob("frame-*.png")) != expected or any(not frame.stat().st_size for frame in expected):
        raise BenchmarkError(f"Expected {VIDEO_FRAMES} numbered frames from the video model.")
    subprocess.run([
        ffmpeg, "-nostdin", "-v", "error", "-framerate", str(VIDEO_FPS), "-start_number", "0", "-i",
        str(frames / "frame-%03d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ], check=True, timeout=120)
    info = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
        "stream=codec_name,width,height,nb_read_frames,r_frame_rate,duration", "-of", "json", str(output),
    ], timeout=60))["streams"][0]
    if (info["codec_name"], info["width"], info["height"], int(info["nb_read_frames"]), info["r_frame_rate"]) != (
        "h264", VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FRAMES, f"{VIDEO_FPS}/1",
    ):
        raise BenchmarkError("The encoded clip does not match the video benchmark profile.")
    checksums = subprocess.check_output([
        ffmpeg, "-nostdin", "-v", "error", "-i", str(output), "-an", "-f", "framemd5", "-",
    ], text=True, timeout=60)
    unique_frames = len({line.rsplit(",", 1)[-1].strip() for line in checksums.splitlines() if not line.startswith("#")})
    if unique_frames < VIDEO_FRAMES // 2:
        raise BenchmarkError("Too few distinct decoded frames in the generated clip.")
    return {**info, "unique_decoded_frames": unique_frames}


@contextmanager
def model_cache(work_dir):
    import fcntl

    if work_dir.resolve() != work_dir:
        raise BenchmarkError("The scene model directory is unsafe.")
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = work_dir / "benchmark.lock"
    if lock_path.is_symlink():
        raise BenchmarkError("The scene model lock is unsafe.")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BenchmarkError("Another scene or image job is using the model directory.") from error
        yield


def prepare_models(work_dir, artifacts, download, *, cancel=None, progress=None, verified=None):
    total = sum(artifact.size for artifact in artifacts)
    completed = 0
    paths = []
    for artifact in artifacts:
        def update(done, size):
            if progress:
                progress((completed + done) / total)

        path = prepare(work_dir, artifact, download, cancel=cancel, progress=update)
        completed += artifact.size
        if verified:
            verified(artifact, path)
        if progress:
            progress(completed / total)
        paths.append(path)
    return paths


def benchmark(
    kind: str, work_dir: Path, download: bool, *, run_dir: Path | None = None,
    prompt: str | None = None, seed: int = SEED, cancel=None, progress=None, verified=None,
    image_size: tuple[int, int] = (512, 512), image_steps: int = 4, reference: Path | None = None,
):
    from PIL import Image, ImageStat

    from backend.avatar.service import _binary

    def notify(stage, fraction):
        if progress:
            progress(stage, fraction)

    ffmpeg, ffprobe = _binary("ffmpeg"), _binary("ffprobe")
    if kind == "video" and not (ffmpeg and ffprobe):
        raise BenchmarkError("Install FFmpeg with libx264 and FFprobe before running the video benchmark.")
    artifacts = IMAGE_WEIGHTS if kind == "image" else VIDEO_WEIGHTS
    with model_cache(work_dir):
        archive, model, encoder, decoder = prepare_models(work_dir, (ENGINE, *artifacts), download,
            cancel=cancel, progress=lambda ratio: notify("downloading", ratio), verified=verified)
        check_cancel(cancel)
        notify("preparing", None)
        with tempfile.TemporaryDirectory(prefix="engine-", dir=work_dir) as runtime:
            with ZipFile(archive) as bundle:
                for name in ("sd-cli", "libstable-diffusion.dylib"):
                    bundle.extract(name, runtime)
            engine = Path(runtime) / "sd-cli"
            engine.chmod(0o700)
            if cancel is None:
                devices = subprocess.check_output([str(engine), "--list-devices"], text=True, timeout=120)
            else:
                probe_log = Path(runtime) / "devices.log"
                run_engine([str(engine), "--list-devices"], probe_log, cancel=cancel)
                devices = probe_log.read_text(errors="replace")
            metal = next((line.split("\t", 1) for line in devices.splitlines()
                          if line.lower().startswith(("metal", "mtl"))), None)
            if not metal:
                raise BenchmarkError("The runtime could not access Metal. Run on Apple Silicon with GPU access.")
            run = run_dir if run_dir is not None else Path(tempfile.mkdtemp(prefix=f"{kind}-", dir=work_dir))
            command = [
                str(engine), "--backend", metal[0], "--diffusion-model", str(model),
                "--seed", str(seed),
            ]
            if kind == "image":
                target = run / "scene.png"
                width, height = image_size
                prompt, steps = prompt or IMAGE_PROMPT, image_steps
                command += ["--llm", str(encoder), "--vae", str(decoder), "-W", str(width), "-H", str(height),
                            "--steps", str(steps), "--cfg-scale", "1.0", "--sampling-method", "euler", "-o", str(target)]
                if reference:
                    command += ["--ref-image", str(reference)]
            else:
                target = run / "scene.mp4"
                frames = run / "frames"
                frames.mkdir()
                prompt, steps = prompt or VIDEO_PROMPT, 8
                command += [
                    "-M", "vid_gen", "--t5xxl", str(encoder), "--taesd", str(decoder),
                    "-W", str(VIDEO_WIDTH), "-H", str(VIDEO_HEIGHT),
                    "--steps", str(steps), "--cfg-scale", "6.0", "--sampling-method", "euler", "--flow-shift", "8",
                    "--flash-attn", "on", "--video-frames", str(VIDEO_FRAMES),
                    "--fps", str(VIDEO_FPS), "-o", str(frames / "frame-%03d.png"),
                ]
            notify("generating", 0)
            elapsed, peak = run_engine(
                [*command, "-p", prompt], run / "engine.log", cancel=cancel,
                progress=lambda value: notify("generating", value),
            )
            check_cancel(cancel)
            notify("validating", None)
            if kind == "image":
                with Image.open(target) as image:
                    spread = ImageStat.Stat(image.convert("L")).stddev[0]
                    if image.format != "PNG" or image.size != image_size or spread < 1:
                        raise BenchmarkError(f"Expected a nonblank {width} × {height} PNG.")
                    media = {"width": image.width, "height": image.height, "luma_stddev": round(spread, 2)}
            else:
                media = encode_video(frames, target, ffmpeg, ffprobe)
            report = {
                "operation": kind, "platform": platform.platform(), "device": "\t".join(metal),
                "engine": ENGINE_REVISION, "weights": {part.name: part.sha256 for part in artifacts},
                "seed": seed, "steps": steps, "prompt": prompt, "inference_seconds": round(elapsed, 2),
                "peak_child_resident_bytes": peak, "output": str(target), "output_bytes": target.stat().st_size,
                **media,
            }
            (run / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
            return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("image", "video"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true",
                        help="approve the pinned download (5.21 GB image or 4.49 GB video, plus 35 MB if needed)")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.exit(2, "This benchmark requires macOS on Apple Silicon.\n")
    try:
        print(json.dumps(benchmark(args.kind, args.work_dir.resolve(), args.download), indent=2))
    except KeyboardInterrupt:
        parser.exit(130, "Benchmark cancelled. Partial downloads can be resumed.\n")
    except (BenchmarkError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
