"""Decode and measure project audio using the shared FFmpeg installation."""

from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from backend.avatar.service import _binary

MAX_SECONDS = 20 * 60
ANALYSIS_RATE = 16_000
HOP = 320
WINDOW = 1024
BANDS = 48


class StudioError(RuntimeError):
    def __init__(self, detail, status_code=422):
        super().__init__(detail)
        self.status_code = status_code


def media_binary():
    binary = _binary("ffmpeg")
    if not binary:
        raise StudioError("FFmpeg is unavailable. See Models & Tools.", 503)
    return binary


def convert(source: Path, target: Path, *, rate=24_000, trim=None, denoise=False, channels=1):
    command = [media_binary(), "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
               "-format_whitelist", "wav,mp3,flac,mov,ogg", "-i", str(source), "-map", "0:a:0", "-vn"]
    if trim:
        start, end = trim
        command.extend(["-ss", str(start), "-t", str(end - start)])
    else:
        command.extend(["-t", str(MAX_SECONDS + 1)])
    if denoise:
        command.extend(["-af", "afftdn"])
    command.extend(["-ac", str(channels), "-ar", str(rate)])
    command.extend(["-c:a", "libmp3lame", "-q:a", "2"] if target.suffix == ".mp3" else ["-c:a", "pcm_s16le"])
    try:
        subprocess.run([*command, "-y", str(target)], capture_output=True, check=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        raise StudioError("FFmpeg could not decode or export this audio file.") from error
    if target.suffix == ".wav":
        metadata(target)


def metadata(path):
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        samples = wav.getnframes()
        channels = wav.getnchannels()
    if not samples or samples > MAX_SECONDS * rate:
        raise StudioError("Choose audio between one sample and 20 minutes long.")
    return {"sample_rate": rate, "channels": channels, "duration_seconds": samples / rate, "size_bytes": path.stat().st_size}


def pcm(path):
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768, wav.getframerate()


def write_wave(destination, samples, rate, normalize=True):
    import numpy as np

    peak = float(np.abs(samples).max()) if len(samples) else 0
    if not math.isfinite(peak) or peak < 0.00001:
        raise StudioError("The audio is empty, silent or invalid.")
    gain = 10 ** (-1 / 20) / peak if normalize else 1
    pcm = np.rint(np.clip(samples * gain, -1, 1) * 32767).astype("<i2")
    with wave.open(str(destination), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm.tobytes())


def reference_quality(path, trim_silence=False):
    import numpy as np

    samples, rate = pcm(path)
    peak = float(np.max(np.abs(samples)))
    activity = np.flatnonzero(np.abs(samples) >= max(0.005, peak * 0.025))
    if activity.size == 0:
        raise StudioError("The reference is silent.")
    start = max(0, int(activity[0]) - rate // 10)
    end = min(len(samples), int(activity[-1]) + rate // 10)
    if trim_silence:
        samples = samples[start:end]
    clipped = float(np.mean(np.abs(samples) >= 0.997))
    if not 3 <= len(samples) / rate <= 30:
        raise StudioError("Use a 3–30 second reference recording or adjust the trim.")
    if clipped > 0.01:
        raise StudioError("The reference clips frequently. Record again at a lower input level.")
    return samples, rate, {"duration_seconds": len(samples) / rate, "peak": peak, "clipped_fraction": clipped,
                           "rms": float(np.sqrt(np.mean(samples ** 2)))}


@dataclass
class AudioAnalysis:
    duration: float
    waveform: object
    rms: object
    peak: object
    bands: object
    wave_frames: object
    onsets: list[float]
    beats: list[float]
    silence: list[list[float]]

    def feature(self, source, time):
        idx = min(len(self.rms) - 1, max(0, time * ANALYSIS_RATE / HOP))
        left = int(idx)
        right = min(left + 1, len(self.rms) - 1)
        if source in ("rms", "peak"):
            values = self.rms if source == "rms" else self.peak
            a, b = values[left], values[right]
        else:
            band = slice(None, 15) if source == "bass" else slice(-14, None)
            a, b = self.bands[left, band].mean(), self.bands[right, band].mean()
        return float(a + (b - a) * (idx - left))

    def frame(self, time):
        index = min(len(self.rms) - 1, max(0, time * ANALYSIS_RATE / HOP))
        left = int(index)
        right = min(left + 1, len(self.rms) - 1)
        return (self.bands[left] + (self.bands[right] - self.bands[left]) * (index - left),
                self.wave_frames[left], float(self.rms[left]))

    def summary(self):
        return {"duration_seconds": self.duration, "waveform": self.waveform.tolist(),
                "peak": float(self.peak.max()), "rms": float((self.rms ** 2).mean() ** .5),
                "onsets": self.onsets, "beats": self.beats, "silence": self.silence}


def analyze(path):
    return _analyze(str(path), path.stat().st_mtime_ns)


@lru_cache(maxsize=3)
def _analyze(path, revision):
    import numpy as np

    raw = subprocess.run(
        [media_binary(), "-v", "error", "-nostdin", "-i", path, "-ac", "1", "-ar", str(ANALYSIS_RATE),
         "-f", "s16le", "pipe:1"], capture_output=True, check=True, timeout=180,
    ).stdout
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if not len(samples):
        raise StudioError("This file has no decodable audio.")
    duration = len(samples) / ANALYSIS_RATE
    padded = np.pad(samples, (WINDOW // 2, WINDOW // 2))
    frames = np.lib.stride_tricks.sliding_window_view(padded, WINDOW)[::HOP]
    rms = np.empty(len(frames), np.float32)
    peak = rms.copy()
    bands = np.empty((len(frames), BANDS), np.float32)
    flux = rms.copy()
    edges = np.geomspace(30, ANALYSIS_RATE / 2, BANDS + 1)
    edges = np.round(edges / (ANALYSIS_RATE / WINDOW)).astype(int)
    prev = np.zeros(WINDOW // 2 + 1)
    window = np.hanning(WINDOW)
    for start in range(0, len(frames), 256):
        block = frames[start:start+256]
        stop = start + len(block)
        spectrum = np.abs(np.fft.rfft(block * window)) * (2 / window.sum())
        rms[start:stop] = np.sqrt(np.mean(block ** 2, axis=1))
        peak[start:stop] = np.max(np.abs(block), axis=1)
        flux[start:stop] = np.maximum(0, np.diff(spectrum, axis=0, prepend=prev[None, :])).mean(axis=1)
        prev = spectrum[-1]
        for band, (a, b) in enumerate(zip(edges, edges[1:])):
            bands[start:stop, band] = spectrum[:, a:max(a+1, b)].max(axis=1)
    bands = np.clip(np.sqrt(bands * 2), 0, 1)
    bands[1:] = (bands[1:] + bands[:-1]) / 2
    threshold = float(np.median(flux) + max(0.0004, 2.5 * np.median(np.abs(flux - np.median(flux)))))
    hits = []
    tick = HOP / ANALYSIS_RATE
    for i in np.flatnonzero((flux[1:-1] > threshold) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] > flux[2:])) + 1:
        if not hits or i * tick - hits[-1] >= .12:
            hits.append(round(i * tick, 3))
    beats = []
    periods = np.diff(hits)
    if len(periods) >= 4:
        period = float(np.median(periods))
        if .28 <= period <= 1.1 and np.mean(np.abs(periods - period) <= .12 * period) >= .8:
            beats = hits.copy()
    silent = np.flatnonzero(np.diff(np.r_[False, rms < .008, False]))
    silence = [[round(a*tick, 3), round(min(b*tick, duration), 3)] for a, b in zip(silent[::2], silent[1::2])
               if (b-a) * tick >= .15]
    waveform = np.array([max(abs(section.min()), abs(section.max()))
                         for section in np.array_split(samples, min(240, len(samples)))])
    return AudioAnalysis(duration, waveform, rms, peak, bands, frames[:, ::16].copy(), hits, beats, silence)


def record_microphone(seconds, stop):
    from io import BytesIO

    try:
        import sounddevice as sd
    except (ImportError, OSError) as error:
        raise StudioError("Microphone capture requires PortAudio in the local runtime.", 503) from error
    try:
        rate = int(sd.query_devices(kind="input")["default_samplerate"])
        buffer = BytesIO()
        with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16") as stream, wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            remaining = seconds * rate
            while remaining and not stop.is_set():
                count = min(1024, remaining)
                samples, overflow = stream.read(count)
                if overflow:
                    raise StudioError("Recording dropped samples. Close other audio apps and try again.")
                wav.writeframesraw(samples)
                remaining -= count
            if remaining:
                raise StudioError("Recording was stopped.", 409)
        return buffer.getvalue()
    except (sd.PortAudioError, ValueError) as error:
        raise StudioError("Could not open the input device. Check microphone access and the default input device.", 409) from error
