"""SMS ride reports via Textbelt."""
from __future__ import annotations
import logging
import re
from datetime import datetime

import requests

from . import config
from .conditions import Assessment

log = logging.getLogger(__name__)

_TEXTBELT_URL = "https://textbelt.com/text"


def build_sms(
    name: str,
    assessment: Assessment,
    window_start: datetime,
    window_end: datetime,
    sunrise: datetime,
    sunset: datetime,
    triggering_zip: str | None = None,
) -> str:
    """Build a concise SMS message (single worst condition, no Unicode, ≤160 chars)."""
    date_str = window_start.strftime("%a %b %-d")
    wind = (
        f"{assessment.wind_min:.0f}-{assessment.wind_max:.0f} mph"
        + (f", Gusts {assessment.gust_max:.0f}" if assessment.gust_max > assessment.wind_max else "")
    )

    # Header: include [zip] when a specific location triggered the status
    if assessment.status != "GO" and triggering_zip:
        header = f"{assessment.status} - {date_str} [{triggering_zip}]"
    else:
        header = f"{assessment.status} - {date_str}"

    lines = [header]

    # Single worst-condition line for non-GO statuses
    if assessment.status == "NO-GO" and assessment.nogo_reasons:
        lines.append(_worst_nogo_line(assessment.nogo_reasons))
    elif assessment.status == "CAUTION" and assessment.caution_notes:
        lines.append(_worst_caution_line(assessment.caution_notes))

    lines.append(f"Temp: {assessment.temp_min:.0f}-{assessment.temp_max:.0f}F")
    lines.append(wind)
    lines.append(f"SR {sunrise.strftime('%-I:%M %p')} SS {sunset.strftime('%-I:%M %p')}")
    lines.append("Reply STOP to unsub")

    # Hard 160-char limit — drop the condition line first if needed
    while len("\n".join(lines)) > 160 and len(lines) > 2:
        lines.pop(1)
    return "\n".join(lines)[:160]


def _worst_nogo_line(reasons: list[str]) -> str:
    """Pick and format the single most dangerous NO-GO reason for SMS.

    Priority: winter precip / thunder > rain > temp > wind > NWS alert.
    """
    def _rank(r: str) -> int:
        r = r.lower()
        if "winter precipitation" in r or "thunderstorm" in r:
            return 0
        if "rain in forecast" in r:
            return 1
        if "temperature" in r:
            return 2
        if "gust" in r or "sustained wind" in r:
            return 3
        return 4  # NWS alert

    worst = min(reasons, key=_rank)
    w = worst.lower()

    m_window = re.search(r'\(([^)]+)\)', worst)
    window = m_window.group(1) if m_window else ""

    if "winter precipitation" in w:
        return f"Ice/Snow {window}" if window else "Ice/Snow in forecast"
    if "thunderstorm" in w:
        return f"Storms {window}" if window else "Thunderstorms"
    if "rain in forecast" in w:
        return f"Rain {window}" if window else "Rain in forecast"
    if "temperature" in w:
        m = re.search(r'low of (\d+)', worst)
        return f"Temp low of {m.group(1)}F" if m else "Temp below 45F"
    if "gust" in w:
        m = re.search(r'\((\d+) mph\)', worst)
        return f"Gusts {m.group(1)} mph" if m else "High wind gusts"
    if "sustained wind" in w:
        m = re.search(r'\((\d+) mph\)', worst)
        return f"Wind {m.group(1)} mph" if m else "High winds"
    # NWS alert
    return worst.replace("NWS Alert: ", "")[:40]


def _worst_caution_line(notes: list[str]) -> str:
    """Pick and format the single most notable CAUTION note for SMS.

    Priority: wind gusts > sustained wind > rain prob > NWS > wet roads > darkness.
    """
    for note in notes:
        n = note.lower()
        if "wind gust" in n:
            m = re.search(r'(\d+) mph', note)
            return f"Gusts {m.group(1)} mph" if m else "High wind gusts"
        if "sustained wind" in n:
            m = re.search(r'(\d+) mph', note)
            return f"Wind {m.group(1)} mph" if m else "High winds"
        if "rain probability" in n:
            m = re.search(r'(\d+)%', note)
            return f"Rain {m.group(1)}% chance" if m else "Elevated rain chance"
        if "nws alert" in n:
            return note.replace("NWS Alert: ", "")
        if "wet" in n:
            return "Wet roads from overnight rain"
        if "reduced visibility" in n:
            return "Reduced visibility: at SR"
    return notes[0][:40] if notes else ""


def send_sms_report(
    to_number: str,
    name: str,
    assessment: Assessment,
    window_start: datetime,
    window_end: datetime,
    sunrise: datetime,
    sunset: datetime,
    triggering_zip: str | None = None,
) -> None:
    """Send a ride report SMS via Textbelt."""
    if not config.TEXTBELT_API_KEY:
        log.warning("TEXTBELT_API_KEY not set — skipping SMS")
        return

    message = build_sms(
        name, assessment, window_start, window_end, sunrise, sunset, triggering_zip
    )
    log.info(f"Sending SMS to {to_number}")

    resp = requests.post(
        _TEXTBELT_URL,
        data={
            "phone": to_number,
            "message": message,
            "key": config.TEXTBELT_API_KEY,
        },
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()

    if result.get("success"):
        log.info(f"SMS sent to {to_number} (quota remaining: {result.get('quotaRemaining', '?')})")
    else:
        log.error(f"Textbelt rejected SMS to {to_number}: {result.get('error')}")
