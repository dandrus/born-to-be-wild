import functools
from datetime import date

import holidays as _holidays


@functools.lru_cache(maxsize=4)
def _federal_holidays(year: int) -> _holidays.HolidayBase:
    return _holidays.UnitedStates(years=year)


def is_us_federal_holiday(d: date) -> bool:
    return d in _federal_holidays(d.year)


def is_off_season(d: date) -> bool:
    """Return True during the winter off-season: Nov 30 – Feb 28/29."""
    return d.month < 3 or d.month == 12 or (d.month == 11 and d.day >= 30)


def is_skip_day(d: date) -> bool:
    """Return True if email should be skipped: off-season, weekend, or US federal holiday."""
    return is_off_season(d) or d.weekday() >= 5 or is_us_federal_holiday(d)
