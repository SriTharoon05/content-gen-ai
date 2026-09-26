"""Read-only deployed API checks. Never prints credentials or raw settings."""
from pathlib import Path
import argparse
from dotenv import dotenv_values
import httpx

root = Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--analytics',action='store_true')
args=parser.parse_args()
env = dotenv_values(root / 'backend' / '.env')
base = env.get('CF_MEDIA_API_URL', 'https://story-shorts-cloudflare-pilot.storyshort.workers.dev').rstrip('/')
headers = {'Authorization': 'Bearer ' + env.get('CF_ADMIN_TOKEN', env.get('ADMIN_TOKEN', ''))}
paths=['/health', '/api/health', '/api/channels', '/api/videos', '/api/schedule', '/api/connections', '/api/music']
if args.analytics:
    paths += ['/api/channels/lorehush/analytics/youtube','/api/channels/lorehush/analytics/instagram']
with httpx.Client(timeout=25) as client:
    for path in paths:
        try:
            response = client.get(base + path, headers=headers)
            value = response.json()
            safe = {k: value[k] for k in ['ok', 'mode', 'capabilities', 'owner', 'detail'] if k in value}
            for key in ['channels', 'videos', 'connections', 'tracks']:
                if key in value:
                    safe[key] = len(value[key])
            if path == '/api/videos':
                safe['recent'] = [{k: v.get(k) for k in ['id', 'state', 'stage_detail', 'generation_timing']} for v in value.get('videos', [])[:2]]
            print(path, response.status_code, safe)
        except Exception as error:
            print(path, type(error).__name__)
