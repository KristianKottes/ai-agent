import shutil
import subprocess
import wave

import pytest
from PIL import Image

from backend.scenes.compose import Timeline, render_timeline


def test_composes_mixed_assets_with_timed_captions(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("The composition check requires FFmpeg and FFprobe.")
    encoders = subprocess.check_output([ffmpeg, "-hide_banner", "-encoders"], text=True)
    if "libx264 " not in encoders or "mov_text " not in encoders:
        pytest.skip("The composition check requires the libx264 and mov_text encoders.")

    path = tmp_path / "scene%01d.png"
    Image.new("RGB", (32, 48), "#df7628").save(path)
    with wave.open(str(tmp_path / "narration.wav"), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(48000))
    subprocess.run([
        ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=80x48:rate=16",
        "-frames:v", "8", "-c:v", "libx264", str(tmp_path / "scene.mp4"),
    ], check=True)
    timeline = Timeline.model_validate({
        "format": "square",
        "narration": "narration.wav",
        "scenes": [
            {"kind": "video", "asset": "scene.mp4", "frames": 12},
            {"kind": "image", "asset": path.name, "frames": 12, "transition": "fade_black"},
        ],
        "captions": [{"start_ms": 125, "end_ms": 875, "text": "Across the scene cut"}],
    })
    output = tmp_path / "export.mp4"
    result = render_timeline(timeline, tmp_path, output)
    assert (result["width"], result["height"], result["frame_count"]) == (1080, 1080, 24)
    assert result["av_start_offset_ms"] == result["av_end_offset_ms"] == 0
    captions = subprocess.check_output([
        ffmpeg, "-nostdin", "-v", "error", "-i", str(output), "-map", "0:s:0", "-f", "srt", "pipe:1",
    ], text=True)
    assert "00:00:00,125 --> 00:00:00,875\nAcross the scene cut" in captions
    boundary = subprocess.check_output([
        ffmpeg, "-nostdin", "-v", "error", "-i", str(output), "-vf",
        "select=eq(n\\,12),scale=1:1,format=gray", "-frames:v", "1", "-f", "rawvideo", "pipe:1",
    ])
    assert len(boundary) == 1 and boundary[0] < 4
