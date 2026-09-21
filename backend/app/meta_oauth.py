"""Direct Instagram Login: no Facebook Pages, one-use state and independent browser nonce."""
import base64
import hashlib
import logging
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

SCOPES = ['instagram_business_basic', 'instagram_business_content_publish', 'instagram_business_manage_insights']


class TokenURLFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if 'graph.instagram.com' in message:
            record.msg = re.sub(r'(https://graph\.instagram\.com/[^\s?"\']+)\?[^\s"\']+', r'\1?[redacted]', message)
            record.args = ()
        return True


# Instagram's long-token endpoints require secrets in query parameters; never log their URLs.
logging.getLogger('httpx').addFilter(TokenURLFilter())


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
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with session_scope() as s:
        row = s.get(OAuthAttempt, digest(ticket), with_for_update=True)
        if not row or row.phase != 'meta_ticket' or row.expires_at < datetime.now(timezone.utc):
            raise ValueError('Meta connection link expired; start again from Publishing')
        slug = row.channel_slug
        s.delete(row)
        s.add(OAuthAttempt(id=digest(state), channel_slug=slug, phase='ig_consent', verifier=digest(nonce),
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    return nonce, 'https://www.instagram.com/oauth/authorize?' + urlencode({
        'client_id':env.meta_app_id, 'redirect_uri':env.meta_redirect_uri, 'response_type':'code',
        'state':state, 'scope':','.join(SCOPES), 'enable_fb_login':'0', 'force_authentication':'1'})


def checked(response):
    try:
        data = response.json()
    except ValueError:
        raise ValueError('Instagram returned an invalid response; reconnect and try again') from None
    if not isinstance(data, dict) or response.status_code != 200 or 'error' in data:
        # Never expose provider URLs, codes or token-bearing payloads to logs/UI.
        raise ValueError('Instagram request failed. Check permissions, token expiry and callback URL, then reconnect.')
    return data


def graph(method, path, token, **params):
    if not re.fullmatch(r'(me|\d+)(/insights)?', path):
        raise ValueError('Invalid Instagram resource')
    return checked(httpx.request(method, f'https://graph.instagram.com/{boot().instagram_graph_version}/{path}',
        headers={'Authorization':'Bearer ' + token}, params=params if method == 'GET' else None,
        data=params if method != 'GET' else None, timeout=30))


def complete(state, cookie, code):
    if not state or not cookie:
        raise ValueError('Meta browser state mismatch; reconnect from Publishing')
    env, _ = config()
    with session_scope() as s:
        row = s.get(OAuthAttempt, digest(state), with_for_update=True)
        if (not row or row.phase != 'ig_consent' or row.expires_at < datetime.now(timezone.utc)
                or not secrets.compare_digest(row.verifier, digest(cookie))):
            raise ValueError('Meta session expired or already used')
        slug = row.channel_slug
        s.delete(row)
    with httpx.Client(timeout=30) as client:
        short = checked(client.post('https://api.instagram.com/oauth/access_token', data={
            'client_id':env.meta_app_id, 'client_secret':env.meta_app_secret,
            'grant_type':'authorization_code', 'redirect_uri':env.meta_redirect_uri, 'code':code}))
        long = checked(client.get('https://graph.instagram.com/access_token', params={
            'grant_type':'ig_exchange_token', 'client_secret':env.meta_app_secret,
            'access_token':short['access_token']}))
        headers = {'Authorization':'Bearer ' + long['access_token']}
        account = checked(client.get(f'https://graph.instagram.com/{env.instagram_graph_version}/me',
                          headers=headers, params={'fields':'user_id,username'}))
    if not account.get('user_id') or not account.get('username'):
        raise ValueError('Instagram did not return a professional account identity')
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(long['expires_in']))
    with session_scope() as s:
        row = s.get(MetaConnection, slug, with_for_update=True)
        if not row:
            row = MetaConnection(channel_slug=slug)
            s.add(row)
        row.token_encrypted = cipher().encrypt(long['access_token'].encode()).decode()
        row.account_id, row.account_name = str(account['user_id']), account['username']
        row.login_mode, row.page_id = 'instagram', ''
        row.token_expires_at = expiry
        row.pending_encrypted, row.pending_until = '', None
    return slug


def connection_status(slug):
    result = {'meta_oauth_configured':bool(boot().meta_app_id and boot().meta_app_secret),
              'instagram_account_name':'', 'instagram_account_id':'', 'meta_accounts':[], 'meta_error':''}
    with session_scope() as s:
        row = s.get(MetaConnection, slug)
        if row:
            result.update(instagram_account_name=row.account_name, instagram_account_id=row.account_id)
            try:
                if row.token_encrypted and (row.login_mode != 'instagram' or not row.token_expires_at or row.token_expires_at <= datetime.now(timezone.utc)):
                    result['meta_error'] = 'Reconnect using direct Instagram Login; the old connection is incompatible or expired'
            except Exception:
                result['meta_error'] = 'Saved Meta authorization cannot be read; reconnect Meta'
    return result


def credentials(slug, validate=False):
    with session_scope() as s:
        row = s.get(MetaConnection, slug, with_for_update=validate)
        if not row or not row.token_encrypted:
            return {}
        if row.login_mode != 'instagram' or not row.token_expires_at or row.token_expires_at <= datetime.now(timezone.utc):
            raise ValueError('Reconnect through direct Instagram Login; this token is expired or from the old Facebook flow')
        token = cipher().decrypt(row.token_encrypted.encode()).decode()
        if validate:
            if row.token_expires_at < datetime.now(timezone.utc) + timedelta(days=7):
                refreshed = checked(httpx.get('https://graph.instagram.com/refresh_access_token',
                    params={'grant_type':'ig_refresh_token','access_token':token}, timeout=30))
                token = refreshed['access_token']
                row.token_encrypted = cipher().encrypt(token.encode()).decode()
                row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(refreshed['expires_in']))
        return {'instagram_account_id':row.account_id, 'instagram_access_token':token}


def insights(slug, days=28):
    days = max(1, min(30, days))
    c = credentials(slug, validate=True)
    if not c:
        raise ValueError('Connect Instagram first')
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    result = graph('GET', c['instagram_account_id'] + '/insights', c['instagram_access_token'],
                   metric='views,reach,accounts_engaged,total_interactions', metric_type='total_value',
                   period='day', since=int(start.timestamp()), until=int(end.timestamp()))
    return {'platform':'instagram', 'channel':slug, 'start':start.date().isoformat(), 'end':end.date().isoformat(),
            'summary':{item['name']:item.get('total_value',{}).get('value') for item in result.get('data',[])}}
