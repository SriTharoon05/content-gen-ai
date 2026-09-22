"""Narrow media-worker capability API; no admin, provider, OAuth or DB secrets leave Render."""
import hashlib
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from .config import boot
from .db import session_scope
from .models import RenderTask
from . import storage


def authorize(authorization: str = Header(default='')):
    expected = boot().render_worker_token
    if not expected or not secrets.compare_digest(authorization, 'Bearer ' + expected):
        raise HTTPException(401, 'Invalid render worker token')


router = APIRouter(prefix='/internal/render-tasks', dependencies=[Depends(authorize)])


class Owner(BaseModel):
    owner: str = Field(min_length=32, max_length=128)


class Completion(Owner):
    status: str = Field(pattern='^(succeeded|failed)$')
    error: str = Field(default='', max_length=2000)
    metrics: dict = Field(default_factory=dict)


def owner_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def owned(s, task_id, owner):
    task = s.get(RenderTask, task_id, with_for_update=True)
    if not task or not secrets.compare_digest(task.owner_hash, owner_hash(owner)):
        raise HTTPException(409, 'Render task is not owned by this worker')
    if task.deadline < datetime.now(timezone.utc):
        raise HTTPException(409, 'Render task expired')
    return task


@router.post('/{task_id}/claim')
def claim(task_id: str, body: Owner):
    with session_scope() as s:
        task = s.get(RenderTask, task_id, with_for_update=True)
        if not task:
            raise HTTPException(404, 'Unknown render task')
        if task.deadline <= datetime.now(timezone.utc):
            return {'claimed':False, 'status':task.status}
        if task.status == 'queued' and task.deadline > datetime.now(timezone.utc):
            task.status, task.owner_hash = 'running', owner_hash(body.owner)
        elif task.status != 'running' or task.owner_hash != owner_hash(body.owner):
            return {'claimed':False, 'status':task.status}
        manifest = dict(task.manifest_json)
    # Return immutable input URLs, never arbitrary DB settings or credentials.
    manifest['files'] = {name:{**entry, 'url':storage.signed_url(storage.ASSETS_BUCKET, entry['key'], 3600)}
                         for name, entry in manifest['files'].items()}
    return {'claimed':True, 'manifest':manifest}


@router.post('/{task_id}/heartbeat')
def heartbeat(task_id: str, body: Owner):
    with session_scope() as s:
        task = owned(s, task_id, body.owner)
        if task.status != 'running':
            raise HTTPException(409, 'Render task is not running')
        task.updated_at = datetime.now(timezone.utc)
    return {'ok':True}


@router.post('/{task_id}/upload-url')
def upload_url(task_id: str, body: Owner):
    with session_scope() as s:
        task = owned(s, task_id, body.owner)
        if task.status != 'running':
            raise HTTPException(409, 'Render task is not running')
        key = task.manifest_json['output_key']
    response = httpx.post(f'{storage._base()}/storage/v1/object/upload/sign/{storage.VIDEOS_BUCKET}/{key}',
                          headers={**storage._headers(), 'x-upsert':'true'}, json={}, timeout=30)
    response.raise_for_status()
    url = response.json()['url']
    return {'url': storage._base() + '/storage/v1' + url if url.startswith('/object/') else
            storage._base() + url if url.startswith('/') else url}


@router.post('/{task_id}/complete')
def complete(task_id: str, body: Completion):
    with session_scope() as s:
        task = owned(s, task_id, body.owner)
        if task.status in ('succeeded','failed'):
            if task.status != body.status:
                raise HTTPException(409, 'Render task already completed')
            return {'ok':True}
        if body.status == 'succeeded':
            url = storage.public_url(storage.VIDEOS_BUCKET, task.manifest_json['output_key'])
            response = httpx.head(url, timeout=30)
            if response.status_code != 200 or int(response.headers.get('content-length', 0)) < 2048:
                raise HTTPException(409, 'Final video is not available in Supabase')
        task.status = body.status
        task.result_json = body.metrics
        task.error = body.error
    return {'ok':True}
