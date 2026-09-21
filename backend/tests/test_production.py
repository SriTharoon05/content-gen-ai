import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import alignment, captions, scheduler
from app.story_selection import score_subtopic, verify_and_score
from app.media import build_timeline
from app.key_pool import KeyPool, NoKeysConfigured


class ProductionTests(unittest.TestCase):
    def test_conversation_cast_is_puck_and_zephyr(self):
        from app import pipeline
        from app.schemas import spoken_text
        with patch.object(pipeline, 'session_scope', return_value=MagicMock()):
            beats=[{'narration':'Why does that happen?'},{'narration':'Here is the explanation.'}]
            result=pipeline.stage_voice('id',{'strategy_json':{'conversation':True}},None,None,beats,'en',{'speech_tempo':1.0})
            self.assertEqual([entry['voice'] for entry in result['cast']],['Puck','Zephyr'])
            self.assertEqual(spoken_text(result['tagged_transcript']),'Why does that happen? Here is the explanation.')

    def test_groq_dialogue_fallback_uses_two_distinct_voices(self):
        import io, wave
        from app.providers import groq_speech
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\x00\x00'*240)
        with tempfile.TemporaryDirectory() as tmp, patch.object(groq_speech,'cfg',side_effect=lambda *p,default=None:default), patch.object(groq_speech,'request_audio',return_value=buffer.getvalue()) as request, patch.object(groq_speech,'pace_and_trim',return_value=50), patch.object(groq_speech,'session_scope',return_value=MagicMock()):
            result=groq_speech.synthesize('id','Alex: Hello there.\nSam: That is interesting.',Path(tmp)/'voice.wav','en',1.0,
                model_override='canopylabs/orpheus-v1-english',cast=[{'speaker':'Alex','voice':'Puck'},{'speaker':'Sam','voice':'Zephyr'}])
            self.assertTrue(result['multi_speaker'])
            self.assertEqual([call.args[2] for call in request.call_args_list],['troy','hannah'])
            self.assertEqual([call.args[0] for call in request.call_args_list],['Hello there.','That is interesting.'])

    def test_speed_change_never_generates_images(self):
        from app import regenerate
        from types import SimpleNamespace
        row = SimpleNamespace(voice_json={'voice':'troy','speech_tempo':1.0}, options_json={})
        db = MagicMock()
        db.__enter__.return_value.get.return_value = row
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'images').mkdir()
            (root/'images'/'s001.png').touch()
            with patch.object(regenerate,'_load',return_value=({},None,None,{}, {},'en')), patch.object(regenerate,'_beats',return_value=[{'shot_id':'s001'}]), patch.object(regenerate.pipeline,'work_dir',return_value=root), patch.object(regenerate,'session_scope',return_value=db), patch.object(regenerate.pipeline,'stage_narrate',return_value={'duration':54.5}) as narrate, patch.object(regenerate.pipeline,'stage_images') as images, patch.object(regenerate,'_invalidate_clips'), patch.object(regenerate,'_rebuild',return_value={}):
                result = regenerate.change_speed('id',{'playback_rate':0.94})
                self.assertEqual(result['images_added'],0)
                self.assertEqual(narrate.call_args.kwargs['playback_rate'],0.94)
                images.assert_not_called()

    def test_free_tts_fallback_does_not_use_paid_key(self):
        from app.providers import free_speech
        with tempfile.TemporaryDirectory() as tmp, patch.object(free_speech,'cfg',return_value=['free-a','free-b']), patch.object(free_speech,'_via_generate_content',side_effect=RuntimeError('quota')) as gemini, patch('app.providers.groq_speech.synthesize',return_value={'fallback':True}) as groq:
            result = free_speech.synthesize('id','Hello',Path(tmp)/'voice.wav','Puck',{},'en',None,1.0,False)
            self.assertTrue(result['fallback'])
            self.assertEqual([call.args[0] for call in gemini.call_args_list],['free-a','free-b'])
            self.assertEqual(groq.call_args.kwargs['model_override'],'canopylabs/orpheus-v1-english')

    def test_backward_asr_boundaries_keep_words_and_order(self):
        heard=[{'word':'own','start':29.94,'end':30.08},{'word':'ghosts.','start':30.08,'end':30.54},{'word':'He','start':29.86,'end':30.84},{'word':'fought','start':30.84,'end':31}]
        result=alignment.measured_captions(heard,32)
        self.assertEqual(result[0],{'word':'own ghosts. He','start':29.86,'end':30.84})

    def test_shared_asr_start_uses_measured_phrase(self):
        result = alignment.measured_captions([
            {'word':'power.', 'start':23.12, 'end':23.6},
            {'word':'Her', 'start':23.12, 'end':24.14},
            {'word':'political', 'start':24.14, 'end':24.54}], 25)
        self.assertEqual(result[0], {'word':'power. Her', 'start':23.12, 'end':24.14})

    def test_groq_chunk_limit_preserves_words(self):
        from app.providers.groq_speech import chunks
        source = 'An engaging sentence about the history of the world. ' * 20
        result = chunks('[dramatic] ' + source)
        self.assertTrue(all(0 < len(part) <= 200 for part in result))
        self.assertEqual(' '.join(result), ' '.join(source.split()))

    def test_groq_rejects_wrong_language_before_api(self):
        from app.providers import groq_speech
        with patch.object(groq_speech, 'cfg', return_value='canopylabs/orpheus-v1-english'), patch.object(groq_speech, 'request_audio') as request:
            with self.assertRaises(ValueError):
                groq_speech.synthesize('id', 'Hello', Path('unused.wav'), 'ta', 1.0)
            request.assert_not_called()

    def test_groq_provider_error_not_masked_by_wav(self):
        from app.providers import groq_speech
        with tempfile.TemporaryDirectory() as tmp, patch.object(groq_speech, 'cfg', side_effect=lambda *p,default=None: 'canopylabs/orpheus-v1-english' if p == ('models','tts') else default), patch.object(groq_speech, 'request_audio', side_effect=RuntimeError('model_terms_required')):
            with self.assertRaisesRegex(RuntimeError, 'model_terms_required'):
                groq_speech.synthesize('id', 'Hello world', Path(tmp)/'narration.wav', 'en', 1.0)

    def test_billing_failure_never_retries_transport(self):
        from app.providers.speech import synthesize, BillingRequired
        with tempfile.TemporaryDirectory() as tmp, patch('app.providers.speech.cfg',side_effect=lambda *p,default=None: 'test-key' if p==('keys','gemini_audio_paid') else 4 if p==('runtime','provider_retries') else 'gemini-3.1-flash-tts-preview'), patch('app.providers.speech._via_sdk',side_effect=RuntimeError('402 prepayment credits depleted')) as sdk, patch('app.providers.speech._via_rest') as rest:
            with self.assertRaises(BillingRequired):
                synthesize('test','Hello world',Path(tmp)/'voice.wav','Charon',{})
            self.assertEqual(sdk.call_count,1)
            rest.assert_not_called()

    def test_audio_timestamps_never_globally_shift(self):
        heard = [{'word':'one','start':0.1234,'end':0.16},{'word':'two','start':0.17,'end':0.20},
                 {'word':'three','start':2.1,'end':2.3}]
        result=alignment.measured_captions(heard,2.5)
        self.assertEqual([w['start'] for w in result],[w['start'] for w in heard])

    def test_missing_asr_fails_closed(self):
        with patch('app.alignment._sixteen_k',return_value=Path('x.wav')), patch('app.alignment._heard_groq',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'refusing guessed'):
                alignment.align(Path('x.wav'),'hello world',Path('.'),2)

    def test_25_shot_timeline_no_drift(self):
        spans=[(i*2.,(i+1)*2.) for i in range(25)]
        timeline=build_timeline(spans,[{'kind':'hard_cut'}]*25,30,50)
        self.assertEqual(timeline.total_frames,1500)
        self.assertEqual(sum(timeline.frames),1500)

    def test_cooling_free_pool_does_not_block_paid_fallback(self):
        pool=KeyPool('free',['a','b'])
        pool.penalise(0); pool.penalise(1)
        with self.assertRaises(NoKeysConfigured): pool.acquire(wait=False)

    def test_reputable_domain_spoof_rejected(self):
        self.assertFalse(score_subtopic([{'link':'https://nature.com.attacker.test/x','snippet':'sufficiently long but unreliable result'}]))
        self.assertFalse(score_subtopic([{'link':'https://bad.test/wikipedia.org','snippet':'sufficiently long but unreliable result'}]))
        self.assertTrue(score_subtopic([{'link':'https://en.wikipedia.org/wiki/Test','snippet':'a sufficiently long reputable result'}]))

    def test_empty_search_does_not_retry(self):
        client=MagicMock(); client.search.return_value={'organic_results':[]}
        self.assertFalse(verify_and_score('term',client)); self.assertEqual(client.search.call_count,1)

    def test_transient_search_retries(self):
        client=MagicMock(); client.search.side_effect=[None,{'organic_results':[{'link':'https://arxiv.org/a','snippet':'a sufficiently long academic result'}]}]
        with patch('app.story_selection.time.sleep'):
            self.assertTrue(verify_and_score('term',client))

    def test_schedule_time_and_window(self):
        values={('schedule','enabled'):True,('schedule','run_at'):'10:00'}
        mock_session=MagicMock(); mock_session.get.return_value=None
        with patch('app.scheduler.cfg',side_effect=lambda *p,default=None:values.get(p,default)), patch('app.scheduler.session_scope') as scope:
            scope.return_value.__enter__.return_value=mock_session
            self.assertFalse(scheduler.due(datetime(2099,1,1,9,59,tzinfo=timezone.utc)))
            self.assertTrue(scheduler.due(datetime(2099,1,1,10,0,tzinfo=timezone.utc)))
            self.assertFalse(scheduler.due(datetime(2099,1,1,14,0,tzinfo=timezone.utc)))
            mock_session.get.return_value=object()
            self.assertFalse(scheduler.due(datetime(2099,1,1,10,1,tzinfo=timezone.utc)))

    def test_paid_routing_keeps_same_model(self):
        from app.llm import generate_text
        free=KeyPool('free',['f1','f2']); paid=KeyPool('paid',['p1'])
        seen=[]
        def client_factory(api_key,**kwargs):
            client=MagicMock()
            def call(**kwargs):
                seen.append((api_key,kwargs['model']))
                if api_key.startswith('f'): raise RuntimeError('429 quota exceeded')
                return type('Response',(),{'text':'OK','usage_metadata':None})()
            client.models.generate_content.side_effect=call
            return client
        with patch('app.llm.cfg',return_value=True), patch('app.llm.pool',side_effect=lambda name:free if name=='gemini_free' else paid), patch('google.genai.Client',side_effect=client_factory), patch('app.llm._build_config',return_value={}):
            self.assertEqual(generate_text('hello'),'OK')
        self.assertEqual(seen,[('f1','gemini-3.1-flash-lite'),('f2','gemini-3.1-flash-lite'),('p1','gemini-3.1-flash-lite')])

if __name__=='__main__': unittest.main()
