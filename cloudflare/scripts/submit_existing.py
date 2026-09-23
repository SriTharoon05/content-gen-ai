"""Submit a COPY of a completed render manifest to the isolated pilot.

Read-only access to existing Supabase records; the pilot creates its own task/job.
Run from backend/ so its existing .env supplies DATABASE_URL. Never publishes.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-task',required=True)
    parser.add_argument('--idempotency-key',default=None)
    args=parser.parse_args()
    from app.db import session_scope
    from app.models import RenderTask
    with session_scope() as session:
        source=session.get(RenderTask,args.source_task)
        if not source or source.status!='succeeded':
            raise ValueError('Choose a completed canonical render task')
        payload={'video_id':source.video_id,'manifest':source.manifest_json}
    base=os.environ['CF_MEDIA_API_URL'].rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('Worker API must use HTTPS')
    task=args.idempotency_key or uuid.uuid4().hex
    # Print the retry key before network I/O so a lost response cannot cause duplicate work.
    print('Idempotency key:',task,flush=True)
    response=httpx.post(base+'/migration/media-tasks',
        headers={'Authorization':'Bearer '+os.environ['CF_ADMIN_TOKEN'],'Idempotency-Key':task},
        json=payload,timeout=60)
    if response.status_code!=202:
        raise RuntimeError(f'Pilot submission HTTP {response.status_code}; retry with the SAME idempotency key')
    print(json.dumps(response.json()))


if __name__=='__main__':
    main()
