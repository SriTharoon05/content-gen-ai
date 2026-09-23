"""Isolated Cloudflare transport; all media algorithms remain in backend/app.

No provider generation, publishing, or database access is allowed in this process.
"""
import hashlib
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
os.environ['DATABASE_URL'] = 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused'
os.environ['RENDER_EXECUTION_BACKEND'] = 'local'
os.environ.setdefault('RENDER_PROFILE', 'auto')


def execute_media(manifest, root, output):
    if manifest.get('version') != 1:
        raise ValueError('Unsupported manifest version')
    if manifest['operation'] == 'prepare_audio':
        from app.audio import pace_and_trim, probe_duration
        from app.settings_store import render_settings
        rate = float(manifest.get('playback_rate', 1.0))
        if not .75 <= rate <= 1.25:
            raise ValueError('Playback rate outside supported range')
        source = root / 'source.audio'
        parts = manifest.get('sources', ['source.audio'])
        if len(parts) > 1:
            import wave
            source = root / 'joined.wav'
            shape = None
            with wave.open(str(source), 'wb') as joined:
                for name in parts:
                    part = (root / name).resolve()
                    if not part.is_relative_to(root.resolve()):
                        raise ValueError('Unsafe audio source')
                    with wave.open(str(part),'rb') as audio:
                        params = (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())
                        if shape is None:
                            shape=params
                            joined.setnchannels(shape[0]); joined.setsampwidth(shape[1]); joined.setframerate(shape[2])
                        if shape!=params: raise ValueError('Incompatible narration chunks')
                        joined.writeframes(audio.readframes(audio.getnframes()))
        with render_settings(manifest['settings']):
            seconds = pace_and_trim(source, output, rate, 120, 250)
        measured = probe_duration(output)
        if seconds <= 0 or abs(measured-seconds) > .001:
            raise ValueError('Prepared audio duration is invalid')
        return {'duration': seconds, 'bytes': output.stat().st_size}
    if manifest['operation'] == 'assemble_script':
        manifest = prepare_script_bundle(manifest, root)
    from app.render_bundle import execute
    return execute(manifest, root, output)


def prepare_script_bundle(manifest, root):
    """Canonical caption/timing algorithms, with precomputed ASR; no provider or DB calls."""
    from dataclasses import asdict
    from app.schemas import Script, EditPlan
    from app.settings_store import render_settings
    from app.audio import probe_duration, speech_window
    from app.alignment import _match, monotonic, measured_captions, normalized
    from app.captions import beat_spans, analyze_audio, write_ass, timing_report
    from app.media import build_timeline
    script=Script.model_validate(manifest['script'])
    plan=EditPlan.model_validate({'transitions':manifest['transitions']})
    with render_settings(manifest['settings']):
        audio=root/'narration.wav'
        total=probe_duration(audio)
        if not 45<=total<=90: raise ValueError('Fresh narration must be 45–90 seconds')
        heard=[dict(w) for w in manifest['words']]
        if not heard: raise ValueError('Measured ASR words required')
        first,last=speech_window(audio)
        heard[0]['start']=max(float(heard[0]['start']),first)
        heard[-1]['end']=min(float(heard[-1]['end']),last)
        reference,ratio=_match(' '.join(b.narration for b in script.beats).split(),heard,max(.5,total-.03))
        if ratio<float(manifest['settings']['align'].get('min_match_ratio',.55)):
            raise ValueError('Narration/script mismatch; refusing unrelated images or captions')
        reference=monotonic(reference,total)
        captions=measured_captions(heard,total)
        spans,kept=beat_spans(reference,[len(b.narration.split()) for b in script.beats],total)
        transitions=[plan.transitions[i].normalised().model_dump() for i in kept]
        timeline=build_timeline(spans,transitions,30,total)
        _,pitches=analyze_audio(audio,root)
        emphasis={normalized(w) for b in script.beats for w in b.emphasis_words}
        stats=write_ass(captions,pitches,emphasis,root/'captions.ass',total)
        timing_report(root/'caption-timing.json',{'reference':reference,'captions':captions,'match_ratio':ratio,
            'source':'groq:whisper-large-v3-turbo','caption_source':'groq:whisper-large-v3-turbo:verbatim',
            'heard_words':len(heard),'speech_window':[first,last]},spans,pitches,stats)
        return {**manifest,'operation':'assemble','images':[manifest['images'][i] for i in kept],
            'transitions':transitions,'timeline':asdict(timeline)}


def download_assets(manifest, root):
    for name, entry in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or '\\' in name or ':' in name:
            raise ValueError('Unsafe asset path')
        url = urlsplit(entry['url'])
        if url.scheme != 'https' or url.username or url.password:
            raise ValueError('Unsafe asset URL')
        path.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.sha256()
        with httpx.stream('GET', entry['url'], timeout=180, follow_redirects=False) as response:
            response.raise_for_status()
            with path.open('wb') as target:
                for chunk in response.iter_bytes():
                    target.write(chunk)
                    checksum.update(chunk)
        if checksum.hexdigest() != entry['sha256']:
            raise ValueError('Asset checksum mismatch')


def main():
    task = os.environ.get('RENDER_TASK_ID', '')
    if len(task) != 32 or any(c not in '0123456789abcdef' for c in task):
        raise ValueError('Invalid task ID')
    base = os.environ['CF_MEDIA_API_URL'].rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('Worker URL must use HTTPS')
    owner = secrets.token_urlsafe(32)
    headers = {'Authorization': 'Bearer ' + os.environ['CF_MEDIA_WORKER_TOKEN']}

    def api(action, **data):
        for attempt in range(3):
            try:
                r = httpx.post(f'{base}/internal/media-tasks/{task}/{action}',
                    headers=headers, json={'owner': owner, **data}, timeout=90)
                if r.status_code < 500 and r.status_code != 429:
                    r.raise_for_status()
                    return r.json()
            except (httpx.TimeoutException, httpx.NetworkError):
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)
        raise RuntimeError('Worker API unavailable')

    from app.db import engine
    from sqlalchemy import event
    @event.listens_for(engine, 'do_connect')
    def no_database(*args, **kwargs):
        raise RuntimeError('Media process must not connect to a database')

    claimed = api('claim')
    if not claimed.get('claimed'):
        print('Task already owned or completed: skipping duplicate.')
        return 0
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(60):
            try:
                api('heartbeat')
                print('Media processing active.', flush=True)
            except Exception:
                print('Heartbeat unavailable; completion will retry.', flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        manifest = claimed['manifest']
        with tempfile.TemporaryDirectory(prefix='cf-media-') as folder:
            root = Path(folder)
            download_assets(manifest, root)
            output = root / ('prepared.wav' if manifest['operation'] == 'prepare_audio' else 'final.mp4')
            from app.render_metrics import measure
            with measure() as metrics:
                info = execute_media(manifest, root, output)
            metrics.update(info)
            for attempt in range(3):
                capability = api('upload')
                try:
                    with output.open('rb') as source:
                        r = httpx.post(capability['url'], data=capability['fields'],
                            files={'file': (output.name, source, 'audio/wav' if output.suffix == '.wav' else 'video/mp4')}, timeout=300)
                    r.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
            api('complete', status='succeeded', metrics=metrics)
            print('Media succeeded:', metrics)
        return 0
    except Exception as error:
        try:
            api('complete', status='failed', metrics={})
        except Exception:
            print('Completion unavailable; durable deadline will resolve task.')
        detail = f'HTTP {error.response.status_code}' if isinstance(error,httpx.HTTPStatusError) else str(error)[:1500] if not isinstance(error,httpx.HTTPError) else 'Media network request failed'
        print('Media failed:', type(error).__name__, detail)
        return 1
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == '__main__':
    sys.exit(main())
