import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Amenities(BaseModel):
    projector: bool
    whiteboard: bool
    video_conf: bool


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    floor: int
    capacity: int
    amenities: Amenities


class AvailabilityBooking(BaseModel):
    # no organizer_email here, everyone can see the grid
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    organizer_name: str
    start_time: datetime
    end_time: datetime
    status: str


class RoomAvailability(RoomOut):
    bookings: list[AvailabilityBooking]


class BusinessHours(BaseModel):
    start: str
    end: str
    slot_minutes: int


class AvailabilityResponse(BaseModel):
    date: str
    timezone: str
    business_hours: BusinessHours
    rooms: list[RoomAvailability]
