"""Install/test the additive pilot RPC using backend/.env. Default is rollback-only."""
import argparse
import json
from pathlib import Path
import sys
import uuid
from sqlalchemy import text

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.db import engine


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    sql=(Path(__file__).resolve().parents[1]/'sql'/'001_media_rpc.sql').read_text()
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            with connection.connection.driver_connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute((Path(__file__).resolve().parents[1]/'sql'/'002_generation_rpc.sql').read_text())
            def call(action,task='',payload=None):
                return connection.execute(text('SELECT public.cf_media_task(:a,:id,CAST(:p AS jsonb))'),
                    {'a':action,'id':task,'p':json.dumps(payload or {})}).scalar_one()
            assert call('check')['ok']
            for role in ('anon','authenticated'):
                assert not connection.execute(text("SELECT has_function_privilege(:r,'public.cf_media_task(text,text,jsonb)','EXECUTE')"),{'r':role}).scalar()
            assert connection.execute(text("SELECT has_function_privilege('service_role','public.cf_media_task(text,text,jsonb)','EXECUTE')")).scalar()
            print('RPC health and access restrictions passed')
            # A SAVEPOINT guarantees all fixture rows are removed even when installing.
            savepoint=connection.begin_nested()
            try:
                video=connection.execute(text('SELECT id FROM public.videos LIMIT 1')).scalar_one()
                task=uuid.uuid4().hex
                payload={'video_id':video,'manifest':{'version':1,'operation':'prepare_audio'}}
                assert call('create',task,payload)['id']==task
                assert call('create',task,payload)['id']==task
                assert call('create',task,{**payload,'manifest':{'version':2}})['status']==409
                assert call('claim',task,{'owner_hash':'a'*64}).get('manifest')
                assert call('claim',task,{'owner_hash':'b'*64})=={}
                assert call('claim',task,{'owner_hash':'a'*64}).get('manifest')
                result={'owner_hash':'a'*64,'status':'succeeded','result':{'test':True}}
                assert call('finish',task,result)['ok']
                assert call('finish',task,result)['ok']
                assert call('load',task)['status']=='succeeded'
                assert call('finish',task,{**result,'status':'failed'})['status']==409
                print('Atomic create, replay, owner isolation, completion and conflict checks passed')
                def gen(action,identity,payload=None):
                    return connection.execute(text('SELECT public.cf_generation(:a,:id,CAST(:p AS jsonb))'),
                        {'a':action,'id':identity,'p':json.dumps(payload or {})}).scalar_one()
                fresh=uuid.uuid4().hex
                assert gen('create',fresh,{'channel':'lorehush'})['id']==fresh
                assert gen('create',fresh,{'channel':'lorehush'})['id']==fresh
                assert gen('create',fresh,{'channel':'curionerve'})['status']==409
                assert gen('status',fresh)['state']=='CF_GENERATING'
                assert gen('config',fresh)['channel']['slug']=='lorehush'
                other=uuid.uuid4().hex
                assert gen('create',other,{'channel':'lorehush'})['id']==other
                rate_model='rate-fixture-'+fresh
                assert gen('image_permit',fresh,{'model':rate_model})['wait_ms']==0
                assert gen('image_permit',other,{'model':rate_model})['wait_ms']>0
                assert gen('image_permit',other,{'model':rate_model+'-independent'})['wait_ms']==0
                for role in ('anon','authenticated'):
                    assert not connection.execute(text("SELECT has_table_privilege(:r,'public.cf_image_rate','SELECT')"),{'r':role}).scalar()
                assert gen('save',fresh,{'key':'fixture','value':{'ok':True}})['ok']
                assert gen('status',fresh)['steps']['fixture']['ok']
                concept={'core_entity':'cf-fixture-'+fresh,'content_angle':'test-angle','core_concept':'Test fixture is rolled back, never used for generation.', 'embedding':json.dumps([1.0]+[0.0]*767)}
                reserved=gen('reserve',fresh,concept)
                assert reserved['video_id']==fresh
                assert gen('reserve',fresh,concept)['id']==reserved['id']
                assert gen('call_start',fresh,{'key':'image','provider':'pollinations','model':'test','credits':.001})['new']
                assert gen('call_start',fresh,{'key':'image','provider':'pollinations','model':'test','credits':.001})['status']=='reserved'
                assert gen('call_finish',fresh,{'key':'image','value':{'test':True}})['ok']
                assert gen('call_start',fresh,{'key':'image','provider':'pollinations','model':'test','credits':.001})['detail_json']['test']
                assert gen('complete',fresh,{'url':'invalid'})['status']==409
                finaltask=uuid.uuid4().hex
                call('create',finaltask,{'video_id':fresh,'manifest':{'version':1,'operation':'assemble_script'}})
                call('claim',finaltask,{'owner_hash':'c'*64})
                call('finish',finaltask,{'owner_hash':'c'*64,'status':'succeeded','result':{'duration':60}})
                for key,value in {'render-task':finaltask,'audio-ready':{'url':'https://res.cloudinary.com/test/video/upload/audio.wav','duration':60,'sha256':'a'*64},
                    'script':{},'premise':{},'qa':{'passed':True},'voice':{},'visuals':{'shots':[{'shot_id':'s001'}]},
                    'image-s001':{'url':'https://res.cloudinary.com/test/image/upload/image.png','sha256':'b'*64},
                    'copy':{'youtube_title':'Fixture','youtube_description':'Fixture description','instagram_caption':'Fixture caption'}}.items():
                    gen('save',fresh,{'key':key,'value':value})
                assert gen('complete',fresh,{'url':'https://res.cloudinary.com/test/video/upload/final.mp4','duration':60})['ok']
                assert gen('complete',fresh,{})['ok']
                assert gen('status',fresh)['state']=='AWAITING_APPROVAL'
                approved=connection.execute(text('SELECT approved FROM public.videos WHERE id=:id'),{'id':fresh}).scalar_one()
                assert not approved
                assert connection.execute(text('SELECT count(*) FROM public.assets WHERE video_id=:id'),{'id':fresh}).scalar_one()==2
                for role in ('anon','authenticated'):
                    assert not connection.execute(text("SELECT has_function_privilege(:r,'public.cf_generation(text,text,jsonb)','EXECUTE')"),{'r':role}).scalar()
                print('Generation create/replay, checkpoints, pgvector, purchase ledger, review-only completion/assets and access checks passed')
            finally:
                savepoint.rollback()
            if args.apply:
                transaction.commit();print('Pilot RPC installed. No fixture rows or video changes persisted.')
            else:
                transaction.rollback();print('Validation only: all SQL changes rolled back.')
        except:
            transaction.rollback()
            raise


if __name__=='__main__': main()
