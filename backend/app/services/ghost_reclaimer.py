# Releases bookings nobody checked in to (GHOST_GRACE_MINUTES after start).

import asyncio
import logging
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.redis import GHOST_LOCK_KEY, REDIS_ERRORS
from app.core.telemetry import ghost_reclaimed_total
from app.core.timeutil import local_date_of
from app.models import Room
from app.services.availability_service import invalidate_dates
from app.services.events import publish_slot_updated

logger = logging.getLogger(__name__)

GHOST_LOCK_TTL = 55

RECLAIM_SQL = text(
    """
    UPDATE bookings
       SET status = 'AUTO_RELEASED', updated_at = now()
     WHERE status = 'CONFIRMED'
       AND start_time <= now() - make_interval(mins => :grace)
    RETURNING id, room_id, start_time
    """
)


async def reclaim_once(session_factory: async_sessionmaker, redis: aioredis.Redis) -> int:
    settings = get_settings()
    try:
        acquired = await redis.set(GHOST_LOCK_KEY, uuid.uuid4().hex, nx=True, ex=GHOST_LOCK_TTL)
        if not acquired:
            logger.debug("Another worker holds %s, skipping this cycle", GHOST_LOCK_KEY)
            return 0
    except REDIS_ERRORS as exc:
        # fine to run without the lock, the update is idempotent
        logger.warning("Redis unavailable for reclaimer lock, proceeding: %s", exc)

    async with session_factory() as session:
        async with session.begin():
            rows = (await session.execute(RECLAIM_SQL, {"grace": settings.GHOST_GRACE_MINUTES})).all()
        if not rows:
            return 0
        room_ids = {r.room_id for r in rows}
        names = dict(
            (await session.execute(select(Room.id, Room.name).where(Room.id.in_(room_ids)))).all()
        )

    ghost_reclaimed_total.inc(len(rows))

    seen: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
    for r in rows:
        seen.setdefault((r.room_id, local_date_of(r.start_time).isoformat()), r.id)

    await invalidate_dates(redis, {d for _, d in seen})
    for (room_id, date_str), booking_id in seen.items():
        await publish_slot_updated(
            redis,
            action="auto_released",
            date_str=date_str,
            room_id=room_id,
            room_name=names.get(room_id, ""),
            booking_id=booking_id,
        )
    logger.info("Ghost reclaimer released %d booking(s)", len(rows))
    return len(rows)


async def run_loop(session_factory: async_sessionmaker, redis: aioredis.Redis) -> None:
    interval = get_settings().GHOST_INTERVAL_SECONDS
    while True:
        try:
            await reclaim_once(session_factory, redis)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ghost reclaimer iteration failed; continuing")
        await asyncio.sleep(interval)
