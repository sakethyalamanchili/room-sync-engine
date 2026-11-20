from datetime import time
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    DATABASE_URL: str = "postgresql+asyncpg://roomsync:roomsync@postgres:5432/roomsync"
    REDIS_URL: str = "redis://redis:6379/0"

    BUSINESS_TZ: str = "America/New_York"
    BUSINESS_DAY_START: str = "08:00"
    BUSINESS_DAY_END: str = "18:00"
    SLOT_MINUTES: int = 30
    MAX_BOOKING_MINUTES: int = 240
    PAST_TOLERANCE_MINUTES: int = 10

    GHOST_GRACE_MINUTES: int = 15
    GHOST_INTERVAL_SECONDS: int = 60

    CACHE_TTL_SECONDS: int = 300
    LOCK_TTL_SECONDS: int = 3
    IDEMPOTENCY_TTL_SECONDS: int = 86400

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 10

    CORS_ORIGINS: str = "http://localhost:5173"
    ENABLE_BACKGROUND_TASKS: bool = True

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def day_start(self) -> time:
        return time.fromisoformat(self.BUSINESS_DAY_START)

    @property
    def day_end(self) -> time:
        return time.fromisoformat(self.BUSINESS_DAY_END)


@lru_cache
def get_settings() -> Settings:
    return Settings()
