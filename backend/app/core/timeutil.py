# Everything is stored in UTC. A "date" always means a day in the office timezone
# (BUSINESS_TZ), so all date logic should go through here.

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def business_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().BUSINESS_TZ)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_business_local(dt_utc: datetime) -> datetime:
    if dt_utc.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt_utc.astimezone(business_tz())


def local_date_of(dt_utc: datetime) -> date:
    return to_business_local(dt_utc).date()


def day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    # local midnight -> next local midnight, in utc. works across DST changes
    tz = business_tz()
    start_local = datetime.combine(d, time(0, 0), tzinfo=tz)
    end_local = datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
