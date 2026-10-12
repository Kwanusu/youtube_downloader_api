"""Central configuration, loaded once from environment / .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name, default=""):
    return os.getenv(name, default).strip()


# Zoho (use the ADMIN account so recordings of every mentor are visible)
ZOHO_DOMAIN = _env("ZOHO_DOMAIN", "zoho.com")
ZOHO_ZSOID = _env("ZOHO_ZSOID")
ZOHO_CLIENT_ID = _env("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = _env("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = _env("ZOHO_REFRESH_TOKEN")
# Override if your data centre / org uses a different API root.
ZOHO_API_BASE = _env("ZOHO_API_BASE", f"https://meeting.{ZOHO_DOMAIN}/api/v2")

# YouTube
GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_PROJECT_ID = _env("GOOGLE_PROJECT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")
YOUTUBE_TOKEN_FILE = Path(_env("YOUTUBE_TOKEN_FILE", str(BASE_DIR / "token.json")))
YOUTUBE_PRIVACY = _env("YOUTUBE_PRIVACY", "unlisted")
YOUTUBE_CATEGORY_ID = _env("YOUTUBE_CATEGORY_ID", "27")  # Education

# Zindua LMS (Rukia API). Calls are made as a Zitadel machine user that is an
# admin member of the organisation, so every cohort's schedule is visible.
LMS_API_BASE_URL = _env("LMS_API_BASE_URL", "https://api.rukialabs.com").rstrip("/")
LMS_ORG_ID = _env("LMS_ORG_ID")  # organisations.id UUID, sent as X-Org-Id
LMS_TOKEN_URL = _env("LMS_TOKEN_URL", "https://auth.rukialabs.com/oauth/v2/token")
LMS_CLIENT_ID = _env("LMS_CLIENT_ID")
LMS_CLIENT_SECRET = _env("LMS_CLIENT_SECRET")
# Must include the project audience, e.g. "openid urn:zitadel:iam:org:project:id:<id>:aud"
LMS_SCOPE = _env("LMS_SCOPE", "openid")
# Alternative to client credentials: a long-lived personal access token.
LMS_ACCESS_TOKEN = _env("LMS_ACCESS_TOKEN")

# Class schedule (all times are local to TIMEZONE)
TIMEZONE = _env("TIMEZONE", "Africa/Nairobi")
FULL_TIME_HOURS = _env("FULL_TIME_HOURS", "09:00-14:00")   # weekdays
PART_TIME_HOURS = _env("PART_TIME_HOURS", "19:00-22:00")   # covers 7-9pm and 8-10pm cohorts
# How far a recording's start may drift from its LMS session and still match.
MATCH_TOLERANCE_MINUTES = int(_env("MATCH_TOLERANCE_MINUTES", "45"))
# Past sessions fetched per cohort when matching recordings.
LMS_SESSION_LOOKBACK = int(_env("LMS_SESSION_LOOKBACK", "200"))

# Storage
DATA_DIR = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "uploader.db"
DOWNLOAD_DIR = DATA_DIR / "downloads"

# Service
POLL_INTERVAL_SECONDS = int(_env("POLL_INTERVAL_SECONDS", "900"))
MAX_ATTEMPTS = int(_env("MAX_ATTEMPTS", "5"))
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
DASHBOARD_USER = _env("DASHBOARD_USER", "mentor")
DASHBOARD_PASSWORD = _env("DASHBOARD_PASSWORD")
ADMIN_USER = _env("ADMIN_USER", "admin")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD")
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8080"))

# Failure alerts (optional)
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
ALERT_EMAIL_TO = _env("ALERT_EMAIL_TO")

GOOGLE_CLIENT_CONFIG = {
    "installed": {
        "client_id": GOOGLE_CLIENT_ID,
        "project_id": GOOGLE_PROJECT_ID,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uris": ["http://localhost"],
    }
}
