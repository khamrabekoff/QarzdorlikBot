"""Polling runner for a plain VPS (Oracle Cloud), used instead of the
PythonAnywhere webhook. Reuses everything app.py sets up (bot, dispatcher,
database init) and replaces the cron with a loop inside the process."""
import asyncio
import logging

import app as webapp
from app import _housekeeping, bot, dp
from reminders import sweep_reminders

logger = logging.getLogger("bot")
TICK_SECONDS = 5 * 60


async def scheduler():
    while True:
        try:
            sent = await asyncio.wait_for(sweep_reminders(bot), 120)
            if sent:
                logger.info("Reminder sweep sent %d", sent)
            await asyncio.wait_for(_housekeeping(from_cron=True), 120)
        except Exception as e:  # noqa: BLE001
            logger.warning("Scheduled tick failed: %s", e, exc_info=True)
        await asyncio.sleep(TICK_SECONDS)


async def main():
    await bot.delete_webhook(drop_pending_updates=False)
    task = asyncio.create_task(scheduler())
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        task.cancel()


if __name__ == "__main__":
    # Same loop app.py used for init_db, so aiosqlite/aiohttp objects stay valid.
    webapp._loop.run_until_complete(main())
