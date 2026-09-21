"""Offline soundtrack and schedule regression tests. No provider calls or uploads."""
import subprocess
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import media, music_edit, scheduler, social


class MusicTests(unittest.TestCase):
    def test_manual_music_can_cross_channel_preferences(self):
        from app import api
        from app.models import Video, MusicTrack
        video = SimpleNamespace(state='READY', output_path='saved.mp4', channel_slug='lorehush', options_json={})
        track = SimpleNamespace(archived=False, rights_cleared=True, channels=['curiousduo'],
                                duration_seconds=90, trim_start=0, trim_end=90)
        db = MagicMock()
        db.__enter__.return_value.get.side_effect = lambda model, *args, **kw: video if model is Video else track
        db.__enter__.return_value.scalar.return_value = None
        with patch.object(api, 'session_scope', return_value=db):
            result = api.regenerate_endpoint('v', 'music', {'music_track':'drama-track'})
        self.assertEqual(result['stage'], 'change_music')
        self.assertEqual(video.state, 'QUEUED')
        self.assertFalse(video.options_json['preview_required'])
        self.assertTrue(video.options_json['force_review'])

    def test_pending_music_edit_still_blocks_publish(self):
        video = SimpleNamespace(state='AWAITING_APPROVAL', output_path='old.mp4', channel_slug='lorehush',
                                options_json={'force_review':True}, approved=False)
        db = MagicMock()
        db.__enter__.return_value.get.return_value = video
        db.__enter__.return_value.scalar.return_value = 'pending-music-job'
        with patch.object(social, 'session_scope', return_value=db):
            with self.assertRaisesRegex(ValueError, 'queued video edits'):
                social.approve_and_publish('v')
        db.__enter__.return_value.add.assert_not_called()

    def test_generation_uses_default_music_and_allows_override(self):
        from app import pipeline
        from app.settings_store import DEFAULTS
        def value(*path,default=None):
            if path==('music','default_track'):
                return 'default-track'
            current=DEFAULTS
            for item in path:
                if not isinstance(current,dict):
                    return default
                current=current.get(item,default)
            return current
        with patch.object(pipeline,'cfg',side_effect=value):
            self.assertEqual(pipeline.resolve_options({}, {})['music_track'],'default-track')
            self.assertEqual(pipeline.resolve_options({}, {'music_track':'chosen'})['music_track'],'chosen')

    def test_upload_stores_only_selected_crop(self):
        from app import api
        from fastapi import UploadFile
        from io import BytesIO
        ffmpeg=media.binary('ffmpeg')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'tone.wav'
            media.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','sine=frequency=180:duration=8',str(source)])
            db=MagicMock()
            captured={}
            def upload(bucket,key,path,delete_local=True):
                captured['duration']=media.seconds(path)
                captured['key']=key
                return 'https://example.test/music/test.m4a'
            with patch.object(api,'session_scope',return_value=db),patch.object(api._storage,'enabled',return_value=True),patch.object(api._storage,'upload',side_effect=upload),patch.object(api,'track_view',return_value={'id':'test'}):
                result=asyncio.run(api.upload_music(file=UploadFile(filename='tone.wav',file=BytesIO(source.read_bytes())),
                    name='Test',category='suspense',mood='',tempo=0,trim_start=2,trim_end=6,
                    default_volume_pct=30,channels='',rights_cleared=True,notes=''))
            self.assertEqual(result['track']['id'],'test')
            self.assertAlmostEqual(captured['duration'],4,places=1)
            self.assertTrue(captured['key'].startswith('suspense/'))

    def test_crop_validation(self):
        self.assertEqual(music_edit.crop_bounds(20, 3, 8), (3, 8))
        self.assertEqual(music_edit.crop_bounds(20, 3, 0), (3, 20))
        for start,end in [(-1,8),(0,21),(6,8),(float('nan'),10),(0,float('inf'))]:
            with self.assertRaises(ValueError):
                music_edit.crop_bounds(20,start,end)

    def test_schedule_window_crosses_midnight(self):
        with patch.object(scheduler,'cfg',return_value='23:30'):
            self.assertEqual(scheduler.window_start(datetime(2026,9,22,1,0)),datetime(2026,9,21,23,30))

    def test_explicit_publish_does_not_require_second_preview(self):
        video=SimpleNamespace(state='AWAITING_APPROVAL',output_path='new.mp4',approved=False,
            options_json={'preview_required':True,'reviewed_output':'old.mp4'},channel_slug='lorehush')
        db=MagicMock(); db.__enter__.return_value.get.return_value=video
        db.__enter__.return_value.scalar.return_value=None
        with patch.object(social,'session_scope',return_value=db), patch.object(social,'status',return_value={'youtube':True}):
            result = social.approve_and_publish('v')
        self.assertTrue(result['approved'])
        self.assertTrue(video.approved)
        self.assertFalse(video.options_json['preview_required'])
        self.assertEqual(db.__enter__.return_value.add.call_count,2)

    def test_force_review_overrides_autonomous_mode(self):
        video=SimpleNamespace(state='READY',output_path='video.mp4',approved=False,options_json={'force_review':True})
        db=MagicMock(); db.__enter__.return_value.get.return_value=video
        with patch.object(social,'session_scope',return_value=db),patch.object(social,'review_required',return_value=False),patch.object(social,'request_publish') as publish:
            self.assertEqual(social.route_upload('v',explicit=True)['status'],'awaiting_approval')
            publish.assert_not_called()

    def test_real_ffmpeg_crop_and_remix_preserve_video_frames(self):
        ffmpeg=media.binary('ffmpeg')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'video.mp4'; voice=root/'narration.wav'; bed=root/'bed.wav'
            media.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','color=c=blue:s=180x320:r=30:d=4',
                '-c:v','libx264','-pix_fmt','yuv420p',str(source)])
            for target,hz in [(voice,440),(bed,180)]:
                media.run([ffmpeg,'-y','-v','error','-f','lavfi','-i',f'sine=frequency={hz}:sample_rate=48000:duration=6',str(target)])
            track={'path':str(bed),'duration_seconds':6,'trim_start':1,'trim_end':5}
            def frame_hash(path):
                return subprocess.check_output([ffmpeg,'-v','error','-i',str(path),'-map','0:v:0','-c','copy','-f','hash','-hash','sha256','-'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            original_hash=frame_hash(source)
            with music_edit.prepared_music(track) as crop:
                self.assertAlmostEqual(media.seconds(crop),4,places=2)
                for ducking in (True,False):
                    output=root/f'remix-{ducking}.mp4'
                    music_edit.remix(source,voice,crop,output,4,.075,ducking)
                    self.assertEqual(frame_hash(output),original_hash)
                    self.assertAlmostEqual(media.seconds(output),4,places=1)
            self.assertFalse(crop.exists())


if __name__=='__main__':
    unittest.main()
