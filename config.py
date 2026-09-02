"""Deployment configuration, read from the environment (.env on the server)."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN", "khamrabekoff.pythonanywhere.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", f"https://{WEBHOOK_DOMAIN}{WEBHOOK_PATH}")

# Secrets guarding the operator endpoints (deploy / status / logs / cron).
DEPLOY_SECRET = os.getenv("DEPLOY_SECRET", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qarzdorlik.db"))

# PythonAnywhere's free tier only reaches the internet through this proxy.
ON_PYTHONANYWHERE = bool(os.getenv("PYTHONANYWHERE_DOMAIN"))
PROXY_URL = "http://proxy.server:3128" if ON_PYTHONANYWHERE else None

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

BOT_VERSION = "2.0"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set (.env)")
