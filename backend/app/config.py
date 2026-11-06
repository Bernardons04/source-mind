import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    SUPABASE_URL: str = "https://example.supabase.co"
    # Supabase publishable key (modern nomenclature)
    SUPABASE_PUBLISHABLE_KEY: str | None = None
    # Optional legacy fallback: modern Supabase projects validate via public JWKS automatically
    SUPABASE_JWT_SECRET: str | None = None
    SUPABASE_STORAGE_BUCKET: str = "documents"
    ENCRYPTION_KEY: str = "0123456789abcdef0123456789abcdef0123456789a="
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    DEFAULT_EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
    DEFAULT_CHAT_MODEL: str = "openai/gpt-4o-mini"
    # Direct PostgreSQL connection (session mode / port 5432) for workers and advisory locks
    DIRECT_DATABASE_URL: str | None = None
    # RabbitMQ connection URL for Dramatiq worker and outbox dispatcher
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    # Supabase service_role / secret key exclusively for worker and backend operations (never exposed to frontend)
    SUPABASE_SECRET_KEY: str | None = None
    # Outbox dispatcher configuration
    OUTBOX_POLL_INTERVAL_SECONDS: float = 2.0
    OUTBOX_BATCH_SIZE: int = 50
    OUTBOX_MAX_RETRIES: int = 5
    MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB

    # Reconciliation worker configuration
    RECONCILIATION_INTERVAL_SECONDS: float = 1800.0  # 30 minutes
    RECONCILIATION_OLDER_THAN_HOURS: int = 2

    # Clock skew tolerance (in seconds) for iat/nbf/exp verification (defaults to 300s/5min)
    JWT_LEEWAY_SECONDS: int = 300

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    def get_direct_database_url(self) -> str:
        """
        Validates and returns the DIRECT_DATABASE_URL for worker operations.
        Raises ValueError if DIRECT_DATABASE_URL is missing or if it points to a
        Transaction Pooling port (6543) incompatible with PostgreSQL session advisory locks.
        Fallback to DATABASE_URL is strictly forbidden to prevent transaction-pooler corruption.
        """
        if not self.DIRECT_DATABASE_URL:
            raise ValueError(
                "DIRECT_DATABASE_URL is not configured. The background worker requires a direct PostgreSQL "
                "connection or Session Mode (port 5432) for session advisory locks (pg_try_advisory_lock). "
                "Silent fallback to DATABASE_URL is not allowed."
            )
        url = self.DIRECT_DATABASE_URL.strip()
        if ":6543" in url:
            raise ValueError(
                "Incompatible DIRECT_DATABASE_URL: detected port 6543 (Transaction Pooling). "
                "PostgreSQL session advisory locks are broken by transaction poolers. "
                "Configure a direct connection or Session Mode on port 5432."
            )
        return url


settings = Settings()
