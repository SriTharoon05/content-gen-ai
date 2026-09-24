"""Opt-in transactional integration test: applies the edit RPC then rolls everything back.

Run from backend: venv\\Scripts\\python.exe ..\\cloudflare\\tests\\test_edit_rpc.py
No media, provider, CI, publication, or persistent database writes.
"""
import hashlib
import json
from pathlib import Path
import sys
import uuid
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.db import engine


def main():
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            with conn.connection.driver_connection.cursor() as cursor:
                cursor.execute((Path(__file__).parents[1] / 'sql' / '006_edit_rpc.sql').read_text())
            def call(action, identity='', payload=None):
                return conn.execute(text('SELECT public.cf_edit(:a,:id,CAST(:p AS jsonb))'),
                                    {'a': action, 'id': identity, 'p': json.dumps(payload or {})}).scalar_one()
            for role in ('anon','authenticated'):
                assert not conn.execute(text("SELECT has_function_privilege(:r,'public.cf_edit(text,text,jsonb)','EXECUTE')"),{'r':role}).scalar_one()
            video=uuid.uuid4().hex
            conn.execute(text("""INSERT INTO public.videos SELECT (jsonb_populate_record(NULL::public.videos,
                to_jsonb(v)||jsonb_build_object('id',CAST(:id AS text),'state','AWAITING_APPROVAL','output_path','https://res.cloudinary.com/test/video/upload/old.mp4',
                'options_json','{}'::jsonb,'approved',false))).* FROM public.videos v LIMIT 1"""), {'id':video})
            old=uuid.uuid4().hex
            manifest={'version':1,'operation':'assemble_script','script':{'beats':[]},'words':[],
                      'files':{'narration.wav':{'url':'https://res.cloudinary.com/test/video/upload/old.wav','sha256':'a'*64}},'images':['images/1.png'],'settings':{}}
            def media(identity, m):
                result=conn.execute(text("SELECT public.cf_media_task('create',:id,CAST(:p AS jsonb))"),
                    {'id':identity,'p':json.dumps({'video_id':video,'manifest':m})}).scalar_one()
                assert result['id']==identity
                conn.execute(text("UPDATE public.render_tasks SET status='succeeded',result_json=CAST(:p AS jsonb) WHERE id=:id"),
                    {'id':identity,'p':json.dumps({'url':'https://res.cloudinary.com/test/video/upload/old.mp4' if identity==old else 'https://res.cloudinary.com/test/video/upload/new.mp4','duration':60,'metrics':{'sha256':'b'*64}})})
            media(old,manifest)
            edit=uuid.uuid4().hex
            payload={'video_id':video,'input':{'operation':'rerender','revision':0}}
            assert call('create',edit,payload)['revision']==1
            assert call('create',edit,payload)['id']==edit
            assert call('create',edit,{**payload,'input':{'operation':'bgm','revision':0}})['status']==409
            assert call('create',uuid.uuid4().hex,payload)['status']==409
            assert call('claim',edit)['snapshot']['manifest']==manifest
            assert call('complete',edit,{'task_id':old})['status']==409
            final=hashlib.sha256((edit+':render').encode()).hexdigest()[:32]
            media(final,manifest)
            assert call('complete',edit,{'task_id':final})['revision']==1
            assert call('complete',edit,{'task_id':final})['revision']==1
            assert call('public',edit)['status']=='succeeded'
            assert call('create',uuid.uuid4().hex,payload)['status']==409
            failed=uuid.uuid4().hex
            assert call('create',failed,{'video_id':video,'input':{'operation':'rerender','revision':1}})['revision']==2
            assert call('fail',failed,{'error':'fixture'})['ok']
            assert call('complete',failed,{'task_id':final})['status']==409
            # Older Render remixes retained only source MP4 + narration, not scene assets.
            remix=uuid.uuid4().hex
            media(remix,{'version':1,'operation':'remix','files':{'narration.wav':manifest['files']['narration.wav']},'music':None})
            conn.execute(text("UPDATE public.render_tasks SET updated_at=clock_timestamp() WHERE id=:id"),{'id':remix})
            rerender=uuid.uuid4().hex
            assert call('create',rerender,{'video_id':video,'input':{'operation':'rerender','revision':2}})['revision']==3
            restored=call('claim',rerender)['snapshot']['manifest']
            assert restored['images']==manifest['images'] and restored['script']==manifest['script']
            assert call('fail',rerender,{'error':'fixture'})['ok']
            conn.execute(text("""INSERT INTO public.publications(id,video_id,platform,status,remote_id,session_url,error,requested_privacy,actual_privacy,updated_at)
                VALUES(:id,:v,'instagram','committing','','','','public','',now())"""),{'id':uuid.uuid4().hex,'v':video})
            assert call('create',uuid.uuid4().hex,{'video_id':video,'input':{'operation':'rerender','revision':3}})['status']==409
            music=uuid.uuid4().hex
            assert call('music-save',music,{'name':'Fixture','category':'drama','path':'https://res.cloudinary.com/test/video/upload/music.wav',
                  'sha256':'c'*64,'duration_seconds':20,'trim_start':2,'trim_end':10,'default_volume_pct':30})['id']==music
            assert any(t['id']==music for t in call('music-list')['tracks'])
            assert call('music-delete',music)['media_preserved']
            assert not any(t['id']==music for t in call('music-list')['tracks'])
            print('PASS: edit ACL, snapshot, idempotency, active/stale revision guards, exact-task completion, failure and shared music archive (rolled back)')
        finally:
            transaction.rollback()


if __name__=='__main__':
    main()
