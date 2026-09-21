"""Bootstrap configuration. Only things needed BEFORE the database is reachable live here.

Everything else (keys, models, video shape, pacing, schedule) is runtime-editable from the
dashboard and stored in the database. See app/settings_store.py.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Boot(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase/Postgres connection string, e.g.
    # postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
    # Required — there is no local SQLite fallback.
    database_url: str
    output_dir: str = "./outputs"
    work_dir: str = "./data/work"
    media_dir: str = "./data/media"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    admin_token: str = ""
    app_env: str = "development"
    serpapi_key: str = ""
    wikimedia_user_agent: str = "StoryShorts/1.0 (https://www.mediawiki.org/wiki/API:Etiquette; educational story research)"
    scheduler_token: str = ""
    render_public_url: str = ""
    youtube_client_id: str = Field(default="", validation_alias=AliasChoices("YOUTUBE_CLIENT_ID", "GOOGLE_CLIENT_ID"))
    youtube_client_secret: str = Field(default="", validation_alias=AliasChoices("YOUTUBE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"))
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    oauth_encryption_key: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/auth/meta/callback"
    youtube_refresh_token: str = ""
    youtube_channel_tokens_json: str = "{}"
    instagram_access_token: str = ""
    instagram_account_id: str = ""
    instagram_graph_version: str = Field(default="v25.0", validation_alias=AliasChoices("INSTAGRAM_GRAPH_VERSION", "META_API_VERSION"))
    social_channels_json: str = "{}"

    # Supabase Storage (optional — if both are set, finished videos and music
    # are uploaded and local copies are deleted to stay within Render's ephemeral disk)
    supabase_url: str = ""
    supabase_key: str = ""   # service-role key (server-side only, never sent to the browser)

    # seed values: copied into the database on first boot, then edited from the dashboard
    gemini_free_keys: str = ""
    gemini_paid_keys: str = ""
    gemini_audio_paid_key: str = ""
    pollinations_api_keys: str = ""
    groq_api_keys: str = ""

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError(
                "DATABASE_URL is required. Set it to your Supabase Postgres connection string "
                "(Project Settings → Database → Connection string) — "
                "e.g. postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres"
            )
        if value.startswith("sqlite"):
            raise ValueError(
                "SQLite is no longer supported. Set DATABASE_URL to your Supabase Postgres connection string instead."
            )
        if value.startswith("postgres://"):
            value = "postgresql+psycopg://" + value[len("postgres://"):]
        elif value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://"):]
        return value

    @property
    def outputs(self) -> Path:
        path = (ROOT / self.output_dir).resolve() if self.output_dir.startswith(".") else Path(self.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def work_root(self) -> Path:
        path = (ROOT / self.work_dir).resolve() if self.work_dir.startswith(".") else Path(self.work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def media_root(self) -> Path:
        path = (ROOT / self.media_dir).resolve() if self.media_dir.startswith(".") else Path(self.media_dir)
        (path / "music").mkdir(parents=True, exist_ok=True)
        return path

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def boot() -> Boot:
    return Boot()


# Back-compat alias used across the codebase for paths only.
settings = boot
