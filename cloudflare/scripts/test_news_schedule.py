"""One real cron admission; restore original schedule immediately after dispatch."""
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import httpx
from dotenv import dotenv_values
from sqlalchemy import text
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.db import engine

env=dotenv_values(Path(__file__).resolve().parents[2]/'backend'/'.env')
base=env['CF_MEDIA_API_URL'].rstrip('/')
with httpx.Client(headers={'Authorization':'Bearer '+env.get('CF_ADMIN_TOKEN',env.get('ADMIN_TOKEN',''))},timeout=30) as client:
    initial=client.get(base+'/api/news').json()
    original=initial['config']; known={p['id'] for p in initial['posts']}
    if original['enabled']: raise RuntimeError('Refusing to replace an enabled production schedule')
    with engine.connect() as connection:
        remaining=connection.execute(text("select cf_news('tick')")).scalar_one()['remaining']
    consumed=original['posts_per_day']-remaining
    if consumed>=19: raise RuntimeError('Insufficient daily request capacity for a one-job test')
    local=datetime.now(timezone.utc)+timedelta(minutes=original['timezone_offset_minutes'])
    target=local+timedelta(minutes=2)
    if target.date()!=local.date(): raise RuntimeError('Run test away from local midnight')
    cfg={**original,'enabled':True,'posts_per_day':consumed+1,'run_at':target.strftime('%H:%M')}
    started=datetime.now(timezone.utc); ident=None
    try:
        r=client.post(base+'/api/news/config',json=cfg);r.raise_for_status()
        print('SCHEDULE ARMED',cfg['run_at'],'offset',cfg['timezone_offset_minutes'],'daily total',cfg['posts_per_day'],flush=True)
        until=time.monotonic()+360
        while time.monotonic()<until:
            d=client.get(base+'/api/news');d.raise_for_status()
            fresh=[p for p in d.json()['posts'] if p['id'] not in known]
            if fresh:
                ident=fresh[0]['id'];print('CRON DISPATCH',ident,flush=True);break
            time.sleep(5)
    finally:
        r=client.post(base+'/api/news/config',json=original);r.raise_for_status()
        print('ORIGINAL SCHEDULE RESTORED',flush=True)
    if not ident: raise RuntimeError('No real cron admission observed')
    prior=None
    until=time.monotonic()+1200
    while time.monotonic()<until:
        r=client.get(base+'/api/news');r.raise_for_status()
        p=next(p for p in r.json()['posts'] if p['id']==ident)
        if p['status']!=prior:
            print(p['status'],p.get('title'),p.get('error'),flush=True);prior=p['status']
        if prior=='failed': raise RuntimeError('Scheduled generation failed; no restart attempted')
        if prior=='awaiting_approval':
            for slide in p['slides']:
                media=httpx.head(slide['url'],timeout=30);media.raise_for_status()
                print('PREVIEW',slide['url'],flush=True)
            print('SUCCESS',ident,'slides',len(p['slides']),flush=True);break
        time.sleep(10)
