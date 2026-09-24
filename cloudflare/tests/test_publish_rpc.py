"""Rollback-only publishing/OAuth SQL integration; never uploads or persists changes."""
import json
from pathlib import Path
import sys
import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.db import engine
from app.models import Video, SocialConnection

def main():
    with engine.connect() as c:
        tx=c.begin()
        try:
            with c.connection.driver_connection.cursor() as cursor:
                cursor.execute((Path(__file__).resolve().parents[1]/'sql/005_publish_rpc.sql').read_text())
            def call(action,identity='',payload=None):
                return c.execute(text('SELECT cf_publish(:a,:i,CAST(:p AS jsonb))'),{'a':action,'i':identity,'p':json.dumps(payload or {})}).scalar_one()
            for role in ('anon','authenticated'):
                assert not c.execute(text("SELECT has_function_privilege(:r,'cf_publish(text,text,jsonb)','EXECUTE')"),{'r':role}).scalar()
            slug=c.execute(text('SELECT slug FROM channels LIMIT 1')).scalar_one()
            session=Session(bind=c)
            video=Video(id=uuid.uuid4().hex,channel_slug=slug,state='AWAITING_APPROVAL',output_path='https://example.invalid/fixture.mp4',options_json={'force_review':True})
            session.add(video)
            if not session.get(SocialConnection,slug):session.add(SocialConnection(channel_slug=slug,refresh_token_encrypted='fixture',remote_channel_id='fixture',remote_channel_name='fixture'))
            session.flush()
            assert call('request',video.id,{'platform':'youtube'})['status']==409
            assert call('approve',video.id,{'output':'old'})['status']==409
            assert call('approve',video.id,{'output':video.output_path})['approved']
            p=call('request',video.id,{'platform':'youtube'})
            assert p['status']=='pending' and 'session_url' not in p
            assert call('request',video.id,{'platform':'youtube'})['id']==p['id']
            assert call('claim',p['id'])['fresh']
            assert not call('claim',p['id'])['fresh']
            call('update',p['id'],{'status':'uncertain','session_url':'secret-session'})
            assert call('request',video.id,{'platform':'youtube'})['status']=='uncertain'
            assert 'session_url' not in call('status',video.id)['publications'][0]
            c.execute(text("UPDATE videos SET options_json=CAST(:p AS jsonb) WHERE id=:i"),{'i':video.id,'p':json.dumps({'execution_backend':'cloudflare-generation','cf_publish_ready':True})})
            assert len(call('pending_dispatch')['videos'])<=1
            call('dispatched',video.id,{'clear':False,'error':'fixture-blocked'})
            assert call('policy',video.id)['options']['cf_publish_ready']
            call('dispatched',video.id,{'clear':True})
            assert not call('policy',video.id)['options']['cf_publish_ready']
            ticket=uuid.uuid4().hex;state=uuid.uuid4().hex
            call('oauth_ticket',ticket,{'channel':slug,'phase':'meta_ticket'})
            call('oauth_start',ticket,{'phase':'meta_ticket','state':state,'verifier':'nonce-hash','next_phase':'ig_consent'})
            assert call('oauth_consume',state,{'phase':'ig_consent','nonce':'wrong'})['status']==400
            assert call('oauth_consume',state,{'phase':'ig_consent','nonce':'nonce-hash'})['channel']==slug
            assert call('oauth_consume',state,{'phase':'ig_consent','nonce':'nonce-hash'})['status']==400
            assert 'refresh_token_encrypted' not in json.dumps(call('connections'))
            print('Publishing approval, stale preview, idempotency, uncertain suppression, OAuth nonce/replay and ACL tests passed (rolled back)')
        finally:tx.rollback()
if __name__=='__main__':main()
