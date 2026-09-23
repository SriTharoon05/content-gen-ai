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
