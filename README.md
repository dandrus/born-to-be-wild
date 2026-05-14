# Born to be Wild

Daily motorcycle ride weather reports for any US location. Sends a GO / CAUTION / NO-GO decision to each subscriber at their configured time every weekday (non-holiday) via **email and/or SMS**. Runs as a rootless Podman container managed by a systemd user service.

Each subscriber can have **multiple zip codes** — useful for commute routes where you leave one zip and arrive at another. The worst condition across all your locations determines the overall status.

## Prerequisites

- Podman installed on the server
- A Gmail account with an App Password ([setup guide](https://support.google.com/accounts/answer/185833))
  - Enable 2-Step Verification, then create an App Password under Security → App Passwords
- (Optional) A [Textbelt](https://textbelt.com) API key for SMS delivery

## Quick Start

**1. Clone and configure**

```bash
git clone <repo> born-to-be-wild
cd born-to-be-wild
mkdir -p ~/.config/born-to-be-wild
cp .env.example ~/.config/born-to-be-wild/.env
# Edit the .env with your credentials
```

**2. Create the data directory**

```bash
mkdir -p ~/born-to-be-wild-data
```

**3. Add your first subscriber**

```bash
# Initialize the DB and add a subscriber (run from the project directory)
# Email only (default)
DB_PATH=~/born-to-be-wild-data/born-to-be-wild.sqlite python3 cli.py add "Daniel" "daniel@example.com" "6:15 AM"

# SMS only
DB_PATH=~/born-to-be-wild-data/born-to-be-wild.sqlite python3 cli.py add "Dan" "dan@example.com" "6:15 AM" --phone 208-555-1234 --no-message-email --message-phone

# Both email and SMS
DB_PATH=~/born-to-be-wild-data/born-to-be-wild.sqlite python3 cli.py add "Dan" "dan@example.com" "6:15 AM" --phone 208-555-1234 --message-phone
```

**4. Add locations for the subscriber**

```bash
# Add a home zip code (first location added is the primary — used for sunrise/sunset)
DB_PATH=~/born-to-be-wild-data/born-to-be-wild.sqlite python3 cli.py add-location Dan 83646 --label home

# Add a work/destination zip code
DB_PATH=~/born-to-be-wild-data/born-to-be-wild.sqlite python3 cli.py add-location Dan 83716 --label work
```

New subscribers are seeded with zip **83642 (Meridian, ID)** if no location is added. Replace it or add more as needed.

**5. Build the container**

```bash
podman build -t born-to-be-wild .
```

**6. Run the container**

```bash
podman run -d \
  --name born-to-be-wild \
  --restart=always \
  -v ~/born-to-be-wild-data:/data:Z \
  --env-file ~/.config/born-to-be-wild/.env \
  localhost/born-to-be-wild
```

**7. Install the systemd user service**

```bash
mkdir -p ~/.config/systemd/user
cp deploy/born-to-be-wild.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now born-to-be-wild.service
loginctl enable-linger $USER
```

## CLI Usage

Run the CLI directly on the server or inside the container:

```bash
# On the server (from the project directory)
python3 cli.py <command>

# Inside the running container
podman exec born-to-be-wild python cli.py <command>
```

Subscribers can be looked up by **name or ID** for all commands that take an identifier.

### Subscriber management

| Command | Example |
|---|---|
| List all subscribers | `python3 cli.py list` |
| Add a subscriber (email only) | `python3 cli.py add "Ben" ben@example.com "7:00 AM"` |
| Add a subscriber (SMS only) | `python3 cli.py add "Ben" ben@example.com "7:00 AM" --phone 208-555-1234 --no-message-email --message-phone` |
| Add a subscriber (email + SMS) | `python3 cli.py add "Ben" ben@example.com "7:00 AM" --phone 208-555-1234 --message-phone` |
| Remove a subscriber | `python3 cli.py remove Ben` |
| Remove a subscriber by ID | `python3 cli.py remove 2` |
| Update name | `python3 cli.py update Ben --name "Benjamin"` |
| Update send time | `python3 cli.py update Ben --time "6:30 AM"` |
| Switch to SMS only | `python3 cli.py update Ben --no-message-email --message-phone` |
| Switch to email only | `python3 cli.py update Ben --message-email --no-message-phone` |
| Enable both channels | `python3 cli.py update Ben --message-email --message-phone` |
| Add a phone number | `python3 cli.py update Ben --phone 208-555-1234` |
| Enable/disable | `python3 cli.py update Ben --active false` |
| Forecast accuracy stats | `python3 cli.py stats` |
| Send history | `python3 cli.py history Ben --days 14` |

### Location management

Each subscriber can have one or more zip codes. The **first location added is the primary** — its coordinates are used for sunrise/sunset times. Weather is fetched for every location; the worst condition across all locations determines the overall GO / CAUTION / NO-GO status.

| Command | Example |
|---|---|
| List locations | `python3 cli.py list-locations Ben` |
| Add a location | `python3 cli.py add-location Ben 83646 --label home` |
| Add a second location | `python3 cli.py add-location Ben 83716 --label work` |
| Remove a location | `python3 cli.py remove-location Ben 83716` |

Zip codes are resolved at add time via [zippopotam.us](https://api.zippopotam.us) — coordinates and timezone are stored in the database so no external calls happen at send time.

## SMS Integration

SMS delivery uses [Textbelt](https://textbelt.com), a pay-as-you-go SMS API (no monthly fees, ~$0.01/message). Set `TEXTBELT_API_KEY` in your `.env` to enable it.

SMS messages are formatted for maximum information in 160 characters (GSM-7 encoding — no emojis or degree symbols, which would triple credit usage):

```
GO - Thu May 14
Temp: 58-75F
15-25 mph
SR 5:45 AM SS 8:32 PM
Reply STOP to unsub
```

For CAUTION or NO-GO, the zip code that triggered the status appears in the header, and a single worst-condition line is shown (precipitation → temperature → wind, in priority order):

```
NO-GO - Thu May 14 [83716]
Ice 6AM-8AM
Temp: 43-58F
20-30 mph
SR 5:45 AM SS 8:32 PM
Reply STOP to unsub
```

## Email Reply Commands

Subscribers can reply to any ride report email with:

| Command | Action |
|---|---|
| `HELP` | Get the full command list |
| `STATUS` | On-demand weather check right now |
| `FORECAST` | 3-day ride outlook |
| `SNOOZE 5` | Pause emails for 5 days |
| `RESUME` | Cancel an active snooze |
| `CHANGE TIME 7:00 AM` | Change your daily send time |
| `UNSUBSCRIBE` | Stop all emails |
| `SUBSCRIBE` | Re-activate your subscription |
| `REPORT ACCURATE` | Today's forecast was correct |
| `REPORT WRONG` | Today's forecast was incorrect |

## Troubleshooting

**View live logs:**
```bash
journalctl --user -u born-to-be-wild.service -f
```

**View recent logs:**
```bash
journalctl --user -u born-to-be-wild.service -n 100
```

**Restart the service:**
```bash
systemctl --user restart born-to-be-wild.service
```

**Stop the service:**
```bash
systemctl --user stop born-to-be-wild.service
```

**Rebuild and redeploy:**
```bash
podman build -t born-to-be-wild .
systemctl --user restart born-to-be-wild.service
```

**Health checks:**
A daily health check runs at 10:00 AM Boise time. If no messages have been sent in the last 24 hours on a weekday, an alert is sent to `ADMIN_EMAIL`.

**Weather pre-fetch:**
Weather is pre-fetched at 5:05 AM daily for all active subscriber locations and cached in memory. Send jobs use this cache as a fallback if a live fetch fails at send time. If neither is available, the send is silently skipped — no "unavailable" message is sent. Pre-fetch failures retry every 15 minutes until 9 AM.

**New subscribers not receiving reports:**
The scheduler loads subscribers at startup. After adding a subscriber via `cli.py`, restart the service:
```bash
systemctl --user restart born-to-be-wild.service
```
