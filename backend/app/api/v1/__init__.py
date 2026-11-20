from fastapi import APIRouter

from app.api.v1 import bookings, rooms, ws

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(rooms.router)
api_router.include_router(bookings.router)
api_router.include_router(ws.router)

__all__ = ["api_router"]
