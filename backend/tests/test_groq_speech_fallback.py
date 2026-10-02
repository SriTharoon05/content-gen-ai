import io
import unittest
import wave
from unittest.mock import patch

import httpx
from app.providers import groq_speech


def wav_bytes():
    b = io.BytesIO()
    with wave.open(b, 'wb') as w:
        w.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        w.writeframes(b'\x00\x00' * 24)
    return b.getvalue()


class GroqSpeechFallback(unittest.TestCase):
    def test_rate_limit_advances_to_independent_key(self):
        seen = []
        def respond(url, **kwargs):
            key = kwargs['headers']['Authorization']
            seen.append(key)
            return httpx.Response(429, headers={'retry-after': '999'}) if key.endswith('first') else httpx.Response(200, content=wav_bytes())
        with patch.object(groq_speech, 'cfg', return_value=['first', 'second']), patch.object(httpx.Client, 'post', side_effect=respond), patch.object(groq_speech.time, 'sleep') as sleep:
            self.assertEqual(groq_speech.request_audio('Hello.', 'fixture', 'troy'), wav_bytes())
            self.assertEqual(seen, ['Bearer first', 'Bearer second'])
            self.assertTrue(all(c.args[0] <= 6.2 for c in sleep.call_args_list))

    def test_all_fail_only_three_complete_sweeps(self):
        seen = []
        def respond(url, **kwargs):
            seen.append(kwargs['headers']['Authorization'])
            return httpx.Response(429, headers={'retry-after': '1'})
        with patch.object(groq_speech, 'cfg', return_value=['first', 'second']), patch.object(httpx.Client, 'post', side_effect=respond), patch.object(groq_speech.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '3 bounded sweeps'):
                groq_speech.request_audio('Hello.', 'fixture', 'troy')
        self.assertEqual(seen, ['Bearer first', 'Bearer second'] * 3)
