import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row
from app.generation_timing import generation_timing


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 23, tzinfo=timezone.utc)
        self.video = Row(created_at=self.start, updated_at=self.start + timedelta(days=2))

    def job(self, status='done', stage='produce_video', seconds=120):
        return Row(stage=stage, status=status, created_at=self.start,
                   updated_at=self.start + timedelta(seconds=seconds))

    def test_finished_excludes_later_video_edits_and_publishing(self):
        result = generation_timing(self.video, [self.job(), self.job(stage='publish', seconds=900)])
        self.assertEqual(result['elapsed_seconds'], 120)
        self.assertEqual(result['status'], 'complete')

    def test_active_includes_waiting(self):
        result = generation_timing(self.video, [self.job('waiting_render')], self.start + timedelta(seconds=200))
        self.assertEqual(result['elapsed_seconds'], 200)
        self.assertIsNone(result['finished_at'])

    def test_cloudflare_and_failure(self):
        self.assertEqual(generation_timing(self.video, [self.job('cf_done', 'cf_generation')])['status'], 'complete')
        self.assertEqual(generation_timing(self.video, [self.job('cf_failed', 'cf_generation')])['status'], 'failed')

    def test_missing_history_not_guessed(self):
        self.assertIsNone(generation_timing(self.video, []))

    def test_naive_utc_and_later_rerender(self):
        old = self.job()
        old.created_at = old.created_at.replace(tzinfo=None)
        old.updated_at = old.updated_at.replace(tzinfo=None)
        newer = self.job(seconds=900)
        newer.created_at += timedelta(minutes=10)
        self.assertEqual(generation_timing(self.video, [newer, old])['elapsed_seconds'], 120)
