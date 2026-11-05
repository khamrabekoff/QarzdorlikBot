"""Deployment configuration, read from the environment (.env on the server)."""
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


DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qarzdorlik.db"))

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

BOT_VERSION = "3.0.0"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set (.env)")
