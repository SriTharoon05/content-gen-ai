"""Optional real locking tests against an EMPTY disposable local Postgres, never Supabase."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app import remote_render, render_api
from app.models import Base, Channel, Video, Job, RenderTask, new_id


@unittest.skipUnless(os.getenv('TEST_RENDER_DATABASE_URL'), 'Set a disposable local TEST_RENDER_DATABASE_URL')
class PostgresRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=make_url(os.environ['TEST_RENDER_DATABASE_URL'])
        if url.host not in ('127.0.0.1','localhost') or url.database != 'render_tests':
            raise ValueError('Refusing any database except localhost/render_tests')
        cls.engine=create_engine(url,connect_args={'prepare_threshold':None})
        Base.metadata.create_all(cls.engine,tables=[Channel.__table__,Video.__table__,Job.__table__,RenderTask.__table__])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @contextmanager
    def session(self):
        with Session(self.engine,expire_on_commit=False) as s:
            with s.begin():
                yield s

    def setUp(self):
        with self.session() as s:
            channel=Channel(slug=new_id(),name='Offline test')
            s.add(channel);s.flush()
            video=Video(channel_slug=channel.slug)
            s.add(video);s.flush()
            job=Job(video_id=video.id,stage='rerender',status='running')
            s.add(job);s.flush()
            self.video,self.job=video.id,job.id
        for module in (remote_render,render_api):
            guard=patch.object(module,'session_scope',self.session)
            guard.start();self.addCleanup(guard.stop)

    def task(self,status='queued'):
        with self.session() as s:
            task=RenderTask(job_id=self.job,video_id=self.video,status=status,manifest_json={'files':{}},
                deadline=datetime.now(timezone.utc)+timedelta(hours=4))
            s.add(task);s.flush()
            return task.id

    def test_concurrent_claims_have_exactly_one_owner(self):
        task_id=self.task()
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(lambda n:render_api.claim(task_id,render_api.Owner(owner=f'{n:032d}')),range(8)))
        self.assertEqual(sum(r['claimed'] for r in replies),1)

    def test_reconcile_is_idempotent_and_preserves_stage(self):
        task_id=self.task('succeeded')
        with self.session() as s:
            job=s.get(Job,self.job);job.status='waiting_render';job.attempts=1
        with patch.object(remote_render,'dispatch_pending'):
            remote_render.reconcile();remote_render.reconcile()
        with self.session() as s:
            job=s.get(Job,self.job)
            self.assertEqual((job.status,job.stage,job.attempts),('queued','rerender',0))

    def test_expired_running_task_fails_without_reclaim(self):
        task_id=self.task('running')
        with self.session() as s:
            s.get(Job,self.job).status='waiting_render'
            s.get(RenderTask,task_id).deadline=datetime.now(timezone.utc)-timedelta(seconds=1)
        with patch.object(remote_render,'dispatch_pending'):
            remote_render.reconcile()
        with self.session() as s:
            self.assertEqual(s.get(RenderTask,task_id).status,'failed')
            self.assertEqual(s.get(Job,self.job).status,'failed')

    def test_submit_commits_assets_and_task_before_trigger(self):
        from types import SimpleNamespace
        def dispatch():
            with self.session() as s:
                job=s.get(Job,self.job)
                self.assertEqual(job.status,'waiting_render')
                self.assertIsNotNone(s.get(RenderTask,job.payload_json['render_task_id']))
        token=remote_render.current_job.set(self.job)
        try:
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'narration.wav';path.write_bytes(b'test fixture')
                with patch.object(remote_render,'boot',return_value=SimpleNamespace(render_worker_token='test',remote_render_timeout_minutes=240)), \
                     patch('app.storage.enabled',return_value=True),patch('app.storage.upload') as upload, \
                     patch('app.storage.checkpoint'),patch('app.settings_store.cfg',return_value=30), \
                     patch.object(remote_render,'dispatch_pending',side_effect=dispatch):
                    with self.assertRaises(remote_render.RenderDeferred):
                        remote_render.submit(self.video,{'operation':'assemble'},{'narration.wav':path},{})
                    upload.assert_called_once()
                    with self.assertRaises(remote_render.RenderDeferred):
                        remote_render.submit(self.video,{'operation':'assemble'},{'narration.wav':path},{})
                    upload.assert_called_once()
        finally:
            remote_render.current_job.reset(token)

    def test_failed_retry_creates_new_task_reusing_manifest(self):
        task_id=self.task('failed')
        with self.session() as s:
            job=s.get(Job,self.job);job.status='failed';job.payload_json={'render_task_id':task_id}
            result=remote_render.retry_task(s,job)
            self.assertNotEqual(result['render_task_id'],task_id)
        with self.session() as s:
            fresh=s.get(RenderTask,result['render_task_id'])
            self.assertEqual(fresh.manifest_json['files'],{})
            self.assertEqual(s.get(RenderTask,task_id).status,'failed')
            self.assertEqual(s.get(Job,result['job_id']).status,'waiting_render')
