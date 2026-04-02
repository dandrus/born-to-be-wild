"""IMAP inbox polling — checks Gmail every 5 minutes for subscriber reply commands."""
from __future__ import annotations
import email as email_lib
import imaplib
import logging
from email.utils import parseaddr
from typing import Any

from . import config
from .subscribers import get_by_email
from .commands import handle_command

log = logging.getLogger(__name__)


def poll_inbox(db_path: str, scheduler: Any) -> None:
    """Check Gmail inbox for unread subscriber replies and dispatch commands."""
    log.info("Polling inbox for replies")
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
            imap.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            imap.select("INBOX")
            _, data = imap.search(None, "UNSEEN")
            msg_ids = data[0].split()
            if not msg_ids:
                log.debug("No unread messages")
                return
            log.info(f"Found {len(msg_ids)} unread message(s)")
            for msg_id in msg_ids:
                _process_message(imap, msg_id, db_path, scheduler)
    except Exception:
        log.error("IMAP polling failed", exc_info=True)


def _process_message(
    imap: imaplib.IMAP4_SSL,
    msg_id: bytes,
    db_path: str,
    scheduler: Any,
) -> None:
    _, msg_data = imap.fetch(msg_id, "(RFC822)")
    raw = msg_data[0][1]
    msg = email_lib.message_from_bytes(raw)

    _, sender_email = parseaddr(msg.get("From", ""))
    sender_email = sender_email.lower().strip()

    sub = get_by_email(db_path, sender_email)
    if sub is None:
        log.debug(f"Ignoring message from non-subscriber: {sender_email}")
        return

    body = _get_text_body(msg)
    command_text = _extract_command_line(body)
    log.info(f"Command from {sender_email}: {command_text!r}")

    handle_command(command_text, sub, db_path, scheduler)
    imap.store(msg_id, "+FLAGS", "\\Seen")


def _get_text_body(msg: email_lib.message.Message) -> str:
    """Extract the plain-text body from a (possibly multipart) email."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _extract_command_line(body: str) -> str:
    """Return the first non-empty, non-quoted line from the reply body."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(">"):
            return stripped
    return ""
