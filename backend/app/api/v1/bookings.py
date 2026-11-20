import uuid

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from app.schemas.booking import BookingCreate, BookingOut, BookingUpdate
from app.services import booking_service

router = APIRouter(prefix="/bookings", tags=["bookings"])

_CONFLICT = {409: {"description": "Slot taken, high contention, or invalid state"}}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=BookingOut,
    responses={**_CONFLICT, 404: {"description": "Room not found"}},
)
async def create_booking(
    payload: BookingCreate,
    request: Request,
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    result = await booking_service.create_booking(
        request.app.state.session_factory, request.app.state.redis, payload, idempotency_key
    )
    headers = {"Idempotent-Replay": "true"} if result.replay else None
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=result.body, headers=headers)


@router.patch("/{booking_id}", response_model=BookingOut, responses=_CONFLICT)
async def update_booking(booking_id: uuid.UUID, patch: BookingUpdate, request: Request) -> JSONResponse:
    body = await booking_service.update_booking(
        request.app.state.session_factory, request.app.state.redis, booking_id, patch
    )
    return JSONResponse(content=body)


@router.post("/{booking_id}/check-in", response_model=BookingOut, responses=_CONFLICT)
async def check_in(booking_id: uuid.UUID, request: Request) -> JSONResponse:
    body = await booking_service.check_in(
        request.app.state.session_factory, request.app.state.redis, booking_id
    )
    return JSONResponse(content=body)


@router.delete("/{booking_id}", response_model=BookingOut)
async def cancel_booking(booking_id: uuid.UUID, request: Request) -> JSONResponse:
    body = await booking_service.cancel_booking(
        request.app.state.session_factory, request.app.state.redis, booking_id
    )
    return JSONResponse(content=body)
