"""Command handlers for subscriber reply emails."""
from __future__ import annotations
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from . import config
from .subscribers import Subscriber, set_active, set_snooze, update_subscriber, log_accuracy
from .holidays import is_skip_day
from .email_sender import send_simple

log = logging.getLogger(__name__)

_HELP_BODY = """\
Available commands (reply to any ride report email):

  HELP                             - List all commands
  STATUS                           - Get an on-demand weather check right now
  FORECAST                         - Get the next 3-day outlook
  SNOOZE [X]                       - Pause emails for X days (e.g., SNOOZE 5)
  RESUME                           - Cancel a snooze early
  CHANGE TIME [HH:MM AM/PM or HH:MM] - Change your daily email time
  UNSUBSCRIBE                      - Stop all emails
  SUBSCRIBE                        - Re-activate your subscription
  REPORT ACCURATE                  - Today's forecast was correct
  REPORT WRONG                     - Today's forecast was incorrect"""


def handle_command(raw: str, subscriber: Subscriber, db_path: str, scheduler: Any) -> None:
    """Parse the first line of a reply and dispatch the appropriate command."""
    text = raw.strip().upper()

    if text == "HELP":
        _cmd_help(subscriber)
    elif text == "STATUS":
        _cmd_status(subscriber)
    elif text == "FORECAST":
        _cmd_forecast(subscriber)
    elif text.startswith("SNOOZE"):
        parts = text.split()
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        _cmd_snooze(subscriber, db_path, days)
    elif text == "RESUME":
        _cmd_resume(subscriber, db_path)
    elif text.upper().startswith("CHANGE TIME"):
        time_str = raw.strip()[len("CHANGE TIME"):].strip()
        _cmd_change_time(subscriber, db_path, scheduler, time_str)
    elif text == "UNSUBSCRIBE":
        _cmd_unsubscribe(subscriber, db_path)
    elif text == "SUBSCRIBE":
        _cmd_subscribe(subscriber, db_path)
    elif text == "REPORT ACCURATE":
        _cmd_report(subscriber, accurate=True, db_path=db_path)
    elif text == "REPORT WRONG":
        _cmd_report(subscriber, accurate=False, db_path=db_path)
    else:
        _cmd_unknown(subscriber, raw)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _cmd_help(subscriber: Subscriber) -> None:
    send_simple(subscriber.email, "Born to be Wild — Commands", _HELP_BODY)


def _cmd_status(subscriber: Subscriber) -> None:
    """Send an immediate on-demand ride report."""
    from .main import _run_send  # lazy import to avoid circular dependency
    log.info(f"STATUS requested by {subscriber.email}")
    _run_send(subscriber)


def _cmd_forecast(subscriber: Subscriber) -> None:
    """Send a 3-day forecast summary."""
    from .weather import fetch_weather
    from .conditions import evaluate
    from .sun import get_sunrise_sunset

    now = datetime.now(tz=config.TIMEZONE)
    send_h, send_m = (int(x) for x in subscriber.send_time.split(":"))
    lines = ["3-Day Ride Forecast for Ada/Canyon County", ""]

    for delta in range(1, 4):
        target = now.date() + timedelta(days=delta)
        day_label = datetime(target.year, target.month, target.day,
                             tzinfo=config.TIMEZONE).strftime("%A, %b %-d")
        skip_note = " (weekend/holiday — no report)" if is_skip_day(target) else ""

        win_start = datetime(target.year, target.month, target.day,
                             send_h, send_m, tzinfo=config.TIMEZONE)
        win_end = win_start + timedelta(hours=config.FORECAST_HOURS)
        overnight_start = win_start - timedelta(hours=6)
        sunrise, sunset = get_sunrise_sunset(target)

        try:
            slices = fetch_weather(win_start, win_end)
            overnight = fetch_weather(overnight_start, win_start)
        except Exception as exc:
            log.warning(f"Forecast fetch failed for {target}: {exc}")
            lines += [f"{day_label}: Data unavailable ⚠️{skip_note}", ""]
            continue

        a = evaluate(slices, sunrise, sunset, win_start, win_end, overnight or None)
        emoji = {"GO": "✅", "CAUTION": "⚠️", "NO-GO": "🚫"}[a.status]

        lines.append(f"{day_label}: {a.status} {emoji}{skip_note}")
        lines.append(
            f"  Temp: {a.temp_min:.0f}°F - {a.temp_max:.0f}°F  |  "
            f"Wind: {a.wind_min:.0f}-{a.wind_max:.0f} mph"
            + (f", gusts to {a.gust_max:.0f} mph" if a.gust_max > a.wind_max else "")
        )
        if a.nogo_reasons:
            for r in a.nogo_reasons:
                lines.append(f"  ❌ {r}")
        elif a.caution_notes:
            for n in a.caution_notes:
                lines.append(f"  ⚠️  {n}")
        lines.append("")

    send_simple(subscriber.email, "3-Day Ride Forecast", "\n".join(lines))


def _cmd_snooze(subscriber: Subscriber, db_path: str, days: int) -> None:
    snooze_until = (date.today() + timedelta(days=days)).isoformat()
    set_snooze(db_path, subscriber.id, snooze_until)
    resume_date = _next_ride_date(date.fromisoformat(snooze_until))
    body = (
        f"Snoozed for {days} day{'s' if days != 1 else ''}. "
        f"Your next report will arrive on {resume_date.strftime('%A, %b %-d')}.\n\n"
        "Reply RESUME to cancel the snooze early."
    )
    send_simple(subscriber.email, "Snooze Confirmed", body)
    log.info(f"Snoozed {subscriber.email} until {snooze_until}")


def _cmd_resume(subscriber: Subscriber, db_path: str) -> None:
    if subscriber.snooze_until is None:
        send_simple(subscriber.email, "No Active Snooze",
                    "You don't have an active snooze. Your reports are already running.")
    else:
        set_snooze(db_path, subscriber.id, None)
        send_simple(subscriber.email, "Snooze Cancelled",
                    "Snooze cancelled. You'll receive your next report tomorrow.")
        log.info(f"Snooze cleared for {subscriber.email}")


def _cmd_change_time(
    subscriber: Subscriber, db_path: str, scheduler: Any, time_str: str
) -> None:
    new_time = _parse_time(time_str)
    if new_time is None:
        send_simple(
            subscriber.email,
            "Invalid Time Format",
            f"Couldn't parse {time_str!r}. Use HH:MM (e.g., 07:00) or H:MM AM/PM (e.g., 7:00 AM).",
        )
        return

    update_subscriber(db_path, subscriber.id, send_time=new_time)

    # Dynamically reschedule the job so the change takes effect immediately
    if scheduler is not None:
        try:
            from apscheduler.triggers.cron import CronTrigger
            h, m = new_time.split(":")
            scheduler.reschedule_job(
                f"subscriber_{subscriber.id}",
                trigger=CronTrigger(hour=int(h), minute=int(m), timezone=config.TIMEZONE),
            )
            log.info(f"Rescheduled job for {subscriber.email} to {new_time}")
        except Exception as exc:
            log.warning(f"Could not reschedule job for {subscriber.email}: {exc}")

    h, m = new_time.split(":")
    display = datetime(2000, 1, 1, int(h), int(m)).strftime("%-I:%M %p")
    send_simple(
        subscriber.email,
        "Email Time Updated",
        f"Your daily ride report will now arrive at {display} (Boise time).",
    )
    log.info(f"Changed send_time for {subscriber.email} to {new_time}")


def _cmd_unsubscribe(subscriber: Subscriber, db_path: str) -> None:
    set_active(db_path, subscriber.id, False)
    send_simple(
        subscriber.email,
        "Unsubscribed",
        "You've been unsubscribed from daily ride reports.\n\n"
        "Reply SUBSCRIBE at any time to re-activate.",
    )
    log.info(f"Unsubscribed {subscriber.email}")


def _cmd_subscribe(subscriber: Subscriber, db_path: str) -> None:
    if subscriber.active:
        send_simple(subscriber.email, "Already Subscribed",
                    "You're already subscribed! Your daily reports are active.")
    else:
        set_active(db_path, subscriber.id, True)
        h, m = subscriber.send_time.split(":")
        display = datetime(2000, 1, 1, int(h), int(m)).strftime("%-I:%M %p")
        send_simple(
            subscriber.email,
            "Welcome Back!",
            f"Welcome back! Your daily ride reports will resume tomorrow at {display}.",
        )
        log.info(f"Re-subscribed {subscriber.email}")


def _cmd_report(subscriber: Subscriber, accurate: bool, db_path: str) -> None:
    label = "accurate" if accurate else "inaccurate"
    found = log_accuracy(db_path, subscriber.id, accurate)
    if not found:
        log.warning(f"No unrated log entry found for {subscriber.email} to mark {label}")
    send_simple(
        subscriber.email,
        "Feedback Received",
        f"Thanks for the feedback! Logged as {label}.",
    )
    log.info(f"Forecast rated {label} by {subscriber.email}")


def _cmd_unknown(subscriber: Subscriber, raw: str) -> None:
    body = f"I didn't understand {raw!r}.\n\n{_HELP_BODY}"
    send_simple(subscriber.email, "Unknown Command", body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_ride_date(after: date) -> date:
    """Return the next non-skip (weekday, non-holiday) date after `after`."""
    d = after + timedelta(days=1)
    for _ in range(14):
        if not is_skip_day(d):
            return d
        d += timedelta(days=1)
    return d


def _parse_time(value: str) -> str | None:
    """Parse HH:MM or H:MM AM/PM → zero-padded HH:MM 24-hour. Returns None on failure."""
    value = value.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(AM|PM)", value, re.IGNORECASE)
    if m:
        h, minute, period = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        h = (0 if h == 12 else h) if period == "AM" else (12 if h == 12 else h + 12)
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return f"{h:02d}:{minute:02d}"
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if m:
        h, minute = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return f"{h:02d}:{minute:02d}"
    return None
