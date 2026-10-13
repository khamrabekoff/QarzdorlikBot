"""Admin-only commands: stats, users, backup, messaging."""
import asyncio
import logging

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database as db
import ops
from config import ADMIN_ID
from states import Admin
from utils import display_name, esc, now_local, utc_to_local

router = Router()
logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    stats = await db.get_stats()
    g = await db.get_growth_stats()

    def pct(part, whole):
        return f"{round(100 * part / whole)}%" if whole else "-"

    await message.answer(
        "📊 <b>Statistika</b>\n" + "━" * 18 + "\n"
        f"<code>Foydalanuvchilar: {stats['users']}</code>\n"
        f"<code>Faol qarzlar:     {stats['debts_active']}</code>\n"
        f"<code>Yopilgan:         {stats['debts_paid']}</code>\n\n"
        "📈 <b>O'sish</b>\n" + "━" * 18 + "\n"
        f"<code>Yangi (7 kun):    {g['users_week']}</code>\n"
        f"<code>Faol (7 kun):     {g['active_week']} ({pct(g['active_week'], g['users_total'])})</code>\n"
        f"<code>Faol (30 kun):    {g['active_month']} ({pct(g['active_month'], g['users_total'])})</code>\n"
        f"<code>Qaytganlar:       {g['returning_week']}</code>\n"
        f"<code>Yangi qarzlar:    {g['debts_week']}</code>\n\n"
        "🤝 <b>Ulashish</b>\n" + "━" * 18 + "\n"
        f"<code>Yuborilgan:       {g['shares_sent']}</code>\n"
        f"<code>Tasdiqlangan:     {g['shares_accepted']} ({pct(g['shares_accepted'], g['shares_sent'])})</code>\n"
        f"<code>Taklif orqali:    {g['from_referral']}</code>"
    )


@router.message(Command("backup"))
async def cmd_backup(message: types.Message, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    try:
        await ops.send_backup(bot)
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ {e}")


# ---------- users ----------

def _ago(ts):
    """'bugun' / '3 kun oldin' / '—' from a stored UTC timestamp."""
    local = utc_to_local(ts)
    if not local:
        return "—"
    days = (now_local().date() - local.date()).days
    return "bugun" if days <= 0 else ("kecha" if days == 1 else f"{days} kun oldin")


@router.message(Command("users"))
async def cmd_users(message: types.Message, state: FSMContext):
    """Who uses the bot, with a button to write to each of them."""
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    users = await db.get_users_overview(limit=30)
    total = await db.count_users()

    lines, buttons = [], []
    for i, u in enumerate(users, 1):
        name = esc(display_name(u["username"], u["first_name"]))
        if name == "?":
            name = f"<code>{u['id']}</code>"
        src = {"debt_share": " 🤝", "ref_link": " 🎁"}.get(u["source"] or "", "")
        lines.append(
            f"<b>{i}. {name}</b>{src} · {u['language'] or '?'}\n"
            f"     📒 {u['active']} faol / {u['total']} jami · 🕓 {_ago(u['last_active'] or u['joined_at'])}"
        )
        if u["id"] != message.from_user.id:
            buttons.append(InlineKeyboardButton(text=f"✉️ {i}", callback_data=f"amsg:{u['id']}"))

    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton(text="📢 Hammaga yozish", callback_data="abcast")])
    more = f"\n\n<i>…va yana {total - len(users)} ta</i>" if total > len(users) else ""
    await message.answer(
        f"👥 <b>Foydalanuvchilar: {total}</b>\n" + "━" * 18 + "\n\n"
        + "\n\n".join(lines) + more
        + "\n\n<i>🤝 qarz havolasi orqali · 🎁 taklif orqali</i>\n"
        "<i>Yozish uchun raqamni bosing 👇</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("amsg:"))
async def ask_direct(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    target = int(callback.data.split(":", 1)[1])
    user = await db.get_user(target)
    name = esc(display_name(user and user["username"], user and user["first_name"]))
    await state.set_state(Admin.direct)
    await state.update_data(target=target)
    await callback.answer()
    await callback.message.answer(
        f"✉️ <b>{name}</b> ga xabar yozing (matn, rasm yoki ovozli xabar).\n\n"
        "<i>Xabar bot nomidan boradi. Bekor qilish: /cancel</i>"
    )


@router.message(Admin.direct)
async def do_direct(message: types.Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    target = (await state.get_data()).get("target")
    await state.clear()
    try:
        # copy_message keeps photos, voice and formatting, without the
        # "forwarded from" header that would expose the admin's account.
        await bot.copy_message(target, message.chat.id, message.message_id)
        await message.answer("✅ Yuborildi.")
    except Exception as e:  # noqa: BLE001 - e.g. the user blocked the bot
        await message.answer(f"❌ Yuborilmadi: {esc(str(e))[:200]}")


# ---------- broadcast ----------

async def _start_broadcast(message: types.Message, state: FSMContext):
    await state.set_state(Admin.broadcast)
    await message.answer(
        "📢 Hammaga yuboriladigan xabarni yozing (matn, rasm yoki ovozli xabar).\n\n"
        "<i>Yuborishdan oldin ko'rib chiqasiz. Bekor qilish: /cancel</i>"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if _is_admin(message.from_user.id):
        await _start_broadcast(message, state)


@router.callback_query(F.data == "abcast")
async def broadcast_button(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if _is_admin(callback.from_user.id):
        await _start_broadcast(callback.message, state)


@router.message(Admin.broadcast)
async def preview_broadcast(message: types.Message, state: FSMContext, bot: Bot):
    """Show exactly what people will receive, and ask before sending - the
    old version went out to everyone the moment the text was typed."""
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    total = len(await db.get_all_user_ids())
    await state.set_state(Admin.broadcast_confirm)
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    await message.answer("👇 Foydalanuvchilar shunday ko'radi:")
    await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    await message.answer(
        f"Yuborilsinmi? Qabul qiluvchilar: <b>{total}</b> ta",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yuborish", callback_data="abcast_go"),
            InlineKeyboardButton(text="❌ Bekor", callback_data="cancel"),
        ]]),
    )


@router.callback_query(F.data == "abcast_go", Admin.broadcast_confirm)
async def do_broadcast(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("⏳ Yuborilmoqda…")

    user_ids = await db.get_all_user_ids()
    sent = 0
    for uid in user_ids:
        try:
            await bot.copy_message(uid, data["src_chat"], data["src_msg"])
            sent += 1
        except Exception as e:  # noqa: BLE001 - one blocked chat must not stop the rest
            logger.info("Broadcast skipped %s: %s", uid, e)
        # Telegram throttles bulk sends; ~20/sec is the safe ceiling.
        await asyncio.sleep(0.05)

    await callback.message.edit_text(
        f"✅ {sent}/{len(user_ids)} ta foydalanuvchiga yuborildi."
        + (f"\n<i>{len(user_ids) - sent} tasi botni bloklagan.</i>" if sent < len(user_ids) else "")
    )
