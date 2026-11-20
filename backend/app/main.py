# Keep this at 1 uvicorn worker, prometheus metrics are in-process.

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import create_engine, create_session_factory
from app.core.redis import create_redis
from app.core.telemetry import preinitialize_metrics, setup_instrumentator
from app.seed import seed_rooms
from app.services.ghost_reclaimer import run_loop
from app.services.pubsub_manager import ConnectionManager, listen_forever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("roomsync")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = create_redis(settings)
    app.state.ws_manager = ConnectionManager()
    preinitialize_metrics(settings.DB_POOL_SIZE, settings.DB_MAX_OVERFLOW)

    await seed_rooms(app.state.session_factory)

    tasks: list[asyncio.Task] = []
    if settings.ENABLE_BACKGROUND_TASKS:
        tasks.append(asyncio.create_task(listen_forever(settings.REDIS_URL, app.state.ws_manager), name="pubsub"))
        tasks.append(asyncio.create_task(run_loop(app.state.session_factory, app.state.redis), name="ghost"))
    logger.info("RoomSync started (background tasks: %s)", settings.ENABLE_BACKGROUND_TASKS)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.redis.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="FAU EECS RoomSync Engine",
        version="1.0.0",
        description="High-concurrency room scheduling for the FAU Department of Electrical Engineering and Computer Science.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Idempotent-Replay"],
    )
    app.include_router(api_router)

    @app.get("/health", tags=["health"])
    async def health(request: Request) -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc.__class__.__name__}"
        try:
            await request.app.state.redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc.__class__.__name__}"
        if all(v == "ok" for v in checks.values()):
            return JSONResponse({"status": "ok"})
        return JSONResponse({"status": "unavailable", "checks": checks}, status_code=503)

    setup_instrumentator(app)
    return app


app = create_app()
