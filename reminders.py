"""Due-date reminders.

Timing rules, all in one place:

  - PRE_REMIND_DAYS before the deadline -> an early warning, because finding
    out on the day itself is too late to actually do anything about it
  - on the deadline                     -> from REMIND_HOUR onwards
  - created today and due today         -> 30 minutes after it was created
  - overdue                             -> on days 1, 3, 7, 14, 30, then monthly

That last rule matters more than it looks. The previous version reminded
about an overdue debt *every single day, forever*, which is exactly how a bot
gets muted - and a muted bot is a lost user. Backing off keeps the reminder
meaningful.

Debts with no due date are never reminded about; they are excluded in SQL.
"""
import logging
from datetime import datetime, timedelta

import database as db
from utils import fmt_amount, fmt_date, i18n, now_local

logger = logging.getLogger(__name__)

REMIND_HOUR = 10
PRE_REMIND_DAYS = 3
CREATED_TODAY_DELAY = timedelta(minutes=30)
OVERDUE_STEPS = (1, 3, 7, 14, 30)


def _reminder_kind(debt, now):
    """Which reminder (if any) is due right now: 'before' | 'today' | 'overdue'."""
    try:
        due = datetime.strptime(debt["due_date"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("Debt %s has an unparseable due_date %r", debt["id"], debt["due_date"])
        return None

    delta = (due - now.date()).days

    if delta > 0:
        return "before" if delta == PRE_REMIND_DAYS and now.hour >= REMIND_HOUR else None

    if delta == 0:
        created = debt["created_at"]
        try:
            created_dt = datetime.strptime(str(created), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            created_dt = None
        if created_dt and created_dt.date() == now.date():
            # Added and due the same day - give them half an hour before nagging.
            return "today" if now >= created_dt + CREATED_TODAY_DELAY else None
        return "today" if now.hour >= REMIND_HOUR else None

    days_over = -delta
    if now.hour < REMIND_HOUR:
        return None
    if days_over in OVERDUE_STEPS or (days_over > 30 and days_over % 30 == 0):
        return "overdue"
    return None


async def sweep_reminders(bot, dry_run: bool = False):
    """Send every reminder that's due. Returns the count (or the list, if dry)."""
    now = now_local()
    horizon = (now.date() + timedelta(days=PRE_REMIND_DAYS)).isoformat()
    candidates = await db.get_debts_needing_reminder(now.date().isoformat(), horizon)

    due_now = []
    for debt in candidates:
        kind = _reminder_kind(debt, now)
        if kind:
            due_now.append((debt, kind))

    if dry_run:
        return [
            {
                "debt_id": d["id"],
                "user_id": d["user_id"],
                "person": d["person_name"],
                "due": d["due_date"],
                "kind": kind,
            }
            for d, kind in due_now
        ]

    lang_cache = {}
    sent = 0
    for debt, kind in due_now:
        user_id = debt["user_id"]
        if user_id not in lang_cache:
            lang_cache[user_id] = await db.get_user_lang(user_id)
        lang = lang_cache[user_id]

        remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
        direction = i18n.get(
            "dir_lent" if debt["debt_type"] == "lent" else "dir_borrowed", lang
        )
        days_over = (now.date() - datetime.strptime(debt["due_date"], "%Y-%m-%d").date()).days

        text = i18n.get(
            {"before": "reminder_before", "today": "reminder_today"}.get(kind, "reminder_overdue"),
            lang,
            direction=direction,
            name=debt["person_name"],
            amount=fmt_amount(remaining),
            currency=debt["currency"],
            date=fmt_date(debt["due_date"]),
            days=PRE_REMIND_DAYS if kind == "before" else days_over,
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
