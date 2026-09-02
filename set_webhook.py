#!/usr/bin/env python3
"""Point Telegram at this deployment's webhook. Run once after deploying."""
import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from config import BOT_TOKEN, PROXY_URL, WEBHOOK_URL

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


async def main():
    session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None
    bot = Bot(token=BOT_TOKEN, session=session)
    try:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        info = await bot.get_webhook_info()
        logging.info("Webhook set to %s", info.url)
        logging.info("Pending: %s | Last error: %s",
                     info.pending_update_count, info.last_error_message or "none")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
