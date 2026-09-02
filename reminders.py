"""Due-date reminders.

Replaces the version in main.py, which had an AI's stream-of-consciousness
left in the source ("Wait, I didn't add...", "Let's fix that by...") wrapped
around logic that pulled every active debt of every user on each sweep.

Timing rules, in one place:
  - a debt due today, created today  -> remind 30 min after it was created
  - a debt due today, created earlier -> remind from REMIND_HOUR onwards
  - an overdue debt                   -> remind from REMIND_HOUR onwards, daily
  - at most one reminder per debt per day
"""
import logging
from datetime import datetime, timedelta

import database as db
from utils import fmt_amount, fmt_date, i18n, now_local

logger = logging.getLogger(__name__)

REMIND_HOUR = 10
CREATED_TODAY_DELAY = timedelta(minutes=30)


def _should_remind(debt, now) -> bool:
    try:
        due = datetime.strptime(debt["due_date"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("Debt %s has an unparseable due_date %r", debt["id"], debt["due_date"])
        return False

    if due > now.date():
        return False  # not due yet; the SQL filter should already exclude these

    if due == now.date():
        created = debt["created_at"]
        try:
            created_dt = datetime.strptime(str(created), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            created_dt = None
        if created_dt and created_dt.date() == now.date():
            return now >= created_dt + CREATED_TODAY_DELAY
        return now.hour >= REMIND_HOUR

    return now.hour >= REMIND_HOUR  # overdue


async def sweep_reminders(bot, dry_run: bool = False):
    """Send every reminder that's due. Returns the count (or the list, if dry)."""
    now = now_local()
    candidates = await db.get_debts_needing_reminder(now.date().isoformat())

    due_now = [d for d in candidates if _should_remind(d, now)]
    if dry_run:
        return [
            {
                "debt_id": d["id"],
                "user_id": d["user_id"],
                "person": d["person_name"],
                "due": d["due_date"],
            }
            for d in due_now
        ]

    lang_cache = {}
    sent = 0
    for debt in due_now:
        user_id = debt["user_id"]
        if user_id not in lang_cache:
            lang_cache[user_id] = await db.get_user_lang(user_id)
        lang = lang_cache[user_id]

        remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
        overdue = debt["due_date"] < now.date().isoformat()
        key = "reminder_overdue" if overdue else "reminder_today"
        direction = i18n.get(
            "dir_lent" if debt["debt_type"] == "lent" else "dir_borrowed", lang
        )

        text = i18n.get(
            key, lang,
            direction=direction,
            name=debt["person_name"],
            amount=fmt_amount(remaining),
            currency=debt["currency"],
            date=fmt_date(debt["due_date"]),
        )

        try:
            await bot.send_message(user_id, text)
            await db.update_last_reminded(debt["id"], now.strftime("%Y-%m-%d %H:%M:%S"))
            sent += 1
        except Exception as e:  # noqa: BLE001 - one bad chat must not stop the sweep
            logger.warning("Reminder to %s failed: %s", user_id, e)

    if sent:
        logger.info("Reminder sweep sent %d messages", sent)
    return sent
