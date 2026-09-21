import unittest
from unittest.mock import patch,MagicMock
from app import pipeline
from app.schemas import spoken_text
from app.settings_store import DEFAULTS
from app.providers.groq_speech import chunks

class ExpressiveTests(unittest.TestCase):
    def test_visual_changes_do_not_force_speaker_changes(self):
        beats=[{'speaker':'Alex','narration':'Have you ever noticed how'},
               {'speaker':'Alex','narration':'old songs bring back a memory?'},
               {'speaker':'Sam','narration':'Yeah, exactly! That happens to me too.'}]
        with patch.object(pipeline,'session_scope',return_value=MagicMock()):
            voice=pipeline.stage_voice('v',{'strategy_json':{'conversation':True}},None,None,beats,'en',{'speech_tempo':1.0})
        self.assertEqual(len(voice['tagged_transcript'].splitlines()),2)
        self.assertEqual(spoken_text(voice['tagged_transcript']),' '.join(b['narration'] for b in beats))

    def test_image_cap_and_longer_target(self):
        with patch.object(pipeline,'cfg',side_effect=lambda a,b,default=None:DEFAULTS.get(a,{}).get(b,default)):
            options=pipeline.resolve_options({}, {'max_shots':80,'target_seconds':90})
        self.assertEqual(options['max_shots'],30)
        self.assertEqual(options['target_seconds'],75)

    def test_groq_prefers_whole_sentence_chunks(self):
        first='This is a natural sentence with enough detail to explain the idea clearly. '
        second='Here is another complete sentence that responds thoughtfully to the previous idea without sounding like a robot.'
        result=chunks(first+first+second)
        self.assertTrue(all(len(x)<=200 for x in result))
        self.assertEqual(' '.join(result),' '.join((first+first+second).split()))
        self.assertTrue(all(x.endswith('.') for x in result))
