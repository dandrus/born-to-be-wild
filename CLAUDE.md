# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Born to be Wild** — A Python service that fetches weather for any US location and delivers motorcycle riders a daily GO / CAUTION / NO-GO ride report via email and/or SMS. Each subscriber can have multiple zip codes (useful for commute routes); the worst condition across all locations determines the verdict. Runs as a rootless Podman container managed by a systemd user service on `server0` (user: `nunchuckfusion`).

## Common Commands

```bash
# Build container
podman build -t born-to-be-wild .

# Run container
podman run -d \
  --name born-to-be-wild \
  --restart=always \
  -v ~/.config/born-to-be-wild/data:/data:Z \
  --env-file ~/.config/born-to-be-wild/.env \
  born-to-be-wild

# Manage subscribers via CLI — MUST use podman exec with DB_PATH so writes go to the mounted volume.
# Running cli.py directly on the host uses a fallback ./born-to-be-wild.sqlite that the container never sees.
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py list
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py add "Dan" dan@example.com "6:15 AM"
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py add "Dan" dan@example.com "6:15 AM" --phone 208-555-1234 --no-message-email --message-phone
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py update Dan --message-phone --no-message-email   # switch to SMS only
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py update 2 --message-email --message-phone        # enable both by ID
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py remove Dan
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py history Dan --days 14
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py stats

# Manage locations — zip codes are resolved at add time, stored as lat/lon in DB
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py list-locations Dan
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py add-location Dan 83646 --label home
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py add-location Dan 83716 --label work
podman exec -e DB_PATH=/data/born-to-be-wild.sqlite born-to-be-wild python cli.py remove-location Dan 83716

# Run tests
python3 -m pytest tests/

# Run a single test file
python3 -m pytest tests/test_conditions.py

# Run a single test
python3 -m pytest tests/test_conditions.py::test_nogo_rain

# View logs
journalctl --user -u born-to-be-wild.service -f

# Systemd management
systemctl --user enable --now born-to-be-wild.service
systemctl --user restart born-to-be-wild.service
loginctl enable-linger nunchuckfusion
```

## Architecture

The app is a **long-running Python process** (`src/main.py`) that uses APScheduler for four recurring jobs:

1. **Weather pre-fetch** — runs at 5:05 AM daily. Fetches a full 24-hour block (midnight–midnight) and caches it in memory. If both sources fail, schedules a retry every 15 minutes until success or 9 AM.
2. **Per-subscriber report jobs** — scheduled at each subscriber's configured send time (weekdays, non-holidays only). Each job tries a fresh weather fetch; on failure falls back to the morning cache; if neither is available the send is silently skipped (no message sent).
3. **Inbox polling loop** — runs every 5 minutes via IMAP, parses reply commands, and dispatches responses.
4. **Health check** — runs daily at 10:00 AM (see below).

### Key Design Points

- **Weather:** Open-Meteo is primary; NWS (`api.weather.gov`) is failover for the base hourly data (both are free/keyless). NOAA HRRR (via Open-Meteo's `models=hrrr_conus`, free/keyless) and Pirate Weather (requires `PIRATE_WEATHER_API_KEY`, free 10k/mo tier) are additionally consulted as best-effort **precip-only checks** — if either detects precipitation in an hour the primary source missed, that slice's `has_precip` flag is OR'd on. This catches localized/convective rain that the default Open-Meteo blend smooths away (see `_augment_precip_signals` in `weather.py`). 5-second timeout per request. Supplemental-source failures are logged and swallowed — they never block a report. Pre-fetched at 5:05 AM for all active subscriber locations (keyed by lat/lon) and cached in memory for the day; send jobs use the cache as a fallback if a fresh fetch fails. If no data is available at send time, the send is skipped — no "unavailable" message is sent.
- **Locations:** Each subscriber has one or more zip codes stored in `subscriber_locations`. Zip codes are resolved at add time via `zippopotam.us` → lat/lon/city/state/timezone stored in DB. No external calls at send time. First location is primary (used for sunrise/sunset). `src/location_resolver.py` handles resolution. Existing subscribers are migrated to Meridian, ID (83642) if they have no locations.
- **Forecast window:** Per-subscriber — starts at their send time, extends 12 hours forward. Weather is fetched per-location; worst condition across all locations determines the overall status (`_combine_assessments` in `main.py`).
- **Condition tiers:** `conditions.py` evaluates NO-GO → CAUTION → GO in priority order. Comparisons use `round(temp_min)` so the displayed temperature matches the tier decision. NO-GO triggers: rounded temp ≤ 44°F (displays as "44F" or below), precipitation, wind > 50 mph, NWS hazards. CAUTION triggers: wind 40–50 mph, rain probability 30–50%, overnight rain, partial darkness in window. Temperature does not trigger CAUTION — 45°F and above is GO from a temp perspective. Precipitation window: single rain hour shows "starting H:MM AM/PM"; multiple hours show "H:MM AM/PM - H:MM AM/PM".
- **Timezone:** All times in `America/Boise` using `zoneinfo`. Times stored and compared in local Boise time. Scheduler is timezone-aware.
- **Database:** SQLite at `DB_PATH` (env var), mounted as a Podman volume at `/data/`. `subscribers.py` handles all DB operations. Schema uses `message_email` (INTEGER 0/1) and `message_phone` (INTEGER 0/1) to control notification channels; legacy `notify_via` column is retained in the DB but unused — migrated on startup.
- **Email sending:** `smtplib` over TLS to `smtp.gmail.com:587`. Gmail app password in `.env`.
- **SMS sending:** `sms_sender.py` via Textbelt API (`https://textbelt.com/text`). Requires `TEXTBELT_API_KEY` in `.env`. Messages are pure ASCII — no emojis or `°` symbol (both force Unicode encoding, tripling credit usage). Hard 160-char limit enforced in `build_sms` (GSM-7: 140 bytes × 8/7 = 160 chars per segment): optional detail lines are dropped first, then hard-truncated. Subscribers with `message_phone = 1` receive texts.
- **Command parsing:** `email_reader.py` matches sender email to subscriber, reads first line of reply body (case-insensitive), dispatches to `commands.py`. Email marked read after processing.
- **Health check:** `health.py` — if it's a weekday non-holiday and no emails have been sent in 24 hours, sends an alert to `ADMIN_EMAIL`.
- **Logging:** Structured JSON to stdout (captured by journald). DEBUG = raw API, INFO = sent/commands, WARNING = API failures, ERROR = exceptions.

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `src/main.py` | Entry point; builds and starts APScheduler with all jobs; `_combine_assessments` merges per-location results |
| `src/config.py` | Loads `.env`, exposes typed constants |
| `src/weather.py` | Open-Meteo + NWS fetch with failover |
| `src/conditions.py` | GO/CAUTION/NO-GO evaluation from parsed forecast |
| `src/email_sender.py` | Compose and send ride report emails via SMTP |
| `src/sms_sender.py` | Compose and send SMS ride reports via Textbelt |
| `src/email_reader.py` | IMAP polling, reply detection, command dispatch |
| `src/commands.py` | Handler for each subscriber command (SNOOZE, RESUME, etc.) |
| `src/subscribers.py` | SQLite CRUD for subscribers table |
| `src/holidays.py` | US federal holiday lookup via `holidays` package |
| `src/location_resolver.py` | Resolve zip code → (lat, lon, city, state, timezone) via zippopotam.us + timezonefinder |
| `src/sun.py` | Sunrise/sunset for a given lat/lon/timezone and date |
| `src/health.py` | 24-hour no-send health alert logic |
| `src/logging_config.py` | JSON log formatter setup |
| `cli.py` | Standalone subscriber management CLI |

## Environment Variables (`.env`)

```
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
ADMIN_EMAIL=
DB_PATH=/data/born-to-be-wild.sqlite
LOG_LEVEL=INFO
TEXTBELT_API_KEY=
PIRATE_WEATHER_API_KEY=
```

## Skip Logic

Emails are skipped on: **off-season** (Nov 30 – Feb 28/29), **weekends** (Saturday/Sunday), and **US federal holidays** (current-year, via `holidays` Python package). Subscribers can also self-snooze via the `SNOOZE [X]` reply command.
