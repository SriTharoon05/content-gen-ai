"""Upload existing local media before deployment; retain all local originals."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select
from app.db import init_db, session_scope
from app.models import Video, MusicTrack
from app import storage

if __name__=='__main__':
    init_db()
    if not storage.enabled():raise RuntimeError('Supabase Storage credentials required')
    storage.ensure_buckets()
    with session_scope() as s:
        videos=s.scalars(select(Video)).all()
        music=s.scalars(select(MusicTrack)).all()
    for v in videos:
        storage.checkpoint(v.id)
        if v.output_path and not storage.is_supabase_url(v.output_path) and Path(v.output_path).is_file():
            url=storage.upload(storage.VIDEOS_BUCKET,f'{v.channel_slug}/{v.id}.mp4',Path(v.output_path),delete_local=False)
            with session_scope() as s:s.get(Video,v.id).output_path=url
        print('checked video',v.id,flush=True)
    for track in music:
        if not storage.is_supabase_url(track.path) and Path(track.path).is_file():
            url=storage.upload(storage.MUSIC_BUCKET,f'{track.id}/{Path(track.path).name}',Path(track.path),delete_local=False)
            with session_scope() as s:s.get(MusicTrack,track.id).path=url
    print('Migration complete; local originals retained.')
