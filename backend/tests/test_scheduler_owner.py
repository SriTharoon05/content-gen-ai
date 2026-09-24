import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from app import scheduler


class SchedulerOwnerTests(unittest.TestCase):
    def test_cloudflare_owner_prevents_render_daily_queue(self):
        session = MagicMock()
        session.get.return_value = SimpleNamespace(value_json={'scheduler_owner': 'cloudflare'})
        @contextmanager
        def scope():
            yield session
        with patch.object(scheduler, 'session_scope', scope), \
             patch.object(scheduler, 'remaining_credits', return_value=1), \
             patch.object(scheduler, 'credits_per_image', return_value=.002):
            result = scheduler.plan_batch(videos_per_channel=1, scheduled_date='2026-09-24')
        self.assertEqual(result['queued'], 0)
        self.assertEqual(result['scheduler_owner'], 'cloudflare')
        session.add.assert_not_called()
        session.scalars.assert_not_called()


if __name__ == '__main__':
    unittest.main()
