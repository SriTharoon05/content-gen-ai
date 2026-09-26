"""Register checksums of existing shared music without copying or regenerating it."""
import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
env=dotenv_values(Path(__file__).resolve().parents[2]/'backend'/'.env')
base=env['CF_MEDIA_API_URL'].rstrip('/')
headers={'Authorization':'Bearer '+env.get('CF_ADMIN_TOKEN',env.get('ADMIN_TOKEN',''))}
with httpx.Client(timeout=30) as client:
    response=client.get(base+'/api/music',headers=headers);response.raise_for_status()
    for track in response.json()['tracks']:
        if track.get('sha256'):
            print(track['id'],'already registered');continue
        path=track['path']; url=urlsplit(path)
        allowed=(url.scheme=='https' and not url.username and not url.password and
                 (url.netloc==urlsplit(env['SUPABASE_URL']).netloc or
                  url.netloc=='res.cloudinary.com' and url.path.startswith('/'+env['CLOUDINARY_CLOUD_NAME']+'/')))
        if not allowed:
            print(track['id'],'not a trusted remote URL; manual migration required');continue
        if not args.apply:
            print(track['id'],'needs checksum registration');continue
        digest=hashlib.sha256();size=0
        with client.stream('GET',path) as media:
            media.raise_for_status()
            for block in media.iter_bytes(262144):
                size+=len(block)
                if size>25*1024*1024:raise RuntimeError('Music exceeds migration size cap')
                digest.update(block)
        result=client.post(base+'/api/music/'+track['id']+'/checksum',headers=headers,json={'path':path,'sha256':digest.hexdigest()})
        result.raise_for_status();print(track['id'],'registered',size,'bytes')
