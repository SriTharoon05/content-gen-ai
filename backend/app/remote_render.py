"""Durable render outbox and continuation. All orchestration stays on Render."""
import hashlib
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from .config import boot
from .db import session_scope
from .models import Job, RenderTask, Video, new_id
from . import storage

current_job = ContextVar('render_job', default=None)


class RenderDeferred(Exception):
    pass


def enabled():
    return boot().render_execution_backend == 'circleci'


def validate_configuration():
    if enabled():
        settings = boot()
        required = ('circleci_token','circleci_project_slug','circleci_pipeline_definition_id','render_worker_token')
        missing = [name.upper() for name in required if not getattr(settings,name)]
        if missing:
            raise ValueError('CircleCI backend requires: ' + ', '.join(missing))


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def submit(video_id, manifest, files, continuation):
    """Snapshot immutable task-specific inputs BEFORE exposing the task to a worker."""
    job_id = current_job.get()
    if not job_id:
        raise ValueError('Remote renders must run through the durable job queue')
    if not storage.enabled() or not boot().render_worker_token:
        raise ValueError('Remote rendering requires Supabase and RENDER_WORKER_TOKEN')
    with session_scope() as s:
        existing = s.scalar(select(RenderTask).where(RenderTask.job_id == job_id))
        if existing:
            raise RenderDeferred(existing.id)
    task_id = new_id()
    entries = {}
    for name, path in files.items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Invalid render asset path')
        key = f'render-tasks/{task_id}/{name}'
        storage.upload(storage.ASSETS_BUCKET, key, Path(path), delete_local=False)
        entries[name] = {'key': key, 'sha256': digest(path)}
    storage.checkpoint(video_id)
    from .settings_store import cfg
    snapshot = {'video': {key: cfg('video', key) for key in ('width','height','fps','zoom_speed','zoom_max')},
                'runtime': {'ffmpeg_path':'ffmpeg', 'ffprobe_path':'ffprobe', 'low_memory_render':True}}
    manifest = {**manifest, 'version':1, 'settings':snapshot, 'files':entries,
                'output_key':f'renders/{video_id}/{task_id}.mp4'}
    with session_scope() as s:
        job = s.get(Job, job_id, with_for_update=True)
        s.add(RenderTask(id=task_id, job_id=job_id, video_id=video_id, manifest_json=manifest,
            continuation_json=continuation, deadline=datetime.now(timezone.utc) + timedelta(minutes=boot().remote_render_timeout_minutes)))
        job.video_id = video_id  # Localisation jobs may have started against the parent video.
        job.status = 'waiting_render'
        job.lease_expires_at = None
        job.payload_json = {**(job.payload_json or {}), 'render_task_id':task_id}
        video = s.get(Video, video_id)
        video.state = 'RENDERING'
        video.stage_detail = 'Queued for CircleCI media worker'
    # Durable outbox handles transient trigger errors, including a lost API response.
    try:
        dispatch_pending()
    except Exception:
        # The outbox is already committed. A trigger/DB outage must not rerun generation.
        pass
    raise RenderDeferred(task_id)


def dispatch_pending():
    from .circleci import trigger
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        tasks = s.scalars(select(RenderTask).where(RenderTask.status == 'queued',
            RenderTask.next_trigger_at <= now).with_for_update(skip_locked=True).limit(2)).all()
        for task in tasks:
            if task.deadline <= now:
                task.status, task.error = 'failed', 'CircleCI render deadline exceeded; retry from dashboard'
                continue
            try:
                task.pipeline_id = trigger(task.id)
                task.error = ''
                # If CircleCI never starts, dispatch again. Atomic claim makes duplicates no-ops.
                delay = 600
            except Exception as error:
                detail = str(error) if isinstance(error,(RuntimeError,ValueError)) else type(error).__name__
                task.error = f'CircleCI trigger unavailable: {detail}; retrying'[:1000]
                delay = min(600, 30 * 2 ** min(task.trigger_attempts, 4))
            task.trigger_attempts += 1
            task.next_trigger_at = now + timedelta(seconds=delay)
            video = s.get(Video,task.video_id)
            video.stage_detail = (task.error or 'CircleCI pipeline triggered; waiting for worker')[:160]


def reconcile():
    """No rendering here. Wake durable continuations; NEVER rerun provider stages."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        tasks = s.scalars(select(RenderTask).join(Job, Job.id == RenderTask.job_id)
            .where(Job.status == 'waiting_render').with_for_update(skip_locked=True)).all()
        for task in tasks:
            if task.status in ('queued','running') and task.deadline < now:
                task.status, task.error = 'failed', 'Remote render deadline exceeded; retry from dashboard'
            job = s.get(Job, task.job_id)
            if task.status == 'succeeded':
                job.status = 'queued'
                # Resume is not a generation attempt; preserve the original budget.
                job.attempts = max(0, job.attempts - 1)
            elif task.status == 'failed':
                job.status, job.error = 'failed', task.error
                video = s.get(Video, task.video_id)
                video.state, video.error = 'FAILED', task.error
    dispatch_pending()


def finish(task_id):
    """Apply only the saved completion metadata, then existing review/publishing rules."""
    from . import pipeline
    from .schemas import Premise
    from .models import Asset
    with session_scope() as s:
        task = s.get(RenderTask, task_id)
        if task.status in ('queued','running'):
            raise RenderDeferred(task_id)
        if task.status != 'succeeded':
            raise ValueError('Remote render has not succeeded')
        data, manifest = task.continuation_json, task.manifest_json
        video_id, job_id = task.video_id, task.job_id
        video = s.get(Video, video_id)
        channel = pipeline.channel_dict(video.channel_slug)
        stage = s.get(Job, job_id).stage
        video.output_path = storage.public_url(storage.VIDEOS_BUCKET, manifest['output_key'])
        video.spec_json = {**(video.spec_json or {}), **data['spec']}
        video.duration_seconds = data['duration']
        video.image_count = data.get('image_count', video.image_count)
        video.reused_count = s.query(Asset).filter_by(video_id=video_id,kind='image',reused=True).count()
        if 'options' in data:
            video.options_json = {**(video.options_json or {}), **data['options']}
        premise = Premise.model_validate(video.premise_json) if stage == 'produce_video' else None
    result = pipeline.settle(video_id, channel, premise, write_history=stage == 'produce_video')
    if stage == 'produce_video':
        queue_languages(video_id)
    return result


def retry_task(s, job):
    """Explicit operator retry creates a NEW task, never reclaims a possibly-live owner."""
    from fastapi import HTTPException
    video = s.get(Video, job.video_id, with_for_update=True)
    if s.scalar(select(Job.id).where(Job.video_id == video.id, Job.status.in_(['queued','running','waiting_render']))):
        raise HTTPException(409, 'This video has an active job')
    old = s.get(RenderTask, job.payload_json['render_task_id'], with_for_update=True)
    if old.status == 'succeeded':
        job.status, job.attempts, job.error = 'queued', 0, ''
        return {'requeued':True}
    if old.status != 'failed':
        raise HTTPException(409, 'Remote task is still active')
    task_id = new_id()
    new_job = Job(video_id=video.id, stage=job.stage, status='waiting_render',
                  payload_json={**job.payload_json, 'render_task_id':task_id})
    s.add(new_job); s.flush()
    s.add(RenderTask(id=task_id, job_id=new_job.id, video_id=video.id,
        manifest_json={**old.manifest_json, 'output_key':f'renders/{video.id}/{task_id}.mp4'},
        continuation_json=old.continuation_json,
        deadline=datetime.now(timezone.utc) + timedelta(minutes=boot().remote_render_timeout_minutes)))
    video.state, video.error = 'RENDERING', ''
    return {'requeued':True, 'job_id':new_job.id, 'render_task_id':task_id}


def queue_languages(video_id):
    from .pipeline import create_variant, resolve_options, channel_dict
    with session_scope() as s:
        video = s.get(Video, video_id)
        options = resolve_options(channel_dict(video.channel_slug), video.options_json)
        primary = video.language
    for language in options.get('languages', []):
        if language == primary:
            continue
        child_id = create_variant(video_id, language)
        with session_scope() as s:
            child = s.get(Video, child_id, with_for_update=True)
            if not s.scalar(select(Job.id).where(Job.video_id == child_id)):
                s.add(Job(video_id=child.id, stage='build_language', payload_json={'parent_id':video_id, 'language':language}))
