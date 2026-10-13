"""Card renderers - every user-facing block of text is built here.

Anything a person typed (names, notes) or Telegram reports about them (first
names) goes through esc(): messages are sent as HTML, and one stray '<' made
Telegram reject the whole message.
"""
from utils import days_until, display_name, esc, fmt_amount, fmt_date, fmt_ts, i18n

RULE = "━" * 18


def due_phrase(iso_date, lang):
    """'осталось 5 дн.' / 'просрочено на 3 дн.' / 'сегодня' / 'без срока'."""
    if not iso_date:
        return i18n.get("no_deadline", lang)
    n = days_until(iso_date)
    if n is None:
        return fmt_date(iso_date)
    if n == 0:
        return i18n.get("due_today", lang)
    if n == 1:
        return i18n.get("due_tomorrow", lang)
    if n > 0:
        return i18n.get("days_left", lang, n=n)
    return i18n.get("days_overdue", lang, n=abs(n))


def _when(iso_date, lang):
    """Date plus its human phrase, or just the phrase when open-ended."""
    phrase = due_phrase(iso_date, lang)
    return f"{fmt_date(iso_date)} — <i>{phrase}</i>" if iso_date else f"<i>{phrase}</i>"


def _has(row, field):
    """Rows from the shared-view query carry extra columns; plain lookups don't."""
    try:
        return field in row.keys()
    except AttributeError:
        return field in row


def _get(row, field, default=None):
    return row[field] if _has(row, field) else default


def counterpart_name(debt):
    """Who the debt is with, from the reader's side. The owner wrote the OTHER
    party's name into person_name; showing that back to the other party reads
    as 'Ali - you owe', which is nonsense from their side."""
    if _get(debt, "is_mine", 1):
        return debt["person_name"]
    return display_name(_get(debt, "owner_username"), _get(debt, "owner_first_name"))


def _money(amount, currency):
    return f"{fmt_amount(amount)} {esc(currency)}"


def _totals_line(rows):
    parts = [_money(r["remaining"], r["currency"]) for r in rows if r["remaining"]]
    return " · ".join(parts) if parts else "0"


def _net_line(lent, borrowed):
    """Per currency: owed to me minus owed by me. Only shown when both sides
    exist - with one side the balance just repeats the line above it."""
    net = {}
    for r in lent:
        net[r["currency"]] = net.get(r["currency"], 0) + (r["remaining"] or 0)
    for r in borrowed:
        net[r["currency"]] = net.get(r["currency"], 0) - (r["remaining"] or 0)
    parts = []
    for cur, v in net.items():
        sign = "+" if v > 0 else ("−" if v < 0 else "")
        parts.append(f"{sign}{_money(abs(v), cur)}")
    return " · ".join(parts)


def home_card(totals_rows, overdue_count, soon_count, lang):
    """Summary the user lands on: what they're owed, what they owe, warnings."""
    lent = [r for r in totals_rows if r["debt_type"] == "lent"]
    borrowed = [r for r in totals_rows if r["debt_type"] == "borrowed"]

    text = i18n.get("home_title", lang) + "\n" + RULE + "\n\n"

    if not lent and not borrowed:
        return text + i18n.get("home_empty", lang)

    if lent:
        text += f"{i18n.get('home_owe_me', lang)}\n<b>{_totals_line(lent)}</b>\n\n"
    if borrowed:
        text += f"{i18n.get('home_i_owe', lang)}\n<b>{_totals_line(borrowed)}</b>\n\n"
    if lent and borrowed:
        text += i18n.get("home_net", lang, totals=_net_line(lent, borrowed)) + "\n\n"

    if overdue_count:
        text += i18n.get("home_overdue", lang, n=overdue_count) + "\n"
    elif soon_count:
        text += i18n.get("home_soon", lang, n=soon_count) + "\n"

    return text.rstrip()


def _list_lines(debts, lang, max_items):
    text, totals = "", {}
    for d in debts:
        remaining = (d["amount"] or 0) - (d["paid_amount"] or 0)
        totals[d["currency"]] = totals.get(d["currency"], 0) + remaining

    for idx, d in enumerate(debts[:max_items], 1):
        remaining = (d["amount"] or 0) - (d["paid_amount"] or 0)
        overdue = (days_until(d["due_date"]) or 0) < 0
        marker = "⚠️" if overdue else "▫️"
        if _get(d, "share_status") == "accepted":
            marker = "🤝"  # confirmed by both sides
        when = due_phrase(d["due_date"], lang)
        if d["due_date"]:
            when = f"{fmt_date(d['due_date'])} — {when}"
        text += (
            f"{marker} <b>{idx}. {esc(counterpart_name(d))}</b>\n"
            f"     {_money(remaining, d['currency'])}\n"
            f"     <i>{when}</i>\n"
        )
        if d["paid_amount"]:
            text += "     <i>" + i18n.get("card_paid", lang, paid=fmt_amount(d["paid_amount"]),
                                         currency=esc(d["currency"])) + "</i>\n"
        if _get(d, "description"):
            text += f"     📝 <i>{esc(d['description'])}</i>\n"
        text += "\n"

    if len(debts) > max_items:
        text += i18n.get("list_more", lang, n=len(debts) - max_items) + "\n\n"
    return text, totals


def debt_list_card(debts, debt_type, lang, max_items=30):
    """The whole list in ONE message. The old version sent a separate message
    per debt, which turned twenty debts into twenty-one notifications."""
    header = i18n.get("list_owe_me" if debt_type == "lent" else "list_i_owe", lang)
    if not debts:
        return f"{header}\n\n{i18n.get('list_empty', lang)}"

    body, totals = _list_lines(debts, lang, max_items)
    totals_str = " · ".join(_money(v, c) for c, v in totals.items())
    return (
        f"{header}  ({len(debts)})\n{RULE}\n\n{body}{RULE}\n"
        + i18n.get("list_total", lang, totals=totals_str)
        + "\n\n" + i18n.get("list_pick", lang)
    )


def search_card(debts, query, lang, max_items=30):
    """Search mixes both directions, so each entry says which way it goes."""
    header = i18n.get("search_title", lang, query=esc(query))
    lines = ""
    for idx, d in enumerate(debts[:max_items], 1):
        remaining = (d["amount"] or 0) - (d["paid_amount"] or 0)
        direction = i18n.get("dir_lent" if d["view_type"] == "lent" else "dir_borrowed", lang)
        lines += (
            f"{'📥' if d['view_type'] == 'lent' else '📤'} <b>{idx}. {esc(counterpart_name(d))}</b>\n"
            f"     {direction}: {_money(remaining, d['currency'])}\n"
            f"     <i>{_when(d['due_date'], lang)}</i>\n\n"
        )
    return f"{header}\n{RULE}\n\n{lines}" + i18n.get("list_pick", lang)


def debt_card(debt, lang, owner_name=None):
    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    # For a shared debt the reader may be the counterparty, for whom the
    # direction is reversed; view_type carries that already-flipped value.
    dtype = _get(debt, "view_type", debt["debt_type"])
    is_mine = _get(debt, "is_mine", 1)
    direction = i18n.get("dir_lent" if dtype == "lent" else "dir_borrowed", lang)

    badge = ""
    if _get(debt, "share_status") == "accepted":
        badge = "  " + i18n.get("shared_badge", lang)
    elif _get(debt, "share_status") == "pending" and is_mine:
        badge = "\n" + i18n.get("share_pending", lang)

    title = owner_name if (not is_mine and owner_name) else counterpart_name(debt)
    cur = esc(debt["currency"])

    text = (
        f"👤 <b>{esc(title)}</b>{badge}\n"
        f"<i>{direction}</i>\n"
        f"{RULE}\n\n"
        f"💵 <b>{fmt_amount(debt['amount'])} {cur}</b>\n"
        f"📅 {_when(debt['due_date'], lang)}\n"
    )
    if debt["paid_amount"]:
        text += "\n" + i18n.get("card_paid", lang, paid=fmt_amount(debt["paid_amount"]), currency=cur)
        text += "\n" + i18n.get("card_remaining", lang, remaining=fmt_amount(remaining), currency=cur) + "\n"
    if _get(debt, "description"):
        text += "\n" + i18n.get("card_note", lang, note=esc(debt["description"])) + "\n"
    created = _get(debt, "created_at")
    if created:
        text += "\n<i>" + i18n.get("card_created", lang, date=fmt_ts(created)) + "</i>"
    if not is_mine and owner_name:
        text += "\n\n" + i18n.get("readonly_hint", lang, owner=esc(owner_name))
    return text.rstrip()


def confirm_card(data, lang):
    direction = i18n.get("dir_lent" if data["debt_type"] == "lent" else "dir_borrowed", lang)
    text = (
        f"{i18n.get('confirm_title', lang)}\n{RULE}\n\n"
        f"👤 <b>{esc(data['person_name'])}</b>\n"
        f"<i>{direction}</i>\n\n"
        f"💵 <b>{_money(data['amount'], data['currency'])}</b>\n"
        f"📅 {_when(data['due_date'], lang)}"
    )
    if data.get("description"):
        text += "\n" + i18n.get("card_note", lang, note=esc(data["description"]))
    return text


def saved_card(data, lang):
    key = "confirm_saved" if data.get("due_date") else "confirm_saved_nodate"
    return (
        i18n.get(key, lang) + "\n\n"
        + f"👤 <b>{esc(data['person_name'])}</b> — {_money(data['amount'], data['currency'])}\n\n"
        + i18n.get("saved_share_hint", lang, name=esc(data["person_name"]))
    )


def closed_card(debt, lang):
    return i18n.get("debt_closed_card", lang, name=esc(debt["person_name"]),
                    amount=fmt_amount(debt["amount"]), currency=esc(debt["currency"]))


def history_card(debts, lang):
    if not debts:
        return f"{i18n.get('history_title', lang)}\n\n{i18n.get('history_empty', lang)}"
    text = i18n.get("history_title", lang) + "\n" + RULE + "\n\n"
    for d in debts[:20]:
        closed = _get(d, "closed_at")
        when = (i18n.get("history_closed_on", lang, date=fmt_ts(closed))
                if closed else fmt_date(d["due_date"]))
        text += f"✅ <b>{esc(d['person_name'])}</b> — {_money(d['amount'], d['currency'])}\n"
        if when:
            text += f"     <i>{when}</i>\n"
    return text


def reminder_text(debt, kind, lang, days, reader_is_owner=True, owner_display=None):
    """The same reminder, worded for whichever side reads it."""
    if reader_is_owner:
        dtype, name = debt["debt_type"], debt["person_name"]
    else:
        dtype = "borrowed" if debt["debt_type"] == "lent" else "lent"
        name = owner_display or "?"
    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    key = {"before": "reminder_before", "today": "reminder_today"}.get(kind, "reminder_overdue")
    return i18n.get(
        key, lang,
        direction=i18n.get("dir_lent" if dtype == "lent" else "dir_borrowed", lang),
        name=esc(name),
        amount=fmt_amount(remaining),
        currency=esc(debt["currency"]),
        date=fmt_date(debt["due_date"]),
        days=days,
    )
