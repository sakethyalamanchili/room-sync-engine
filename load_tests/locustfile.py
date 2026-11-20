# locust -f load_tests/locustfile.py --headless -u 30 -r 10 -t 60s --host http://localhost:8000 --csv load_tests/results
# 409s are expected here (people losing the race), so they count as success

import random
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from locust import HttpUser, between, events, task

BUSINESS_TZ = ZoneInfo("America/New_York")
OUTCOMES = {"201": 0, "409": 0}


class RoomSchedulerUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        resp = self.client.get("/api/v1/rooms", name="/api/v1/rooms")
        resp.raise_for_status()
        self.room_ids = [r["id"] for r in resp.json()]
        self.target_date = (datetime.now(BUSINESS_TZ) + timedelta(days=1)).date()

    @task(8)
    def poll_availability(self) -> None:
        self.client.get(
            f"/api/v1/rooms/availability?date={self.target_date.isoformat()}",
            name="/api/v1/rooms/availability",
        )

    @task(2)
    def attempt_booking(self) -> None:
        d = self.target_date
        # Random 30-minute-aligned start between 09:00 and 11:00 business time.
        start_minutes = 9 * 60 + 30 * random.randint(0, 4)
        start_local = datetime(d.year, d.month, d.day, start_minutes // 60, start_minutes % 60, tzinfo=BUSINESS_TZ)
        duration = random.choice((30, 60))
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = start_utc + timedelta(minutes=duration)
        n = random.randint(1, 10_000)
        body = {
            "room_id": random.choice(self.room_ids),
            "organizer_name": f"Load User {n}",
            "organizer_email": f"load{n}@example.com",
            "title": "Load test meeting",
            "start_time": start_utc.isoformat().replace("+00:00", "Z"),
            "end_time": end_utc.isoformat().replace("+00:00", "Z"),
        }
        with self.client.post(
            "/api/v1/bookings",
            json=body,
            headers={"Idempotency-Key": str(uuid.uuid4())},
            name="/api/v1/bookings",
            catch_response=True,
        ) as resp:
            if resp.status_code in (201, 409):
                OUTCOMES[str(resp.status_code)] += 1
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")


@events.quitting.add_listener
def _print_outcomes(environment, **_kwargs) -> None:
    print(f"Booking outcomes: 201 created={OUTCOMES['201']}, 409 conflict={OUTCOMES['409']}")
