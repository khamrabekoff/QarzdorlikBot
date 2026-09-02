"""Onboarding, home screen and settings.

Onboarding is one tap: pick a language, then straight to the bot.

The previous version demanded a phone number before showing anything. Nothing
in the codebase ever read that number - it was pure friction at the exact
moment a stranger decides whether this bot is worth trusting, and it collected
personal data for no purpose. Removed.
"""
import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import views
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
    if user and user["language"]:
        await show_home(message, message.from_user.id, user["language"])
    else:
        await db.upsert_user(message.from_user.id, username=message.from_user.username)
        await message.answer(i18n.get("choose_lang"), reply_markup=kb.lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def pick_language(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.split(":", 1)[1]
    if lang not in ("uz", "ru"):
        lang = "uz"
    user_before = await db.get_user(callback.from_user.id)
    await db.upsert_user(callback.from_user.id, language=lang)
    await callback.answer()

    had_language = bool(user_before and user_before["language"])
    if had_language:
        await callback.message.edit_text(i18n.get("lang_changed", lang))
    else:
        await callback.message.edit_text(i18n.get("welcome", lang))
    await show_home(callback.message, callback.from_user.id, lang)


@router.message(F.contact)
async def got_contact(message: types.Message, state: FSMContext):
    """Someone whose old keyboard still shows the removed phone button. Accept
    it gracefully rather than leaving them staring at no response."""
    lang = await db.get_user_lang(message.from_user.id)
    await state.clear()
    await show_home(message, message.from_user.id, lang)


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
