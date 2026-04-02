"""Tests for GO/CAUTION/NO-GO condition evaluation logic."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.weather import HourlySlice
from src.conditions import evaluate

_TZ = ZoneInfo("America/Boise")


def _make_slice(
    hour: int,
    temp_f: float = 65.0,
    wind_mph: float = 10.0,
    gust_mph: float = 15.0,
    precip_mm: float = 0.0,
    precip_prob: int = 0,
    weather_code: int = 1,
    has_precip: bool = False,
    description: str = "Partly cloudy",
) -> HourlySlice:
    return HourlySlice(
        time=datetime(2026, 3, 26, hour, 0, tzinfo=_TZ),
        temp_f=temp_f,
        wind_mph=wind_mph,
        gust_mph=gust_mph,
        precip_mm=precip_mm,
        precip_prob=precip_prob,
        weather_code=weather_code,
        description=description,
        has_precip=has_precip,
    )


def _base_slices(hours: range = range(6, 16), **kwargs) -> list[HourlySlice]:
    return [_make_slice(h, **kwargs) for h in hours]


_SUNRISE = datetime(2026, 3, 26, 7, 30, tzinfo=_TZ)
_SUNSET = datetime(2026, 3, 26, 19, 48, tzinfo=_TZ)
_WIN_START = datetime(2026, 3, 26, 6, 0, tzinfo=_TZ)
_WIN_END = datetime(2026, 3, 26, 16, 0, tzinfo=_TZ)


def _evaluate(slices, overnight=None):
    return evaluate(
        slices=slices,
        sunrise=_SUNRISE,
        sunset=_SUNSET,
        window_start=_WIN_START,
        window_end=_WIN_END,
        overnight_slices=overnight,
    )


# ---------------------------------------------------------------------------
# GO cases
# ---------------------------------------------------------------------------

def _evaluate_go(slices, overnight=None):
    """Evaluate with a post-sunrise window to avoid darkness CAUTION triggers."""
    win_start = datetime(2026, 3, 26, 8, 0, tzinfo=_TZ)   # after 7:30 AM sunrise
    win_end = datetime(2026, 3, 26, 18, 0, tzinfo=_TZ)    # before 7:48 PM sunset
    return evaluate(
        slices=slices,
        sunrise=_SUNRISE,
        sunset=_SUNSET,
        window_start=win_start,
        window_end=win_end,
        overnight_slices=overnight,
    )


def test_go_perfect_conditions():
    result = _evaluate_go(_base_slices(temp_f=70.0, wind_mph=8.0, gust_mph=12.0))
    assert result.status == "GO"
    assert not result.nogo_reasons
    assert not result.caution_notes


def test_go_temp_at_boundary_above():
    """Exactly 50°F is above the CAUTION threshold → GO."""
    result = _evaluate_go(_base_slices(temp_f=50.0))
    assert result.status == "GO"


def test_go_wind_at_boundary():
    """Exactly 40 mph gust is the CAUTION floor — but gust == wind, so wind_max == 40 == CAUTION_LOW."""
    slices = _base_slices(wind_mph=40.0, gust_mph=40.0)
    result = _evaluate(slices)
    assert result.status == "CAUTION"


# ---------------------------------------------------------------------------
# CAUTION cases
# ---------------------------------------------------------------------------

def test_caution_low_temp():
    slices = _base_slices()
    slices[0] = _make_slice(6, temp_f=47.0)  # one cold hour
    result = _evaluate(slices)
    assert result.status == "CAUTION"
    assert any("47°F" in n for n in result.caution_notes)


def test_caution_wind_gusts():
    slices = _base_slices(gust_mph=45.0)
    result = _evaluate(slices)
    assert result.status == "CAUTION"
    assert any("45" in n for n in result.caution_notes)


def test_caution_elevated_precip_probability():
    slices = _base_slices(precip_prob=40)
    result = _evaluate(slices)
    assert result.status == "CAUTION"
    assert any("40%" in n for n in result.caution_notes)


def test_caution_overnight_rain():
    slices = _base_slices()
    overnight = [_make_slice(3, has_precip=True, description="Rain")]
    result = _evaluate(slices, overnight=overnight)
    assert result.status == "CAUTION"
    assert any("wet" in n.lower() for n in result.caution_notes)


def test_caution_window_before_sunrise():
    """Window starts before sunrise → consolidated reduced visibility note."""
    slices = _base_slices()
    # _WIN_START is 6:00 AM, _SUNRISE is 7:30 AM → partial darkness
    result = _evaluate(slices)
    assert result.status == "CAUTION"
    assert any("Reduced visibility" in n for n in result.caution_notes)
    # Single note, not two separate ones
    dark_notes = [n for n in result.caution_notes if "visibility" in n.lower()]
    assert len(dark_notes) == 1
    assert "sunrise" in dark_notes[0] and "sunset" in dark_notes[0]


def test_caution_window_after_sunset():
    """Window extends past sunset → reduced visibility note."""
    win_start = datetime(2026, 3, 26, 10, 0, tzinfo=_TZ)
    win_end = datetime(2026, 3, 26, 22, 0, tzinfo=_TZ)   # past 7:48 PM sunset
    result = evaluate(
        slices=_base_slices(),
        sunrise=_SUNRISE,
        sunset=_SUNSET,
        window_start=win_start,
        window_end=win_end,
    )
    assert result.status == "CAUTION"
    assert any("Reduced visibility" in n for n in result.caution_notes)


def test_nogo_nws_alert():
    """Active NWS alert triggers NO-GO."""
    slices = _base_slices(temp_f=65.0, wind_mph=10.0, gust_mph=12.0)
    win_start = datetime(2026, 3, 26, 8, 0, tzinfo=_TZ)
    win_end = datetime(2026, 3, 26, 18, 0, tzinfo=_TZ)
    result = evaluate(
        slices=slices,
        sunrise=_SUNRISE,
        sunset=_SUNSET,
        window_start=win_start,
        window_end=win_end,
        nws_alerts=["Dense Fog Advisory"],
    )
    assert result.status == "NO-GO"
    assert any("Dense Fog Advisory" in r for r in result.nogo_reasons)


def test_nws_alerts_none_does_not_affect_go():
    """No alerts → GO conditions unaffected."""
    slices = _base_slices(temp_f=65.0, wind_mph=10.0, gust_mph=12.0)
    win_start = datetime(2026, 3, 26, 8, 0, tzinfo=_TZ)
    win_end = datetime(2026, 3, 26, 18, 0, tzinfo=_TZ)
    result = evaluate(
        slices=slices,
        sunrise=_SUNRISE,
        sunset=_SUNSET,
        window_start=win_start,
        window_end=win_end,
        nws_alerts=None,
    )
    assert result.status == "GO"


# ---------------------------------------------------------------------------
# NO-GO cases
# ---------------------------------------------------------------------------

def test_nogo_temperature_below_45():
    slices = _base_slices()
    slices[0] = _make_slice(6, temp_f=38.0)
    result = _evaluate(slices)
    assert result.status == "NO-GO"
    assert any("below 45°F" in r for r in result.nogo_reasons)


def test_nogo_temp_44_point_9_is_caution_not_nogo():
    """44.9°F rounds to 45 for display — should be CAUTION, not NO-GO."""
    slices = _base_slices()
    slices[0] = _make_slice(6, temp_f=44.9)
    result = _evaluate_go([_make_slice(h, temp_f=44.9) for h in range(8, 16)])
    assert result.status == "CAUTION"
    assert not result.nogo_reasons


def test_nogo_temp_44_is_nogo():
    """44.0°F rounds to 44 — should still be NO-GO."""
    result = _evaluate_go([_make_slice(h, temp_f=44.0) for h in range(8, 16)])
    assert result.status == "NO-GO"


def test_nogo_rain_in_forecast():
    slices = _base_slices()
    slices[3] = _make_slice(9, has_precip=True, weather_code=63, description="Moderate rain")
    result = _evaluate(slices)
    assert result.status == "NO-GO"
    assert any("Rain" in r for r in result.nogo_reasons)


def test_nogo_rain_single_hour_shows_start_time():
    """Single rain hour should show 'starting HH:MM' not 'HH:MM - HH:MM'."""
    slices = _base_slices()
    slices[3] = _make_slice(9, has_precip=True, weather_code=63, description="Moderate rain")
    result = _evaluate(slices)
    assert result.precip_window.startswith("starting ")
    assert " - " not in result.precip_window


def test_nogo_rain_multi_hour_shows_range():
    """Multiple rain hours should show a time range."""
    slices = _base_slices()
    slices[3] = _make_slice(9, has_precip=True, weather_code=63, description="Moderate rain")
    slices[4] = _make_slice(10, has_precip=True, weather_code=63, description="Moderate rain")
    slices[5] = _make_slice(11, has_precip=True, weather_code=63, description="Moderate rain")
    result = _evaluate(slices)
    assert " - " in result.precip_window


def test_nogo_snow_in_forecast():
    slices = _base_slices()
    slices[3] = _make_slice(9, has_precip=True, weather_code=73, description="Moderate snow")
    result = _evaluate(slices)
    assert result.status == "NO-GO"
    assert any("precipitation" in r.lower() for r in result.nogo_reasons)


def test_nogo_thunderstorm():
    slices = _base_slices()
    slices[3] = _make_slice(9, has_precip=True, weather_code=95, description="Thunderstorm")
    result = _evaluate(slices)
    assert result.status == "NO-GO"
    assert any("thunder" in r.lower() for r in result.nogo_reasons)


def test_nogo_high_gust():
    slices = _base_slices(wind_mph=30.0, gust_mph=55.0)
    result = _evaluate(slices)
    assert result.status == "NO-GO"
    assert any("55" in r for r in result.nogo_reasons)


def test_nogo_high_sustained_wind():
    slices = _base_slices(wind_mph=52.0, gust_mph=52.0)
    result = _evaluate(slices)
    assert result.status == "NO-GO"


def test_nogo_overrides_caution_factors():
    """A cold temp that would be CAUTION becomes irrelevant under a NO-GO rain trigger."""
    slices = _base_slices(temp_f=47.0)
    slices[3] = _make_slice(9, temp_f=47.0, has_precip=True, weather_code=61, description="Slight rain")
    result = _evaluate(slices)
    assert result.status == "NO-GO"
    assert not result.caution_notes  # caution notes suppressed under NO-GO


# ---------------------------------------------------------------------------
# Assessment data
# ---------------------------------------------------------------------------

def test_assessment_temp_range():
    slices = [_make_slice(h, temp_f=float(55 + h)) for h in range(6, 16)]
    result = _evaluate(slices)
    assert result.temp_min == 61.0  # hour 6 → 55+6=61
    assert result.temp_max == 70.0  # hour 15 → 55+15=70


def test_assessment_gust_max():
    slices = _base_slices(gust_mph=22.0)
    slices[5] = _make_slice(11, gust_mph=35.0)
    result = _evaluate(slices)
    assert result.gust_max == 35.0
