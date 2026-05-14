"""Entry point: multi-subscriber scheduler + inbox polling."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, date

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .logging_config import setup_logging
from . import config
from .subscribers import (
    Location, Subscriber,
    init_db, list_subscribers, get_by_id, get_locations, log_email_sent,
)
from .holidays import is_skip_day
from .weather import HourlySlice, fetch_weather, fetch_nws_alerts, filter_slices
from .conditions import Assessment, evaluate
from .email_sender import send_report
from .sms_sender import send_sms_report
from .sun import get_sunrise_sunset
from .health import run_health_check

# Cache keyed by (lat, lon) — populated by the 5:05 AM pre-fetch job
_daily_cache: dict[tuple[float, float], list[HourlySlice]] = {}
_cache_date: date | None = None

log = logging.getLogger(__name__)


def _do_prefetch() -> bool:
    """Fetch and cache today's full 24-hour weather block for every active location."""
    global _daily_cache, _cache_date
    today = datetime.now(tz=config.TIMEZONE).date()
    window_start = datetime(today.year, today.month, today.day, 0, 0, tzinfo=config.TIMEZONE)
    window_end = window_start + timedelta(hours=24)

    active_subs = list_subscribers(config.DB_PATH, active_only=True)
    unique_locs: dict[tuple[float, float], str] = {}
    for sub in active_subs:
        for loc in get_locations(config.DB_PATH, sub.id):
            unique_locs[(loc.lat, loc.lon)] = loc.zip_code

    if not unique_locs:
        log.warning("No locations found for any active subscriber — skipping pre-fetch")
        return False

    new_cache: dict[tuple[float, float], list[HourlySlice]] = {}
    for (lat, lon), zip_code in unique_locs.items():
        try:
            slices = fetch_weather(window_start, window_end, lat, lon)
            new_cache[(lat, lon)] = slices
            log.info(f"Pre-fetched {zip_code}: {len(slices)} slices")
        except RuntimeError as exc:
            log.warning(f"Pre-fetch failed for {zip_code}: {exc}")

    if new_cache:
        _daily_cache = new_cache
        _cache_date = today
        log.info(f"Weather pre-fetch complete: {len(new_cache)} location(s) cached for {today}")
        return True

    log.warning("Weather pre-fetch failed — all locations unavailable")
    return False


def _prefetch_job(scheduler: BlockingScheduler) -> None:
    """Runs at 5:05 AM. On failure, starts 15-min retries until 9 AM."""
    if not _do_prefetch():
        if not scheduler.get_job("weather_prefetch_retry"):
            scheduler.add_job(
                _prefetch_retry_job,
                trigger=IntervalTrigger(minutes=15),
                kwargs={"scheduler": scheduler},
                id="weather_prefetch_retry",
                name="Weather pre-fetch retry",
                misfire_grace_time=120,
            )
            log.info("Weather pre-fetch retry scheduled every 15 minutes")


def _prefetch_retry_job(scheduler: BlockingScheduler) -> None:
    """Retries weather pre-fetch every 15 min; stops on success or after 9 AM."""
    if datetime.now(tz=config.TIMEZONE).hour >= 9:
        log.info("Weather pre-fetch retry giving up (past 9 AM)")
        try:
            scheduler.remove_job("weather_prefetch_retry")
        except Exception:
            pass
        return
    if _do_prefetch():
        try:
            scheduler.remove_job("weather_prefetch_retry")
        except Exception:
            pass


def main() -> None:
    setup_logging()
    log.info("Born to be Wild starting")

    init_db(config.DB_PATH)

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

    scheduler.add_job(
        _prefetch_job,
        trigger=CronTrigger(hour=5, minute=5, timezone=config.TIMEZONE),
        kwargs={"scheduler": scheduler},
        id="weather_prefetch",
        name="Weather pre-fetch",
        misfire_grace_time=600,
    )
    log.info("Weather pre-fetch scheduled at 5:05 AM")

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
        snooze_date = date.fromisoformat(sub.snooze_until)
        if today <= snooze_date:
            log.info(f"Skipping {sub.email}: snoozed until {snooze_date}")
            return

    _run_send(sub)


def _run_send(sub: Subscriber) -> None:
    """Fetch weather for all subscriber locations, combine, and send the ride report."""
    locations = get_locations(config.DB_PATH, sub.id)
    if not locations:
        log.warning(f"No locations configured for {sub.name} — skipping")
        return

    now = datetime.now(tz=config.TIMEZONE)
    today = now.date()

    send_h, send_m = (int(x) for x in sub.send_time.split(":"))
    window_start = now.replace(hour=send_h, minute=send_m, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=config.FORECAST_HOURS)
    overnight_start = window_start - timedelta(hours=6)

    primary = locations[0]
    sunrise, sunset = get_sunrise_sunset(today, primary.lat, primary.lon, primary.timezone)

    log.info(
        f"Fetching weather for {sub.name} "
        f"({window_start.strftime('%-I:%M %p')} - {window_end.strftime('%-I:%M %p')}) "
        f"across {len(locations)} location(s)"
    )

    loc_results: list[tuple[Location, Assessment]] = []

    for loc in locations:
        slices = _fetch_with_cache_fallback(loc, window_start, window_end, today)
        if slices is None:
            continue

        overnight = _fetch_with_cache_fallback(loc, overnight_start, window_start, today)
        alerts = fetch_nws_alerts(loc.lat, loc.lon)

        assessment = evaluate(
            slices=slices,
            sunrise=sunrise,
            sunset=sunset,
            window_start=window_start,
            window_end=window_end,
            overnight_slices=overnight or None,
            nws_alerts=alerts or None,
        )
        loc_results.append((loc, assessment))

    if not loc_results:
        log.error(f"No weather data for any of {sub.name}'s locations — skipping")
        return

    assessment, triggering_zip = _combine_assessments(loc_results)
    log.info(
        f"Assessment for {sub.name}: {assessment.status}"
        + (f" [triggered by {triggering_zip}]" if triggering_zip else "")
    )

    location_label = _location_label(locations)

    if sub.message_email:
        send_report(
            name=sub.name,
            to_address=sub.email,
            assessment=assessment,
            window_start=window_start,
            window_end=window_end,
            sunrise=sunrise,
            sunset=sunset,
            location_label=location_label,
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
                triggering_zip=triggering_zip,
            )
        except Exception as exc:
            log.error(f"SMS failed for {sub.name}: {exc}")

    log_email_sent(config.DB_PATH, sub.id, assessment.status)


def _fetch_with_cache_fallback(
    loc: Location,
    window_start: datetime,
    window_end: datetime,
    today: date,
) -> list[HourlySlice] | None:
    """Try a live fetch; fall back to the daily cache. Returns None if unavailable."""
    try:
        return fetch_weather(window_start, window_end, loc.lat, loc.lon)
    except RuntimeError:
        pass

    cache_key = (loc.lat, loc.lon)
    if _daily_cache.get(cache_key) is not None and _cache_date == today:
        slices = filter_slices(_daily_cache[cache_key], window_start, window_end)
        if slices:
            log.info(f"Using cached weather for {loc.zip_code} ({len(slices)} slices)")
            return slices
        log.error(f"Cache has no data for {loc.zip_code} window")
    else:
        log.error(f"No weather data available for {loc.zip_code}")
    return None


def _combine_assessments(
    loc_results: list[tuple[Location, Assessment]],
) -> tuple[Assessment, str | None]:
    """Merge per-location assessments into a single worst-case result.

    Returns (merged_assessment, triggering_zip). triggering_zip is None for GO.
    The merged assessment's temp/wind stats span all locations; reasons come from
    the worst-status location.
    """
    status_rank = {"NO-GO": 0, "CAUTION": 1, "GO": 2}
    worst_loc, worst_a = min(loc_results, key=lambda x: status_rank[x[1].status])

    merged = Assessment(
        status=worst_a.status,
        nogo_reasons=worst_a.nogo_reasons,
        caution_notes=worst_a.caution_notes,
        temp_min=min(a.temp_min for _, a in loc_results),
        temp_max=max(a.temp_max for _, a in loc_results),
        wind_min=min(a.wind_min for _, a in loc_results),
        wind_max=max(a.wind_max for _, a in loc_results),
        gust_max=max(a.gust_max for _, a in loc_results),
        has_precip=worst_a.has_precip,
        precip_window=worst_a.precip_window,
        precip_prob_max=max(a.precip_prob_max for _, a in loc_results),
        conditions_summary=worst_a.conditions_summary,
    )
    triggering_zip = None if worst_a.status == "GO" else worst_loc.zip_code
    return merged, triggering_zip


def _location_label(locations: list[Location]) -> str:
    """Build a human-readable location string for email display."""
    parts = []
    for loc in locations:
        if loc.city and loc.state:
            label = f"{loc.city}, {loc.state} ({loc.zip_code})"
        else:
            label = loc.zip_code
        if loc.label:
            label = f"{loc.label}: {label}"
        parts.append(label)
    return " → ".join(parts)


if __name__ == "__main__":
    main()
