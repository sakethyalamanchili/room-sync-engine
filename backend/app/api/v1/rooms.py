from datetime import date

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.models import Room
from app.schemas.room import AvailabilityResponse, RoomOut
from app.services.availability_service import get_availability_json

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.get("", response_model=list[RoomOut])
async def list_rooms(request: Request) -> list[Room]:
    async with request.app.state.session_factory() as session:
        result = await session.execute(select(Room).order_by(Room.floor, Room.name))
        return list(result.scalars().all())


@router.get(
    "/availability",
    response_model=AvailabilityResponse,
    responses={200: {"description": "Availability grid for a business-local date"}},
)
async def availability(
    request: Request,
    date_: date = Query(..., alias="date", description="Business-local date, YYYY-MM-DD"),
) -> Response:
    payload = await get_availability_json(
        request.app.state.session_factory, request.app.state.redis, date_
    )
    return Response(content=payload, media_type="application/json")
