"""Phase 3 entry point: multi-subscriber scheduler + inbox polling."""
from __future__ import annotations
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .logging_config import setup_logging
from . import config
from .subscribers import Subscriber, init_db, list_subscribers, get_by_id, log_email_sent
from .holidays import is_skip_day
from .weather import fetch_weather, fetch_nws_alerts
from .conditions import evaluate
from .email_sender import send_report
from .sms_sender import send_sms_report
from .sun import get_sunrise_sunset
from .health import run_health_check

log = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    log.info("Born to be Wild starting (Phase 3)")

    init_db(config.DB_PATH)

    # Note: subscribers are loaded once at startup. Restart the service to
    # pick up new subscribers or send_time changes made via cli.py.
    # CHANGE TIME commands reschedule jobs dynamically without a restart.
    active_subs = list_subscribers(config.DB_PATH, active_only=True)
    if not active_subs:
        log.warning("No active subscribers found — scheduler will start with no jobs")

    scheduler = BlockingScheduler(timezone=config.TIMEZONE)

    for sub in active_subs:
        _schedule_subscriber(scheduler, sub)
        log.info(f"Scheduled job for {sub.name} ({sub.email}) at {sub.send_time}")

    from .email_reader import poll_inbox
    scheduler.add_job(
        poll_inbox,
        trigger=IntervalTrigger(minutes=5),
        kwargs={"db_path": config.DB_PATH, "scheduler": scheduler},
        id="inbox_poll",
        name="Inbox polling",
        misfire_grace_time=60,
    )
    log.info("Inbox polling scheduled every 5 minutes")

    scheduler.add_job(
        run_health_check,
        trigger=CronTrigger(hour=10, minute=0, timezone=config.TIMEZONE),
        kwargs={"db_path": config.DB_PATH},
        id="health_check",
        name="Health check",
        misfire_grace_time=3600,
    )
    log.info("Health check scheduled daily at 10:00 AM")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


def _schedule_subscriber(scheduler: BlockingScheduler, sub: Subscriber) -> None:
    hour, minute = sub.send_time.split(":")
    scheduler.add_job(
        send_job,
        trigger=CronTrigger(
            hour=int(hour),
            minute=int(minute),
            timezone=config.TIMEZONE,
        ),
        kwargs={"subscriber_id": sub.id, "db_path": config.DB_PATH},
        id=f"subscriber_{sub.id}",
        name=f"Ride report: {sub.name}",
        misfire_grace_time=600,
        replace_existing=True,
    )


def send_job(subscriber_id: int, db_path: str) -> None:
    """APScheduler calls this at each subscriber's send_time."""
    sub = get_by_id(db_path, subscriber_id)
    if sub is None:
        log.warning(f"Job fired for subscriber_id={subscriber_id} but not found in DB")
        return
    if not sub.active:
        log.info(f"Skipping {sub.email}: inactive")
        return

    today = datetime.now(tz=config.TIMEZONE).date()

    if is_skip_day(today):
        log.info(f"Skipping {sub.email}: off-season, weekend, or holiday ({today})")
        return

    if sub.snooze_until is not None:
        from datetime import date
        snooze_date = date.fromisoformat(sub.snooze_until)
        if today <= snooze_date:
            log.info(f"Skipping {sub.email}: snoozed until {snooze_date}")
            return

    _run_send(sub)


def _run_send(sub: Subscriber) -> None:
    """Fetch weather, evaluate, and send the ride report for one subscriber."""
    boise_tz = config.TIMEZONE
    now = datetime.now(tz=boise_tz)

    send_h, send_m = (int(x) for x in sub.send_time.split(":"))
    window_start = now.replace(hour=send_h, minute=send_m, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=config.FORECAST_HOURS)
    overnight_start = window_start - timedelta(hours=6)

    log.info(
        f"Fetching weather for {sub.name} "
        f"({window_start.strftime('%-I:%M %p')} - {window_end.strftime('%-I:%M %p')})"
    )

    sunrise, sunset = get_sunrise_sunset(now.date())

    try:
        slices = fetch_weather(window_start, window_end)
    except RuntimeError:
        log.error(f"Both weather sources failed for {sub.email} — sending unavailable email")
        _send_unavailable(sub, window_start, sunrise, sunset)
        return

    try:
        overnight = fetch_weather(overnight_start, window_start)
    except Exception as exc:
        log.warning(f"Could not fetch overnight data for {sub.email}: {exc}")
        overnight = []

    alerts = fetch_nws_alerts()

    assessment = evaluate(
        slices=slices,
        sunrise=sunrise,
        sunset=sunset,
        window_start=window_start,
        window_end=window_end,
        overnight_slices=overnight if overnight else None,
        nws_alerts=alerts if alerts else None,
    )

    log.info(f"Assessment for {sub.name}: {assessment.status}")

    if sub.message_email:
        send_report(
            name=sub.name,
            to_address=sub.email,
            assessment=assessment,
            window_start=window_start,
            window_end=window_end,
            sunrise=sunrise,
            sunset=sunset,
        )

    if sub.message_phone and sub.phone:
        try:
            send_sms_report(
                to_number=sub.phone,
                name=sub.name,
                assessment=assessment,
                window_start=window_start,
                window_end=window_end,
                sunrise=sunrise,
                sunset=sunset,
            )
        except Exception as exc:
            log.error(f"SMS failed for {sub.name}: {exc}")

    log_email_sent(config.DB_PATH, sub.id, assessment.status)


def _send_unavailable(
    sub: Subscriber,
    window_start: datetime,
    sunrise: datetime,
    sunset: datetime,
) -> None:
    date_str = window_start.strftime("%a %b %-d, %Y")
    subject = f"Ride Report: UNKNOWN - {date_str}"
    body = (
        f"Good morning, {sub.name}!\n\n"
        "TODAY'S RIDE STATUS: UNKNOWN ⚠️\n\n"
        "⚠ Weather data unavailable — could not reach weather services. "
        "Ride with caution and check conditions manually.\n\n"
        f"Sunrise: {sunrise.strftime('%-I:%M %p')} | Sunset: {sunset.strftime('%-I:%M %p')}\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = sub.email
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        smtp.sendmail(config.GMAIL_ADDRESS, sub.email, msg.as_string())


if __name__ == "__main__":
    main()
