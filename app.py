"""Flask entry point for the debts bot (webhook mode).

Things about earlier versions worth knowing, because each made the bot look
broken to users:

1. It started an AsyncIOScheduler bound to `worker_loop` and expected a job
   to fire every 5 minutes. But that loop only runs *inside*
   run_until_complete(), i.e. only while a webhook request is being handled -
   between requests it is idle, so the timer never fired. Scheduling now
   comes from outside: a PythonAnywhere task (or any cron) hits
   /cron/reminders, and ordinary bot traffic also triggers a throttled sweep.

2. Conversation state lived in aiogram's MemoryStorage. PythonAnywhere
   recycles the worker constantly, so anyone midway through adding a debt
   silently lost their progress. State now lives in SQLite (fsm_storage.py).

3. Any exception made the webhook answer 500, and Telegram re-delivers a
   failed update - so one stale button press ("query is too old") was
   replayed 8 times over two minutes on 2026-10-12, re-running the handler
   each time. Errors are now handled inside the dispatcher (logged, admin
   alerted, user told) and the webhook always acknowledges. Transient proxy
   failures (PythonAnywhere's proxy returns 503 now and then) are retried
   per request instead, where a retry can't duplicate a database write.
"""
import asyncio
import hmac
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import (
    TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
)
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent, Update
from flask import Flask, jsonify, request

try:  # aiogram >= 3.7
    from aiogram.client.default import DefaultBotProperties
except ImportError:  # older 3.x took parse_mode directly
    DefaultBotProperties = None

# What the proxied session raises when PythonAnywhere's proxy says 503. Note
# aiohttp_socks re-raises with its OWN ProxyError class, unrelated to
# python_socks.ProxyError - catching only the latter meant v2.1 never
# actually retried a proxy failure (seen in the log: "Daily backup failed:
# 503 Service Unavailable").
_PROXY_ERRORS = ()
try:
    from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
    _PROXY_ERRORS += (ProxyError, ProxyConnectionError, ProxyTimeoutError)
except ImportError:  # pragma: no cover
    pass
try:
    import python_socks
    _PROXY_ERRORS += (python_socks.ProxyError,)
except ImportError:  # pragma: no cover
    pass

import database as db
import ops
from config import (
    ADMIN_ID, BOT_TOKEN, BOT_VERSION, CRON_SECRET, DB_PATH, DEPLOY_SECRET,
    PROXY_URL, WEBHOOK_DOMAIN, WEBHOOK_PATH, WEBHOOK_SECRET, WEBHOOK_URL,
)
from fsm_storage import SQLiteStorage, purge_expired_states
from reminders import sweep_reminders
from utils import esc, i18n

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------- bot ----------

_NETWORK_ERRORS = (
    TelegramNetworkError, TelegramServerError, aiohttp.ClientError,
    asyncio.TimeoutError, ConnectionError,
) + _PROXY_ERRORS


class ResilientRequests(BaseRequestMiddleware):
    """Retry a Telegram call that failed on the way there, and don't let an
    unanswerable button press abort the rest of a handler."""

    # Pauses between attempts. The proxy's 503 bursts were seen lasting
    # 5+ seconds (2026-10-13 12:01-12:02): 0.6+1.2+1.8 s was not enough.
    BACKOFF = (0.5, 1.0, 2.0, 3.0, 4.0)
    ATTEMPTS = len(BACKOFF) + 1

    async def __call__(self, make_request, bot, method):
        for attempt in range(1, self.ATTEMPTS + 1):
            try:
                return await make_request(bot, method)
            except TelegramBadRequest:
                if isinstance(method, AnswerCallbackQuery):
                    # 'query is too old': the spinner can no longer be
                    # cleared, but the card edit that follows still works.
                    return True
                raise
            except TelegramRetryAfter as e:
                if attempt == self.ATTEMPTS or e.retry_after > 5:
                    raise
                await asyncio.sleep(e.retry_after)
            except _NETWORK_ERRORS as e:
                if attempt == self.ATTEMPTS:
                    if isinstance(method, AnswerCallbackQuery):
                        # Only clears the button's spinner - never worth
                        # aborting the screen the user actually asked for.
                        return True
                    raise
                logger.info("Telegram call %s failed (%s), retry %d",
                            type(method).__name__, type(e).__name__, attempt)
                await asyncio.sleep(self.BACKOFF[attempt - 1])


session = None
if PROXY_URL:
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(proxy=PROXY_URL)

if DefaultBotProperties is not None:
    bot = Bot(token=BOT_TOKEN, session=session,
              default=DefaultBotProperties(parse_mode="HTML"))
else:
    bot = Bot(token=BOT_TOKEN, session=session, parse_mode="HTML")
bot.session.middleware(ResilientRequests())

dp = Dispatcher(storage=SQLiteStorage(DB_PATH))


@dp.update.outer_middleware()
async def track_activity(handler, event, data):
    """Stamp last_active (retention) and the current display name (so the
    other side of a shared debt sees a name, not '?')."""
    user = data.get("event_from_user")
    if user:
        try:
            await db.touch_user(user.id, user.username, user.first_name)
        except Exception as e:  # noqa: BLE001 - never block an update over analytics
            logger.debug("touch_user failed: %s", e)
    return await handler(event, data)


@dp.errors()
async def on_error(event: ErrorEvent, bot: Bot):
    exc = event.exception
    network = isinstance(exc, _NETWORK_ERRORS)
    logger.error("Update %s failed: %s: %s", event.update.update_id,
                 type(exc).__name__, exc, exc_info=None if network else exc)
    if not network:
        # Connection outages aren't alerted: the alert itself can't get out
        # while the proxy is down and is stale noise once it's back. Long
        # outages still surface through the "cron didn't run" check.
        await ops.alert_admin(
            bot,
            f"⚠️ <b>Xatolik</b> (v{BOT_VERSION})\n<code>{esc(type(exc).__name__)}: {esc(str(exc))[:400]}</code>",
            key=f"error:{type(exc).__name__}", every_seconds=600,
        )

    # Tell the person something went wrong instead of leaving them waiting.
    upd = event.update
    who = (upd.message and upd.message.from_user) or (upd.callback_query and upd.callback_query.from_user)
    if who:
        try:
            lang = await db.get_user_lang(who.id)
            await bot.send_message(who.id, i18n.get("err_generic", lang))
        except Exception:  # noqa: BLE001 - the failure may be Telegram itself
            pass
    return True


from handlers import admin, debts, fallback, sharing, start  # noqa: E402  (routers need `bot` above)

# sharing first: it claims /start with a deep-link payload, which the plain
# /start handler in `start` would otherwise swallow. fallback last: it takes
# whatever nobody else did.
dp.include_router(sharing.router)
dp.include_router(start.router)
dp.include_router(debts.router)
dp.include_router(admin.router)
dp.include_router(fallback.router)

app = Flask(__name__)
# A Telegram update is a few KB; refuse anything absurd before parsing it.
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024


# ---------- async runtime ----------
# One event loop for the worker's lifetime, driven from the request thread.
# run_until_complete leaves the loop open (unlike asyncio.run), so aiohttp's
# connector stays valid between requests instead of being rebuilt every time.

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_loop_lock = threading.Lock()
_stats = {"updates": 0, "errors": 0, "rejected": 0, "last_ms": None, "reminder_sweeps": 0}


def run_sync(coro, timeout=40):
    with _loop_lock:
        return _loop.run_until_complete(asyncio.wait_for(coro, timeout))


try:
    run_sync(db.init_db(), timeout=60)
except Exception as e:  # noqa: BLE001 - must not stop the app from serving
    logger.error("Database init failed: %s", e, exc_info=True)


def _secret_ok(given, expected) -> bool:
    """Constant-time comparison, and an unset secret never matches."""
    return bool(expected) and hmac.compare_digest(str(given or ""), expected)


# ---------- housekeeping ----------

async def ensure_commands():
    """The '/' menu in Telegram, per language. Re-sent once per version."""
    if await db.meta_get("commands_version") == BOT_VERSION:
        return
    for lang, code in (("uz", None), ("ru", "ru")):
        commands = [
            BotCommand(command=c, description=i18n.get(f"cmd_{c}", lang))
            for c in ("start", "new", "help", "cancel")
        ]
        await bot.set_my_commands(commands, language_code=code)
    if ADMIN_ID:
        # Only the admin's own chat sees these in the "/" menu.
        await bot.set_my_commands(
            [BotCommand(command="start", description="Bosh sahifa"),
             BotCommand(command="users", description="Foydalanuvchilar va xabar yozish"),
             BotCommand(command="broadcast", description="Hammaga xabar"),
             BotCommand(command="stats", description="Statistika"),
             BotCommand(command="backup", description="Bazaning zaxira nusxasi")],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    await db.meta_set("commands_version", BOT_VERSION)


async def _housekeeping(from_cron: bool):
    if from_cron:
        await ops.record_cron_run()
        await purge_expired_states(DB_PATH)
    else:
        await ops.check_cron_alive(bot)
    await ops.daily_backup(bot)  # traffic retries a backup the cron couldn't deliver
    await ops.keep_host_alive(bot)
    try:
        await ensure_commands()
    except Exception as e:  # noqa: BLE001
        logger.info("set_my_commands skipped: %s", e)


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
        run_sync(_housekeeping(from_cron=False), timeout=60)
    except Exception as e:  # noqa: BLE001
        logger.warning("Reminder sweep skipped: %s", e)


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if not _secret_ok(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), WEBHOOK_SECRET):
        _stats["rejected"] += 1
        return "Forbidden", 403

    started = datetime.now()
    try:
        # Deliberately not logging the update body: it carries phone numbers
        # and message text, and used to be written to the error log wholesale.
        update = Update.model_validate_json(request.get_data().decode("utf-8"))
        run_sync(dp.feed_update(bot, update))
        _stats["updates"] += 1
        _stats["last_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    except Exception as e:  # noqa: BLE001
        # Errors inside handlers are caught by on_error; reaching here means
        # an unparseable update or a timeout. Acknowledge anyway: a replay
        # would fail the same way, and could repeat a write that succeeded.
        _stats["errors"] += 1
        logger.error("Webhook error: %s: %s", type(e).__name__, e, exc_info=True)

    _maybe_sweep_reminders()
    return "OK", 200


# ---------- operator endpoints ----------

@app.route("/health", methods=["GET"])
def health():
    import cards
    return jsonify({"status": "ok", "bot": "running", "version": BOT_VERSION,
                    "cards": cards.AVAILABLE}), 200


@app.route("/status/<secret>", methods=["GET"])
def status(secret):
    if not _secret_ok(secret, DEPLOY_SECRET):
        return "Forbidden", 403
    try:
        stats = run_sync(db.get_stats(), timeout=20)
        stats["last_cron_at"] = run_sync(db.meta_get("last_cron_at"), timeout=10)
        stats["last_backup_date"] = run_sync(db.meta_get("last_backup_date"), timeout=10)
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
    if not _secret_ok(secret, DEPLOY_SECRET):
        return "Forbidden", 403
    path = f"/var/log/{WEBHOOK_DOMAIN}.error.log"
    try:
        n = max(1, min(int(request.args.get("n", 60)), 2000))
    except ValueError:
        n = 60
    try:
        with open(path, "r", errors="replace") as fh:
            tail = fh.readlines()[-n:]
        return "".join(tail), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:  # noqa: BLE001
        return f"Could not read {path}: {e}", 500, {"Content-Type": "text/plain"}


@app.route("/deploy/<secret>", methods=["GET", "POST"])
def deploy(secret):
    if not _secret_ok(secret, DEPLOY_SECRET):
        return "Forbidden", 403
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ["git", "pull"], cwd=repo_dir, capture_output=True, text=True, timeout=60
        )
        git_output = (result.stdout + result.stderr).strip()

        # New dependency? Install it into this app's own virtualenv, so a
        # deploy never needs someone at the PythonAnywhere console.
        pip_output = None
        if "requirements.txt" in git_output or request.args.get("pip") == "1":
            cmd = [os.path.join(sys.prefix, "bin", "python"), "-m", "pip", "install", "-q",
                   "-r", os.path.join(repo_dir, "requirements.txt")]
            if PROXY_URL:
                cmd += ["--proxy", PROXY_URL]
            pip = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, timeout=280)
            pip_output = f"exit {pip.returncode}: " + (pip.stdout + pip.stderr).strip()[-800:]

        wsgi_file = f"/var/www/{WEBHOOK_DOMAIN.replace('.', '_')}_wsgi.py"
        try:
            os.utime(wsgi_file, None)  # touching it makes PythonAnywhere reload
            reload_status = "reloaded"
        except Exception as e:  # noqa: BLE001
            reload_status = f"touch failed: {e}"
        logger.info("Deploy: %s | %s", git_output[:300], reload_status)
        return jsonify({"status": "ok", "git": git_output, "pip": pip_output,
                        "reload": reload_status}), 200
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/setup/<secret>", methods=["GET", "POST"])
def setup(secret):
    """Point Telegram at this webhook WITH the secret header, and refresh
    the command menu. Run once after a deploy that changes either."""
    if not _secret_ok(secret, DEPLOY_SECRET):
        return "Forbidden", 403
    try:
        run_sync(bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET,
                                 allowed_updates=["message", "callback_query"]), timeout=30)
        run_sync(db.meta_set("commands_version", ""), timeout=10)
        run_sync(ensure_commands(), timeout=30)
        info = run_sync(bot.get_webhook_info(), timeout=30)
        return jsonify({"status": "ok", "webhook": info.url,
                        "pending": info.pending_update_count,
                        "last_error": info.last_error_message}), 200
    except Exception as e:  # noqa: BLE001
        logger.error("Setup failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/cron/reminders/<secret>", methods=["GET", "POST"])
def cron_reminders(secret):
    """Scheduled sweep, plus the daily chores. ?dry=1 reports who would be
    notified and does nothing else - always use it when testing, these go to
    real people."""
    if not _secret_ok(secret, CRON_SECRET):
        return "Forbidden", 403
    dry = request.args.get("dry") in ("1", "true", "yes")
    try:
        result = run_sync(sweep_reminders(bot, dry_run=dry), timeout=120)
        if not dry:
            run_sync(_housekeeping(from_cron=True), timeout=120)
        key = "would_notify" if dry else "sent"
        return jsonify({"status": "ok", "dry_run": dry, key: result}), 200
    except Exception as e:  # noqa: BLE001
        logger.error("Cron error: %s: %s", type(e).__name__, e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/backup/<secret>", methods=["GET", "POST"])
def backup(secret):
    """Send the database to the admin as a Telegram document."""
    if not _secret_ok(secret, DEPLOY_SECRET):
        return "Forbidden", 403
    try:
        size_kb = run_sync(ops.send_backup(bot), timeout=90)
        return jsonify({"status": "ok", "size_kb": round(size_kb)}), 200
    except Exception as e:  # noqa: BLE001
        logger.error("Backup failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000)
