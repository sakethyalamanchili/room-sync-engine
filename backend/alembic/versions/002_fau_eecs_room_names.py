"""rename rooms to EECS rooms (in place so existing bookings keep their room)

Revision ID: 002
Revises: 001
Create Date: 2026-11-19

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RENAMES: list[tuple[str, str]] = [
    ("Focus Pod 1", "Grad Study Room A"),
    ("Focus Pod 2", "Grad Study Room B"),
    ("Focus Pod 3", "Remote Interview Pod"),
    ("Huddle Nook", "TA Office Hours Nook"),
    ("Maple Room", "Embedded Systems Lab"),
    ("Cedar Room", "Cybersecurity Lab"),
    ("War Room A", "Senior Design Studio A"),
    ("War Room B", "Senior Design Studio B"),
    ("Training Lab", "Teaching Computer Lab"),
    ("Innovation Studio", "AI and Machine Learning Lab"),
    ("Boardroom Beta", "Thesis Defense Room"),
    ("Boardroom Alpha", "EECS Conference Room"),
]

_RENAME = sa.text("UPDATE rooms SET name = :new WHERE name = :old")


def upgrade() -> None:
    bind = op.get_bind()
    for old, new in RENAMES:
        bind.execute(_RENAME, {"old": old, "new": new})


def downgrade() -> None:
    bind = op.get_bind()
    for old, new in RENAMES:
        bind.execute(_RENAME, {"old": new, "new": old})
