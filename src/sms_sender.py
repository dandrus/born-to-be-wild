"""SMS ride reports via Textbelt."""
from __future__ import annotations
import logging
from datetime import datetime

import requests

from . import config
from .conditions import Assessment

log = logging.getLogger(__name__)

_TEXTBELT_URL = "https://textbelt.com/text"


def _ascii(text: str) -> str:
    """Replace non-GSM characters so the message stays in 160-char mode."""
    return text.replace("°", "")


def build_sms(
    name: str,
    assessment: Assessment,
    window_start: datetime,
    window_end: datetime,
    sunrise: datetime,
    sunset: datetime,
) -> str:
    """Build a concise SMS message. Avoids Unicode (emojis, degree symbol) to stay
    within 160 chars/segment and use 1 credit per send."""
    date_str = window_start.strftime("%a %b %-d")
    wind = (
        f"{assessment.wind_min:.0f}-{assessment.wind_max:.0f} mph"
        + (f", Gusts {assessment.gust_max:.0f}" if assessment.gust_max > assessment.wind_max else "")
    )

    lines = [f"{assessment.status} - {date_str}"]

    # Temp reason (only when below threshold)
    if assessment.temp_min < 50:
        lines.append(f"Temp low of {assessment.temp_min:.0f}F")

    # Precipitation line
    if assessment.has_precip and assessment.precip_window:
        lines.append(f"Rain {assessment.precip_window}")
    elif assessment.precip_prob_max >= 30:
        lines.append(f"Rain {assessment.precip_prob_max}% chance")

    # Any other NO-GO or CAUTION reasons (wind, alerts, wet roads, etc.)
    skip_prefixes = ("temperature", "rain", "temp")
    if assessment.status == "NO-GO":
        for reason in assessment.nogo_reasons:
            if not _ascii(reason).lower().startswith(skip_prefixes):
                lines.append(_ascii(reason))
    elif assessment.status == "CAUTION":
        for note in assessment.caution_notes:
            if not _ascii(note).lower().startswith(skip_prefixes):
                sms_note = _ascii(note)
                # SR/SS already appear in the footer line; drop the redundant times
                if sms_note.lower().startswith("reduced visibility:"):
                    sms_note = "Reduced visibility: at SR"
                lines.append(sms_note)

    lines.append(f"Temp: {assessment.temp_min:.0f}-{assessment.temp_max:.0f}F")
    lines.append(f"Wind: {wind}")
    lines.append(f"SR {sunrise.strftime('%-I:%M %p')} SS {sunset.strftime('%-I:%M %p')}")
    lines.append("Reply STOP to unsub")

    # Hard 160-char limit (GSM-7: 140 bytes × 8/7 = 160 chars per segment).
    # Drop optional detail lines (after header) one at a time,
    # always preserving "Reply STOP to unsub" at the end.
    while len("\n".join(lines)) > 160 and len(lines) > 2:
        lines.pop(1)
    return "\n".join(lines)[:160]


def send_sms_report(
    to_number: str,
    name: str,
    assessment: Assessment,
    window_start: datetime,
    window_end: datetime,
    sunrise: datetime,
    sunset: datetime,
) -> None:
    """Send a ride report SMS via Textbelt."""
    if not config.TEXTBELT_API_KEY:
        log.warning("TEXTBELT_API_KEY not set — skipping SMS")
        return

    message = build_sms(name, assessment, window_start, window_end, sunrise, sunset)
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
