import asyncio
import logging
import random
import uuid

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import Settings

logger = logging.getLogger(__name__)

ROOM_EVENTS_CHANNEL = "room_events"
GHOST_LOCK_KEY = "lock:ghost_reclaimer"

# if redis throws one of these we skip that layer instead of failing the request
REDIS_ERRORS: tuple[type[BaseException], ...] = (
    RedisConnectionError,
    RedisTimeoutError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)

# only delete the lock if we still own it (ttl may have expired and someone else took it)
RELEASE_LOCK_LUA = (
    "if redis.call('get',KEYS[1])==ARGV[1] then "
    "return redis.call('del',KEYS[1]) else return 0 end"
)


def create_redis(settings: Settings) -> aioredis.Redis:
    return aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def avail_key(date_str: str) -> str:
    return f"avail:grid:{date_str}"


def idemp_key(key: uuid.UUID | str) -> str:
    return f"idemp:{key}"


def room_lock_key(room_id: uuid.UUID | str, date_str: str) -> str:
    return f"lock:room:{room_id}:{date_str}"


class LockResult:
    # state is "acquired", "busy", or "skipped" (redis down, so we just go to the db)

    __slots__ = ("state", "token")

    def __init__(self, state: str, token: str | None = None) -> None:
        self.state = state
        self.token = token


async def acquire_lock(redis: aioredis.Redis, key: str, ttl: int, retries: int = 1) -> LockResult:
    token = uuid.uuid4().hex
    try:
        for attempt in range(retries + 1):
            if await redis.set(key, token, nx=True, ex=ttl):
                return LockResult("acquired", token)
            if attempt < retries:
                await asyncio.sleep(0.05 + random.uniform(0, 0.025))
        return LockResult("busy")
    except REDIS_ERRORS as exc:
        logger.warning("Redis unavailable while acquiring %s, skipping lock layer: %s", key, exc)
        return LockResult("skipped")


async def release_lock(redis: aioredis.Redis, key: str, token: str | None) -> None:
    if not token:
        return
    try:
        await redis.eval(RELEASE_LOCK_LUA, 1, key, token)
    except REDIS_ERRORS as exc:
        logger.error("Failed to release lock %s (it will expire by TTL): %s", key, exc)
