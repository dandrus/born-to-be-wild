#!/usr/bin/env python3
"""Subscriber management CLI for Born to be Wild."""
from __future__ import annotations
import argparse
import os
import re
import sys
from datetime import datetime

os.environ.setdefault("GMAIL_ADDRESS", "unused@example.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "unused")

from dotenv import load_dotenv
load_dotenv()

from src import config
from src.subscribers import (
    init_db,
    add_subscriber,
    list_subscribers,
    get_by_email,
    get_by_id,
    get_by_name,
    delete_subscriber,
    update_subscriber,
    set_active,
    get_accuracy_stats,
    get_email_history,
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(db_path: str) -> None:
    subs = list_subscribers(db_path)
    if not subs:
        print("No subscribers found.")
        return
    print(f"{'ID':<4} {'Name':<18} {'Email':<28} {'Time':<8} {'Email?':<7} {'SMS?':<6} {'Phone':<16} {'Active':<8} {'Snooze Until'}")
    print("-" * 108)
    for s in subs:
        print(
            f"{s.id:<4} {s.name:<18} {s.email:<28} {s.send_time:<8} "
            f"{'Yes' if s.message_email else 'No':<7} {'Yes' if s.message_phone else 'No':<6} "
            f"{s.phone or '-':<16} {'Yes' if s.active else 'No':<8} {s.snooze_until or '-'}"
        )


def cmd_add(
    db_path: str, name: str, email: str, time_str: str,
    phone: str | None, message_email: bool, message_phone: bool,
) -> None:
    send_time = _parse_time(time_str)
    if phone:
        phone = _parse_phone(phone)
    if message_phone and not phone:
        sys.exit("--message-phone requires a phone number (--phone)")
    if not message_email and not message_phone:
        sys.exit("At least one of --message-email or --message-phone must be enabled")
    try:
        sub = add_subscriber(db_path, name, email, send_time, phone=phone,
                             message_email=message_email, message_phone=message_phone)
        channels = " + ".join(filter(None, [
            "email" if sub.message_email else None,
            "SMS" if sub.message_phone else None,
        ]))
        print(f"Added: {sub.name} ({sub.email}) at {_display_time(sub.send_time)} — notify via {channels}")
    except ValueError as e:
        sys.exit(str(e))


def cmd_remove(db_path: str, identifier: str) -> None:
    sub = _resolve_subscriber(db_path, identifier)
    delete_subscriber(db_path, sub.id)
    print(f"Removed: {sub.name} ({sub.email})")


def cmd_update(
    db_path: str,
    identifier: str,
    name: str | None,
    time_str: str | None,
    active: str | None,
    phone: str | None,
    message_email: bool | None,
    message_phone: bool | None,
) -> None:
    sub = _resolve_subscriber(db_path, identifier)
    changes: list[str] = []

    if name is not None:
        update_subscriber(db_path, sub.id, name=name)
        changes.append(f"name → {name}")

    if time_str is not None:
        send_time = _parse_time(time_str)
        update_subscriber(db_path, sub.id, send_time=send_time)
        changes.append(f"time → {_display_time(send_time)}")

    if phone is not None:
        parsed = _parse_phone(phone) if phone else None
        update_subscriber(db_path, sub.id, phone=parsed)
        changes.append(f"phone → {parsed or 'cleared'}")

    if message_email is not None:
        update_subscriber(db_path, sub.id, message_email=message_email)
        changes.append(f"message-email → {'on' if message_email else 'off'}")

    if message_phone is not None:
        effective_phone = phone or sub.phone
        if message_phone and not effective_phone:
            sys.exit("--message-phone requires a phone number (--phone)")
        update_subscriber(db_path, sub.id, message_phone=message_phone)
        changes.append(f"message-phone → {'on' if message_phone else 'off'}")

    if active is not None:
        val = active.lower()
        if val not in ("true", "false"):
            sys.exit("--active must be 'true' or 'false'")
        set_active(db_path, sub.id, val == "true")
        changes.append(f"active → {val}")

    if not changes:
        sys.exit("Specify at least one of --name, --time, --phone, --message-email, --message-phone, --active")

    print(f"Updated {sub.name} ({sub.email}): {', '.join(changes)}")
    print("Note: restart the service to apply send_time or active changes to the scheduler.")


def cmd_stats(db_path: str) -> None:
    rows = get_accuracy_stats(db_path)
    if not rows:
        print("No data yet.")
        return
    print(f"{'Name':<20} {'Email':<32} {'Sent':<6} {'Accurate':<10} {'Wrong':<7} {'Accuracy'}")
    print("-" * 84)
    for r in rows:
        rated = (r["accurate"] or 0) + (r["wrong"] or 0)
        pct = f"{(r['accurate'] or 0) / rated * 100:.0f}%" if rated else "-"
        print(
            f"{r['name']:<20} {r['email']:<32} {r['total_sent'] or 0:<6} "
            f"{r['accurate'] or 0:<10} {r['wrong'] or 0:<7} {pct}"
        )


def cmd_history(db_path: str, identifier: str, days: int) -> None:
    sub = _resolve_subscriber(db_path, identifier)
    entries = get_email_history(db_path, sub.id, days=days)
    if not entries:
        print(f"No history for {sub.name} in the last {days} day(s).")
        return
    print(f"History for {sub.name} ({sub.email}) — last {days} day(s):\n")
    print(f"{'Sent At (UTC)':<28} {'Status':<10} {'Accuracy'}")
    print("-" * 52)
    for e in entries:
        print(f"{e['sent_at'][:19]:<28} {e['status']:<10} {e['accuracy'] or '-'}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_phone(value: str) -> str:
    """Normalize a phone number to E.164 format (+1XXXXXXXXXX)."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        sys.exit(f"Invalid phone number: {value!r}. Use 10-digit US number e.g. 208-555-1234")
    return f"+{digits}"


def _resolve_subscriber(db_path: str, identifier: str):
    """Look up a subscriber by ID (if numeric) or name. Exits on not found or ambiguous."""
    if identifier.isdigit():
        sub = get_by_id(db_path, int(identifier))
        if sub is None:
            sys.exit(f"No subscriber found with ID: {identifier}")
        return sub
    matches = get_by_name(db_path, identifier)
    if not matches:
        sys.exit(f"No subscriber found with name: {identifier!r}")
    if len(matches) > 1:
        ids = ", ".join(f"{s.name} (ID {s.id})" for s in matches)
        sys.exit(f"Multiple subscribers named {identifier!r}: {ids} — use ID instead")
    return matches[0]


def _parse_time(value: str) -> str:
    """Accept HH:MM or H:MM AM/PM — normalize to zero-padded HH:MM 24-hour."""
    value = value.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(AM|PM)", value, re.IGNORECASE)
    if m:
        h, minute, period = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        h = (0 if h == 12 else h) if period == "AM" else (12 if h == 12 else h + 12)
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return f"{h:02d}:{minute:02d}"
        sys.exit(f"Invalid time: {value!r}")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if m:
        h, minute = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return f"{h:02d}:{minute:02d}"
    sys.exit(f"Invalid time format: {value!r}. Use HH:MM or 'H:MM AM/PM'.")


def _display_time(send_time: str) -> str:
    h, m = send_time.split(":")
    return datetime(2000, 1, 1, int(h), int(m)).strftime("%-I:%M %p")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Born to be Wild — Subscriber Management",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    sub.add_parser("list", help="Show all subscribers and their settings")

    p_add = sub.add_parser("add", help='Add a subscriber (e.g. add "Dan" dan@example.com "6:15 AM")')
    p_add.add_argument("name")
    p_add.add_argument("email")
    p_add.add_argument("time", help='Send time, e.g. "06:15" or "6:15 AM"')
    p_add.add_argument("--phone", help="Phone number for SMS, e.g. 208-555-1234")
    p_add.add_argument("--message-email", action=argparse.BooleanOptionalAction, default=True,
                       dest="message_email", help="Send via email (default: on)")
    p_add.add_argument("--message-phone", action=argparse.BooleanOptionalAction, default=False,
                       dest="message_phone", help="Send via SMS (default: off)")

    p_remove = sub.add_parser("remove", help="Remove a subscriber by name or ID")
    p_remove.add_argument("identifier", help="Subscriber name or ID")

    p_update = sub.add_parser("update", help="Update subscriber settings")
    p_update.add_argument("identifier", help="Subscriber name or ID")
    p_update.add_argument("--name", help="New display name")
    p_update.add_argument("--time", dest="time", help='New send time, e.g. "7:00 AM"')
    p_update.add_argument("--phone", help="Phone number for SMS, e.g. 208-555-1234")
    p_update.add_argument("--message-email", action=argparse.BooleanOptionalAction, default=None,
                          dest="message_email", help="Send via email (--message-email / --no-message-email)")
    p_update.add_argument("--message-phone", action=argparse.BooleanOptionalAction, default=None,
                          dest="message_phone", help="Send via SMS (--message-phone / --no-message-phone)")
    p_update.add_argument("--active", help="Enable or disable (true/false)")

    sub.add_parser("stats", help="Show forecast accuracy ratings")

    p_hist = sub.add_parser("history", help="Show recent send history for a subscriber")
    p_hist.add_argument("identifier", help="Subscriber name or ID")
    p_hist.add_argument("--days", type=int, default=7, help="Number of days to look back (default 7)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    db_path = config.DB_PATH
    init_db(db_path)

    if args.command == "list":
        cmd_list(db_path)
    elif args.command == "add":
        cmd_add(db_path, args.name, args.email, args.time, args.phone,
                args.message_email, args.message_phone)
    elif args.command == "remove":
        cmd_remove(db_path, args.identifier)
    elif args.command == "update":
        cmd_update(db_path, args.identifier, args.name, args.time, args.active,
                   args.phone, args.message_email, args.message_phone)
    elif args.command == "stats":
        cmd_stats(db_path)
    elif args.command == "history":
        cmd_history(db_path, args.identifier, args.days)


if __name__ == "__main__":
    main()
