import json
import logging
from collections.abc import Iterable
from datetime import date

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.redis import REDIS_ERRORS, avail_key
from app.core.telemetry import redis_cache_operations_total
from app.core.timeutil import day_bounds_utc
from app.models import ACTIVE_STATUSES, Booking, Room
from app.schemas.room import AvailabilityBooking, RoomOut

logger = logging.getLogger(__name__)


async def get_cached(redis: aioredis.Redis, d: date) -> str | None:
    try:
        return await redis.get(avail_key(d.isoformat()))
    except REDIS_ERRORS as exc:
        logger.warning("Redis unavailable on cache read, falling back to DB: %s", exc)
        return None


async def build_availability(session_factory: async_sessionmaker, d: date) -> dict:
    # 2 queries: rooms, then only that day's active bookings via selectinload
    settings = get_settings()
    start_utc, end_utc = day_bounds_utc(d)
    stmt = (
        select(Room)
        .options(
            selectinload(
                Room.bookings.and_(
                    Booking.status.in_(ACTIVE_STATUSES),
                    Booking.start_time < end_utc,
                    Booking.end_time > start_utc,
                )
            )
        )
        .order_by(Room.floor, Room.name)
    )
    async with session_factory() as session:
        rooms = (await session.execute(stmt)).scalars().all()

    room_payloads = []
    for room in rooms:
        data = RoomOut.model_validate(room).model_dump(mode="json")
        data["bookings"] = [
            AvailabilityBooking.model_validate(b).model_dump(mode="json")
            for b in sorted(room.bookings, key=lambda b: b.start_time)
        ]
        room_payloads.append(data)

    return {
        "date": d.isoformat(),
        "timezone": settings.BUSINESS_TZ,
        "business_hours": {
            "start": settings.BUSINESS_DAY_START,
            "end": settings.BUSINESS_DAY_END,
            "slot_minutes": settings.SLOT_MINUTES,
        },
        "rooms": room_payloads,
    }


async def get_availability_json(
    session_factory: async_sessionmaker, redis: aioredis.Redis, d: date
) -> str:
    cached = await get_cached(redis, d)
    if cached is not None:
        redis_cache_operations_total.labels(operation="hit").inc()
        return cached

    redis_cache_operations_total.labels(operation="miss").inc()
    payload = await build_availability(session_factory, d)
    serialized = json.dumps(payload, separators=(",", ":"))
    try:
        await redis.set(avail_key(d.isoformat()), serialized, ex=get_settings().CACHE_TTL_SECONDS)
    except REDIS_ERRORS as exc:
        logger.warning("Redis unavailable on cache write: %s", exc)
    return serialized


async def invalidate_dates(redis: aioredis.Redis, date_strs: Iterable[str]) -> None:
    keys = sorted({avail_key(ds) for ds in date_strs})
    if not keys:
        return
    try:
        await redis.delete(*keys)
    except REDIS_ERRORS as exc:
        logger.error("Failed to invalidate %s (stale until TTL): %s", keys, exc)
