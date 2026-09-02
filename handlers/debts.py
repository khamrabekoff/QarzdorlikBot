"""Adding, listing, and settling debts.

The add flow used to be five separate questions (type, name, amount, currency,
date). It is now three: type, "who and how much" on one line, and a date -
with the currency defaulting to whatever the person used last. Fewer steps
means fewer chances to lose someone halfway.
"""
import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import views
from handlers.start import show_home
from keyboards import CURRENCIES
from states import AddDebt, Pay
from utils import fmt_amount, i18n, parse_amount, parse_date, shift_date

router = Router()
logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = CURRENCIES[0]


async def _edit(callback: types.CallbackQuery, text, markup=None):
    """Edit in place, ignoring Telegram's complaint when nothing changed."""
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception as e:  # noqa: BLE001
        if "not modified" not in str(e).lower():
            logger.debug("edit_text fell back to send: %s", e)
            await callback.message.answer(text, reply_markup=markup)


async def _last_currency(user_id):
    debts = await db.get_active_debts(user_id)
    return debts[0]["currency"] if debts else DEFAULT_CURRENCY


# ---------- add ----------

@router.message(F.text.in_(i18n.all_variants("menu_add")))
async def start_add(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await state.update_data(lang=lang)
    await message.answer(i18n.get("add_choose_type", lang), reply_markup=kb.type_kb(lang))


@router.callback_query(F.data.startswith("type:"))
async def picked_type(callback: types.CallbackQuery, state: FSMContext):
    debt_type = callback.data.split(":", 1)[1]
    lang = await db.get_user_lang(callback.from_user.id)
    await state.update_data(debt_type=debt_type, lang=lang)

    names = await db.get_recent_names(callback.from_user.id)
    await state.update_data(recent_names=names)
    await callback.answer()
    await _edit(callback, i18n.get("add_who_amount", lang), kb.names_kb(names, lang))
    await state.set_state(AddDebt.entering_who_amount)


@router.callback_query(F.data.startswith("name:"), AddDebt.entering_who_amount)
async def picked_name(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    names = data.get("recent_names", [])
    try:
        name = names[int(callback.data.split(":", 1)[1])]
    except (ValueError, IndexError):
        await callback.answer(i18n.get("err_not_found", data.get("lang", "uz")), show_alert=True)
        return
    await state.update_data(person_name=name)
    await callback.answer()
    await _edit(callback, i18n.get("add_ask_amount", data.get("lang", "uz"), name=name))
    await state.set_state(AddDebt.entering_amount)


@router.message(AddDebt.entering_who_amount)
async def got_who_amount(message: types.Message, state: FSMContext):
    """Accepts 'Ali 500000' or just 'Ali'."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    parts = (message.text or "").strip().split()

    if len(parts) >= 2:
        amount = parse_amount(" ".join(parts[1:]))
        if amount:
            await state.update_data(person_name=parts[0], amount=amount)
            await _ask_date(message, state, lang)
            return

    name = (message.text or "").strip()
    if not name:
        await message.answer(i18n.get("add_who_amount", lang))
        return
    await state.update_data(person_name=name)
    await message.answer(i18n.get("add_ask_amount", lang, name=name))
    await state.set_state(AddDebt.entering_amount)


@router.message(AddDebt.entering_amount)
async def got_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    amount = parse_amount(message.text)
    if not amount:
        await message.answer(i18n.get("err_amount", lang))
        return
    await state.update_data(amount=amount)
    await _ask_date(message, state, lang)


async def _ask_date(message: types.Message, state: FSMContext, lang):
    await message.answer(
        i18n.get("add_ask_date", lang) + "\n\n" + i18n.get("date_hint", lang),
        reply_markup=kb.date_kb(lang),
    )
    await state.set_state(AddDebt.entering_date)


@router.callback_query(F.data.startswith("date:"), AddDebt.entering_date)
async def picked_date(callback: types.CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "none":
        # An open-ended debt: recorded, listed last, never reminded about.
        await callback.answer()
        await _show_confirm(callback.message, state, None, edit_from=callback,
                            user_id=callback.from_user.id)
        return
    iso = {
        "d3": lambda: shift_date(days=3),
        "w1": lambda: shift_date(weeks=1),
        "d10": lambda: shift_date(days=10),
        "m1": lambda: shift_date(months=1),
        "ew": lambda: shift_date(end_of="week"),
        "em": lambda: shift_date(end_of="month"),
    }.get(choice, lambda: shift_date(days=7))()
    await callback.answer()
    await _show_confirm(callback.message, state, iso, edit_from=callback,
                        user_id=callback.from_user.id)


@router.message(AddDebt.entering_date)
async def typed_date(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    iso = parse_date(message.text)
    if not iso:
        await message.answer(i18n.get("err_date", lang))
        return
    await _show_confirm(message, state, iso, user_id=message.from_user.id)


async def _show_confirm(message, state: FSMContext, iso_date, edit_from=None, user_id=None):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    # user_id is passed explicitly: `message` here may be a callback's message,
    # whose from_user is the BOT, not the person we want the currency for.
    currency = data.get("currency") or await _last_currency(user_id or message.chat.id)
    await state.update_data(due_date=iso_date, currency=currency)

    payload = {
        "person_name": data.get("person_name", "?"),
        "amount": data.get("amount", 0),
        "currency": currency,
        "due_date": iso_date,
        "debt_type": data.get("debt_type", "lent"),
    }
    text = views.confirm_card(payload, lang)
    if edit_from:
        await _edit(edit_from, text, kb.confirm_kb(lang))
    else:
        await message.answer(text, reply_markup=kb.confirm_kb(lang))


@router.callback_query(F.data == "pick_cur")
async def pick_currency(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await callback.answer()
    await _edit(callback, i18n.get("choose_currency", data.get("lang", "uz")),
                kb.currency_kb(data.get("lang", "uz")))


@router.callback_query(F.data.startswith("cur:"))
async def picked_currency(callback: types.CallbackQuery, state: FSMContext):
    try:
        currency = CURRENCIES[int(callback.data.split(":", 1)[1])]
    except (ValueError, IndexError):
        currency = DEFAULT_CURRENCY
    data = await state.get_data()
    await state.update_data(currency=currency)
    await callback.answer()

    payload = {
        "person_name": data.get("person_name", "?"),
        "amount": data.get("amount", 0),
        "currency": currency,
        "due_date": data.get("due_date"),
        "debt_type": data.get("debt_type", "lent"),
    }
    await _edit(callback, views.confirm_card(payload, data.get("lang", "uz")),
                kb.confirm_kb(data.get("lang", "uz")))


@router.callback_query(F.data == "save")
async def save_debt(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    # due_date is intentionally absent: an open-ended debt stores NULL.
    required = ("debt_type", "person_name", "amount")
    if not all(data.get(k) for k in required):
        await callback.answer(i18n.get("err_generic", lang), show_alert=True)
        await state.clear()
        return

    await db.add_debt(
        user_id=callback.from_user.id,
        debt_type=data["debt_type"],
        amount=data["amount"],
        currency=data.get("currency") or DEFAULT_CURRENCY,
        person_name=data["person_name"],
        due_date=data.get("due_date"),
    )
    await state.clear()
    await callback.answer()
    await _edit(callback, i18n.get("confirm_saved", lang))
    await show_home(callback.message, callback.from_user.id, lang)


# ---------- lists ----------

async def _send_list(message, user_id, debt_type, lang, edit_from=None):
    debts = await db.get_active_debts(user_id, debt_type)
    text = views.debt_list_card(debts, debt_type, lang)
    markup = kb.debt_numbers_kb(debts, debt_type) if debts else None
    if edit_from:
        await _edit(edit_from, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_(i18n.all_variants("menu_owe_me")))
async def list_owe_me(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await _send_list(message, message.from_user.id, "lent", lang)


@router.message(F.text.in_(i18n.all_variants("menu_i_owe")))
async def list_i_owe(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await _send_list(message, message.from_user.id, "borrowed", lang)


@router.callback_query(F.data.startswith("list:"))
async def back_to_list(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt_type = callback.data.split(":", 1)[1]
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await _send_list(None, callback.from_user.id, debt_type, lang, edit_from=callback)


# ---------- one debt ----------

@router.callback_query(F.data.startswith("debt:"))
async def open_debt(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    _, debt_id, back_to = callback.data.split(":")
    lang = await db.get_user_lang(callback.from_user.id)
    debt = await db.get_debt(int(debt_id))
    if not debt or debt["user_id"] != callback.from_user.id:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await callback.answer()
    await _edit(callback, views.debt_card(debt, lang),
                kb.debt_card_kb(debt["id"], lang, back_to))


@router.callback_query(F.data.startswith("close:"))
async def close_debt(callback: types.CallbackQuery):
    debt_id = int(callback.data.split(":", 1)[1])
    lang = await db.get_user_lang(callback.from_user.id)
    ok = await db.mark_debt_paid(debt_id, callback.from_user.id)
    await callback.answer(i18n.get("debt_closed" if ok else "err_not_found", lang))
    if ok:
        debt = await db.get_debt(debt_id)
        await _send_list(None, callback.from_user.id, debt["debt_type"], lang, edit_from=callback)


@router.callback_query(F.data.startswith("del:"))
async def ask_delete(callback: types.CallbackQuery):
    debt_id = int(callback.data.split(":", 1)[1])
    lang = await db.get_user_lang(callback.from_user.id)
    debt = await db.get_debt(debt_id)
    if not debt or debt["user_id"] != callback.from_user.id:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await callback.answer()
    await _edit(callback, i18n.get("confirm_delete", lang),
                kb.confirm_delete_kb(debt_id, lang, debt["debt_type"]))


@router.callback_query(F.data.startswith("delyes:"))
async def do_delete(callback: types.CallbackQuery):
    debt_id = int(callback.data.split(":", 1)[1])
    lang = await db.get_user_lang(callback.from_user.id)
    debt = await db.get_debt(debt_id)
    debt_type = debt["debt_type"] if debt else "lent"
    await db.delete_debt(debt_id, callback.from_user.id)
    await callback.answer(i18n.get("debt_deleted", lang))
    await _send_list(None, callback.from_user.id, debt_type, lang, edit_from=callback)


# ---------- payments ----------

@router.callback_query(F.data.startswith("pay:"))
async def ask_payment(callback: types.CallbackQuery, state: FSMContext):
    debt_id = int(callback.data.split(":", 1)[1])
    lang = await db.get_user_lang(callback.from_user.id)
    debt = await db.get_debt(debt_id)
    if not debt or debt["user_id"] != callback.from_user.id:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return

    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    await state.set_state(Pay.entering_amount)
    await state.update_data(debt_id=debt_id, lang=lang)
    await callback.answer()
    await callback.message.answer(i18n.get(
        "ask_payment", lang,
        name=debt["person_name"],
        remaining=fmt_amount(remaining),
        currency=debt["currency"],
    ))


@router.message(Pay.entering_amount)
async def got_payment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    amount = parse_amount(message.text)
    if not amount:
        await message.answer(i18n.get("err_amount", lang))
        return

    debt = await db.get_debt(data["debt_id"])
    if not debt or debt["user_id"] != message.from_user.id:
        await message.answer(i18n.get("err_not_found", lang))
        await state.clear()
        return

    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    if amount > remaining + 0.001:
        await message.answer(i18n.get("err_too_large", lang,
                                      remaining=fmt_amount(remaining),
                                      currency=debt["currency"]))
        return

    ok, left = await db.update_debt_payment(data["debt_id"], message.from_user.id, amount)
    await state.clear()
    if not ok:
        await message.answer(i18n.get("err_not_found", lang))
        return

    if left <= 0:
        await message.answer(i18n.get("payment_full", lang))
    else:
        await message.answer(i18n.get("payment_partial", lang,
                                      remaining=fmt_amount(left),
                                      currency=debt["currency"]))
    await show_home(message, message.from_user.id, lang)
