"""SQLAlchemy engine/session — Supabase Postgres only, no SQLite fallback."""
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import settings

# pool_recycle keeps connections from going stale behind Supabase's pgbouncer pooler;
# pool_pre_ping catches any that die anyway before they reach a query.
# Supabase transaction pooling cannot preserve connection-local prepared statements.
engine = create_engine(settings().database_url, pool_pre_ping=True, pool_recycle=300,
                       connect_args={"prepare_threshold": None}, future=True)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope():
    """Commit on success, roll back on failure. Every writer uses this."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401

    # pgvector is a required production dependency for the content-uniqueness ledger. Fail fast
    # with the real database error instead of silently running without collision protection.
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    models.Base.metadata.create_all(engine)
    # OAuth secrets must never be exposed through Supabase's browser-facing API,
    # including on a local-development backend pointed at the real database.
    with engine.begin() as connection:
        for table in ('social_connections', 'oauth_attempts', 'meta_connections', 'render_tasks'):
            connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
            connection.execute(text(f'REVOKE ALL ON TABLE {table} FROM anon, authenticated'))
    if settings().app_env == "production":
        with engine.begin() as connection:
            for table in models.Base.metadata.sorted_tables:
                quoted = connection.dialect.identifier_preparer.quote(table.name)
                connection.execute(text(f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY"))
                connection.execute(text(f"REVOKE ALL ON TABLE {quoted} FROM anon, authenticated"))
    # Upgrade an installation created from the attached SQL before this app version added the
    # video linkage. Safe on fresh databases and idempotent on existing Supabase projects.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE meta_connections ADD COLUMN IF NOT EXISTS login_mode VARCHAR(32) NOT NULL DEFAULT 'facebook'"))
        connection.execute(text("ALTER TABLE meta_connections ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE music_tracks ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false"))
        connection.execute(text("ALTER TABLE publications ADD COLUMN IF NOT EXISTS requested_privacy VARCHAR(16) NOT NULL DEFAULT 'private'"))
        connection.execute(text("ALTER TABLE publications ADD COLUMN IF NOT EXISTS actual_privacy VARCHAR(16) NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE content_ledger ADD COLUMN IF NOT EXISTS academic_term TEXT"))
        connection.execute(text("ALTER TABLE content_ledger DROP CONSTRAINT IF EXISTS content_ledger_status_ck"))
        connection.execute(text("ALTER TABLE content_ledger DROP CONSTRAINT IF EXISTS content_ledger_status_check"))
        connection.execute(text("UPDATE content_ledger SET status = CASE WHEN status = 'failed' THEN 'discarded' ELSE 'active' END WHERE status NOT IN ('active', 'discarded')"))
        connection.execute(text("ALTER TABLE content_ledger ALTER COLUMN status SET DEFAULT 'active'"))
        connection.execute(text("ALTER TABLE content_ledger ADD CONSTRAINT content_ledger_status_ck CHECK (status IN ('active','discarded'))"))
        connection.execute(text(
            "ALTER TABLE content_ledger ADD COLUMN IF NOT EXISTS video_id VARCHAR(32)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_content_ledger_video_id ON content_ledger (video_id)"
        ))
