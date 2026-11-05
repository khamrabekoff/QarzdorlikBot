"""Operations: backups and admin alerts.

- The database lives on one server, so a copy goes to the admin's Telegram daily.
- Errors go to the admin as (throttled) messages, not only to a log.
"""
import logging
import os
import tempfile
import time

from aiogram.types import FSInputFile

import database as db
from config import ADMIN_ID
from utils import now_local

logger = logging.getLogger(__name__)

async def alert_admin(bot, text: str, key: str, every_seconds: int = 3600) -> bool:
    """Message the admin, at most once per `every_seconds` for the same key -
    a flapping proxy must not turn into a hundred notifications."""
    if not ADMIN_ID:
        return False
    meta_key = f"alert:{key}"
    try:
        last = float(await db.meta_get(meta_key, "0") or 0)
    except ValueError:
        last = 0.0
    if time.time() - last < every_seconds:
        return False
    await db.meta_set(meta_key, time.time())
    try:
        await bot.send_message(ADMIN_ID, text)
        return True
    except Exception as e:  # noqa: BLE001 - alerting must never raise
        logger.warning("Admin alert failed: %s", e)
        return False


async def send_backup(bot, silent: bool = False) -> float:
    """Send the database to the admin as a document. Returns its size in KB."""
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID not set")
    stamp = now_local().strftime("%Y%m%d_%H%M")
    tmp = os.path.join(tempfile.gettempdir(), f"qarzdorlik_{stamp}.db")
    try:
        await db.backup_to(tmp)
        stats = await db.get_stats()
        size_kb = os.path.getsize(tmp) / 1024
        caption = (
            f"💾 <b>Zaxira nusxa</b> · {now_local().strftime('%d.%m.%Y')}\n"
            f"<code>Foydalanuvchilar: {stats.get('users', '?')}</code>\n"
            f"<code>Faol qarzlar:     {stats.get('debts_active', '?')}</code>\n"
            f"<code>Hajmi:            {size_kb:.0f} KB</code>"
        )
        await bot.send_document(ADMIN_ID, FSInputFile(tmp), caption=caption,
                                disable_notification=silent)
        return size_kb
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def daily_backup(bot):
    """Once per calendar day, from whichever trigger runs first. Marked done
    only once it's actually delivered, so a proxy hiccup means "try again on
    the next trigger", not "skip today" (which is what happened 2026-10-13)."""
    today = now_local().date().isoformat()
    if await db.meta_get("last_backup_date") == today:
        return
    try:
        await send_backup(bot, silent=True)
        await db.meta_set("last_backup_date", today)
    except Exception as e:  # noqa: BLE001
        logger.warning("Daily backup failed (will retry): %s", e)
