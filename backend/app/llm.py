"""Text generation: key rotation, free/paid tier routing, strict JSON, and real token metering.

Every call's token usage is attributed to the video that triggered it through a context variable, so
the dashboard can show exactly what the agents cost per video rather than an estimate.
"""
import json
import logging
import re
import time
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


def _record_usage(model: str, response, tier: str = "free") -> None:  # noqa: ANN001
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
                    provider="gemini_text",
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


def generate_text(prompt: str, system: str = "", temperature: float = 0.9, json_mode: bool = True) -> str:
    from google import genai

    last_error: Exception | None = None
    model = TEXT_MODEL
    for tier in (("free", "paid") if cfg("models", "allow_paid_text_fallback", default=True) else ("free",)):
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
