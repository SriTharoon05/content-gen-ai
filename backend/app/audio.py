"""Narration post-processing: pacing, then silence trimming.

This module exists because of a real sync bug. Gemini TTS returns audio with a variable amount of
trailing silence. If you measure the raw file, the last beat's span stretches across that silence:
the final caption lingers on screen with nothing being said, and the last image sits on a dead tail.
Everything before it looks fine, which is exactly the "only the last part drifts" symptom.

The fix is to make the narration file contain only speech plus a fixed, known lead-in and tail. Every
downstream duration is then measured from a file whose end really is the end of the voice.
"""
import wave
from pathlib import Path

import numpy as np

from .media import binary, run

TARGET_RATE = 48000
WINDOW_MS = 20


def _atempo_chain(tempo: float) -> str:
    """atempo only accepts 0.5-2.0 per instance, so compose several for anything outside that."""
    tempo = max(0.5, min(2.0, float(tempo)))
    if abs(tempo - 1.0) < 0.005:
        return "anull"
    return f"atempo={tempo:.4f}"


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        frames = frames.reshape(-1, channels).mean(axis=1)
    return frames, rate


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((clipped * 32767).astype("<i2").tobytes())


def speech_bounds(samples: np.ndarray, rate: int, floor: float = 0.015) -> tuple[int, int]:
    """First and last sample index that actually carries voice, via a 20 ms RMS envelope."""
    window = max(1, int(rate * WINDOW_MS / 1000))
    usable = (len(samples) // window) * window
    if usable == 0:
        return 0, len(samples)
    envelope = np.sqrt((samples[:usable].reshape(-1, window) ** 2).mean(axis=1))
    peak = float(envelope.max()) if envelope.size else 0.0
    if peak <= 0:
        return 0, len(samples)
    threshold = max(floor, peak * 0.06)
    voiced = np.flatnonzero(envelope > threshold)
    if voiced.size == 0:
        return 0, len(samples)
    return int(voiced[0] * window), int(min(len(samples), (voiced[-1] + 1) * window))


def pace_and_trim(source: Path, destination: Path, tempo: float, lead_ms: int, tail_ms: int) -> float:
    """Resample, apply the pacing tempo, trim dead air, and return the exact final duration."""
    staged = destination.parent / (destination.stem + "-paced.wav")
    run(
        [
            binary("ffmpeg"), "-y", "-v", "error", "-i", str(source),
            "-af", f"aresample={TARGET_RATE}:first_pts=0,{_atempo_chain(tempo)},dynaudnorm=f=250:g=7:p=0.6",
            "-ac", "1", "-ar", str(TARGET_RATE), "-c:a", "pcm_s16le", str(staged),
        ],
        timeout=300,
    )

    samples, rate = _read_wav(staged)
    start, end = speech_bounds(samples, rate)
    lead = np.zeros(int(rate * lead_ms / 1000), dtype=np.float32)
    tail = np.zeros(int(rate * tail_ms / 1000), dtype=np.float32)

    # short fades at the splice points so trimming cannot introduce a click
    body = samples[start:end].copy()
    ramp = max(1, int(rate * 0.006))
    if len(body) > 2 * ramp:
        body[:ramp] *= np.linspace(0.0, 1.0, ramp)
        body[-ramp:] *= np.linspace(1.0, 0.0, ramp)

    final = np.concatenate([lead, body, tail])
    _write_wav(destination, final, rate)
    staged.unlink(missing_ok=True)
    return len(final) / rate


def probe_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def speech_window(path: Path) -> tuple[float, float]:
    """Where the voice starts and stops inside an already-trimmed file, in seconds."""
    samples, rate = _read_wav(path)
    start, end = speech_bounds(samples, rate)
    return start / rate, end / rate
