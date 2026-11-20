import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.room import Room

ACTIVE_STATUSES = ("CONFIRMED", "CHECKED_IN")


class Booking(Base):
    # constraints here just mirror migration 001
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_bookings_end_after_start"),
        CheckConstraint(
            "status IN ('CONFIRMED','CHECKED_IN','CANCELLED','AUTO_RELEASED')",
            name="ck_bookings_status_valid",
        ),
        ExcludeConstraint(
            ("room_id", "="),
            (text("tstzrange(start_time, end_time, '[)')"), "&&"),
            name="no_overlapping_bookings",
            using="gist",
            where=text("status IN ('CONFIRMED', 'CHECKED_IN')"),
        ),
        Index("idx_bookings_room_date", "room_id", "start_time", "end_time"),
    )
    # get created_at/updated_at back from the insert
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    organizer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    organizer_email: Mapped[str] = mapped_column(String(180), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'CONFIRMED'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    room: Mapped["Room"] = relationship(back_populates="bookings", lazy="raise")
