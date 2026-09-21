"""Explicit, restartable operator comparison batch; never publishes or processes unrelated jobs."""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select, or_
from app.db import session_scope
from app.models import Video, Job
from app.worker import process, queue_comparisons
from app.comparisons import create

BATCH = 'image-comparison-2026-09-20'
FLUX = 'black-forest-labs/flux.1-schnell'
DREAM = 'lykon/dreamshaper-8-lcm'


def execute(video_id, stage):
    with session_scope() as session:
        video = session.get(Video, video_id)
        if video.state == 'READY':
            return True
        jobs = session.scalars(select(Job).where(Job.video_id == video_id, Job.stage == stage).order_by(Job.created_at.desc())).all()
        if jobs and jobs[0].status == 'running':
            raise RuntimeError('This batch already has a running job; do not run concurrent copies')
        job = Job(video_id=video_id, stage=stage, status='running', attempts=1, max_attempts=1)
        session.add(job)
        session.flush()
        jid = job.id
        video.state='RUNNING'
    print('START', video_id, stage, flush=True)
    process(jid)
    with session_scope() as session:
        job = session.get(Job,jid)
        video = session.get(Video,video_id)
        print('RESULT', video.channel_slug, video.id, job.status, video.duration_seconds, video.image_count,
              video.output_path or job.error[:500], flush=True)
        return job.status == 'done'


def source(slug):
    if slug == 'lorehush':
        return 'd6816eddba9a4e949fb0a5950a0bf308'
    with session_scope() as session:
        rows=session.scalars(select(Video).where(Video.channel_slug==slug,or_(Video.parent_id.is_(None),Video.parent_id==''))).all()
        for row in rows:
            if (row.options_json or {}).get('comparison_batch') == BATCH:
                return row.id
        row=Video(channel_slug=slug, language='en', options_json={
            'comparison_batch':BATCH, 'image_model':FLUX, 'min_shots':20, 'max_shots':20,
            'target_seconds':55, 'speech_tempo':1.0, 'languages':[], 'primary_language':'en',
            'auto_publish':False, 'pace_note':'Warm, relaxed, natural pauses; never rush.',
            'style_note':'Natural conversational pacing; preserve clarity.'})
        session.add(row)
        session.flush()
        return row.id


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--queue', action='store_true', help='Persist work and return; the background worker renders it')
    parser.add_argument('--channels', nargs='+', default=['lorehush','talemorrow','nowsift','curionerve','neurascify'])
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    for slug in args.channels:
        sid=source(slug)
        if args.queue:
            models=[FLUX,DREAM] if slug=='lorehush' else [DREAM]
            payload={'comparison_models':models, 'comparison_batch':BATCH}
            with session_scope() as session:
                video=session.get(Video,sid,with_for_update=True)
                ready=video.state in ('READY','AWAITING_APPROVAL')
                if not ready:
                    active=session.scalars(select(Job).where(Job.video_id==sid,
                        Job.stage=='produce_video',Job.status.in_(['queued','running']))).first()
                    if active:
                        active.payload_json={**(active.payload_json or {}),**payload}
                    else:
                        session.add(Job(video_id=sid,stage='produce_video',payload_json=payload,max_attempts=2))
                        video.state='QUEUED'
                        video.error=''
            if ready:
                queue_comparisons(sid,payload)
            print('QUEUED COMPARISON PAIR',slug,sid,flush=True)
            continue
        if not execute(sid,'produce_video'):
            continue
        models=[FLUX,DREAM] if slug=='lorehush' else [DREAM]
        for model in models:
            vid=create(sid,model,BATCH)
            execute(vid,'image_variant')


if __name__=='__main__':
    main()
