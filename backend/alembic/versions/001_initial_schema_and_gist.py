"""Initial schema: rooms, bookings, btree_gist exclusion constraint.

Revision ID: 001
Revises:
Create Date: 2026-11-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")

    op.create_table(
        "rooms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("floor", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("amenities", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "bookings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "room_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organizer_name", sa.String(120), nullable=False),
        sa.Column("organizer_email", sa.String(180), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'CONFIRMED'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("end_time > start_time", name="ck_bookings_end_after_start"),
        sa.CheckConstraint(
            "status IN ('CONFIRMED','CHECKED_IN','CANCELLED','AUTO_RELEASED')",
            name="ck_bookings_status_valid",
        ),
    )

    # [) so 10-11 and 11-12 don't conflict. any real overlap -> 23P01
    op.execute(
        """
        ALTER TABLE bookings
        ADD CONSTRAINT no_overlapping_bookings
        EXCLUDE USING gist (
          room_id WITH =,
          tstzrange(start_time, end_time, '[)') WITH &&
        ) WHERE (status IN ('CONFIRMED', 'CHECKED_IN'));
        """
    )

    op.execute("CREATE INDEX idx_bookings_room_date ON bookings (room_id, start_time, end_time);")


def downgrade() -> None:
    op.execute("ALTER TABLE bookings DROP CONSTRAINT IF EXISTS no_overlapping_bookings;")
    op.execute("DROP INDEX IF EXISTS idx_bookings_room_date;")
    op.drop_table("bookings")
    op.drop_table("rooms")
    # leaving btree_gist installed
