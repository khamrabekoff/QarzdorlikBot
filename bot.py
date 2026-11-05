"""Entry point of the debts bot: long polling, run as a systemd service.

    python bot.py

Conversation state lives in SQLite (fsm_storage.py), so a restart - the server
reboots nightly - does not lose anyone's half-filled form. Reminders, the
daily backup and state cleanup run from a background loop in the same
process; no external cron is needed.
"""
import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import (
    TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
)
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent

try:  # aiogram >= 3.7
    from aiogram.client.default import DefaultBotProperties
except ImportError:  # older 3.x took parse_mode directly
    DefaultBotProperties = None

import database as db
import ops
from config import ADMIN_ID, BOT_TOKEN, BOT_VERSION, DB_PATH
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
) 


class ResilientRequests(BaseRequestMiddleware):
    """Retry a Telegram call that failed on the way there, and don't let an
    unanswerable button press abort the rest of a handler."""

    # Pauses between attempts; Telegram hiccups can last several seconds.
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


if DefaultBotProperties is not None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
else:
    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
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
        # while the network is down and is stale noise once it's back.
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


# ---------- background work ----------

SWEEP_EVERY_SECONDS = 5 * 60


async def housekeeping():
    await purge_expired_states(DB_PATH)
    await ops.daily_backup(bot)
    try:
        await ensure_commands()
    except Exception as e:  # noqa: BLE001
        logger.info("set_my_commands skipped: %s", e)


async def background_loop():
    """Reminder sweep + daily chores, forever. A failure only skips one round."""
    while True:
        try:
            sent = await sweep_reminders(bot)
            if sent:
                logger.info("Reminder sweep sent %d", sent)
            await housekeeping()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Background round failed: %s: %s", type(e).__name__, e)
        await asyncio.sleep(SWEEP_EVERY_SECONDS)


async def main():
    await db.init_db()
    # Polling and a webhook are mutually exclusive; keep queued updates.
    await bot.delete_webhook(drop_pending_updates=False)
    me = await bot.get_me()
    logger.info("Starting @%s (v%s) in polling mode", me.username, BOT_VERSION)
    task = asyncio.create_task(background_loop())
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
