import importlib.util
from pathlib import Path
import tempfile
import shutil
import wave
import math
import struct
import unittest
from unittest.mock import patch, MagicMock

spec=importlib.util.spec_from_file_location('cf_media_worker',Path(__file__).parents[1]/'scripts'/'media_worker.py')
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class MediaTests(unittest.TestCase):
    def test_translated_phrases_keep_source_alignment_and_use_phrase_ass(self):
        from app.settings_store import DEFAULTS
        beats=[{'shot_id':f's{i:03}','narration':'An original measured sentence for this scene.','emphasis_words':[]} for i in range(1,21)]
        words=' '.join(b['narration'] for b in beats).split()
        heard=[{'word':w,'start':.12+i*.4,'end':.12+i*.4+.3} for i,w in enumerate(words)]
        phrases=[{'word':'An English translation, not newly timed words.','start':.12,'end':3.0}]
        manifest={'version':1,'operation':'assemble_script','settings':DEFAULTS,'language':'ta','caption_mode':'translated_phrase',
            'captions':phrases,'script':{'hook_kind':'question','hook':beats[0]['narration'],'beats':beats},'words':heard,
            'images':[f'images/{b["shot_id"]}.png' for b in beats],
            'transitions':[{'shot_id':b['shot_id'],'kind':'zoom_out','duration_ms':300} for b in beats]}
        with tempfile.TemporaryDirectory() as folder, patch('app.audio.probe_duration',return_value=60),patch('app.audio.speech_window',return_value=(.12,59.8)),patch('app.captions.analyze_audio',return_value=([],[])),patch('app.captions.write_ass',return_value={'dialogue_lines':1}) as ass:
            result=worker.prepare_script_bundle(manifest,Path(folder))
            self.assertEqual(result['timeline']['total_frames'],1800)
            self.assertEqual(ass.call_args.kwargs['phrase_mode'],True)
            self.assertEqual(ass.call_args.args[0][0]['word'],phrases[0]['word'])
            manifest['captions'][0]['end']=80
            with self.assertRaisesRegex(ValueError,'caption timing'):
                worker.prepare_script_bundle(manifest,Path(folder))

    def test_fresh_bundle_uses_measured_words_and_canonical_timeline(self):
        import json
        from app.settings_store import DEFAULTS
        beats=[{'shot_id':f's{i:03}','narration':'Could this old mystery have another surprising answer?','emphasis_words':[]} for i in range(1,21)]
        words=' '.join(b['narration'] for b in beats).split()
        heard=[{'word':w,'start':.12+i*.37,'end':.12+i*.37+.3} for i,w in enumerate(words)]
        manifest={'version':1,'operation':'assemble_script','settings':DEFAULTS,
            'script':{'hook_kind':'question','hook':beats[0]['narration'],'beats':beats},'words':heard,
            'images':[f'images/{b["shot_id"]}.png' for b in beats],
            'transitions':[{'shot_id':b['shot_id'],'kind':'zoom_out','duration_ms':300} for b in beats]}
        with tempfile.TemporaryDirectory() as folder, patch('app.audio.probe_duration',return_value=60),patch('app.audio.speech_window',return_value=(.12,59.8)),patch('app.captions.analyze_audio',return_value=([],[])):
            root=Path(folder); result=worker.prepare_script_bundle(manifest,root)
            self.assertEqual(result['operation'],'assemble')
            self.assertEqual(result['timeline']['total_frames'],1800)
            self.assertEqual(len(result['images']),20)
            report=json.loads((root/'caption-timing.json').read_text())
            self.assertEqual(report['heard_words'],len(words))
            self.assertTrue((root/'captions.ass').exists())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_real_audio_preparation_preserves_readable_wav(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with wave.open(str(root/'source.audio'),'wb') as audio:
                audio.setparams((1,2,24000,0,'NONE','not compressed'))
                audio.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*2*math.pi*220/24000))) for i in range(48000)))
            out=root/'prepared.wav'
            result=worker.execute_media({'version':1,'operation':'prepare_audio','settings':{'runtime':{'ffmpeg_path':'ffmpeg'}},'playback_rate':.94},root,out)
            self.assertGreater(result['duration'],2)
            with wave.open(str(out)) as audio:
                self.assertGreater(audio.getnframes(),0)
                self.assertEqual(audio.getnchannels(),1)

    def test_video_delegates_to_canonical_renderer(self):
        with patch('app.render_bundle.execute',return_value={'fps':30}) as render:
            manifest={'version':1,'operation':'assemble'}
            self.assertEqual(worker.execute_media(manifest,Path('/inputs'),Path('/out')),{'fps':30})
            render.assert_called_once_with(manifest,Path('/inputs'),Path('/out'))

    def test_audio_delegates_to_existing_pacing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'prepared.wav';output.write_bytes(b'a'*2048)
            with patch('app.audio.pace_and_trim',return_value=50.0) as pace, patch('app.audio.probe_duration',return_value=50.0):
                result=worker.execute_media({'version':1,'operation':'prepare_audio','settings':{},'playback_rate':.9},root,output)
                self.assertEqual(result['duration'],50)
                pace.assert_called_once_with(root/'source.audio',output,.9,120,250)

    def test_unsafe_path_fails_before_network(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker.httpx,'stream') as network:
            with self.assertRaises(ValueError): worker.download_assets({'files':{'../escape':{}}},Path(folder))
            network.assert_not_called()

    def test_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker.httpx,'stream') as stream:
            stream.return_value.__enter__.return_value.iter_bytes.return_value=[b'invalid']
            with self.assertRaisesRegex(ValueError,'checksum'): worker.download_assets({'files':{'asset':{'url':'https://example.test/a','sha256':'a'*64}}},Path(folder))

    def test_speed_bounds(self):
        with self.assertRaises(ValueError): worker.execute_media({'version':1,'operation':'prepare_audio','playback_rate':5},Path('/in'),Path('/out'))

if __name__=='__main__': unittest.main()
