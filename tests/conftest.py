"""Set required environment variables before any src imports."""
import os

os.environ.setdefault("GMAIL_ADDRESS", "test@example.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "test-password")
