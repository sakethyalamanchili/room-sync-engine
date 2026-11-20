import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.booking import Booking


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    floor: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    amenities: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # raise instead of lazy loading (lazy loads blow up under async anyway)
    bookings: Mapped[list["Booking"]] = relationship(back_populates="room", lazy="raise")
