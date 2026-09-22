"""Media-only execution contract. Never import pipeline, generation or publishing here."""
from pathlib import Path
from . import media
from .settings_store import render_settings


def execute(manifest, root: Path, output: Path):
    if manifest.get('version') != 1:
        raise ValueError('Unsupported render manifest version')
    from .music_edit import prepared_music, remix
    track = manifest.get('music')
    if track:
        track = {**track, 'path':str(root / 'music.audio')}
    with render_settings(manifest['settings']):
        with prepared_music(track, manifest.get('music_start'), manifest.get('music_end')) as music:
            if manifest['operation'] == 'assemble':
                timeline = media.Timeline(**manifest['timeline'])
                media.assemble([root / name for name in manifest['images']], root / 'narration.wav',
                    timeline, manifest['transitions'], root / 'captions.ass', output, root / 'clips', music,
                    manifest['intensity'], manifest['ducking'], 0)
                expected = timeline.duration
            elif manifest['operation'] == 'remix':
                expected = manifest['duration']
                remix(root / 'source.mp4', root / 'narration.wav', music, output, expected,
                      manifest['intensity'], manifest['ducking'])
            else:
                raise ValueError('Unsupported render operation')
        info = media.probe(output)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        fps = int(manifest['settings']['video']['fps'])
        if (video['width'],video['height']) != (manifest['settings']['video']['width'],manifest['settings']['video']['height']):
            raise ValueError('Output dimensions differ from render snapshot')
        if video['avg_frame_rate'] != f'{fps}/1' or video['codec_name'] != 'h264':
            raise ValueError('Output FPS/codec differs from render snapshot')
        if abs(float(video['duration']) - expected) > 1/fps + .001:
            raise ValueError('Output frame timing differs from narration')
        return {'duration':float(video['duration']), 'fps':fps, 'bytes':output.stat().st_size,
                'frames':int(video.get('nb_frames',0))}
