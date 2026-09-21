"""DB-backed job queue. Survives restarts, needs no broker, scales with worker_concurrency.

Stages are dispatched by name, so the dashboard's targeted regenerations run on the same queue and
inherit the same retry and error handling as a full production run.
"""
import logging
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_, and_

from .db import session_scope
from .models import Job, Video
from .settings_store import cfg

log = logging.getLogger("worker")


def _scope(query):
    """Optional local evaluation cutoff: never resume unrelated older jobs."""
    cutoff = os.getenv('WORKER_CREATED_AFTER', '')
    if cutoff:
        query = query.where(Job.created_at >= datetime.fromisoformat(cutoff))
    return query


def enqueue(video_id: str, stage: str = "produce_video", payload: dict | None = None, max_attempts: int = 2) -> str:
    with session_scope() as session:
        job = Job(video_id=video_id, stage=stage, payload_json=payload or {}, max_attempts=max_attempts)
        session.add(job)
        session.flush()
        return job.id


def claim() -> str | None:
    with session_scope() as session:
        query = (
            _scope(select(Job))
            .where(Job.status == "queued")
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = session.scalars(query).first()
        if not job:
            return None
        job.status = "running"
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
        job.attempts += 1
        video = session.get(Video, job.video_id)
        if video and video.state in ("QUEUED", "RETRY", "FAILED"):
            video.state = "RUNNING"
            video.error = ""
        return job.id


def dispatch(stage: str, video_id: str, payload: dict) -> dict:
    if stage == 'change_music':
        from .music_edit import apply
        return apply(video_id, payload)
    if stage == 'image_variant':
        from .comparisons import produce
        return produce(video_id)
    if stage == "verification_backlog":
        from .story_selection import retry_backlog
        retry_backlog()
        return {"ok": True}
    if stage == "publish":
        from .social import publish
        return publish(video_id, payload["platform"])
    from . import regenerate
    from .pipeline import run

    handlers = {
        "change_speed": lambda: regenerate.change_speed(video_id, payload),
        "produce_video": lambda: run(video_id),
        "rerender": lambda: regenerate.rerender(video_id, payload),
        "regenerate_edit": lambda: regenerate.regenerate_edit(video_id, payload),
        "regenerate_copy": lambda: regenerate.regenerate_copy(video_id, payload),
        "regenerate_image": lambda: regenerate.regenerate_image(video_id, payload),
        "regenerate_visuals": lambda: regenerate.regenerate_visuals(video_id, payload),
        "regenerate_narration": lambda: regenerate.regenerate_narration(video_id, payload),
        "regenerate_script": lambda: regenerate.regenerate_script(video_id, payload),
        "add_language": lambda: regenerate.add_language(video_id, payload),
    }
    if stage not in handlers:
        raise ValueError(f"Unknown job stage '{stage}'")
    return handlers[stage]()


def process(job_id: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        stage, video_id, payload = job.stage, job.video_id, dict(job.payload_json or {})
        attempts, max_attempts = job.attempts, job.max_attempts
    lease_stop = threading.Event()
    def renew():
        while not lease_stop.wait(30):
            try:
                with session_scope() as session:
                    row = session.get(Job, job_id)
                    if not row or row.status != "running":
                        return
                    row.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
            except Exception:
                log.exception("Job lease renewal failed")
    with session_scope() as session:
        session.get(Job, job_id).lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
    threading.Thread(target=renew, name="job-lease", daemon=True).start()
    try:
        from . import storage
        if stage != 'publish':
            storage.restore(video_id)
        result = dispatch(stage, video_id, payload)
        if stage != 'publish':
            storage.checkpoint(video_id, cleanup=True)
        queue_comparisons(video_id, payload)
        if stage == 'produce_video':
            from .social import route_upload
            result['publishing'] = route_upload(video_id)
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = "done"
            job.lease_expires_at = None
            job.payload_json = {**(job.payload_json or {}), "result": result}
        log.info("job %s (%s) done", job_id, stage)
    except Exception as error:  # noqa: BLE001
        try:
            from . import storage
            if stage != 'publish':
                storage.checkpoint(video_id)
        except Exception:
            log.exception("Unable to persist failed job media")
        detail = f"{error}\n{traceback.format_exc()[-2000:]}"
        log.error("job %s (%s) failed: %s", job_id, stage, error)
        retryable = attempts < max_attempts and not _permanent(error)
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = "queued" if retryable else "failed"
            job.lease_expires_at = None
            job.error = detail[:4000]
            video = session.get(Video, video_id)
            if video and stage != "publish":
                video.error = str(error)[:2000]
                video.state = "RETRY" if retryable else "FAILED"
    finally:
        lease_stop.set()


def queue_comparisons(video_id: str, payload: dict) -> list[str]:
    """Persist follow-up variants after source assets are safely checkpointed.

    The parent job retains this intent across restarts. Locking each child makes
    retries idempotent, including a crash between enqueue and parent completion.
    """
    from .comparisons import create

    queued = []
    for model in payload.get('comparison_models', []):
        child_id = create(video_id, model, payload.get('comparison_batch', ''))
        with session_scope() as session:
            child = session.get(Video, child_id, with_for_update=True)
            existing = session.scalars(select(Job).where(
                Job.video_id == child_id, Job.stage == 'image_variant',
                Job.status.in_(['queued', 'running', 'done']))).first()
            if child.state not in ('READY', 'AWAITING_APPROVAL') and not existing:
                session.add(Job(video_id=child_id, stage='image_variant',
                                payload_json={}, max_attempts=2))
                child.state = 'QUEUED'
                queued.append(child_id)
    return queued


def _permanent(error: Exception) -> bool:
    """Do not burn a retry on something a retry cannot fix."""
    from .key_pool import NoKeysConfigured
    from .providers.images import CreditExhausted
    from .providers.speech import BillingRequired

    return isinstance(error, (CreditExhausted, NoKeysConfigured, BillingRequired))


def loop(stop: threading.Event, poll: float = 3.0) -> None:
    while not stop.is_set():
        try:
            requeue_stuck()
            job_id = claim()
        except Exception as error:  # noqa: BLE001
            log.error("queue claim failed: %s", error)
            stop.wait(poll)
            continue
        if job_id is None:
            stop.wait(poll)
            continue
        process(job_id)


def start_background() -> tuple[threading.Event, list[threading.Thread]]:
    stop = threading.Event()
    threads = []
    for index in range(max(1, int(cfg("runtime", "worker_concurrency", default=1)))):
        thread = threading.Thread(target=loop, args=(stop,), name=f"worker-{index}", daemon=True)
        thread.start()
        threads.append(thread)
    return stop, threads


def requeue_stuck() -> int:
    """A crash mid-job leaves rows in 'running'. Put them back on startup."""
    with session_scope() as session:
        stuck = session.scalars(_scope(select(Job)).where(Job.status == "running", or_(and_(Job.lease_expires_at.is_(None), Job.created_at < datetime.now(timezone.utc) - timedelta(minutes=3)),
            Job.lease_expires_at < datetime.now(timezone.utc))).with_for_update(skip_locked=True)).all()
        for job in stuck:
            job.status = "queued" if job.attempts < job.max_attempts else "failed"
            job.lease_expires_at = None
            if job.stage != "publish":
                video = session.get(Video, job.video_id)
                if video:
                    video.state = "RETRY" if job.status == "queued" else "FAILED"
                    video.error = "Worker stopped before completion; " + ("resuming saved checkpoints" if job.status == "queued" else "retry from dashboard")
        return len(stuck)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from .db import init_db
    from .seed import seed_all

    init_db()
    seed_all()
    log.info("recovered %d stuck job(s)", requeue_stuck())
    stop, threads = start_background()
    scheduler_stop = None
    if os.getenv("RUN_SCHEDULER", "0") == "1":
        from .scheduler import start_background as start_scheduler

        scheduler_stop = start_scheduler()
    log.info("worker running with %d thread(s); ctrl-c to stop", len(threads))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        if scheduler_stop:
            scheduler_stop.set()
        for thread in threads:
            thread.join(timeout=5)
