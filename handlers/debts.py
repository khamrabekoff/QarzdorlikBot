"""Adding, listing, editing and settling debts.

The add flow used to be five separate questions (type, name, amount, currency,
date). It is three: type, "who and how much" on one line, and a date - with
the currency defaulting to the person's own choice or their last one. Fewer
steps means fewer chances to lose someone halfway.

Handler order matters: aiogram tries handlers in registration order, so the
main-menu buttons are registered FIRST. Otherwise pressing "📥 Menga qarzdor"
while the bot was waiting for a name saved a debt to someone called
"📥 Menga qarzdor".
"""
import logging

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import views
from handlers.common import edit_or_send, notify_counterparty, user_display
from keyboards import CURRENCIES
from states import AddDebt, Edit, Pay
from utils import (
    MAX_NOTE_LEN, clean_name, esc, fmt_amount, i18n, parse_amount, parse_date,
    resolve_date_choice, split_name_amount,
)

router = Router()
logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = CURRENCIES[0]


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _flip(debt_type):
    return "borrowed" if debt_type == "lent" else "lent"


# ---------- main menu (must stay first) ----------

@router.message(Command("new"))
@router.message(F.text.in_(i18n.all_variants("menu_add")))
async def start_add(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await state.update_data(lang=lang)
    await message.answer(i18n.get("add_choose_type", lang), reply_markup=kb.type_kb(lang))


@router.message(F.text.in_(i18n.all_variants("menu_owe_me")))
async def list_owe_me(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await _send_list(message.bot, message.from_user.id, "lent", lang)


@router.message(F.text.in_(i18n.all_variants("menu_i_owe")))
async def list_i_owe(message: types.Message, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(message.from_user.id)
    await _send_list(message.bot, message.from_user.id, "borrowed", lang)


# ---------- add ----------

@router.callback_query(F.data.startswith("type:"))
async def picked_type(callback: types.CallbackQuery, state: FSMContext):
    debt_type = "borrowed" if callback.data.endswith("borrowed") else "lent"
    lang = await db.get_user_lang(callback.from_user.id)
    names = await db.get_recent_names(callback.from_user.id)
    await state.update_data(debt_type=debt_type, lang=lang, recent_names=names)
    await state.set_state(AddDebt.entering_who_amount)
    await callback.answer()
    await edit_or_send(callback, i18n.get("add_who_amount", lang), kb.names_kb(names, lang))


@router.callback_query(F.data.startswith("name:"), AddDebt.entering_who_amount)
async def picked_name(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    names = data.get("recent_names", [])
    idx = _int(callback.data.split(":", 1)[1])
    if idx is None or not 0 <= idx < len(names):
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await state.update_data(person_name=names[idx])
    await state.set_state(AddDebt.entering_amount)
    await callback.answer()
    await edit_or_send(callback, i18n.get("add_ask_amount", lang, name=esc(names[idx])), kb.cancel_kb(lang))


@router.message(AddDebt.entering_who_amount, F.text)
async def got_who_amount(message: types.Message, state: FSMContext):
    """Accepts 'Ali 500000', 'Vali aka 1,5 mln', or just 'Ali'."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    name, amount = split_name_amount(message.text)
    if not name:
        await message.answer(i18n.get("err_name", lang), reply_markup=kb.cancel_kb(lang))
        return
    await state.update_data(person_name=name)
    if amount:
        await state.update_data(amount=amount)
        await _ask_date(message.bot, message.from_user.id, state, lang)
        return
    await state.set_state(AddDebt.entering_amount)
    await message.answer(i18n.get("add_ask_amount", lang, name=esc(name)), reply_markup=kb.cancel_kb(lang))


@router.message(AddDebt.entering_amount, F.text)
async def got_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    amount = parse_amount(message.text)
    if not amount:
        await message.answer(i18n.get("err_amount", lang), reply_markup=kb.cancel_kb(lang))
        return
    await state.update_data(amount=amount)
    await _ask_date(message.bot, message.from_user.id, state, lang)


async def _ask_date(bot: Bot, user_id, state: FSMContext, lang, callback=None):
    text = i18n.get("add_ask_date", lang) + "\n\n" + i18n.get("date_hint", lang)
    await state.set_state(AddDebt.entering_date)
    if callback:
        await edit_or_send(callback, text, kb.date_kb(lang))
    else:
        await bot.send_message(user_id, text, reply_markup=kb.date_kb(lang))


@router.callback_query(F.data.startswith("qtype:"), AddDebt.choosing_type)
async def picked_quick_type(callback: types.CallbackQuery, state: FSMContext):
    """Second half of typing 'Ali 500000' on the home screen."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.update_data(debt_type="borrowed" if callback.data.endswith("borrowed") else "lent")
    await callback.answer()
    await _ask_date(callback.bot, callback.from_user.id, state, lang, callback=callback)


@router.callback_query(F.data.startswith("date:"), AddDebt.entering_date)
async def picked_date(callback: types.CallbackQuery, state: FSMContext):
    iso = resolve_date_choice(callback.data.split(":", 1)[1])
    await callback.answer()
    await _show_confirm(callback.bot, callback.from_user.id, state, iso, callback=callback)


@router.message(AddDebt.entering_date, F.text)
async def typed_date(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    iso = parse_date(message.text)
    if not iso:
        await message.answer(i18n.get("err_date", lang), reply_markup=kb.date_kb(lang))
        return
    await _show_confirm(message.bot, message.from_user.id, state, iso)


def _payload(data):
    return {
        "person_name": data.get("person_name", "?"),
        "amount": data.get("amount", 0),
        "currency": data.get("currency") or DEFAULT_CURRENCY,
        "due_date": data.get("due_date"),
        "debt_type": data.get("debt_type", "lent"),
        "description": data.get("description"),
    }


async def _show_confirm(bot: Bot, user_id, state: FSMContext, iso_date, callback=None):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    currency = data.get("currency") or await db.get_default_currency(user_id, DEFAULT_CURRENCY)
    await state.update_data(due_date=iso_date, currency=currency)
    # The confirm card has its own buttons; leaving the date state would make
    # a stray typed message get lost, so typed text keeps meaning "a date".
    await state.set_state(AddDebt.entering_date)
    data.update(due_date=iso_date, currency=currency)
    await _render_confirm(bot, user_id, data, lang, callback)


async def _render_confirm(bot: Bot, user_id, data, lang, callback=None):
    text = views.confirm_card(_payload(data), lang)
    if callback:
        await edit_or_send(callback, text, kb.confirm_kb(lang))
    else:
        await bot.send_message(user_id, text, reply_markup=kb.confirm_kb(lang))


@router.callback_query(F.data == "pick_date")
async def change_date(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("debt_type"):
        await _stale(callback, state)
        return
    await callback.answer()
    await _ask_date(callback.bot, callback.from_user.id, state, data.get("lang", "uz"), callback=callback)


@router.callback_query(F.data == "pick_cur")
async def pick_currency(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("debt_type"):
        await _stale(callback, state)
        return
    await callback.answer()
    await edit_or_send(callback, i18n.get("choose_currency", data.get("lang", "uz")),
                       kb.currency_kb(data.get("lang", "uz")))


@router.callback_query(F.data.startswith("cur:"))
async def picked_currency(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("debt_type"):
        await _stale(callback, state)
        return
    idx = _int(callback.data.split(":", 1)[1])
    currency = CURRENCIES[idx] if idx is not None and 0 <= idx < len(CURRENCIES) else DEFAULT_CURRENCY
    await state.update_data(currency=currency)
    data["currency"] = currency
    await callback.answer()
    await _render_confirm(callback.bot, callback.from_user.id, data, data.get("lang", "uz"), callback)


@router.callback_query(F.data == "add_note")
async def ask_note(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("debt_type"):
        await _stale(callback, state)
        return
    lang = data.get("lang", "uz")
    await state.set_state(AddDebt.entering_note)
    await callback.answer()
    await edit_or_send(callback, i18n.get("add_ask_note", lang), kb.cancel_kb(lang))


@router.message(AddDebt.entering_note, F.text)
async def got_note(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    note = " ".join(message.text.split())
    if len(note) > MAX_NOTE_LEN:
        await message.answer(i18n.get("err_note", lang), reply_markup=kb.cancel_kb(lang))
        return
    await state.update_data(description=note)
    await state.set_state(AddDebt.entering_date)
    data["description"] = note
    await _render_confirm(message.bot, message.from_user.id, data, lang)


@router.callback_query(F.data == "save")
async def save_debt(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang") or await db.get_user_lang(callback.from_user.id)
    # due_date is intentionally absent: an open-ended debt stores NULL.
    if not all(data.get(k) for k in ("debt_type", "person_name", "amount")):
        # Usually a second tap on an already-saved card.
        await _stale(callback, state)
        return

    payload = _payload(data)
    debt_id = await db.add_debt(
        user_id=callback.from_user.id,
        debt_type=payload["debt_type"],
        amount=payload["amount"],
        currency=payload["currency"],
        person_name=payload["person_name"],
        due_date=payload["due_date"],
        description=payload["description"] or "",
    )
    await state.clear()
    await callback.answer()
    await edit_or_send(callback, views.saved_card(payload, lang),
                       kb.saved_kb(debt_id, payload["person_name"], payload["debt_type"], lang))


async def _stale(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer(i18n.get("stale_button", lang), show_alert=True)


# ---------- lists ----------

async def _send_list(bot: Bot, user_id, debt_type, lang, callback=None):
    debts = await db.get_active_debts(user_id, debt_type)
    text = views.debt_list_card(debts, debt_type, lang, max_items=kb.MAX_LIST)
    markup = kb.debt_numbers_kb(debts, debt_type) if debts else None
    if callback:
        await edit_or_send(callback, text, markup)
    else:
        await bot.send_message(user_id, text, reply_markup=markup)


@router.callback_query(F.data.startswith("list:"))
async def back_to_list(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt_type = "borrowed" if callback.data.endswith("borrowed") else "lent"
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer()
    await _send_list(callback.bot, callback.from_user.id, debt_type, lang, callback=callback)


# ---------- one debt ----------

async def _card(debt, uid, lang):
    """(text, markup) for a debt as `uid` sees it, or None if they can't."""
    if not debt:
        return None
    is_mine = debt["user_id"] == uid
    is_counterparty = debt["counterparty_id"] == uid and debt["share_status"] == "accepted"
    if not (is_mine or is_counterparty):
        return None

    view = dict(debt)
    view["view_type"] = debt["debt_type"] if is_mine else _flip(debt["debt_type"])
    view["is_mine"] = 1 if is_mine else 0
    owner_name = None if is_mine else await user_display(debt["user_id"])

    if debt["status"] != "active":
        return views.closed_card(debt, lang), kb.row_kb(
            (i18n.get("btn_to_list", lang), f"list:{view['view_type']}"))

    markup = kb.debt_card_kb(debt["id"], lang, view["view_type"], is_mine=is_mine,
                             shareable=is_mine and debt["share_status"] != "accepted")
    return views.debt_card(view, lang, owner_name=owner_name), markup


async def _show_card(callback: types.CallbackQuery, debt_id, lang, new_message=False):
    card = await _card(await db.get_debt(debt_id), callback.from_user.id, lang)
    if not card:
        await callback.bot.send_message(callback.from_user.id, i18n.get("err_not_found", lang))
        return
    if new_message:
        await callback.bot.send_message(callback.from_user.id, card[0], reply_markup=card[1])
    else:
        await edit_or_send(callback, *card)


@router.callback_query(F.data.startswith("debt:"))
async def open_debt(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    lang = await db.get_user_lang(callback.from_user.id)
    debt_id = _int(callback.data.split(":")[1]) if callback.data.count(":") >= 1 else None
    card = await _card(await db.get_debt(debt_id) if debt_id else None, callback.from_user.id, lang)
    if not card:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await callback.answer()
    await edit_or_send(callback, *card)


async def _own_active_debt(callback: types.CallbackQuery, state: FSMContext = None):
    """The debt named in the callback, if the tapper owns it and it's open.
    Answers the callback with an error otherwise."""
    lang = await db.get_user_lang(callback.from_user.id)
    debt_id = _int(callback.data.split(":")[1])
    debt = await db.get_debt(debt_id) if debt_id else None
    if not debt or debt["user_id"] != callback.from_user.id:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return None, lang
    if debt["status"] != "active":
        await callback.answer(i18n.get("debt_closed", lang), show_alert=True)
        return None, lang
    return debt, lang


# ---------- close / reopen / delete ----------

async def _close(callback: types.CallbackQuery, debt, lang):
    prev_paid = debt["paid_amount"] or 0
    if not await db.mark_debt_paid(debt["id"], callback.from_user.id):
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await callback.answer(i18n.get("debt_closed", lang))
    await edit_or_send(callback, views.closed_card(debt, lang),
                       kb.closed_kb(debt["id"], prev_paid, debt["debt_type"], lang))
    await notify_counterparty(callback.bot, debt, "notify_closed")


@router.callback_query(F.data.startswith("close:"))
async def close_debt(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt, lang = await _own_active_debt(callback)
    if debt:
        await _close(callback, debt, lang)


@router.callback_query(F.data.startswith("reopen:"))
async def reopen(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    lang = await db.get_user_lang(callback.from_user.id)
    debt_id = _int(parts[1])
    try:
        prev_paid = float(parts[2]) if len(parts) > 2 else 0.0
    except ValueError:
        prev_paid = 0.0
    if not debt_id or not await db.reopen_debt(debt_id, callback.from_user.id, prev_paid):
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await callback.answer(i18n.get("debt_reopened", lang))
    await _show_card(callback, debt_id, lang)
    await notify_counterparty(callback.bot, await db.get_debt(debt_id), "notify_reopened")


@router.callback_query(F.data.startswith("del:"))
async def ask_delete(callback: types.CallbackQuery):
    debt, lang = await _own_active_debt(callback)
    if not debt:
        return
    await callback.answer()
    await edit_or_send(callback, i18n.get("confirm_delete", lang),
                       kb.confirm_delete_kb(debt["id"], lang, debt["debt_type"]))


@router.callback_query(F.data.startswith("delyes:"))
async def do_delete(callback: types.CallbackQuery):
    lang = await db.get_user_lang(callback.from_user.id)
    debt_id = _int(callback.data.split(":")[1])
    debt = await db.get_debt(debt_id) if debt_id else None
    if not debt or not await db.delete_debt(debt_id, callback.from_user.id):
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return
    await callback.answer(i18n.get("debt_deleted", lang))
    await _send_list(callback.bot, callback.from_user.id, debt["debt_type"], lang, callback=callback)
    await notify_counterparty(callback.bot, debt, "notify_deleted")


# ---------- payments ----------

@router.callback_query(F.data.startswith("pay:"))
async def ask_payment(callback: types.CallbackQuery, state: FSMContext):
    debt, lang = await _own_active_debt(callback)
    if not debt:
        return
    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    await state.set_state(Pay.entering_amount)
    await state.update_data(debt_id=debt["id"], lang=lang)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        i18n.get("ask_payment", lang, name=esc(debt["person_name"]),
                 remaining=fmt_amount(remaining), currency=esc(debt["currency"])),
        reply_markup=kb.pay_kb(debt["id"], remaining, lang),
    )


@router.callback_query(F.data.startswith("payall:"))
async def pay_all(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt, lang = await _own_active_debt(callback)
    if debt:
        await _close(callback, debt, lang)


@router.callback_query(F.data.startswith("payhalf:"))
async def pay_half(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt, lang = await _own_active_debt(callback)
    if not debt:
        return
    half = round(((debt["amount"] or 0) - (debt["paid_amount"] or 0)) / 2, 2)
    await callback.answer()
    await _apply_payment(callback.bot, callback.from_user.id, debt, half, lang, callback=callback)


@router.message(Pay.entering_amount, F.text)
async def got_payment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    amount = parse_amount(message.text)
    if not amount:
        await message.answer(i18n.get("err_amount", lang), reply_markup=kb.cancel_kb(lang))
        return

    debt = await db.get_debt(data.get("debt_id") or 0)
    if not debt or debt["user_id"] != message.from_user.id or debt["status"] != "active":
        await message.answer(i18n.get("err_not_found", lang))
        await state.clear()
        return

    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    if amount > remaining + 0.001:
        await message.answer(i18n.get("err_too_large", lang, remaining=fmt_amount(remaining),
                                      currency=esc(debt["currency"])),
                             reply_markup=kb.cancel_kb(lang))
        return
    await state.clear()
    await _apply_payment(message.bot, message.from_user.id, debt, amount, lang)


async def _apply_payment(bot: Bot, uid, debt, amount, lang, callback=None):
    ok, left = await db.update_debt_payment(debt["id"], uid, amount)
    if not ok:
        await bot.send_message(uid, i18n.get("err_not_found", lang))
        return

    if left <= 0:
        text = i18n.get("payment_full", lang) + "\n\n" + views.closed_card(debt, lang)
        markup = kb.closed_kb(debt["id"], debt["paid_amount"] or 0, debt["debt_type"], lang)
        await notify_counterparty(bot, debt, "notify_closed")
    else:
        text = i18n.get("payment_partial", lang, remaining=fmt_amount(left), currency=esc(debt["currency"]))
        markup = kb.row_kb(
            (i18n.get("btn_open", lang), f"debt:{debt['id']}:{debt['debt_type']}"),
            (i18n.get("btn_to_list", lang), f"list:{debt['debt_type']}"),
        )
        await notify_counterparty(bot, debt, "notify_payment", paid=fmt_amount(amount),
                                  remaining=fmt_amount(left))

    if callback:
        await edit_or_send(callback, text, markup)
    else:
        await bot.send_message(uid, text, reply_markup=markup)


# ---------- edit ----------

@router.callback_query(F.data.startswith("edit:"))
async def edit_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt, lang = await _own_active_debt(callback)
    if not debt:
        return
    await callback.answer()
    await edit_or_send(callback, i18n.get("edit_title", lang),
                       kb.edit_kb(debt["id"], lang, debt["debt_type"]))


@router.callback_query(F.data.startswith("ef:"))
async def edit_field(callback: types.CallbackQuery, state: FSMContext):
    """Also the reminder's 'move the deadline' button (field 'due')."""
    debt, lang = await _own_active_debt(callback)
    if not debt:
        return
    field = callback.data.split(":")[2] if callback.data.count(":") >= 2 else ""
    if field not in ("name", "amount", "due", "note"):
        await _stale(callback, state)
        return

    await state.set_state(Edit.value)
    await state.update_data(debt_id=debt["id"], field=field, lang=lang)
    await callback.answer()

    if field == "due":
        await edit_or_send(callback, i18n.get("edit_ask_date", lang),
                           kb.date_kb(lang, prefix=f"rd:{debt['id']}",
                                      back=f"debt:{debt['id']}:{debt['debt_type']}"))
        return

    prompt = {
        "name": i18n.get("edit_ask_name", lang),
        "amount": i18n.get("edit_ask_amount", lang, amount=fmt_amount(debt["amount"]),
                           currency=esc(debt["currency"])),
        "note": i18n.get("edit_ask_note", lang),
    }[field]
    await edit_or_send(callback, prompt, kb.cancel_kb(lang))


@router.callback_query(F.data.startswith("rd:"))
async def picked_new_date(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    debt, lang = await _own_active_debt(callback)
    if not debt:
        return
    code = callback.data.split(":")[2] if callback.data.count(":") >= 2 else "w1"
    await _save_field(callback.bot, callback.from_user.id, debt, "due_date",
                      resolve_date_choice(code), lang, callback=callback)


@router.message(Edit.value, F.text)
async def got_edit_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    field = data.get("field")
    debt = await db.get_debt(data.get("debt_id") or 0)
    if not debt or debt["user_id"] != message.from_user.id or debt["status"] != "active":
        await state.clear()
        await message.answer(i18n.get("err_not_found", lang))
        return

    text = message.text.strip()
    if field == "name":
        value = clean_name(text)
        if not value:
            await message.answer(i18n.get("err_name", lang), reply_markup=kb.cancel_kb(lang))
            return
        column = "person_name"
    elif field == "amount":
        value = parse_amount(text)
        if not value:
            await message.answer(i18n.get("err_amount", lang), reply_markup=kb.cancel_kb(lang))
            return
        if value <= (debt["paid_amount"] or 0):
            await message.answer(i18n.get("err_amount_below_paid", lang,
                                          paid=fmt_amount(debt["paid_amount"]),
                                          currency=esc(debt["currency"])),
                                 reply_markup=kb.cancel_kb(lang))
            return
        column = "amount"
    elif field == "note":
        value = "" if text == "-" else " ".join(text.split())
        if len(value) > MAX_NOTE_LEN:
            await message.answer(i18n.get("err_note", lang), reply_markup=kb.cancel_kb(lang))
            return
        column = "description"
    elif field == "due":
        value = parse_date(text)
        if not value:
            await message.answer(i18n.get("err_date", lang))
            return
        column = "due_date"
    else:
        await state.clear()
        return

    await state.clear()
    await _save_field(message.bot, message.from_user.id, debt, column, value, lang)


async def _save_field(bot: Bot, uid, debt, column, value, lang, callback=None):
    if not await db.update_debt_field(debt["id"], uid, column, value):
        await bot.send_message(uid, i18n.get("err_not_found", lang))
        return
    updated = await db.get_debt(debt["id"])
    text, markup = await _card(updated, uid, lang)
    text = i18n.get("edit_saved", lang) + "\n\n" + text
    if callback:
        await edit_or_send(callback, text, markup)
    else:
        await bot.send_message(uid, text, reply_markup=markup)
    if column in ("amount", "due_date"):
        await notify_counterparty(bot, updated, "notify_changed")
