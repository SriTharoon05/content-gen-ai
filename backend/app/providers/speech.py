"""Narration. The only place the paid Gemini key is used, and the only paid audio call per video.

The current TTS surface is the Interactions API: `response_format: {"type": "audio"}` with a
`speech_config` LIST that carries either one voice or two speaker/voice pairs. Three call paths are
tried in order, because SDK versions in the wild differ:

  1. `client.interactions.create(...)` when the installed SDK exposes it
  2. a direct REST call to /v1beta/interactions
  3. the older `models.generate_content` with response_modalities=["AUDIO"]

Google documents that this model occasionally returns text tokens instead of audio and fails the
request with a 500, and that vague prompts can make it read the director's notes aloud. Both are
handled: retries are automatic, and the prompt carries an explicit synthesis preamble with a clearly
labelled transcript boundary.
"""
import base64
import hashlib
import logging
import json
import time
import wave
from pathlib import Path

import httpx

from ..audio import pace_and_trim
from ..db import session_scope
from ..models import ProviderCall, new_id
from ..settings_store import cfg, tts_cost

log = logging.getLogger("speech")
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
REST_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"

PREAMBLE = (
    "Synthesize speech for the transcript below. Perform it using the profile, scene and director's "
    "notes as direction only. Never read the direction aloud, never read speaker labels aloud, and "
    "never read the square-bracket audio tags aloud: the tags tell you how to deliver the words "
    "around them. Speak only the lines under TRANSCRIPT."
)


class SpeechError(RuntimeError):
    pass


class BillingRequired(SpeechError):
    pass


def build_prompt(direction: dict, tagged_transcript: str) -> str:
    """Google's documented prompt structure: profile, scene, director's notes, context, transcript."""
    parts = [PREAMBLE, ""]
    if direction.get("audio_profile"):
        parts += [f"# AUDIO PROFILE\n{direction['audio_profile']}", ""]
    if direction.get("scene"):
        parts += [f"## THE SCENE\n{direction['scene']}", ""]
    if direction.get("directors_notes"):
        parts += [f"### DIRECTOR'S NOTES\n{direction['directors_notes']}", ""]
    if direction.get("sample_context"):
        parts += [f"### SAMPLE CONTEXT\n{direction['sample_context']}", ""]
    parts += ["#### TRANSCRIPT", tagged_transcript.strip()]
    return "\n".join(parts)


def speech_config(voice: str, cast: list[dict] | None, language: str) -> list[dict]:
    if cast and len(cast) >= 2:
        return [{"speaker": entry["speaker"], "voice": entry["voice"], "language": language} for entry in cast[:2]]
    return [{"voice": voice, "language": language}]


def _write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


def _scan_for_audio(node, found: list[bytes]) -> None:
    """Pull base64 audio out of a response whose exact shape varies between API revisions."""
    if isinstance(node, dict):
        data = node.get("data")
        mime = str(node.get("mime_type") or node.get("mimeType") or node.get("type") or "")
        if isinstance(data, str) and (mime.startswith("audio") or node.get("type") == "audio") and len(data) > 256:
            try:
                found.append(base64.b64decode(data))
                return
            except Exception:  # noqa: BLE001
                pass
        for value in node.values():
            _scan_for_audio(value, found)
    elif isinstance(node, list):
        for value in node:
            _scan_for_audio(value, found)


def _tokens_from(payload) -> int:  # noqa: ANN001
    for key in ("usage", "usage_metadata", "usageMetadata"):
        usage = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(usage, dict):
            for field in ("input_tokens", "prompt_token_count", "promptTokenCount", "inputTokens"):
                if usage.get(field):
                    return int(usage[field])
    return 0


def _via_sdk(api_key: str, model: str, prompt: str, config: list[dict]) -> tuple[bytes, int]:
    from google import genai

    with genai.Client(api_key=api_key, http_options={"timeout": 240000}) as client:
        if not hasattr(client, "interactions"):
            raise AttributeError("installed SDK has no interactions API")
        interaction = client.interactions.create(
            model=model, input=prompt,
            response_format={"type": "audio"},
            generation_config={"speech_config": config},
        )
    audio = getattr(interaction, "output_audio", None)
    if audio is None or not getattr(audio, "data", None):
        raise SpeechError("interaction returned no audio")
    usage = getattr(interaction, "usage", None)
    tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    return base64.b64decode(audio.data), tokens


def _via_rest(api_key: str, model: str, prompt: str, config: list[dict]) -> tuple[bytes, int]:
    timeout = httpx.Timeout(float(cfg("runtime", "http_timeout_seconds", default=240)), connect=20.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            REST_ENDPOINT,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "model": model,
                "input": prompt,
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": config},
            },
        )
    if response.status_code == 404:
        raise AttributeError("interactions endpoint unavailable on this project")
    response.raise_for_status()
    payload = response.json()
    found: list[bytes] = []
    _scan_for_audio(payload, found)
    if not found:
        raise SpeechError("REST response contained no audio")
    return b"".join(found), _tokens_from(payload)


def _via_generate_content(api_key: str, model: str, prompt: str, voice: str, cast: list[dict] | None):
    from google import genai
    from google.genai import types

    if cast and len(cast) >= 2:
        speech = types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker=entry["speaker"],
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=entry["voice"])
                        ),
                    )
                    for entry in cast[:2]
                ]
            )
        )
    else:
        speech = types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))
        )

    with genai.Client(api_key=api_key, http_options={"timeout": 240000}) as client:
        response = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=speech),
        )
    chunks = [
        part.inline_data.data
        for candidate in (response.candidates or [])
        for part in (getattr(getattr(candidate, "content", None), "parts", None) or [])
        if getattr(part, "inline_data", None) and part.inline_data.data
    ]
    if not chunks:
        raise SpeechError("generate_content returned no audio")
    usage = getattr(response, "usage_metadata", None)
    return b"".join(chunks), int(getattr(usage, "prompt_token_count", 0) or 0)


def _ledger(video_id: str, key: str, seconds: float, tokens: int, voice: str, language: str) -> None:
    usd = tts_cost(tokens, seconds)
    call_id = hashlib.sha256(key.encode()).hexdigest()[:32]
    with session_scope() as session:
        call = session.get(ProviderCall, call_id)
        if call is None:
            call = ProviderCall(id=call_id, video_id=video_id, idempotency_key=key, provider="gemini_tts")
            session.add(call)
        call.model = cfg("models", "tts")
        call.status = "settled"
        call.units = round(seconds, 3)
        call.usd = round(usd, 8)
        call.detail_json = {"input_tokens": tokens, "voice": voice, "language": language}


def synthesize(
    video_id: str,
    tagged_transcript: str,
    destination: Path,
    voice: str,
    direction: dict,
    language: str = "en",
    cast: list[dict] | None = None,
    tempo: float | None = None,
    force: bool = False,
) -> dict:
    """Generate, pace and trim the narration. Returns duration plus how it was produced."""
    if cfg("models", "tts") == "gemini-2.5-flash-preview-tts" and cfg("voice", "free_tts_first", default=True):
        from .free_speech import synthesize as free_synthesize
        return free_synthesize(video_id, tagged_transcript, destination, voice, direction, language, cast, tempo, force)
    if str(cfg("models", "tts")).startswith("canopylabs/"):
        from .groq_speech import synthesize as groq_synthesize
        return groq_synthesize(video_id, tagged_transcript, destination, language, tempo, force, cast=cast)
    api_key = cfg("keys", "gemini_audio_paid", default="")
    if not api_key:
        raise SpeechError("No paid Gemini audio key configured. Add it in Settings.")

    model = cfg("models", "tts")
    prompt = build_prompt(direction, tagged_transcript)
    identity = hashlib.sha256(json.dumps([model, prompt, voice, language, cast, tempo], sort_keys=True).encode()).hexdigest()
    cache = destination.with_suffix(".cache.json")
    cache_identity = json.loads(cache.read_text()).get("identity") if cache.exists() else None
    if destination.exists() and cache_identity == identity and not force:
        with wave.open(str(destination), "rb") as handle:
            return {"duration": handle.getnframes() / handle.getframerate(), "cached": True, "path": str(destination)}

    config = speech_config(voice, cast, language)
    attempts = int(cfg("runtime", "provider_retries", default=4))
    key = f"{video_id}:{language}:tts:{hashlib.sha256((prompt + voice).encode()).hexdigest()[:20]}"

    pcm: bytes | None = None
    tokens = 0
    transport = ""
    last_error: Exception | None = None

    for attempt in range(attempts):
        for path_name, call in (
            ("interactions_sdk", lambda: _via_sdk(api_key, model, prompt, config)),
            ("interactions_rest", lambda: _via_rest(api_key, model, prompt, config)),
            ("generate_content", lambda: _via_generate_content(api_key, model, prompt, voice, cast)),
        ):
            try:
                pcm, tokens = call()
                transport = path_name
                break
            except AttributeError as error:
                last_error = error  # this transport is unavailable; try the next one
                continue
            except Exception as error:  # noqa: BLE001
                if "402" in str(error) or "prepayment credits" in str(error).lower():
                    raise BillingRequired("Gemini paid credits depleted (402). Top up the GEMINI_AUDIO_PAID_KEY project, then retry this video.") from error
                last_error = error
                log.warning("tts via %s failed (attempt %d): %s", path_name, attempt + 1, error)
                continue
        if pcm:
            break
        time.sleep(min(20.0, 2.5 * (attempt + 1)))

    if not pcm:
        raise SpeechError(f"Narration generation failed after {attempts} attempts: {last_error}")

    raw = destination.parent / (destination.stem + "-raw.wav")
    _write_wav(raw, pcm)
    duration = pace_and_trim(
        raw,
        destination,
        tempo if tempo is not None else float(cfg("voice", "speech_tempo", default=1.08)),
        int(cfg("video", "lead_silence_ms", default=120)),
        int(cfg("video", "tail_silence_ms", default=250)),
    )
    raw.unlink(missing_ok=True)
    cache.write_text(json.dumps({"identity": identity}), encoding="utf-8")
    _ledger(video_id, key, duration, tokens, voice, language)
    return {
        "duration": duration,
        "cached": False,
        "transport": transport,
        "input_tokens": tokens,
        "voice": voice,
        "language": language,
        "multi_speaker": bool(cast and len(cast) >= 2),
        "path": str(destination),
    }
