"""Tests for weather API parsing and failover logic."""
from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

from src.weather import (
    _fetch_open_meteo,
    _fetch_nws,
    _parse_nws_wind,
    fetch_weather,
    fetch_nws_alerts,
    filter_slices,
    HourlySlice,
)

_TZ = ZoneInfo("America/Boise")
_WIN_START = datetime(2026, 3, 26, 6, 0, tzinfo=_TZ)
_WIN_END = datetime(2026, 3, 26, 16, 0, tzinfo=_TZ)
_LAT = 43.6121
_LON = -116.3915


# ---------------------------------------------------------------------------
# _parse_nws_wind
# ---------------------------------------------------------------------------

def test_parse_nws_wind_simple():
    assert _parse_nws_wind("10 mph") == 10.0


def test_parse_nws_wind_range():
    assert _parse_nws_wind("5 to 15 mph") == 15.0


def test_parse_nws_wind_zero():
    assert _parse_nws_wind("Calm") == 0.0


def test_parse_nws_wind_gusts():
    assert _parse_nws_wind("20 mph") == 20.0


# ---------------------------------------------------------------------------
# Open-Meteo parsing
# ---------------------------------------------------------------------------

def _open_meteo_response(hours: list[str], temp: float = 65.0) -> dict:
    n = len(hours)
    return {
        "hourly": {
            "time": hours,
            "temperature_2m": [temp] * n,
            "precipitation_probability": [10] * n,
            "precipitation": [0.0] * n,
            "weathercode": [1] * n,
            "windspeed_10m": [8.0] * n,
            "windgusts_10m": [12.0] * n,
        }
    }


def test_open_meteo_filters_to_window():
    hours = [f"2026-03-26T{h:02d}:00" for h in range(0, 24)]
    payload = _open_meteo_response(hours)
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("src.weather.requests.get", return_value=mock_resp):
        slices = _fetch_open_meteo(_WIN_START, _WIN_END, _LAT, _LON)

    assert all(_WIN_START <= s.time < _WIN_END for s in slices)
    assert len(slices) == 10  # hours 6-15 inclusive


def test_open_meteo_no_data_in_window_raises():
    hours = [f"2026-03-27T{h:02d}:00" for h in range(0, 24)]  # tomorrow
    payload = _open_meteo_response(hours)
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("src.weather.requests.get", return_value=mock_resp):
        with pytest.raises(ValueError, match="No hourly data"):
            _fetch_open_meteo(_WIN_START, _WIN_END, _LAT, _LON)


def test_open_meteo_has_precip_flag():
    hours = [f"2026-03-26T{h:02d}:00" for h in range(6, 16)]
    payload = _open_meteo_response(hours)
    payload["hourly"]["weathercode"][3] = 63  # Moderate rain at hour 9
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("src.weather.requests.get", return_value=mock_resp):
        slices = _fetch_open_meteo(_WIN_START, _WIN_END, _LAT, _LON)

    assert slices[3].has_precip is True
    assert slices[0].has_precip is False


# ---------------------------------------------------------------------------
# NWS parsing
# ---------------------------------------------------------------------------

def _nws_points_response() -> dict:
    return {"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/BOI/89,97/forecast/hourly"}}


def _nws_hourly_response(hours: list[str]) -> dict:
    periods = []
    for h_str in hours:
        end_str = h_str.replace(":00:00", ":00:00")  # same format, just increment mentally
        periods.append({
            "startTime": h_str,
            "endTime": h_str,
            "temperature": 65,
            "temperatureUnit": "F",
            "windSpeed": "10 mph",
            "windGust": None,
            "windDirection": "NW",
            "isDaytime": True,
            "shortForecast": "Mostly Sunny",
            "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None},
        })
    return {"properties": {"periods": periods}}


def test_nws_filters_to_window():
    all_hours = [f"2026-03-26T{h:02d}:00:00-06:00" for h in range(0, 24)]
    pts_mock = MagicMock()
    pts_mock.json.return_value = _nws_points_response()
    pts_mock.raise_for_status.return_value = None
    fc_mock = MagicMock()
    fc_mock.json.return_value = _nws_hourly_response(all_hours)
    fc_mock.raise_for_status.return_value = None

    with patch("src.weather.requests.get", side_effect=[pts_mock, fc_mock]):
        slices = _fetch_nws(_WIN_START, _WIN_END, _LAT, _LON)

    assert all(_WIN_START <= s.time < _WIN_END for s in slices)
    assert len(slices) == 10


def test_nws_precip_detected_from_short_forecast():
    hours = [f"2026-03-26T{h:02d}:00:00-06:00" for h in range(6, 16)]
    payload = _nws_hourly_response(hours)
    payload["properties"]["periods"][3]["shortForecast"] = "Rain and Thunder"

    pts_mock = MagicMock()
    pts_mock.json.return_value = _nws_points_response()
    pts_mock.raise_for_status.return_value = None
    fc_mock = MagicMock()
    fc_mock.json.return_value = payload
    fc_mock.raise_for_status.return_value = None

    with patch("src.weather.requests.get", side_effect=[pts_mock, fc_mock]):
        slices = _fetch_nws(_WIN_START, _WIN_END, _LAT, _LON)

    assert slices[3].has_precip is True
    assert slices[0].has_precip is False


# ---------------------------------------------------------------------------
# Failover logic
# ---------------------------------------------------------------------------

def test_fetch_weather_falls_back_to_nws():
    # Boise is MDT (UTC-6) in late March after DST change
    all_hours = [f"2026-03-26T{h:02d}:00:00-06:00" for h in range(6, 16)]
    pts_mock = MagicMock()
    pts_mock.json.return_value = _nws_points_response()
    pts_mock.raise_for_status.return_value = None
    fc_mock = MagicMock()
    fc_mock.json.return_value = _nws_hourly_response(all_hours)
    fc_mock.raise_for_status.return_value = None

    with patch("src.weather._fetch_open_meteo", side_effect=RuntimeError("timeout")):
        with patch("src.weather.requests.get", side_effect=[pts_mock, fc_mock]):
            slices = fetch_weather(_WIN_START, _WIN_END, _LAT, _LON)

    assert len(slices) == 10


def test_fetch_weather_raises_when_both_fail():
    with patch("src.weather._fetch_open_meteo", side_effect=RuntimeError("timeout")):
        with patch("src.weather._fetch_nws", side_effect=RuntimeError("NWS down")):
            with pytest.raises(RuntimeError, match="Both weather sources unavailable"):
                fetch_weather(_WIN_START, _WIN_END, _LAT, _LON)


# ---------------------------------------------------------------------------
# NWS alerts
# ---------------------------------------------------------------------------

def test_fetch_nws_alerts_returns_event_names():
    payload = {
        "features": [
            {"properties": {"event": "Dense Fog Advisory"}},
            {"properties": {"event": "Wind Advisory"}},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("src.weather.requests.get", return_value=mock_resp):
        alerts = fetch_nws_alerts(_LAT, _LON)

    assert alerts == ["Dense Fog Advisory", "Wind Advisory"]


def test_fetch_nws_alerts_empty_when_none_active():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"features": []}
    mock_resp.raise_for_status.return_value = None

    with patch("src.weather.requests.get", return_value=mock_resp):
        alerts = fetch_nws_alerts(_LAT, _LON)

    assert alerts == []


def test_fetch_nws_alerts_returns_empty_on_failure():
    with patch("src.weather.requests.get", side_effect=ConnectionError("network down")):
        alerts = fetch_nws_alerts(_LAT, _LON)

    assert alerts == []


# ---------------------------------------------------------------------------
# filter_slices
# ---------------------------------------------------------------------------

def _make_slice(hour: int) -> HourlySlice:
    return HourlySlice(
        time=datetime(2026, 5, 14, hour, 0, tzinfo=_TZ),
        temp_f=65.0, wind_mph=8.0, gust_mph=12.0,
        precip_mm=0.0, precip_prob=10, weather_code=1,
        description="Clear", has_precip=False,
    )


def test_filter_slices_returns_within_window():
    slices = [_make_slice(h) for h in range(0, 24)]
    start = datetime(2026, 5, 14, 6, 0, tzinfo=_TZ)
    end = datetime(2026, 5, 14, 18, 0, tzinfo=_TZ)
    result = filter_slices(slices, start, end)
    assert len(result) == 12
    assert all(start <= s.time < end for s in result)


def test_filter_slices_excludes_boundary_end():
    slices = [_make_slice(h) for h in range(0, 24)]
    start = datetime(2026, 5, 14, 6, 0, tzinfo=_TZ)
    end = datetime(2026, 5, 14, 6, 0, tzinfo=_TZ)  # zero-width window
    result = filter_slices(slices, start, end)
    assert result == []


def test_filter_slices_empty_input():
    start = datetime(2026, 5, 14, 6, 0, tzinfo=_TZ)
    end = datetime(2026, 5, 14, 18, 0, tzinfo=_TZ)
    assert filter_slices([], start, end) == []
