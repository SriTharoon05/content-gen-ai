"""Instagram upload stages: failed is retryable, uncertain is not."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from app import social


def response(body, status=200):
    return httpx.Response(status, json=body)


class InstagramPublishingTests(unittest.TestCase):
    def upload(self, posts, states=None):
        client = MagicMock()
        client.post.side_effect = posts
        client.get.side_effect = states or [response({'status_code':'FINISHED'})]
        video = SimpleNamespace(channel_slug='test', output_path='https://storage.test/video.mp4')
        with patch.object(social, 'instagram_credentials', return_value={'instagram_access_token':'saved-token','instagram_account_id':'123'}), \
             patch.object(social.storage, 'is_supabase_url', return_value=True), \
             patch('app.publishing_copy.publication_copy', return_value={'instagram_caption':'Test'}), \
             patch.object(social.httpx, 'Client') as factory, patch.object(social, '_update') as update, \
             patch.object(social.time, 'sleep'):
            factory.return_value.__enter__.return_value = client
            result = social._instagram(video, SimpleNamespace(id='p'))
        return result, client, update

    def test_success_uses_saved_direct_instagram_token_and_container(self):
        result, client, update = self.upload([response({'id':'container'}), response({'id':'media'})])
        self.assertEqual(result, 'media')
        self.assertIn('graph.instagram.com', client.post.call_args_list[0].args[0])
        self.assertEqual(client.post.call_args_list[0].kwargs['headers']['Authorization'], 'Bearer saved-token')
        self.assertEqual(client.post.call_args.kwargs['data'], {'creation_id':'container'})
        update.assert_called_once_with('p', session_url='container')

    def test_local_protocol_error_before_create_is_safe_failure(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([httpx.LocalProtocolError('sensitive request detail')])
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn('sensitive', str(caught.exception))

    def test_create_rejection_has_codes_not_raw_secrets(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'error':{'code':190,'message':'secret-value'}},400)])
        self.assertFalse(caught.exception.uncertain)
        self.assertIn('code=190', str(caught.exception))
        self.assertNotIn('secret-value', str(caught.exception))

    def test_processing_failure_is_not_uncertain(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'id':'container'})], [response({'status_code':'ERROR'})])
        self.assertFalse(caught.exception.uncertain)

    def test_processing_timeout_does_not_publish(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'id':'container'})], [response({'status_code':'IN_PROGRESS'})]*30)
        self.assertFalse(caught.exception.uncertain)

    def test_final_publish_timeout_stays_uncertain(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'id':'container'}), httpx.ReadTimeout('private details')])
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn('private details', str(caught.exception))

    def test_final_publish_server_error_stays_uncertain(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'id':'container'}), response({'error':{'code':2}},503)])
        self.assertTrue(caught.exception.uncertain)

    def test_final_publish_explicit_rejection_is_retryable(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'id':'container'}), response({'error':{'code':9007,'error_subcode':2207027}},400)])
        self.assertFalse(caught.exception.uncertain)

    def test_missing_final_id_is_uncertain(self):
        with self.assertRaises(social.InstagramPublishError) as caught:
            self.upload([response({'id':'container'}), response({'success':True})])
        self.assertTrue(caught.exception.uncertain)

    def test_publish_persists_safe_failure(self):
        context = MagicMock()
        session = context.__enter__.return_value
        session.get.return_value = SimpleNamespace(state='READY', approved=True)
        session.scalar.return_value = SimpleNamespace(id='p',status='pending')
        with patch.object(social,'session_scope',return_value=context), \
             patch.object(social,'review_required',return_value=False), \
             patch.object(social,'_instagram',side_effect=social.InstagramPublishError('safe failure')), \
             patch.object(social,'_update') as update:
            with self.assertRaisesRegex(RuntimeError,'safe failure'):
                social.publish('v','instagram')
        update.assert_called_once_with('p',status='failed',error='safe failure')

    def route(self, platforms, connections, review=False):
        context = MagicMock()
        video = SimpleNamespace(state='READY', output_path='https://storage.test/v.mp4',
                                options_json={'publishing_mode':'direct','publish_platforms':platforms},
                                channel_slug='test', approved=False)
        context.__enter__.return_value.get.return_value = video
        with patch.object(social,'session_scope',return_value=context), \
             patch.object(social,'review_required',return_value=review), \
             patch.object(social,'cfg',return_value=True), \
             patch.object(social,'status',return_value=connections), \
             patch.object(social,'request_publish',return_value={'status':'pending'}) as upload:
            result = social.route_upload('v')
        return result, upload

    def test_autonomous_instagram_only(self):
        result, upload = self.route(['instagram'], {'instagram':True})
        self.assertEqual(result['status'],'pending')
        upload.assert_called_once_with('v','instagram')

    def test_autonomous_both_platforms(self):
        result, upload = self.route(['youtube','instagram'], {'youtube':True,'instagram':True})
        self.assertEqual(set(result['destinations']), {'youtube','instagram'})
        self.assertEqual(upload.call_count,2)

    def test_disconnected_instagram_does_not_block_youtube(self):
        result, upload = self.route(['youtube','instagram'], {'youtube':True,'instagram':False})
        self.assertEqual(result['destinations']['instagram']['status'],'not_connected')
        upload.assert_called_once_with('v','youtube')

    def test_review_blocks_both_destinations(self):
        result, upload = self.route(['youtube','instagram'], {'youtube':True,'instagram':True}, review=True)
        self.assertEqual(result['status'],'awaiting_approval')
        upload.assert_not_called()

    def test_run_options_preserve_destinations_and_reject_invalid(self):
        from app.api import RunOptions
        from pydantic import ValidationError
        with patch('app.api.cfg',return_value='test-image'):
            self.assertEqual(RunOptions(publish_platforms=['instagram']).as_options()['publish_platforms'],['instagram'])
        for value in ([], ['facebook'], ['youtube','instagram','youtube']):
            with self.assertRaises(ValidationError):
                RunOptions(publish_platforms=value)

    def test_platform_defaults_do_not_enable_instagram(self):
        with patch.object(social,'cfg',return_value=['youtube']):
            self.assertEqual(social.publishing_platforms(),['youtube'])
        self.assertEqual(social.publishing_platforms({'publish_platforms':['instagram','instagram']}),['instagram'])

    def test_retry_known_failed_instagram_queues_once(self):
        context = MagicMock(); session = context.__enter__.return_value
        session.get.return_value = SimpleNamespace(state='READY',output_path='v.mp4',approved=True,options_json={},channel_slug='test')
        publication = SimpleNamespace(id='p',status='failed',error='previous error')
        session.scalar.side_effect = [None,publication]
        with patch.object(social,'session_scope',return_value=context), \
             patch.object(social,'status',return_value={'instagram':True}), \
             patch.object(social,'review_required',return_value=True):
            self.assertEqual(social.request_publish('v','instagram',approve=True)['status'],'pending')
        self.assertEqual(session.add.call_count,1)

    def test_uncertain_instagram_never_queues_duplicate(self):
        context = MagicMock(); session = context.__enter__.return_value
        session.get.return_value = SimpleNamespace(state='READY',output_path='v.mp4',approved=True,options_json={},channel_slug='test')
        session.scalar.side_effect = [None,SimpleNamespace(id='p',status='uncertain',remote_id='')]
        with patch.object(social,'session_scope',return_value=context), \
             patch.object(social,'status',return_value={'instagram':True}), \
             patch.object(social,'review_required',return_value=True):
            self.assertEqual(social.request_publish('v','instagram',approve=True)['status'],'uncertain')
        session.add.assert_not_called()

    def test_verify_returns_instagram_link_without_credentials(self):
        context = MagicMock(); session = context.__enter__.return_value
        session.get.return_value = SimpleNamespace(channel_slug='test')
        session.scalar.return_value = SimpleNamespace(remote_id='123')
        with patch.object(social,'session_scope',return_value=context), \
             patch.object(social,'instagram_credentials',return_value={'instagram_access_token':'secret'}), \
             patch.object(social.httpx,'get',return_value=response({'id':'123','permalink':'https://www.instagram.com/reel/test/'})):
            self.assertEqual(social.verify_instagram_publication('v'),{'remote_id':'123','url':'https://www.instagram.com/reel/test/'})

    def test_verify_rejects_unexpected_link_host(self):
        context = MagicMock(); session = context.__enter__.return_value
        session.get.return_value = SimpleNamespace(channel_slug='test')
        session.scalar.return_value = SimpleNamespace(remote_id='123')
        with patch.object(social,'session_scope',return_value=context), \
             patch.object(social,'instagram_credentials',return_value={'instagram_access_token':'secret'}), \
             patch.object(social.httpx,'get',return_value=response({'id':'123','permalink':'https://untrusted.test/'})):
            with self.assertRaisesRegex(ValueError,'valid Reel link'):
                social.verify_instagram_publication('v')


if __name__ == '__main__':
    unittest.main()
