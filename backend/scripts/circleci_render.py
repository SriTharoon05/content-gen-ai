"""Claim one durable media task. Downloads/uploads go directly to Supabase signed URLs."""
import hashlib
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Imports share the app's model declarations, but this worker NEVER connects to a database.
os.environ['DATABASE_URL'] = 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused'
os.environ['RENDER_EXECUTION_BACKEND'] = 'local'
os.environ.setdefault('RENDER_PROFILE', 'auto')


def main():
    task_id = os.environ.get('RENDER_TASK_ID', '')
    if len(task_id) != 32 or any(c not in '0123456789abcdef' for c in task_id):
        raise ValueError('A valid render_task_id is required')
    base = os.environ['RENDER_API_URL'].rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('RENDER_API_URL must use HTTPS')
    owner = secrets.token_urlsafe(32)
    headers = {'Authorization':'Bearer ' + os.environ['RENDER_WORKER_TOKEN']}

    def api(action, **payload):
        for attempt in range(5):
            try:
                response = httpx.post(f'{base}/internal/render-tasks/{task_id}/{action}',
                    headers=headers, json={'owner':owner, **payload}, timeout=90)
                if response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                    return response.json()
            except (httpx.TimeoutException, httpx.NetworkError):
                pass
            time.sleep(min(30, 2**attempt))
        raise RuntimeError(f'Render task {action} unavailable after retries')

    from app.db import engine
    from sqlalchemy import event
    @event.listens_for(engine, 'do_connect')
    def no_database(*args, **kwargs):
        raise RuntimeError('CircleCI media worker must never access the database')

    result = api('claim')
    if not result.get('claimed'):
        print('Task already claimed or completed; no rendering performed.')
        return 0
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(60):
            try:
                api('heartbeat')
                print('Media task still running; Render heartbeat sent.',flush=True)
            except Exception:
                print('Heartbeat unavailable; completion will retry independently.')
    thread = threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    try:
        manifest = result['manifest']
        with tempfile.TemporaryDirectory(prefix='story-render-') as folder:
            root = Path(folder)
            for name, entry in manifest['files'].items():
                path = (root / name).resolve()
                if not path.is_relative_to(root.resolve()) or '\\' in name:
                    raise ValueError('Unsafe manifest path')
                path.parent.mkdir(parents=True, exist_ok=True)
                sha = hashlib.sha256()
                with httpx.stream('GET',entry['url'],timeout=180) as response:
                    response.raise_for_status()
                    with path.open('wb') as out:
                        for chunk in response.iter_bytes():
                            sha.update(chunk); out.write(chunk)
                if sha.hexdigest() != entry['sha256']:
                    raise ValueError(f'Asset checksum mismatch: {name}')
            from app.render_bundle import execute
            from app.render_metrics import measure
            output = root / 'final.mp4'
            with measure() as metrics:
                media_info = execute(manifest,root,output)
            metrics.update(media_info)
            # Obtain a fresh two-hour upload capability only after rendering finishes.
            for attempt in range(3):
                url = api('upload-url')['url']
                try:
                    with output.open('rb') as source:
                        response = httpx.put(url,content=source,headers={'Content-Type':'video/mp4',
                            'Content-Length':str(output.stat().st_size),'x-upsert':'true'},timeout=300)
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
            api('complete',status='succeeded',metrics=metrics)
            print('Render succeeded:', metrics)
    except Exception as error:
        # No raw HTTP exceptions: signed URLs and credentials must not enter job logs.
        try:
            detail = str(error)[:1800] if isinstance(error,(RuntimeError,ValueError)) else type(error).__name__
            api('complete',status='failed',error=f'Media worker failed: {detail}',metrics={})
        except Exception:
            print('Completion unavailable; task deadline will reconcile the failure.')
        print(f'Media worker failed: {type(error).__name__}')
        return 1
    finally:
        stop.set()
        thread.join(timeout=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
