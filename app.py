"""Flask entry point for the debts bot (webhook mode).

Two things about the previous version are worth knowing, because both made
the bot look broken to users:

1. It started an AsyncIOScheduler bound to `worker_loop` and expected a job
   to fire every 5 minutes. But that loop only runs *inside*
   run_until_complete(), i.e. only while a webhook request is being handled -
   between requests it is idle, so the timer never fired. Reminders, the whole
   point of a debt bot, effectively did not work. Scheduling now comes from
   outside: a PythonAnywhere task (or any cron) hits /cron/reminders, and
   ordinary bot traffic also triggers a throttled sweep as a fallback.

2. Conversation state lived in aiogram's MemoryStorage. PythonAnywhere
   recycles the worker constantly, so anyone midway through adding a debt
   silently lost their progress. State now lives in SQLite (fsm_storage.py).
"""
import asyncio
import logging
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update
from flask import Flask, jsonify, request

import database as db
from config import (
    BOT_TOKEN, BOT_VERSION, CRON_SECRET, DB_PATH, DEPLOY_SECRET,
    PROXY_URL, WEBHOOK_DOMAIN, WEBHOOK_PATH,
)
from fsm_storage import SQLiteStorage, purge_expired_states
from reminders import sweep_reminders

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- bot ----------

session = None
if PROXY_URL:
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(proxy=PROXY_URL)

bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher(storage=SQLiteStorage(DB_PATH))

from handlers import admin, debts, start  # noqa: E402  (routers need `bot` above)

dp.include_router(start.router)
dp.include_router(debts.router)
dp.include_router(admin.router)

app = Flask(__name__)

# ---------- async runtime ----------
# One event loop for the worker's lifetime, driven from the request thread.
# run_until_complete leaves the loop open (unlike asyncio.run), so aiohttp's
# connector stays valid between requests instead of being rebuilt every time.

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_loop_lock = threading.Lock()
_stats = {"updates": 0, "errors": 0, "last_ms": None, "reminder_sweeps": 0}


def run_sync(coro, timeout=40):
    with _loop_lock:
        return _loop.run_until_complete(asyncio.wait_for(coro, timeout))


try:
    run_sync(db.init_db(), timeout=60)
except Exception as e:  # noqa: BLE001 - must not stop the app from serving
    logger.error("Database init failed: %s", e, exc_info=True)


# ---------- webhook ----------

# Fallback scheduling: any bot traffic may trigger a reminder sweep, but at
# most once per interval so a busy minute doesn't cause a stampede.
_REMINDER_MIN_INTERVAL = 15 * 60
_last_sweep = 0.0


def _maybe_sweep_reminders():
    global _last_sweep
    if time.time() - _last_sweep < _REMINDER_MIN_INTERVAL:
        return
    _last_sweep = time.time()
    try:
        sent = run_sync(sweep_reminders(bot), timeout=90)
        _stats["reminder_sweeps"] += 1
        if sent:
            logger.info("Traffic-triggered reminder sweep sent %d", sent)
    except Exception as e:  # noqa: BLE001
        logger.warning("Reminder sweep skipped: %s", e)


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    started = datetime.now()
    try:
        # Deliberately not logging the update body: it carries phone numbers
        # and message text, and used to be written to the error log wholesale.
        update = Update.model_validate_json(request.get_data().decode("utf-8"))
        run_sync(dp.feed_update(bot, update))
        _stats["updates"] += 1
        _stats["last_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    except Exception as e:  # noqa: BLE001
        _stats["errors"] += 1
        logger.error("Webhook error: %s: %s", type(e).__name__, e, exc_info=True)
        return "Error", 500

    _maybe_sweep_reminders()
    return "OK", 200


# ---------- operator endpoints ----------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "bot": "running"}), 200


@app.route("/status/<secret>", methods=["GET"])
def status(secret):
    if not DEPLOY_SECRET or secret != DEPLOY_SECRET:
        return "Forbidden", 403
    try:
        stats = run_sync(db.get_stats(), timeout=20)
    except Exception as e:  # noqa: BLE001
        stats = {"error": str(e)}
    return jsonify({
        "status": "ok",
        "version": BOT_VERSION,
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "runtime": _stats,
        "db": stats,
    }), 200


@app.route("/logs/<secret>", methods=["GET"])
def logs(secret):
    if not DEPLOY_SECRET or secret != DEPLOY_SECRET:
        return "Forbidden", 403
    path = f"/var/log/{WEBHOOK_DOMAIN}.error.log"
    try:
        with open(path, "r", errors="replace") as fh:
            tail = fh.readlines()[-int(request.args.get("n", 60)):]
        return "".join(tail), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:  # noqa: BLE001
        return f"Could not read {path}: {e}", 500, {"Content-Type": "text/plain"}


@app.route("/deploy/<secret>", methods=["GET", "POST"])
def deploy(secret):
    if not DEPLOY_SECRET or secret != DEPLOY_SECRET:
        return "Forbidden", 403
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ["git", "pull"], cwd=repo_dir, capture_output=True, text=True, timeout=60
        )
        git_output = (result.stdout + result.stderr).strip()
        wsgi_file = f"/var/www/{WEBHOOK_DOMAIN.replace('.', '_')}_wsgi.py"
        try:
            os.utime(wsgi_file, None)  # touching it makes PythonAnywhere reload
            reload_status = "reloaded"
        except Exception as e:  # noqa: BLE001
            reload_status = f"touch failed: {e}"
        logger.info("Deploy: %s | %s", git_output[:300], reload_status)
        return jsonify({"status": "ok", "git": git_output, "reload": reload_status}), 200
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/cron/reminders/<secret>", methods=["GET", "POST"])
def cron_reminders(secret):
    """Scheduled sweep. ?dry=1 reports who would be notified, sending nothing -
    always use it when testing, these go to real people."""
    if not CRON_SECRET or secret != CRON_SECRET:
        return "Forbidden", 403
    dry = request.args.get("dry") in ("1", "true", "yes")
    try:
        result = run_sync(sweep_reminders(bot, dry_run=dry), timeout=120)
        run_sync(purge_expired_states(DB_PATH), timeout=30)
        key = "would_notify" if dry else "sent"
        return jsonify({"status": "ok", "dry_run": dry, key: result}), 200
    except Exception as e:  # noqa: BLE001
        logger.error("Cron error: %s: %s", type(e).__name__, e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/backup/<secret>", methods=["GET", "POST"])
def backup(secret):
    """Send the database to the admin as a Telegram document - the only
    off-site copy this free-tier host has."""
    if not DEPLOY_SECRET or secret != DEPLOY_SECRET:
        return "Forbidden", 403
    from config import ADMIN_ID
    if not ADMIN_ID:
        return jsonify({"status": "error", "message": "ADMIN_ID not set"}), 400

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    tmp = os.path.join(tempfile.gettempdir(), f"qarzdorlik_{stamp}.db")
    try:
        run_sync(db.backup_to(tmp), timeout=60)
        stats = run_sync(db.get_stats(), timeout=20)
        size_kb = os.path.getsize(tmp) / 1024

        from aiogram.types import FSInputFile
        caption = (
            f"💾 <b>Zaxira nusxa</b>\n"
            f"<code>Foydalanuvchilar: {stats.get('users', '?')}</code>\n"
            f"<code>Faol qarzlar:     {stats.get('debts_active', '?')}</code>\n"
            f"<code>Hajmi:            {size_kb:.0f} KB</code>"
        )
        run_sync(bot.send_document(ADMIN_ID, FSInputFile(tmp), caption=caption), timeout=90)
        return jsonify({"status": "ok", "size_kb": round(size_kb)}), 200
    except Exception as e:  # noqa: BLE001
        logger.error("Backup failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    app.run(port=5000)
