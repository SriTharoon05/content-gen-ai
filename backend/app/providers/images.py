"""Pollinations image generation: rotating keys, reserved-before-spend ledger, strict idempotency.

The price per image comes from the model catalog in Settings, so switching from Z-Image Turbo
(0.004 credits) to FLUX.1 schnell (0.002) immediately halves the cost of every subsequent image and
doubles the "images affordable" figure, with no code change and no restart.
"""
import base64
import hashlib
import time
import threading
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

from ..db import session_scope
from ..key_pool import is_auth_error, is_rate_limited, pool
from ..models import ProviderCall
from ..settings_store import cfg, credits_per_image, image_requests_per_minute


# Shared by all image threads/jobs/keys in this worker process. Run only one
# worker process per provider account; multiple replicas need a distributed limiter.
_rate_lock = threading.Lock()
_next_request: dict[str, float] = {}


def _wait_for_model(model: str) -> None:
    # Slight margin avoids boundary bursts: 1.02s at 60 RPM, 0.204s at 300 RPM.
    interval = 60.0 / image_requests_per_minute(model) * 1.02
    while True:
        with _rate_lock:
            now = time.monotonic()
            delay = _next_request.get(model, 0.0) - now
            if delay <= 0:
                _next_request[model] = now + interval
                return
        time.sleep(min(delay, 1.0))


def _cool_down_model(model: str, retry_after: str | None, attempt: int) -> None:
    delay = min(60.0, 2.0 ** (attempt + 1))
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            try:
                delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    with _rate_lock:
        _next_request[model] = max(_next_request.get(model, 0.0), time.monotonic() + delay)


class CreditExhausted(RuntimeError):
    pass


class ImageGenerationError(RuntimeError):
    pass


@dataclass
class ImageResult:
    path: Path
    cached: bool = False
    credits: float = 0.0


def image_credits_used() -> float:
    with session_scope() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderCall.credits), 0.0)).where(
                ProviderCall.provider == "pollinations", ProviderCall.status == "settled"
            )
        )
    return round(float(total or 0.0), 6)


def remaining_credits() -> float:
    return round(float(cfg("pricing", "credit_balance", default=0.0)) - image_credits_used(), 6)


def images_affordable() -> int:
    per = credits_per_image()
    return int(max(0.0, remaining_credits()) / per) if per > 0 else 0


def _reserve(video_id: str, key: str, model: str) -> tuple[str, str, float]:
    per_image = credits_per_image(model)
    usd = per_image * float(cfg("pricing", "credit_price_usd", default=1.185))
    call_id = hashlib.sha256(key.encode()).hexdigest()[:32]
    with session_scope() as session:
        existing = session.get(ProviderCall, call_id)
        if existing:
            if existing.status == 'failed':
                if remaining_credits() < per_image:
                    raise CreditExhausted('Insufficient credits to retry this image')
                existing.status = 'reserved'
                existing.credits = per_image
                existing.usd = round(usd, 8)
                existing.units = 1
            return call_id, existing.status, existing.credits
        if remaining_credits() < per_image:
            raise CreditExhausted(
                f"Only {remaining_credits():.4f} credits remain and one image on {model} costs {per_image}"
            )
        session.add(
            ProviderCall(
                id=call_id, video_id=video_id, idempotency_key=key, provider="pollinations", model=model,
                status="reserved", credits=per_image, usd=round(usd, 8), units=1,
            )
        )
    return call_id, "reserved", per_image


def _finish(call_id: str, status: str, detail: dict) -> None:
    with session_scope() as session:
        call = session.get(ProviderCall, call_id)
        if not call:
            return
        call.status = status
        call.detail_json = detail
        if status == "failed":
            call.credits = 0.0
            call.usd = 0.0
            call.units = 0


def generate_image(video_id: str, shot_id: str, prompt: str, destination: Path, model: str | None = None) -> ImageResult:
    """Generate one 9:16 still. Idempotent on (video, shot, prompt, model) so a retry never bills twice."""
    model = model or cfg("models", "image_model")
    key = f"{video_id}:image:{shot_id}:{model}:{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
    call_id, status, charged = _reserve(video_id, key, model)
    if status == "settled" and destination.exists():
        return ImageResult(destination, cached=True, credits=0.0)

    keys = pool("pollinations")
    endpoint = cfg("models", "image_endpoint")
    timeout = httpx.Timeout(float(cfg("runtime", "http_timeout_seconds", default=240)), connect=20.0)
    body = {
        "model": model,
        "prompt": prompt,
        "size": cfg("models", "image_size"),
        "n": 1,
        "response_format": "url",
    }

    attempts = int(cfg("runtime", "provider_retries", default=4))
    last_error: Exception | None = None
    for attempt in range(max(attempts, len(keys))):
        index, api_key = keys.acquire()
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                _wait_for_model(model)
                response = client.post(
                    endpoint, json=body,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if response.status_code in (401, 403):
                    keys.mark_dead(index)
                    raise ImageGenerationError(f"Key rejected ({response.status_code})")
                if response.status_code == 402:
                    _finish(call_id, "failed", {"http": 402, "body": response.text[:400]})
                    raise CreditExhausted("Provider reports no credits remaining on this key")
                if response.status_code == 429:
                    _cool_down_model(model, response.headers.get('Retry-After'), attempt)
                    keys.penalise(index)
                    raise ImageGenerationError("Rate limited")
                response.raise_for_status()

                payload = response.json()
                item = (payload.get("data") or [{}])[0]
                if item.get("b64_json"):
                    data = base64.b64decode(item["b64_json"])
                elif item.get("url"):
                    downloaded = client.get(item["url"])
                    downloaded.raise_for_status()
                    data = downloaded.content
                else:
                    raise ImageGenerationError(f"Unexpected provider response: {str(payload)[:200]}")

            if len(data) < 2048:
                raise ImageGenerationError("Provider returned a suspiciously small image")
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = destination.with_suffix(".download")
            staged.write_bytes(data)
            staged.replace(destination)
            _finish(
                call_id, "settled",
                {"shot_id": shot_id, "bytes": len(data), "model": model,
                 "sha256": hashlib.sha256(data).hexdigest(),
                 "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:16]},
            )
            return ImageResult(destination, credits=charged)
        except CreditExhausted:
            raise
        except Exception as error:  # noqa: BLE001
            last_error = error
            if is_auth_error(error):
                keys.mark_dead(index)
            elif is_rate_limited(error):
                keys.penalise(index)
            time.sleep(min(12.0, 2.0 * (attempt + 1)))

    _finish(call_id, "failed", {"error": str(last_error)[:400]})
    raise ImageGenerationError(f"Image generation failed for {shot_id}: {last_error}")
