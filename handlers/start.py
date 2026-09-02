"""Onboarding, home screen and settings.

Onboarding is deliberately two taps: pick a language, share a phone. Every
extra question here costs users who would otherwise have stayed.
"""
import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import views
from states import Registration
from utils import days_until, i18n

router = Router()
logger = logging.getLogger(__name__)


async def show_home(message: types.Message, user_id: int, lang: str):
    """The screen everything returns to."""
    totals = await db.get_totals(user_id)
    active = await db.get_active_debts(user_id)
    overdue = sum(1 for d in active if (days_until(d["due_date"]) or 0) < 0)
    soon = sum(1 for d in active if 0 <= (days_until(d["due_date"]) or 99) <= 3)
    await message.answer(
        views.home_card(totals, overdue, soon, lang),
        reply_markup=kb.main_kb(lang),
    )


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if user and user["phone_number"]:
        await show_home(message, message.from_user.id, user["language"] or "uz")
    else:
        await db.upsert_user(message.from_user.id, username=message.from_user.username)
        await message.answer(i18n.get("choose_lang"), reply_markup=kb.lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def pick_language(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.split(":", 1)[1]
    if lang not in ("uz", "ru"):
        lang = "uz"
    await db.upsert_user(callback.from_user.id, language=lang)
    await callback.answer()

    user = await db.get_user(callback.from_user.id)
    if user and user["phone_number"]:
        # Language change from settings - straight back home.
        await callback.message.edit_text(i18n.get("lang_changed", lang))
        await show_home(callback.message, callback.from_user.id, lang)
        return

    await callback.message.edit_text(i18n.get("welcome", lang))
    await callback.message.answer(i18n.get("ask_phone", lang), reply_markup=kb.phone_kb(lang))
    await state.set_state(Registration.sending_phone)


@router.message(F.contact)
async def got_contact(message: types.Message, state: FSMContext):
    """Accepted in any state - a contact can only mean one thing, and making
    it state-dependent left users stuck if their state had been lost."""
    lang = await db.get_user_lang(message.from_user.id)
    await db.upsert_user(
        message.from_user.id,
        username=message.from_user.username,
        phone=message.contact.phone_number,
    )
    await state.clear()
    await message.answer(i18n.get("registered", lang), reply_markup=kb.main_kb(lang))
    await show_home(message, message.from_user.id, lang)


@router.message(Registration.sending_phone)
async def nudge_for_contact(message: types.Message):
    lang = await db.get_user_lang(message.from_user.id)
    await message.answer(i18n.get("ask_phone", lang), reply_markup=kb.phone_kb(lang))


# ---------- settings ----------

@router.message(F.text.in_(i18n.all_variants("menu_settings")))
async def open_settings(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await message.answer(i18n.get("settings_title", lang), reply_markup=kb.settings_kb(lang))


@router.callback_query(F.data == "pick_lang")
async def change_language(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(i18n.get("choose_lang"), reply_markup=kb.lang_kb())


@router.callback_query(F.data == "history")
async def show_history(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    debts = await db.get_closed_debts(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(views.history_card(debts, lang))


@router.callback_query(F.data == "home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await show_home(callback.message, callback.from_user.id, lang)
