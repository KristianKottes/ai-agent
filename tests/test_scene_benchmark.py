import hashlib
import io
import os
import sys
import threading
import time

import pytest

from backend.scenes import benchmark
from backend.scenes.profiles import Artifact


def test_download_restarts_cleanly_when_server_ignores_range(tmp_path, monkeypatch):
    content = b"correct weights"
    artifact = Artifact("weights.gguf", "https://example.invalid/weights", len(content),
                        hashlib.sha256(content).hexdigest())
    partial = tmp_path / "weights.gguf.part"
    partial.write_bytes(b"cor")
    monkeypatch.setattr(benchmark, "DISK_RESERVE", 0)

    def open_response(request, *, timeout):
        assert request.get_header("Range") == "bytes=3-"
        reply = io.BytesIO(content)
        reply.status = 200
        return reply

    monkeypatch.setattr(benchmark, "urlopen", open_response)
    model = benchmark.prepare(tmp_path, artifact, download=True)
    assert model.read_bytes() == content
    assert not partial.exists()


def test_wrong_checksum_is_never_promoted(tmp_path, monkeypatch):
    content = b"correct weights"
    artifact = Artifact("weights.gguf", "https://example.invalid/weights", len(content),
                        hashlib.sha256(content).hexdigest())
    partial = tmp_path / "weights.gguf.part"
    partial.write_bytes(b"invalid weights")
    monkeypatch.setattr(benchmark, "DISK_RESERVE", 0)
    with pytest.raises(benchmark.BenchmarkError, match="SHA-256"):
        benchmark.prepare(tmp_path, artifact, download=True)
    assert not (tmp_path / artifact.name).exists()
    assert not partial.exists()


@pytest.mark.skipif(os.name != "posix", reason="The scene engine uses POSIX process groups.")
def test_scene_cancellation_stops_and_reaps_the_running_worker(tmp_path):
    pid_file = tmp_path / "worker-pid"
    event = threading.Event()

    def request_cancel_after_worker_starts():
        deadline = time.monotonic() + 5
        while not pid_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        event.set()

    cancellation = threading.Thread(target=request_cancel_after_worker_starts)
    cancellation.start()
    script = "from pathlib import Path; import os, sys, time; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    try:
        with pytest.raises(benchmark.SceneCancelled):
            benchmark.run_engine([sys.executable, "-c", script, str(pid_file)], tmp_path / "worker.log", cancel=event)
        assert pid_file.is_file()
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        cancellation.join()
