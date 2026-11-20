from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def business_date(days_ahead: int = 1):
    tz = ZoneInfo(get_settings().BUSINESS_TZ)
    return (datetime.now(tz) + timedelta(days=days_ahead)).date()


def business_slot(hour: int, minute: int, duration_min: int, days_ahead: int = 1) -> tuple[str, str]:
    # e.g. business_slot(10, 0, 60) -> tomorrow 10:00-11:00 New York time, as UTC iso strings
    tz = ZoneInfo(get_settings().BUSINESS_TZ)
    d = business_date(days_ahead)
    start_local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=duration_min)
    return start_utc.isoformat().replace("+00:00", "Z"), end_utc.isoformat().replace("+00:00", "Z")


def booking_body(room_id: str, start: str, end: str, email: str = "alex@example.com", title: str = "Sync") -> dict:
    return {
        "room_id": room_id,
        "organizer_name": "Alex Doe",
        "organizer_email": email,
        "title": title,
        "start_time": start,
        "end_time": end,
    }
