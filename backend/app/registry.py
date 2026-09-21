"""Asset library, within-video image reuse, story uniqueness and the categorised music library.

Reuse is deliberately narrow and deterministic:

  * within a single video, two shots whose visible tags match exactly share one image
  * images are never matched across different stories
  * no model call is spent judging candidates, so reuse costs zero tokens

Every image is still written to the library with full metadata and a content hash, so a cross-story
library can be built later without regenerating anything.
"""
import hashlib
import re
from pathlib import Path

from sqlalchemy import select

from .db import session_scope
from .models import MusicTrack, RegistryAsset, StoryHistory
from .settings_store import cfg

STOP = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "how", "why", "what", "is", "it", "that"}
CROSS_STORY_TYPES = {"object", "generic"}


def _path_available(value: str) -> bool:
    """Local paths are checked on disk; Supabase URLs are already durable references."""
    if not value:
        return False
    from . import storage

    return storage.is_supabase_url(value) or Path(value).exists()


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", (text or "").lower()) if w not in STOP and len(w) > 2}


def canonical(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (tag or "").lower()).strip("_")


# ----------------------------------------------------------------------------- story uniqueness


def prior_stories(channel_slug: str, limit: int = 60) -> list[dict]:
    cross_channel = bool(cfg("reuse", "cross_channel_dedup", default=False))
    with session_scope() as session:
        query = select(StoryHistory).order_by(StoryHistory.created_at.desc()).limit(limit)
        if not cross_channel:
            query = query.where(StoryHistory.channel_slug == channel_slug)
        return [
            {"video_id": r.video_id, "channel": r.channel_slug,
             "premise_summary": r.premise_summary, "theme_tags": r.theme_tags or []}
            for r in session.scalars(query).all()
        ]


def similarity(candidate: str, tags: list[str], prior: list[dict]) -> list[dict]:
    threshold = float(cfg("reuse", "similarity_threshold", default=0.18))
    base = tokens(candidate) | {t.lower() for t in (tags or [])}
    scored = []
    for row in prior:
        other = tokens(row["premise_summary"]) | {t.lower() for t in (row["theme_tags"] or [])}
        if not base or not other:
            continue
        score = len(base & other) / len(base | other)
        if score >= threshold:
            scored.append({**row, "overlap": round(score, 3)})
    return sorted(scored, key=lambda r: -r["overlap"])[:6]


# --------------------------------------------------------------------------------- asset library


def signature(subject_type: str, action_tag: str, setting_tag: str, character_refs: list[str]) -> str:
    """The exact visual identity of a shot. Equal signatures mean one image can serve both."""
    refs = "|".join(sorted(canonical(c) for c in (character_refs or [])))
    return f"{subject_type}/{canonical(action_tag)}/{canonical(setting_tag)}/{refs}"


def register_asset(
    video_id: str,
    channel_slug: str,
    shot_id: str,
    path: Path,
    prompt: str,
    subject_type: str,
    action_tag: str,
    setting_tag: str,
    character_refs: list[str],
) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    with session_scope() as session:
        asset = RegistryAsset(
            story_id=video_id,
            channel_slug=channel_slug,
            storage_path=str(path),
            subject_type=subject_type,
            action_tag=canonical(action_tag),
            setting_tag=canonical(setting_tag),
            character_refs=sorted({canonical(c) for c in (character_refs or [])}),
            signature=signature(subject_type, action_tag, setting_tag, character_refs),
            reusability_scope="story_only",
            prompt=prompt,
            sha256=digest,
            referenced_by=[{"video_id": video_id, "shot_id": shot_id}],
            use_count=1,
        )
        session.add(asset)
        session.flush()
        return asset.id


def reference_asset(asset_id: str, video_id: str, shot_id: str) -> None:
    with session_scope() as session:
        asset = session.get(RegistryAsset, asset_id)
        if not asset:
            return
        references = list(asset.referenced_by or [])
        entry = {"video_id": video_id, "shot_id": shot_id}
        if entry not in references:
            references.append(entry)
            asset.referenced_by = references
            asset.use_count = len(references)


def plan_within_video(video_id: str, shots: list[dict]) -> dict[str, str]:
    """Map each duplicate shot to the earlier shot_id that will carry its image. Deterministic, free."""
    if not cfg("reuse", "enabled", default=True):
        return {}
    seen: dict[str, str] = {}
    duplicates: dict[str, str] = {}
    for shot in shots:
        key = signature(shot["subject_type"], shot["action_tag"], shot["setting_tag"], shot["character_refs"])
        if key in seen:
            duplicates[shot["shot_id"]] = seen[key]
        else:
            seen[key] = shot["shot_id"]
    return duplicates


def find_in_story(video_id: str, subject_type: str, action_tag: str, setting_tag: str, refs: list[str]):
    """Existing assets from THIS video that match exactly. Never looks at other stories."""
    if not cfg("reuse", "enabled", default=True):
        return []
    target = signature(subject_type, action_tag, setting_tag, refs)
    with session_scope() as session:
        rows = session.scalars(
            select(RegistryAsset)
            .where(RegistryAsset.story_id == video_id, RegistryAsset.signature == target)
            .order_by(RegistryAsset.created_at.desc())
            .limit(5)
        ).all()
        return [
            {"asset_id": r.id, "storage_path": r.storage_path, "prompt": r.prompt[:400]}
            for r in rows
            if _path_available(r.storage_path)
        ]


def registry_stats() -> dict:
    from .settings_store import credits_per_image

    with session_scope() as session:
        assets = session.scalars(select(RegistryAsset)).all()
    reuses = sum(max(0, a.use_count - 1) for a in assets)
    per_image = credits_per_image()
    usd = per_image * float(cfg("pricing", "credit_price_usd", default=1.185))
    return {
        "assets": len(assets),
        "reuses": reuses,
        "credits_saved": round(reuses * per_image, 5),
        "usd_saved": round(reuses * usd, 4),
        "inr_saved": round(reuses * usd * float(cfg("pricing", "usd_to_inr", default=96.2)), 2),
    }


# ------------------------------------------------------------------------------- music library


def music_library(channel_slug: str | None = None, category: str | None = None) -> list[dict]:
    with session_scope() as session:
        tracks = session.scalars(select(MusicTrack).where(MusicTrack.archived.is_(False)).order_by(MusicTrack.created_at.desc())).all()
        out = []
        for track in tracks:
            if not track.rights_cleared or not _path_available(track.path):
                continue
            if channel_slug and track.channels and channel_slug not in track.channels:
                continue
            if category and track.category != category:
                continue
            out.append({
                "id": track.id, "name": track.name, "category": track.category, "mood": track.mood,
                "tempo": track.tempo, "duration_seconds": track.duration_seconds,
                "default_volume_pct": track.default_volume_pct, "channels": track.channels,
                "path": track.path, "notes": track.notes,
                "trim_start": track.trim_start, "trim_end": track.trim_end,
            })
        return out


def music_track(identifier: str | None) -> dict | None:
    if not identifier:
        return None
    with session_scope() as session:
        track = session.get(MusicTrack, identifier) or session.scalar(
            select(MusicTrack).where(MusicTrack.name == identifier)
        )
        if not track or track.archived or not track.rights_cleared or not _path_available(track.path):
            return None
        return {
            "id": track.id, "name": track.name, "path": track.path,
            "default_volume_pct": track.default_volume_pct,
            "trim_start": track.trim_start, "trim_end": track.trim_end,
            "duration_seconds": track.duration_seconds, "category": track.category,
        }


def intensity_from_pct(pct: int) -> float:
    ceiling = float(cfg("music", "max_intensity", default=0.25))
    return round(max(0, min(100, int(pct))) / 100.0 * ceiling, 5)
