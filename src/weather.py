from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config  # used for API_TIMEOUT

log = logging.getLogger(__name__)
_BOISE_TZ = ZoneInfo("America/Boise")

# WMO weather codes that indicate precipitation (used for NO-GO evaluation)
_PRECIP_CODES = {
    51, 53, 55,          # drizzle
    56, 57,              # freezing drizzle
    61, 63, 65,          # rain
    66, 67,              # freezing rain
    71, 73, 75, 77,      # snow / snow grains
    80, 81, 82,          # rain showers
    85, 86,              # snow showers
    95, 96, 99,          # thunderstorm / with hail
}

_WMO_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


@dataclass
class HourlySlice:
    time: datetime       # timezone-aware, Boise local
    temp_f: float
    wind_mph: float
    gust_mph: float
    precip_mm: float
    precip_prob: int     # 0-100
    weather_code: int    # WMO code; -1 if not available
    description: str
    has_precip: bool     # derived: True if code is in _PRECIP_CODES or shortForecast says rain/snow


def filter_slices(slices: list[HourlySlice], window_start: datetime, window_end: datetime) -> list[HourlySlice]:
    """Return slices whose time falls within [window_start, window_end)."""
    return [s for s in slices if window_start <= s.time < window_end]


def fetch_weather(
    window_start: datetime, window_end: datetime, lat: float, lon: float
) -> list[HourlySlice]:
    """Fetch hourly slices covering [window_start, window_end].

    Open-Meteo is the primary source (NWS is fallback for the base data).
    HRRR (via Open-Meteo) and Pirate Weather are additionally consulted as
    best-effort precip-only checks: if either detects precipitation in an hour
    the primary source missed, the slice's has_precip flag is OR'd on. This
    catches localized/convective precip that the default Open-Meteo blend can
    smooth away.
    """
    try:
        slices = _fetch_open_meteo(window_start, window_end, lat, lon)
        log.info("Weather fetched from Open-Meteo")
    except Exception as exc:
        log.warning(f"Open-Meteo failed ({exc}), trying NWS")
        try:
            slices = _fetch_nws(window_start, window_end, lat, lon)
            log.info("Weather fetched from NWS (fallback)")
        except Exception as exc2:
            log.warning(f"NWS also failed ({exc2})")
            raise RuntimeError("Both weather sources unavailable") from exc2

    _augment_precip_signals(slices, window_start, window_end, lat, lon)
    return slices


def _augment_precip_signals(
    slices: list[HourlySlice],
    window_start: datetime,
    window_end: datetime,
    lat: float,
    lon: float,
) -> None:
    """OR extra-source precip detections into the primary slices in place.

    Failures in the extra sources are logged and swallowed — they are
    supplementary signals, not required data.
    """
    extra_precip_hours: set[datetime] = set()

    try:
        hrrr_hours = _fetch_hrrr_precip_hours(window_start, window_end, lat, lon)
        if hrrr_hours:
            log.info(f"HRRR detected precip in {len(hrrr_hours)} hour(s)")
        extra_precip_hours |= hrrr_hours
    except Exception as exc:
        log.warning(f"HRRR precip check failed: {exc}")

    try:
        from . import config
        if config.PIRATE_WEATHER_API_KEY:
            pirate_hours = _fetch_pirate_precip_hours(
                window_start, window_end, lat, lon, config.PIRATE_WEATHER_API_KEY
            )
            if pirate_hours:
                log.info(f"Pirate Weather detected precip in {len(pirate_hours)} hour(s)")
            extra_precip_hours |= pirate_hours
    except Exception as exc:
        log.warning(f"Pirate Weather precip check failed: {exc}")

    if not extra_precip_hours:
        return

    for s in slices:
        if not s.has_precip and s.time in extra_precip_hours:
            s.has_precip = True


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------

def _fetch_open_meteo(
    window_start: datetime, window_end: datetime, lat: float, lon: float
) -> list[HourlySlice]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "precipitation_probability",
            "precipitation",
            "weathercode",
            "windspeed_10m",
            "windgusts_10m",
        ]),
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "mm",
        "timezone": "America/Boise",
        "forecast_days": 2,
    }
    log.debug(f"Open-Meteo request: {url} {params}")
    resp = requests.get(url, params=params, timeout=config.API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    log.debug(f"Open-Meteo raw response keys: {list(data.get('hourly', {}).keys())}")

    hourly = data["hourly"]
    times = hourly["time"]
    temps = hourly["temperature_2m"]
    probs = hourly["precipitation_probability"]
    precips = hourly["precipitation"]
    codes = hourly["weathercode"]
    winds = hourly["windspeed_10m"]
    gusts = hourly["windgusts_10m"]

    slices: list[HourlySlice] = []
    for i, t_str in enumerate(times):
        dt = datetime.fromisoformat(t_str).replace(tzinfo=_BOISE_TZ)
        if dt < window_start or dt >= window_end:
            continue
        code = int(codes[i])
        precip_mm = float(precips[i])
        slices.append(HourlySlice(
            time=dt,
            temp_f=float(temps[i]),
            wind_mph=float(winds[i]),
            gust_mph=float(gusts[i]),
            precip_mm=precip_mm,
            precip_prob=int(probs[i]) if probs[i] is not None else 0,
            weather_code=code,
            description=_WMO_DESCRIPTIONS.get(code, f"Code {code}"),
            has_precip=code in _PRECIP_CODES or precip_mm > 0,
        ))

    if not slices:
        raise ValueError("No hourly data in the requested window")
    return slices


# ---------------------------------------------------------------------------
# National Weather Service (fallback)
# ---------------------------------------------------------------------------

_NWS_PRECIP_KEYWORDS = {
    "rain", "drizzle", "shower", "snow", "sleet", "hail",
    "ice", "freezing", "thunderstorm", "storm", "wintry",
}

def _fetch_nws(
    window_start: datetime, window_end: datetime, lat: float, lon: float
) -> list[HourlySlice]:
    headers = {"User-Agent": "born-to-be-wild/1.0 (serversignal0@gmail.com)"}

    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    log.debug(f"NWS points request: {points_url}")
    pts = requests.get(points_url, headers=headers, timeout=config.API_TIMEOUT)
    pts.raise_for_status()
    forecast_hourly_url = pts.json()["properties"]["forecastHourly"]

    log.debug(f"NWS hourly forecast request: {forecast_hourly_url}")
    fc = requests.get(forecast_hourly_url, headers=headers, timeout=config.API_TIMEOUT)
    fc.raise_for_status()
    periods = fc.json()["properties"]["periods"]

    slices: list[HourlySlice] = []
    for p in periods:
        dt = datetime.fromisoformat(p["startTime"]).astimezone(_BOISE_TZ)
        if dt < window_start or dt >= window_end:
            continue

        wind_mph = _parse_nws_wind(p.get("windSpeed", "0 mph"))
        gust_str = p.get("windGust") or "0 mph"
        gust_mph = _parse_nws_wind(gust_str)
        precip_prob_raw = (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
        short = p.get("shortForecast", "").lower()
        has_precip = any(kw in short for kw in _NWS_PRECIP_KEYWORDS)

        slices.append(HourlySlice(
            time=dt,
            temp_f=float(p["temperature"]),
            wind_mph=wind_mph,
            gust_mph=max(gust_mph, wind_mph),
            precip_mm=0.0,  # not available in NWS hourly endpoint
            precip_prob=int(precip_prob_raw),
            weather_code=-1,
            description=p.get("shortForecast", "Unknown"),
            has_precip=has_precip,
        ))

    if not slices:
        raise ValueError("No NWS hourly data in the requested window")
    return slices


def _parse_nws_wind(value: str) -> float:
    """Parse NWS wind string like '10 mph' or '5 to 15 mph' → max value as float."""
    nums = re.findall(r"\d+", value)
    if not nums:
        return 0.0
    return float(max(int(n) for n in nums))


_NWS_HEADERS = {"User-Agent": "born-to-be-wild/1.0 (serversignal0@gmail.com)"}


def fetch_nws_alerts(lat: float, lon: float) -> list[str]:
    """Fetch active NWS hazard/warning alerts for a location.

    Returns a list of event name strings (e.g. ['Dense Fog Advisory', 'Wind Advisory']).
    Returns an empty list if the request fails — alerts are best-effort.
    """
    url = "https://api.weather.gov/alerts/active"
    params = {"point": f"{lat},{lon}"}
    try:
        resp = requests.get(url, params=params, headers=_NWS_HEADERS, timeout=config.API_TIMEOUT)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        alerts = list(dict.fromkeys(f["properties"]["event"] for f in features))
        if alerts:
            log.warning(f"NWS active alerts: {alerts}")
        else:
            log.debug("No active NWS alerts")
        return alerts
    except Exception as exc:
        log.warning(f"Could not fetch NWS alerts: {exc}")
        return []


# ---------------------------------------------------------------------------
# Supplemental precip checks (HRRR, Pirate Weather)
# ---------------------------------------------------------------------------

def _fetch_hrrr_precip_hours(
    window_start: datetime, window_end: datetime, lat: float, lon: float
) -> set[datetime]:
    """Return set of hours where NOAA HRRR forecasts precipitation.

    HRRR (High-Resolution Rapid Refresh) is a 3km CONUS model that updates
    hourly and tends to resolve localized convective precip better than the
    Open-Meteo default blend. Accessed via Open-Meteo's `models=hrrr_conus`.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation,weathercode",
        "precipitation_unit": "mm",
        "timezone": "America/Boise",
        "models": "hrrr_conus",
        "forecast_days": 2,
    }
    resp = requests.get(url, params=params, timeout=config.API_TIMEOUT)
    resp.raise_for_status()
    hourly = resp.json().get("hourly") or {}
    times = hourly.get("time") or []
    precips = hourly.get("precipitation") or []
    codes = hourly.get("weathercode") or []

    hours: set[datetime] = set()
    for i, t_str in enumerate(times):
        dt = datetime.fromisoformat(t_str).replace(tzinfo=_BOISE_TZ)
        if dt < window_start or dt >= window_end:
            continue
        precip_mm = float(precips[i]) if i < len(precips) and precips[i] is not None else 0.0
        code = int(codes[i]) if i < len(codes) and codes[i] is not None else -1
        if precip_mm > 0 or code in _PRECIP_CODES:
            hours.add(dt)
    return hours


def _fetch_pirate_precip_hours(
    window_start: datetime, window_end: datetime, lat: float, lon: float, api_key: str
) -> set[datetime]:
    """Return set of hours where Pirate Weather forecasts precipitation.

    Pirate Weather is a Dark Sky drop-in and provides hyperlocal precip
    forecasts (similar to what the iOS Weather app shows). Free tier: 10k
    calls/month.
    """
    exclude = "currently,minutely,daily,alerts,flags"
    url = f"https://api.pirateweather.net/forecast/{api_key}/{lat},{lon}"
    params = {"units": "us", "exclude": exclude}
    resp = requests.get(url, params=params, timeout=config.API_TIMEOUT)
    resp.raise_for_status()
    hourly_data = (resp.json().get("hourly") or {}).get("data") or []

    # Pirate marks precip when precipType is set and precipProbability is
    # meaningful. We use precipIntensity > 0 as the primary signal (matches
    # what the iOS Weather "raining now" UI is derived from) and require a
    # non-"none" precipType to filter out spurious zero-intensity flags.
    hours: set[datetime] = set()
    for entry in hourly_data:
        ts = entry.get("time")
        if ts is None:
            continue
        dt = datetime.fromtimestamp(int(ts), tz=_BOISE_TZ).replace(minute=0, second=0, microsecond=0)
        if dt < window_start or dt >= window_end:
            continue
        intensity = float(entry.get("precipIntensity") or 0.0)
        ptype = (entry.get("precipType") or "").lower()
        if intensity > 0 and ptype and ptype != "none":
            hours.add(dt)
    return hours
