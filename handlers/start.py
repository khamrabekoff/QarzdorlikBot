"""Onboarding, home screen, settings and help.

Onboarding is one tap: pick a language, then straight to the bot.

The previous version demanded a phone number before showing anything. Nothing
in the codebase ever read that number - it was pure friction at the exact
moment a stranger decides whether this bot is worth trusting, and it collected
personal data for no purpose. Removed.
"""
import csv
import io
import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile

import cards
import database as db
import keyboards as kb
import views
from handlers.common import edit_or_send, show_card
from keyboards import CURRENCIES
from utils import days_until, fmt_date, fmt_paid_csv, fmt_ts, i18n, lang_from_code, now_local

router = Router()
logger = logging.getLogger(__name__)


MENU_VERSION = "3"  # bump when main_kb changes, so everyone gets the new one


def filter_debts(debts, flt):
    if flt == "in":
        return [d for d in debts if d["view_type"] == "lent"]
    if flt == "out":
        return [d for d in debts if d["view_type"] == "borrowed"]
    if flt == "late":
        return [d for d in debts if (days_until(d["due_date"]) or 0) < 0]
    return list(debts)


async def render_home(bot, user_id: int, lang: str, flt: str = "all", page: int = 0, callback=None):
    """The screen everything returns to: totals on top, people as buttons.
    From a button press it edits the message in place."""
    if flt not in kb.FILTERS:
        flt = "all"
    debts = await db.get_active_debts(user_id)
    totals = await db.get_totals(user_id)
    overdue = sum(1 for d in debts if (days_until(d["due_date"]) or 0) < 0)
    soon = sum(1 for d in debts if 0 <= (days_until(d["due_date"]) or 99) <= 3)
    counts = {f: len(views.group_people(filter_debts(debts, f))) for f in ("in", "out", "late")}
    people = views.group_people(filter_debts(debts, flt))
    pages = max(1, -(-len(people) // kb.PEOPLE_PER_PAGE))
    page = min(max(page, 0), pages - 1)

    text = views.home_card(totals, overdue, soon, lang, filtered_empty=bool(debts) and not people)
    png = cards.render_home(totals, people, lang, flt, page, kb.PEOPLE_PER_PAGE)
    # Without a picture (no Pillow) the buttons must carry the amounts.
    markup = kb.home_kb(people, flt, page, counts, lang, with_amounts=png is None)
    caption = i18n.get("home_pick" if people else "home_hint_add", lang)
    if not callback:
        await _ensure_menu(bot, user_id, lang)
    await show_card(bot, user_id, png, caption, markup, text, callback=callback)


async def _ensure_menu(bot, user_id, lang):
    """The bottom menu is a reply keyboard, which can only ride on its own
    message - the home card carries inline buttons. Send it once per menu
    version (and on language change), not with every screen."""
    key = f"menu:{user_id}"
    if await db.meta_get(key) == f"{MENU_VERSION}:{lang}":
        return
    await bot.send_message(user_id, i18n.get("menu_ready", lang), reply_markup=kb.main_kb(lang))
    await db.meta_set(key, f"{MENU_VERSION}:{lang}")


async def show_home(message: types.Message, user_id: int, lang: str):
    await render_home(message.bot, user_id, lang)


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if user and user["language"]:
        await show_home(message, message.from_user.id, user["language"])
    else:
        await db.upsert_user(message.from_user.id, username=message.from_user.username)
        await message.answer(i18n.get("choose_lang"), reply_markup=kb.lang_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Works from anywhere, including halfway through adding a debt."""
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await message.answer(i18n.get("cancelled", lang))
    await show_home(message, message.from_user.id, lang)


@router.callback_query(F.data == "cancel")
async def cancel_button(callback: types.CallbackQuery, state: FSMContext):
    """Back to the home screen. A text step (e.g. 'who and how much?') is
    marked cancelled and the home card comes after it; a caption step on a
    picture turns back into the home picture in place."""
    await state.clear()
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer(i18n.get("cancelled", lang))
    if getattr(callback.message, "photo", None):
        await render_home(callback.bot, callback.from_user.id, lang, callback=callback)
        return
    await edit_or_send(callback, i18n.get("cancelled", lang))
    await render_home(callback.bot, callback.from_user.id, lang)


@router.callback_query(F.data.startswith("h:"))
async def home_nav(callback: types.CallbackQuery, state: FSMContext):
    """Filter chips, paging and every 'back to home' button."""
    await state.clear()
    parts = callback.data.split(":")
    flt = parts[1] if len(parts) > 1 else "all"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await render_home(callback.bot, callback.from_user.id, lang, flt, page, callback=callback)


@router.message(F.text.in_(i18n.all_variants("menu_home")))
async def menu_home(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await render_home(message.bot, message.from_user.id, lang)


@router.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await message.answer(i18n.get("help_text", lang))


@router.callback_query(F.data.startswith("lang:"))
async def pick_language(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.split(":", 1)[1]
    if lang not in ("uz", "ru"):
        lang = "uz"
    user_before = await db.get_user(callback.from_user.id)
    await db.upsert_user(callback.from_user.id, language=lang)
    await callback.answer()

    had_language = bool(user_before and user_before["language"])
    await edit_or_send(callback, i18n.get("lang_changed" if had_language else "welcome", lang))
    await show_home(callback.message, callback.from_user.id, lang)


@router.message(F.contact)
async def got_contact(message: types.Message, state: FSMContext):
    """Someone whose old keyboard still shows the removed phone button. Accept
    it gracefully rather than leaving them staring at no response."""
    lang = await db.get_user_lang(message.from_user.id)
    await state.clear()
    await show_home(message, message.from_user.id, lang)


# ---------- settings ----------

async def _settings_markup(user_id, lang):
    currency = await db.get_default_currency(user_id, CURRENCIES[0])
    return kb.settings_kb(lang, currency)


@router.message(F.text.in_(i18n.all_variants("menu_settings")))
async def open_settings(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await message.answer(i18n.get("settings_title", lang),
                         reply_markup=await _settings_markup(message.from_user.id, lang))


@router.callback_query(F.data == "settings")
async def back_to_settings(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await edit_or_send(callback, i18n.get("settings_title", lang),
                       await _settings_markup(callback.from_user.id, lang))


@router.callback_query(F.data == "pick_lang")
async def change_language(callback: types.CallbackQuery):
    await callback.answer()
    await edit_or_send(callback, i18n.get("choose_lang"), kb.lang_kb())


@router.callback_query(F.data == "pick_defcur")
async def pick_default_currency(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await edit_or_send(callback, i18n.get("choose_currency", lang), kb.currency_kb(lang, prefix="defcur"))


@router.callback_query(F.data.startswith("defcur:"))
async def set_default_currency(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    try:
        currency = CURRENCIES[int(callback.data.split(":", 1)[1])]
    except (ValueError, IndexError):
        currency = CURRENCIES[0]
    await db.set_user_currency(callback.from_user.id, currency)
    await callback.answer(i18n.get("currency_set", lang, cur=currency).replace("<b>", "").replace("</b>", ""))
    await edit_or_send(callback, i18n.get("settings_title", lang),
                       await _settings_markup(callback.from_user.id, lang))


@router.callback_query(F.data == "history")
async def show_history(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    debts = await db.get_closed_debts(callback.from_user.id)
    await callback.answer()
    await edit_or_send(callback, views.history_card(debts, lang), kb.back_to_settings_kb(lang))


@router.callback_query(F.data == "help")
async def help_button(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await edit_or_send(callback, i18n.get("help_text", lang), kb.back_to_settings_kb(lang))


def _cell(value) -> str:
    """Neutralise spreadsheet formulas. A name like '=HYPERLINK(...)' would be
    executed by Excel - and on a shared debt the name shown is the OTHER
    person's Telegram name, i.e. typed by someone else."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.callback_query(F.data == "export")
async def export_debts(callback: types.CallbackQuery):
    """CSV with a BOM and ';' separators: that is what Excel in Uzbek and
    Russian locales opens into proper columns on a double-click. (No openpyxl
    on the server, and a real .xlsx isn't worth a new dependency.)"""
    uid = callback.from_user.id
    lang = await db.get_user_lang(uid)
    debts = await db.get_debts_for_export(uid)
    await callback.answer()
    if not debts:
        await callback.bot.send_message(uid, i18n.get("export_empty", lang))
        return

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(i18n.get("export_headers", lang).split(";"))
    for d in debts:
        amount, paid = d["amount"] or 0, d["paid_amount"] or 0
        who = views.counterpart_name(d)
        writer.writerow([
            i18n.get("dir_lent" if d["view_type"] == "lent" else "dir_borrowed", lang),
            _cell(who),
            fmt_paid_csv(amount),
            fmt_paid_csv(paid),
            fmt_paid_csv(amount - paid),
            _cell(d["currency"]),
            fmt_date(d["due_date"]),
            i18n.get("status_paid" if d["status"] == "paid" else "status_active", lang),
            _cell(d["description"]),
            fmt_ts(d["created_at"]),
            fmt_ts(d["closed_at"]),
        ])

    data = ("﻿" + buf.getvalue()).encode("utf-8")
    name = f"qarzlar_{now_local().strftime('%Y-%m-%d')}.csv"
    await callback.bot.send_document(
        uid, BufferedInputFile(data, filename=name), caption=i18n.get("export_caption", lang)
    )


@router.callback_query(F.data == "home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await render_home(callback.bot, callback.from_user.id, lang, callback=callback)


def default_lang(from_user) -> str:
    return lang_from_code(getattr(from_user, "language_code", None))
