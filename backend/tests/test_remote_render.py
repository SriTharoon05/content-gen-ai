"""Offline CircleCI contracts. No production credentials or project needed."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import circleci, remote_render, render_api, render_profile
from app.settings_store import render_settings, cfg


class RemoteRenderTests(unittest.TestCase):
    def db(self, task):
        db = MagicMock()
        db.__enter__.return_value.get.return_value = task
        return db

    def task(self):
        return SimpleNamespace(status='queued', owner_hash='', manifest_json={'files':{}},
            deadline=datetime.now(timezone.utc)+timedelta(hours=4))

    def test_trigger_only_contains_task_id(self):
        config = SimpleNamespace(circleci_token='secret',circleci_project_slug='circleci/org/project',
            circleci_pipeline_definition_id='definition',circleci_branch='main')
        response = MagicMock(status_code=201)
        response.json.return_value={'id':'pipeline'}
        with patch.object(circleci,'boot',return_value=config), patch.object(circleci.httpx,'post',return_value=response) as post:
            self.assertEqual(circleci.trigger('a'*32),'pipeline')
        kwargs=post.call_args.kwargs
        self.assertEqual(kwargs['json']['parameters'],{'render_task_id':'a'*32})
        self.assertNotIn('secret',str(kwargs['json']))
        self.assertEqual(kwargs['json']['checkout'],{'branch':'main'})

    def test_duplicate_claim_does_not_render_twice(self):
        task=self.task()
        with patch.object(render_api,'session_scope',return_value=self.db(task)):
            self.assertTrue(render_api.claim('task',render_api.Owner(owner='a'*32))['claimed'])
            self.assertFalse(render_api.claim('task',render_api.Owner(owner='b'*32))['claimed'])
            # Lost HTTP response may retry with the SAME unguessable owner.
            self.assertTrue(render_api.claim('task',render_api.Owner(owner='a'*32))['claimed'])
            task.status='succeeded'
            self.assertFalse(render_api.claim('task',render_api.Owner(owner='a'*32))['claimed'])

    def test_completed_task_cannot_be_changed_by_other_owner(self):
        from fastapi import HTTPException
        task=self.task(); task.status='succeeded'; task.owner_hash=render_api.owner_hash('a'*32)
        with patch.object(render_api,'session_scope',return_value=self.db(task)):
            with self.assertRaises(HTTPException):
                render_api.complete('task',render_api.Completion(owner='b'*32,status='failed'))
            self.assertTrue(render_api.complete('task',render_api.Completion(owner='a'*32,status='succeeded'))['ok'])

    def test_failed_or_expired_claim_is_not_reclaimed(self):
        task=self.task(); task.status='failed'
        with patch.object(render_api,'session_scope',return_value=self.db(task)):
            self.assertFalse(render_api.claim('task',render_api.Owner(owner='b'*32))['claimed'])
            task.status='queued'; task.deadline=datetime.now(timezone.utc)-timedelta(seconds=1)
            self.assertFalse(render_api.claim('task',render_api.Owner(owner='b'*32))['claimed'])

    def test_worker_token_is_separate_from_admin(self):
        from fastapi import HTTPException
        with patch.object(render_api,'boot',return_value=SimpleNamespace(render_worker_token='worker-only')):
            with self.assertRaises(HTTPException):
                render_api.authorize('Bearer admin')
            render_api.authorize('Bearer worker-only')

    def test_snapshot_never_loads_database_settings(self):
        with patch('app.settings_store.load',side_effect=AssertionError('DB read')), render_settings({'video':{'fps':30}}):
            self.assertEqual(cfg('video','fps'),30)
            self.assertEqual(cfg('missing',default=7),7)

    def test_profiles_use_actual_cpu_and_memory(self):
        with patch.object(render_profile,'resources',return_value=(2*1024**3,1)),render_profile.using('auto'):
            profile=render_profile.policy()
            self.assertTrue(profile.fast)
            self.assertEqual(profile.threads,1)
            self.assertEqual(profile.ceiling,1536*1024**2)
        with patch.object(render_profile,'resources',return_value=(512*1024**2,1)),render_profile.using('auto'):
            self.assertFalse(render_profile.policy().fast)
        with patch.object(render_profile,'resources',return_value=(8*1024**3,2)),render_profile.using('low_memory'):
            self.assertFalse(render_profile.policy().fast)

    def test_cgroup_v2_limits_override_host(self):
        def read(path):
            return {'/sys/fs/cgroup/memory.max':2*1024**3}.get(path)
        with patch.object(render_profile,'read_number',side_effect=read), \
             patch.object(render_profile.psutil,'virtual_memory',return_value=SimpleNamespace(total=32*1024**3)), \
             patch.object(Path,'read_text',return_value='100000 100000'):
            memory,cpus=render_profile.resources()
            self.assertEqual(memory,2*1024**3)
            self.assertEqual(cpus,1)

    def test_reuse_translation_cannot_call_model(self):
        from app.english_captions import english_captions
        with tempfile.TemporaryDirectory() as folder,patch('app.english_captions.generate_model') as model:
            with self.assertRaisesRegex(ValueError,'reuse does not call'):
                english_captions([{'word':'hello','start':0,'end':1}],'ta',Path(folder),allow_generate=False)
            model.assert_not_called()

    def test_worker_resumes_without_dispatch_or_generation(self):
        from app import worker
        job=SimpleNamespace(stage='rerender',video_id='video',payload_json={'render_task_id':'task'},
                            attempts=1,max_attempts=2,status='running')
        with patch.object(worker,'session_scope',return_value=self.db(job)), \
             patch.object(worker,'dispatch') as dispatch, \
             patch.object(remote_render,'finish',return_value={'output':'saved.mp4'}) as finish, \
             patch.object(worker,'queue_comparisons'),patch('app.storage.restore') as restore, \
             patch('app.storage.checkpoint') as checkpoint:
            worker.process('job')
            finish.assert_called_once_with('task')
            dispatch.assert_not_called(); restore.assert_not_called(); checkpoint.assert_not_called()
            self.assertEqual(job.status,'done')

    def test_deferred_job_is_not_marked_failed_or_completed(self):
        from app import worker
        job=SimpleNamespace(stage='rerender',video_id='video',payload_json={},attempts=1,max_attempts=2,status='waiting_render')
        with patch.object(worker,'session_scope',return_value=self.db(job)), \
             patch.object(worker,'dispatch',side_effect=remote_render.RenderDeferred('task')), \
             patch('app.storage.restore'),patch('app.storage.checkpoint') as checkpoint:
            worker.process('job')
            self.assertEqual(job.status,'waiting_render')
            checkpoint.assert_not_called()

    def test_config_does_not_run_on_push(self):
        import yaml
        config=yaml.safe_load((Path(__file__).resolve().parents[2]/'.circleci/config.yml').read_text())
        self.assertEqual(set(config['parameters']),{'render_task_id'})
        self.assertEqual(config['workflows']['render-request']['when']['and'][0],{'equal':['api','<< pipeline.trigger.type >>']})
        self.assertEqual(config['jobs']['render-media']['resource_class'],'small')

    def test_signed_download_url_has_storage_prefix(self):
        from app import storage
        response=MagicMock(status_code=200)
        response.json.return_value={'signedURL':'/object/sign/assets/file?token=short-lived'}
        with patch.object(storage,'_base',return_value='https://example.supabase.co'), \
             patch.object(storage,'_headers',return_value={}),patch.object(storage.httpx,'post',return_value=response):
            self.assertEqual(storage.signed_url('assets','file'),
                'https://example.supabase.co/storage/v1/object/sign/assets/file?token=short-lived')

    def test_memory_pressure_falls_back_without_changing_inputs(self):
        from app import media
        timeline=media.Timeline([30],[0],[0],30,30)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(render_profile,'resources',return_value=(2*1024**3,1)),render_profile.using('auto'), \
                 patch.object(media,'direct_segments',side_effect=render_profile.MemoryPressure('pressure')) as fast, \
                 patch.object(media,'ensure_clip',return_value=root) as clip,patch.object(media,'readable',return_value=True), \
                 patch('app.settings_store.cfg',return_value=True),patch.object(media,'assemble_bounded',return_value=root) as safe:
                media.assemble([root],root,timeline,[{'kind':'hard_cut'}],root,root,root)
                fast.assert_called_once();clip.assert_called_once();safe.assert_called_once()
                self.assertIs(safe.call_args.args[4],timeline)

    def test_build_video_defers_before_heavy_ffmpeg(self):
        import json
        from app import pipeline
        from app.schemas import EditPlan
        from app.settings_store import DEFAULTS
        row=SimpleNamespace(voice_json={},spec_json={})
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'images').mkdir()
            (root/'images/s001.png').write_bytes(b'image fixture')
            (root/'narration.wav').write_bytes(b'audio fixture')
            (root/'timeline.json').write_text(json.dumps({'spans':[[0,2]],'duration':2,
                'shots':[{'shot_id':'s001','narration':'Hello there'}],
                'alignment':{'captions':[{'word':'Hello','start':0,'end':1}]}}))
            with render_settings(DEFAULTS),patch.object(pipeline,'work_dir',return_value=root), \
                 patch.object(pipeline,'boot',return_value=SimpleNamespace(outputs=root)), \
                 patch.object(pipeline,'session_scope',return_value=self.db(row)), \
                 patch.object(pipeline,'set_state'),patch.object(pipeline,'record'), \
                 patch.object(pipeline,'analyze_audio',return_value=(None,[])), \
                 patch.object(pipeline,'timing_report'),patch.object(pipeline,'assemble') as assemble, \
                 patch.object(remote_render,'enabled',return_value=True), \
                 patch.object(remote_render,'submit',side_effect=remote_render.RenderDeferred) as submit:
                with self.assertRaises(remote_render.RenderDeferred):
                    pipeline.build_video('video',{'slug':'test'},EditPlan(transitions=[]),{'music_enabled':False},
                        [{'shot_id':'s001','narration':'Hello there'}])
                assemble.assert_not_called()
                self.assertEqual(submit.call_args.args[1]['operation'],'assemble')
                self.assertIn('captions.ass',submit.call_args.args[2])
                self.assertIn('fonts/LuckiestGuy-Regular.ttf',submit.call_args.args[2])

    def test_bgm_defers_without_remixing_on_render(self):
        from app import music_edit
        row=SimpleNamespace(output_path='source.mp4',duration_seconds=54,channel_slug='test',spec_json={},options_json={})
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'narration.wav').write_bytes(b'fixture')
            with patch('app.pipeline.work_dir',return_value=root),patch.object(music_edit,'session_scope',return_value=self.db(row)), \
                 patch.object(music_edit.registry,'music_track',return_value=None), \
                 patch.object(music_edit,'fetch_media',return_value=root/'source.mp4'), \
                 patch.object(music_edit,'remix') as remix,patch.object(remote_render,'enabled',return_value=True), \
                 patch.object(remote_render,'submit',side_effect=remote_render.RenderDeferred) as submit:
                with self.assertRaises(remote_render.RenderDeferred):
                    music_edit.apply('video',{})
                remix.assert_not_called()
                self.assertEqual(submit.call_args.args[1]['operation'],'remix')
                self.assertTrue(submit.call_args.args[3]['options']['force_review'])

    def test_ephemeral_worker_transfer_and_duplicate_contract(self):
        import hashlib
        import importlib.util
        script=Path(__file__).resolve().parents[1]/'scripts/circleci_render.py'
        for scenario in ('success','duplicate','bad-checksum'):
            with self.subTest(scenario=scenario),patch.dict(os.environ,{
                'RENDER_TASK_ID':'a'*32,'RENDER_API_URL':'https://render.example',
                'RENDER_WORKER_TOKEN':'worker-test'}):
                spec=importlib.util.spec_from_file_location('circleci_worker_test',script)
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                completed=[]
                data=b'checkpoint'
                manifest={'files':{'narration.wav':{'url':'https://storage.example/input',
                    'sha256':hashlib.sha256(data).hexdigest() if scenario!='bad-checksum' else '0'*64}}}
                def post(url,**kwargs):
                    self.assertTrue(url.startswith('https://render.example/internal/render-tasks/'))
                    self.assertEqual(kwargs['headers'],{'Authorization':'Bearer worker-test'})
                    response=MagicMock(status_code=200)
                    if url.endswith('/claim'):
                        response.json.return_value={'claimed':scenario!='duplicate','manifest':manifest}
                    elif url.endswith('/upload-url'):
                        response.json.return_value={'url':'https://storage.example/upload?token=scoped'}
                    else:
                        completed.append(kwargs['json']);response.json.return_value={'ok':True}
                    return response
                stream=MagicMock()
                stream.__enter__.return_value.iter_bytes.return_value=[data]
                def execute(received,root,output):
                    self.assertEqual((root/'narration.wav').read_bytes(),data)
                    output.write_bytes(b'mp4-fixture'*400)
                    return {'fps':30,'duration':54}
                with patch.object(module.httpx,'post',side_effect=post),patch.object(module.httpx,'stream',return_value=stream) as download, \
                     patch.object(module.httpx,'put',return_value=MagicMock(status_code=200)) as upload, \
                     patch('app.render_bundle.execute',side_effect=execute) as render,patch('app.render_metrics.measure') as measured, \
                     patch('sqlalchemy.event.listens_for',return_value=lambda fn:fn):
                    measured.return_value.__enter__.return_value={}
                    result=module.main()
                    if scenario=='success':
                        self.assertEqual(result,0);render.assert_called_once();upload.assert_called_once()
                        self.assertEqual(completed[-1]['status'],'succeeded')
                        self.assertNotIn('Authorization',upload.call_args.kwargs['headers'])
                    elif scenario=='duplicate':
                        self.assertEqual(result,0);render.assert_not_called();download.assert_not_called();upload.assert_not_called()
                    else:
                        self.assertEqual(result,1);render.assert_not_called();upload.assert_not_called()
                        self.assertEqual(completed[-1]['status'],'failed')
