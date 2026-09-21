"""Reusable music crops and video-only-preserving soundtrack revisions."""
from contextlib import contextmanager
from pathlib import Path
import math
import tempfile
import uuid

import httpx

from . import media, registry, storage
from .config import boot
from .db import session_scope
from .models import Video


def crop_bounds(duration, start=0, end=0):
    start, end = float(start), float(end or duration)
    if not all(math.isfinite(n) for n in (duration, start, end)) or start < 0 or end > duration + .05 or end - start < 3:
        raise ValueError('Choose a crop of at least 3 seconds within the audio duration')
    return start, min(end, duration)


def fetch_media(source, target):
    if storage.is_supabase_url(source):
        with httpx.stream('GET', source, timeout=180) as response:
            response.raise_for_status()
            with target.open('wb') as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return target
    path = Path(source)
    if not path.is_file():
        raise ValueError('Saved media file is unavailable')
    return path


@contextmanager
def prepared_music(track, start=None, end=None):
    if not track:
        yield None
        return
    start, end = crop_bounds(track['duration_seconds'],
        track.get('trim_start', 0) if start is None else start,
        track.get('trim_end', 0) if end is None else end)
    with tempfile.TemporaryDirectory(prefix='story-bgm-') as folder:
        root = Path(folder)
        source = fetch_media(track['path'], root / 'source.audio')
        cropped = root / 'crop.wav'
        media.run([media.binary('ffmpeg'), '-y', '-v', 'error', '-ss', str(start), '-i', str(source),
            '-t', str(end-start), '-vn', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(cropped)])
        yield cropped


def apply(video_id, payload):
    from .pipeline import work_dir
    with session_scope() as s:
        v = s.get(Video, video_id)
        source, duration, slug = v.output_path, v.duration_seconds, v.channel_slug
        previous = dict(v.spec_json or {})
    narration = work_dir(video_id) / 'narration.wav'
    if not narration.exists():
        raise ValueError('Saved clean narration is missing; cannot safely replace background music')
    track = registry.music_track(payload.get('music_track'))
    if payload.get('music_track') and not track:
        raise ValueError('Selected music was removed or is unavailable')
    volume = int(payload.get('music_volume_pct', 30))
    intensity = registry.intensity_from_pct(volume) if track else 0
    ducking = bool(payload.get('ducking', True))
    start = payload.get('music_start_seconds')
    end = payload.get('music_end_seconds')
    filename = f'{video_id[:8]}-bgm-{uuid.uuid4().hex[:10]}.mp4'
    with tempfile.TemporaryDirectory(prefix='story-remix-') as folder:
        root = Path(folder)
        original = fetch_media(source, root / 'source.mp4')
        output = root / filename
        with prepared_music(track, start, end) as music:
            remix(original, narration, music, output, duration, intensity, ducking)
        if storage.enabled():
            result = storage.upload(storage.VIDEOS_BUCKET, f'{slug}/{filename}', output)
        else:
            target = boot().outputs / filename
            import shutil
            shutil.copy2(output, target)
            result = str(target)
    with session_scope() as s:
        v = s.get(Video, video_id, with_for_update=True)
        opts = dict(v.options_json or {})
        history = list(opts.get('music_revisions', []))
        history.append({'output': source, 'music': {k: previous.get(k) for k in
            ('music_track','music_name','music_volume_pct','music_start_seconds','music_end_seconds','ducking')}})
        selection = {'music_track': (track or {}).get('id'), 'music_name': (track or {}).get('name'),
            'music_volume_pct': volume, 'music_intensity': intensity, 'ducking': ducking,
            'music_start_seconds': float(start if start is not None else (track or {}).get('trim_start',0)),
            'music_end_seconds': float(end if end is not None else (track or {}).get('trim_end',0))}
        v.spec_json = {**previous, **selection}
        v.options_json = {**opts, **selection, 'music_enabled': bool(track), 'music_revisions':history,
            'force_review':True, 'preview_required':False, 'reviewed_output':'', 'auto_publish':False}
        v.output_path = result
        v.approved = False
        v.state = 'AWAITING_APPROVAL'
        v.progress = 99
        v.stage_detail = 'Soundtrack applied. Ready to publish; replay is optional.'
        v.error = ''
    return {'output':result, 'state':'AWAITING_APPROVAL', 'images_added':0, 'provider_calls':0}


def remix(original, narration, music, output, duration, intensity, ducking):
    args = [media.binary('ffmpeg'), '-y','-v','error','-filter_complex_threads','1',
            '-i',str(original),'-i',str(narration)]
    filters = [f'[1:a]aresample=48000:first_pts=0,apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[voice]']
    if music and intensity > 0:
        args += ['-stream_loop','-1','-i',str(music)]
        filters += [f'[2:a]aresample=48000,volume={intensity:.5f},atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[bed]']
        if ducking:
            filters += ['[voice]asplit=2[main][side]', '[bed][side]sidechaincompress=threshold=0.025:ratio=8:attack=10:release=250[duck]',
                        '[main][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[mix]']
        else:
            filters += ['[voice][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[mix]']
    else:
        filters += ['[voice]alimiter=limit=0.95:latency=1[mix]']
    media.run(args + ['-filter_complex',';'.join(filters),'-map','0:v:0','-map','[mix]',
        '-c:v','copy','-c:a','aac','-b:a','160k','-ar','48000','-t',str(duration),'-movflags','+faststart',str(output)])
    media.validate(output, duration)
