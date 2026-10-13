"""When reminders fire, and who receives them."""
from datetime import datetime, timedelta

import pytest

import database as db
import reminders
from reminders import _reminder_kind, sweep_reminders
from utils import now_local


@pytest.fixture
def at_11am(monkeypatch):
    """Today, 11:00 Tashkent - after the reminder hour, whatever the real clock says."""
    fixed = now_local().replace(hour=11, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(reminders, "now_local", lambda: fixed)
    return fixed


def _debt(due, created="2026-01-01 00:00:00"):
    return {"id": 1, "due_date": due, "created_at": created}


NOW = datetime(2026, 10, 13, 11, 0)  # local, after REMIND_HOUR


def test_three_days_before():
    assert _reminder_kind(_debt("2026-10-16"), NOW) == "before"
    assert _reminder_kind(_debt("2026-10-15"), NOW) is None


def test_due_today_waits_for_remind_hour():
    assert _reminder_kind(_debt("2026-10-13"), NOW) == "today"
    assert _reminder_kind(_debt("2026-10-13"), NOW.replace(hour=8)) is None


def test_created_today_due_today_waits_30_minutes_in_local_time():
    # Created 10:45 Tashkent = 05:45 UTC (how SQLite stores it).
    debt = _debt("2026-10-13", created="2026-10-13 05:45:00")
    assert _reminder_kind(debt, NOW) is None                         # 11:00, 15 min later
    assert _reminder_kind(debt, NOW.replace(minute=20)) == "today"   # 11:20, 35 min later


def test_overdue_backs_off():
    fired = [d for d in range(1, 95)
             if _reminder_kind(_debt((NOW.date() - timedelta(days=d)).isoformat()), NOW)]
    assert fired == [1, 3, 7, 14, 30, 60, 90]


async def test_sweep_sends_buttons_and_marks_reminded(bot, tg, ali, at_11am):
    overdue = (at_11am.date() - timedelta(days=3)).isoformat()
    debt_id = await db.add_debt(1, "lent", 1000, "so'm", "Vali", overdue)
    tg.clear()
    assert await sweep_reminders(bot) == 1
    assert f"close:{debt_id}" in tg.buttons()
    assert f"ef:{debt_id}:due" in tg.buttons()
    # Second sweep the same day sends nothing.
    assert await sweep_reminders(bot) == 0


async def test_shared_debt_reminds_both_sides(bot, tg, ali, vali, at_11am):
    """The share invitation promises reminders to both - check it happens."""
    overdue = (at_11am.date() - timedelta(days=1)).isoformat()
    debt_id = await db.add_debt(1, "lent", 5000, "so'm", "Vali", overdue)
    token = await db.create_share_token(debt_id, 1)
    await db.accept_share(token, 2)

    result = await sweep_reminders(bot, dry_run=True)
    assert result[0]["also_to"] == 2

    tg.clear()
    assert await sweep_reminders(bot) == 2
    to_vali = [m for m in tg.sent("SendMessage") if m.chat_id == 2]
    assert len(to_vali) == 1
    assert "@ali" in to_vali[0].text               # sees who recorded it
    assert "Siz qarzdorsiz" in to_vali[0].text     # direction flipped for them
    # Read-only side gets only an "open" button.
    assert [b.callback_data for row in to_vali[0].reply_markup.inline_keyboard for b in row] == \
        [f"debt:{debt_id}:borrowed"]
