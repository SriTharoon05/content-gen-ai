import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit, parse_qs

from app import meta_oauth as meta, social


class MetaTests(unittest.TestCase):
    def env(self, **kw):
        return SimpleNamespace(**dict(dict(meta_app_id='123', meta_app_secret='test-secret',
            meta_redirect_uri='https://example.test/auth/meta/callback', instagram_graph_version='v25.0',
            oauth_encryption_key='', social_channels_json='{}'), **kw))

    def db(self, row):
        db = MagicMock()
        db.__enter__.return_value.get.return_value = row
        return db

    def test_bad_callback_rejected(self):
        for uri in ['http://example.test/auth/meta/callback', 'https://example.test/wrong', 'https://example.test/auth/meta/callback?x=1']:
            with patch.object(meta,'boot',return_value=self.env(meta_redirect_uri=uri)), self.assertRaises(ValueError):
                meta.config()

    def test_begin_binds_provider_state_and_redirect(self):
        row = SimpleNamespace(phase='meta_ticket', channel_slug='lorehush', expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        db = self.db(row)
        with patch.object(meta,'boot',return_value=self.env()), patch.object(meta,'session_scope',return_value=db):
            state, url = meta.begin('ticket')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['state'],[state])
        self.assertEqual(query['redirect_uri'],['https://example.test/auth/meta/callback'])
        self.assertNotIn('test-secret',url)
        db.__enter__.return_value.delete.assert_called_once_with(row)

    def test_google_ticket_cannot_be_reused_for_meta(self):
        row = SimpleNamespace(phase='ticket', expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        with patch.object(meta,'boot',return_value=self.env()), patch.object(meta,'session_scope',return_value=self.db(row)), self.assertRaises(ValueError):
            meta.begin('ticket')

    def test_state_mismatch_never_calls_provider(self):
        with patch.object(meta.httpx,'Client') as client, self.assertRaises(ValueError):
            meta.complete('a','b','code')
        client.assert_not_called()

    def test_complete_encrypts_candidates_without_auto_selecting(self):
        attempt = SimpleNamespace(phase='meta_consent',channel_slug='lorehush',expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        row = SimpleNamespace(pending_encrypted='', pending_until=None, account_id='old-account')
        db = self.db(None)
        db.__enter__.return_value.get.side_effect = [attempt,row]
        client = MagicMock()
        response = lambda body: SimpleNamespace(status_code=200,json=lambda:body)
        client.__enter__.return_value.post.side_effect = [response({'access_token':'short'}),response({'access_token':'long'})]
        client.__enter__.return_value.get.side_effect = [response({'data':[{'permission':p,'status':'granted'} for p in meta.SCOPES]}),
            response({'data':[{'id':'42','name':'Page','access_token':'page-secret','instagram_business_account':{'id':'99','username':'lore'}}]})]
        with patch.object(meta,'boot',return_value=self.env()),patch.object(meta,'session_scope',return_value=db),patch.object(meta.httpx,'Client',return_value=client):
            self.assertEqual(meta.complete('state','state','code'),'lorehush')
            self.assertNotIn('page-secret',row.pending_encrypted)
            self.assertEqual(meta.pending_accounts(row)[0]['id'],'99')
        self.assertEqual(row.account_id,'old-account')

    def test_account_selection_is_scoped_encrypted_and_one_use(self):
        with patch.object(meta,'boot',return_value=self.env()):
            encrypted = meta.cipher().encrypt(json.dumps([{'id':'99','name':'lore','page_id':'42','page_name':'Page','token':'page-secret'}]).encode()).decode()
            row = SimpleNamespace(pending_encrypted=encrypted,pending_until=datetime.now(timezone.utc)+timedelta(minutes=5))
            with patch.object(meta,'session_scope',return_value=self.db(row)):
                with self.assertRaises(ValueError): meta.select_account('lorehush','100')
                result = meta.select_account('lorehush','99')
                self.assertEqual(result,{'account_id':'99','account_name':'lore'})
                self.assertNotIn('page-secret',row.token_encrypted)
                self.assertEqual(meta.credentials('lorehush')['instagram_access_token'],'page-secret')
                with self.assertRaises(ValueError): meta.select_account('lorehush','99')

    def test_pending_status_never_returns_tokens(self):
        row = SimpleNamespace(account_id='',account_name='',pending_encrypted='',pending_until=None)
        with patch.object(meta,'boot',return_value=self.env()),patch.object(meta,'session_scope',return_value=self.db(row)),patch.object(meta,'pending_accounts',return_value=[{'id':'99','name':'lore','page_name':'Page','token':'secret'}]):
            result = meta.connection_status('lorehush')
        self.assertNotIn('secret',json.dumps(result))

    def test_instagram_credentials_do_not_depend_on_google(self):
        with patch.object(meta,'credentials',return_value={'instagram_account_id':'99','instagram_access_token':'secret'}),patch.object(social,'credentials',side_effect=ValueError('Google unavailable')):
            self.assertEqual(social.instagram_credentials('lorehush')['instagram_account_id'],'99')
