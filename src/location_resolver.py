"""Resolve a US zip code to coordinates and timezone."""
from __future__ import annotations
import logging

import requests
from timezonefinder import TimezoneFinder

log = logging.getLogger(__name__)
_tf = TimezoneFinder()
_DEFAULT_TZ = "America/Denver"


def resolve_zip(zip_code: str) -> tuple[float, float, str, str, str]:
    """Return (lat, lon, city, state, timezone) for a US zip code.

    Raises ValueError for unknown zip codes, RuntimeError on network failure.
    """
    url = f"https://api.zippopotam.us/us/{zip_code}"
    try:
        resp = requests.get(url, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"Could not reach zip lookup service for {zip_code!r}: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"Zip code {zip_code!r} not found")
    resp.raise_for_status()

    data = resp.json()
    place = data["places"][0]
    lat = float(place["latitude"])
    lon = float(place["longitude"])
    city = place["place name"]
    state = place["state abbreviation"]
    tz = _tf.timezone_at(lat=lat, lng=lon) or _DEFAULT_TZ
    log.debug(f"Resolved {zip_code} → {city}, {state} ({lat}, {lon}) [{tz}]")
    return lat, lon, city, state, tz
