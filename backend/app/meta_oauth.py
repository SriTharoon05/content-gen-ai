"""Facebook Login for Instagram: one-use browser state, encrypted Page tokens, explicit selection."""
import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import delete

from .config import boot
from .db import session_scope
from .models import Channel, OAuthAttempt, MetaConnection
from .youtube_oauth import digest

SCOPES = ['pages_show_list', 'pages_read_engagement', 'instagram_basic', 'instagram_content_publish']


def cipher():
    env = boot()
    if env.oauth_encryption_key:
        return Fernet(env.oauth_encryption_key.encode())
    if not env.meta_app_secret:
        raise ValueError('Configure META_APP_SECRET')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('story-shorts:meta:' + env.meta_app_secret).encode()).digest()))


def config():
    env = boot()
    if not env.meta_app_id or not env.meta_app_secret:
        raise ValueError('Set META_APP_ID and META_APP_SECRET in Render Environment')
    uri = urlsplit(env.meta_redirect_uri)
    if (uri.scheme != 'https' and not (uri.scheme == 'http' and uri.hostname in ('localhost', '127.0.0.1'))
            or uri.path != '/auth/meta/callback' or uri.query or uri.fragment or uri.username or not uri.netloc):
        raise ValueError('META_REDIRECT_URI must be HTTPS and end with /auth/meta/callback (HTTP only on localhost)')
    if not re.fullmatch(r'v\d+\.\d+', env.instagram_graph_version):
        raise ValueError('Invalid META_API_VERSION')
    return env, uri


def issue_ticket(slug):
    env, uri = config()
    ticket = secrets.token_urlsafe(32)
    with session_scope() as s:
        if not s.get(Channel, slug):
            raise ValueError('Unknown channel')
        s.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < datetime.now(timezone.utc)))
        s.add(OAuthAttempt(id=digest(ticket), channel_slug=slug, phase='meta_ticket',
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    return f'{uri.scheme}://{uri.netloc}/auth/meta/start?' + urlencode({'ticket':ticket})


def begin(ticket):
    env, _ = config()
    state = secrets.token_urlsafe(32)
    with session_scope() as s:
        row = s.get(OAuthAttempt, digest(ticket), with_for_update=True)
        if not row or row.phase != 'meta_ticket' or row.expires_at < datetime.now(timezone.utc):
            raise ValueError('Meta connection link expired; start again from Publishing')
        slug = row.channel_slug
        s.delete(row)
        s.add(OAuthAttempt(id=digest(state), channel_slug=slug, phase='meta_consent',
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    return state, f'https://www.facebook.com/{env.instagram_graph_version}/dialog/oauth?' + urlencode({
        'client_id':env.meta_app_id, 'redirect_uri':env.meta_redirect_uri, 'response_type':'code',
        'state':state, 'scope':','.join(SCOPES), 'auth_type':'rerequest'})


def checked(response):
    if response.status_code != 200 or 'error' in response.json():
        # Never expose provider URLs, codes or token-bearing payloads to logs/UI.
        raise ValueError('Meta request failed. Check app permissions, callback URL and account access, then reconnect.')
    return response.json()


def complete(state, cookie, code):
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        raise ValueError('Meta browser state mismatch; reconnect from Publishing')
    env, _ = config()
    with session_scope() as s:
        row = s.get(OAuthAttempt, digest(state), with_for_update=True)
        if not row or row.phase != 'meta_consent' or row.expires_at < datetime.now(timezone.utc):
            raise ValueError('Meta session expired or already used')
        slug = row.channel_slug
        s.delete(row)
    base = f'https://graph.facebook.com/{env.instagram_graph_version}'
    with httpx.Client(timeout=30) as client:
        short = checked(client.post(base + '/oauth/access_token', data={
            'client_id':env.meta_app_id, 'client_secret':env.meta_app_secret,
            'redirect_uri':env.meta_redirect_uri, 'code':code}))
        long = checked(client.post(base + '/oauth/access_token', data={
            'grant_type':'fb_exchange_token', 'client_id':env.meta_app_id,
            'client_secret':env.meta_app_secret, 'fb_exchange_token':short['access_token']}))
        headers = {'Authorization':'Bearer ' + long['access_token']}
        granted = checked(client.get(base + '/me/permissions', headers=headers))
        permissions = {p['permission'] for p in granted.get('data',[]) if p.get('status') == 'granted'}
        if not set(SCOPES).issubset(permissions):
            raise ValueError('Grant Page listing/read and Instagram basic/publishing permissions, then reconnect')
        accounts, after = [], None
        for _ in range(20):
            params = {'fields':'id,name,access_token,instagram_business_account{id,username}', 'limit':100}
            if after:
                params['after'] = after
            result = checked(client.get(base + '/me/accounts', headers=headers, params=params))
            for page in result.get('data',[]):
                ig = page.get('instagram_business_account') or {}
                if ig.get('id') and page.get('access_token'):
                    accounts.append({'id':ig['id'], 'name':ig.get('username') or page['name'],
                                     'page_id':page['id'], 'page_name':page['name'], 'token':page['access_token']})
            paging = result.get('paging',{})
            if not paging.get('next'):
                break
            after = paging.get('cursors',{}).get('after')
            if not after:
                raise ValueError('Cannot finish listing Pages; reconnect with fewer selected Pages')
        else:
            raise ValueError('Too many Pages; reconnect granting only the relevant Pages')
    if not accounts:
        raise ValueError('No linked professional Instagram account found. Link it to a Facebook Page and grant access to that Page.')
    with session_scope() as s:
        row = s.get(MetaConnection, slug, with_for_update=True)
        if not row:
            row = MetaConnection(channel_slug=slug)
            s.add(row)
        row.pending_encrypted = cipher().encrypt(json.dumps(accounts).encode()).decode()
        row.pending_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    return slug


def pending_accounts(row):
    if not row.pending_encrypted or not row.pending_until or row.pending_until < datetime.now(timezone.utc):
        return []
    return json.loads(cipher().decrypt(row.pending_encrypted.encode()).decode())


def select_account(slug, account_id):
    with session_scope() as s:
        row = s.get(MetaConnection, slug, with_for_update=True)
        matches = [a for a in pending_accounts(row) if a['id'] == account_id] if row else []
        if not matches:
            raise ValueError('Account selection expired or invalid; reconnect Meta')
        account = matches[0]
        row.token_encrypted = cipher().encrypt(account['token'].encode()).decode()
        row.account_id, row.account_name, row.page_id = account['id'], account['name'], account['page_id']
        row.pending_encrypted, row.pending_until = '', None
        return {'account_id':row.account_id, 'account_name':row.account_name}


def connection_status(slug):
    result = {'meta_oauth_configured':bool(boot().meta_app_id and boot().meta_app_secret),
              'instagram_account_name':'', 'instagram_account_id':'', 'meta_accounts':[], 'meta_error':''}
    with session_scope() as s:
        row = s.get(MetaConnection, slug)
        if row:
            result.update(instagram_account_name=row.account_name, instagram_account_id=row.account_id)
            try:
                result['meta_accounts'] = [{k:a[k] for k in ('id','name','page_name')} for a in pending_accounts(row)]
            except Exception:
                result['meta_error'] = 'Saved Meta authorization cannot be read; reconnect Meta'
    return result


def credentials(slug):
    with session_scope() as s:
        row = s.get(MetaConnection, slug)
        if not row or not row.token_encrypted:
            return {}
        return {'instagram_account_id':row.account_id,
                'instagram_access_token':cipher().decrypt(row.token_encrypted.encode()).decode()}
