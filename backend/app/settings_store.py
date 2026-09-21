"""Runtime settings, stored in the database so the dashboard can change anything without a redeploy.

Two rules keep this safe:
  * `cfg()` is read at the moment of use, never captured at import time
  * the dashboard sends the WHOLE object on Save All; it is validated as one unit and written in one
    transaction, so a half-applied configuration is impossible

Secrets are returned as fingerprints, never raw values. Sending a fingerprint back means "keep this
key unchanged", which is what makes the Settings form idempotent: opening the page and pressing Save
All without touching anything is a no-op that cannot lose a key.
"""
import threading
from copy import deepcopy
from typing import Any

from .config import boot
from .db import session_scope
from .models import AppSetting

KEEP = "__keep__"

# The 30 prebuilt Gemini TTS voices, with the descriptor Google publishes for each.
VOICES = [
    ("Zephyr", "Bright"), ("Puck", "Upbeat"), ("Charon", "Informative"), ("Kore", "Firm"),
    ("Fenrir", "Excitable"), ("Leda", "Youthful"), ("Orus", "Firm"), ("Aoede", "Breezy"),
    ("Callirrhoe", "Easy-going"), ("Autonoe", "Bright"), ("Enceladus", "Breathy"), ("Iapetus", "Clear"),
    ("Umbriel", "Easy-going"), ("Algieba", "Smooth"), ("Despina", "Smooth"), ("Erinome", "Clear"),
    ("Algenib", "Gravelly"), ("Rasalgethi", "Informative"), ("Laomedeia", "Upbeat"), ("Achernar", "Soft"),
    ("Alnilam", "Firm"), ("Schedar", "Even"), ("Gacrux", "Mature"), ("Pulcherrima", "Forward"),
    ("Achird", "Friendly"), ("Zubenelgenubi", "Casual"), ("Vindemiatrix", "Gentle"), ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"), ("Sulafat", "Warm"),
]
VOICE_NAMES = {name for name, _ in VOICES}

# Supported TTS languages, front-loaded with the ones this operator asked for.
LANGUAGES = [
    ("en", "English"), ("ta", "Tamil"), ("te", "Telugu"), ("ml", "Malayalam"), ("kn", "Kannada"),
    ("hi", "Hindi"), ("mr", "Marathi"), ("bn", "Bangla"), ("gu", "Gujarati"), ("pa", "Punjabi"),
    ("ur", "Urdu"), ("ar", "Arabic"), ("id", "Indonesian"), ("ms", "Malay"), ("th", "Thai"),
    ("vi", "Vietnamese"), ("fil", "Filipino"), ("ja", "Japanese"), ("ko", "Korean"),
    ("cmn", "Chinese, Mandarin"), ("es", "Spanish"), ("pt", "Portuguese"), ("fr", "French"),
    ("de", "German"), ("it", "Italian"), ("nl", "Dutch"), ("pl", "Polish"), ("ru", "Russian"),
    ("tr", "Turkish"), ("uk", "Ukrainian"),
]
LANGUAGE_CODES = {code for code, _ in LANGUAGES}
LANGUAGE_NAMES = dict(LANGUAGES)

DEFAULTS: dict[str, Any] = {
    "keys": {
        "gemini_free": [],          # rotated free-tier text keys
        "gemini_paid": [],          # rotated paid text keys, used when models.text_tier = "paid"
        "gemini_audio_paid": "",    # narration and paid text fallback
        "pollinations": [],         # image generation
        "groq": [],                 # Whisper word alignment
    },
    "models": {
        "text": "gemini-3.1-flash-lite",
        "text_fallback": "gemini-3.1-flash-lite",
        "embedding_model": "gemini-embedding-001",
        "embedding_fallback": "gemini-embedding-001",
        "text_tier": "free",          # free | paid
        "allow_paid_text_fallback": True,
        "service_tier": "standard",   # standard | flex (flex is half price, higher latency)
        "thinking": "high",           # text is cheap; quality is not
        "tts": "gemini-3.1-flash-tts-preview",
        "image_model": "tongyi-mai/z-image-turbo",
        "image_catalog": [
            {"id": "tongyi-mai/z-image-turbo", "label": "Z-Image Turbo", "credits": 0.004, "rpm": 60},
            {"id": "black-forest-labs/flux.1-schnell", "label": "FLUX.1 schnell", "credits": 0.002, "rpm": 60},
            {"id": "lykon/dreamshaper-8-lcm", "label": "DreamShaper 8 LCM", "credits": 0.0001, "rpm": 300},
        ],
        "image_endpoint": "https://gen.pollinations.ai/v1/images/generations",
        "image_size": "1080x1920",
    },
    "video": {
        "width": 720,
        "height": 1280,
        "fps": 30,
        "min_shots": 20,
        "max_shots": 30,
        "target_seconds": 65,
        "min_seconds": 45,
        "max_seconds": 90,
        "min_shot_seconds": 0.85,
        "zoom_speed": 0.045,          # fractional zoom added per second of screen time
        "zoom_max": 0.16,             # never zoom past this, however long the shot
        "caption_y": 700,
        "caption_max_word_seconds": 1.1,
        "caption_min_word_seconds": 0.10,
        "lead_silence_ms": 120,
        "tail_silence_ms": 250,
    },
    "voice": {
        "free_tts_first": True,
        "groq_voice": "troy",
        "groq_arabic_voice": "fahad",
        "default_voice": "Charon",
        "speech_tempo": 1.0,
        "pace_note": "expressive, warm human storytelling; fluent complete thoughts, natural breaths and lively emphasis, never rushed",
        "style_extra": "",
        "allow_multi_speaker": True,
        "agent_directs_voice": True,   # let the audio agent write the whole director's-notes prompt
    },
    "languages": {
        "primary": "en",
        "additional": [],              # each extra language becomes its own video, same images
    },
    "align": {
        "provider": "groq",            # groq | vosk | estimate
        "groq_model": "whisper-large-v3-turbo",
        "groq_endpoint": "https://api.groq.com/openai/v1/audio/transcriptions",
        "min_match_ratio": 0.55,       # below this, caption the transcriber's own words instead
    },
    "music": {
        "enabled": True,
        "default_track": "",
        "default_volume_pct": 30,
        "max_intensity": 0.25,
        "ducking": True,
        "duck_threshold": 0.025,
        "duck_ratio": 8,
        "categories": ["emotion", "drama", "suspense", "thriller", "emotional", "horror", "mystery", "uplifting", "tense", "documentary", "neutral"],
        "auto_fit": True,              # trim or loop the bed to the exact video length
    },
    "reuse": {
        "enabled": False,              # distinct visuals for every beat
        "cross_channel_dedup": False,  # story-uniqueness scope
        "similarity_threshold": 0.18,
    },
    "pricing": {
        "credit_price_usd": 1.185,     # 2 credits = $2.37
        "usd_to_inr": 96.2,            # 2 credits = Rs 228
        "credit_balance": 2.0,
        # gemini-3.1-flash-lite paid tier, USD per 1M tokens
        "text_input_usd_per_1m": 0.25,
        "text_output_usd_per_1m": 1.50,
        "text_flex_discount": 0.5,
        # gemini-3.1-flash-tts-preview, USD per 1M tokens; audio bills at 25 tokens per second
        "tts_input_usd_per_1m": 1.00,
        "tts_audio_usd_per_1m": 20.00,
        "tts_tokens_per_second": 25,
    },
    "runtime": {
        "agent_runtime": "roles",
        "worker_concurrency": 1,
        "image_concurrency": 2,
        "ffmpeg_threads": 1,
        "low_memory_render": True,
        "auto_approve": True,
        "max_qa_rounds": 2,
        "ffmpeg_path": "ffmpeg",
        "ffprobe_path": "ffprobe",
        "http_timeout_seconds": 240,
        "provider_retries": 4,
    },
    "schedule": {
        "enabled": False,
        "channels": [],
        "run_at": "10:00",
        "timezone_offset_minutes": 330,
        "videos_per_channel": 1,
        "daily_credit_ceiling": 0.8,
        "auto_publish": False,
    },
        "publishing": {"youtube_privacy": "private", "review_before_upload": True, "platforms": ["youtube"]},
}

_lock = threading.Lock()
_cache: dict | None = None


def _deep_merge(base: dict, patch: dict) -> dict:
    out = deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _seed_from_env(stored: dict) -> dict:
    env = boot()
    keys = stored.setdefault("keys", {})
    if not keys.get("gemini_free") and env.gemini_free_keys:
        keys["gemini_free"] = [k.strip() for k in env.gemini_free_keys.split(",") if k.strip()]
    if not keys.get("gemini_paid") and env.gemini_paid_keys:
        keys["gemini_paid"] = [k.strip() for k in env.gemini_paid_keys.split(",") if k.strip()]
    if not keys.get("gemini_audio_paid") and env.gemini_audio_paid_key:
        keys["gemini_audio_paid"] = env.gemini_audio_paid_key.strip()
    if not keys.get("pollinations") and env.pollinations_api_keys:
        keys["pollinations"] = [k.strip() for k in env.pollinations_api_keys.split(",") if k.strip()]
    if not keys.get("groq") and env.groq_api_keys:
        keys["groq"] = [k.strip() for k in env.groq_api_keys.split(",") if k.strip()]
    return stored


def load(force: bool = False) -> dict:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        with session_scope() as session:
            row = session.get(AppSetting, "runtime")
            stored = deepcopy(row.value_json) if row else {}
        _cache = _seed_from_env(_deep_merge(DEFAULTS, stored))
        _cache["models"].update(text="gemini-3.1-flash-lite", text_fallback="gemini-3.1-flash-lite", text_tier="free", service_tier="standard")
        _cache["runtime"]["agent_runtime"] = "roles"
        _cache["align"]["provider"] = "groq"
        _cache['music']['categories'] = list(dict.fromkeys(['emotion','drama','suspense','thriller', *_cache['music']['categories']]))
        return _cache


def cfg(*path: str, default: Any = None) -> Any:
    node: Any = load()
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


# ------------------------------------------------------------------------------ derived values


def image_requests_per_minute(model_id: str) -> int:
    """Provider ceilings, including for saved catalogs predating RPM metadata."""
    return {
        'tongyi-mai/z-image-turbo': 60,
        'black-forest-labs/flux.1-schnell': 60,
        'lykon/dreamshaper-8-lcm': 300,
    }.get(model_id, 60)


def credits_per_image(model_id: str | None = None) -> float:
    """What one image costs on the selected model. Switching models changes this everywhere at once."""
    model_id = model_id or cfg("models", "image_model")
    for entry in cfg("models", "image_catalog", default=[]) or []:
        if entry.get("id") == model_id:
            try:
                return float(entry.get("credits"))
            except (TypeError, ValueError):
                return 0.004
    return 0.004


def text_rates() -> tuple[float, float]:
    """(input, output) USD per 1M tokens for the selected text tier. Free tier is zero."""
    if cfg("models", "text_tier", default="free") == "free":
        return 0.0, 0.0
    price_in = float(cfg("pricing", "text_input_usd_per_1m", default=0.25))
    price_out = float(cfg("pricing", "text_output_usd_per_1m", default=1.50))
    if cfg("models", "service_tier", default="standard") == "flex":
        discount = float(cfg("pricing", "text_flex_discount", default=0.5))
        price_in *= discount
        price_out *= discount
    return price_in, price_out


def tts_cost(input_tokens: int, audio_seconds: float) -> float:
    """TTS bills text input plus audio output, where audio is 25 tokens per second."""
    audio_tokens = audio_seconds * float(cfg("pricing", "tts_tokens_per_second", default=25))
    return (
        input_tokens / 1_000_000 * float(cfg("pricing", "tts_input_usd_per_1m", default=1.0))
        + audio_tokens / 1_000_000 * float(cfg("pricing", "tts_audio_usd_per_1m", default=20.0))
    )


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


# ---------------------------------------------------------------------------- secret handling


def fingerprint(value: str) -> str:
    """A stable, non-reversible label so the UI can show WHICH key is stored without revealing it."""
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else "****"
    return f"{KEEP}:{len(value)}:{tail}"


def is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(KEEP)


def masked() -> dict:
    view = deepcopy(load())
    keys = view["keys"]
    for field in ("gemini_free", "gemini_paid", "pollinations", "groq"):
        keys[field] = [fingerprint(k) for k in keys.get(field, [])]
    keys["gemini_audio_paid"] = fingerprint(keys.get("gemini_audio_paid", ""))
    return view


def _resolve_secrets(incoming: dict, current: dict) -> dict:
    out = deepcopy(incoming)
    keys = out.get("keys")
    if not isinstance(keys, dict):
        return out

    for field in ("gemini_free", "gemini_paid", "pollinations", "groq"):
        supplied = keys.get(field)
        if supplied is None:
            keys[field] = list(current["keys"].get(field, []))
            continue
        existing = list(current["keys"].get(field, []))
        by_fingerprint = {fingerprint(k): k for k in existing}
        resolved: list[str] = []
        for value in supplied:
            if is_fingerprint(value):
                if value in by_fingerprint:
                    resolved.append(by_fingerprint[value])
            elif str(value).strip():
                resolved.append(str(value).strip())
        keys[field] = resolved

    paid = keys.get("gemini_audio_paid")
    if paid is None or is_fingerprint(paid):
        keys["gemini_audio_paid"] = current["keys"].get("gemini_audio_paid", "")
    else:
        keys["gemini_audio_paid"] = str(paid).strip()
    return out


class SettingsError(ValueError):
    pass


def validate(candidate: dict) -> list[str]:
    problems: list[str] = []
    video, pricing, music = candidate["video"], candidate["pricing"], candidate["music"]
    runtime, voice, schedule = candidate["runtime"], candidate["voice"], candidate["schedule"]
    models, keys, languages = candidate["models"], candidate["keys"], candidate["languages"]
    align = candidate["align"]
    if candidate.get("publishing", {}).get("youtube_privacy", "private") not in ("private", "unlisted", "public"):
        problems.append("YouTube privacy must be private, unlisted or public")
    platforms = candidate.get('publishing', {}).get('platforms', ['youtube'])
    if not isinstance(platforms, list) or not platforms or any(p not in ('youtube', 'instagram') for p in platforms):
        problems.append('Choose at least one publishing destination: YouTube or Instagram')

    if not 20 <= video["min_shots"] <= video["max_shots"] <= 30:
        problems.append("Shot range must satisfy 20 <= min shots <= max shots <= 30")
    if video["fps"] not in (24, 25, 30):
        problems.append("Frame rate must be 24, 25 or 30")
    if not 45 <= video["min_seconds"] <= video["max_seconds"] <= 90:
        problems.append("Duration window must satisfy 45 <= min <= max <= 90 seconds")
    if not 0.3 <= video["min_shot_seconds"] <= 4.0:
        problems.append("Minimum shot length must be between 0.3 and 4.0 seconds")
    if not 0.0 <= video["zoom_speed"] <= 0.25:
        problems.append("Zoom speed must be between 0 and 0.25 per second")
    if not 0.0 <= video["zoom_max"] <= 0.6:
        problems.append("Maximum zoom must be between 0 and 0.6")
    if not 0.75 <= voice["speech_tempo"] <= 1.6:
        problems.append("Speech tempo must be between 0.75 and 1.6")
    if voice["default_voice"] not in VOICE_NAMES:
        problems.append(f"'{voice['default_voice']}' is not a Gemini TTS voice")
    if not 0 <= music["default_volume_pct"] <= 100:
        problems.append("Music volume must be between 0 and 100")
    if not 0 < music["max_intensity"] <= 0.5:
        problems.append("Music ceiling must be between 0 and 0.5")

    catalog = models.get("image_catalog") or []
    if not catalog:
        problems.append("Add at least one image model")
    ids = [str(entry.get("id", "")).strip() for entry in catalog]
    if len(ids) != len(set(ids)):
        problems.append("Image model ids must be unique")
    for entry in catalog:
        if not str(entry.get("id", "")).strip():
            problems.append("Every image model needs an id")
            continue
        try:
            if float(entry.get("credits")) <= 0:
                problems.append(f"Credits per image for '{entry.get('id')}' must be greater than zero")
        except (TypeError, ValueError):
            problems.append(f"Credits per image for '{entry.get('id')}' must be a number")
    if models.get("image_model") not in ids:
        problems.append("The selected image model is not in the catalog")
    if models.get("text_tier") != "free":
        problems.append("Text routing is always free-first with paid fallback")
    if models.get("service_tier") not in ("standard", "flex"):
        problems.append("Service tier must be 'standard' or 'flex'")
    if models.get("thinking") not in ("low", "medium", "high"):
        problems.append("Thinking level must be low, medium or high")

    if pricing["credit_balance"] < 0:
        problems.append("Credit balance cannot be negative")
    if pricing["usd_to_inr"] <= 0 or pricing["credit_price_usd"] <= 0:
        problems.append("Currency conversion rates must be positive")

    if runtime["agent_runtime"] != "roles":
        problems.append("Only the fixed production pipeline is supported")
    if models.get("text") != "gemini-3.1-flash-lite" or models.get("text_fallback") != "gemini-3.1-flash-lite":
        problems.append("Text generation requires gemini-3.1-flash-lite")
    if models.get("tts") not in ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts", "canopylabs/orpheus-v1-english", "canopylabs/orpheus-arabic-saudi"):
        problems.append("Choose a supported audio model")
    if voice.get("groq_voice") not in ("troy", "austin", "daniel", "autumn", "diana", "hannah"):
        problems.append("Choose a supported Groq English voice")
    if voice.get("groq_arabic_voice") not in ("fahad", "abdullah", "sultan", "lulwa", "noura", "aisha"):
        problems.append("Choose a supported Groq Arabic voice")
    if video["width"] * 16 != video["height"] * 9 or video["width"] % 2 or video["height"] % 2:
        problems.append("Video dimensions must be even and portrait 9:16")
    if runtime.get("low_memory_render", True) and (video["width"] > 720 or runtime["worker_concurrency"] != 1):
        problems.append("Render free profile requires <=720p width and one worker")
    if not 1 <= runtime["worker_concurrency"] <= 8:
        problems.append("Worker threads must be between 1 and 8")
    if not 1 <= runtime["image_concurrency"] <= 12:
        problems.append("Image concurrency must be between 1 and 12")

    if models.get("text_tier") == "paid":
        if not keys.get("gemini_paid"):
            problems.append("Paid text tier is selected but no paid Gemini key is configured")
    elif not keys.get("gemini_free"):
        problems.append("At least one free Gemini key is required")
    if not keys.get("pollinations"):
        problems.append("At least one Pollinations key is required")
    if align.get("provider") != "groq":
        problems.append("Production captions require Groq audio timestamps")
    if align.get("provider") == "groq" and not keys.get("groq"):
        problems.append("Groq alignment is selected but no Groq key is configured")

    if languages.get("primary") not in LANGUAGE_CODES:
        problems.append(f"'{languages.get('primary')}' is not a supported TTS language")
    for code in languages.get("additional") or []:
        if code not in LANGUAGE_CODES:
            problems.append(f"'{code}' is not a supported TTS language")
        elif code == languages.get("primary"):
            problems.append(f"'{language_name(code)}' is already the primary language")

    try:
        hour, minute = str(schedule["run_at"]).split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError
    except Exception:  # noqa: BLE001
        problems.append("Daily run time must look like HH:MM")
    return problems


def save_all(incoming: dict) -> dict:
    """Atomic save-all. Nothing is applied unless the whole object validates."""
    current = load()
    candidate = _deep_merge(current, _resolve_secrets(incoming, current))
    problems = validate(candidate)
    if problems:
        raise SettingsError(" • ".join(problems))
    with session_scope() as session:
        row = session.get(AppSetting, "runtime")
        if row:
            row.value_json = candidate
        else:
            session.add(AppSetting(key="runtime", value_json=candidate))
    invalidate()
    from .key_pool import reset_pools

    reset_pools()
    return masked()
