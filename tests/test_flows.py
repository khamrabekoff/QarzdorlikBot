"""End-to-end conversations through the real dispatcher.

Every message the bot sends is also checked for valid HTML (see conftest),
so an unescaped name anywhere fails the test that produced it.
"""
from datetime import timedelta

import aiosqlite

import database as db
from tests.conftest import Chatter, _DB
from utils import now_local


async def _debts(user_id):
    async with aiosqlite.connect(_DB) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM debts WHERE user_id = ? ORDER BY id", (user_id,)) as cur:
            return await cur.fetchall()


# ---------- onboarding ----------

async def test_new_user_picks_language_then_lands_home(bot, tg):
    c = Chatter(bot, uid=10)
    await c.say("/start")
    assert "Tilni tanlang" in tg.last_text()
    await c.tap("lang:ru")
    assert "МОИ ДОЛГИ" in tg.last_text()
    assert (await db.get_user(10))["language"] == "ru"


# ---------- adding ----------

async def test_add_debt_three_steps(ali, tg):
    await ali.say("➕ Yangi qarz")
    await ali.tap("type:lent")
    await ali.say("Vali aka 500k")
    assert "date:w1" in tg.buttons() and "cancel" in tg.buttons()
    await ali.tap("date:w1")
    assert "Vali aka" in tg.last_text() and "500 000" in tg.last_text()
    await ali.tap("save")

    [debt] = await _debts(1)
    assert (debt["person_name"], debt["amount"], debt["debt_type"]) == ("Vali aka", 500000, "lent")
    assert debt["due_date"] == (now_local().date() + timedelta(weeks=1)).isoformat()
    # Right after saving, the bot offers to send it to the other person.
    assert f"share:{debt['id']}" in tg.buttons()


async def test_double_tap_on_save_does_not_duplicate(ali, tg):
    await ali.say("➕ Yangi qarz")
    await ali.tap("type:lent")
    await ali.say("Ali 1000")
    await ali.tap("date:none")
    await ali.tap("save")
    await ali.tap("save")
    assert len(await _debts(1)) == 1
    assert any("eskirgan" in a for a in tg.alerts())


async def test_note_and_currency_on_confirm(ali, tg):
    await ali.say("➕ Yangi qarz")
    await ali.tap("type:borrowed")
    await ali.say("Sardor 100")
    await ali.tap("date:none")
    await ali.tap("pick_cur")
    await ali.tap("cur:1")
    await ali.tap("add_note")
    await ali.say("telefon uchun")
    assert "telefon uchun" in tg.last_text()
    await ali.tap("save")
    [debt] = await _debts(1)
    assert (debt["currency"], debt["description"], debt["due_date"]) == ("$", "telefon uchun", None)


async def test_menu_button_mid_flow_is_not_saved_as_a_name(ali, tg):
    """The old handler order turned '📥 Menga qarzdor' into a debtor's name."""
    await ali.say("➕ Yangi qarz")
    await ali.tap("type:lent")
    await ali.say("📥 Menga qarzdor")
    assert "MENGA QARZDOR" in tg.last_text()
    await ali.say("Vali 500")          # flow is over: this is now a quick add
    assert "qtype:lent" in tg.buttons()
    assert await _debts(1) == []


async def test_cancel_button_leaves_the_flow(ali, tg):
    await ali.say("➕ Yangi qarz")
    await ali.tap("type:lent")
    await ali.tap("cancel")
    assert "Bekor qilindi" in tg.last_text()
    await ali.say("salom")
    assert "Ali 500000" in tg.last_text()  # the hint, not 'how much?'


async def test_quick_add_from_free_text(ali, tg):
    await ali.say("Vali 1,5 mln")
    await ali.tap("qtype:borrowed")
    await ali.tap("date:none")
    await ali.tap("save")
    [debt] = await _debts(1)
    assert (debt["person_name"], debt["amount"], debt["debt_type"]) == ("Vali", 1500000, "borrowed")


# ---------- finding ----------

async def test_typing_a_name_searches(ali, tg):
    await db.add_debt(1, "lent", 700, "so'm", "Али", None)
    await ali.say("али")                   # Cyrillic, different case
    assert "Али" in tg.last_text() and "700" in tg.last_text()


async def test_unknown_text_gets_a_hint_not_silence(ali, tg):
    tg.clear()
    await ali.say("salom")
    assert tg.texts(), "the bot must answer"


async def test_stale_button_is_answered(ali, tg):
    await ali.tap("date:w1")               # no add flow in progress
    assert any("eskirgan" in a for a in tg.alerts())


# ---------- paying / closing ----------

async def test_pay_half_with_one_tap(ali, tg):
    debt_id = await db.add_debt(1, "lent", 1000, "so'm", "Vali", None)
    await ali.tap(f"pay:{debt_id}")
    assert f"payhalf:{debt_id}" in tg.buttons()
    await ali.tap(f"payhalf:{debt_id}")
    debt = await db.get_debt(debt_id)
    assert (debt["paid_amount"], debt["status"]) == (500, "active")


async def test_typed_payment_larger_than_remaining_is_refused(ali, tg):
    debt_id = await db.add_debt(1, "lent", 1000, "so'm", "Vali", None)
    await ali.tap(f"pay:{debt_id}")
    await ali.say("5000")
    assert (await db.get_debt(debt_id))["paid_amount"] == 0
    await ali.say("300")
    assert (await db.get_debt(debt_id))["paid_amount"] == 300


async def test_close_then_undo_restores_partial_payment(ali, tg):
    debt_id = await db.add_debt(1, "lent", 1000, "so'm", "Vali", None)
    await db.update_debt_payment(debt_id, 1, 200)
    await ali.tap(f"close:{debt_id}")
    assert (await db.get_debt(debt_id))["status"] == "paid"
    undo = next(b for b in tg.buttons() if b.startswith("reopen:"))
    await ali.tap(undo)
    debt = await db.get_debt(debt_id)
    assert (debt["status"], debt["paid_amount"], debt["closed_at"]) == ("active", 200, None)


async def test_cannot_touch_someone_elses_debt(ali, vali, tg):
    debt_id = await db.add_debt(1, "lent", 1000, "so'm", "Vali", None)
    for data in (f"close:{debt_id}", f"delyes:{debt_id}", f"payall:{debt_id}", f"rd:{debt_id}:w1"):
        await vali.tap(data)
    debt = await db.get_debt(debt_id)
    assert (debt["status"], debt["due_date"]) == ("active", None)


# ---------- editing ----------

async def test_edit_amount_and_date(ali, tg):
    debt_id = await db.add_debt(1, "lent", 1000, "so'm", "Vali", None)
    await db.update_debt_payment(debt_id, 1, 400)

    await ali.tap(f"ef:{debt_id}:amount")
    await ali.say("300")                     # below what's already paid
    assert (await db.get_debt(debt_id))["amount"] == 1000
    await ali.say("2000")
    assert (await db.get_debt(debt_id))["amount"] == 2000

    await db.update_last_reminded(debt_id, "2026-01-01 10:00:00")
    await ali.tap(f"ef:{debt_id}:due")
    await ali.tap(f"rd:{debt_id}:t1")
    debt = await db.get_debt(debt_id)
    assert debt["due_date"] == (now_local().date() + timedelta(days=1)).isoformat()
    assert debt["last_reminded_at"] is None  # new deadline, fresh reminders


async def test_names_with_html_are_safe_everywhere(ali, tg):
    """conftest fails any message with broken HTML."""
    await ali.say("➕ Yangi qarz")
    await ali.tap("type:lent")
    await ali.say("<b>Ali & Co 500")
    await ali.tap("date:t1")
    await ali.tap("save")
    [debt] = await _debts(1)
    assert debt["person_name"] == "<b>Ali & Co"
    await ali.say("📥 Menga qarzdor")
    await ali.tap(f"debt:{debt['id']}:lent")
    assert "&lt;b&gt;Ali &amp; Co" in tg.last_text()


# ---------- sharing ----------

async def test_share_accept_and_owner_changes_reach_the_other_side(bot, tg, ali):
    debt_id = await db.add_debt(1, "lent", 5000, "so'm", "Vali", None)
    token = await db.create_share_token(debt_id, 1)

    # A Russian-speaking newcomer opens the link: no language question,
    # Russian guessed from their Telegram app.
    vali = Chatter(bot, uid=2, first_name="Вали", username=None, lang_code="ru")
    await vali.say(f"/start d_{token}")
    assert "Подтверждение долга" in tg.last_text()
    await vali.tap(f"shyes:{token}")
    assert (await db.get_debt(debt_id))["share_status"] == "accepted"

    tg.clear()
    await ali.tap(f"close:{debt_id}")
    to_vali = [m.text for m in tg.sent("SendMessage") if m.chat_id == 2]
    assert to_vali and "закрыл" in to_vali[0]


async def test_stranger_cannot_decline_an_accepted_share(bot, ali, vali):
    debt_id = await db.add_debt(1, "lent", 5000, "so'm", "Vali", None)
    token = await db.create_share_token(debt_id, 1)
    await vali.tap(f"shyes:{token}")
    stranger = Chatter(bot, uid=3)
    await stranger.register()
    await stranger.tap(f"shno:{token}")
    assert (await db.get_debt(debt_id))["share_status"] == "accepted"


# ---------- settings ----------

async def test_default_currency_is_used_for_new_debts(ali, tg):
    await ali.tap("defcur:1")
    await ali.say("Vali 100")
    await ali.tap("qtype:lent")
    await ali.tap("date:none")
    await ali.tap("save")
    [debt] = await _debts(1)
    assert debt["currency"] == "$"


async def test_export_sends_a_csv_excel_can_open(ali, tg):
    await db.add_debt(1, "lent", 1500000, "so'm", "Vali", "2026-12-01", "telefon")
    await ali.tap("export")
    [doc] = tg.sent("SendDocument")
    data = doc.document.data.decode("utf-8")
    assert data.startswith("﻿")                    # BOM: Excel reads UTF-8
    assert "Vali;1500000;0;1500000;so'm;01.12.2026;Faol;telefon" in data


async def test_export_neutralises_spreadsheet_formulas(ali, tg):
    await db.add_debt(1, "lent", 1, "so'm", "=HYPERLINK(\"http://x\")", None)
    await ali.tap("export")
    data = tg.sent("SendDocument")[0].document.data.decode("utf-8")
    assert "'=HYPERLINK" in data and ";=HYPERLINK" not in data


# ---------- admin ----------

async def test_admin_users_list_and_direct_message(bot, tg, ali):
    admin = Chatter(bot, uid=999, first_name="Boss", username="boss")
    await admin.register()
    await admin.say("/users")
    assert "Foydalanuvchilar: 2" in tg.last_text() and "@ali" in tg.last_text()
    assert "amsg:1" in tg.buttons() and "amsg:999" not in tg.buttons()

    await admin.tap("amsg:1")
    tg.clear()
    await admin.say("Salom, yangi versiya chiqdi!")
    [copy] = tg.sent("CopyMessage")
    assert copy.chat_id == 1 and copy.from_chat_id == 999


async def test_broadcast_asks_before_sending(bot, tg, ali):
    admin = Chatter(bot, uid=999, first_name="Boss", username="boss")
    await admin.register()
    await admin.say("/broadcast")
    tg.clear()
    await admin.say("Hammaga salom")
    # Only the preview to the admin so far - nobody else got anything.
    assert [m.chat_id for m in tg.sent("CopyMessage")] == [999]
    assert "abcast_go" in tg.buttons()
    tg.clear()
    await admin.tap("abcast_go")
    assert sorted(m.chat_id for m in tg.sent("CopyMessage")) == [1, 999]


async def test_non_admin_cannot_use_admin_commands(ali, tg):
    tg.clear()
    await ali.say("/users")
    await ali.tap("amsg:1")
    assert not any("Foydalanuvchilar" in t for t in tg.texts())
    assert tg.sent("CopyMessage") == []
