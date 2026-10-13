"""Keyboard builders.

The bottom (reply) keyboard is only the main menu - always visible, nothing to
hunt for. Everything inside a flow uses inline buttons on the message itself,
so a conversation edits one card instead of pushing a wall of new messages.

Every step of a flow carries a way out (cancel or back). Without one, a person
who changed their mind had no visible exit and the next thing they typed was
swallowed as an answer to the abandoned question.
"""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

from utils import fmt_amount, i18n
from views import signed_money

CURRENCIES = ["so'm", "$", "€", "₽"]

DATE_CHOICES = [
    ("btn_tomorrow", "t1"),
    ("btn_days_3", "d3"),
    ("btn_week_1", "w1"),
    ("btn_days_10", "d10"),
    ("btn_month_1", "m1"),
    ("btn_end_month", "em"),
]

# Kept out of DATE_CHOICES so it gets its own full-width row - it is a
# different kind of answer, not another preset offset.
NO_DEADLINE = ("btn_no_deadline", "none")

# Telegram caps a message at 4096 characters; a list longer than this is
# cut and the rest is reachable through search.
MAX_LIST = 30


def _btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def _rows(buttons, per_row):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def main_kb(lang):
    """Two buttons. Everything else lives on the home message itself, which
    is edited in place as you navigate - no wall of new messages."""
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text=i18n.get("menu_add", lang)),
            KeyboardButton(text=i18n.get("menu_home", lang)),
        ]],
        resize_keyboard=True,
        input_field_placeholder="Ali 500000",
    )


PEOPLE_PER_PAGE = 8
FILTERS = ("all", "in", "out", "late")


def home_kb(people, flt, page, counts, lang):
    """Filter chips, one button per person, paging, then actions.
    `counts` = {'in': n, 'out': n, 'late': n} people per filter."""
    labels = {
        "all": i18n.get("filter_all", lang),
        "in": f"📥 {counts.get('in', 0)}",
        "out": f"📤 {counts.get('out', 0)}",
        "late": f"⚠️ {counts.get('late', 0)}",
    }
    rows = [[_btn(("• " if f == flt else "") + labels[f], f"h:{f}:0")
             for f in FILTERS if f == "all" or counts.get(f)]]
    if len(rows[0]) == 1:
        rows = []  # one chip filters nothing

    pages = max(1, -(-len(people) // PEOPLE_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    for p in people[page * PEOPLE_PER_PAGE:(page + 1) * PEOPLE_PER_PAGE]:
        dot = "🔴 " if p["overdue"] else ""
        rows.append([_btn(f"{dot}{p['name']} · {signed_money(p['net'])}", f"p:{p['debts'][0]['id']}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◂", f"h:{flt}:{page - 1}"))
        nav.append(_btn(f"{page + 1}/{pages}", f"h:{flt}:{page}"))
        if page < pages - 1:
            nav.append(_btn("▸", f"h:{flt}:{page + 1}"))
        rows.append(nav)

    rows.append([
        _btn(i18n.get("menu_add", lang), "add"),
        _btn("⚙️", "settings"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def person_kb(person, lang):
    debts = person["debts"][:MAX_LIST]
    rows = _rows([_btn(str(i), f"debt:{d['id']}:{_view_type(d)}") for i, d in enumerate(debts, 1)], 5)
    first = person["debts"][0]
    if _is_mine(first):  # can only add to people you track yourself
        rows.append([_btn(i18n.get("btn_add_more", lang), f"padd:{first['id']}")])
    rows.append([_btn(i18n.get("btn_back", lang), "h:all:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_mine(d):
    try:
        return bool(d["is_mine"])
    except (KeyError, IndexError):
        return True


def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn("🇺🇿 O'zbekcha", "lang:uz"),
        _btn("🇷🇺 Русский", "lang:ru"),
    ]])


def row_kb(*buttons):
    """A one-row keyboard from (text, callback_data) pairs."""
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t, d) for t, d in buttons]])


def cancel_row(lang):
    return [_btn(i18n.get("btn_cancel", lang), "cancel")]


def cancel_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[cancel_row(lang)])


def type_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(i18n.get("btn_lent", lang), "type:lent")],
        [_btn(i18n.get("btn_borrowed", lang), "type:borrowed")],
        cancel_row(lang),
    ])


def quick_type_kb(name, lang):
    """After someone types 'Ali 500000' out of the blue: which way round?"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(i18n.get("btn_quick_lent", lang, name=name), "qtype:lent")],
        [_btn(i18n.get("btn_quick_borrowed", lang, name=name), "qtype:borrowed")],
        cancel_row(lang),
    ])


def names_kb(names, lang):
    """Recent counterparties as shortcuts. Indexes, not names, go in the
    callback data - names are user input and can blow past Telegram's 64-byte
    callback limit or contain anything at all."""
    rows = _rows([_btn(n, f"name:{i}") for i, n in enumerate(names)], 2)
    rows.append(cancel_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def date_kb(lang, prefix="date", back=None):
    """Deadline presets. `prefix` routes the answer: 'date' while adding a
    debt, 'rd:<id>' when moving an existing one. `back` is the callback for
    the bottom button (cancel while adding, back to the card when editing)."""
    rows = _rows([_btn(i18n.get(key, lang), f"{prefix}:{code}") for key, code in DATE_CHOICES], 2)
    key, code = NO_DEADLINE
    rows.append([_btn(i18n.get(key, lang), f"{prefix}:{code}")])
    if back:
        rows.append([_btn(i18n.get("btn_back", lang), back)])
    else:
        rows.append(cancel_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def currency_kb(lang, prefix="cur"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(c, f"{prefix}:{i}") for i, c in enumerate(CURRENCIES)]
    ])


def confirm_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(i18n.get("btn_save", lang), "save")],
        [
            _btn(i18n.get("btn_change_date", lang), "pick_date"),
            _btn(i18n.get("btn_change_currency", lang), "pick_cur"),
        ],
        [
            _btn(i18n.get("btn_add_note", lang), "add_note"),
            _btn(i18n.get("btn_cancel", lang), "cancel"),
        ],
    ])


def saved_kb(debt_id, name, debt_type, lang):
    """Right after saving is the moment the other person is on their mind -
    the best time to offer sending it to them."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(i18n.get("btn_share_to", lang, name=name), f"share:{debt_id}")],
        [_btn(i18n.get("btn_open", lang), f"debt:{debt_id}:{debt_type}")],
    ])


def debt_numbers_kb(debts, back_to=None):
    """One numbered button per debt, so a long list stays a single message.
    In mixed lists (search results) each debt returns to its own list."""
    buttons = []
    for idx, d in enumerate(debts[:MAX_LIST], 1):
        target = back_to or _view_type(d)
        buttons.append(_btn(str(idx), f"debt:{d['id']}:{target}"))
    return InlineKeyboardMarkup(inline_keyboard=_rows(buttons, 5))


def _view_type(d):
    try:
        return d["view_type"]
    except (KeyError, IndexError):
        return d["debt_type"]


def debt_card_kb(debt_id, lang, back_to, is_mine=True, shareable=False):
    """Only the owner gets editing buttons; the other party sees it read-only.
    Keeping edits one-sided avoids two people silently overwriting each other.
    Back goes to the person (or home, if this is their only debt)."""
    back = _btn(i18n.get("btn_back", lang), f"pp:{debt_id}")
    if not is_mine:
        return InlineKeyboardMarkup(inline_keyboard=[[back]])

    rows = [
        [
            _btn(i18n.get("btn_pay", lang), f"pay:{debt_id}"),
            _btn(i18n.get("btn_close", lang), f"close:{debt_id}"),
        ],
        [_btn(i18n.get("btn_edit", lang), f"edit:{debt_id}")],
    ]
    if shareable:
        rows[1].append(_btn(i18n.get("btn_share", lang), f"share:{debt_id}"))
    rows.append([_btn(i18n.get("btn_delete", lang), f"del:{debt_id}"), back])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_kb(debt_id, lang, back_to):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(i18n.get("btn_edit_name", lang), f"ef:{debt_id}:name"),
            _btn(i18n.get("btn_edit_amount", lang), f"ef:{debt_id}:amount"),
        ],
        [
            _btn(i18n.get("btn_edit_date", lang), f"ef:{debt_id}:due"),
            _btn(i18n.get("btn_edit_note", lang), f"ef:{debt_id}:note"),
        ],
        [_btn(i18n.get("btn_back", lang), f"debt:{debt_id}:{back_to}")],
    ])


def pay_kb(debt_id, remaining, lang):
    """Most payments are 'all of it' or 'half' - one tap instead of typing."""
    rows = [[_btn(i18n.get("btn_pay_full", lang, amount=fmt_amount(remaining)), f"payall:{debt_id}")]]
    half = remaining / 2
    if half >= 1:
        rows.append([_btn(i18n.get("btn_pay_half", lang, amount=fmt_amount(half)), f"payhalf:{debt_id}")])
    rows.append(cancel_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def closed_kb(debt_id, prev_paid, back_to, lang):
    """Closing by accident is easy and used to be permanent - offer an undo.
    The earlier partial payments ride along so the undo restores them."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(i18n.get("btn_reopen", lang), f"reopen:{debt_id}:{fmt_paid(prev_paid)}"),
        _btn(i18n.get("btn_to_list", lang), f"list:{back_to}"),
    ]])


def fmt_paid(value):
    """Compact, parseable number for callback data (no spaces, no exponent)."""
    value = float(value or 0)
    return str(int(value)) if value == int(value) else f"{value:.2f}"


def reminder_kb(debt_id, view_type, lang, is_owner=True):
    """Act on a reminder right where it lands, without digging for the debt."""
    open_btn = _btn(i18n.get("btn_open", lang), f"debt:{debt_id}:{view_type}")
    if not is_owner:
        return InlineKeyboardMarkup(inline_keyboard=[[open_btn]])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(i18n.get("btn_paid", lang), f"close:{debt_id}"),
            _btn(i18n.get("btn_partial", lang), f"pay:{debt_id}"),
        ],
        [
            _btn(i18n.get("btn_postpone", lang), f"ef:{debt_id}:due"),
            open_btn,
        ],
    ])


def share_confirm_kb(token, lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(i18n.get("btn_confirm_share", lang), f"shyes:{token}"),
        _btn(i18n.get("btn_decline_share", lang), f"shno:{token}"),
    ]])


def confirm_delete_kb(debt_id, lang, back_to):
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(i18n.get("btn_yes_delete", lang), f"delyes:{debt_id}"),
        _btn(i18n.get("btn_no", lang), f"debt:{debt_id}:{back_to}"),
    ]])


def settings_kb(lang, currency):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(i18n.get("btn_change_lang", lang), "pick_lang")],
        [_btn(i18n.get("btn_currency", lang, cur=currency), "pick_defcur")],
        [
            _btn(i18n.get("btn_history", lang), "history"),
            _btn(i18n.get("btn_export", lang), "export"),
        ],
        [
            _btn(i18n.get("btn_invite", lang), "invite"),
            _btn(i18n.get("btn_help", lang), "help"),
        ],
        [_btn(i18n.get("btn_home", lang), "h:all:0")],
    ])


def back_to_settings_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(i18n.get("btn_back", lang), "settings")]])


def with_home_button(markup, lang):
    rows = list(markup.inline_keyboard) if markup else []
    rows.append([_btn(i18n.get("btn_home", lang), "h:all:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
