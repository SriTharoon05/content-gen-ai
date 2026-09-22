"""Text generation: key rotation, free/paid tier routing, strict JSON, and real token metering.

Every call's token usage is attributed to the video that triggered it through a context variable, so
the dashboard can show exactly what the agents cost per video rather than an estimate.
"""
import json
import logging
import re
import time
from types import SimpleNamespace
import httpx
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from .key_pool import NoKeysConfigured, is_auth_error, is_rate_limited, pool
from .settings_store import cfg, text_rates

log = logging.getLogger("llm")
T = TypeVar("T", bound=BaseModel)
FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)

_current_video: ContextVar[str | None] = ContextVar("current_video", default=None)
_current_stage: ContextVar[str] = ContextVar("current_stage", default="agent")

THINKING_BUDGET = {"low": 512, "medium": 4096, "high": 16384}
TEXT_MODEL = "gemini-3.1-flash-lite"
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"


@contextmanager
def attribute_to(video_id: str, stage: str = "agent"):
    """Everything generated inside this block is billed to `video_id` in the ledger."""
    video_token = _current_video.set(video_id)
    stage_token = _current_stage.set(stage)
    try:
        yield
    finally:
        _current_video.reset(video_token)
        _current_stage.reset(stage_token)


class TextGenerationError(RuntimeError):
    pass


def _record_usage(model: str, response, tier: str = "free", provider: str = "gemini_text") -> None:  # noqa: ANN001
    video_id = _current_video.get()
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return
    prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    output_tokens = int(
        (getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
    )
    if not (prompt_tokens or output_tokens):
        return

    price_in, price_out = (0.25, 1.50) if tier == "paid" else (0.0, 0.0)
    usd = prompt_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out

    from .db import session_scope
    from .models import ProviderCall, new_id

    try:
        with session_scope() as session:
            session.add(
                ProviderCall(
                    id=new_id(),
                    video_id=video_id or "",
                    idempotency_key=f"text:{new_id()}",
                    provider=provider,
                    model=model,
                    status="settled",
                    credits=0.0,
                    usd=round(usd, 8),
                    units=prompt_tokens + output_tokens,
                    detail_json={
                        "stage": _current_stage.get(),
                        "input_tokens": prompt_tokens,
                        "output_tokens": output_tokens,
                        "tier": tier,
                        "service_tier": "standard",
                    },
                )
            )
    except Exception as error:  # noqa: BLE001
        # Metering must never be able to fail a production run.
        log.warning("could not record token usage: %s", error)


def _build_config(system: str, temperature: float, json_mode: bool):  # noqa: ANN202
    from google.genai import types

    kwargs = {
        "system_instruction": system or None,
        "temperature": temperature,
        "response_mime_type": "application/json" if json_mode else "text/plain",
        "max_output_tokens": 16384,
    }
    budget = THINKING_BUDGET.get(cfg("models", "thinking", default="high"))
    if budget:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="LOW")
        except Exception:  # noqa: BLE001
            pass  # older SDKs simply do not expose a thinking budget
    return types.GenerateContentConfig(**kwargs)


def _groq_text(prompt: str, system: str, temperature: float, json_mode: bool) -> str:
    keys = pool('groq_text')
    last_error = 'No available Groq text keys'
    for index, key in keys.retry_rounds(rounds=3):
        body = {'model': GROQ_TEXT_MODEL, 'temperature': temperature,
                'reasoning_effort': 'low', 'max_completion_tokens': 4096,
                'messages': [{'role': 'system', 'content': system or 'You are a helpful assistant.'},
                             {'role': 'user', 'content': prompt}]}
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
            body['messages'][0]['content'] += '\nReturn only valid JSON.'
        try:
            response = httpx.post('https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json=body, timeout=180)
            if response.status_code in (401, 403):
                keys.mark_dead(index)
                last_error = f'Groq text HTTP {response.status_code}'
                continue
            if response.status_code == 429:
                # These credentials have independent quotas: cool only this key.
                try:
                    delay = min(86400, max(65, float(response.headers.get('retry-after', '65'))))
                except ValueError:
                    delay = 65
                keys.penalise(index, delay)
                last_error = 'Groq text key quota exhausted'
                continue
            if response.status_code >= 400:
                last_error = f'Groq text HTTP {response.status_code}'
                keys.penalise(index)
                continue
            data = response.json()
            usage = data.get('usage') or {}
            _record_usage(GROQ_TEXT_MODEL, SimpleNamespace(usage_metadata=SimpleNamespace(
                prompt_token_count=usage.get('prompt_tokens', 0),
                candidates_token_count=usage.get('completion_tokens', 0))), provider='groq_text')
            choice = data['choices'][0]
            text = (choice['message'].get('content') or '').strip()
            if text and choice.get('finish_reason') != 'length':
                return text
            last_error = 'Groq text returned empty or truncated output'
            keys.penalise(index)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            last_error = f'Groq text request failed ({type(error).__name__})'
            keys.penalise(index)
    raise TextGenerationError(last_error)


def generate_text(prompt: str, system: str = "", temperature: float = 0.9, json_mode: bool = True) -> str:
    from google import genai

    last_error: Exception | None = None
    model = TEXT_MODEL
    for tier in (("free", "groq", "paid") if cfg("models", "allow_paid_text_fallback", default=True) else ("free", "groq")):
        if tier == 'groq':
            try:
                return _groq_text(prompt, system, temperature, json_mode)
            except (TextGenerationError, NoKeysConfigured) as error:
                last_error = error
                continue
        keys = pool("gemini_" + tier)
        for attempt in range(len(keys) if tier == "free" else max(2, len(keys))):
            try:
                index, key = keys.acquire(wait=False)
            except NoKeysConfigured as error:
                last_error = error
                break
            client = None
            try:
                # Keep a strong reference to the client for the full request. The google-genai
                # transport can be closed by temporary-object cleanup before the response is read.
                client = genai.Client(api_key=key, http_options={"timeout": 180000})
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=_build_config(system, temperature, json_mode),
                )
                _record_usage(model, response, tier)
                text = (response.text or "").strip()
                if text:
                    return text
                last_error = TextGenerationError("Model returned an empty response")
            except Exception as error:  # noqa: BLE001
                last_error = error
                if is_auth_error(error):
                    keys.mark_dead(index)
                    continue
                if is_rate_limited(error):
                    keys.penalise(index)
                    continue
                if any(marker in str(error).lower() for marker in ('503', 'unavailable', 'high demand', '502', '504')):
                    keys.penalise(index)
                    continue
                time.sleep(min(12.0, 1.5 * (attempt + 1)))
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
    raise TextGenerationError(f"Text generation failed on every key ({last_error})")


def strip_fences(text: str) -> str:
    cleaned = FENCE.sub("", text).strip()
    start = min([i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0], default=-1)
    if start > 0:
        cleaned = cleaned[start:]
    return cleaned


def generate_model(schema: Type[T], prompt: str, system: str = "", temperature: float = 0.9, retries: int = 4) -> T:
    """Validate against a pydantic schema and hand the validator's own complaints back for repair."""
    instruction = (
        f"{system}\n\nReturn ONLY minified JSON matching this JSON Schema. No prose, no markdown fences.\n"
        f"{json.dumps(schema.model_json_schema())}"
    )
    message = prompt
    last_error = ""
    for _ in range(retries):
        raw = generate_text(message, system=instruction, temperature=temperature)
        try:
            return schema.model_validate_json(strip_fences(raw))
        except (ValidationError, ValueError) as error:
            last_error = str(error)[:1800]
            message = (
                f"{prompt}\n\nYour previous answer was rejected by the validator:\n{last_error}\n"
                "Fix every listed problem and return corrected JSON only."
            )
            temperature = max(0.2, temperature - 0.2)
    raise TextGenerationError(f"Model could not produce a valid {schema.__name__}: {last_error}")
