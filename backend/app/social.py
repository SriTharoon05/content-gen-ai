"""Optional credential-driven publishing and YouTube analytics. No credentials go to the browser."""
import json
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, text
from .config import boot
from .db import session_scope
from .models import Publication, Video, Job, SocialConnection
from .settings_store import cfg
from . import storage

def credentials(slug):
    env = boot()
    mapping = json.loads(env.social_channels_json).get(slug, {})
    refresh = mapping.get("youtube_refresh_token", "") or json.loads(env.youtube_channel_tokens_json).get(slug, "")
    with session_scope() as s:
        saved = s.get(SocialConnection, slug)
        if saved:
            from .youtube_oauth import cipher
            refresh = cipher().decrypt(saved.refresh_token_encrypted.encode()).decode()
    return {"youtube_refresh_token": refresh,
            "instagram_access_token": mapping.get("instagram_access_token", ""),
            "instagram_account_id": mapping.get("instagram_account_id", "")}

def status(slug):
    from .meta_oauth import connection_status
    error = ''
    try:
        c = credentials(slug)
    except Exception:
        c = {}; error = 'Saved authorization cannot be read; reconnect this channel'
    with session_scope() as s:
        saved = s.get(SocialConnection, slug)
    meta = connection_status(slug)
    try:
        c.update(instagram_credentials(slug))
    except Exception:
        c['instagram_access_token'] = ''
        meta['meta_error'] = 'Saved Meta token cannot be read; reconnect Meta'
    configured = bool(boot().youtube_client_id and boot().youtube_client_secret)
    return {"channel": slug, "youtube": bool(c.get("youtube_refresh_token") and configured),
            "youtube_oauth_configured": configured,
            "youtube_channel_name": saved.remote_channel_name if saved else '',
            "youtube_channel_id": saved.remote_channel_id if saved else '', 'connection_error': error,
            "instagram": bool(c.get("instagram_access_token") and c.get("instagram_account_id")), **meta}

def youtube_token(slug):
    c = credentials(slug)
    if not status(slug)["youtube"]:
        raise RuntimeError("YouTube is not configured for this channel")
    r = httpx.post("https://oauth2.googleapis.com/token", data={"client_id": boot().youtube_client_id,
        "client_secret": boot().youtube_client_secret, "refresh_token": c["youtube_refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def review_required(video=None):
    options = (video.options_json or {}) if video else {}
    if options.get('force_review') or options.get('publishing_mode') == 'review':
        return True
    if options.get('publishing_mode') == 'direct':
        return False
    return bool(cfg('publishing', 'review_before_upload', default=True))


def route_upload(video_id, explicit=False):
    """One routing path for finished productions and existing-video submissions."""
    with session_scope() as s:
        v = s.get(Video, video_id, with_for_update=True)
        if not v or v.state not in ('READY', 'AWAITING_APPROVAL') or not v.output_path:
            raise ValueError('Finish rendering before sending a video to publishing')
        if not explicit and ((not cfg('schedule', 'auto_publish', default=False) and (v.options_json or {}).get('publishing_mode','settings') == 'settings')
                             or (v.options_json or {}).get('auto_publish') is False):
            return {'status': 'disabled'}
        if (review_required(v) or (v.options_json or {}).get('force_review')) and not v.approved:
            v.state = 'AWAITING_APPROVAL'
            v.progress = 99
            return {'status': 'awaiting_approval'}
        v.state = 'READY'
        slug = v.channel_slug
    # Instagram remains manual until its integration is configured/tested.
    if not explicit and not status(slug)['youtube']:
        return {'status': 'not_connected', 'message': 'Connect YouTube in Publishing'}
    return request_publish(video_id, 'youtube')


def approve_and_publish(video_id):
    result = request_publish(video_id, 'youtube', approve=True)
    return {'approved': True, 'state': 'READY', 'publication': result}


def request_publish(video_id, platform, approve=False):
    if platform not in ("youtube", "instagram"):
        raise ValueError("Unsupported platform")
    with session_scope() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"publish:{video_id}:{platform}"})
        v = s.get(Video, video_id, with_for_update=True)
        if not approve and v and (v.options_json or {}).get('preview_required') and (v.options_json or {}).get('reviewed_output') != v.output_path:
            raise ValueError('Play and confirm the latest soundtrack preview before approving upload')
        if approve and v and v.state in ('READY', 'AWAITING_APPROVAL') and v.output_path:
            v.approved = True
            v.options_json = {**(v.options_json or {}), 'preview_required': False}
            v.state = 'READY'
            v.progress = 100
        if not v or v.state != "READY" or not v.output_path:
            raise ValueError("Only completed, approved videos can be published")
        if review_required(v) and not v.approved:
            raise ValueError('Review this video and select Approve & upload first')
        if s.scalar(select(Job.id).where(Job.video_id == video_id,
                Job.status.in_(['queued', 'running']),
                Job.stage.notin_(['produce_video', 'publish']))):
            raise ValueError('Wait for the queued video edits to finish before approving an upload')
        if not status(v.channel_slug)[platform]:
            raise ValueError(f"Configure {platform} credentials for {v.channel_slug} in SOCIAL_CHANNELS_JSON")
        old = s.scalar(select(Publication).where(Publication.video_id == video_id, Publication.platform == platform))
        if old:
            if old.status == 'blocked' and v.approved:
                old.status = 'pending'
                old.error = ''
                j = Job(video_id=video_id, stage='publish', payload_json={'platform': platform}, max_attempts=1)
                s.add(j)
                s.flush()
                return {'id': old.id, 'job_id': j.id, 'status': old.status}
            return {"id": old.id, "status": old.status, "remote_id": old.remote_id}
        p = Publication(video_id=video_id, platform=platform,
                        requested_privacy=cfg('publishing', 'youtube_privacy', default='private'))
        s.add(p)
        j = Job(video_id=video_id, stage="publish", payload_json={"platform": platform}, max_attempts=1)
        s.add(j)
        s.flush()
        return {"id": p.id, "job_id": j.id, "status": p.status}

def _update(pub_id, **fields):
    with session_scope() as s:
        p = s.get(Publication, pub_id)
        for key, value in fields.items():
            setattr(p, key, value)

def publish(video_id, platform):
    with session_scope() as s:
        v = s.get(Video, video_id)
        p = s.scalar(select(Publication).where(Publication.video_id == video_id, Publication.platform == platform))
    if not p:
        raise ValueError('Publication was not queued')
    if p.status == "published":
        return {"remote_id": p.remote_id}
    if p.status in ("uploading", "uncertain"):
        raise RuntimeError("Previous publish may have succeeded; reconcile remote account before retry")
    if v.state != 'READY' or (review_required(v) and not v.approved):
        _update(p.id, status='blocked', error='Review and approve the completed video before upload')
        raise ValueError('Upload blocked by review policy')
    _update(p.id, status="uploading")
    try:
        remote = _youtube(v, p) if platform == "youtube" else _instagram(v, p)
        _update(p.id, status="published", remote_id=remote, error="")
        if platform == 'youtube':
            try:
                verify_youtube_publication(video_id)
            except Exception:
                _update(p.id, error='Upload accepted; visibility verification pending. Use Check YouTube.')
        return {"remote_id": remote, "platform": platform}
    except Exception as error:
        # A timeout after a publish request is ambiguous; never automatically create a duplicate.
        _update(p.id, status="uncertain", error=type(error).__name__ + ": provider operation failed; check remote account")
        raise RuntimeError(f"{platform} publication needs reconciliation; inspect Publications") from error

def _youtube(v, p):
    from .publishing_copy import publication_copy
    copy = publication_copy(v)
    headers = {"Authorization": "Bearer " + youtube_token(v.channel_slug)}
    if not storage.is_supabase_url(v.output_path):
        raise RuntimeError("Publishing requires persisted Supabase output")
    with tempfile.TemporaryDirectory(prefix="story-publish-") as folder:
        path = Path(folder) / "video.mp4"
        with httpx.stream("GET", v.output_path, timeout=180) as response:
            response.raise_for_status()
            with path.open("wb") as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
        size = path.stat().st_size
        with httpx.Client(timeout=240) as client:
            response = client.post("https://www.googleapis.com/upload/youtube/v3/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                headers={**headers, "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                json={"snippet": {"title": copy['youtube_title'], "description": copy['youtube_description'], "tags": copy['youtube_tags'], "categoryId": "27"},
                      "status": {"privacyStatus": p.requested_privacy, "selfDeclaredMadeForKids": v.made_for_kids}})
            response.raise_for_status()
            uri = response.headers["location"]
            _update(p.id, session_url=uri)
            with path.open("rb") as stream:
                response = client.put(uri, headers={**headers, "Content-Length": str(size), "Content-Type": "video/mp4"}, content=stream)
            response.raise_for_status()
            return response.json()["id"]

def verify_youtube_publication(video_id):
    with session_scope() as s:
        v = s.get(Video, video_id)
        p = s.scalar(select(Publication).where(Publication.video_id == video_id, Publication.platform == 'youtube'))
    if not p or not p.remote_id:
        raise ValueError('No uploaded YouTube video to verify yet')
    r = httpx.get('https://www.googleapis.com/youtube/v3/videos',
        params={'part': 'status,processingDetails', 'id': p.remote_id},
        headers={'Authorization': 'Bearer ' + youtube_token(v.channel_slug)}, timeout=30)
    r.raise_for_status()
    items = r.json().get('items', [])
    if not items:
        raise ValueError('Uploaded video is not visible to the connected account yet')
    remote = items[0]
    actual = remote.get('status', {}).get('privacyStatus', '')
    warning = '' if actual == p.requested_privacy else f'Requested {p.requested_privacy}; YouTube reports {actual}. Check API project audit restrictions in YouTube.'
    processing = remote.get('processingDetails', {}).get('processingStatus')
    upload_status = remote.get('status', {}).get('uploadStatus')
    if processing in ('failed', 'terminated') or upload_status in ('failed', 'rejected', 'deleted'):
        warning = f'YouTube did not finish publishing: processing={processing}, upload={upload_status}. Inspect the video in YouTube Studio.'
        _update(p.id, status='remote_failed')
    _update(p.id, actual_privacy=actual, error=warning)
    return {'remote_id': p.remote_id, 'privacyStatus': actual,
            'processing': processing, 'warning': warning}


def instagram_credentials(slug, validate=False):
    from .meta_oauth import credentials as meta_credentials
    return meta_credentials(slug, validate=validate)


def _instagram(v, p):
    from .publishing_copy import publication_copy
    c = instagram_credentials(v.channel_slug, validate=True)
    if not c.get('instagram_access_token') or not c.get('instagram_account_id'):
        raise ValueError('Connect Instagram for this channel first')
    if not storage.is_supabase_url(v.output_path):
        raise ValueError('Instagram requires a persisted Supabase video')
    base = f"https://graph.instagram.com/{boot().instagram_graph_version}"
    headers = {"Authorization": "Bearer " + c["instagram_access_token"]}
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{base}/{c['instagram_account_id']}/media", headers=headers,
            data={"media_type": "REELS", "video_url": v.output_path, "caption": publication_copy(v)['instagram_caption'], "share_to_feed":"true"})
        r.raise_for_status()
        container = r.json()["id"]
        _update(p.id, session_url=container)
        for _ in range(30):
            r = client.get(f"{base}/{container}", headers=headers, params={"fields": "status_code"})
            r.raise_for_status()
            state = r.json().get("status_code")
            if state == "FINISHED":
                break
            if state in ("ERROR", "EXPIRED"):
                raise RuntimeError("Instagram rejected media container")
            time.sleep(10)
        else:
            raise RuntimeError("Instagram processing timeout")
        r = client.post(f"{base}/{c['instagram_account_id']}/media_publish", headers=headers, data={"creation_id": container})
        r.raise_for_status()
        return r.json()["id"]

def analytics(slug, days=28):
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=max(1, min(90, days)) - 1)
    r = httpx.get("https://youtubeanalytics.googleapis.com/v2/reports", headers={"Authorization": "Bearer " + youtube_token(slug)},
        params={"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
                "metrics": "views,estimatedMinutesWatched,averageViewDuration,subscribersGained", "dimensions": "day", "sort": "day"}, timeout=30)
    r.raise_for_status()
    return r.json()
