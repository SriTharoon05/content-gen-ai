"""Wall-clock generation timing, separate from playback and publishing duration."""
from datetime import datetime, timezone


def generation_timing(video, jobs, now=None):
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    relevant = sorted(
        (j for j in jobs if j.stage in ('produce_video', 'cf_generation')),
        key=lambda j: utc(j.created_at),
    )
    if not relevant:
        return None  # Do not guess from video.updated_at: edits/publishing change it.
    job = relevant[0]  # Later re-renders must not change the original generation time.
    status = ('complete' if job.status in ('done', 'cf_done') else
              'failed' if job.status in ('failed', 'cf_failed') else 'running')
    start = utc(video.created_at)
    end = utc(job.updated_at) if status != 'running' else None
    return {'status': status, 'started_at': start.isoformat(),
            'finished_at': end.isoformat() if end else None,
            'elapsed_seconds': max(0, ((end or now or datetime.now(timezone.utc)) - start).total_seconds())}
