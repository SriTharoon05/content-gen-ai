"""Same content/audio/edit, separate image-model comparison outputs."""
import copy
import re
import shutil

from sqlalchemy import select

from .db import session_scope
from .models import Video
from .settings_store import cfg


def create(source_id, model, batch=''):
    allowed = {row['id'] for row in cfg('models', 'image_catalog')}
    if model not in allowed:
        raise ValueError('Choose a configured image model')
    with session_scope() as session:
        source = session.get(Video, source_id, with_for_update=True)
        if not source or source.state not in ('READY', 'AWAITING_APPROVAL'):
            raise ValueError('Source video must finish before making image comparisons')
        existing = session.scalars(select(Video).where(Video.parent_id == source_id)).all()
        for row in existing:
            if (row.options_json or {}).get('comparison_model') == model and (row.options_json or {}).get('comparison_batch') == batch:
                return row.id
        row = Video(channel_slug=source.channel_slug, parent_id=source_id, language=source.language,
            topic=source.topic, title=re.sub(r'\s*\[[^\]]+\]$', '', source.title or '') + f" [{model.split('/')[-1]}]", description=source.description,
            instagram_caption=source.instagram_caption, hashtags=source.hashtags,
            premise_json=copy.deepcopy(source.premise_json), script_json=copy.deepcopy(source.script_json),
            qa_json=copy.deepcopy(source.qa_json), visual_json=copy.deepcopy(source.visual_json),
            voice_json=copy.deepcopy(source.voice_json), made_for_kids=source.made_for_kids,
            options_json={**copy.deepcopy(source.options_json or {}), 'media_manifest':{},
                'languages':[], 'image_model':model, 'comparison_model':model, 'comparison_batch':batch,
                'comparison_source':source_id, 'auto_publish':False}, state='QUEUED')
        session.add(row)
        session.flush()
        return row.id


def produce(video_id):
    from . import pipeline, storage, regenerate
    from .schemas import Script
    with session_scope() as session:
        row = session.get(Video, video_id)
        options = dict(row.options_json)
        parent_id = row.parent_id
        source = session.get(Video, parent_id)
        spec = copy.deepcopy(source.spec_json or {})
        script = Script.model_validate(row.script_json)
        channel = pipeline.channel_dict(row.channel_slug)
        visuals = copy.deepcopy(row.visual_json)
    storage.restore(parent_id)
    source_root, root = pipeline.work_dir(parent_id), pipeline.work_dir(video_id)
    for path in source_root.iterdir():
        if path.is_file() and path.suffix in ('.json', '.wav', '.md', '.ass') and not path.name.startswith('align-'):
            target = root / path.name
            if not target.exists():
                shutil.copy2(path, target)
    pipeline.stage_images(video_id, channel, visuals)
    result = regenerate._rebuild(video_id, channel, spec, options, script)
    return {**result, 'source_video':parent_id, 'image_model':options['image_model']}
