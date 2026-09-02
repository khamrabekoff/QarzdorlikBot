"""Keyboard builders.

The bottom (reply) keyboard is only the main menu - always visible, nothing to
hunt for. Everything inside a flow uses inline buttons on the message itself,
so a conversation edits one card instead of pushing a wall of new messages.
"""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

from utils import i18n

CURRENCIES = ["so'm", "$", "€", "₽"]

DATE_CHOICES = [
    ("btn_days_3", "date:d3"),
    ("btn_week_1", "date:w1"),
    ("btn_days_10", "date:d10"),
    ("btn_month_1", "date:m1"),
    ("btn_end_week", "date:ew"),
    ("btn_end_month", "date:em"),
]

# Kept out of DATE_CHOICES so it gets its own full-width row - it is a
# different kind of answer, not another preset offset.
NO_DEADLINE = ("btn_no_deadline", "date:none")


def main_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.get("menu_add", lang))],
            [
                KeyboardButton(text=i18n.get("menu_owe_me", lang)),
                KeyboardButton(text=i18n.get("menu_i_owe", lang)),
            ],
            [KeyboardButton(text=i18n.get("menu_settings", lang))],
        ],
        resize_keyboard=True,
    )


def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
    ]])


def phone_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=i18n.get("btn_share_phone", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def type_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.get("btn_lent", lang), callback_data="type:lent")],
        [InlineKeyboardButton(text=i18n.get("btn_borrowed", lang), callback_data="type:borrowed")],
    ])


def names_kb(names, lang):
    """Recent counterparties as shortcuts. Indexes, not names, go in the
    callback data - names are user input and can blow past Telegram's 64-byte
    callback limit or contain anything at all."""
    rows = []
    for i in range(0, len(names), 2):
        rows.append([
            InlineKeyboardButton(text=n, callback_data=f"name:{i + j}")
            for j, n in enumerate(names[i:i + 2])
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def date_kb(lang):
    rows, row = [], []
    for key, data in DATE_CHOICES:
        row.append(InlineKeyboardButton(text=i18n.get(key, lang), callback_data=data))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    key, data = NO_DEADLINE
    rows.append([InlineKeyboardButton(text=i18n.get(key, lang), callback_data=data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def currency_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=c, callback_data=f"cur:{i}")
        for i, c in enumerate(CURRENCIES)
    ]])


def confirm_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.get("btn_save", lang), callback_data="save")],
        [InlineKeyboardButton(text=i18n.get("btn_change_currency", lang), callback_data="pick_cur")],
    ])


def debt_numbers_kb(debts, back_to):
    """One numbered button per debt, so a long list stays a single message."""
    rows, row = [], []
    for idx, d in enumerate(debts, 1):
        row.append(InlineKeyboardButton(text=str(idx), callback_data=f"debt:{d['id']}:{back_to}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def debt_card_kb(debt_id, lang, back_to, is_mine=True, shareable=False):
    """Only the owner gets editing buttons; the other party sees it read-only.
    Keeping edits one-sided avoids two people silently overwriting each other."""
    rows = []
    if is_mine:
        rows.append([
            InlineKeyboardButton(text=i18n.get("btn_pay", lang), callback_data=f"pay:{debt_id}"),
            InlineKeyboardButton(text=i18n.get("btn_close", lang), callback_data=f"close:{debt_id}"),
        ])
        if shareable:
            rows.append([
                InlineKeyboardButton(text=i18n.get("btn_share", lang), callback_data=f"share:{debt_id}")
            ])
        rows.append([
            InlineKeyboardButton(text=i18n.get("btn_delete", lang), callback_data=f"del:{debt_id}"),
            InlineKeyboardButton(text=i18n.get("btn_back", lang), callback_data=f"list:{back_to}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text=i18n.get("btn_back", lang), callback_data=f"list:{back_to}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_confirm_kb(token, lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=i18n.get("btn_confirm_share", lang), callback_data=f"shyes:{token}"),
        InlineKeyboardButton(text=i18n.get("btn_decline_share", lang), callback_data=f"shno:{token}"),
    ]])


def confirm_delete_kb(debt_id, lang, back_to):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=i18n.get("btn_yes_delete", lang), callback_data=f"delyes:{debt_id}"),
        InlineKeyboardButton(text=i18n.get("btn_no", lang), callback_data=f"debt:{debt_id}:{back_to}"),
    ]])


def settings_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.get("btn_change_lang", lang), callback_data="pick_lang")],
        [InlineKeyboardButton(text=i18n.get("btn_history", lang), callback_data="history")],
        [InlineKeyboardButton(text=i18n.get("btn_invite", lang), callback_data="invite")],
    ])
