# Write path for bookings. Redis (idempotency + room lock) is only there to soak up
# bursts and give nicer errors; if it's down we skip it. The exclusion constraint in
# postgres is what actually prevents double bookings.

import asyncio
import hashlib
import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeVar

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.redis import (
    REDIS_ERRORS,
    acquire_lock,
    idemp_key,
    release_lock,
    room_lock_key,
)
from app.core.telemetry import booking_requests_total
from app.core.timeutil import local_date_of, to_business_local, utcnow
from app.models import ACTIVE_STATUSES, Booking, Room
from app.schemas.booking import BookingCreate, BookingOut, BookingUpdate
from app.services.availability_service import invalidate_dates
from app.services.events import publish_slot_updated

logger = logging.getLogger(__name__)

IDEMP_PENDING_TTL = 30
IDEMP_POLL_INTERVAL = 0.05
IDEMP_POLL_TIMEOUT = 2.0

MSG_DB_CONFLICT = "Room is already booked for the selected time slot."
MSG_LOCK_CONFLICT = "High contention on room; please retry"
MSG_IDEMP_MISMATCH = "Idempotency-Key was already used with a different payload"
MSG_IDEMP_PENDING = "A request with this Idempotency-Key is still in progress"

# deadlock / serialization failure. retry these instead of returning a 500
TRANSIENT_SQLSTATES = frozenset({"40P01", "40001"})
DB_WRITE_ATTEMPTS = 3

T = TypeVar("T")


@dataclass
class CreateResult:
    body: dict
    replay: bool = False


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail=msg)


def validate_window(start: datetime, end: datetime, now: datetime | None = None) -> str:
    # returns the local (office tz) date string, which is what locks/cache keys use
    s = get_settings()
    now = now or utcnow()
    if start.tzinfo is None or end.tzinfo is None:
        raise _unprocessable("start_time and end_time must be timezone-aware")
    if start >= end:
        raise _unprocessable("start_time must be before end_time")

    ls, le = to_business_local(start), to_business_local(end)
    for label, dt in (("start_time", ls), ("end_time", le)):
        minutes = dt.hour * 60 + dt.minute
        if dt.second or dt.microsecond or minutes % s.SLOT_MINUTES:
            raise _unprocessable(
                f"{label} must align to a {s.SLOT_MINUTES}-minute boundary in {s.BUSINESS_TZ}"
            )

    duration_min = (end - start).total_seconds() / 60
    if duration_min < s.SLOT_MINUTES or duration_min > s.MAX_BOOKING_MINUTES:
        raise _unprocessable(
            f"Duration must be between {s.SLOT_MINUTES} and {s.MAX_BOOKING_MINUTES} minutes"
        )

    if ls.date() != le.date():
        raise _unprocessable(f"Booking must start and end on the same day in {s.BUSINESS_TZ}")
    if ls.time() < s.day_start or le.time() > s.day_end:
        raise _unprocessable(
            f"Booking must be within business hours {s.BUSINESS_DAY_START} to {s.BUSINESS_DAY_END} ({s.BUSINESS_TZ})"
        )

    if start < now - timedelta(minutes=s.PAST_TOLERANCE_MINUTES):
        raise _unprocessable("start_time is in the past")

    return ls.date().isoformat()


def _sqlstates(exc: DBAPIError) -> set[str]:
    orig = getattr(exc, "orig", None)
    candidates = (orig, getattr(orig, "__cause__", None))
    return {s for s in (getattr(c, "sqlstate", None) for c in candidates) if s}


def is_exclusion_violation(exc: IntegrityError) -> bool:
    return "23P01" in _sqlstates(exc) or "no_overlapping_bookings" in str(exc)


def is_transient(exc: DBAPIError) -> bool:
    return bool(_sqlstates(exc) & TRANSIENT_SQLSTATES)


ADVISORY_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


async def lock_room_day_in_db(session: AsyncSession, room_id: uuid.UUID, date_str: str) -> None:
    # Without this, when redis is down, concurrent inserts into the same room
    # deadlock each other inside the exclusion constraint check (found this with
    # the redis-down test). Advisory xact lock just makes them queue up.
    await session.execute(ADVISORY_LOCK_SQL, {"key": f"booking:{room_id}:{date_str}"})


async def run_write(
    session_factory: async_sessionmaker, work: Callable[[AsyncSession], Awaitable[T]]
) -> T:
    for attempt in range(1, DB_WRITE_ATTEMPTS + 1):
        try:
            async with session_factory() as session:
                async with session.begin():
                    return await work(session)
        except DBAPIError as exc:
            if isinstance(exc, IntegrityError) or not is_transient(exc) or attempt == DB_WRITE_ATTEMPTS:
                raise
            logger.warning("Transient DB error %s on attempt %d, retrying", _sqlstates(exc), attempt)
            await asyncio.sleep(random.uniform(0.01, 0.05) * attempt)
    raise AssertionError("unreachable")


def fingerprint(payload: BookingCreate) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def serialize(booking: Booking) -> dict:
    return BookingOut.model_validate(booking).model_dump(mode="json")


async def _room_name(session: AsyncSession, room_id: uuid.UUID) -> str | None:
    return (await session.execute(select(Room.name).where(Room.id == room_id))).scalar_one_or_none()


async def _get_room_name_or_404(session_factory: async_sessionmaker, room_id: uuid.UUID) -> str:
    async with session_factory() as session:
        name = await _room_name(session, room_id)
    if name is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return name


class _Idem:
    def __init__(self, redis: aioredis.Redis, key: uuid.UUID | None, fp: str | None) -> None:
        self.redis = redis
        self.rkey = idemp_key(key) if key else None
        self.fp = fp
        self.owned = False

    async def begin(self) -> dict | None:
        # returns the stored response if this is a replay, otherwise None
        if not self.rkey:
            return None
        pending = json.dumps({"state": "PENDING", "fp": self.fp})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + IDEMP_POLL_TIMEOUT
        try:
            while True:
                if await self.redis.set(self.rkey, pending, nx=True, ex=IDEMP_PENDING_TTL):
                    self.owned = True
                    return None
                raw = await self.redis.get(self.rkey)
                if raw is None:
                    continue  # owner gave up between our SET and GET
                data = json.loads(raw)
                if data.get("fp") != self.fp:
                    raise _unprocessable(MSG_IDEMP_MISMATCH)
                if data.get("state") == "DONE":
                    return data["response"]
                if loop.time() >= deadline:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_IDEMP_PENDING)
                await asyncio.sleep(IDEMP_POLL_INTERVAL)
        except REDIS_ERRORS as exc:
            logger.warning("Redis unavailable for idempotency, skipping layer: %s", exc)
            self.owned = False
            return None

    async def abandon(self) -> None:
        if not (self.rkey and self.owned):
            return
        try:
            await self.redis.delete(self.rkey)
        except REDIS_ERRORS as exc:
            logger.error("Failed to delete idempotency key %s (expires in %ss): %s", self.rkey, IDEMP_PENDING_TTL, exc)

    async def complete(self, response: dict) -> None:
        if not (self.rkey and self.owned):
            return
        try:
            await self.redis.set(
                self.rkey,
                json.dumps({"state": "DONE", "fp": self.fp, "response": response}),
                ex=get_settings().IDEMPOTENCY_TTL_SECONDS,
            )
        except REDIS_ERRORS as exc:
            logger.error("Failed to store idempotent response for %s: %s", self.rkey, exc)


async def create_booking(
    session_factory: async_sessionmaker,
    redis: aioredis.Redis,
    payload: BookingCreate,
    idempotency_key: uuid.UUID | None = None,
) -> CreateResult:
    settings = get_settings()
    date_str = validate_window(payload.start_time, payload.end_time)
    room_name = await _get_room_name_or_404(session_factory, payload.room_id)

    idem =_Idem(redis, idempotency_key, fingerprint(payload) if idempotency_key else None)
    replay = await idem.begin()
    if replay is not None:
        booking_requests_total.labels(outcome="idempotent_replay").inc()
        return CreateResult(body=replay, replay=True)

    lock_key =room_lock_key(payload.room_id, date_str)
    lock = await acquire_lock(redis, lock_key, settings.LOCK_TTL_SECONDS)
    if lock.state == "busy":
        booking_requests_total.labels(outcome="conflict_lock").inc()
        await idem.abandon()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_LOCK_CONFLICT)

    try:
        async def _insert(session: AsyncSession) -> Booking:
            await lock_room_day_in_db(session, payload.room_id, date_str)
            new = Booking(
                id=uuid.uuid4(),
                room_id=payload.room_id,
                organizer_name=payload.organizer_name,
                organizer_email=str(payload.organizer_email),
                title=payload.title,
                start_time=payload.start_time,
                end_time=payload.end_time,
                status="CONFIRMED",
            )
            session.add(new)
            await session.flush()
            return new

        try:
            booking = await run_write(session_factory, _insert)
        except IntegrityError as exc:
            if is_exclusion_violation(exc):
                booking_requests_total.labels(outcome="conflict_db").inc()
                await idem.abandon()
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_DB_CONFLICT) from None
            booking_requests_total.labels(outcome="error").inc()
            await idem.abandon()
            raise
        except Exception:
            booking_requests_total.labels(outcome="error").inc()
            await idem.abandon()
            raise

        # committed. none of these raise if redis is down
        body = serialize(booking)
        await invalidate_dates(redis, [date_str])
        await idem.complete(body)
        await publish_slot_updated(
            redis,
            action="created",
            date_str=date_str,
            room_id=booking.room_id,
            room_name=room_name,
            booking_id=booking.id,
        )
        booking_requests_total.labels(outcome="created").inc()
        return CreateResult(body=body)
    finally:
        await release_lock(redis, lock_key, lock.token)


async def update_booking(
    session_factory: async_sessionmaker,
    redis: aioredis.Redis,
    booking_id: uuid.UUID,
    patch: BookingUpdate,
) -> dict:
    settings = get_settings()
    async with session_factory() as session:
        current = await session.get(Booking, booking_id)
        old_room_name = await _room_name(session, current.room_id) if current else None
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if current.status != "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only CONFIRMED bookings can be modified (current status: {current.status})",
        )

    new_room_id = patch.room_id or current.room_id
    new_start = patch.start_time or current.start_time
    new_end = patch.end_time or current.end_time
    moving = (
        new_room_id != current.room_id
        or new_start != current.start_time
        or new_end != current.end_time
    )

    values: dict = {"updated_at": func.now()}
    if patch.title is not None:
        values["title"] = patch.title.strip()

    old_date = local_date_of(current.start_time).isoformat()
    new_date = old_date
    new_room_name = old_room_name or ""
    lock_key: str | None = None
    lock_token: str | None = None

    if moving:
        new_date = validate_window(new_start, new_end)
        if new_room_id != current.room_id:
            new_room_name = await _get_room_name_or_404(session_factory, new_room_id)
        values.update(room_id=new_room_id, start_time=new_start, end_time=new_end)
        lock_key = room_lock_key(new_room_id, new_date)
        lock = await acquire_lock(redis, lock_key, settings.LOCK_TTL_SECONDS)
        if lock.state == "busy":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_LOCK_CONFLICT)
        lock_token = lock.token

    try:
        stmt = (
            update(Booking)
            .where(Booking.id == booking_id, Booking.status == "CONFIRMED")
            .values(**values)
            .returning(Booking)
            .execution_options(synchronize_session=False)
        )

        async def _update(session: AsyncSession) -> Booking | None:
            if moving:
                await lock_room_day_in_db(session, new_room_id, new_date)
            return (await session.execute(stmt)).scalar_one_or_none()

        try:
            updated = await run_write(session_factory, _update)
        except IntegrityError as exc:
            if is_exclusion_violation(exc):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_DB_CONFLICT) from None
            raise
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Booking is no longer CONFIRMED and cannot be modified",
            )
        body = serialize(updated)
    finally:
        if lock_key:
            await release_lock(redis, lock_key, lock_token)

    # old and new day can differ if the booking moved, so clear/notify both
    await invalidate_dates(redis, [old_date, new_date])
    affected = {(current.room_id, old_date, old_room_name or ""), (new_room_id, new_date, new_room_name)}
    for room_id, date_str, name in sorted(affected, key=lambda t: (str(t[0]), t[1])):
        await publish_slot_updated(
            redis,
            action="updated",
            date_str=date_str,
            room_id=room_id,
            room_name=name,
            booking_id=booking_id,
        )
    return body


async def _after_status_change(redis: aioredis.Redis, booking: Booking, room_name: str, action: str) -> None:
    date_str = local_date_of(booking.start_time).isoformat()
    await invalidate_dates(redis, [date_str])
    await publish_slot_updated(
        redis,
        action=action,
        date_str=date_str,
        room_id=booking.room_id,
        room_name=room_name,
        booking_id=booking.id,
    )


async def check_in(session_factory: async_sessionmaker, redis: aioredis.Redis, booking_id: uuid.UUID) -> dict:
    async with session_factory() as session:
        async with session.begin():
            stmt = (
                update(Booking)
                .where(
                    Booking.id == booking_id,
                    Booking.status == "CONFIRMED",
                    Booking.end_time > func.now(),
                )
                .values(status="CHECKED_IN", updated_at=func.now())
                .returning(Booking)
                .execution_options(synchronize_session=False)
            )
            updated = (await session.execute(stmt)).scalar_one_or_none()
            room_name = await _room_name(session, updated.room_id) if updated else None

        if updated is None:
            current = await session.get(Booking, booking_id, populate_existing=True)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
            if current.status == "CHECKED_IN":
                return serialize(current)
            if current.status in ("CANCELLED", "AUTO_RELEASED"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cannot check in: booking is {current.status}",
                )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot check in: booking has already ended")

    body = serialize(updated)
    await _after_status_change(redis, updated, room_name or "", "checked_in")
    return body


async def cancel_booking(session_factory: async_sessionmaker, redis: aioredis.Redis, booking_id: uuid.UUID) -> dict:
    async with session_factory() as session:
        async with session.begin():
            stmt = (
                update(Booking)
                .where(Booking.id == booking_id, Booking.status.in_(ACTIVE_STATUSES))
                .values(status="CANCELLED", updated_at=func.now())
                .returning(Booking)
                .execution_options(synchronize_session=False)
            )
            updated = (await session.execute(stmt)).scalar_one_or_none()
            room_name = await _room_name(session, updated.room_id) if updated else None

        if updated is None:
            current = await session.get(Booking, booking_id, populate_existing=True)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
            return serialize(current)  # already cancelled/released, treat as success

    body = serialize(updated)
    await _after_status_change(redis, updated, room_name or "", "cancelled")
    return body
