"""Permanent pgvector concept memory; selection is isolated in story_selection.py.

An entity lock makes collision check plus commit atomic. A video lock prevents duplicate
commitments on concurrent retries. Downstream failure never discards a committed concept.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from google import genai
from sqlalchemy import select, text

from .db import session_scope
from .key_pool import is_auth_error, is_rate_limited, pool
from .llm import generate_model
from .models import ContentLedger, Video
from .schemas import UniqueConcept, UniqueConceptSet
from .settings_store import cfg

log = logging.getLogger("content_ledger")

EMBEDDING_DIMENSIONS = 768
WILDCARD_ANGLES = (
    "contrarian_take",
    "myth_busting",
    "historical_deep_dive",
    "hidden_mechanism",
    "unexpected_comparison",
    "common_mistake",
    "forgotten_origin",
    "what_changed",
)

COLLISION_SQL = text(
    """
    WITH entity_baseline AS (
        SELECT 1 - (a.embedding <=> b.embedding) AS sim
        FROM content_ledger a
        JOIN content_ledger b
          ON a.core_entity = b.core_entity AND a.id < b.id
        WHERE a.core_entity = :entity
          AND a.status = 'active'
          AND b.status = 'active'
    ),
    baseline_metrics AS (
        SELECT
            percentile_cont(0.90) WITHIN GROUP (ORDER BY sim) AS local_90th_percentile,
            COUNT(*) AS pair_count
        FROM entity_baseline
    ),
    candidate_metrics AS (
        SELECT MAX(1 - (embedding <=> CAST(:embedding AS vector))) AS nearest_neighbor_sim
        FROM content_ledger
        WHERE core_entity = :entity
          AND status = 'active'
    )
    SELECT
        COALESCE(nearest_neighbor_sim, 0) AS nearest_neighbor_sim,
        CASE WHEN pair_count < 10 THEN 0.85 ELSE local_90th_percentile END AS applied_threshold,
        COALESCE(nearest_neighbor_sim, 0) > COALESCE(
            CASE WHEN pair_count < 10 THEN 0.85 ELSE local_90th_percentile END, 0.85
        ) AS is_collision
    FROM candidate_metrics, baseline_metrics
    """
)


def _normalise(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def _pair_key(entity: str, angle: str) -> tuple[str, str]:
    return _normalise(entity), _normalise(angle)


def _vector_literal(values: list[float]) -> str:
    if len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"Expected {EMBEDDING_DIMENSIONS}-dimensional embedding, got {len(values)}")
    return "[" + ",".join(f"{float(value):.9g}" for value in values) + "]"


def recent_forbidden_pairs(days: int = 180) -> list[dict[str, str]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as session:
        rows = session.execute(
            select(ContentLedger.core_entity, ContentLedger.content_angle)
            .where(
                ContentLedger.created_at >= cutoff,
                ContentLedger.status == "active",
            )
            .order_by(ContentLedger.created_at.desc())
        ).all()
    # SQLAlchemy returns row tuples for a multi-column select.
    return [{"core_entity": row[0], "content_angle": row[1]} for row in rows]




def embed_text(value: str) -> list[float]:
    """Embed with the requested model, falling back when Google retires an embedding model."""
    models = [
        cfg("models", "embedding_model", default="text-embedding-004"),
        cfg("models", "embedding_fallback", default="gemini-embedding-001"),
    ]
    models = list(dict.fromkeys(model for model in models if model))
    keys = pool("text")
    attempts = max(3, int(cfg("runtime", "provider_retries", default=4)))
    last_error: Exception | None = None
    for model in models:
        for attempt in range(attempts):
            index, api_key = keys.acquire()
            client = None
            try:
                client = genai.Client(api_key=api_key)
                try:
                    from google.genai import types

                    response = client.models.embed_content(
                        model=model,
                        contents=value,
                        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
                    )
                except (AttributeError, TypeError):
                    # Older google-genai releases expose embed_content without the config type.
                    response = client.models.embed_content(model=model, contents=value)
                embeddings = getattr(response, "embeddings", None) or []
                first = embeddings[0] if embeddings else None
                values = getattr(first, "values", None) if first is not None else None
                if values is None and isinstance(first, dict):
                    values = first.get("values")
                vector = [float(item) for item in (values or [])]
                if len(vector) != EMBEDDING_DIMENSIONS:
                    raise RuntimeError(f"Embedding model returned {len(vector)} dimensions")
                return vector
            except Exception as error:  # noqa: BLE001
                last_error = error
                message = str(error).lower()
                if is_auth_error(error):
                    keys.mark_dead(index)
                    continue
                if "404" in message or "not found" in message or "not supported" in message:
                    # A model retirement is not transient; try the configured compatibility model.
                    break
                if is_rate_limited(error):
                    keys.penalise(index)
                time.sleep(min(12.0, 1.5 * (attempt + 1)))
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
    raise RuntimeError(f"Embedding generation failed for models {models}: {last_error}")


def _try_reserve(video_id: str, candidate: UniqueConcept, embedding: list[float], academic_term: str | None = None) -> dict | None:
    entity = _normalise(candidate.core_entity)
    angle = _normalise(candidate.content_angle)
    concept = " ".join(candidate.core_concept.split())
    vector = _vector_literal(embedding)
    with session_scope() as session:
        if video_id:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"concept-video:{video_id}"})
            existing = session.scalar(select(ContentLedger).where(ContentLedger.video_id == video_id, ContentLedger.status == "active").limit(1))
            if existing:
                return {"collision": False, "id": str(existing.id), "core_entity": existing.core_entity,
                        "content_angle": existing.content_angle, "core_concept": existing.core_concept}
        # Advisory locks are transaction-scoped and make check + insert one atomic decision per entity.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                        {"lock_key": f"story-shorts:content:{entity}"})
        exact = session.scalar(select(ContentLedger.id).where(ContentLedger.core_entity == entity,
            ContentLedger.content_angle == angle, ContentLedger.status == "active").limit(1))
        if exact:
            return {"collision": True, "reason": "exact_pair"}
        result = session.execute(COLLISION_SQL, {"entity": entity, "embedding": vector}).mappings().one()
        if bool(result["is_collision"]):
            return {
                "collision": True,
                "nearest_neighbor_sim": float(result["nearest_neighbor_sim"] or 0),
                "applied_threshold": float(result["applied_threshold"] or 0.85),
            }
        row = ContentLedger(
            video_id=video_id,
            core_entity=entity,
            content_angle=angle,
            core_concept=concept,
            status="active",
            academic_term=academic_term,
            embedding=embedding,
        )
        session.add(row)
        session.flush()
        return {
            "collision": False,
            "id": str(row.id),
            "core_entity": entity,
            "content_angle": angle,
            "core_concept": concept,
            "nearest_neighbor_sim": float(result["nearest_neighbor_sim"] or 0),
            "applied_threshold": float(result["applied_threshold"] or 0.85),
        }


def _remember_reservation(video_id: str, reservation: dict) -> None:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise ValueError(f"Video {video_id} disappeared while reserving content")
        options = dict(video.options_json or {})
        options["content_ledger_id"] = reservation["id"]
        options["content_entity"] = reservation["core_entity"]
        options["content_angle"] = reservation["content_angle"]
        options["content_concept"] = reservation["core_concept"]
        video.options_json = options


def _existing_reservation(video_id: str) -> dict | None:
    with session_scope() as session:
        row = session.scalar(
            select(ContentLedger).where(
                ContentLedger.video_id == video_id,
                ContentLedger.status == "active",
            ).order_by(ContentLedger.created_at.desc())
        )
        if not row:
            return None
        return {
            "id": str(row.id), "core_entity": row.core_entity, "content_angle": row.content_angle,
            "core_concept": row.core_concept, "academic_term": row.academic_term,
        }


def reserve_for_video(video_id: str, channel: dict, topic: str, options: dict | None = None) -> dict:
    """Return a reserved unique concept, reusing an in-flight reservation on a worker retry."""
    existing = _existing_reservation(video_id)
    if existing:
        return existing

    from .story_selection import select_unique_concept
    reservation = select_unique_concept(channel, video_id=video_id, topic=topic, options=options)
    _remember_reservation(video_id, reservation)
    return reservation




def finalize_for_video(video_id: str, success: bool) -> None:
    # Concept commitment is permanent, independent of downstream generation failures.
    # Only an explicit operator discard may remove it from uniqueness checks.
    return None
