# Only call these after the commit. If publish fails we just log it, the booking
# is already saved and clients will catch up on their next refetch.

import json
import logging
import uuid

import redis.asyncio as aioredis

from app.core.redis import REDIS_ERRORS, ROOM_EVENTS_CHANNEL

logger = logging.getLogger(__name__)

ACTIONS = ("created", "updated", "cancelled", "checked_in", "auto_released")


async def publish_slot_updated(
    redis: aioredis.Redis,
    *,
    action: str,
    date_str: str,
    room_id: uuid.UUID | str,
    room_name: str,
    booking_id: uuid.UUID | str,
) -> None:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    payload = {
        "event": "SLOT_UPDATED",
        "action": action,
        "date": date_str,
        "room_id": str(room_id),
        "room_name": room_name,
        "booking_id": str(booking_id),
    }
    try:
        await redis.publish(ROOM_EVENTS_CHANNEL, json.dumps(payload))
    except REDIS_ERRORS as exc:
        logger.error("couldn't publish %s: %s", payload, exc)
