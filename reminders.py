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

A confirmed shared debt is reminded to BOTH sides. The share invitation
promised exactly that ("eslatma ikkalangizga ham keladi"), but until v2.1
only the owner was ever messaged.

Debts with no due date are never reminded about; they are excluded in SQL.
"""
import logging
from datetime import datetime, timedelta

import database as db
import keyboards as kb
import views
from utils import display_name, now_local, utc_to_local

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
        # created_at is UTC (SQLite CURRENT_TIMESTAMP); `now` is local.
        created_dt = utc_to_local(debt["created_at"])
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


def _recipients(debt):
    """(user_id, is_owner) for everyone who should hear about this debt."""
    out = [(debt["user_id"], True)]
    if debt["counterparty_id"] and debt["share_status"] == "accepted":
        out.append((debt["counterparty_id"], False))
    return out


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
                "also_to": d["counterparty_id"] if d["share_status"] == "accepted" else None,
                "person": d["person_name"],
                "due": d["due_date"],
                "kind": kind,
            }
            for d, kind in due_now
        ]

    users = {}

    async def user(uid):
        if uid not in users:
            users[uid] = await db.get_user(uid)
        return users[uid]

    sent = 0
    for debt, kind in due_now:
        days_over = (now.date() - datetime.strptime(debt["due_date"], "%Y-%m-%d").date()).days
        days = PRE_REMIND_DAYS if kind == "before" else days_over
        owner = await user(debt["user_id"])
        owner_display = display_name(owner and owner["username"], owner and owner["first_name"])

        delivered = False
        for uid, is_owner in _recipients(debt):
            u = await user(uid)
            lang = (u and u["language"]) or "uz"
            view_type = debt["debt_type"] if is_owner else (
                "borrowed" if debt["debt_type"] == "lent" else "lent")
            try:
                await bot.send_message(
                    uid,
                    views.reminder_text(debt, kind, lang, days, is_owner, owner_display),
                    reply_markup=kb.reminder_kb(debt["id"], view_type, lang, is_owner=is_owner),
                )
                sent += 1
                delivered = True
            except Exception as e:  # noqa: BLE001 - one bad chat must not stop the sweep
                logger.warning("Reminder to %s failed: %s", uid, e)

        if delivered:
            await db.update_last_reminded(debt["id"], now.strftime("%Y-%m-%d %H:%M:%S"))

    if sent:
        logger.info("Reminder sweep sent %d messages", sent)
    return sent
