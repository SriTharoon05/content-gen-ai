"""First-boot seeding: channels into the database, settings row created from defaults."""
from .channels import CHANNELS
from .db import session_scope
from .models import Channel
from .settings_store import load


def seed_all() -> None:
    with session_scope() as session:
        for channel in CHANNELS:
            existing = session.get(Channel, channel["slug"])
            if existing:
                # Preserve operator-owned names, niches and instructions across restarts.
                merged = {**channel["strategy_json"], **(existing.strategy_json or {})}
                existing.strategy_json = merged
            else:
                session.add(Channel(**channel, enabled=True, overrides_json={}))
    load(force=True)
