"""Offline workflow checks: never upload, generate media, or contact providers."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import social, youtube_oauth
from app.models import Publication, Job


class PublishingTests(unittest.TestCase):
    def db(self, row):
        context = MagicMock()
        session = context.__enter__.return_value
        session.get.return_value = row
        session.scalar.return_value = None
        return context, session

    def video(self):
        return SimpleNamespace(state='READY', output_path='https://example.test/video.mp4',
            approved=False, options_json={}, channel_slug='lorehush', progress=100)

    def test_review_mode_waits_without_upload(self):
        video = self.video()
        context, _ = self.db(video)
        with patch.object(social, 'session_scope', return_value=context), patch.object(social, 'review_required', return_value=True), patch.object(social, 'request_publish') as upload:
            self.assertEqual(social.route_upload('v', explicit=True)['status'], 'awaiting_approval')
            self.assertEqual(video.state, 'AWAITING_APPROVAL')
            upload.assert_not_called()

    def test_autonomous_mode_queues_without_approval(self):
        video = self.video()
        # This test targets YouTube only, regardless of saved dashboard destinations.
        video.options_json = {'publish_platforms': ['youtube']}
        context, _ = self.db(video)
        with patch.object(social, 'session_scope', return_value=context), patch.object(social, 'review_required', return_value=False), patch.object(social, 'request_publish', return_value={'status':'pending'}) as upload:
            self.assertEqual(social.route_upload('v', explicit=True)['status'], 'pending')
            upload.assert_called_once_with('v','youtube')
            self.assertFalse(video.approved)

    def test_comparison_opt_out_stays_excluded(self):
        video = self.video(); video.options_json = {'auto_publish':False}
        context, _ = self.db(video)
        with patch.object(social, 'session_scope', return_value=context), patch.object(social, 'cfg', return_value=True), patch.object(social, 'request_publish') as upload:
            self.assertEqual(social.route_upload('v')['status'], 'disabled')
            upload.assert_not_called()

    def test_finished_eligible_production_automatically_queues(self):
        context, _ = self.db(self.video())
        with patch.object(social,'session_scope',return_value=context), patch.object(social,'cfg',return_value=True), patch.object(social,'review_required',return_value=False), patch.object(social,'status',return_value={'youtube':True}), patch.object(social,'request_publish',return_value={'status':'pending'}) as upload:
            self.assertEqual(social.route_upload('v')['status'],'pending')
            upload.assert_called_once_with('v','youtube')

    def test_unapproved_direct_upload_cannot_bypass_review(self):
        context, session = self.db(self.video())
        with patch.object(social,'session_scope',return_value=context), patch.object(social,'review_required',return_value=True):
            with self.assertRaisesRegex(ValueError,'Review this video'):
                social.request_publish('v','youtube')
            session.add.assert_not_called()

    def test_approve_queues_one_public_upload(self):
        video = self.video(); video.state = 'AWAITING_APPROVAL'
        context, session = self.db(video)
        with patch.object(social,'session_scope',return_value=context), patch.object(social,'review_required',return_value=True), patch.object(social,'status',return_value={'youtube':True}), patch.object(social,'cfg',return_value='public'):
            social.approve_and_publish('v')
        added = [c.args[0] for c in session.add.call_args_list]
        self.assertTrue(video.approved)
        self.assertEqual(video.state,'READY')
        self.assertEqual(len([x for x in added if isinstance(x,Job)]),1)
        self.assertEqual(next(x for x in added if isinstance(x,Publication)).requested_privacy,'public')

    def test_duplicate_publication_does_not_queue_again(self):
        video = self.video(); video.approved = True
        context, session = self.db(video)
        session.scalar.side_effect = [None, SimpleNamespace(id='p',status='published',remote_id='remote')]
        with patch.object(social,'session_scope',return_value=context), patch.object(social,'review_required',return_value=True), patch.object(social,'status',return_value={'youtube':True}):
            result = social.request_publish('v','youtube')
        self.assertEqual(result['remote_id'],'remote')
        session.add.assert_not_called()

    def test_oauth_rejects_wrong_browser_before_exchange(self):
        with patch.object(youtube_oauth.httpx,'Client') as client:
            with self.assertRaisesRegex(ValueError,'state mismatch'):
                youtube_oauth.complete('one','two','code')
            client.assert_not_called()

    def test_pending_edits_block_approval_upload(self):
        video = self.video(); video.approved = True
        context, session = self.db(video)
        session.scalar.return_value = 'active-edit'
        with patch.object(social,'session_scope',return_value=context), patch.object(social,'review_required',return_value=True):
            with self.assertRaisesRegex(ValueError,'queued video edits'):
                social.request_publish('v','youtube')
            session.add.assert_not_called()

    def test_private_restriction_is_reported_not_claimed_public(self):
        context, session = self.db(self.video())
        session.scalar.return_value = SimpleNamespace(id='p',remote_id='remote',requested_privacy='public')
        response = MagicMock()
        response.json.return_value = {'items':[{'status':{'privacyStatus':'private'},'processingDetails':{'processingStatus':'succeeded'}}]}
        with patch.object(social,'session_scope',return_value=context), patch.object(social,'youtube_token',return_value='token'), patch.object(social.httpx,'get',return_value=response), patch.object(social,'_update') as update:
            result = social.verify_youtube_publication('v')
            self.assertEqual(result['privacyStatus'],'private')
            self.assertIn('YouTube reports private',result['warning'])
            self.assertEqual(update.call_args.kwargs['actual_privacy'],'private')

    def test_callback_ticket_rejects_non_https_remote_url(self):
        env = SimpleNamespace(youtube_client_id='id',youtube_client_secret='secret',google_redirect_uri='http://example.test/auth/google/callback')
        with patch.object(youtube_oauth,'boot',return_value=env):
            with self.assertRaisesRegex(ValueError,'HTTPS'):
                youtube_oauth.issue_ticket('lorehush')

    def test_tokens_are_encrypted_and_recoverable(self):
        env = SimpleNamespace(oauth_encryption_key='',youtube_client_secret='test-secret')
        with patch.object(youtube_oauth,'boot',return_value=env):
            encrypted = youtube_oauth.cipher().encrypt(b'test-refresh-token')
            self.assertNotIn(b'test-refresh-token',encrypted)
            self.assertEqual(youtube_oauth.cipher().decrypt(encrypted),b'test-refresh-token')


if __name__ == '__main__':
    unittest.main()
