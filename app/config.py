from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "NexoraAPI"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "change-this-secret"

    # JWT
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "nexora"
    postgres_user: str = "nexora"
    postgres_password: str = "nexora_secret"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # Security
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15
    rate_limit_per_minute: int = 60

    # IPTV concurrency
    heartbeat_ttl_seconds: int = 180        # auto-disconnect after 3 missed heartbeats
    playback_token_expire_seconds: int = 60 # short-lived: 30-120s for HLS/Flussonic

    # Client (subscriber) tokens — longer-lived for mobile/TV apps
    client_access_token_expire_hours: int = 24
    client_refresh_token_expire_days: int = 90

    # STB
    stb_portal_url: str = "http://172.27.99.151/nexora_portal"

    @property
    def database_url(self) -> str:
        """Async URL for SQLAlchemy create_async_engine (psycopg3)."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic offline mode."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
