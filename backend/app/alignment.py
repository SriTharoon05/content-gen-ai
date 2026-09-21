"""Word-level alignment.

Captions only look right when every word's start and end come from the audio itself. Three
providers, tried in order of accuracy:

  groq      Whisper large-v3-turbo with word timestamps. Works on every supported language,
            including Tamil, Telugu, Malayalam, Kannada and Hindi.
  vosk      offline English-only fallback
  estimate  weighted guess from word length; last resort, and reported as such

Two word lists come back from every provider:

  reference  the script's own words, timed. Beat boundaries are computed from these, so shot spans
             always line up with the script regardless of transcription quality.
  captions   what actually gets burned in. Normally the reference words; but when the transcriber
             and the script disagree badly (heavy code-mixing, a retake, an unusual script), the
             transcriber's own words are used instead, because their timings are exact and a caption
             that matches the audio beats a caption that matches the plan.
"""
import json
import logging
import re
import wave
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import httpx

from .config import ROOT
from .key_pool import is_auth_error, is_rate_limited, pool
from .settings_store import cfg

log = logging.getLogger("alignment")
VOSK_MODEL = ROOT / "models/vosk-model-small-en-us-0.15"
WORD_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def normalized(word: str) -> str:
    """Script-agnostic normalisation: keep letters and digits in any alphabet."""
    return "".join(WORD_RE.findall((word or "").lower().replace("\u2019", "'")))


def vosk_available() -> bool:
    return VOSK_MODEL.exists()


@lru_cache(maxsize=1)
def _vosk_model():
    if not VOSK_MODEL.exists():
        return None
    try:
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)
        return Model(str(VOSK_MODEL))
    except Exception as error:  # noqa: BLE001
        log.warning("vosk model could not be loaded: %s", error)
        return None


# ------------------------------------------------------------------------------- transcribers


def _heard_groq(audio: Path, language: str) -> list[dict]:
    """Groq's Whisper endpoint, with the key pool rotating exactly like every other provider."""
    keys = pool("groq")
    endpoint = cfg("align", "groq_endpoint")
    model = cfg("align", "groq_model")
    timeout = httpx.Timeout(float(cfg("runtime", "http_timeout_seconds", default=240)), connect=20.0)
    payload = audio.read_bytes()
    import hashlib
    fingerprint = hashlib.sha256(payload + str(model).encode() + language.encode()).hexdigest()
    cached = audio.with_suffix('.words.json')
    if cached.exists():
        saved = json.loads(cached.read_text(encoding='utf-8'))
        if saved.get('fingerprint') == fingerprint:
            return saved['words']

    last_error: Exception | None = None
    for attempt in range(max(2, len(keys))):
        index, key = keys.acquire()
        try:
            data = {
                "model": model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
                "temperature": "0",
            }
            if language and language != "auto":
                data["language"] = language
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (audio.name, payload, "audio/wav")},
                    data=data,
                )
            if response.status_code in (401, 403):
                keys.mark_dead(index)
                raise RuntimeError(f"Groq key rejected ({response.status_code})")
            if response.status_code == 429:
                keys.penalise(index)
                raise RuntimeError("Groq rate limited")
            response.raise_for_status()
            body = response.json()

            words = body.get("words")
            if not words:  # some responses nest words inside segments
                words = [w for segment in body.get("segments", []) for w in segment.get("words", [])]
            out = []
            for entry in words or []:
                text = (entry.get("word") or entry.get("text") or "").strip()
                if not text:
                    continue
                out.append({"word": text, "start": float(entry["start"]), "end": float(entry["end"])})
            if not out:
                raise RuntimeError("Groq returned no word timestamps")
            cached.write_text(json.dumps({'fingerprint': fingerprint, 'words': out}), encoding='utf-8')
            return out
        except Exception as error:  # noqa: BLE001
            last_error = error
            if is_auth_error(error):
                keys.mark_dead(index)
            elif is_rate_limited(error):
                keys.penalise(index)
            log.warning("groq transcription attempt %d failed: %s", attempt + 1, error)
    raise RuntimeError(f"Groq transcription failed: {last_error}")


def _heard_vosk(audio: Path) -> list[dict]:
    model = _vosk_model()
    if model is None:
        raise RuntimeError("vosk model is not installed")
    from vosk import KaldiRecognizer

    with wave.open(str(audio), "rb") as handle:
        if handle.getframerate() != 16000 or handle.getnchannels() != 1:
            raise RuntimeError("vosk needs 16 kHz mono audio")
        pcm = handle.readframes(handle.getnframes())

    recognizer = KaldiRecognizer(model, 16000)
    recognizer.SetWords(True)
    heard: list[dict] = []
    for offset in range(0, len(pcm), 8000):
        if recognizer.AcceptWaveform(pcm[offset : offset + 8000]):
            heard.extend(json.loads(recognizer.Result()).get("result", []))
    heard.extend(json.loads(recognizer.FinalResult()).get("result", []))
    return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for w in heard]


def _sixteen_k(audio: Path, work: Path) -> Path:
    """One canonical 16 kHz mono copy, reused by every transcriber and by the pitch analyser."""
    from .media import binary, run

    work.mkdir(parents=True, exist_ok=True)
    target = work / ("align-" + audio.stem + ".wav")
    if not target.exists() or target.stat().st_mtime < audio.stat().st_mtime:
        run(
            [binary("ffmpeg"), "-y", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(target)],
            timeout=300,
        )
    return target


# ------------------------------------------------------------------------------------ matching


def _estimate(words: list[str], horizon: float) -> list[dict]:
    weights = [max(1, len(normalized(w)) or 1) for w in words]
    total = sum(weights) or 1
    cursor = 0.0
    out = []
    for word, weight in zip(words, weights):
        end = cursor + horizon * weight / total
        out.append({"word": word, "start": cursor, "end": end})
        cursor = end
    return out


def _match(reference: list[str], heard: list[dict], horizon: float) -> tuple[list[dict], float]:
    """Map heard timings onto the script's words. Unmatched runs are spread between their neighbours."""
    if not heard:
        return _estimate(reference, horizon), 0.0

    matcher = SequenceMatcher(
        None, [normalized(w) for w in reference], [normalized(w["word"]) for w in heard], autojunk=False
    )
    timed: list[tuple[float, float] | None] = [None] * len(reference)
    matched = 0
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == "equal":
            for j, k in zip(range(a, b), range(c, d)):
                timed[j] = (heard[k]["start"], heard[k]["end"])
                matched += 1
        elif b > a:
            start = heard[c - 1]["end"] if c > 0 else 0.0
            end = heard[c]["start"] if c < len(heard) else horizon
            if end <= start:
                end = min(horizon, start + 0.22 * (b - a))
            weights = [max(1, len(normalized(reference[i])) or 1) for i in range(a, b)]
            total = sum(weights)
            cursor = start
            for index, weight in zip(range(a, b), weights):
                stop = cursor + (end - start) * weight / total
                timed[index] = (cursor, stop)
                cursor = stop

    ratio = matched / len(reference) if reference else 0.0
    cursor = 0.0
    out = []
    for word, span in zip(reference, timed):
        start, end = span if span else (cursor, cursor + 0.2)
        out.append({"word": word, "start": float(start), "end": float(end)})
        cursor = end
    return out, ratio


def monotonic(words: list[dict], total: float) -> list[dict]:
    """Guarantee every word keeps a visible, non-overlapping slot inside [0, total].

    This is the invariant that stops captions from being skipped or from sticking. Words are never
    dropped: if the tail runs past the end of the audio, the whole sequence is compressed to fit.
    """
    if not words:
        return []
    floor = float(cfg("video", "caption_min_word_seconds", default=0.10))
    cap = float(cfg("video", "caption_max_word_seconds", default=1.1))
    ceiling = max(floor * len(words), total - 0.03)

    cleaned = []
    cursor = 0.0
    for entry in words:
        start = max(cursor, min(float(entry["start"]), ceiling))
        end = min(max(start + floor, float(entry["end"])), start + cap)
        cleaned.append({"word": entry["word"], "start": start, "end": end})
        cursor = end

    overflow = cleaned[-1]["end"] - ceiling
    if overflow > 0:
        # compress uniformly rather than truncating, so no word is lost off the end
        scale = ceiling / cleaned[-1]["end"]
        cursor = 0.0
        for entry in cleaned:
            entry["start"] = max(cursor, entry["start"] * scale)
            entry["end"] = max(entry["start"] + min(floor, ceiling / len(cleaned)), entry["end"] * scale)
            cursor = entry["end"]
        cleaned[-1]["end"] = min(cleaned[-1]["end"], ceiling)

    for entry in cleaned:
        entry["start"] = round(entry["start"], 4)
        entry["end"] = round(max(entry["end"], entry["start"] + 0.04), 4)
    return cleaned


# --------------------------------------------------------------------------------- entry point


def align(audio: Path, transcript: str, work: Path, total: float, language: str = "en") -> dict:
    """Return reference words (for shot spans) and caption words (for burn-in), plus provenance."""
    reference_words = transcript.split()
    horizon = max(0.5, total - 0.03)
    provider = cfg("align", "provider", default="groq")
    sixteen_k = _sixteen_k(audio, work)

    heard: list[dict] = []
    source = "estimated_word_timing"
    error_note = ""

    order = ["groq"]
    for candidate in order:
        try:
            if candidate == "groq":
                heard = _heard_groq(sixteen_k, language)
                source = f"groq:{cfg('align', 'groq_model')}"
            else:
                if language != "en":
                    continue  # the bundled vosk model is English-only
                heard = _heard_vosk(sixteen_k)
                source = "vosk:offline"
            if heard:
                break
        except Exception as error:  # noqa: BLE001
            error_note = str(error)[:300]
            log.warning("%s alignment unavailable: %s", candidate, error_note)
            heard = []

    if not heard:
        raise RuntimeError(f"Audio timestamp verification failed; refusing guessed captions: {error_note}")
    from .audio import speech_window
    first_voice, last_voice = speech_window(audio)
    # Whisper can attach the first word to time zero despite an intentional silent lead-in.
    heard[0]["start"] = max(float(heard[0]["start"]), first_voice)
    heard[-1]["end"] = min(float(heard[-1]["end"]), last_voice)
    reference, ratio = _match(reference_words, heard, horizon)
    threshold = float(cfg("align", "min_match_ratio", default=0.55))

    if heard:
        # The transcriber disagrees with the script. Caption what was actually said.
        captions = measured_captions(heard, total)
        caption_source = f"{source}:verbatim"
    else:
        captions = monotonic(reference, total)
        caption_source = source if heard else "estimated_word_timing"

    return {
        "reference": monotonic(reference, total),
        "captions": captions,
        "source": source,
        "caption_source": caption_source,
        "match_ratio": round(ratio, 3),
        "heard_words": len(heard),
        "note": error_note,
        "speech_window": [first_voice, last_voice],
    }


def measured_captions(heard: list[dict], total: float) -> list[dict]:
    """Preserve ASR starts: never stretch/compress all timings to fit a guessed word floor."""
    grouped = []
    for word in heard:
        grouped.append(dict(word))
        while len(grouped) > 1 and float(grouped[-1]['start']) <= float(grouped[-2]['start']):
            # Backward/shared boundaries are not reliable word boundaries. Keep
            # the measured phrase envelope and spoken order; never invent timing.
            right = grouped.pop()
            left = grouped[-1]
            left['word'] += ' ' + right['word']
            left['start'] = min(float(left['start']), float(right['start']))
            left['end'] = max(float(left['end']), float(right['end']))
    heard = grouped
    result = []
    for index, word in enumerate(heard):
        start = max(0.0, float(word["start"]))
        next_start = float(heard[index + 1]["start"]) if index + 1 < len(heard) else total
        end = min(total, next_start, float(word["end"]))
        if end <= start:
            end = min(total, next_start, start + 0.033)
        if end <= start or start >= total:
            raise RuntimeError("Invalid/zero-duration Groq word timestamp; refusing desynchronised captions")
        result.append({"word": word["word"], "start": round(start, 4), "end": round(end, 4)})
    return result


def status() -> dict:
    from .settings_store import cfg as settings

    return {
        "provider": settings("align", "provider"),
        "groq_keys": len(settings("keys", "groq", default=[]) or []),
        "groq_model": settings("align", "groq_model"),
        "vosk_installed": vosk_available(),
    }
