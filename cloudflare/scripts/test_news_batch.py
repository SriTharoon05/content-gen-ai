"""Explicit live generation-to-preview test. Never approves or publishes posts."""
import argparse
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
import httpx
from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', help='Required: consumes provider credits')
    parser.add_argument('--count', type=int, default=4, choices=range(4, 9))
    parser.add_argument('--channel', default='lorehush')
    parser.add_argument('--resume', nargs='*', default=[], help='Existing request IDs counted toward this batch')
    args = parser.parse_args()
    if not args.run:
        parser.error('Pass --run only with authorization to generate paid images')
    env = dotenv_values(Path(__file__).resolve().parents[2] / 'backend' / '.env')
    base = env['CF_MEDIA_API_URL'].rstrip('/')
    headers = {'Authorization': 'Bearer ' + env.get('CF_ADMIN_TOKEN', env.get('ADMIN_TOKEN', ''))}
    if len(args.resume)>args.count or any(len(i)!=32 or any(c not in '0123456789abcdef' for c in i) for i in args.resume):
        parser.error('Invalid resume IDs')
    pending, previous, submitted = dict.fromkeys(args.resume, False), {}, {}
    deadline = time.monotonic() + 1800
    with httpx.Client(timeout=30, headers=headers) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(base + '/api/news')
                response.raise_for_status()
                rows = {p['id']: p for p in response.json()['posts']}
            except httpx.HTTPError:
                print('Status check unavailable; retrying without generating extra posts', flush=True)
                time.sleep(10)
                continue
            # Let the previous request reserve its story before selecting the next.
            last = next(reversed(pending), None) if pending else None
            if len(pending) < args.count and (last is None or rows.get(last, {}).get('status') not in (None, 'queued', 'planning')):
                ident = uuid.uuid4().hex
                pending[ident] = False
                submitted[ident] = datetime.now(timezone.utc)
                try:
                    result = client.post(base + '/api/news/run', json={'channel': args.channel}, headers={'Idempotency-Key': ident})
                    result.raise_for_status()
                except httpx.HTTPError:
                    print(f'{ident} submission uncertain; stop creating new requests and inspect this ID', flush=True)
                    return
                print(f'SUBMITTED {ident} {submitted[ident].isoformat()}', flush=True)
            for ident in pending:
                row = rows.get(ident, {})
                state = row.get('status', 'queued')
                if state != previous.get(ident):
                    print(f'{ident} {state} {row.get("title", "") or ""} {row.get("error", "") or ""}', flush=True)
                    previous[ident] = state
                if state == 'awaiting_approval' and not pending[ident]:
                    for slide in row.get('slides', []):
                        # Media hosts must never receive the dashboard admin token.
                        media = httpx.head(slide['url'], timeout=30)
                        print(f'MEDIA {ident} HTTP {media.status_code} {slide["url"]}', flush=True)
                    elapsed = (datetime.fromisoformat(row['updated_at']) - submitted[ident]).total_seconds() if ident in submitted else None
                    print(f'COMPLETE {ident} seconds={elapsed} slides={len(row.get("slides", []))}', flush=True)
                    pending[ident] = True
                elif state in ('failed', 'uncertain'):
                    pending[ident] = True
            if len(pending) == args.count and all(pending.values()):
                print('BATCH TERMINAL: ' + str(previous), flush=True)
                return
            time.sleep(10)
        print('Monitoring deadline reached; existing runs left untouched', flush=True)


if __name__ == '__main__':
    main()
