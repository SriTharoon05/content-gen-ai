"""Recompose selected unapproved news posts through CircleCI, reusing all assets."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import httpx
from dotenv import dotenv_values
from sqlalchemy import text
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.db import engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ids', nargs='+')
    parser.add_argument('--version', default='adaptive-spacing-v1')
    args = parser.parse_args()
    env = dotenv_values(Path(__file__).resolve().parents[2] / 'backend' / '.env')
    base = env['CF_MEDIA_API_URL'].rstrip('/')
    plans = {}
    with engine.connect() as connection:
        for ident in args.ids:
            post = connection.execute(text('SELECT status,checkpoint FROM cf_news_posts WHERE id=:id'), {'id': ident}).mappings().one()
            if post['status'] != 'awaiting_approval':
                raise ValueError('Only awaiting-approval posts may be recomposed')
            plans[ident] = []
            for old in post['checkpoint']['task_ids']:
                manifest = connection.execute(text('SELECT manifest_json FROM render_tasks WHERE id=:id'), {'id': old}).scalar_one()
                if manifest['operation'] != 'news_slide':
                    raise ValueError('Only news slide composition is permitted')
                task = hashlib.sha256((old + ':' + args.version).encode()).hexdigest()[:32]
                plans[ident].append((task, manifest))
    with httpx.Client(headers={'Authorization': 'Bearer ' + env.get('CF_ADMIN_TOKEN', env.get('ADMIN_TOKEN', ''))}, timeout=30) as client:
        for ident, tasks in plans.items():
            for task, manifest in tasks:
                r = client.post(base + '/migration/media-tasks', headers={'Idempotency-Key': task}, json={'video_id': ident, 'manifest': manifest})
                if r.status_code != 202:
                    raise RuntimeError('Reflow submission HTTP ' + str(r.status_code))
                print('QUEUED', ident, task, flush=True)
        deadline = time.monotonic() + 1200
        while plans and time.monotonic() < deadline:
            for ident, tasks in list(plans.items()):
                rows = []
                for task, _ in tasks:
                    r = client.get(base + '/migration/media-tasks/' + task)
                    r.raise_for_status()
                    rows.append(r.json())
                if any(r['status'] == 'failed' for r in rows):
                    raise RuntimeError('Reflow failed; existing previews were retained')
                if not all(r['status'] == 'succeeded' for r in rows):
                    continue
                slides = [{'url': r['result']['url'], 'width': 1080, 'height': 1350} for r in rows]
                for slide in slides:
                    httpx.head(slide['url'], timeout=30).raise_for_status()
                with engine.begin() as connection:
                    result = connection.execute(text("SELECT cf_news('save',:id,CAST(:payload AS jsonb))"), {'id': ident, 'payload': json.dumps({'slides': slides, 'task_ids': [t for t, _ in tasks]})}).scalar_one()
                    if isinstance(result.get('status'), int):
                        raise RuntimeError('Post changed during reflow; preview swap refused')
                print('UPDATED', ident, json.dumps(slides), flush=True)
                del plans[ident]
            if plans:
                time.sleep(10)
        if plans:
            raise RuntimeError('Reflow timeout; existing previews retained, task IDs may be monitored')


if __name__ == '__main__':
    main()
