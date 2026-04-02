from __future__ import annotations
import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS: str = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD: str = os.environ["GMAIL_APP_PASSWORD"]
ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", GMAIL_ADDRESS)
DB_PATH: str = os.getenv("DB_PATH", "./born-to-be-wild.sqlite")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

TEXTBELT_API_KEY: str = os.getenv("TEXTBELT_API_KEY", "")

LAT: float = 43.6121
LON: float = -116.3915
TIMEZONE = ZoneInfo("America/Boise")
FORECAST_HOURS: int = 12
API_TIMEOUT: int = 5
