import os
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused')
import unittest
from unittest.mock import patch, MagicMock
from app import llm
from app.key_pool import KeyPool
from pydantic import BaseModel


def response(status=200, text='{"answer":"yes"}', finish='stop'):
    r = MagicMock(status_code=status, headers={})
    r.json.return_value = {'choices': [{'message': {'content': text}, 'finish_reason': finish}],
                           'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}
    return r


class GroqTextTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        def advance(seconds):
            self.now += seconds
        clock = patch('app.key_pool.time.time', side_effect=lambda: self.now)
        sleep = patch('app.key_pool.time.sleep', side_effect=advance)
        clock.start()
        self.sleep = sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)

    def test_gemini_overload_falls_back_before_paid(self):
        keys = {'gemini_free': KeyPool('free', ['f1', 'f2']),
                'groq_text': KeyPool('groq_text', ['g1'])}
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError('503 UNAVAILABLE high demand')
        with patch.object(llm, 'pool', side_effect=keys.__getitem__), patch.object(llm, 'cfg', return_value=True), patch.object(llm, '_build_config'), patch('google.genai.Client', return_value=client), patch.object(llm.httpx, 'post', return_value=response()) as post, patch.object(llm, '_record_usage') as usage:
            self.assertEqual(llm.generate_text('question'), '{"answer":"yes"}')
            self.assertEqual(client.models.generate_content.call_count, 2)
            self.assertEqual(post.call_args.kwargs['json']['model'], 'openai/gpt-oss-120b')
            self.assertEqual(post.call_args.kwargs['json']['response_format'], {'type': 'json_object'})
            self.assertEqual(usage.call_args.kwargs['provider'], 'groq_text')
            self.assertEqual(keys['gemini_free'].status()['cooling'], 2)

    def test_auth_replacement_and_plain_text(self):
        keys = KeyPool('groq_text', ['bad', 'good'])
        with patch.object(llm, 'pool', return_value=keys), patch.object(llm.httpx, 'post', side_effect=[response(401), response(text='Hello')]) as post, patch.object(llm, '_record_usage'):
            self.assertEqual(llm._groq_text('hello', '', .8, False), 'Hello')
            self.assertNotIn('response_format', post.call_args.kwargs['json'])
            self.assertEqual(keys.status()['rejected'], 1)

    def test_independent_quota_tries_next_key_without_waiting(self):
        keys = KeyPool('groq_text', ['one', 'two'])
        with patch.object(llm, 'pool', return_value=keys), patch.object(llm.httpx, 'post', side_effect=[response(429), response()]) as post, patch.object(llm, '_record_usage'):
            self.assertEqual(llm._groq_text('q', '', .9, True), '{"answer":"yes"}')
            self.assertEqual(post.call_count, 2)
            self.assertEqual(keys.status()['cooling'], 1)
            self.sleep.assert_not_called()

    def test_all_fail_three_ordered_rounds_only(self):
        keys = KeyPool('groq_text', ['one', 'two', 'three', 'four'])
        with patch.object(llm, 'pool', return_value=keys), patch.object(llm.httpx, 'post', return_value=response(429)) as post:
            with self.assertRaises(llm.TextGenerationError): llm._groq_text('q', '', .9, True)
            self.assertEqual(post.call_count, 12)
            self.assertEqual([c.kwargs['headers']['Authorization'] for c in post.call_args_list],
                             ['Bearer one', 'Bearer two', 'Bearer three', 'Bearer four'] * 3)
            self.assertEqual(self.sleep.call_count, 2)
            self.assertEqual(keys.status()['cooling'], 4)

    def test_long_retry_after_is_not_bypassed(self):
        keys = KeyPool('groq_text', ['one'])
        r = response(429)
        r.headers = {'retry-after': '3600'}
        with patch.object(llm, 'pool', return_value=keys), patch.object(llm.httpx, 'post', return_value=r) as post:
            with self.assertRaises(llm.TextGenerationError): llm._groq_text('q', '', .9, True)
            self.assertEqual(post.call_count, 1)

    def test_second_round_recovers(self):
        keys = KeyPool('groq_text', ['one', 'two'])
        with patch.object(llm, 'pool', return_value=keys), patch.object(llm.httpx, 'post', side_effect=[response(503), response(503), response()]) as post, patch.object(llm, '_record_usage'):
            self.assertEqual(llm._groq_text('q', '', .9, True), '{"answer":"yes"}')
            self.assertEqual(post.call_count, 3)
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer one')
            self.sleep.assert_called_once_with(65)

    def test_empty_and_truncated_outputs_fail(self):
        for text, finish in [('', 'stop'), ('partial', 'length')]:
            with self.subTest(finish=finish), patch.object(llm, 'pool', return_value=KeyPool('groq_text', ['one'])), patch.object(llm.httpx, 'post', return_value=response(text=text, finish=finish)), patch.object(llm, '_record_usage'):
                with self.assertRaises(llm.TextGenerationError): llm._groq_text('q', '', .9, True)

    def test_schema_validation_still_repairs(self):
        class Answer(BaseModel):
            answer: str
        with patch.object(llm, 'generate_text', side_effect=['{"wrong":1}', '{"answer":"yes"}']) as generate:
            self.assertEqual(llm.generate_model(Answer, 'q').answer, 'yes')
            self.assertEqual(generate.call_count, 2)

    def test_no_paid_fallback_when_disabled(self):
        with patch.object(llm, 'cfg', return_value=False), patch.object(llm, 'pool', return_value=KeyPool('empty', [])) as pool, patch.object(llm, '_groq_text', side_effect=llm.TextGenerationError('unavailable')):
            with self.assertRaises(llm.TextGenerationError): llm.generate_text('q')
            pool.assert_called_once_with('gemini_free')

if __name__ == '__main__':
    unittest.main()
