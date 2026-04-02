# Born to be Wild — Motorcycle Ride Weather Notification System

## Project Overview

Build a Python application called **Born to be Wild** that checks weather conditions daily and emails motorcycle riders a GO / CAUTION / NO-GO ride report. The system runs as a long-lived service on a Debian/Ubuntu Linux server (hostname: server0, username: nunchuckfusion) deployed as a rootless Podman container managed by a systemd user service.

## Architecture

### Runtime

- **Language:** Python 3.11+
- **Deployment:** Rootless Podman container with a systemd user service (`~/.config/systemd/user/born-to-be-wild.service`)
- **Scheduler:** Use APScheduler (or similar) inside the long-running process to handle per-subscriber send times and the inbox polling loop
- **Database:** SQLite (single file, mounted as a Podman volume)
- **Config:** `.env` file for secrets, mounted into the container

### Project Structure

```
born-to-be-wild/
├── Containerfile
├── requirements.txt
├── .env.example
├── README.md
├── src/
│   ├── __init__.py
│   ├── main.py              # Entry point, scheduler setup
│   ├── config.py             # Load .env, constants
│   ├── weather.py            # Weather API clients (Open-Meteo + NWS)
│   ├── conditions.py         # GO/CAUTION/NO-GO evaluation logic
│   ├── email_sender.py       # Gmail SMTP sending
│   ├── email_reader.py       # Gmail IMAP inbox polling + command parsing
│   ├── commands.py           # Command handler logic
│   ├── subscribers.py        # Subscriber database operations
│   ├── holidays.py           # US federal holiday detection
│   ├── sun.py                # Sunrise/sunset calculation
│   ├── health.py             # Health check / self-alert
│   └── logging_config.py     # Structured logging setup
├── cli.py                    # CLI tool for subscriber management
└── tests/
    ├── test_conditions.py
    ├── test_commands.py
    └── test_weather.py
```

## Weather

### APIs (Dual Source with Failover)

1. **Open-Meteo** (free, no API key) — primary source for hourly forecast data
2. **National Weather Service (NWS)** API (free, no key, `api.weather.gov`) — secondary source and cross-reference

**Failover logic:** If the primary API fails or times out (5-second timeout), fall back to the secondary. If both fail, send the email anyway with a note: "⚠ Weather data unavailable — could not reach weather services. Ride with caution and check conditions manually."

### Location

- **Riding zone:** Ada and Canyon Counties, Idaho (use a central lat/lon point such as Meridian, ID: 43.6121, -116.3915)
- All subscribers share the same riding zone — one weather fetch per check window

### Forecast Window

- For each subscriber, check the **hourly forecast starting from their email time through 10 hours later**
- Example: Dan's email is at 6:15 AM → check 6:00 AM – 4:00 PM

### Condition Evaluation (Three-Tier)

**NO-GO** — Any of the following in the forecast window:
- Temperature that rounds to 44°F or below (e.g., 44.4°F → NO-GO; 44.9°F displays as 45°F → CAUTION)
- Rain, snow, hail, freezing rain, thunderstorms, ice pellets
- Sustained wind or gusts exceeding 50 mph
- Any other NWS hazard/warning for the area (e.g., dense fog advisory, dust storm)

**CAUTION** — None of the NO-GO triggers, but any of:
- Temperature that rounds to 45°F–49°F at any hour
- Sustained wind or gusts between 40 and 50 mph
- Rain probability between 30% and 50% with no actual precipitation in the forecast
- Overnight rain detected (rain in the 6 hours before the rider's start time) → "Roads may be wet" note
- Part of the 10-hour window falls before sunrise or after sunset → "Reduced visibility: sunrise at X / sunset at X" note

**GO** — None of the above

### Sunrise / Sunset

- Calculate sunrise and sunset for Meridian, ID for the current date
- If any portion of the 10-hour ride window is before sunrise or after sunset, include a note in the email body (this is informational, contributes to CAUTION but not NO-GO on its own)

## Email

### Sending (Gmail SMTP)

- Use a dedicated Gmail account with an app password
- SMTP via `smtplib` over TLS (smtp.gmail.com:587)
- Credentials stored in `.env`:
  ```
  GMAIL_ADDRESS=your-address@gmail.com
  GMAIL_APP_PASSWORD=your-app-password
  ```

### Schedule

- Each subscriber has their own send time (e.g., Dan at 6:15 AM, Ben at 7:00 AM)
- Weather is fetched fresh right before each subscriber's email is sent
- **Skip weekends** (Saturday and Sunday)
- **Skip US federal holidays** (use the `holidays` Python package for current-year federal holidays)

### Email Format

**Subject line:**
```
Ride Report: GO - Mon Mar 24, 2026
Ride Report: CAUTION - Mon Mar 24, 2026
Ride Report: NO-GO - Mon Mar 24, 2026
```

**Body (plain text):**
```
Good morning, Dan!

TODAY'S RIDE STATUS: GO ✅

Forecast for Ada/Canyon County (6:15 AM - 4:15 PM):
- Temperature range: 58°F - 74°F
- Wind: 8-15 mph, gusts up to 22 mph
- Precipitation: None expected
- Conditions: Mostly sunny

Sunrise: 7:32 AM | Sunset: 7:48 PM

Have a great ride!

---
COMMANDS (reply to this email):
  HELP          - List all commands
  STATUS        - Get an on-demand weather check right now
  FORECAST      - Get the next 3-day outlook
  SNOOZE [X]    - Pause emails for X days (e.g., SNOOZE 5)
  RESUME        - Cancel a snooze early
  CHANGE TIME [HH:MM AM/PM] - Change your email time
  UNSUBSCRIBE   - Stop all emails
  REPORT ACCURATE - Today's forecast was correct
  REPORT WRONG    - Today's forecast was incorrect
```

**NO-GO example body:**
```
Good morning, Dan!

TODAY'S RIDE STATUS: NO-GO 🚫

Forecast for Ada/Canyon County (6:15 AM - 4:15 PM):
- Temperature range: 38°F - 52°F
- Wind: 12-25 mph, gusts up to 35 mph
- Precipitation: Rain expected 9:00 AM - 1:00 PM
- Conditions: Rain and cold temperatures

❌ Rain in forecast (9:00 AM - 1:00 PM)
❌ Temperature below 45°F (low of 38°F)

Sunrise: 7:32 AM | Sunset: 7:48 PM

Stay safe, maybe next time.

---
COMMANDS (reply to this email):
  ...
```

**CAUTION example additions in body:**
```
⚠️ Heads up:
- Roads may be wet (rain overnight until 3:00 AM)
- Temperature near threshold: low of 48°F at 7:00 AM
```

## Inbox Polling & Reply Commands

### Polling

- Check the Gmail inbox via IMAP (imap.gmail.com:993) on a regular interval (every 5 minutes)
- Look for replies to sent ride report emails
- Match the sender's email address to a subscriber in the database
- Parse the first line/word(s) of the reply body as a command (case-insensitive)
- After processing, mark the email as read

### Commands

| Command | Action | Confirmation Reply |
|---|---|---|
| `HELP` | Reply with the full command list | Sends the command reference |
| `STATUS` | Fetch current weather and send a ride report immediately | Sends a ride report email |
| `FORECAST` | Send a 3-day forecast outlook | Sends a 3-day summary email |
| `SNOOZE [X]` | Pause daily emails for X days (default 1 if no number given) | "Snoozed for X days. Your next report will arrive on [date]. Reply RESUME to cancel." |
| `RESUME` | Cancel an active snooze | "Snooze cancelled. You'll receive your next report tomorrow." / "You don't have an active snooze." |
| `CHANGE TIME [HH:MM AM/PM]` | Update the subscriber's daily email time | "Email time updated to [time]. Effective tomorrow." |
| `UNSUBSCRIBE` | Mark subscriber as inactive, stop all emails | "You've been unsubscribed. Reply SUBSCRIBE to re-activate." |
| `SUBSCRIBE` | Re-activate a previously unsubscribed subscriber | "Welcome back! Your daily reports will resume tomorrow at [time]." |
| `REPORT ACCURATE` | Log that today's forecast was accurate | "Thanks for the feedback! Logged as accurate." |
| `REPORT WRONG` | Log that today's forecast was inaccurate | "Thanks for the feedback! Logged as inaccurate." |
| (unrecognized) | Reply with the help/command list | "I didn't understand that. Here are the available commands: ..." |

## CLI Subscriber Management

Create `cli.py` as a standalone script (also runnable inside the container via `podman exec`) for managing subscribers. All commands that take an identifier accept either the subscriber's **name** or **numeric ID**.

```
Usage: python cli.py <command> [options]

Commands:
  list                                     Show all subscribers and their settings
  add <name> <email> <time>                Add a subscriber (time as "6:15 AM")
    --phone <number>                         Phone number for SMS (e.g. 208-555-1234)
    --message-email / --no-message-email     Send via email (default: on)
    --message-phone / --no-message-phone     Send via SMS (default: off)
  remove <name|id>                         Remove a subscriber
  update <name|id>                         Update subscriber settings
    --name "NewName"                         Change display name
    --time "7:00 AM"                         Change send time
    --phone <number>                         Update phone number
    --message-email / --no-message-email     Toggle email notifications
    --message-phone / --no-message-phone     Toggle SMS notifications
    --active true/false                      Enable or disable the subscriber
  stats                                    Show forecast accuracy ratings
  history <name|id> [--days 7]             Show recent send history for a subscriber
```

**Examples:**
```bash
python cli.py add "Dan" dan@example.com "6:15 AM" --phone 208-555-1234 --no-message-email --message-phone
python cli.py update Dan --message-phone --no-message-email
python cli.py update 2 --message-email --message-phone
python cli.py remove Ben
python cli.py history Dan --days 14
```

## Health Check & Self-Alert

- If the service is running and it's a weekday (non-holiday) but **no emails have been sent in the last 24 hours**, send an alert email to the admin (first subscriber or a configured admin email in `.env`)
- Subject: `⚠ Born to be Wild — Health Check Alert`
- Body: reason for the alert (e.g., "No emails sent in the last 24 hours. Service may be stuck.")
- Also log a warning to structured logs

## Logging

- Use Python's `logging` module with structured JSON output
- Log levels: DEBUG for weather API raw responses, INFO for emails sent / commands processed, WARNING for API failures / failovers, ERROR for unhandled exceptions
- Log to stdout (Podman will capture via `journalctl --user`)

## Deployment Files

### Containerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-m", "src.main"]
```

### .env.example

```env
GMAIL_ADDRESS=your-address@gmail.com
GMAIL_APP_PASSWORD=your-app-password
ADMIN_EMAIL=your-personal@email.com
DB_PATH=/data/born-to-be-wild.sqlite
LOG_LEVEL=INFO
TEXTBELT_API_KEY=
```

### Podman + Systemd

Provide:
1. `podman build` and `podman run` commands with:
   - Volume mount for SQLite DB: `-v ~/born-to-be-wild-data:/data:Z`
   - Volume mount for .env: `--env-file ~/.config/born-to-be-wild/.env`
   - Container name: `born-to-be-wild`
   - Restart policy: `--restart=always`
2. A systemd user service file at `~/.config/systemd/user/born-to-be-wild.service` using `podman` start/stop
3. Enable with `systemctl --user enable --now born-to-be-wild.service` and `loginctl enable-linger nunchuckfusion`

## README.md

Include a README with:
1. Project description
2. Prerequisites (Podman, Gmail app password setup steps)
3. Quick start (clone, configure .env, build, run)
4. CLI usage examples
5. Command reference (email reply commands)
6. Troubleshooting (logs, health checks)

## Implementation Notes

- Use `pytz` or `zoneinfo` with timezone `America/Boise` for all time calculations
- All times stored and compared in local Boise time
- The scheduler should be timezone-aware
- Handle DST transitions gracefully
- Be defensive with API parsing — weather APIs can return unexpected data
- Include type hints throughout
- Include docstrings on all public functions
- Write unit tests for condition evaluation logic and command parsing at minimum

