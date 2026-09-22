"""Atomic daily queueing; Supabase Cron wakes sleeping Render instances."""
import logging
import threading
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text
from .db import session_scope
from .models import AppSetting, Channel, Job, ScheduleRun, Video
from .providers.images import remaining_credits
from .settings_store import cfg, credits_per_image, load
log = logging.getLogger("scheduler")

def local_now():
    return datetime.now(timezone.utc) + timedelta(minutes=int(cfg("schedule", "timezone_offset_minutes", default=330)))

def window_start(now):
    hour, minute = map(int, cfg("schedule", "run_at", default="10:00").split(":"))
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return start

def due(now):
    if not cfg("schedule", "enabled", default=False):
        return False
    start = window_start(now)
    if not start <= now < start + timedelta(hours=4):
        return False
    with session_scope() as session:
        return session.get(ScheduleRun, start.date().isoformat()) is None

def plan_batch(videos_per_channel=None, channels=None, scheduled_date=None):
    count = int(videos_per_channel or cfg("schedule", "videos_per_channel", default=1))
    if not 1 <= count <= 20:
        raise ValueError("videos_per_channel must be 1–20")
    cost = max(0.0001, min(30,max(20,int(cfg('video','max_shots',default=30)))) * credits_per_image())
    budget = min(float(cfg("schedule", "daily_credit_ceiling", default=0.8)), remaining_credits())
    capacity = int((budget + 1e-9) / cost)
    queued, skipped = [], []
    with session_scope() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('story-shorts:schedule'))"))
        if scheduled_date and session.get(ScheduleRun, scheduled_date):
            return {"queued": 0, "videos": [], "already_queued": True, "skipped_channels": []}
        rows = session.scalars(select(Channel).where(Channel.enabled.is_(True)).order_by(Channel.slug)).all()
        slugs = [r.slug for r in rows if not channels or r.slug in channels]
        for slug in slugs:
            for _ in range(count):
                if len(queued) >= capacity:
                    skipped.append(slug)
                    continue
                video = Video(channel_slug=slug, options_json={"image_model":cfg('models','image_model'), **({"scheduled_date": scheduled_date} if scheduled_date else {})})
                session.add(video)
                session.flush()
                job = Job(video_id=video.id)
                session.add(job)
                session.flush()
                queued.append({"channel": slug, "video_id": video.id, "job_id": job.id})
        if scheduled_date:
            session.add(ScheduleRun(run_date=scheduled_date, status="done", queued=len(queued)))
    return {"queued": len(queued), "videos": queued, "budget_credits": budget,
            "worst_case_credits": len(slugs) * count * cost, "skipped_channels": sorted(set(skipped))}

def tick():
    load(force=True)
    now = local_now()
    result = plan_batch(channels=cfg('schedule','channels',default=[]),
                        scheduled_date=window_start(now).date().isoformat()) if due(now) else {"queued": 0}
    with session_scope() as session:
        active = session.scalar(select(Job.id).where(Job.status.in_(["queued", "running", "waiting_render"])).limit(1))
    return {**result, "active": bool(active)}

def loop(stop, poll=30):
    while not stop.is_set():
        try:
            tick()
        except Exception:
            log.exception("Scheduler tick failed; transaction rolled back")
        stop.wait(poll)

def start_background():
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop,), name="scheduler", daemon=True).start()
    return stop
