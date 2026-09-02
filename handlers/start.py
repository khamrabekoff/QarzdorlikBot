from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from states import Registration
from utils import i18n
import keyboards as kb
import database as db

router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    if user:
        lang = user[2] # language
        await message.answer(i18n.get("main_menu", lang), reply_markup=kb.get_main_kb(lang))
    else:
        await state.set_state(Registration.choosing_lang)
        await message.answer(i18n.get("welcome", "uz"), reply_markup=kb.get_lang_kb())

@router.message(Registration.choosing_lang)
async def process_lang(message: types.Message, state: FSMContext):
    text = message.text
    lang = "uz"
    if "Русский" in text:
        lang = "ru"
    
    await state.update_data(lang=lang)
    
    # Check if user already exists (Settings flow)
    user = await db.get_user(message.from_user.id)
    if user:
        # Update lang in DB immediately
        await db.update_user_lang(message.from_user.id, lang)
        await state.clear()
        await message.answer(i18n.get("lang_changed", lang), reply_markup=kb.get_main_kb(lang))
        # Send main menu
        await message.answer(i18n.get("main_menu", lang))
    else:
        # Registration flow
        await state.set_state(Registration.sending_phone)
        await message.answer(i18n.get("ask_phone", lang), reply_markup=kb.get_phone_kb(lang))

@router.message(Registration.sending_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    phone = message.contact.phone_number
    
    await db.add_user(message.from_user.id, message.from_user.username)
    await db.update_user_lang(message.from_user.id, lang)
    await db.update_user_phone(message.from_user.id, phone)
    
    await state.clear()
    await message.answer(i18n.get("main_menu", lang), reply_markup=kb.get_main_kb(lang))
