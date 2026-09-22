"""Real FFmpeg regressions for unknown-rate xfade links; synthetic local media only."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import media
from app.settings_store import DEFAULTS


class RenderCFRTests(unittest.TestCase):
    def test_direct_segments_exact_frame_counts(self):
        from PIL import Image
        from app.render_profile import using
        with tempfile.TemporaryDirectory() as folder, patch('app.settings_store.cfg',side_effect=self.config(30)), using('auto'):
            work=Path(folder)
            images=[work/f'{i}.png' for i in range(3)]
            for path in images:
                Image.new('RGB',(180,320),'red').save(path)
            transitions=[{'kind':'hard_cut'}, {'kind':'crossfade'}, {'kind':'crossfade'}]
            timeline=media.build_timeline([(0,1),(1,2),(2,3)],[{**t,'duration_ms':300} for t in transitions],30,3)
            pieces=media.direct_segments(images,timeline,transitions,work)
            actual=[int(media.probe(path)['streams'][0]['nb_frames']) for path in pieces]
            self.assertEqual(actual,[21,9,21,9,30])

    def setUp(self):
        # These regressions must run during image build with no database or provider access.
        guard = patch('app.db.engine.connect', side_effect=AssertionError('Render test attempted database access'))
        guard.start()
        self.addCleanup(guard.stop)

    def config(self, fps=30, bounded=True):
        overrides = {('video','fps'):fps, ('video','width'):180, ('video','height'):320,
                     ('runtime','low_memory_render'):bounded}
        return lambda section, key, default=None: overrides.get((section,key), DEFAULTS.get(section,{}).get(key,default))

    def test_timing_normalizes_after_timestamp_reset(self):
        for fps in (24,25,30):
            self.assertEqual(media.cfr_filter(fps),f'setpts=PTS-STARTPTS,fps={fps}:start_time=0,settb=AVTB')
        for invalid in (0,-1,30.5,'30'):
            with self.assertRaises(ValueError):
                media.cfr_filter(invalid)

    def test_unknown_concat_rate_fails_without_normalization_and_passes_with_it(self):
        with patch('app.settings_store.cfg',side_effect=self.config()):
            args = [media.binary('ffmpeg'),'-v','error','-filter_complex_threads','1']
            for rate in (24,30,30):
                args += ['-f','lavfi','-i',f'color=blue:s=180x320:r={rate}:d=1']
            raw = '[0:v][1:v]concat=n=2:v=1:a=0,setpts=PTS-STARTPTS,settb=AVTB[a];[2:v]settb=AVTB[b];[a][b]xfade=duration=0.3:offset=1.7[v]'
            with self.assertRaisesRegex(RuntimeError,'constant frame rate'):
                media.run(args+['-filter_complex',raw,'-map','[v]','-f','null','-'])
            fixed = f'[0:v][1:v]concat=n=2:v=1:a=0,{media.cfr_filter(30)}[a];[2:v]{media.cfr_filter(30)}[b];[a][b]xfade=duration=0.3:offset=1.7[v]'
            media.run(args+['-filter_complex',fixed,'-map','[v]','-f','null','-'])

    def test_full_assembly_mixed_cuts_and_blends_at_all_supported_rates(self):
        # Both code paths must preserve the exact video-frame count and narration duration.
        from app.captions import write_ass
        for fps in (24,25,30):
            with self.subTest(fps=fps), tempfile.TemporaryDirectory() as folder:
                work=Path(folder)
                transitions=[{'kind':kind,'duration_ms':300} for kind in
                             ('hard_cut','hard_cut','zoom_out','match_cut','crossfade','dissolve')]
                timeline=media.build_timeline([(i,i+1) for i in range(6)],transitions,fps,6)
                with patch('app.settings_store.cfg',side_effect=self.config(fps)), \
                     patch('app.captions.cfg',side_effect=self.config(fps)):
                    clips=[]
                    for i, frames in enumerate(timeline.frames):
                        clip=work/f'scene-{i}.mp4'
                        media.run([media.binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i',
                                   f'color={"blue" if i%2 else "red"}:s=180x320:r={fps}',
                                   '-frames:v',str(frames),'-c:v','libx264','-threads','1',
                                   '-pix_fmt','yuv420p',str(clip)])
                        clips.append(clip)
                    voice=work/'voice.wav'
                    media.run([media.binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i',
                               'sine=frequency=440:duration=6',str(voice)])
                    captions=work/'captions.ass'
                    write_ass([{'word':'Timing','start':.2,'end':1.2}],[],set(),captions,6)
                from app.render_profile import using
                for bounded, profile in ((True,'low_memory'),(False,'low_memory'),(True,'auto')):
                    with self.subTest(bounded=bounded), patch('app.settings_store.cfg',side_effect=self.config(fps,bounded)), \
                         patch.object(media,'ensure_clip',side_effect=clips), using(profile):
                        # Auto consumes image sources directly instead of intermediate scene MP4s.
                        if profile == 'auto':
                            from PIL import Image
                            for i in range(6):
                                Image.new('RGB',(180,320),'blue' if i%2 else 'red').save(work/f'image-{i}.png')
                        output=work/f'final-{bounded}-{profile}.mp4'
                        media.assemble([work/f'image-{i}.png' for i in range(6)],voice,timeline,
                                       transitions,captions,output,work/'pieces')
                        streams=media.probe(output)['streams']
                        video=next(s for s in streams if s['codec_type']=='video')
                        audio=next(s for s in streams if s['codec_type']=='audio')
                        self.assertEqual(int(video['nb_frames']),timeline.total_frames)
                        self.assertEqual(video['avg_frame_rate'],f'{fps}/1')
                        self.assertAlmostEqual(float(video['duration']),6,delta=1/fps)
                        self.assertAlmostEqual(float(video['duration']),float(audio['duration']),delta=1/fps)


if __name__=='__main__':
    unittest.main()
