# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Born to be Wild** — A Python service that fetches weather for Ada/Canyon Counties, Idaho and emails motorcycle riders a daily GO / CAUTION / NO-GO ride report. Runs as a rootless Podman container managed by a systemd user service on `server0` (user: `nunchuckfusion`).

## Common Commands

```bash
# Build container
podman build -t born-to-be-wild .

# Run container
podman run -d \
  --name born-to-be-wild \
  --restart=always \
  -v ~/born-to-be-wild-data:/data:Z \
  --env-file ~/.config/born-to-be-wild/.env \
  born-to-be-wild

# Manage subscribers via CLI (local or inside container)
python cli.py list
python cli.py add "Dan" dan@example.com "6:15 AM"
python cli.py add "Dan" dan@example.com "6:15 AM" --phone 208-555-1234 --no-message-email --message-phone
python cli.py update Dan --message-phone --no-message-email   # switch to SMS only
python cli.py update 2 --message-email --message-phone        # enable both by ID
python cli.py remove Dan
python cli.py history Dan --days 14
python cli.py stats

# Run inside running container
podman exec born-to-be-wild python cli.py list

# Run tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_conditions.py

# Run a single test
python -m pytest tests/test_conditions.py::test_nogo_rain

# View logs
journalctl --user -u born-to-be-wild.service -f

# Systemd management
systemctl --user enable --now born-to-be-wild.service
systemctl --user restart born-to-be-wild.service
loginctl enable-linger nunchuckfusion
```

## Architecture

The app is a **long-running Python process** (`src/main.py`) that uses APScheduler for two recurring jobs:

1. **Per-subscriber email jobs** — scheduled at each subscriber's configured send time (weekdays, non-holidays only). Each job fetches fresh weather data, evaluates conditions, and sends one email.
2. **Inbox polling loop** — runs every 5 minutes via IMAP, parses reply commands, and dispatches responses.

### Key Design Points

- **Weather:** Open-Meteo is primary; NWS (`api.weather.gov`) is failover. Both are free/keyless. 5-second timeout per request. If both fail, email sends anyway with a warning note.
- **Forecast window:** Per-subscriber — starts at their email send time, extends 12 hours forward. One shared lat/lon: Meridian, ID (43.6121, -116.3915).
- **Condition tiers:** `conditions.py` evaluates NO-GO → CAUTION → GO in priority order. Comparisons use `round(temp_min)` so the displayed temperature matches the tier decision. NO-GO triggers: rounded temp ≤ 44°F (displays as "44F" or below), precipitation, wind > 50 mph, NWS hazards. CAUTION triggers: rounded temp 45–49°F, wind 40–50 mph, rain probability 30–50%, overnight rain, partial darkness in window. Precipitation window: single rain hour shows "starting H:MM AM/PM"; multiple hours show "H:MM AM/PM - H:MM AM/PM".
- **Timezone:** All times in `America/Boise` using `zoneinfo`. Times stored and compared in local Boise time. Scheduler is timezone-aware.
- **Database:** SQLite at `DB_PATH` (env var), mounted as a Podman volume at `/data/`. `subscribers.py` handles all DB operations. Schema uses `message_email` (INTEGER 0/1) and `message_phone` (INTEGER 0/1) to control notification channels; legacy `notify_via` column is retained in the DB but unused — migrated on startup.
- **Email sending:** `smtplib` over TLS to `smtp.gmail.com:587`. Gmail app password in `.env`.
- **SMS sending:** `sms_sender.py` via Textbelt API (`https://textbelt.com/text`). Requires `TEXTBELT_API_KEY` in `.env`. Messages are pure ASCII — no emojis or `°` symbol (both force Unicode encoding, tripling credit usage). Hard 140-byte limit enforced in `build_sms`: optional detail lines are dropped first, then hard-truncated. Subscribers with `message_phone = 1` receive texts.
- **Command parsing:** `email_reader.py` matches sender email to subscriber, reads first line of reply body (case-insensitive), dispatches to `commands.py`. Email marked read after processing.
- **Health check:** `health.py` — if it's a weekday non-holiday and no emails have been sent in 24 hours, sends an alert to `ADMIN_EMAIL`.
- **Logging:** Structured JSON to stdout (captured by journald). DEBUG = raw API, INFO = sent/commands, WARNING = API failures, ERROR = exceptions.

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `src/main.py` | Entry point; builds and starts APScheduler with all jobs |
| `src/config.py` | Loads `.env`, exposes typed constants |
| `src/weather.py` | Open-Meteo + NWS fetch with failover |
| `src/conditions.py` | GO/CAUTION/NO-GO evaluation from parsed forecast |
| `src/email_sender.py` | Compose and send ride report emails via SMTP |
| `src/sms_sender.py` | Compose and send SMS ride reports via Textbelt |
| `src/email_reader.py` | IMAP polling, reply detection, command dispatch |
| `src/commands.py` | Handler for each subscriber command (SNOOZE, RESUME, etc.) |
| `src/subscribers.py` | SQLite CRUD for subscribers table |
| `src/holidays.py` | US federal holiday lookup via `holidays` package |
| `src/sun.py` | Sunrise/sunset for Meridian, ID for a given date |
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
```

## Skip Logic

Emails are skipped on: **off-season** (Nov 30 – Feb 28/29), **weekends** (Saturday/Sunday), and **US federal holidays** (current-year, via `holidays` Python package). Subscribers can also self-snooze via the `SNOOZE [X]` reply command.
