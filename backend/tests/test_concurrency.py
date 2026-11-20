import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from prometheus_client import REGISTRY
from sqlalchemy import event, func, select, text

from app.core.redis import ROOM_EVENTS_CHANNEL, avail_key
from app.models import Booking
from app.services.ghost_reclaimer import reclaim_once
from tests.conftest import TEST_REDIS_URL
from tests.helpers import booking_body, business_date, business_slot

BOOKINGS = "/api/v1/bookings"
AVAIL = "/api/v1/rooms/availability"


def _metric(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


async def _count_rows(session_factory, room_id: str, start: str | None = None, end: str | None = None) -> int:
    stmt = select(func.count()).select_from(Booking).where(Booking.room_id == uuid.UUID(room_id))
    if start and end:
        stmt = stmt.where(
            Booking.start_time == datetime.fromisoformat(start),
            Booking.end_time == datetime.fromisoformat(end),
        )
    async with session_factory() as s:
        return (await s.execute(stmt)).scalar_one()


async def test_30_user_race_condition(client, redis, session_factory, room_id):
    start, end = business_slot(10, 0, 60)
    bodies = [booking_body(room_id, start, end, email=f"user{i}@example.com") for i in range(30)]

    responses = await asyncio.gather(*(client.post(BOOKINGS, json=b) for b in bodies))
    codes = [r.status_code for r in responses]

    assert codes.count(201) == 1, codes
    assert codes.count(409) == 29, codes
    assert set(codes) == {201, 409}
    assert await _count_rows(session_factory, room_id, start, end) == 1
    assert await redis.keys("lock:*") == []


async def test_back_to_back_half_open_interval(client, room_id):
    s1, e1 = business_slot(10, 0, 60)
    s2, e2 = business_slot(11, 0, 60)
    r1 = await client.post(BOOKINGS, json=booking_body(room_id, s1, e1))
    r2 = await client.post(BOOKINGS, json=booking_body(room_id, s2, e2))
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text


async def test_idempotency_key_replay(client, session_factory, room_id):
    start, end = business_slot(10, 0, 60)
    key = str(uuid.uuid4())
    body = booking_body(room_id, start, end)

    r1 = await client.post(BOOKINGS, json=body, headers={"Idempotency-Key": key})
    r2 = await client.post(BOOKINGS, json=body, headers={"Idempotency-Key": key})

    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert "Idempotent-Replay" not in r1.headers
    assert await _count_rows(session_factory, room_id) == 1


async def test_concurrent_same_idempotency_key(client, session_factory, room_id):
    start, end = business_slot(10, 0, 60)
    key = str(uuid.uuid4())
    body = booking_body(room_id, start, end)

    responses = await asyncio.gather(
        *(client.post(BOOKINGS, json=body, headers={"Idempotency-Key": key}) for _ in range(10))
    )
    codes = [r.status_code for r in responses]
    assert codes == [201] * 10, [(r.status_code, r.text) for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(1 for r in responses if r.headers.get("Idempotent-Replay") == "true") == 9
    assert await _count_rows(session_factory, room_id) == 1


async def test_idempotency_key_payload_mismatch(client, room_id):
    start, end = business_slot(10, 0, 60)
    key = str(uuid.uuid4())
    r1 = await client.post(BOOKINGS, json=booking_body(room_id, start, end), headers={"Idempotency-Key": key})
    r2 = await client.post(
        BOOKINGS, json=booking_body(room_id, start, end, title="Different"), headers={"Idempotency-Key": key}
    )
    assert r1.status_code == 201
    assert r2.status_code == 422
    assert "different payload" in r2.json()["detail"]

    bad = await client.post(BOOKINGS, json=booking_body(room_id, start, end), headers={"Idempotency-Key": "nope"})
    assert bad.status_code == 422


async def test_cancel_frees_slot(client, room_id):
    start, end = business_slot(14, 0, 60)
    r1 = await client.post(BOOKINGS, json=booking_body(room_id, start, end))
    assert r1.status_code == 201
    booking_id = r1.json()["id"]

    blocked = await client.post(BOOKINGS, json=booking_body(room_id, start, end, email="b@example.com"))
    assert blocked.status_code == 409

    cancel = await client.delete(f"{BOOKINGS}/{booking_id}")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED"

    again = await client.delete(f"{BOOKINGS}/{booking_id}")
    assert again.status_code == 200 and again.json()["status"] == "CANCELLED"

    rebook = await client.post(BOOKINGS, json=booking_body(room_id, start, end, email="b@example.com"))
    assert rebook.status_code == 201, rebook.text

    missing = await client.delete(f"{BOOKINGS}/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_patch_conflict_and_move(client, redis, room_id):
    sa, ea = business_slot(10, 0, 60)
    sb, eb = business_slot(11, 0, 60)
    a = await client.post(BOOKINGS, json=booking_body(room_id, sa, ea, title="A"))
    b = await client.post(BOOKINGS, json=booking_body(room_id, sb, eb, title="B"))
    assert a.status_code == 201 and b.status_code == 201
    b_id = b.json()["id"]

    overlap_start, _ = business_slot(10, 30, 30)
    conflict = await client.patch(f"{BOOKINGS}/{b_id}", json={"start_time": overlap_start})
    assert conflict.status_code == 409, conflict.text

    # fill the cache, then move B to 12-13
    d1 = business_date(1).isoformat()
    await client.get(AVAIL, params={"date": d1})
    assert await redis.exists(avail_key(d1)) == 1

    s12, e13 = business_slot(12, 0, 60)
    moved = await client.patch(f"{BOOKINGS}/{b_id}", json={"start_time": s12, "end_time": e13})
    assert moved.status_code == 200, moved.text
    assert moved.json()["start_time"] == s12
    assert await redis.exists(avail_key(d1)) == 0

    # moving to another day should clear both days
    d2 = business_date(2).isoformat()
    await client.get(AVAIL, params={"date": d1})
    await client.get(AVAIL, params={"date": d2})
    assert await redis.exists(avail_key(d1), avail_key(d2)) == 2
    s_next, e_next = business_slot(12, 0, 60, days_ahead=2)
    moved2 = await client.patch(f"{BOOKINGS}/{b_id}", json={"start_time": s_next, "end_time": e_next})
    assert moved2.status_code == 200, moved2.text
    assert await redis.exists(avail_key(d1), avail_key(d2)) == 0

    retitled = await client.patch(f"{BOOKINGS}/{b_id}", json={"title": "Renamed"})
    assert retitled.status_code == 200 and retitled.json()["title"] == "Renamed"
    await client.delete(f"{BOOKINGS}/{b_id}")
    closed = await client.patch(f"{BOOKINGS}/{b_id}", json={"title": "Nope"})
    assert closed.status_code == 409
    empty = await client.patch(f"{BOOKINGS}/{a.json()['id']}", json={})
    assert empty.status_code == 422


async def test_availability_two_queries_and_cache(app, client, room_id):
    d = business_date(1).isoformat()
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sync_engine = app.state.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        hits_before = _metric("redis_cache_operations_total", {"operation": "hit"})
        misses_before = _metric("redis_cache_operations_total", {"operation": "miss"})

        cold = await client.get(AVAIL, params={"date": d})
        assert cold.status_code == 200
        assert len(statements) == 2, statements
        payload = cold.json()
        assert payload["date"] == d
        assert payload["timezone"] == "America/New_York"
        assert len(payload["rooms"]) == 12
        assert _metric("redis_cache_operations_total", {"operation": "miss"}) == misses_before + 1

        statements.clear()
        warm = await client.get(AVAIL, params={"date": d})
        assert warm.status_code == 200
        assert len(statements) == 0, statements
        assert warm.json() == payload
        assert _metric("redis_cache_operations_total", {"operation": "hit"}) == hits_before + 1
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    start, end = business_slot(9, 0, 30)
    created = await client.post(BOOKINGS, json=booking_body(room_id, start, end, title="Fresh"))
    assert created.status_code == 201

    misses_before = _metric("redis_cache_operations_total", {"operation": "miss"})
    after = await client.get(AVAIL, params={"date": d})
    assert _metric("redis_cache_operations_total", {"operation": "miss"}) == misses_before + 1
    room = next(r for r in after.json()["rooms"] if r["id"] == room_id)
    assert [b["id"] for b in room["bookings"]] == [created.json()["id"]]
    assert "organizer_email" not in room["bookings"][0]


async def test_ghost_reclaimer(client, redis, session_factory, rooms):
    ghost_room = uuid.UUID(rooms[0]["id"])
    present_room = uuid.UUID(rooms[1]["id"])
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=20)
    end = start + timedelta(minutes=60)

    ghost = Booking(
        room_id=ghost_room, organizer_name="Ghost", organizer_email="g@example.com",
        title="No-show", start_time=start, end_time=end, status="CONFIRMED",
    )
    present = Booking(
        room_id=present_room, organizer_name="Here", organizer_email="h@example.com",
        title="Attended", start_time=start, end_time=end, status="CHECKED_IN",
    )
    async with session_factory() as s, s.begin():
        s.add_all([ghost, present])

    before = _metric("ghost_reclaimed_total")
    released = await reclaim_once(session_factory, redis)
    assert released == 1
    assert _metric("ghost_reclaimed_total") == before + 1

    async with session_factory() as s:
        statuses = dict((await s.execute(select(Booking.id, Booking.status))).all())
    assert statuses[ghost.id] == "AUTO_RELEASED"
    assert statuses[present.id] == "CHECKED_IN"

    # slot should be free again
    async with session_factory() as s, s.begin():
        s.add(Booking(
            room_id=ghost_room, organizer_name="Walk-in", organizer_email="w@example.com",
            title="Walk-in", start_time=start, end_time=end, status="CONFIRMED",
        ))

    late = await client.post(f"{BOOKINGS}/{ghost.id}/check-in")
    assert late.status_code == 409


async def test_validation_rules(client, room_id):
    def body(start: str, end: str) -> dict:
        return booking_body(room_id, start, end)

    s, e = business_slot(10, 0, 60)
    naive = body(s.replace("Z", ""), e.replace("Z", ""))
    cases = {
        "naive datetime": naive,
        "end <= start": body(e, s),
        "end == start": body(s, s),
        "not aligned": body(*business_slot(10, 15, 60)),
        "before hours": body(*business_slot(7, 0, 60)),
        "after hours": body(*business_slot(17, 30, 60)),
        "crosses midnight": body(*business_slot(23, 30, 60)),
        "over 240 minutes": body(*business_slot(8, 0, 270)),
        "in the past": body(*business_slot(10, 0, 60, days_ahead=-1)),
        "bad email": {**body(s, e), "organizer_email": "not-an-email"},
    }
    for name, payload in cases.items():
        resp = await client.post(BOOKINGS, json=payload)
        assert resp.status_code == 422, f"{name}: {resp.status_code} {resp.text}"

    unknown = await client.post(BOOKINGS, json=booking_body(str(uuid.uuid4()), s, e))
    assert unknown.status_code == 404

    bad_date = await client.get(AVAIL, params={"date": "2026-13-45"})
    assert bad_date.status_code == 422


async def test_event_published_after_booking(client, room_id):
    sub_client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    pubsub = sub_client.pubsub()
    try:
        await pubsub.subscribe(ROOM_EVENTS_CHANNEL)
        confirm = await pubsub.get_message(timeout=2.0)
        assert confirm and confirm["type"] == "subscribe"

        start, end = business_slot(15, 0, 60)
        created = await client.post(BOOKINGS, json=booking_body(room_id, start, end))
        assert created.status_code == 201

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        event_msg = None
        while loop.time() < deadline and event_msg is None:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg["type"] == "message":
                data = json.loads(msg["data"])
                if data.get("booking_id") == created.json()["id"]:
                    event_msg = data
        assert event_msg is not None, "no SLOT_UPDATED event within 2s"
        assert event_msg["event"] == "SLOT_UPDATED"
        assert event_msg["action"] == "created"
        assert event_msg["room_id"] == room_id
        assert event_msg["room_name"] == "EECS Conference Room"
        assert event_msg["date"] == business_date(1).isoformat()
    finally:
        await pubsub.aclose()
        await sub_client.aclose()


async def test_check_in_flow(client, room_id):
    start, end = business_slot(16, 0, 60)
    created = await client.post(BOOKINGS, json=booking_body(room_id, start, end))
    bid = created.json()["id"]
    first = await client.post(f"{BOOKINGS}/{bid}/check-in")
    assert first.status_code == 200 and first.json()["status"] == "CHECKED_IN"
    second = await client.post(f"{BOOKINGS}/{bid}/check-in")
    assert second.status_code == 200 and second.json()["status"] == "CHECKED_IN"
    missing = await client.post(f"{BOOKINGS}/{uuid.uuid4()}/check-in")
    assert missing.status_code == 404


async def test_redis_down_fails_open_and_db_still_prevents_double_booking(app, client, session_factory, room_id):
    # point the app at a redis that doesn't exist. postgres alone should still give exactly one winner
    real_redis = app.state.redis
    dead = aioredis.from_url("redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.5)
    app.state.redis = dead
    errors_before = _metric("booking_requests_total", {"outcome": "error"})
    try:
        start, end = business_slot(9, 0, 60)
        bodies = [booking_body(room_id, start, end, email=f"down{i}@example.com") for i in range(30)]
        responses = await asyncio.gather(
            *(client.post(BOOKINGS, json=b, headers={"Idempotency-Key": str(uuid.uuid4())}) for b in bodies)
        )
        codes = sorted(r.status_code for r in responses)
        assert codes == [201] + [409] * 29, codes
        assert await _count_rows(session_factory, room_id, start, end) == 1
        assert _metric("booking_requests_total", {"outcome": "error"}) == errors_before

        grid = await client.get(AVAIL, params={"date": business_date(1).isoformat()})
        assert grid.status_code == 200
        health = await client.get("/health")
        assert health.status_code == 503
    finally:
        app.state.redis = real_redis
        await dead.aclose()


async def test_db_constraint_rejects_overlap_directly(session_factory, room_id):
    start, end = business_slot(13, 0, 60)
    s1, e1 = datetime.fromisoformat(start), datetime.fromisoformat(end)
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO bookings (room_id, organizer_name, organizer_email, title, start_time, end_time)"
                " VALUES (:r, 'a', 'a@x.io', 't', :s, :e)"
            ),
            {"r": uuid.UUID(room_id), "s": s1, "e": e1},
        )
    from sqlalchemy.exc import IntegrityError

    from app.services.booking_service import is_exclusion_violation

    try:
        async with session_factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO bookings (room_id, organizer_name, organizer_email, title, start_time, end_time)"
                    " VALUES (:r, 'b', 'b@x.io', 't', :s, :e)"
                ),
                {"r": uuid.UUID(room_id), "s": s1 + timedelta(minutes=30), "e": e1 + timedelta(minutes=30)},
            )
        raise AssertionError("overlap was not rejected")
    except IntegrityError as exc:
        assert is_exclusion_violation(exc)


async def test_rooms_are_fau_eecs_catalog(rooms):
    from app.seed import ROOMS

    assert len(rooms) == 12
    assert {r["name"] for r in rooms} == {r["name"] for r in ROOMS}
    assert [(r["floor"], r["name"]) for r in rooms] == sorted((r["floor"], r["name"]) for r in rooms)
    assert all(set(r["amenities"]) == {"projector", "whiteboard", "video_conf"} for r in rooms)
    assert not any(r["name"].startswith(("Boardroom", "Focus Pod", "War Room")) for r in rooms)
