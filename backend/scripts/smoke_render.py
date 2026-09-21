"""Render a complete video from synthetic images and synthetic speech. Spends nothing.

Proves FFmpeg, the zoom, the transition chain, the caption animation and the A/V sync guard all work
on your machine before any credit is spent.

    python scripts/smoke_render.py --shots 38 --seconds 56
"""
import argparse
import random
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import boot  # noqa: E402
from app.db import init_db  # noqa: E402
from app.seed import seed_all  # noqa: E402

WORDS = ("every quiet street remembers the night it changed for good and nothing after that felt "
         "ordinary again so we stayed and watched the light move across the wall until morning").split()
KINDS = ["hard_cut", "hard_cut", "hard_cut", "crossfade", "dissolve", "dip_to_black", "flash_white", "match_cut"]


def fake_image(index: int, path: Path, size=(1080, 1920)) -> None:
    random.seed(index)
    top = tuple(random.randint(20, 90) for _ in range(3))
    bottom = tuple(random.randint(90, 200) for _ in range(3))
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        ratio = y / size[1]
        draw.line([(0, y), (size[0], y)], fill=tuple(int(top[c] + (bottom[c] - top[c]) * ratio) for c in range(3)))
    draw.ellipse([size[0] // 4, size[1] // 3, size[0] * 3 // 4, size[1] * 2 // 3], outline=(255, 255, 255), width=14)
    draw.text((60, 80), f"SHOT {index:03}", fill=(255, 255, 255))
    image.save(path)


def fake_speech(path: Path, duration: float, rate: int = 24000) -> None:
    t = np.linspace(0, duration, int(duration * rate), endpoint=False)
    f0 = 140 + 45 * np.sin(2 * np.pi * 0.35 * t) + 20 * np.sin(2 * np.pi * 1.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    signal = 0.4 * np.sin(phase) + 0.15 * np.sin(2 * phase)
    envelope = 0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 2.4 * t))
    pcm = (signal * envelope * 24000).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    # deliberate trailing silence: this is exactly what used to desync the last shot
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames + b"\x00\x00" * int(rate * 1.4))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=int, default=36)
    parser.add_argument("--seconds", type=float, default=54.0)
    args = parser.parse_args()

    init_db()
    seed_all()
    from app.audio import pace_and_trim
    from app.captions import beat_spans, pitch_frames, write_ass
    from app.media import assemble, build_timeline
    from app.settings_store import cfg

    work = boot().work_root / "smoke"
    (work / "images").mkdir(parents=True, exist_ok=True)

    counts = [8] * args.shots
    total_words = sum(counts)

    raw = work / "narration-raw.wav"
    fake_speech(raw, args.seconds)
    narration = work / "narration.wav"
    total = pace_and_trim(raw, narration, 1.0, cfg("video", "lead_silence_ms"), cfg("video", "tail_silence_ms"))
    print(f"narration: {args.seconds + 1.4:.2f}s raw -> {total:.2f}s after trimming dead air")

    step = total / total_words
    words = [{"word": WORDS[i % len(WORDS)], "start": i * step, "end": (i + 1) * step - 0.02}
             for i in range(total_words)]
    spans, kept = beat_spans(words, counts, total)
    print(f"shots: {len(spans)} (merged {len(counts) - len(kept)})")

    images = []
    for index in range(len(spans)):
        path = work / "images" / f"s{index:03}.png"
        if not path.exists():
            fake_image(index, path)
        images.append(path)

    random.seed(7)
    transitions = [{"kind": "hard_cut", "duration_ms": 0, "why": "open"}]
    for _ in range(1, len(spans)):
        kind = random.choice(KINDS)
        transitions.append({"kind": kind, "duration_ms": 0 if kind in ("hard_cut", "match_cut") else 320, "why": "test"})

    if cfg("runtime", "low_memory_render", default=True):
        transitions = [{"kind": "hard_cut", "duration_ms": 0} for _ in spans]
    timeline = build_timeline(spans, transitions, int(cfg("video", "fps")), total)
    print(f"timeline: {timeline.total_frames} frames = {timeline.duration:.3f}s (narration {total:.3f}s)")
    assert abs(timeline.duration - total) < 1.0 / int(cfg("video", "fps")), "timeline must match narration length"

    with wave.open(str(narration), "rb") as handle:
        rate = handle.getframerate()
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(float) / 32768.0
    pitches = pitch_frames(samples[:: max(1, rate // 16000)], 16000)
    print(f"voiced pitch frames: {len(pitches)}")

    captions = work / "captions.ass"
    write_ass(words, pitches, {"night", "morning"}, captions, total)

    clips_dir = work / "clips"
    output = boot().outputs / "smoke-test.mp4"
    assemble(images=images, narration=narration, timeline=timeline, transitions=transitions,
             captions=captions, output=output, clips_dir=clips_dir)
    print(f"\nrendered and sync-validated: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
