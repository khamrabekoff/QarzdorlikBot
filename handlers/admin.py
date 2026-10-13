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
# Two versions, one per language: each user gets the one matching the
# language they picked in the bot. Russian is optional - skip it and
# everyone gets the Uzbek one.

def _kb(*buttons):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t, callback_data=d) for t, d in buttons]])


async def _start_broadcast(message: types.Message, state: FSMContext):
    await state.set_state(Admin.broadcast)
    await message.answer(
        "📢 <b>Hammaga xabar</b>\n\n"
        "1/2 · 🇺🇿 <b>O'zbekcha</b> variantini yuboring (matn, rasm yoki ovozli xabar).\n\n"
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
async def got_broadcast_uz(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    await state.update_data(src_chat=message.chat.id, src_uz=message.message_id, src_ru=None)
    await state.set_state(Admin.broadcast_ru)
    await message.answer(
        "2/2 · 🇷🇺 Endi <b>ruscha</b> variantini yuboring.\n\n"
        "<i>Rus tilini tanlaganlar shuni oladi.</i>",
        reply_markup=_kb(("⏭ O'tkazib yuborish — hammaga o'zbekcha", "abcast_skip")),
    )


@router.message(Admin.broadcast_ru)
async def got_broadcast_ru(message: types.Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    await state.update_data(src_ru=message.message_id)
    await _preview(message.chat.id, state, bot)


@router.callback_query(F.data == "abcast_skip", Admin.broadcast_ru)
async def skip_broadcast_ru(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    if _is_admin(callback.from_user.id):
        await _preview(callback.message.chat.id, state, bot)


def _split_by_lang(users):
    """[(id, lang)] -> (uzbek_ids, russian_ids); unknown language counts as Uzbek."""
    ru = [uid for uid, lang in users if lang == "ru"]
    uz = [uid for uid, lang in users if lang != "ru"]
    return uz, ru


async def _preview(chat_id, state: FSMContext, bot: Bot):
    """Show exactly what each group will receive, and ask before sending."""
    data = await state.get_data()
    uz, ru = _split_by_lang(await db.get_user_ids_with_lang())
    await state.set_state(Admin.broadcast_confirm)

    if data.get("src_ru"):
        await bot.send_message(chat_id, f"👇 🇺🇿 O'zbekcha — <b>{len(uz)}</b> kishi:")
        await bot.copy_message(chat_id, data["src_chat"], data["src_uz"])
        await bot.send_message(chat_id, f"👇 🇷🇺 Ruscha — <b>{len(ru)}</b> kishi:")
        await bot.copy_message(chat_id, data["src_chat"], data["src_ru"])
    else:
        await bot.send_message(chat_id, f"👇 Hammaga (<b>{len(uz) + len(ru)}</b> kishi) shu boradi:")
        await bot.copy_message(chat_id, data["src_chat"], data["src_uz"])
    await bot.send_message(chat_id, "Yuborilsinmi?",
                           reply_markup=_kb(("✅ Yuborish", "abcast_go"), ("❌ Bekor", "cancel")))


@router.callback_query(F.data == "abcast_go", Admin.broadcast_confirm)
async def do_broadcast(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("⏳ Yuborilmoqda…")

    uz, ru = _split_by_lang(await db.get_user_ids_with_lang())
    src_ru = data.get("src_ru") or data["src_uz"]
    plan = [(uid, data["src_uz"]) for uid in uz] + [(uid, src_ru) for uid in ru]
    sent = 0
    for uid, src in plan:
        try:
            await bot.copy_message(uid, data["src_chat"], src)
            sent += 1
        except Exception as e:  # noqa: BLE001 - one blocked chat must not stop the rest
            logger.info("Broadcast skipped %s: %s", uid, e)
        # Telegram throttles bulk sends; ~20/sec is the safe ceiling.
        await asyncio.sleep(0.05)

    await callback.message.edit_text(
        f"✅ {sent}/{len(plan)} ta foydalanuvchiga yuborildi "
        f"(🇺🇿 {len(uz)} · 🇷🇺 {len(ru)})."
        + (f"\n<i>{len(plan) - sent} tasi botni bloklagan.</i>" if sent < len(plan) else "")
    )
