"""Explicit paid end-to-end acceptance run; creates exactly one video and no variants."""
import json
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import init_db, session_scope
from app.models import Video, Job
from app.worker import process
from app.settings_store import cfg
from app.storage import ensure_buckets

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    # httpx request logs can expose provider credentials in URLs (e.g. SERP).
    logging.getLogger('httpx').setLevel(logging.WARNING)
    init_db()
    ensure_buckets()
    if cfg('schedule','auto_publish',default=False):
        raise RuntimeError('Disable auto_publish before acceptance testing')
    with session_scope() as s:
        video = Video(channel_slug='lorehush', options_json={'languages': [], 'primary_language':'en',
            'min_shots':20,'max_shots':20,'target_seconds':50,'music_enabled':False})
        s.add(video); s.flush()
        job = Job(video_id=video.id, status='running', attempts=1, max_attempts=1)
        s.add(job); s.flush()
        video_id, job_id = video.id, job.id
    print(json.dumps({'video_id':video_id,'job_id':job_id}), flush=True)
    process(job_id)
    with session_scope() as s:
        v=s.get(Video,video_id); j=s.get(Job,job_id)
        print(json.dumps({'video_id':v.id,'state':v.state,'job':j.status,'duration':v.duration_seconds,
            'images':v.image_count,'output':v.output_path,'error':v.error}), flush=True)
        sys.exit(0 if j.status=='done' else 1)
