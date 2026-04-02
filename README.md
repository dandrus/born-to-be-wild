# Born to be Wild

Daily motorcycle ride weather reports for Ada and Canyon Counties, Idaho. Sends a GO / CAUTION / NO-GO email to each subscriber at their configured time every weekday (non-holiday). Runs as a rootless Podman container managed by a systemd user service.

## Prerequisites

- Podman installed on the server
- A Gmail account with an App Password ([setup guide](https://support.google.com/accounts/answer/185833))
  - Enable 2-Step Verification, then create an App Password under Security → App Passwords

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

**4. Build the container**

```bash
podman build -t born-to-be-wild .
```

**5. Run the container**

```bash
podman run -d \
  --name born-to-be-wild \
  --restart=always \
  -v ~/born-to-be-wild-data:/data:Z \
  --env-file ~/.config/born-to-be-wild/.env \
  localhost/born-to-be-wild
```

**6. Install the systemd user service**

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
A daily health check runs at 10:00 AM Boise time. If no emails have been sent in the last 24 hours on a weekday, an alert is sent to `ADMIN_EMAIL`.

**New subscribers not receiving emails:**
The scheduler loads subscribers at startup. After adding a subscriber via `cli.py`, restart the service:
```bash
systemctl --user restart born-to-be-wild.service
```
