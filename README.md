# FAU EECS RoomSync

Room booking for the EECS department at FAU. The goal was to handle the moments when a lot of people grab for the same room at once (conference room before a faculty meeting, thesis defense room near the end of the semester, senior design studios before demo day) without ever double booking, and have every open browser update live without polling.

Stack: FastAPI, PostgreSQL 16, Redis 7, React + TypeScript (Vite, Tailwind, TanStack Query), Prometheus and Grafana. `docker compose up` starts all of it.

![Availability grid](docs/grid.png)

## Architecture

```mermaid
flowchart LR
    UI[React app] -- REST --> API[FastAPI]
    UI <-- WebSocket --> API
    API -- bookings --> PG[(Postgres)]
    API -- cache, locks, idempotency keys --> R[(Redis)]
    API -- publish room_events --> R
    R -- subscribe --> API
    Prom[Prometheus] -- scrape /metrics --> API
    Graf[Grafana] --> Prom
```

When someone books a room:

```mermaid
sequenceDiagram
    participant C as Browser
    participant A as API
    participant R as Redis
    participant P as Postgres

    C->>A: POST /api/v1/bookings (Idempotency-Key)
    A->>R: SET idemp:{key} NX
    Note over A,R: if the key already finished, replay the saved 201
    A->>R: SET lock:room:{room}:{date} NX EX 3
    Note over A,R: still locked after one retry -> 409
    A->>P: BEGIN, advisory lock, INSERT, COMMIT
    Note over A,P: overlap -> 23P01 -> 409
    A->>R: delete cached grid, save response, PUBLISH room_events
    A->>R: release lock
    A-->>C: 201
    R-->>C: event reaches every open tab over the WebSocket
```

## The 3-tier concurrency defense

1. **Idempotency key.** The frontend sends a UUID with each submit. The first request claims `idemp:{key}` in Redis with `SET NX`. Duplicates (double clicks, retries after a timeout) wait for it and get the same 201 back. Reusing a key with a different body is a 422.
2. **Redis lock per room per day.** `SET lock:room:{room_id}:{date} NX EX 3`. If it's taken, wait about 50ms and try once more, then give up with a 409. The lock is released with a small Lua script so a request can't delete a lock that isn't its own anymore.
3. **Postgres exclusion constraint.** This is the one that matters:

   ```sql
   EXCLUDE USING gist (room_id WITH =, tstzrange(start_time, end_time, '[)') WITH &&)
   WHERE (status IN ('CONFIRMED', 'CHECKED_IN'))
   ```

   `[)` means 10:00-11:00 and 11:00-12:00 don't conflict. Cancelled and auto-released bookings drop out of the constraint, so their slot frees up right away.

Only the database constraint guarantees correctness. The Redis layers are there to turn people away early during a burst, give cleaner errors, and make retries safe. If Redis goes down the API skips those layers and keeps working; there's a test that points the app at a dead Redis and still gets exactly one 201 and 29 409s.

### A bug the tests caught

The first version of that Redis-down test failed about 3 times out of 5 with `deadlock detected` (40P01). Without the Redis lock, all the concurrent inserts wrote their row and then waited on each other inside the exclusion constraint check. Postgres only breaks one of those cycles per `deadlock_timeout` (1s), and the victims came back as 500s. Nothing got double booked, but it was slow and the error was wrong. Retrying alone didn't fix it because the retries just piled up again.

The fix was taking `pg_advisory_xact_lock(hashtextextended('booking:{room}:{date}', 0))` at the start of each booking transaction, so writers for the same room and day queue up inside Postgres instead. It's released automatically on commit or rollback. I kept a small retry on 40P01/40001 as a backup.

## Why I Didn't Over-Engineer

| Decision | Went with | Instead of | Why |
|---|---|---|---|
| Overall shape | One FastAPI service + Postgres + Redis | Microservices, Kafka | Around 30 concurrent writers at peak. One process handles that easily. |
| Preventing double bookings | Exclusion constraint | Checking for overlaps in Python before inserting | A check-then-insert in app code is a race. The constraint holds no matter which code path writes. |
| Live updates | Redis Pub/Sub | RabbitMQ or Kafka | The events are just "refetch this day" hints. Losing one is fine because clients refetch on reconnect and on focus. Redis was already there. |
| Workers | One Uvicorn worker | Several workers | Prometheus metrics live in process memory, and one async worker is plenty for this load. |
| Cancelling | Soft delete (`status = 'CANCELLED'`) | Deleting the row | Keeps history, makes DELETE safe to repeat, and the constraint already ignores cancelled rows. |
| Caching | Cache the day grid for 300s, delete it on every write | No cache | The grid gets read way more than it's written. The TTL is just a safety net if a delete gets lost. |

## Quickstart

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000/d/roomsync (no login)

If something else on your machine already uses port 5432, set `POSTGRES_HOST_PORT=5433` in `.env`.

Tests (run inside the compose network against a separate `roomsync_test` database and Redis db 15):

```bash
docker compose run --rm \
  -e TEST_DATABASE_URL=postgresql+asyncpg://roomsync:roomsync@postgres:5432/roomsync_test \
  -e TEST_REDIS_URL=redis://redis:6379/15 \
  backend pytest -v
```

Load test and smoke test:

```bash
locust -f load_tests/locustfile.py --headless -u 30 -r 10 -t 60s --host http://localhost:8000 --csv load_tests/results
bash scripts/smoke_test.sh
```

## Deploying (free, one server)

I run it on an Oracle Cloud "Always Free" ARM VM with the same compose setup plus Caddy for HTTPS. Everything in the stack has ARM images.

What's different in `docker-compose.prod.yml`:

- Only Caddy is exposed (80/443). Postgres, Redis, Prometheus and Grafana are only reachable inside the docker network.
- The frontend is built once and served as static files by Caddy, same domain as the API (so no CORS and the websocket is just `wss://<domain>/api/v1/ws`).
- The whole site sits behind one shared password, since the app has no real login and anyone could cancel bookings otherwise.
- `/metrics` isn't public. Grafana is at `/grafana/`, read-only for visitors.
- Passwords come from `.env`, see `.env.prod.example`.

Steps:

1. **Make the VM.** Oracle Cloud > Compute > Create instance. Shape `VM.Standard.A1.Flex` (2 OCPU / 12 GB is plenty), image Ubuntu 24.04. If it says out of capacity, try again later or another availability domain.
2. **Open ports 80 and 443 in Oracle.** In the VM's subnet security list add ingress rules for TCP 80 and TCP 443 from `0.0.0.0/0`.
3. **Get a domain.** A free one from duckdns.org works: make a subdomain and point it at the VM's public IP.
4. **On the VM, clone and run the setup script.** It opens the VM's own firewall (Oracle's Ubuntu image blocks 80/443 in iptables), installs docker, asks for the domain and a site password, generates the other passwords into `.env`, and starts everything.
   ```bash
   git clone <your repo url> room-sync-engine && cd room-sync-engine
   bash deploy/server-setup.sh
   ```
   First build takes a few minutes. Caddy gets the HTTPS certificate on its own once the domain points at the server.

Updating later: `git pull && docker compose -f docker-compose.prod.yml up -d --build`. Migrations run on startup.

Oracle can reclaim Always Free VMs that sit almost idle for a week. Upgrading the account to Pay As You Go generally avoids that, and you're still not charged as long as you stay inside the free limits.

## Observability

The Grafana dashboard loads automatically and refreshes every 5 seconds.

1. **Request throughput and p95/p99 latency by endpoint.** Requests per second on the left axis, latency on the right. The table under it is sorted by max so the busiest endpoint is on top.
2. **Booking outcomes.** Stacked rates of `created`, `conflict_lock`, `conflict_db`, `idempotent_replay` and `error`. A contention spike looks like a tall orange bar with a thin green line on top: lots of people tried, one got each slot. `conflict_lock` means Redis turned them away, `conflict_db` means the constraint did. Anything in `error` is an actual problem.
3. **Connection pool.** Checked-out connections against the pool size (10) and the max (30). It sits near 0 because queries take a few ms and Prometheus only samples every 5s.
4. **Cache hit ratio and WebSocket clients.** Hit ratio dips after each booking (the day's grid gets cleared) and climbs back on the next reads. The purple line is how many tabs are connected.

![Grafana](docs/grafana.png)

## Results

From my machine (Windows 11, Docker Desktop), 2026-11-19.

### pytest

```text
============================= test session starts ==============================
platform linux -- Python 3.12.16, pytest-9.2.0, pluggy-1.7.0 -- /usr/local/bin/python3.12
cachedir: .pytest_cache
rootdir: /app
configfile: pytest.ini
testpaths: tests
plugins: locust-2.47.0, asyncio-1.5.0, anyio-4.16.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=session, asyncio_default_test_loop_scope=session
collecting ... collected 15 items

tests/test_concurrency.py::test_30_user_race_condition PASSED            [  6%]
tests/test_concurrency.py::test_back_to_back_half_open_interval PASSED   [ 13%]
tests/test_concurrency.py::test_idempotency_key_replay PASSED            [ 20%]
tests/test_concurrency.py::test_concurrent_same_idempotency_key PASSED   [ 26%]
tests/test_concurrency.py::test_idempotency_key_payload_mismatch PASSED  [ 33%]
tests/test_concurrency.py::test_cancel_frees_slot PASSED                 [ 40%]
tests/test_concurrency.py::test_patch_conflict_and_move PASSED           [ 46%]
tests/test_concurrency.py::test_availability_two_queries_and_cache PASSED [ 53%]
tests/test_concurrency.py::test_ghost_reclaimer PASSED                   [ 60%]
tests/test_concurrency.py::test_validation_rules PASSED                  [ 66%]
tests/test_concurrency.py::test_event_published_after_booking PASSED     [ 73%]
tests/test_concurrency.py::test_check_in_flow PASSED                     [ 80%]
tests/test_concurrency.py::test_redis_down_fails_open_and_db_still_prevents_double_booking PASSED [ 86%]
tests/test_concurrency.py::test_db_constraint_rejects_overlap_directly PASSED [ 93%]
tests/test_concurrency.py::test_rooms_are_fau_eecs_catalog PASSED        [100%]

============================== 15 passed in 3.96s ==============================
```

I ran it 5 times in a row, all green. The 30-user race gives exactly one 201, 29 409s and one row every time. As a sanity check I dropped the exclusion constraint on the test database once: 5 of the tests failed, so they really do depend on it.

### Locust

30 users for 3 minutes, run from the host:

| Endpoint | Requests | Failures | p50 | p95 | p99 |
|---|---|---|---|---|---|
| POST /api/v1/bookings | 822 | 0 | 62 ms | 76 ms | 88 ms |
| GET /api/v1/rooms/availability | 3385 | 0 | 7 ms | 12 ms | 25 ms |
| GET /api/v1/rooms | 30 | 0 | 41 ms | 75 ms | 75 ms |
| All | 4237 | 0 | 7 ms | 68 ms | 76 ms |

52 bookings created, 773 rejected with 409, no errors. Afterwards I checked the table for any two active bookings overlapping in the same room: zero.

The POST numbers look slow mostly because of Docker Desktop on Windows. Requests with a body going through its port forwarding pick up about 40 ms (a plain 404 POST took 45 ms from the host while GETs took 3 ms). Running the same locustfile inside the compose network, POST p50 was 7 to 22 ms depending on how busy my laptop was.

## Decisions and known limitations

Decisions:

- **Rooms.** 12 EECS spaces (conference room, thesis defense room, two senior design studios, AI/ML lab, cybersecurity lab, embedded systems lab, teaching lab, TA office hours nook, interview pod, two grad study rooms). The names are placeholders, not real FAU room numbers. They live in `backend/app/seed.py`. Migration `002` renames rooms in place, so existing bookings keep their room.
- **Branding.** FAU blue and red, text only. I didn't use the FAU logo.
- **Advisory lock plus retry** on every booking write, explained above.
- **Unknown room is checked first**, so a bad `room_id` is a 404 before anything touches Redis.
- **PATCH** that only changes the title skips the time validation and the lock (so you can rename a meeting that already started). Moving a booking re-runs all the validation.
- **Check-in** is allowed any time before the meeting ends. If the reclaimer releases it first, check-in returns 409.
- **The frontend idempotency key** is made when the modal opens and reused if you retry the same thing. If you change the form before retrying it makes a new one, otherwise the server would reject it as a different payload.
- **Postgres host port** can be changed with `POSTGRES_HOST_PORT` because I already had another Postgres on 5432.

Not in scope: login/permissions, multiple offices or timezones, recurring meetings, email notifications, running more than one backend process.

Known limitations:

- No auth, so anyone can cancel anyone's booking. The organizer is just a name and email typed in.
- One timezone (America/New_York). The grid always shows that, not the browser's timezone.
- One backend worker because metrics are in memory. Scaling out would need Prometheus multiprocess mode.
- A read that misses the cache can race a write and put slightly stale data back in the cache until the 300s TTL (or the next write). Fine for a room grid. A versioned cache key would fix it.
- Replaying an idempotency key returns the response from when it was created, even if the booking was cancelled later.
- Pub/Sub can drop a message. Tabs catch up when they reconnect, on window focus, or after 30s.
- Pub/Sub channels are shared across Redis db numbers, so running the tests while the app is open sends a few test events to open tabs.
- Grafana runs with anonymous admin. That's only OK for a local demo.
- I haven't tested beyond 30 users or tuned Postgres. Next steps would be PgBouncer, more workers, and a read replica for the grid query.
