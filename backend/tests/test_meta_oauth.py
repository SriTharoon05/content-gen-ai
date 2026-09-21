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
            nonce, url = meta.begin('ticket')
        query = parse_qs(urlsplit(url).query)
        self.assertNotEqual(query['state'],[nonce])
        self.assertEqual(urlsplit(url).hostname,'www.instagram.com')
        self.assertEqual(query['enable_fb_login'],['0'])
        attempt = db.__enter__.return_value.add.call_args.args[0]
        self.assertEqual(attempt.verifier,meta.digest(nonce))
        self.assertEqual(attempt.id,meta.digest(query['state'][0]))
        self.assertEqual(query['redirect_uri'],['https://example.test/auth/meta/callback'])
        self.assertNotIn('test-secret',url)
        db.__enter__.return_value.delete.assert_called_once_with(row)

    def test_google_ticket_cannot_be_reused_for_meta(self):
        row = SimpleNamespace(phase='ticket', expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        with patch.object(meta,'boot',return_value=self.env()), patch.object(meta,'session_scope',return_value=self.db(row)), self.assertRaises(ValueError):
            meta.begin('ticket')

    def test_state_mismatch_never_calls_provider(self):
        row = SimpleNamespace(phase='ig_consent',verifier=meta.digest('expected'),expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        with patch.object(meta,'boot',return_value=self.env()),patch.object(meta,'session_scope',return_value=self.db(row)),patch.object(meta.httpx,'Client') as client, self.assertRaises(ValueError):
            meta.complete('a','b','code')
        client.assert_not_called()

    def test_complete_connects_directly_with_encrypted_token(self):
        attempt = SimpleNamespace(phase='ig_consent',verifier=meta.digest('nonce'),channel_slug='lorehush',expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        row = SimpleNamespace(pending_encrypted='', pending_until=None, account_id='old-account')
        db = self.db(None)
        db.__enter__.return_value.get.side_effect = [attempt,row]
        client = MagicMock()
        response = lambda body: SimpleNamespace(status_code=200,json=lambda:body)
        client.__enter__.return_value.post.return_value = response({'access_token':'short','user_id':99})
        client.__enter__.return_value.get.side_effect = [response({'access_token':'ig-secret','expires_in':5184000}),
            response({'user_id':'99','username':'lore'})]
        with patch.object(meta,'boot',return_value=self.env()),patch.object(meta,'session_scope',return_value=db),patch.object(meta.httpx,'Client',return_value=client):
            self.assertEqual(meta.complete('state','nonce','code'),'lorehush')
            self.assertNotIn('ig-secret',row.token_encrypted)
            self.assertEqual(meta.cipher().decrypt(row.token_encrypted.encode()),b'ig-secret')
        self.assertEqual(row.account_id,'99')
        self.assertEqual(row.pending_encrypted,'')

    def test_pending_status_never_returns_tokens(self):
        row = SimpleNamespace(account_id='',account_name='',pending_encrypted='',pending_until=None,token_encrypted='')
        with patch.object(meta,'boot',return_value=self.env()),patch.object(meta,'session_scope',return_value=self.db(row)):
            result = meta.connection_status('lorehush')
        self.assertNotIn('secret',json.dumps(result))

    def test_instagram_credentials_do_not_depend_on_google(self):
        with patch.object(meta,'credentials',return_value={'instagram_account_id':'99','instagram_access_token':'secret'}),patch.object(social,'credentials',side_effect=ValueError('Google unavailable')):
            self.assertEqual(social.instagram_credentials('lorehush')['instagram_account_id'],'99')

    def test_refresh_near_expiry_without_repeated_identity_request(self):
        with patch.object(meta,'boot',return_value=self.env()):
            row = SimpleNamespace(login_mode='instagram',account_id='99',token_encrypted=meta.cipher().encrypt(b'old').decode(),token_expires_at=datetime.now(timezone.utc)+timedelta(days=2))
            response = SimpleNamespace(status_code=200,json=lambda:{'access_token':'fresh','expires_in':5184000})
            with patch.object(meta,'session_scope',return_value=self.db(row)),patch.object(meta.httpx,'get',return_value=response) as refresh,patch.object(meta,'graph') as identity:
                self.assertEqual(meta.credentials('lorehush',validate=True)['instagram_access_token'],'fresh')
                refresh.assert_called_once()
                identity.assert_not_called()

    def test_old_facebook_and_expired_tokens_rejected(self):
        for mode,expiry in [('facebook',datetime.now(timezone.utc)+timedelta(days=60)),('instagram',datetime.now(timezone.utc)-timedelta(seconds=1))]:
            row = SimpleNamespace(login_mode=mode,token_encrypted='unused',token_expires_at=expiry)
            with patch.object(meta,'session_scope',return_value=self.db(row)),self.assertRaises(ValueError):
                meta.credentials('lorehush')

    def test_secret_exchange_urls_are_redacted(self):
        import logging
        record = logging.LogRecord('httpx',20,'',1,'HTTP GET %s',('https://graph.instagram.com/access_token?client_secret=SECRET&access_token=TOKEN',),None)
        meta.TokenURLFilter().filter(record)
        self.assertNotIn('SECRET',record.getMessage())
        self.assertNotIn('TOKEN',record.getMessage())
