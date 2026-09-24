"""FastAPI control surface for the dashboard.

Every write endpoint honours ADMIN_TOKEN when it is set. Settings use save-all semantics: the
dashboard sends the whole object and it is validated and applied as one unit.
"""
import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.requests import Request

from . import alignment, memory, registry, regenerate, scheduler, storage as _storage
from .config import boot
from .db import init_db, session_scope
from .key_pool import pool_status
from .media import binary, seconds as media_seconds
from .models import Asset, Channel, Decision, Job, MusicTrack, ProviderCall, Video
from .providers.images import image_credits_used, images_affordable, remaining_credits
from .seed import seed_all
from .settings_store import (
    LANGUAGES,
    VOICES,
    SettingsError,
    cfg,
    credits_per_image,
    language_name,
    masked,
    save_all,
    text_rates,
)
from .worker import enqueue, requeue_stuck, start_background

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


class OAuthLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if str(args[2]).startswith(('/auth/google/', '/auth/meta/')):
                args[2] = str(args[2]).split('?')[0]
                record.args = tuple(args)
        return True


logging.getLogger('uvicorn.access').addFilter(OAuthLogFilter())

AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def require_admin(request: Request) -> None:
    import secrets
    token = boot().admin_token
    if not token:
        return
    if not secrets.compare_digest(request.headers.get("authorization", "").removeprefix("Bearer ").strip(), token):
        raise HTTPException(401, "Missing or invalid admin token")


Admin = Depends(require_admin)
_stops: list = []


def lifespan_factory():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        from .remote_render import validate_configuration
        validate_configuration()
        if boot().app_env == "production" and (not boot().admin_token or not _storage.enabled()):
            raise RuntimeError("Production requires ADMIN_TOKEN, SUPABASE_URL and SUPABASE_KEY")
        init_db()
        seed_all()
        _storage.ensure_buckets()
        if os.getenv("RUN_WORKER", "1") == "1":
            recovered = requeue_stuck()
            if recovered:
                log.info("recovered %d stuck job(s)", recovered)
            stop, _threads = start_background()
            _stops.append(stop)
            log.info("in-process worker started")
        if os.getenv("RUN_SCHEDULER", "1") == "1":
            _stops.append(scheduler.start_background())
            log.info("scheduler started")
        yield
        for stop in _stops:
            stop.set()

    return lifespan


app = FastAPI(title="Story Shorts", version="3.0.0", lifespan=lifespan_factory())
from .render_api import router as render_router
app.include_router(render_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=boot().origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------------------- money


def money(usd: float) -> dict:
    return {"usd": round(usd, 6), "inr": round(usd * float(cfg("pricing", "usd_to_inr", default=96.2)), 3)}


def cost_summary() -> dict:
    price = float(cfg("pricing", "credit_price_usd", default=1.185))
    per_image = credits_per_image()
    price_in, price_out = text_rates()

    with session_scope() as session:
        rows = session.scalars(select(ProviderCall).where(ProviderCall.status == "settled")).all()
        rendered = session.scalar(select(func.count()).select_from(Video).where(Video.output_path != "")) or 0

    images = [r for r in rows if r.provider == "pollinations"]
    tts = [r for r in rows if r.provider == "gemini_tts"]
    text = [r for r in rows if r.provider == "gemini_text"]
    image_usd = sum(r.usd for r in images)
    tts_usd = sum(r.usd for r in tts)
    text_usd = sum(r.usd for r in text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    recent = len([r for r in images if r.created_at and r.created_at.replace(tzinfo=timezone.utc) >= cutoff])
    total_usd = image_usd + tts_usd + text_usd

    return {
        "credits": {
            "balance": float(cfg("pricing", "credit_balance", default=0.0)),
            "used": image_credits_used(),
            "remaining": remaining_credits(),
            "per_image": per_image,
            "model": cfg("models", "image_model"),
            "images_affordable": images_affordable(),
            "credit_price": money(price),
            "per_image_price": money(per_image * price),
        },
        "images": {"count": len(images), "spent": money(image_usd), "last_24h": recent},
        "narration": {"calls": len(tts), "seconds": round(sum(r.units for r in tts), 1), "spent": money(tts_usd)},
        "text": {
            "tier": cfg("models", "text_tier"),
            "service_tier": cfg("models", "service_tier"),
            "calls": len(text),
            "tokens": int(sum(r.units for r in text)),
            "input_rate_per_1m": money(price_in),
            "output_rate_per_1m": money(price_out),
            "spent": money(text_usd),
        },
        "total": {
            "spent": money(total_usd),
            "videos": rendered,
            "per_video": money(total_usd / rendered) if rendered else money(0.0),
        },
        "reuse_savings": registry.registry_stats(),
    }


def per_video_costs(video_id: str) -> dict:
    with session_scope() as session:
        rows = session.query(ProviderCall).filter_by(video_id=video_id).all()
    buckets: dict[str, dict] = {}
    for row in rows:
        entry = buckets.setdefault(row.provider, {"calls": 0, "usd": 0.0, "units": 0.0, "credits": 0.0})
        entry["calls"] += 1
        if row.status == "settled":
            entry["usd"] += row.usd
            entry["units"] += row.units
            entry["credits"] += row.credits
    total = sum(entry["usd"] for entry in buckets.values())
    return {
        "by_provider": {
            name: {**entry, "usd": round(entry["usd"], 6), "cost": money(entry["usd"])}
            for name, entry in buckets.items()
        },
        "text_tokens": int(buckets.get("gemini_text", {}).get("units", 0)),
        "credits": round(buckets.get("pollinations", {}).get("credits", 0.0), 5),
        "total": money(total),
    }


# -------------------------------------------------------------------------------------- health


@app.get("/api/health")
def health() -> dict:
    from .captions import FONT

    return {
        "ok": True,
        "runtime": cfg("runtime", "agent_runtime"),
        "auto_approve": cfg("runtime", "auto_approve"),
        "keys": pool_status(),
        "paid_audio_key": bool(cfg("keys", "gemini_audio_paid")),
        "text_tier": cfg("models", "text_tier"),
        "image_model": cfg("models", "image_model"),
        "credits_per_image": credits_per_image(),
        "ffmpeg": bool(shutil.which(binary("ffmpeg")) or Path(binary("ffmpeg")).exists()),
        "ffprobe": bool(shutil.which(binary("ffprobe")) or Path(binary("ffprobe")).exists()),
        "caption_font": FONT.exists(),
        "alignment": alignment.status(),
        "languages": {"primary": cfg("languages", "primary"), "additional": cfg("languages", "additional")},
        "scheduler": {"enabled": cfg("schedule", "enabled"), "run_at": cfg("schedule", "run_at")},
        "scheduler_process": os.getenv("RUN_SCHEDULER", "1") == "1",
        "admin_token_configured": bool(boot().admin_token),
        "app_env": boot().app_env,
        "credits_remaining": remaining_credits(),
        "supabase_storage": _storage.enabled(),
    }


@app.get("/health")
def lightweight_health():
    return {"ok": True}


@app.post("/api/schedule/tick")
def schedule_tick(request: Request):
    import secrets
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not boot().scheduler_token or not secrets.compare_digest(token, boot().scheduler_token):
        raise HTTPException(401, "Invalid scheduler token")
    return scheduler.tick()


@app.post("/api/story-selection/retry-backlog", dependencies=[Admin])
def retry_story_backlog():
    from .story_selection import retry_backlog
    retry_backlog()
    return {"ok": True}


@app.get("/api/costs")
def costs() -> dict:
    return cost_summary()


@app.get("/api/options")
def options() -> dict:
    """Static choice lists the dashboard renders: voices, languages, transitions, music categories."""
    return {
        "voices": [{"name": name, "descriptor": descriptor} for name, descriptor in VOICES],
        "languages": [{"code": code, "name": name} for code, name in LANGUAGES],
        "transitions": ["hard_cut", "match_cut", "crossfade", "dissolve", "dip_to_black", "flash_white", "zoom_out"],
        "music_categories": cfg("music", "categories"),
        "image_models": cfg("models", "image_catalog"),
    }


# ------------------------------------------------------------------------------------ settings


@app.get("/api/settings", dependencies=[Admin])
def get_settings() -> dict:
    return {"settings": masked()}


@app.put("/api/settings", dependencies=[Admin])
def put_settings(payload: dict) -> dict:
    """Save All. Validated and applied as one unit, or rejected in full."""
    try:
        return {"settings": save_all(payload.get("settings", payload)), "saved": True}
    except SettingsError as error:
        raise HTTPException(422, str(error)) from error


# ------------------------------------------------------------------------------------ channels


class ChannelPatch(BaseModel):
    model_config = {'str_strip_whitespace':True}
    name: str | None = Field(default=None,min_length=1,max_length=120)
    tagline: str | None = Field(default=None,max_length=255)
    niche: str | None = Field(default=None,min_length=1,max_length=3000)
    instructions: str | None = Field(default=None,max_length=20000)
    conversation: bool | None = None
    enabled: bool | None = None
    overrides: dict | None = None
    topic_seeds: list[str] | None = None
    voice_name: str | None = None


def channel_view(row: Channel) -> dict:
    from .agents.roles import channel_instructions
    return {"slug": row.slug, "name": row.name, "tagline": row.tagline, "niche": row.niche,
            'instructions': channel_instructions({'slug':row.slug,'strategy_json':row.strategy_json or {}}),
            "enabled": row.enabled, "strategy": row.strategy_json or {}, "overrides": row.overrides_json or {}}


@app.get("/api/channels", dependencies=[Admin])
def channels() -> dict:
    with session_scope() as session:
        rows = session.scalars(select(Channel)).all()
        stats = {row.slug: {'videos':0, 'ready':0} for row in rows}
        for slug, state, count in session.execute(select(Video.channel_slug,Video.state,func.count()).group_by(Video.channel_slug,Video.state)):
            if slug in stats:
                stats[slug]['videos'] += count
                if state == 'READY':
                    stats[slug]['ready'] += count
        return {"channels": [{**channel_view(r), "stats": stats[r.slug]} for r in rows]}


class ChannelCreate(BaseModel):
    model_config = {'str_strip_whitespace':True}
    slug: str = Field(pattern=r'^[a-z][a-z0-9-]{1,63}$')
    name: str = Field(min_length=1,max_length=120)
    tagline: str = Field(default='',max_length=255)
    niche: str = Field(min_length=1,max_length=3000)
    instructions: str = Field(default='',max_length=20000)
    topic_seeds: list[str] = Field(default_factory=list,max_length=100)
    conversation: bool = False


@app.post('/api/channels',dependencies=[Admin])
def create_channel(body: ChannelCreate):
    from sqlalchemy.exc import IntegrityError
    try:
        with session_scope() as session:
            if session.get(Channel,body.slug):
                raise HTTPException(409,'Channel identifier already exists')
            row=Channel(slug=body.slug,name=body.name.strip(),tagline=body.tagline.strip(),niche=body.niche.strip(),
                enabled=True,overrides_json={},strategy_json={'instructions':body.instructions,
                    'topic_seeds':[t.strip() for t in body.topic_seeds if t.strip()],
                    'conversation':body.conversation,'audience':'general adult'})
            session.add(row)
            session.flush()
            return {'channel':channel_view(row)}
    except IntegrityError:
        raise HTTPException(409,'Channel identifier already exists') from None


@app.put("/api/channels/{slug}", dependencies=[Admin])
def patch_channel(slug: str, patch: ChannelPatch) -> dict:
    with session_scope() as session:
        row = session.get(Channel, slug)
        if not row:
            raise HTTPException(404, "Unknown channel")
        if patch.enabled is not None:
            row.enabled = patch.enabled
        if patch.overrides is not None:
            row.overrides_json = patch.overrides
        strategy = dict(row.strategy_json or {})
        for field in ('name','tagline','niche'):
            value=getattr(patch,field)
            if value is not None:
                setattr(row,field,value.strip())
        if patch.instructions is not None:
            strategy['instructions']=patch.instructions
        if patch.conversation is not None:
            strategy['conversation']=patch.conversation
        if patch.topic_seeds is not None:
            strategy["topic_seeds"] = [t.strip() for t in patch.topic_seeds if t.strip()]
        if patch.voice_name is not None:
            strategy["voice_name"] = patch.voice_name
        row.strategy_json = strategy
        return {"channel": channel_view(row)}


# -------------------------------------------------------------------------------------- memory


class MemoryIn(BaseModel):
    content: str
    kind: str = "note"
    pinned: bool = False


@app.get("/api/channels/{slug}/memory")
def get_memory(slug: str) -> dict:
    return {"memory": memory.listing(slug)}


@app.post("/api/channels/{slug}/memory", dependencies=[Admin])
def post_memory(slug: str, body: MemoryIn) -> dict:
    try:
        return {"entry": memory.add(slug, body.content, body.kind, body.pinned)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.delete("/api/memory/{memory_id}", dependencies=[Admin])
def delete_memory(memory_id: str) -> dict:
    if not memory.remove(memory_id):
        raise HTTPException(404, "No such memory entry")
    return {"deleted": True}


# ----------------------------------------------------------------------------------- producing


class RunOptions(BaseModel):
    publishing_mode: Literal['settings','review','direct'] = 'settings'
    publish_platforms: list[Literal['youtube','instagram']] | None = Field(default=None, min_length=1, max_length=2)
    count: int = Field(default=1, ge=1, le=20)
    topic: str | None = None
    min_shots: int | None = None
    max_shots: int | None = None
    target_seconds: int | None = None
    voice: str | None = None
    speech_tempo: float | None = None
    pace_note: str | None = None
    agent_directs_voice: bool | None = None
    primary_language: str | None = None
    languages: list[str] | None = None
    music_enabled: bool | None = None
    music_track: str | None = None
    music_volume_pct: int | None = Field(default=None, ge=0, le=100)
    ducking: bool | None = None
    tone: str | None = None
    style_note: str | None = None
    must_include: str | None = None
    must_avoid: str | None = None

    def as_options(self) -> dict:
        data = self.model_dump(exclude_none=True)
        data.pop("count", None)
        data.pop("topic", None)
        data['image_model'] = cfg('models', 'image_model')
        return data


class BatchRequest(RunOptions):
    videos_per_channel: int = Field(default=2, ge=1, le=20)
    channels: list[str] | None = None


def _affordability(videos: int, extra_languages: int = 0) -> dict:
    per_image = credits_per_image()
    worst = videos * int(cfg("video", "max_shots", default=40)) * per_image
    return {
        "worst_case_credits": round(worst, 4),
        "remaining": remaining_credits(),
        "affordable": worst <= remaining_credits(),
        "per_image": per_image,
        "image_model": cfg("models", "image_model"),
        "extra_language_videos": videos * extra_languages,
    }


@app.post("/api/channels/{slug}/run", dependencies=[Admin])
def run_channel(slug: str, body: RunOptions) -> dict:
    with session_scope() as session:
        if not session.get(Channel, slug):
            raise HTTPException(404, "Unknown channel")
    if body.publishing_mode == 'direct':
        from .social import status, publishing_platforms
        connected = status(slug)
        missing = [p for p in publishing_platforms(body.as_options()) if not connected.get(p)]
        if missing:
            raise HTTPException(422,'Connect this channel to ' + ', '.join(missing) + ' before direct publishing')
    check = _affordability(body.count, len(body.languages or []))
    if not check["affordable"]:
        raise HTTPException(402, f"Worst case {check['worst_case_credits']} credits exceeds "
                                 f"the {check['remaining']} remaining")

    created = []
    for _ in range(body.count):
        with session_scope() as session:
            video = Video(channel_slug=slug, topic=body.topic or "", options_json=body.as_options(),
                          language=body.primary_language or cfg("languages", "primary", default="en"))
            session.add(video)
            session.flush()
            video_id = video.id
        created.append({"video_id": video_id, "job_id": enqueue(video_id)})
    return {"queued": len(created), "videos": created, **check}


@app.post("/api/run-all", dependencies=[Admin])
def run_all(body: BatchRequest) -> dict:
    with session_scope() as session:
        rows = session.scalars(select(Channel).where(Channel.enabled.is_(True))).all()
        slugs = [r.slug for r in rows if not body.channels or r.slug in body.channels]
    if not slugs:
        raise HTTPException(400, "No enabled channels selected")
    if body.publishing_mode == 'direct':
        from .social import status, publishing_platforms
        for slug in slugs:
            connected = status(slug)
            missing = [p for p in publishing_platforms(body.as_options()) if not connected.get(p)]
            if missing:
                raise HTTPException(422, f'Connect {slug} to ' + ', '.join(missing) + ' before direct publishing')

    check = _affordability(len(slugs) * body.videos_per_channel, len(body.languages or []))
    if not check["affordable"]:
        raise HTTPException(402, f"Worst case {check['worst_case_credits']} credits exceeds "
                                 f"the {check['remaining']} remaining")

    created = []
    for slug in slugs:
        for _ in range(body.videos_per_channel):
            with session_scope() as session:
                video = Video(channel_slug=slug, topic=body.topic or "", options_json=body.as_options(),
                              language=body.primary_language or cfg("languages", "primary", default="en"))
                session.add(video)
                session.flush()
                video_id = video.id
            created.append({"channel": slug, "video_id": video_id, "job_id": enqueue(video_id)})
    return {"queued": len(created), "videos": created, **check}


@app.post("/api/schedule/run-now", dependencies=[Admin])
def schedule_now() -> dict:
    return scheduler.plan_batch(channels=cfg('schedule','channels',default=[]))


# -------------------------------------------------------------------------------------- videos


def _output_exists(output_path: str) -> bool:
    """True when the video file is accessible — works for both local paths and Supabase URLs."""
    if not output_path:
        return False
    if _storage.is_supabase_url(output_path):
        return True  # URL in DB means the upload succeeded
    return Path(output_path).exists()


def video_view(video: Video, variants: list | None = None) -> dict:
    from .publishing_copy import publication_copy
    price = float(cfg("pricing", "credit_price_usd", default=1.185))
    return {
        "id": video.id,
        "parent_id": video.parent_id,
        "language": video.language,
        "language_name": language_name(video.language),
        "channel": video.channel_slug,
        "state": video.state,
        "stage_detail": video.stage_detail,
        "progress": video.progress,
        "topic": video.topic,
        "title": video.title,
        "description": video.description,
        "instagram_caption": video.instagram_caption,
        "hashtags": video.hashtags or [],
        "publication_copy": publication_copy(video),
        "made_for_kids": video.made_for_kids,
        "duration_seconds": video.duration_seconds,
        "narration_seconds": video.narration_seconds,
        "image_count": video.image_count,
        "reused_count": video.reused_count,
        "credits_spent": video.credits_spent,
        "text_tokens": video.text_tokens,
        "cost": money(video.credits_spent * price),
        "approved": video.approved,
        "output_revision": video.output_path,
        "has_output": _output_exists(video.output_path),
        "error": video.error,
        "options": video.options_json or {},
        "voice": video.voice_json or {},
        "variants": variants or [],
        "created_at": video.created_at.isoformat() if video.created_at else None,
    }


@app.get("/api/videos")
def list_videos(channel: str | None = None, state: str | None = None, limit: int = 100,
                include_variants: bool = True) -> dict:
    with session_scope() as session:
        query = select(Video).order_by(Video.created_at.desc()).limit(min(limit, 500))
        if channel:
            query = query.where(Video.channel_slug == channel)
        if state:
            query = query.where(Video.state == state)
        if not include_variants:
            query = query.where(Video.parent_id == "")
        rows = session.scalars(query).all()

        out = []
        by_parent = {}
        from .generation_timing import generation_timing
        timing_jobs = {}
        for job in session.scalars(select(Job).where(Job.video_id.in_([r.id for r in rows]),
                                                    Job.stage.in_(['produce_video', 'cf_generation']))):
            timing_jobs.setdefault(job.video_id, []).append(job)
        for child in session.scalars(select(Video).where(Video.parent_id.in_([r.id for r in rows]))):
            by_parent.setdefault(child.parent_id, []).append(child)
        for video in rows:
            children = by_parent.get(video.id, [])
            out.append(video_view(video, [
                {"id": c.id, "language": c.language, "language_name": language_name(c.language),
                 "state": c.state, "progress": c.progress, "image_model":(c.options_json or {}).get('comparison_model'),
                 "has_output": _output_exists(c.output_path)}
                for c in children
            ]))
            out[-1]['generation_timing'] = generation_timing(video, timing_jobs.get(video.id, []))
        return {"videos": out}


@app.get("/api/videos/{video_id}")
def get_video(video_id: str) -> dict:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(404, "No such video")
        decisions = session.scalars(
            select(Decision).where(Decision.video_id == video_id).order_by(Decision.created_at)
        ).all()
        children = session.query(Video).filter_by(parent_id=video_id).all()
        from .generation_timing import generation_timing
        payload = {
            'generation_timing': generation_timing(video, session.scalars(select(Job).where(
                Job.video_id == video_id, Job.stage.in_(['produce_video', 'cf_generation']))).all()),
            **video_view(video, [
                {"id": c.id, "language": c.language, "language_name": language_name(c.language),
                 "state": c.state, "progress": c.progress, "duration_seconds": c.duration_seconds, "image_model":(c.options_json or {}).get('comparison_model'),
                 "has_output": _output_exists(c.output_path), "error": c.error}
                for c in children
            ]),
            "premise": video.premise_json or {},
            "script": video.script_json or {},
            "qa": video.qa_json or {},
            "spec": video.spec_json or {},
            "decisions": [
                {"agent": d.agent, "kind": d.kind, "reasoning": d.reasoning,
                 "at": d.created_at.isoformat() if d.created_at else None}
                for d in decisions
            ],
        }
    payload["costs"] = per_video_costs(video_id)
    try:
        payload["shots"] = regenerate.shot_view(video_id)
    except Exception:  # noqa: BLE001
        payload["shots"] = []
    return payload


@app.get("/api/videos/{video_id}/shots/{shot_id}/image")
def shot_image(video_id: str, shot_id: str):
    with session_scope() as session:
        asset = session.query(Asset).filter_by(video_id=video_id, shot_id=shot_id, kind="image").first()
    if not asset:
        raise HTTPException(404, "No image for that shot yet")
    if _storage.enabled() and _storage.is_supabase_url(asset.path):
        return RedirectResponse(url=asset.path, status_code=302)
    if not Path(asset.path).exists():
        raise HTTPException(404, "No image for that shot yet")
    return FileResponse(asset.path, media_type="image/png")


@app.get('/api/videos/{video_id}/narration')
def clean_narration(video_id: str):
    with session_scope() as session:
        video = session.get(Video,video_id)
        if not video:
            raise HTTPException(404,'No such video')
        source = video.narration_path or (video.options_json or {}).get('media_manifest',{}).get('narration.wav',{}).get('url','')
    if source and _storage.is_supabase_url(source):
        return RedirectResponse(source,status_code=302)
    local = boot().work_root / video_id / 'narration.wav'
    if not local.is_file():
        raise HTTPException(404,'Clean narration unavailable for live mixing; use the saved rendered preview')
    return FileResponse(local,media_type='audio/wav')


@app.get("/api/videos/{video_id}/download")
def download(video_id: str):
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video or not video.output_path:
            raise HTTPException(404, "Nothing rendered for this video yet")
        output_path = video.output_path

    # Supabase path: redirect the browser directly to the storage URL
    if _storage.is_supabase_url(output_path):
        return RedirectResponse(url=output_path, status_code=302)

    # Local path fallback
    path = Path(output_path)
    if not path.exists():
        raise HTTPException(404, "The rendered file is missing from disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/videos/{video_id}/stream")
def stream(video_id: str):
    return download(video_id)


@app.post("/api/videos/{video_id}/approve", dependencies=[Admin])
def approve(video_id: str) -> dict:
    from .social import approve_and_publish
    try:
        return approve_and_publish(video_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.delete("/api/videos/{video_id}", dependencies=[Admin])
def delete_video(video_id: str) -> dict:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise HTTPException(404, "No such video")

        for child in session.query(Video).filter_by(parent_id=video_id).all():
            _delete_output(child.output_path)
            session.query(Asset).filter_by(video_id=child.id).delete()
            session.delete(child)

        _delete_output(video.output_path)
        session.query(Asset).filter_by(video_id=video_id).delete()
        session.delete(video)
    return {"deleted": True}


def _delete_output(output_path: str) -> None:
    """Delete a video file from Supabase Storage or local disk, whichever applies."""
    if not output_path:
        return
    if _storage.enabled() and _storage.is_supabase_url(output_path):
        try:
            key = _storage.key_from_url(output_path, _storage.VIDEOS_BUCKET)
            _storage.delete(_storage.VIDEOS_BUCKET, key)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not delete Supabase video object: %s", exc)
    else:
        Path(output_path).unlink(missing_ok=True)


# --------------------------------------------------------------------------------- regeneration


REGEN_STAGES = {
    "music": ("change_music", "free"),
    "speed": ("change_speed", "transcription_only"),
    "image": ("regenerate_image", "paid"),
    "visuals": ("regenerate_visuals", "paid"),
    "narration": ("regenerate_narration", "paid"),
    "script": ("regenerate_script", "paid"),
    "language": ("add_language", "paid"),
    "edit": ("regenerate_edit", "free"),
    "copy": ("regenerate_copy", "free"),
    "render": ("rerender", "free"),
}


@app.post("/api/videos/{video_id}/regenerate/{what}", dependencies=[Admin])
def regenerate_endpoint(video_id: str, what: str, payload: dict | None = None) -> dict:
    if what not in REGEN_STAGES:
        raise HTTPException(400, f"Regenerate one of: {', '.join(REGEN_STAGES)}")
    stage, kind = REGEN_STAGES[what]
    payload = payload or {}

    with session_scope() as session:
        video = session.get(Video, video_id, with_for_update=True)
        if not video:
            raise HTTPException(404, "No such video")
        if session.scalar(select(Job.id).where(Job.video_id == video_id, Job.status.in_(['queued', 'running', 'waiting_render']))):
            raise HTTPException(409, "This video already has a queued or running job")
        if what == 'speed':
            try:
                rate = float(payload.get('playback_rate', 0.94))
                if not 0.75 <= rate <= 1.25:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(400, "Playback speed must be between 0.75 and 1.25")
        if what == 'music':
            from .models import Publication
            from .music_edit import crop_bounds
            if video.state not in ('READY','AWAITING_APPROVAL') or not video.output_path:
                raise HTTPException(409, 'Wait for a finished video before changing music')
            if session.scalar(select(Publication.id).where(Publication.video_id == video_id)):
                raise HTTPException(409, 'This video has already entered publishing; edit an unpublished video instead')
            try:
                payload = MusicEditRequest.model_validate(payload).model_dump()
                if payload['music_track']:
                    track = session.get(MusicTrack, payload['music_track'])
                    if not track or track.archived or not track.rights_cleared:
                        raise ValueError('Select an available rights-cleared track')
                    # Channel preferences guide automatic choices, not a manual soundtrack edit.
                    start,end = crop_bounds(track.duration_seconds,
                        payload['music_start_seconds'] if payload['music_start_seconds'] is not None else track.trim_start,
                        payload['music_end_seconds'] if payload['music_end_seconds'] is not None else track.trim_end)
                    payload.update(music_start_seconds=start,music_end_seconds=end)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error

    charge = 0
    if what == "image":
        charge = 1
    elif what == "visuals":
        charge = len(payload.get("shot_ids") or [])
    if charge:
        needed = charge * credits_per_image()
        if remaining_credits() < needed:
            raise HTTPException(402, f"Needs {needed:.4f} credits, {remaining_credits():.4f} remain")

    with session_scope() as session:
        row = session.get(Video, video_id, with_for_update=True)
        if what == 'music':
            from .models import Publication
            if not row or row.state not in ('READY', 'AWAITING_APPROVAL') or session.scalar(select(Publication.id).where(Publication.video_id == video_id)):
                raise HTTPException(409, 'Video changed or entered publishing; refresh before editing')
        if session.scalar(select(Job.id).where(Job.video_id == video_id, Job.status.in_(['queued', 'running', 'waiting_render']))):
            raise HTTPException(409, "This video already has a queued or running job")
        job = Job(video_id=video_id, stage=stage, payload_json=payload, max_attempts=1)
        session.add(job)
        session.flush()
        job_id = job.id
        if what == 'music':
            row.approved = False
            row.state = 'QUEUED'
            row.stage_detail = 'Applying background music to the saved video'
            row.options_json = {**(row.options_json or {}),'force_review':True,'preview_required':False,'reviewed_output':''}
    return {"job_id": job_id, "stage": stage, "kind": kind, "images_charged": charge,
            "credits_charged": round(charge * credits_per_image(), 5)}


# --------------------------------------------------------------------------------------- music

class MusicEditRequest(BaseModel):
    music_track: str = ''
    music_volume_pct: int = Field(default=30, ge=0, le=100)
    music_start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    music_end_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    ducking: bool = True


@app.post('/api/videos/{video_id}/preview-reviewed', dependencies=[Admin])
def preview_reviewed(video_id: str, payload: dict):
    with session_scope() as s:
        v = s.get(Video, video_id, with_for_update=True)
        if not v or v.state not in ('READY','AWAITING_APPROVAL') or payload.get('output_revision') != v.output_path:
            raise HTTPException(409, 'The preview changed; play the latest completed version')
        if s.scalar(select(Job.id).where(Job.video_id == video_id, Job.status.in_(['queued','running','waiting_render']))):
            raise HTTPException(409, 'Wait for the current edit to finish')
        v.options_json = {**(v.options_json or {}),'reviewed_output':v.output_path}
    return {'reviewed':True}


def track_view(track: MusicTrack) -> dict:
    # For Supabase URLs, exists is always True (URL in DB = upload succeeded).
    # For local paths, check the filesystem.
    if _storage.enabled() and _storage.is_supabase_url(track.path):
        exists = True
    else:
        exists = Path(track.path).exists()
    return {
        "id": track.id, "name": track.name, "filename": track.filename, "category": track.category,
        "mood": track.mood, "tempo": track.tempo, "duration_seconds": track.duration_seconds,
        "trim_start": track.trim_start, "trim_end": track.trim_end,
        "default_volume_pct": track.default_volume_pct, "channels": track.channels or [],
        "rights_cleared": track.rights_cleared, "notes": track.notes, "exists": exists,
    }


@app.get("/api/music")
def list_music(category: str | None = None) -> dict:
    with session_scope() as session:
        query = select(MusicTrack).where(MusicTrack.archived.is_(False)).order_by(MusicTrack.created_at.desc())
        if category:
            query = query.where(MusicTrack.category == category)
        tracks = session.scalars(query).all()
        return {
            "tracks": [track_view(t) for t in tracks],
            "categories": cfg("music", "categories"),
            "max_intensity": cfg("music", "max_intensity"),
            "default_volume_pct": cfg("music", "default_volume_pct"),
            "default_track": cfg('music','default_track',default=''),
        }


@app.post("/api/music", dependencies=[Admin])
async def upload_music(
    file: UploadFile = File(...),
    name: str = Form(""),
    category: str = Form("neutral"),
    mood: str = Form(""),
    tempo: int = Form(0),
    trim_start: float = Form(0.0),
    trim_end: float = Form(0.0),
    default_volume_pct: int = Form(30),
    channels: str = Form(""),
    rights_cleared: bool = Form(True),
    notes: str = Form(""),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AUDIO_TYPES:
        raise HTTPException(415, f"Audio files only ({', '.join(sorted(AUDIO_TYPES))})")
    if not rights_cleared:
        raise HTTPException(422, "Only rights-cleared music can be added to the library")

    category = (category or "neutral").strip().lower()
    import re, uuid
    from starlette.concurrency import run_in_threadpool
    from .music_edit import crop_bounds
    from .media import run as media_run
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', category) or category not in cfg('music','categories'):
        raise HTTPException(422, 'Select a valid category folder from Music settings')

    # Always write to a local temp file first so media_seconds() can probe it.
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
    destination = Path(tmp_path_str)
    try:
        size = 0
        with os.fdopen(tmp_fd, "wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 40 * 1024 * 1024:
                    raise HTTPException(413, "Music files must be under 40 MB")
                handle.write(chunk)

        try:
            duration = await run_in_threadpool(media_seconds, destination)
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "That file could not be decoded as audio") from None

        try:
            start,end = crop_bounds(duration,trim_start,trim_end)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        cropped = destination.with_name(destination.stem + '-crop.m4a')
        try:
            await run_in_threadpool(media_run,[binary('ffmpeg'),'-y','-v','error','-ss',str(start),
                '-i',str(destination),'-t',str(end-start),'-vn','-c:a','aac','-b:a','160k',str(cropped)])
        except Exception:
            cropped.unlink(missing_ok=True)
            raise
        destination.unlink(missing_ok=True)
        destination = cropped
        duration = await run_in_threadpool(media_seconds, destination)
        start,end = 0.0,duration
        filename = Path(file.filename or 'music').stem + '.m4a'
        unique_name = uuid.uuid4().hex + '.m4a'

        # Upload to Supabase when configured, else keep on local disk permanently.
        if _storage.enabled():
            storage_key = f"{category}/{unique_name}"
            stored_path = await run_in_threadpool(_storage.upload, _storage.MUSIC_BUCKET, storage_key, destination, delete_local=True)
            log.info("music uploaded to Supabase: %s", stored_path)
        else:
            folder = boot().media_root / "music" / category
            folder.mkdir(parents=True, exist_ok=True)
            permanent = folder / unique_name
            shutil.move(str(destination),str(permanent))
            stored_path = str(permanent)
            destination = permanent  # so the finally block doesn't try to unlink it again

    finally:
        # Only unlink if Supabase handled it (storage.upload already deleted it)
        # or if an error happened before rename. The rename case means destination no
        # longer exists at the old path, so missing_ok=True handles it cleanly.
        if 'permanent' in locals() and destination == permanent:
            pass  # local permanent path — leave it
        else:
            destination.unlink(missing_ok=True)

    with session_scope() as session:
        track = MusicTrack(
            name=(name or Path(filename).stem)[:160],
            filename=filename,
            path=stored_path,
            category=category,
            mood=mood[:160],
            tempo=int(tempo or 0),
            duration_seconds=round(duration, 2),
            trim_start=round(start, 2),
            trim_end=round(end, 2),
            default_volume_pct=max(0, min(100, int(default_volume_pct))),
            channels=[c.strip() for c in channels.split(",") if c.strip()],
            rights_cleared=True,
            notes=notes[:1000],
        )
        session.add(track)
        session.flush()
        return {"track": track_view(track)}


class TrackPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    mood: str | None = None
    tempo: int | None = None
    trim_start: float | None = Field(default=None, ge=0)
    trim_end: float | None = Field(default=None, ge=0)
    default_volume_pct: int | None = Field(default=None, ge=0, le=100)
    channels: list[str] | None = None
    notes: str | None = None


@app.put("/api/music/{track_id}", dependencies=[Admin])
def patch_music(track_id: str, patch: TrackPatch) -> dict:
    with session_scope() as session:
        track = session.get(MusicTrack, track_id)
        if not track:
            raise HTTPException(404, "No such track")
        data = patch.model_dump(exclude_none=True)
        from .music_edit import crop_bounds
        try:
            crop_bounds(track.duration_seconds,data.get('trim_start',track.trim_start),data.get('trim_end',track.trim_end))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        if 'category' in data and data['category'] not in cfg('music','categories'):
            raise HTTPException(422,'Unknown category folder')
        if "trim_start" in data and data["trim_start"] > track.duration_seconds:
            raise HTTPException(422, "Crop start is past the end of the track")
        for field, value in data.items():
            setattr(track, field, value)
        if track.trim_end and track.trim_end <= track.trim_start:
            raise HTTPException(422, "Crop end must be after crop start")
        return {"track": track_view(track)}


@app.get("/api/music/{track_id}/preview")
def preview_music(track_id: str):
    with session_scope() as session:
        track = session.get(MusicTrack, track_id)
        if not track:
            raise HTTPException(404, "No such track")
        path, filename = track.path, track.filename

    # Supabase: redirect to the public URL directly
    if _storage.enabled() and _storage.is_supabase_url(path):
        return RedirectResponse(url=path, status_code=302)

    # Local fallback
    local = Path(path)
    if not local.exists():
        raise HTTPException(404, "No such track")
    return FileResponse(local, filename=filename)


@app.delete("/api/music/{track_id}", dependencies=[Admin])
def delete_music(track_id: str) -> dict:
    with session_scope() as session:
        track = session.get(MusicTrack, track_id)
        if not track:
            raise HTTPException(404, "No such track")
        if cfg('music','default_track',default='') == track_id:
            raise HTTPException(409,'Choose another default track before removing this one')
        track.archived = True
    return {"deleted": True, 'retained_for_history':True}


# ---------------------------------------------------------------------------------------- jobs


@app.get("/api/jobs", dependencies=[Admin])
def jobs(status: str | None = None, limit: int = 60) -> dict:
    with session_scope() as session:
        query = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 300))
        if status:
            query = query.where(Job.status == status)
        rows = session.scalars(query).all()
        return {"jobs": [
            {"id": j.id, "video_id": j.video_id, "stage": j.stage, "status": j.status,
             "attempts": j.attempts, "max_attempts": j.max_attempts, "error": (j.error or "")[:800],
             "render_task_id": (j.payload_json or {}).get('render_task_id'),
             "created_at": j.created_at.isoformat() if j.created_at else None}
            for j in rows
        ]}


@app.post('/api/jobs/{job_id}/cancel', dependencies=[Admin])
def cancel_job(job_id: str):
    with session_scope() as session:
        job = session.get(Job, job_id, with_for_update=True)
        if not job:
            raise HTTPException(404,'No such job')
        now = datetime.now(timezone.utc)
        stale = job.status == 'running' and ((job.lease_expires_at and job.lease_expires_at < now)
            or (not job.lease_expires_at and job.created_at < now-timedelta(minutes=5)))
        if job.stage == 'publish' or not (job.status == 'queued' or stale):
            raise HTTPException(409,'Only queued or expired non-publishing jobs can be cancelled')
        job.status = 'cancelled'
        job.lease_expires_at = None
        job.error = 'Cancelled by operator; saved assets retained'
        video = session.get(Video,job.video_id)
        if video:
            video.state = 'AWAITING_APPROVAL' if video.output_path else 'FAILED'
            video.error = job.error
    return {'cancelled':True}


@app.post("/api/jobs/{job_id}/retry", dependencies=[Admin])
def retry_job(job_id: str) -> dict:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "No such job")
        if job.status in ("queued", "running", "waiting_render"):
            raise HTTPException(409, "Job is already active")
        if job.stage == "publish":
            raise HTTPException(409, "Reconcile publication with the remote account before retrying")
        if job.payload_json.get('render_task_id'):
            from .remote_render import retry_task
            return retry_task(session, job)
        job.status = "queued"
        job.attempts = 0
        job.error = ""
        video = session.get(Video, job.video_id)
        if video:
            video.state = "QUEUED"
            video.error = ""
    return {"requeued": True}


@app.get("/api/registry")
def registry_summary() -> dict:
    return registry.registry_stats()


@app.get("/api/integrations", dependencies=[Admin])
def integrations():
    from .social import status
    from .models import Publication
    with session_scope() as s:
        channels = s.scalars(select(Channel)).all()
        rows = s.scalars(select(Publication).order_by(Publication.updated_at.desc()).limit(100)).all()
    return {"review_before_upload": cfg('publishing', 'review_before_upload', default=True),
        "platforms": cfg('publishing', 'platforms', default=['youtube']),
        "youtube_privacy": cfg('publishing', 'youtube_privacy', default='private'),
        "enabled": cfg('schedule', 'auto_publish', default=False),
        "channels": [status(c.slug) for c in channels], "publications": [
        {"video_id": p.video_id, "platform": p.platform, "status": p.status, "remote_id": p.remote_id,
         "requested_privacy": p.requested_privacy, "actual_privacy": p.actual_privacy, "error": p.error} for p in rows]}


@app.post('/api/channels/{slug}/youtube/connect', dependencies=[Admin])
def connect_youtube(slug: str):
    from .youtube_oauth import issue_ticket
    try:
        return {'url': issue_ticket(slug)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/auth/google/start')
def google_start(ticket: str):
    from .youtube_oauth import begin
    try:
        state, url = begin(ticket)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    response = RedirectResponse(url)
    response.set_cookie('youtube_oauth_state', state, max_age=600, httponly=True,
                        secure=boot().google_redirect_uri.startswith('https:'),
                        samesite='lax', path='/auth/google')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/auth/google/callback', response_class=HTMLResponse)
def google_callback(request: Request, state: str = '', code: str = '', error: str = ''):
    from .youtube_oauth import complete
    from html import escape
    try:
        if error or not code:
            raise ValueError('Google access was not granted. Return to Publishing and reconnect.')
        slug, name = complete(state, request.cookies.get('youtube_oauth_state', ''), code)
        message = f'Connected {name} to {slug}. You can close this tab and return to Publishing.'
        status_code = 200
    except ValueError as exc:
        message, status_code = str(exc), 400
    except Exception:
        message, status_code = 'Connection failed. Check Google API setup and reconnect from Publishing.', 502
    response = HTMLResponse('<!doctype html><html><head><title>YouTube connection</title></head>'
        '<body style="font:18px system-ui;background:#f6f7f4;color:#203847;padding:60px">'
        '<h1>YouTube connection</h1><p>' + escape(message) + '</p></body></html>', status_code=status_code)
    response.delete_cookie('youtube_oauth_state', path='/auth/google')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.post('/api/channels/{slug}/meta/connect', dependencies=[Admin])
def connect_meta(slug: str):
    from .meta_oauth import issue_ticket
    try:
        return {'url': issue_ticket(slug)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/auth/meta/start')
def meta_start(ticket: str):
    from .meta_oauth import begin
    try:
        nonce, url = begin(ticket)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    response = RedirectResponse(url)
    response.set_cookie('oauth_meta', nonce, max_age=600, httponly=True,
                        secure=boot().meta_redirect_uri.startswith('https:'), samesite='lax', path='/auth/meta')
    response.headers.update({'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'})
    return response


@app.get('/auth/meta/callback', response_class=HTMLResponse)
def meta_callback(request: Request, state: str = '', code: str = '', error: str = ''):
    from .meta_oauth import complete
    from html import escape
    try:
        if error or not code:
            raise ValueError('Meta access was not granted. Return to Publishing and reconnect.')
        slug = complete(state, request.cookies.get('oauth_meta',''), code)
        message = f'Instagram connected to {slug}. You can close this tab and return to Publishing. Nothing has been published.'
        status_code = 200
    except ValueError as exc:
        message, status_code = str(exc), 400
    except Exception:
        message, status_code = 'Meta connection failed. Check app setup and reconnect from Publishing.', 502
    response = HTMLResponse('<!doctype html><html><head><title>Instagram connection</title></head>'
        '<body style="font:18px system-ui;background:#f6f7f4;color:#203847;padding:60px">'
        '<h1>Instagram connection</h1><p>' + escape(message) + '</p></body></html>', status_code=status_code)
    response.delete_cookie('oauth_meta', path='/auth/meta')
    response.headers.update({'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'})
    return response


@app.post('/api/videos/{video_id}/upload-flow', dependencies=[Admin])
def upload_flow(video_id: str):
    from .social import route_upload
    try:
        return route_upload(video_id, explicit=True)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/api/channels/{slug}/instagram/analytics', dependencies=[Admin])
def instagram_analytics(slug: str, days: int = 28):
    from .meta_oauth import insights
    try:
        return insights(slug, days)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception:
        raise HTTPException(502, 'Instagram insights unavailable. Check Insights permission and reconnect.') from None


@app.post('/api/videos/{video_id}/youtube/verify', dependencies=[Admin])
def verify_upload(video_id: str):
    from .social import verify_youtube_publication
    try:
        return verify_youtube_publication(video_id)
    except Exception as error:
        raise HTTPException(502, 'Unable to verify YouTube status; check the connection and try again') from error


@app.post("/api/videos/{video_id}/publish/{platform}", dependencies=[Admin])
def publish_video(video_id: str, platform: str):
    from .social import request_publish
    try:
        return request_publish(video_id, platform)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post('/api/videos/{video_id}/instagram/approve', dependencies=[Admin])
def approve_instagram(video_id: str):
    from .social import request_publish
    try:
        return request_publish(video_id, 'instagram', approve=True)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/api/videos/{video_id}/instagram/verify', dependencies=[Admin])
def verify_instagram(video_id: str):
    from .social import verify_instagram_publication, InstagramPublishError
    try:
        return verify_instagram_publication(video_id)
    except (ValueError, InstagramPublishError) as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(502, 'Unable to verify Instagram; retry shortly or check the connection.') from None


@app.get("/api/channels/{slug}/analytics", dependencies=[Admin])
def channel_analytics(slug: str, days: int = 28):
    from .social import analytics
    try:
        return analytics(slug, days)
    except Exception as error:
        raise HTTPException(502, "Analytics unavailable: check channel credentials, OAuth scope and API access") from error


@app.post("/api/concepts/{concept_id}/discard", dependencies=[Admin])
def discard_concept(concept_id: str):
    import uuid
    from .models import ContentLedger
    try:
        key = uuid.UUID(concept_id)
    except ValueError as error:
        raise HTTPException(422, "Invalid concept UUID") from error
    with session_scope() as session:
        row = session.get(ContentLedger, key)
        if not row:
            raise HTTPException(404, "No such concept")
        row.status = "discarded"
    return {"status": "discarded", "id": concept_id}
