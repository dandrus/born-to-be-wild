from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Subscriber:
    id: int
    name: str
    email: str
    send_time: str          # "HH:MM" 24-hour
    active: bool
    snooze_until: str | None  # "YYYY-MM-DD" or None
    created_at: str           # ISO timestamp
    phone: str | None = None  # E.164 format, e.g. +12085551234
    message_email: bool = True
    message_phone: bool = False


def init_db(db_path: str) -> None:
    """Create all tables if they do not exist. Safe to call on every startup."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                email        TEXT    NOT NULL UNIQUE,
                send_time    TEXT    NOT NULL,
                active       INTEGER NOT NULL DEFAULT 1,
                snooze_until TEXT,
                created_at   TEXT    NOT NULL
            )
        """)
        # Migrate existing DBs — safe to run on every startup
        for col, definition in [
            ("phone", "TEXT"),
            ("notify_via", "TEXT NOT NULL DEFAULT 'email'"),
            ("message_email", "INTEGER NOT NULL DEFAULT 1"),
            ("message_phone", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE subscribers ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migrate notify_via → message_email + message_phone (idempotent)
        try:
            conn.execute("""
                UPDATE subscribers
                SET message_email = CASE notify_via WHEN 'sms' THEN 0 ELSE 1 END,
                    message_phone = CASE notify_via WHEN 'sms' THEN 1 WHEN 'both' THEN 1 ELSE 0 END
                WHERE notify_via IS NOT NULL
            """)
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id INTEGER NOT NULL,
                sent_at       TEXT    NOT NULL,
                status        TEXT    NOT NULL,
                accuracy      TEXT,
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
            )
        """)


def add_subscriber(
    db_path: str,
    name: str,
    email: str,
    send_time: str,
    phone: str | None = None,
    message_email: bool = True,
    message_phone: bool = False,
) -> Subscriber:
    """Insert a new subscriber. Raises ValueError on duplicate email."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO subscribers "
                "(name, email, send_time, active, phone, message_email, message_phone, created_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
                (name, email, send_time, phone, int(message_email), int(message_phone), created_at),
            )
            subscriber_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"A subscriber with email {email!r} already exists")
    return Subscriber(
        id=subscriber_id,
        name=name,
        email=email,
        send_time=send_time,
        active=True,
        snooze_until=None,
        created_at=created_at,
        phone=phone,
        message_email=message_email,
        message_phone=message_phone,
    )


def list_subscribers(db_path: str, active_only: bool = False) -> list[Subscriber]:
    """Return all subscribers, or only active ones when active_only=True."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if active_only:
            rows = conn.execute(
                "SELECT * FROM subscribers WHERE active = 1 ORDER BY send_time"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM subscribers ORDER BY send_time"
            ).fetchall()
    return [_row_to_subscriber(r) for r in rows]


def get_by_email(db_path: str, email: str) -> Subscriber | None:
    """Return the subscriber with the given email, or None."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM subscribers WHERE email = ?", (email,)
        ).fetchone()
    return _row_to_subscriber(row) if row else None


def get_by_id(db_path: str, subscriber_id: int) -> Subscriber | None:
    """Return the subscriber with the given id, or None."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)
        ).fetchone()
    return _row_to_subscriber(row) if row else None


def get_by_name(db_path: str, name: str) -> list[Subscriber]:
    """Return subscribers whose name matches exactly (case-insensitive)."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM subscribers WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchall()
    return [_row_to_subscriber(r) for r in rows]


def update_subscriber(db_path: str, subscriber_id: int, **fields) -> None:
    """Update name, email, send_time, phone, message_email, or message_phone by keyword argument."""
    allowed = {"name", "email", "send_time", "phone", "message_email", "message_phone"}
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"Invalid field(s) for update: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [subscriber_id]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE subscribers SET {set_clause} WHERE id = ?", values
        )


def set_active(db_path: str, subscriber_id: int, active: bool) -> None:
    """Enable or disable a subscriber."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE subscribers SET active = ? WHERE id = ?",
            (1 if active else 0, subscriber_id),
        )


def set_snooze(db_path: str, subscriber_id: int, snooze_until: str | None) -> None:
    """Set or clear the snooze_until date. Pass None to clear (RESUME)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE subscribers SET snooze_until = ? WHERE id = ?",
            (snooze_until, subscriber_id),
        )


def delete_subscriber(db_path: str, subscriber_id: int) -> None:
    """Hard-delete a subscriber row."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM subscribers WHERE id = ?", (subscriber_id,))


def log_email_sent(db_path: str, subscriber_id: int, status: str) -> int:
    """Log a sent ride report. Returns the new log entry id."""
    sent_at = datetime.now(tz=timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO email_log (subscriber_id, sent_at, status) VALUES (?, ?, ?)",
            (subscriber_id, sent_at, status),
        )
        return cursor.lastrowid


def log_accuracy(db_path: str, subscriber_id: int, accurate: bool) -> bool:
    """Rate the most recent unrated log entry for a subscriber. Returns True if found."""
    rating = "ACCURATE" if accurate else "WRONG"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """UPDATE email_log SET accuracy = ?
               WHERE id = (
                   SELECT id FROM email_log
                   WHERE subscriber_id = ? AND accuracy IS NULL
                   ORDER BY sent_at DESC LIMIT 1
               )""",
            (rating, subscriber_id),
        )
        return cursor.rowcount > 0


def get_email_history(db_path: str, subscriber_id: int, days: int = 7) -> list[dict]:
    """Return recent email log entries for a subscriber, newest first."""
    from datetime import timedelta, timezone as tz
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT sent_at, status, accuracy FROM email_log
               WHERE subscriber_id = ? AND sent_at >= ?
               ORDER BY sent_at DESC""",
            (subscriber_id, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def get_accuracy_stats(db_path: str) -> list[dict]:
    """Return forecast accuracy counts per subscriber."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT s.name, s.email,
                      SUM(CASE WHEN l.accuracy = 'ACCURATE' THEN 1 ELSE 0 END) AS accurate,
                      SUM(CASE WHEN l.accuracy = 'WRONG'    THEN 1 ELSE 0 END) AS wrong,
                      COUNT(l.id) AS total_sent
               FROM subscribers s
               LEFT JOIN email_log l ON l.subscriber_id = s.id
               GROUP BY s.id
               ORDER BY s.send_time""",
        ).fetchall()
    return [dict(r) for r in rows]


def _row_to_subscriber(row: sqlite3.Row) -> Subscriber:
    return Subscriber(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        send_time=row["send_time"],
        active=bool(row["active"]),
        snooze_until=row["snooze_until"],
        created_at=row["created_at"],
        phone=row["phone"],
        message_email=bool(row["message_email"]),
        message_phone=bool(row["message_phone"]),
    )
