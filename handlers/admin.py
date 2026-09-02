"""Admin-only commands: stats and broadcast."""
import asyncio
import logging

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

import database as db
from config import ADMIN_ID
from states import Admin

router = Router()
logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    stats = await db.get_stats()
    await message.answer(
        "📊 <b>Statistika</b>\n"
        + "━" * 18 + "\n"
        f"<code>Foydalanuvchilar: {stats['users']}</code>\n"
        f"<code>Faol qarzlar:     {stats['debts_active']}</code>\n"
        f"<code>Yopilgan:         {stats['debts_paid']}</code>"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(Admin.broadcast)
    await message.answer("Yuboriladigan xabarni yozing.\nBekor qilish: /cancel")


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.")


@router.message(Admin.broadcast)
async def do_broadcast(message: types.Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    await state.clear()

    user_ids = await db.get_all_user_ids()
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, message.html_text)
            sent += 1
        except Exception as e:  # noqa: BLE001 - one blocked chat must not stop the rest
            logger.info("Broadcast skipped %s: %s", uid, e)
        # Telegram throttles bulk sends; ~20/sec is the safe ceiling.
        await asyncio.sleep(0.05)

    await message.answer(f"✅ {sent}/{len(user_ids)} ta foydalanuvchiga yuborildi.")
