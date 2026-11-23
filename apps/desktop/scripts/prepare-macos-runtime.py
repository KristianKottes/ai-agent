"""Stage the relocatable runtime for the Apple Silicon macOS app bundle."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[2]
BUILD = SCRIPTS.parent / "src-tauri" / ".macos-build"
PYTHON_VERSION = "3.12.12"
SYSTEM_LIBRARIES = ("/usr/lib/", "/System/Library/")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def capture(*command, env=None):
    return subprocess.check_output(command, env=env, text=True).strip()


def media_source(name):
    binary = shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin:/usr/local/bin")
    if not binary:
        raise RuntimeError(f"Install {name} on the build machine before packaging the app.")
    return Path(binary).resolve(strict=True)


def copy_executable(source, destination):
    shutil.copyfile(source, destination)
    destination.chmod(0o755)


def bundle_media(root):
    media = root / "media/bin"
    media.mkdir(parents=True)
    sources = {}
    for name in ("ffmpeg", "ffprobe"):
        source = media_source(name)
        target = media / name
        copy_executable(source, target)
        sources[target] = source
    zonos = BUILD / "zonos-native"
    native_tools = [zonos / "bin" / name for name in ("zonos3-cli", "spk-encoder-cli")]
    if all(source.is_file() for source in native_tools) and (zonos / "licenses/ZONOS3-NOTICE").is_file():
        for source in native_tools:
            target = media / source.name
            copy_executable(source, target)
            sources[target] = source
        shutil.copytree(zonos / "licenses", root / "media/zonos-licenses")
    return sources


def native_links(binary):
    output = capture("/usr/bin/otool", "-arch", "arm64", "-L", binary)
    names = [line.strip().rsplit(" (", 1)[0] for line in output.splitlines()[1:]]
    if binary.suffix == ".dylib" and names:
        identity = capture("/usr/bin/otool", "-arch", "arm64", "-D", binary).splitlines()[1:]
        if identity and names[0] == identity[0]:
            names.pop(0)
    return names


def external_rpaths(binary):
    output = capture("/usr/bin/otool", "-arch", "arm64", "-l", binary)
    paths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (/[^\n]+?) \(offset \d+\)", output)
    return [path for path in dict.fromkeys(paths) if not path.startswith(SYSTEM_LIBRARIES)]


def external_library(name, original):
    if name.startswith("/"):
        return Path(name).resolve(strict=True)
    if name.startswith("@loader_path/"):
        return (original.parent / name.removeprefix("@loader_path/")).resolve(strict=True)
    raise RuntimeError(f"Cannot relocate {name} from {original}. Use an FFmpeg build with absolute or loader-relative library paths.")


def localize_libraries(root, media_sources):
    """Retarget host libraries for media tools and Python extension modules."""
    libraries = root / "lib"
    libraries.mkdir()
    pending = dict(media_sources)
    for binary in (root / "python").rglob("*"):
        if binary.is_file() and (binary.suffix in (".so", ".dylib") or binary.name == "python3.12"):
            pending[binary] = binary
    copied = {}
    while pending:
        binary, original = pending.popitem()
        changes = []
        for link in native_links(binary):
            if link.startswith(SYSTEM_LIBRARIES):
                continue
            if original == binary and not link.startswith("/"):
                continue  # The portable Python distribution and wheels already have their own relative links.
            source = external_library(link, original)
            target = libraries / source.name
            if target in copied and copied[target] != source:
                raise RuntimeError(f"Two native libraries have the same file name: {source.name}")
            if target not in copied:
                copy_executable(source, target)
                copied[target] = source
                pending[target] = source
            changes.extend(("-change", link, f"@loader_path/{os.path.relpath(target, binary.parent)}"))
        if binary in copied:
            changes.extend(("-id", f"@loader_path/{binary.name}"))
        for path in external_rpaths(binary):
            changes.extend(("-delete_rpath", path))
        if changes:
            subprocess.run(["/usr/bin/install_name_tool", *changes, binary], check=True)
        if changes or binary in media_sources:
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", binary], check=True, capture_output=True)
    return copied


def install_python(root):
    local_uv = ROOT / "backend/.venv/bin/uv"
    uv = local_uv if local_uv.is_file() else shutil.which("uv")
    if not uv or not capture(uv, "--version").startswith("uv 0.11.2 "):
        raise RuntimeError("Install uv 0.11.2 on the build machine or in backend/.venv.")
    backend = ROOT / "backend"
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(backend / ".uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(backend / ".managed-python"),
        "CMAKE_ARGS": "-DGGML_METAL=on -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_APPLE_SILICON_PROCESSOR=arm64",
        "CMAKE_BUILD_PARALLEL_LEVEL": "4",
    }
    subprocess.run([uv, "python", "install", "--no-bin", PYTHON_VERSION], env=env, check=True)
    base = backend / ".managed-python" / f"cpython-{PYTHON_VERSION}-macos-aarch64-none"
    python_home = root / "python"
    shutil.copytree(base, python_home, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    python = python_home / "bin/python3.12"
    site = python_home / "lib/python3.12/site-packages"
    command = [uv, "pip", "install", "--python", python, "--target", site,
               "--link-mode", "copy", "-r", SCRIPTS / "requirements-macos.txt"]
    # A source build from the existing development setup can supply the same Metal-enabled ctypes wheel.
    wheel = next((backend / ".pip-cache/wheels").glob("**/llama_cpp_python-0.3.36-py3-none-macosx_*_arm64.whl"), None)
    if wheel is not None:
        command.extend(("--find-links", wheel.parent))
    subprocess.run(command, env=env, check=True)
    return python


def copy_backend(root):
    app = root / "app"
    app.mkdir()
    shutil.copytree(ROOT / "backend", app / "backend", ignore=shutil.ignore_patterns(
        ".*", "__pycache__", "*.pyc",
    ))
    return app


def validate(root, python, app):
    env = {"PATH": SYSTEM_PATH, "AI_BLOGGER_MEDIA_BIN_DIR": str(root / "media/bin")}
    subprocess.run([python, "-B", "-E", "-s", "-c", (
        "import ssl, llama_cpp, sherpa_onnx, pocketsphinx, PIL.Image; "
        "from backend.runner import main; "
        "from backend.avatar.service import availability; "
        "assert availability()[0], availability(); "
        "assert ssl.create_default_context().get_ca_certs()"
    )], env=env, cwd=app, check=True)
    for name in ("ffmpeg", "ffprobe"):
        executable = root / "media/bin" / name
        subprocess.run([executable, "-version"], env=env, check=True, stdout=subprocess.DEVNULL)
    encoders = capture(root / "media/bin/ffmpeg", "-hide_banner", "-encoders", env=env)
    if not re.search(r"\blibx264\b", encoders):
        raise RuntimeError("The packaged FFmpeg build must include the libx264 encoder.")


def provenance(root, sources):
    records = {}
    for target, source in sorted(sources.items()):
        digest = hashlib.sha256()
        with source.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        records[str(target.relative_to(root))] = {"input": source.name, "input_sha256": digest.hexdigest()}
    (root / "build-info.json").write_text(
        json.dumps({"python": PYTHON_VERSION, "native_inputs": records}, indent=2) + "\n", encoding="utf-8",
    )


def main():
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise RuntimeError("Build the macOS runtime on an Apple Silicon Mac.")
    BUILD.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="prepare-", dir=BUILD) as work:
        root = Path(work) / "studio-runtime"
        root.mkdir()
        python = install_python(root)
        app = copy_backend(root)
        media_sources = bundle_media(root)
        libraries = localize_libraries(root, media_sources)
        validate(root, python, app)
        provenance(root, {**media_sources, **libraries})
        target = BUILD / "studio-runtime"
        if target.exists():
            shutil.rmtree(target)
        root.rename(target)
    print(f"Packaged macOS runtime staged at {target}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Could not prepare the macOS runtime: {error}")
