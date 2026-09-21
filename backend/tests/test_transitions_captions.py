import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import media
from app.english_captions import english_captions
from app.schemas import TransitionChoice


class TransitionCaptionTests(unittest.TestCase):
    def test_editor_rejects_all_cuts_and_accepts_context_blend(self):
        from app.agents import roles
        shots = [{'shot_id': f's{i:03}', 'duration': 3., 'narration': 'A specific scene'} for i in range(4)]
        def fake(schema, *args, **kwargs):
            entries = [{'shot_id': s['shot_id'], 'kind': 'hard_cut'} for s in shots]
            with self.assertRaises(ValueError):
                schema.model_validate({'transitions': entries})
            entries[2]['kind'] = 'zoom_out'
            return schema.model_validate({'transitions': entries})
        with patch.object(roles, 'context', return_value=''), patch.object(roles, 'generate_model', side_effect=fake):
            result = roles.plan_edit({}, shots, [])
            self.assertEqual(result.transitions[2].kind, 'zoom_out')

    def test_zoom_out_schema_and_no_swipes(self):
        self.assertEqual(TransitionChoice(shot_id='s002', kind='zoom_out').normalised().duration_ms, 320)
        with self.assertRaises(ValueError):
            TransitionChoice(shot_id='s002', kind='slideleft')
        with patch('app.settings_store.cfg', side_effect=lambda *a, default=None: default):
            self.assertIn('(1-on/59)', media.zoom_filter(60, 180, 320, 30, True))

    def test_english_is_unchanged_without_provider(self):
        words = [{'word': 'Hello', 'start': .1, 'end': .6}]
        with patch('app.english_captions.generate_model') as generate:
            self.assertIs(english_captions(words, 'en-US', Path('.')), words)
            generate.assert_not_called()

    def test_translation_keeps_measured_phrase_times_and_caches(self):
        words = [{'word': 'வணக்கம்.', 'start': .2, 'end': 1.2}, {'word': 'நண்பா.', 'start': 1.8, 'end': 2.4}]
        def fake(schema, *a, **kw):
            return schema.model_validate({'phrases': [{'id': 0, 'text': 'Hello.'}, {'id': 1, 'text': 'My friend.'}]})
        with tempfile.TemporaryDirectory() as tmp, patch('app.english_captions.generate_model', side_effect=fake) as generate:
            captions = english_captions(words, 'ta', Path(tmp))
            self.assertEqual([(p['start'], p['end']) for p in captions], [(.2, 1.2), (1.8, 2.4)])
            self.assertEqual(english_captions(words, 'ta', Path(tmp)), captions)
            self.assertEqual(generate.call_count, 1)

    def test_translation_rejects_missing_ids(self):
        def fake(schema, *a, **kw):
            return schema.model_validate({'phrases': []})
        with tempfile.TemporaryDirectory() as tmp, patch('app.english_captions.generate_model', side_effect=fake):
            with self.assertRaises(ValueError):
                english_captions([{'word': 'வணக்கம்.', 'start': .2, 'end': 1.2}], 'ta', Path(tmp))

    def test_real_ffmpeg_bounded_transitions_preserve_frames(self):
        # Synthetic local fixtures only: no model requests, purchased images or published output.
        from app.settings_store import DEFAULTS
        config = lambda section, key, default=None: DEFAULTS.get(section, {}).get(key, default)
        with tempfile.TemporaryDirectory() as tmp, patch('app.settings_store.cfg', side_effect=config):
            work = Path(tmp)
            kinds = ['hard_cut', 'zoom_out', 'crossfade', 'dissolve', 'dip_to_black', 'flash_white']
            transitions = [{'kind': k, 'duration_ms': 300} for k in kinds]
            timeline = media.build_timeline([(i, i + 1) for i in range(len(kinds))], transitions, 30, len(kinds))
            clips = []
            for i, frames in enumerate(timeline.frames):
                clip = work / f'source-{i}.mp4'
                media.run([media.binary('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i',
                           f'color=c={"red" if i % 2 else "blue"}:s=180x320:r=30', '-frames:v', str(frames),
                           '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(clip)])
                clips.append(clip)
            pieces = media.transition_segments(clips, timeline, transitions, work)
            count = sum(int(media.probe(p)['streams'][0]['nb_frames']) for p in pieces)
            self.assertEqual(count, timeline.total_frames)
            voice = work / 'voice.wav'
            media.run([media.binary('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i',
                       'sine=frequency=440:duration=6', str(voice)])
            from app.captions import write_ass
            captions = work / 'captions.ass'
            write_ass([{'word': 'A clear English subtitle translated from another language.', 'start': .2, 'end': 2.8}],
                      [], set(), captions, 6, phrase_mode=True)
            self.assertIn(r'\N', captions.read_text())
            output = work / 'output.mp4'
            media.assemble_bounded(pieces, voice, captions, output, timeline, None, 0, False, 0)
            self.assertAlmostEqual(media.seconds(output), 6, delta=.05)
