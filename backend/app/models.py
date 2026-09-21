"""Persistence: channels, videos, the asset registry, memory, settings, jobs and the spend ledger."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Channel(Base):
    __tablename__ = "channels"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    tagline: Mapped[str] = mapped_column(String(255), default="")
    niche: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    strategy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    overrides_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SocialConnection(Base):
    __tablename__ = "social_connections"
    channel_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    remote_channel_id: Mapped[str] = mapped_column(String(128))
    remote_channel_name: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_slug: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128), default="")
    phase: Mapped[str] = mapped_column(String(16), default="ticket")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    channel_slug: Mapped[str] = mapped_column(ForeignKey("channels.slug"), index=True)
    parent_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    language: Mapped[str] = mapped_column(String(8), default="en", index=True)
    state: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    stage_detail: Mapped[str] = mapped_column(String(160), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    topic: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    instagram_caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    made_for_kids: Mapped[bool] = mapped_column(Boolean, default=False)
    premise_json: Mapped[dict] = mapped_column(JSON, default=dict)
    script_json: Mapped[dict] = mapped_column(JSON, default=dict)
    visual_json: Mapped[dict] = mapped_column(JSON, default=dict)
    spec_json: Mapped[dict] = mapped_column(JSON, default=dict)
    qa_json: Mapped[dict] = mapped_column(JSON, default=dict)
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    narration_path: Mapped[str] = mapped_column(Text, default="")
    # NOTE: when Supabase Storage is enabled, output_path stores a full https:// URL.
    # When running locally without Supabase, it stores a local filesystem path as before.
    output_path: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    narration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    reused_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_spent: Mapped[float] = mapped_column(Float, default=0.0)
    text_tokens: Mapped[int] = mapped_column(Integer, default=0)
    voice_json: Mapped[dict] = mapped_column(JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RegistryAsset(Base):
    """Every generated image lands here, tagged by what is VISIBLE in frame, not by story reason."""

    __tablename__ = "registry_assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    story_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    channel_slug: Mapped[str] = mapped_column(String(64), default="", index=True)
    storage_path: Mapped[str] = mapped_column(Text, default="")
    subject_type: Mapped[str] = mapped_column(String(16), default="generic", index=True)
    action_tag: Mapped[str] = mapped_column(String(120), default="", index=True)
    setting_tag: Mapped[str] = mapped_column(String(120), default="", index=True)
    character_refs: Mapped[list] = mapped_column(JSON, default=list)
    signature: Mapped[str] = mapped_column(String(255), default="", index=True)
    reusability_scope: Mapped[str] = mapped_column(String(20), default="story_only", index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    referenced_by: Mapped[list] = mapped_column(JSON, default=list)
    use_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Asset(Base):
    """Per-video media rows: images used in this cut, plus narration and music."""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    shot_id: Mapped[str] = mapped_column(String(32), default="")
    kind: Mapped[str] = mapped_column(String(16), default="image")
    path: Mapped[str] = mapped_column(Text, default="")
    registry_id: Mapped[str] = mapped_column(String(64), default="")
    reused: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MusicTrack(Base):
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    __tablename__ = "music_tracks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    filename: Mapped[str] = mapped_column(Text)
    # NOTE: when Supabase Storage is enabled, path stores a full https:// URL.
    # When running locally without Supabase, it stores a local filesystem path as before.
    path: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(48), default="neutral", index=True)
    mood: Mapped[str] = mapped_column(String(160), default="")
    tempo: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    trim_start: Mapped[float] = mapped_column(Float, default=0.0)
    trim_end: Mapped[float] = mapped_column(Float, default=0.0)
    default_volume_pct: Mapped[int] = mapped_column(Integer, default=30)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    rights_cleared: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProviderCall(Base):
    """One row per paid call. The idempotency key is what stops a retry from billing twice."""

    __tablename__ = "provider_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default="reserved", index=True)
    credits: Mapped[float] = mapped_column(Float, default=0.0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    units: Mapped[float] = mapped_column(Float, default=0.0)
    detail_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(48), default="produce_video")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(48))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StoryHistory(Base):
    __tablename__ = "story_history"

    video_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_slug: Mapped[str] = mapped_column(String(64), index=True)
    premise_summary: Mapped[str] = mapped_column(Text, default="")
    theme_tags: Mapped[list] = mapped_column(JSON, default=list)
    plot_beat_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContentLedger(Base):
    """Reserved/published concepts used to prevent recent semantic collisions.

    The embedding is deliberately kept in Postgres via pgvector. The prompt-facing hard exclusion
    is the exact (entity, angle) pair; the vector check catches a concept that is worded differently
    but is still materially the same story.
    """

    __tablename__ = "content_ledger"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'discarded')", name="content_ledger_status_ck"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    video_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    core_entity: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    content_angle: Mapped[str] = mapped_column(Text, nullable=False)
    core_concept: Mapped[str] = mapped_column(Text, nullable=False)
    academic_term: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)


class ScheduleRun(Base):
    """Persistent idempotency record for one local calendar day's scheduled batch."""

    __tablename__ = "schedule_runs"

    run_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="started", nullable=False, index=True)
    queued: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KnowledgeFrontier(Base):
    __tablename__ = "knowledge_frontier"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel_slug: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_frontier.id", ondelete="CASCADE"), nullable=True)
    topic_name: Mapped[str] = mapped_column(Text)
    academic_term: Mapped[str | None] = mapped_column(Text, nullable=True)
    depth_level: Mapped[int] = mapped_column(Integer, default=0)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    is_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VerificationBacklog(Base):
    __tablename__ = "verification_backlog"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel_slug: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    proposed_term: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ChannelMemory(Base):
    """Durable per-channel notes fed back into prompts: style rules, recurring cast, what to avoid."""

    __tablename__ = "channel_memory"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    channel_slug: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="note")
    content: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("video_id", "platform"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    remote_id: Mapped[str] = mapped_column(Text, default="")
    session_url: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    requested_privacy: Mapped[str] = mapped_column(String(16), default="private")
    actual_privacy: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
