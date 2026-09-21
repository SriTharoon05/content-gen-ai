"""Admin-initiated, browser-bound OAuth with one-use state and encrypted tokens."""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import delete

from .config import boot
from .db import session_scope
from .models import Channel, OAuthAttempt, SocialConnection

SCOPES = ' '.join([
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/yt-analytics.readonly',
])


def cipher():
    env = boot()
    if env.oauth_encryption_key:
        return Fernet(env.oauth_encryption_key.encode())
    if not env.youtube_client_secret:
        raise ValueError('Configure GOOGLE_CLIENT_SECRET first')
    # Stable server-only fallback; use a separate Fernet key in production so
    # client-secret rotation does not invalidate saved refresh tokens.
    key = hashlib.sha256(('story-shorts:oauth:' + env.youtube_client_secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def issue_ticket(slug):
    env = boot()
    if not env.youtube_client_id or not env.youtube_client_secret:
        raise ValueError('Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET')
    uri = urlsplit(env.google_redirect_uri)
    if uri.scheme != 'https' and not (uri.scheme == 'http' and uri.hostname in ('localhost', '127.0.0.1')):
        raise ValueError('Google callback must use HTTPS, except on localhost')
    if uri.path != '/auth/google/callback' or uri.query or uri.fragment:
        raise ValueError('GOOGLE_REDIRECT_URI must end with /auth/google/callback')
    ticket = secrets.token_urlsafe(32)
    with session_scope() as s:
        if not s.get(Channel, slug):
            raise ValueError('Unknown channel')
        s.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < datetime.now(timezone.utc)))
        s.add(OAuthAttempt(id=digest(ticket), channel_slug=slug,
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    return f'{uri.scheme}://{uri.netloc}/auth/google/start?{urlencode({"ticket": ticket})}'


def begin(ticket):
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    with session_scope() as s:
        attempt = s.get(OAuthAttempt, digest(ticket), with_for_update=True)
        if not attempt or attempt.phase != 'ticket' or attempt.expires_at < datetime.now(timezone.utc):
            raise ValueError('Connection link expired; start again from Publishing')
        slug = attempt.channel_slug
        s.delete(attempt)
        s.add(OAuthAttempt(id=digest(state), channel_slug=slug, phase='consent', verifier=verifier,
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return state, 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': boot().youtube_client_id, 'redirect_uri': boot().google_redirect_uri,
        'response_type': 'code', 'scope': SCOPES, 'access_type': 'offline',
        'prompt': 'consent select_account', 'state': state,
        'code_challenge': challenge, 'code_challenge_method': 'S256',
    })


def complete(state, cookie, code):
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        raise ValueError('OAuth browser state mismatch; start again from Publishing')
    with session_scope() as s:
        attempt = s.get(OAuthAttempt, digest(state), with_for_update=True)
        if not attempt or attempt.phase != 'consent' or attempt.expires_at < datetime.now(timezone.utc):
            raise ValueError('OAuth session expired or already used')
        slug, verifier = attempt.channel_slug, attempt.verifier
        s.delete(attempt)
    with httpx.Client(timeout=30) as client:
        r = client.post('https://oauth2.googleapis.com/token', data={
            'client_id': boot().youtube_client_id, 'client_secret': boot().youtube_client_secret,
            'redirect_uri': boot().google_redirect_uri, 'code': code,
            'code_verifier': verifier, 'grant_type': 'authorization_code',
        })
        if r.status_code != 200:
            raise ValueError('Google authorization failed; check the exact callback URL and reconnect')
        tokens = r.json()
        if not tokens.get('refresh_token'):
            raise ValueError('Google did not grant offline access; reconnect and grant the requested permissions')
        granted = set(tokens.get('scope', '').split())
        if not set(SCOPES.split()).issubset(granted):
            raise ValueError('Grant upload, channel read and analytics permissions to connect')
        r = client.get('https://www.googleapis.com/youtube/v3/channels',
                       params={'part': 'snippet', 'mine': 'true'},
                       headers={'Authorization': 'Bearer ' + tokens['access_token']})
        if r.status_code != 200:
            raise ValueError('Cannot read YouTube channel; enable YouTube Data API v3 and reconnect')
        channels = r.json().get('items', [])
        if len(channels) != 1:
            raise ValueError('Select a Google/Brand account with exactly one YouTube channel')
        channel = channels[0]
    with session_scope() as s:
        row = s.get(SocialConnection, slug)
        if not row:
            row = SocialConnection(channel_slug=slug)
            s.add(row)
        row.refresh_token_encrypted = cipher().encrypt(tokens['refresh_token'].encode()).decode()
        row.remote_channel_id = channel['id']
        row.remote_channel_name = channel['snippet']['title']
    return slug, channel['snippet']['title']
