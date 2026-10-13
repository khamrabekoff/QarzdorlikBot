"""Deployment configuration, read from the environment (.env on the server)."""
import hashlib
import os

from dotenv import load_dotenv

# Load .env from THIS file's directory, not the process's working directory.
# Under WSGI the cwd is not the project folder, so a bare load_dotenv()
# finds nothing and every setting silently falls back to its default.
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, '.env'))
load_dotenv()  # also honour a .env found the usual way, if any

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN", "khamrabekoff.pythonanywhere.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", f"https://{WEBHOOK_DOMAIN}{WEBHOOK_PATH}")

# Telegram echoes this in the X-Telegram-Bot-Api-Secret-Token header of every
# webhook call. Without checking it, anyone who guessed /webhook could post a
# forged update "from" any user and, say, delete their debts. Derived from the
# bot token so there is no extra secret to manage.
WEBHOOK_SECRET = hashlib.sha256(f"webhook:{os.getenv('BOT_TOKEN', '')}".encode()).hexdigest()[:48]

# Secrets guarding the operator endpoints (deploy / status / logs / cron).
DEPLOY_SECRET = os.getenv("DEPLOY_SECRET", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qarzdorlik.db"))

# PythonAnywhere's free tier only reaches the internet through this proxy.
ON_PYTHONANYWHERE = bool(os.getenv("PYTHONANYWHERE_DOMAIN"))
PROXY_URL = "http://proxy.server:3128" if ON_PYTHONANYWHERE else None

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

# Optional: lets the bot renew its own scheduled task before the free tier
# expires it, and warn about the web app's expiry. Without it those checks
# are skipped and only the "cron didn't run" alert remains.
PA_API_TOKEN = os.getenv("PA_API_TOKEN", "")
PA_USERNAME = os.getenv("PA_USERNAME", WEBHOOK_DOMAIN.split(".")[0])

BOT_VERSION = "2.3.1"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set (.env)")
