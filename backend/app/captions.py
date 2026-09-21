"""Caption rendering and shot-span derivation.

Everything here is local and free. The alignment module guarantees the word list is monotonic and
complete; this module guarantees that every one of those words gets a Dialogue line, so a caption can
neither be skipped nor left stuck on screen.
"""
import json
import math
import wave
from pathlib import Path

import numpy as np
from PIL import ImageFont

from .config import ROOT
from .settings_store import cfg

FONT = ROOT / "assets/fonts/LuckiestGuy-Regular.ttf"
PLAY_W, PLAY_H = 720, 1280


def pitch_frames(samples, sample_rate: int = 16000):
    """Autocorrelation F0 with voiced-confidence gating over 65-420 Hz."""
    samples = np.asarray(samples, dtype=np.float64)
    if sample_rate > 8000:
        stride = round(sample_rate / 8000)
        samples = samples[::stride]
        sample_rate = sample_rate / stride
    window = int(0.05 * sample_rate)
    hop = int(0.025 * sample_rate)
    if window <= 0 or hop <= 0:
        return []
    lo, hi = int(sample_rate / 420), int(sample_rate / 65)
    result = []
    for offset in range(0, max(0, len(samples) - window), hop):
        chunk = samples[offset : offset + window]
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        if rms < 0.008:
            continue
        chunk = (chunk - chunk.mean()) * np.hanning(window)
        spectrum = np.fft.rfft(chunk, n=1024)
        correlation = np.fft.irfft(spectrum * np.conj(spectrum), n=1024)[: hi + 1]
        lag = lo + int(np.argmax(correlation[lo : hi + 1]))
        if correlation[lag] / max(correlation[0], 1e-12) > 0.45:
            result.append((offset / sample_rate, sample_rate / lag, rms))
    return result


def analyze_audio(path: Path, work: Path):
    """Decode narration to 16 kHz mono once, and measure pitch frames from it."""
    from .alignment import _sixteen_k

    target = _sixteen_k(path, work)
    with wave.open(str(target), "rb") as handle:
        pcm = handle.readframes(handle.getnframes())
    samples = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768.0
    return target, pitch_frames(samples)


def animation(word: str, start: float, end: float, pitches, emphasized: bool = False) -> str:
    """Tiny to large inside one second, with the peak tracking measured narration pitch."""
    base = float(np.median([p[1] for p in pitches])) if pitches else 150.0
    voiced = [p[1] for p in pitches if start <= p[0] <= end]
    pitch = float(np.median(voiced)) if voiced else base
    relative = float(np.clip(math.log2(max(pitch, 1.0) / max(base, 1.0)), -0.5, 0.5))
    final = 100 + relative * 16
    peak = min(108.0, 103 + (4 if emphasized else 0))
    final = 100.0

    length = max(80, round((end - start) * 1000))
    rise = min(100, max(40, round(length * 0.30)))
    settle = min(length, max(rise + 20, round(length * 0.8)))

    font = ImageFont.truetype(str(FONT), 88) if FONT.exists() else ImageFont.load_default()
    try:
        measured = max(1.0, font.getlength(word.upper()))
    except Exception:  # noqa: BLE001
        measured = max(1.0, len(word) * 48)
    size = max(34, min(88, int(88 * 620 / (measured * (peak / 100)))))
    y = int(cfg("video", "caption_y", default=700))

    return (
        rf"{{\an5\pos({PLAY_W // 2},{y})\fs{size}\fscx96\fscy96"
        rf"\t(0,{rise},1,\fscx{peak:.1f}\fscy{peak:.1f})"
        rf"\t({rise},{settle},\fscx{final:.1f}\fscy{final:.1f})}}"
    )


def ass_time(value: float) -> str:
    cs = max(0, round(value * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def safe_caption(value: str) -> str:
    """Strip ASS control syntax that arrived inside natural-language text."""
    return value.replace("\\", "").replace("{", "").replace("}", "").replace("\r", "").replace("\n", " ").strip()


def write_ass(words, pitches, emphasis: set[str], path: Path, total: float, phrase_mode: bool = False) -> dict:
    """One Dialogue line per word. Never drops a word; the input is already fitted to `total`."""
    import shutil

    from .alignment import normalized

    if not FONT.exists():
        raise RuntimeError("Missing LuckiestGuy-Regular.ttf. Run: python scripts/setup_caption_assets.py")
    fonts_dir = path.parent / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FONT, fonts_dir / FONT.name)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {PLAY_W}",
        f"PlayResY: {PLAY_H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
        "MarginV,Encoding",
        "Style: Default,Luckiest Guy,88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,"
        "5,3,5,45,45,45,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    written = 0
    for word in words:
        text = safe_caption(str(word["word"]).upper())
        if not text:
            continue
        # ASS has centisecond precision. Round starts up so text cannot appear early.
        start = math.ceil(max(0.0, float(word["start"])) * 100 - 1e-7) / 100
        end = min(float(word["end"]), total)
        if end <= start:
            end = min(total, start + 0.01)
        emphasized = normalized(word["word"]) in emphasis
        if phrase_mode:
            import textwrap
            rows = textwrap.wrap(text, width=32)
            text = r'\N'.join(rows)
            override = animation(max(rows, key=len), start, end, [], False)
        else:
            override = animation(word["word"], start, end, pitches, emphasized)
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{override}{text}"
        )
        written += 1

    path.write_text("\n".join(lines), encoding="utf-8")
    return {"dialogue_lines": written, "words_in": len(words), "dropped": len(words) - written}


def beat_spans(words, beat_word_counts: list[int], total: float, min_duration: float | None = None):
    """Map word timings onto per-shot spans that tile [0, total] exactly.

    Shots shorter than `min_duration` are absorbed into the one being built, so their image is never
    bought. Returns (spans, kept_indices).
    """
    if min_duration is None:
        min_duration = float(cfg("video", "min_shot_seconds", default=0.85))

    boundaries = [0.0]
    cursor = 0
    for count in beat_word_counts:
        cursor += count
        if cursor >= len(words):
            boundaries.append(total)
            break
        previous_end = float(words[cursor - 1]["end"])
        next_start = float(words[cursor]["start"])
        boundaries.append(min(total, max(previous_end, (previous_end + next_start) / 2.0)))
    while len(boundaries) < len(beat_word_counts) + 1:
        boundaries.append(total)
    boundaries[-1] = total

    spans: list[tuple[float, float]] = []
    kept: list[int] = []
    open_start = boundaries[0]
    for index in range(len(beat_word_counts)):
        end = boundaries[index + 1]
        is_last = index == len(beat_word_counts) - 1
        if end - open_start < min_duration and not is_last:
            continue
        spans.append((open_start, end))
        kept.append(index)
        open_start = end

    if spans and spans[-1][1] - spans[-1][0] < min_duration and len(spans) > 1:
        spans.pop()
        kept.pop()
        spans[-1] = (spans[-1][0], total)
    if not spans:
        spans, kept = [(0.0, total)], [0]
    spans[-1] = (spans[-1][0], total)
    return spans, kept


def timing_report(path: Path, alignment: dict, spans, pitches, caption_stats: dict) -> dict:
    payload = {
        "timing_source": alignment["source"],
        "caption_source": alignment["caption_source"],
        "match_ratio": alignment["match_ratio"],
        "heard_words": alignment["heard_words"],
        "voiced_pitch_frames": len(pitches),
        "captions": caption_stats,
        "shots": [{"index": i, "start": round(s, 3), "end": round(e, 3)} for i, (s, e) in enumerate(spans)],
        "words": alignment["captions"],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
