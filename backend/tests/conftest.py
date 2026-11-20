# Tests use their own db (roomsync_test) and redis db 15.

import os
import subprocess
import sys
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://roomsync:roomsync@localhost:5432/roomsync_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

# has to happen before importing the app (settings are cached)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["ENABLE_BACKGROUND_TASKS"] = "false"

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
async def database() -> str:
    url = make_url(TEST_DATABASE_URL)
    admin = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port or 5432,
        database="postgres",
    )
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await admin.close()

    # alembic's env.py calls asyncio.run(), can't do that inside the test loop
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        check=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def app(database):
    get_settings.cache_clear()
    from app.main import create_app

    application = create_app()
    async with LifespanManager(application, startup_timeout=30, shutdown_timeout=30):
        yield application


@pytest.fixture(scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
async def redis(app):
    return app.state.redis


@pytest.fixture(scope="session")
async def session_factory(app):
    return app.state.session_factory


@pytest.fixture(autouse=True)
async def clean_state(app):
    from app.seed import seed_rooms

    async with app.state.engine.begin() as conn:
        await conn.execute(text("TRUNCATE bookings"))
    await app.state.redis.flushdb()
    await seed_rooms(app.state.session_factory)
    yield


@pytest.fixture
async def rooms(client) -> list[dict]:
    resp = await client.get("/api/v1/rooms")
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture
async def room_id(rooms) -> str:
    return next(r["id"] for r in rooms if r["name"] == "EECS Conference Room")
