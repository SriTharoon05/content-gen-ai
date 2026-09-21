"""Headless batch runner, for when you want to skip the dashboard.

    python scripts/run_batch.py                    # 2 per enabled channel
    python scripts/run_batch.py --per-channel 4
    python scripts/run_batch.py --channel lorehush --topic "a map with one impossible island"
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import boot  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Channel, Video  # noqa: E402
from app.pipeline import run  # noqa: E402
from app.providers.images import remaining_credits  # noqa: E402
from app.seed import seed_all  # noqa: E402
from app.settings_store import cfg  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("batch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-channel", type=int, default=2)
    parser.add_argument("--channel", default=None)
    parser.add_argument("--topic", default=None)
    args = parser.parse_args()

    init_db()
    seed_all()
    with session_scope() as session:
        rows = session.query(Channel).filter(Channel.enabled.is_(True)).all()
        slugs = [r.slug for r in rows if not args.channel or r.slug == args.channel]
    if not slugs:
        log.error("no enabled channel matched")
        return 2

    worst = len(slugs) * args.per_channel * int(cfg("video", "max_shots")) * float(cfg("pricing", "credits_per_image"))
    rate = float(cfg("pricing", "credit_price_usd")) * float(cfg("pricing", "usd_to_inr"))
    log.info("%d videos planned; worst case %.3f credits (~Rs %.0f); %.3f available",
             len(slugs) * args.per_channel, worst, worst * rate, remaining_credits())
    if worst > remaining_credits():
        log.error("not enough credits for the worst case; top up or lower --per-channel")
        return 3

    failures = 0
    for slug in slugs:
        for index in range(args.per_channel):
            with session_scope() as session:
                video = Video(channel_slug=slug, topic=args.topic or "")
                session.add(video)
                session.flush()
                video_id = video.id
            log.info("=== %s %d/%d (%s)", slug, index + 1, args.per_channel, video_id)
            try:
                log.info("done: %s", run(video_id))
            except Exception as error:  # noqa: BLE001
                failures += 1
                log.exception("failed: %s", error)
    log.info("complete with %d failure(s). Videos are in %s", failures, boot().outputs)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
