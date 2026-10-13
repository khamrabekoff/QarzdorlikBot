"""Operations: backups, admin alerts, and keeping the free-tier host alive.

Everything here exists because of something that actually went wrong:

- The daily reminder task on PythonAnywhere's free tier expires after a
  month unless someone clicks "extend". It silently expired on 2026-09-30 and
  nobody noticed for two weeks. Now (a) a missed cron run alerts the admin,
  and (b) with PA_API_TOKEN set, the task is recreated before it expires.
- The database lived only on the server. Now a copy goes to the admin's
  Telegram every day.
- Errors went only to a log nobody reads. Now the admin gets a (throttled)
  message.
"""
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta

import aiohttp
from aiogram.types import FSInputFile

import database as db
from config import ADMIN_ID, PA_API_TOKEN, PA_USERNAME, PROXY_URL, WEBHOOK_DOMAIN
from utils import esc, now_local

logger = logging.getLogger(__name__)

CRON_MISSED_AFTER = timedelta(hours=30)
RENEW_TASK_DAYS_BEFORE = 7
WARN_WEBAPP_DAYS_BEFORE = 10


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
    """Once per calendar day, from whichever trigger runs first."""
    today = now_local().date().isoformat()
    if await db.meta_get("last_backup_date") == today:
        return
    await db.meta_set("last_backup_date", today)
    try:
        await send_backup(bot, silent=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("Daily backup failed: %s", e)


async def record_cron_run():
    await db.meta_set("last_cron_at", now_local().isoformat(timespec="seconds"))


async def check_cron_alive(bot):
    """Called on ordinary traffic. If the scheduled task hasn't hit us in
    over a day, it has most likely expired - say so while it's still cheap."""
    last = await db.meta_get("last_cron_at")
    if not last:
        return  # never recorded yet (fresh deploy); nothing to compare
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return
    if now_local() - last_dt > CRON_MISSED_AFTER:
        await alert_admin(
            bot,
            "⚠️ <b>Eslatmalar ishlamayapti</b>\n\n"
            f"Rejalashtirilgan vazifa oxirgi marta {esc(last)} da ishlagan.\n"
            "Ehtimol muddati tugagan: pythonanywhere.com → <b>Tasks</b> → <b>Extend</b>.",
            key="cron_missed", every_seconds=24 * 3600,
        )


# ---------- PythonAnywhere API (optional, needs PA_API_TOKEN) ----------

def _pa_url(path: str) -> str:
    return f"https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/{path.lstrip('/')}"


async def _pa(session, method, path, **kwargs):
    async with session.request(method, _pa_url(path), proxy=PROXY_URL, **kwargs) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"PA API {method} {path}: {resp.status} {await resp.text()}")
        if resp.status == 204:
            return None
        return await resp.json(content_type=None)


def _days_left(expiry) -> int:
    try:
        return (date.fromisoformat(str(expiry)[:10]) - now_local().date()).days
    except ValueError:
        return 999


async def keep_host_alive(bot):
    """Recreate the reminder task before it expires, and warn about the web
    app's own expiry (that one can only be extended by a click on the site).

    Runs at most once a day. Does nothing without PA_API_TOKEN."""
    if not PA_API_TOKEN or not PA_USERNAME:
        return
    today = now_local().date().isoformat()
    if await db.meta_get("last_keepalive_date") == today:
        return
    await db.meta_set("last_keepalive_date", today)

    headers = {"Authorization": f"Token {PA_API_TOKEN}"}
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
            for task in await _pa(s, "GET", "schedule/"):
                if "/cron/reminders/" not in (task.get("command") or ""):
                    continue
                if _days_left(task.get("expiry")) > RENEW_TASK_DAYS_BEFORE:
                    continue
                # The API can't extend a task, but a new task starts with a
                # fresh expiry. Free accounts get one task, so delete first.
                spec = {k: task[k] for k in ("command", "enabled", "interval", "hour", "minute")
                        if task.get(k) is not None}
                spec["enabled"] = True
                spec["description"] = task.get("description") or "Qarzdorlik: reminders + daily backup"
                await _pa(s, "DELETE", f"schedule/{task['id']}/")
                new = await _pa(s, "POST", "schedule/", json=spec)
                logger.info("Renewed scheduled task %s -> %s (expires %s)",
                            task["id"], new.get("id"), new.get("expiry"))
                await alert_admin(bot, f"🔄 Eslatma vazifasi yangilandi. Yangi muddat: {new.get('expiry')}",
                                  key="task_renewed", every_seconds=3600)

            webapp = await _pa(s, "GET", f"webapps/{WEBHOOK_DOMAIN}/")
            left = _days_left(webapp.get("expiry"))
            if left <= WARN_WEBAPP_DAYS_BEFORE:
                await alert_admin(
                    bot,
                    f"⏳ <b>Bot {left} kundan keyin to'xtaydi</b> ({webapp.get('expiry')}).\n\n"
                    "pythonanywhere.com → <b>Web</b> → <b>Run until 3 months from today</b> "
                    "tugmasini bosing.",
                    key="webapp_expiry", every_seconds=24 * 3600,
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("keep_host_alive failed: %s", e)
        await alert_admin(bot, f"⚠️ PythonAnywhere tekshiruvi ishlamadi: {esc(str(e))[:300]}",
                          key="keepalive_failed", every_seconds=24 * 3600)
