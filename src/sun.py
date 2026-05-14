from datetime import date, datetime
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun


def get_sunrise_sunset(
    for_date: date,
    lat: float = 43.6121,
    lon: float = -116.3915,
    timezone: str = "America/Boise",
) -> tuple[datetime, datetime]:
    """Return (sunrise, sunset) as timezone-aware datetimes in local time."""
    tz = ZoneInfo(timezone)
    location = LocationInfo(latitude=lat, longitude=lon, timezone=timezone)
    s = sun(location.observer, date=for_date, tzinfo=tz)
    return s["sunrise"], s["sunset"]
