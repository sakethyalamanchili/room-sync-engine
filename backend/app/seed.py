# Rooms for the EECS dept. Runs on every startup, safe to re-run.
# TODO: swap in the real room numbers

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Room


def _amenities(*keys: str) -> dict[str, bool]:
    return {k: k in keys for k in ("projector", "whiteboard", "video_conf")}


ROOMS: list[dict] = [
    {"name": "Grad Study Room A", "floor": 1, "capacity": 2, "amenities": _amenities("whiteboard")},
    {"name": "Grad Study Room B", "floor": 1, "capacity": 2, "amenities": _amenities("whiteboard")},
    {"name": "Remote Interview Pod", "floor": 1, "capacity": 2, "amenities": _amenities("video_conf")},
    {"name": "TA Office Hours Nook", "floor": 1, "capacity": 4, "amenities": _amenities("video_conf", "whiteboard")},
    {"name": "Embedded Systems Lab", "floor": 2, "capacity": 6, "amenities": _amenities("projector", "whiteboard")},
    {"name": "Cybersecurity Lab", "floor": 2, "capacity": 8, "amenities": _amenities("projector", "video_conf")},
    {"name": "Senior Design Studio A", "floor": 3, "capacity": 10, "amenities": _amenities("projector", "whiteboard", "video_conf")},
    {"name": "Senior Design Studio B", "floor": 3, "capacity": 10, "amenities": _amenities("projector", "whiteboard", "video_conf")},
    {"name": "Teaching Computer Lab", "floor": 3, "capacity": 24, "amenities": _amenities("projector", "whiteboard")},
    {"name": "AI and Machine Learning Lab", "floor": 4, "capacity": 14, "amenities": _amenities("projector", "whiteboard", "video_conf")},
    {"name": "Thesis Defense Room", "floor": 5, "capacity": 12, "amenities": _amenities("projector", "video_conf")},
    {"name": "EECS Conference Room", "floor": 5, "capacity": 20, "amenities": _amenities("projector", "whiteboard", "video_conf")},
]


async def seed_rooms(session_factory: async_sessionmaker) -> None:
    stmt = insert(Room).values(ROOMS).on_conflict_do_nothing(index_elements=["name"])
    async with session_factory() as session, session.begin():
        await session.execute(stmt)
