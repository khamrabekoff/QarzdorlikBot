from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from states import AdminStates
import database as db
import os
import asyncio
import logging

router = Router()
@router.message(Command("sendall"))
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    admin_id = os.getenv("ADMIN_ID")
    if str(message.from_user.id) != str(admin_id):
        # Silently ignore or give feedback for debugging
        # await message.answer(f"Access denied. Your ID: {message.from_user.id}")
        return
    
    await message.answer("Введите текст сообщения для рассылки всем пользователям:")
    await state.set_state(AdminStates.waiting_for_broadcast_msg)

@router.message(AdminStates.waiting_for_broadcast_msg)
async def process_broadcast(message: types.Message, state: FSMContext, bot: Bot):
    admin_id = os.getenv("ADMIN_ID")
    if str(message.from_user.id) != str(admin_id):
        await state.clear()
        return

    broadcast_text = message.text
    await state.clear()
    
    users = await db.get_all_users()
    count = 0
    errors = 0
    
    status_msg = await message.answer(f"Начинаю рассылку для {len(users)} пользователей...")
    
    for user_id in users:
        try:
            await bot.send_message(user_id, broadcast_text)
            count += 1
            # Sleep slightly to avoid flood limits
            await asyncio.sleep(0.1)
        except Exception as e:
            logging.error(f"Failed to send broadcast to {user_id}: {e}")
            errors += 1
            
    await status_msg.edit_text(f"Рассылка завершена!\n✅ Успешно: {count}\n❌ Ошибок: {errors}")
