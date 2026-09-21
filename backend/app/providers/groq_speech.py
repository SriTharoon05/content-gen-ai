"""Orpheus narration with bounded chunks; quotas are shared across organization keys."""
import hashlib
import io
import json
import re
import threading
import time
import wave

import httpx

from ..audio import pace_and_trim
from ..db import session_scope
from ..models import ProviderCall
from ..settings_store import cfg

_lock = threading.Lock()
_last_request = 0.0


def chunks(text):
    # Gemini stage directions and speaker labels are not spoken narration.
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"(?m)^\s*(?:Speaker\s*\d+|Narrator):\s*", "", text)
    # Prefer sentence boundaries to avoid prosody resets halfway through a thought.
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    result = []
    current = ""
    for sentence in sentences:
        if current and len(current)+1+len(sentence)>200:
            result.append(current)
            current = ""
        for word in sentence.split():
            if len(word)>200:
                raise ValueError('Orpheus narration contains a word longer than 200 characters')
            if len(current)+len(word)+bool(current)>200:
                result.append(current)
                current=''
            current=(current+' '+word).strip()
    if current:
        result.append(current)
    if not result:
        raise ValueError("Empty narration")
    return result


def request_audio(text, model, voice):
    global _last_request
    keys = cfg("keys", "groq", default=[])
    if not keys:
        raise RuntimeError("Add a Groq key in Settings")
    # Keep one key for quota retries: rotating keys does not increase org limits.
    with _lock, httpx.Client(timeout=180) as client:
        for key in keys:
            for attempt in range(3):
                time.sleep(max(0, 6.2 - (time.monotonic() - _last_request)))
                _last_request = time.monotonic()
                response = client.post("https://api.groq.com/openai/v1/audio/speech",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "voice": voice, "input": text, "response_format": "wav"})
                if response.status_code == 401:
                    break  # a replacement credential is useful only for authentication failure
                if response.status_code == 429:
                    try:
                        delay = float(response.headers.get("retry-after", "65"))
                    except ValueError:
                        delay = 65
                    if delay > 120 or attempt == 2:
                        raise RuntimeError("Groq quota exhausted; wait for reset or upgrade the organization plan. Key rotation does not add quota.")
                    time.sleep(max(1, delay))
                    continue
                if response.status_code >= 400:
                    raise RuntimeError(f"Groq speech HTTP {response.status_code}: {response.text[:600]}")
                with wave.open(io.BytesIO(response.content), "rb") as audio:
                    if audio.getnframes() == 0:
                        raise RuntimeError("Groq returned empty audio")
                return response.content
    raise RuntimeError("Groq rejected the configured speech credentials")


def synthesize(video_id, transcript, destination, language, tempo, force=False, model_override=None, cast=None):
    model = model_override or cfg("models", "tts")
    arabic = model.endswith("arabic-saudi")
    if language.split('-')[0] != ("ar" if arabic else "en"):
        raise ValueError("Selected Orpheus model does not support this video's language; choose Gemini or the matching Orpheus model")
    voice = cfg("voice", "groq_arabic_voice", default="fahad") if arabic else cfg("voice", "groq_voice", default="troy")
    parts = chunks(transcript)
    part_voices = [voice] * len(parts)
    if cast and len(cast) == 2:
        voices = ['fahad', 'noura'] if arabic else ['troy', 'hannah']
        mapping = {entry['speaker']: voices[i] for i,entry in enumerate(cast)}
        parts, part_voices = [], []
        for line in transcript.splitlines():
            if not line.strip():
                continue
            speaker, separator, content = line.partition(':')
            if not separator or speaker.strip() not in mapping:
                raise ValueError('Dialogue requires explicit known speaker labels on every line')
            for part in chunks(content):
                parts.append(part)
                part_voices.append(mapping[speaker.strip()])
    identity = hashlib.sha256(json.dumps([model, part_voices, parts, tempo]).encode()).hexdigest()
    cache = destination.with_suffix('.cache.json')
    if destination.exists() and cache.exists() and json.loads(cache.read_text()).get('identity') == identity and not force:
        with wave.open(str(destination), 'rb') as audio:
            return {"duration": audio.getnframes()/audio.getframerate(), "cached": True, "path": str(destination),
                    "voice": voice, "language": language, "multi_speaker": bool(cast),
                    "transport": "groq_orpheus", "model": model}
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.with_name(destination.stem + '-groq-raw.wav')
    paths = []
    # Fetch before opening the output WAV, so a provider failure cannot be masked
    # by wave.close() complaining about an uninitialized header.
    for index, part in enumerate(parts):
        part_path = destination.with_name(f'groq-{identity[:16]}-{index:03}.wav')
        if force or not part_path.exists():
            payload = request_audio(part, model, part_voices[index])
            temporary = part_path.with_suffix('.download')
            temporary.write_bytes(payload)
            temporary.replace(part_path)
        paths.append(part_path)
    parameters = None
    with wave.open(str(raw), 'wb') as joined:
        for part_path in paths:
            with wave.open(str(part_path), 'rb') as audio:
                shape = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
                if parameters is None:
                    parameters = shape
                    joined.setnchannels(shape[0])
                    joined.setsampwidth(shape[1])
                    joined.setframerate(shape[2])
                if shape != parameters:
                    raise RuntimeError('Groq audio chunks have inconsistent formats')
                joined.writeframes(audio.readframes(audio.getnframes()))
    duration = pace_and_trim(raw, destination, tempo if tempo is not None else 1.0,
        int(cfg('video', 'lead_silence_ms', default=120)), int(cfg('video', 'tail_silence_ms', default=250)))
    cache.write_text(json.dumps({'identity': identity}), encoding='utf-8')
    call_id = hashlib.sha256(f'{video_id}:groq:{identity}'.encode()).hexdigest()[:32]
    with session_scope() as session:
        if not session.get(ProviderCall, call_id):
            session.add(ProviderCall(id=call_id, video_id=video_id, idempotency_key=call_id,
                provider='groq_tts', model=model, status='settled', units=duration,
                usd=sum(map(len, parts)) * (40 if arabic else 22) / 1_000_000,
                detail_json={'characters': sum(map(len, parts)), 'requests': len(parts), 'voice': voice,
                    'cast': cast or [], 'part_voices':part_voices,
                    'billing_note': 'List-price estimate, not invoice cost; free-tier charge may be zero'}))
    return {'duration': duration, 'cached': False, 'path': str(destination), 'transport': 'groq_orpheus',
            'voice': voice, 'language': language, 'multi_speaker': bool(cast), 'requests': len(parts), 'model': model}
