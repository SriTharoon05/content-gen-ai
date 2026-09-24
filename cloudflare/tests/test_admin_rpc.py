"""Explicit DB integration test. Every DDL/fixture write is rolled back, including on failure.

Run from backend: venv/Scripts/python.exe ../cloudflare/tests/test_admin_rpc.py
Never calls init_db, commits, generation, providers or publishing.
"""
import json
import sys
import uuid
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.db import engine


def main():
    slug='admin-test-'+uuid.uuid4().hex
    with engine.connect() as conn:
        tx=conn.begin()
        try:
            conn.execute(text("SET LOCAL lock_timeout='5s'"))
            conn.execute(text("SET LOCAL statement_timeout='30s'"))
            with conn.connection.driver_connection.cursor() as cur:
                cur.execute((Path(__file__).resolve().parents[1]/'sql/007_admin_rpc.sql').read_text())
            def call(action, identity='', payload=None):
                return conn.execute(text('SELECT public.cf_admin(:a,:i,CAST(:p AS jsonb))'),
                    {'a':action,'i':identity,'p':json.dumps(payload or {})}).scalar_one()
            for role in ('anon','authenticated'):
                assert not conn.execute(text("SELECT has_function_privilege(:r,'public.cf_admin(text,text,jsonb)','EXECUTE')"),{'r':role}).scalar()
            assert conn.execute(text("SELECT has_function_privilege('service_role','public.cf_admin(text,text,jsonb)','EXECUTE')")).scalar()
            created=call('channel_create',slug,{'name':'Fixture','niche':'Fixture science','instructions':'Original instructions'})
            assert created['channel']['slug']==slug
            assert call('channel_create',slug,{'name':'Fixture','niche':'Fixture'})['status']==409
            edited=call('channel_update',slug,{'instructions':'New instructions','enabled':False,'overrides':{'primary_language':'ta'}})
            assert edited['channel']['strategy']['instructions']=='New instructions'
            assert edited['channel']['enabled'] is False
            assert edited['channel']['overrides']['primary_language']=='ta'
            conn.execute(text("UPDATE channels SET overrides_json=CAST(:p AS json),strategy_json=CAST(:s AS json) WHERE slug=:slug"),
                {'slug':slug,'p':json.dumps({'primary_language':'en','api_token':'fixture-secret','voice':{'token':'fixture-secret'},'languages':[{'token':'fixture-secret'}]}),'s':json.dumps({'instructions':'Allowed','keys':'fixture-secret','audience':{'secret':'fixture-secret'}})})
            assert 'fixture-secret' not in json.dumps(call('channel_get',slug))
            assert any(c['slug']==slug for c in call('channels')['channels'])
            # Replace runtime inside this uncommitted transaction, never echo real credentials.
            fixture={'keys':{'groq':['fixture-secret-a','fixture-secret-b'],'gemini_audio_paid':'fixture-secret-paid'},'voice':{'speech_tempo':1},'schedule':{'enabled':False}}
            conn.execute(text("INSERT INTO app_settings(key,value_json,updated_at) VALUES('runtime',CAST(:v AS json),clock_timestamp()) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at"),{'v':json.dumps(fixture)})
            before=call('settings_get');assert 'fixture-secret' not in json.dumps(before)
            def save(patch,revision=None):
                return call('settings_save',payload={'patch':patch,'revision':revision if revision is not None else call('settings_get')['revision']})
            assert save({'voice':{'speech_tempo':1.1}}).get('settings')
            assert save({'voice':{'speech_tempo':1.2}},before['revision'])['status']==409
            assert save({'keys':{'groq':['__keep__:1','fixture-secret-new']}}).get('settings')
            stored=conn.execute(text("SELECT value_json FROM app_settings WHERE key='runtime'")).scalar_one()
            assert stored['keys']['groq']==['fixture-secret-b','fixture-secret-new']
            assert stored['keys']['gemini_audio_paid']=='fixture-secret-paid'
            assert save({'keys':{'groq':['__keep__:49']}})['status']==409
            assert save({'keys':{'groq':['__keep__:not-an-index']}})['status']==422
            assert save({'keys':{'admin_token':'fixture-secret'}})['status']==422
            assert save({'voice':{'speech_tempo':1.2}},'stale')['status']==409
            assert save({'schedule':{'enabled':True}})['status']==422
            assert 'fixture-secret' not in json.dumps(call('settings_get'))
            conn.execute(text("INSERT INTO channel_memory(id,channel_slug,kind,content,pinned,source,created_at) VALUES(:i,:s,'note','fixture',false,'operator',now())"),{'i':uuid.uuid4().hex,'s':slug})
            assert call('channel_delete',slug)['status']==409
            conn.execute(text('DELETE FROM channel_memory WHERE channel_slug=:s'),{'s':slug})
            assert call('channel_delete',slug)['deleted']
            assert call('channel_get',slug)['status']==404
            print('PASS: SQL compile, ACL, channel CRUD/redaction, secret preservation, conflict checks, protected deletion')
        finally:
            tx.rollback()
    print('ROLLBACK complete: no persistent changes')


if __name__=='__main__':
    main()
