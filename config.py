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
