from datetime import date, datetime
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun

_LOCATION = LocationInfo(
    name="Meridian",
    region="US",
    timezone="America/Boise",
    latitude=43.6121,
    longitude=-116.3915,
)
_BOISE_TZ = ZoneInfo("America/Boise")


def get_sunrise_sunset(for_date: date) -> tuple[datetime, datetime]:
    """Return (sunrise, sunset) as timezone-aware datetimes in Boise local time."""
    s = sun(_LOCATION.observer, date=for_date, tzinfo=_BOISE_TZ)
    return s["sunrise"], s["sunset"]
