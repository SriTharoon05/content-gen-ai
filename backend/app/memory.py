"""Per-channel memory: durable notes fed back into every prompt for that channel.

Two sources:
  * operator notes written from the dashboard (pinned ones always survive trimming)
  * system notes the pipeline writes after each finished video, so the next run knows what already
    exists and what style landed
"""
from sqlalchemy import select

from .db import session_scope
from .models import ChannelMemory

MAX_IN_PROMPT = 14
KINDS = ("note", "style", "avoid", "character")


def add(channel_slug: str, content: str, kind: str = "note", pinned: bool = False, source: str = "operator") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    content = " ".join((content or "").split())
    if not content:
        raise ValueError("Memory content cannot be empty")
    with session_scope() as session:
        entry = ChannelMemory(
            channel_slug=channel_slug, content=content[:2000], kind=kind, pinned=pinned, source=source
        )
        session.add(entry)
        session.flush()
        return view(entry)


def view(entry: ChannelMemory) -> dict:
    return {
        "id": entry.id,
        "channel": entry.channel_slug,
        "kind": entry.kind,
        "content": entry.content,
        "pinned": entry.pinned,
        "source": entry.source,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def listing(channel_slug: str) -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(
            select(ChannelMemory)
            .where(ChannelMemory.channel_slug == channel_slug)
            .order_by(ChannelMemory.pinned.desc(), ChannelMemory.created_at.desc())
        ).all()
        return [view(row) for row in rows]


def remove(memory_id: str) -> bool:
    with session_scope() as session:
        entry = session.get(ChannelMemory, memory_id)
        if not entry:
            return False
        session.delete(entry)
        return True


def prompt_block(channel_slug: str) -> str:
    """The slice of memory that actually goes into a prompt. Pinned first, then most recent."""
    entries = listing(channel_slug)[:MAX_IN_PROMPT]
    if not entries:
        return ""
    lines = [f"- [{e['kind']}] {e['content']}" for e in entries]
    return "CHANNEL MEMORY (carry these forward; they came from earlier runs and the operator):\n" + "\n".join(lines)


def remember_video(channel_slug: str, premise_summary: str, title: str, archetype: str) -> None:
    add(
        channel_slug,
        f"Already published: \"{title}\" — {archetype} — {premise_summary[:220]}",
        kind="avoid",
        source="system",
    )
