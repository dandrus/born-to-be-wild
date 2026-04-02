from __future__ import annotations
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from . import config
from .conditions import Assessment

log = logging.getLogger(__name__)

_STATUS_EMOJI = {"GO": "✅", "CAUTION": "⚠️", "NO-GO": "🚫"}
_CLOSING = {
    "GO": "Have a great ride!",
    "CAUTION": "Ride carefully and stay alert.",
    "NO-GO": "Stay safe, maybe next time.",
}
_COMMANDS_FOOTER = """\
---
Commands (reply to this email):
  HELP                      - List all commands
  STATUS                    - Get an on-demand weather check right now
  FORECAST                  - Get the next 3-day outlook
  SNOOZE [X]                - Pause emails for X days (e.g., SNOOZE 5)
  RESUME                    - Cancel a snooze early
  CHANGE TIME [HH:MM AM/PM or HH:MM] - Change your email time
  UNSUBSCRIBE               - Stop all emails
  REPORT ACCURATE           - Today's forecast was correct
  REPORT WRONG              - Today's forecast was incorrect"""


def build_subject(assessment: Assessment, send_date: datetime) -> str:
    date_str = send_date.strftime("%a %b %-d, %Y")
    return f"Ride Report: {assessment.status} - {date_str}"


def build_body(
    name: str,
    assessment: Assessment,
    window_start: datetime,
    window_end: datetime,
    sunrise: datetime,
    sunset: datetime,
) -> str:
    emoji = _STATUS_EMOJI[assessment.status]
    window_str = (
        f"{window_start.strftime('%-I:%M %p')} - {window_end.strftime('%-I:%M %p')}"
    )

    # Precipitation line
    if assessment.has_precip:
        precip_line = f"Rain/snow expected {assessment.precip_window}"
    elif assessment.precip_prob_max >= 30:
        precip_line = f"Rain probability {assessment.precip_prob_max}%"
    else:
        precip_line = "None expected"

    # Wind line
    if assessment.gust_max > assessment.wind_max:
        wind_line = (
            f"{assessment.wind_min:.0f}-{assessment.wind_max:.0f} mph, "
            f"gusts up to {assessment.gust_max:.0f} mph"
        )
    else:
        wind_line = f"{assessment.wind_min:.0f}-{assessment.wind_max:.0f} mph"

    lines: list[str] = [
        f"Good morning, {name}!",
        "",
        f"TODAY'S RIDE STATUS: {assessment.status} {emoji}",
        "",
        f"Forecast for Ada/Canyon County ({window_str}):",
        f"- Temperature range: {assessment.temp_min:.0f}°F - {assessment.temp_max:.0f}°F",
        f"- Wind: {wind_line}",
        f"- Precipitation: {precip_line}",
        f"- Conditions: {assessment.conditions_summary}",
        "",
        f"Sunrise: {sunrise.strftime('%-I:%M %p')} | Sunset: {sunset.strftime('%-I:%M %p')}",
    ]

    # Status-specific detail block
    if assessment.status == "NO-GO" and assessment.nogo_reasons:
        lines.append("")
        for reason in assessment.nogo_reasons:
            lines.append(f"❌ {reason}")
    elif assessment.status == "CAUTION" and assessment.caution_notes:
        lines.append("")
        lines.append("⚠️ Heads up:")
        for note in assessment.caution_notes:
            lines.append(f"- {note}")

    lines += ["", _CLOSING[assessment.status], "", _COMMANDS_FOOTER]
    return "\n".join(lines)


def send_simple(to_address: str, subject: str, body: str) -> None:
    """Send a plain-text reply email from the Gmail account."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = to_address
    log.info(f"Sending '{subject}' to {to_address}")
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        smtp.sendmail(config.GMAIL_ADDRESS, to_address, msg.as_string())
    log.info(f"Reply sent to {to_address}")


def send_report(
    name: str,
    to_address: str,
    assessment: Assessment,
    window_start: datetime,
    window_end: datetime,
    sunrise: datetime,
    sunset: datetime,
) -> None:
    """Compose and send the ride report email via Gmail SMTP."""
    subject = build_subject(assessment, window_start)
    body = build_body(name, assessment, window_start, window_end, sunrise, sunset)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = to_address

    log.info(f"Sending '{subject}' to {to_address}")
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        smtp.sendmail(config.GMAIL_ADDRESS, to_address, msg.as_string())
    log.info(f"Email sent successfully to {to_address}")
