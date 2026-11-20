import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("datetime must be timezone-aware (include an offset or 'Z')")
    return value.astimezone(timezone.utc)


class BookingCreate(BaseModel):
    room_id: uuid.UUID
    organizer_name: str = Field(min_length=1, max_length=120)
    organizer_email: EmailStr = Field(max_length=180)
    title: str = Field(min_length=1, max_length=255)
    start_time: datetime
    end_time: datetime

    @field_validator("organizer_name", "title")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return _aware_utc(v)

    @model_validator(mode="after")
    def _order(self) -> "BookingCreate":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class BookingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    room_id: uuid.UUID | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _aware_utc(v)

    @model_validator(mode="after")
    def _non_empty(self) -> "BookingUpdate":
        if not self.model_fields_set or all(
            getattr(self, f) is None for f in ("title", "room_id", "start_time", "end_time")
        ):
            raise ValueError("at least one of title, room_id, start_time, end_time is required")
        return self


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    room_id: uuid.UUID
    organizer_name: str
    organizer_email: str
    title: str
    start_time: datetime
    end_time: datetime
    status: str
    created_at: datetime
    updated_at: datetime
