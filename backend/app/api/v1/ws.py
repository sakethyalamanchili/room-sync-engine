import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.pubsub_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager
    await manager.connect(ws)
    try:
        while True:
            message = await ws.receive_text()
            if message == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.info("WebSocket closed with error: %s", exc)
    finally:
        manager.disconnect(ws)
