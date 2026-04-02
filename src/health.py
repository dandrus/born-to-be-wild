"""Health check: alert admin if no emails have been sent in 24 hours on a business day."""
from __future__ import annotations
import logging
import sqlite3
from datetime import datetime, timedelta

from . import config
from .holidays import is_skip_day
from .email_sender import send_simple

log = logging.getLogger(__name__)


def run_health_check(db_path: str) -> None:
    """Run once daily. Sends an alert if emails should be going out but aren't."""
    now = datetime.now(tz=config.TIMEZONE)

    if is_skip_day(now.date()):
        log.debug("Health check skipped: weekend or holiday")
        return

    cutoff = (now - timedelta(hours=24)).isoformat()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM email_log WHERE sent_at >= ?", (cutoff,)
        ).fetchone()[0]

    if count == 0:
        msg = "No emails sent in the last 24 hours. Service may be stuck."
        log.warning(f"Health check FAILED: {msg}")
        try:
            send_simple(
                config.ADMIN_EMAIL,
                "⚠ Born to be Wild — Health Check Alert",
                f"Health check alert:\n\n{msg}\n\nCheck the service logs:\n\n"
                "  journalctl --user -u born-to-be-wild.service -n 100",
            )
        except Exception as exc:
            log.error(f"Could not send health alert email: {exc}", exc_info=True)
    else:
        log.info(f"Health check OK: {count} email(s) sent in the last 24 hours")
