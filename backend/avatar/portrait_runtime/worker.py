"""Run MuseTalk in its own Python environment and stream generated frames."""

from __future__ import annotations

import argparse
import math
from itertools import islice
from pathlib import Path

from backend.avatar.encoder import FPS, write_video

SIZE = 640
SAMPLE_RATE = 16_000


def _imports():
    import cv2
    import mlx.core as mx
    import numpy as np
    from musetalk_mlx.pipeline_mlx import MuseTalkPipeline
    from musetalk_mlx.whisper.log_mel import log_mel_spectrogram

    return cv2, mx, np, MuseTalkPipeline, log_mel_spectrogram


def frames_for_audio(photo, rectangle, waveform, model, reference_video=None, *, animate_head=False):
    cv2, mx, np, pipeline, log_mel = _imports()
    cv2.setNumThreads(2)
    if not mx.metal.is_available():
        raise RuntimeError("MLX requires access to the Apple GPU")
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(2 * 1024**3)

    x, y, side = rectangle
    reference = photo[y:y + side, x:x + side]
    face = cv2.resize(reference, (256, 256), interpolation=cv2.INTER_AREA)
    model = pipeline.from_pretrained_mlx(model)
    count = math.ceil(len(waveform) * FPS / SAMPLE_RATE)
    samples = count * SAMPLE_RATE // FPS + 1
    padded = np.pad(waveform, (0, samples - len(waveform)))
    features = model.encode_audio(log_mel(mx.array(padded)), samples, fps=FPS)
    if features.shape[0] != count:
        raise RuntimeError("MuseTalk did not generate the expected number of audio feature frames")

    # The conditioning masks the lower half of the crop. Blend that region back
    # into the portrait before applying any motion.
    mask = np.zeros((256, 256), dtype=np.float32)
    cv2.ellipse(mask, (128, 188), (110, 68), 0, 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (33, 33), 0)
    mask[:118, :] = 0
    if reference_video is None:
        motion = None
        if animate_head:
            from backend.avatar.portrait_runtime.still_motion import StillMotion

            motion = StillMotion(photo, rectangle)
        latents = model.get_latents_for_unet(face).astype(mx.float16)
        still_mask = cv2.resize(mask, (side, side))[:, :, None]
        for offset in range(0, count, 2):
            batch = features[offset:offset + 2].astype(mx.float16)
            faces = model.generate_faces(mx.repeat(latents, batch.shape[0], axis=0), batch)
            for index, generated in enumerate(faces, offset):
                patch = cv2.resize(generated, (side, side), interpolation=cv2.INTER_LINEAR)
                frame = photo.copy()
                frame[y:y + side, x:x + side] = np.rint(
                    patch * still_mask + reference * (1 - still_mask)
                ).astype(np.uint8)
                if motion is not None:
                    frame = motion.apply(frame, index / FPS)
                yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).tobytes()
        return

    from backend.avatar.portrait_runtime.motion import tracked_frames

    source = tracked_frames(reference_video, rectangle, count)
    for offset in range(0, count, 2):
        frames = list(islice(source, 2))
        latents = mx.concatenate([model.get_latents_for_unet(crop) for _, crop, _ in frames]).astype(mx.float16)
        faces = model.generate_faces(latents, features[offset:offset + len(frames)].astype(mx.float16))
        for (frame, _, transform), generated in zip(frames, faces, strict=True):
            overlay = cv2.warpAffine(generated, transform, (SIZE, SIZE))
            alpha = cv2.warpAffine(mask, transform, (SIZE, SIZE))[:, :, None]
            blended = np.rint(overlay * alpha + frame * (1 - alpha)).astype(np.uint8)
            yield cv2.cvtColor(blended, cv2.COLOR_BGR2RGB).tobytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--portrait", type=Path)
    motion = parser.add_mutually_exclusive_group()
    motion.add_argument("--reference-video", type=Path)
    motion.add_argument("--animate-head", action="store_true")
    parser.add_argument("--pcm", type=Path)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--crop", type=int, nargs=3)
    args = parser.parse_args()
    cv2, mx, np, _, _ = _imports()
    if args.probe:
        if not mx.metal.is_available():
            raise RuntimeError("MLX Metal device is unavailable")
        mx.eval(mx.array([1]) + 1)
        return

    photo = cv2.imread(str(args.portrait))
    if photo is None or photo.shape[:2] != (SIZE, SIZE):
        raise ValueError("Expected a prepared square portrait")
    x, y, side = args.crop
    if side < 160 or min(x, y) < 0 or max(x, y) + side > SIZE:
        raise ValueError("The crop is outside the prepared portrait")
    waveform = np.fromfile(args.pcm, "<i2").astype(np.float32) / 32768
    if not 0.5 * SAMPLE_RATE <= len(waveform) <= 10 * SAMPLE_RATE:
        raise ValueError("The PCM clip must contain between 0.5 and 10 seconds")
    frames = frames_for_audio(photo, args.crop, waveform, args.model, args.reference_video, animate_head=args.animate_head)
    write_video(args.ffmpeg, args.wav, args.output, frames, SIZE, len(waveform) / SAMPLE_RATE)


if __name__ == "__main__":
    main()
