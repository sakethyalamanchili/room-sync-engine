import asyncio
import logging

import redis.asyncio as aioredis
from fastapi import WebSocket

from app.core.redis import ROOM_EVENTS_CHANNEL
from app.core.telemetry import active_websocket_clients

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 5


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()

    @property
    def count(self) -> int:
        return len(self._sockets)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._sockets.add(ws)
        active_websocket_clients.inc()

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._sockets:
            self._sockets.discard(ws)
            active_websocket_clients.dec()

    async def _send(self, ws: WebSocket, message: str) -> None:
        try:
            await asyncio.wait_for(ws.send_text(message), timeout=SEND_TIMEOUT_SECONDS)
        except Exception as exc:  # dead or slow socket
            logger.info("Dropping websocket after send failure: %s", exc)
            self.disconnect(ws)
            try:
                await ws.close()
            except Exception:
                pass

    async def broadcast(self, message: str) -> None:
        sockets = list(self._sockets)
        if sockets:
            await asyncio.gather(*(self._send(ws, message) for ws in sockets))


async def listen_forever(redis_url: str, manager: ConnectionManager) -> None:
    # reconnects with backoff if redis goes away
    backoff = 1.0
    while True:
        client: aioredis.Redis | None = None
        pubsub = None
        try:
            client = aioredis.from_url(redis_url, decode_responses=True, health_check_interval=30)
            pubsub = client.pubsub()
            await pubsub.subscribe(ROOM_EVENTS_CHANNEL)
            logger.info("Subscribed to Redis channel %s", ROOM_EVENTS_CHANNEL)
            backoff = 1.0
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    await manager.broadcast(msg["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Pub/Sub listener error, reconnecting in %.0fs: %s", backoff, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            for closer in (pubsub, client):
                if closer is not None:
                    try:
                        await closer.aclose()
                    except Exception:
                        pass
