"""Card renderers - every user-facing block of text is built here."""
from utils import days_until, fmt_amount, fmt_date, i18n


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


def _totals_line(rows, lang):
    parts = [f"{fmt_amount(r['remaining'])} {r['currency']}" for r in rows if r["remaining"]]
    joiner = " · "
    return joiner.join(parts) if parts else "0"


def home_card(totals_rows, overdue_count, soon_count, lang):
    """Summary the user lands on: what they're owed, what they owe, warnings."""
    lent = [r for r in totals_rows if r["debt_type"] == "lent"]
    borrowed = [r for r in totals_rows if r["debt_type"] == "borrowed"]

    text = i18n.get("home_title", lang) + "\n" + "━" * 18 + "\n\n"

    if not lent and not borrowed:
        return text + i18n.get("home_empty", lang)

    if lent:
        text += f"{i18n.get('home_owe_me', lang)}\n<b>{_totals_line(lent, lang)}</b>\n\n"
    if borrowed:
        text += f"{i18n.get('home_i_owe', lang)}\n<b>{_totals_line(borrowed, lang)}</b>\n\n"

    if overdue_count:
        text += i18n.get("home_overdue", lang, n=overdue_count) + "\n"
    elif soon_count:
        text += i18n.get("home_soon", lang, n=soon_count) + "\n"

    return text.rstrip()


def debt_list_card(debts, debt_type, lang):
    """The whole list in ONE message. The old version sent a separate message
    per debt, which turned twenty debts into twenty-one notifications."""
    header = i18n.get("list_owe_me" if debt_type == "lent" else "list_i_owe", lang)
    if not debts:
        return f"{header}\n\n{i18n.get('list_empty', lang)}"

    text = f"{header}  ({len(debts)})\n" + "━" * 18 + "\n\n"
    totals = {}

    for idx, d in enumerate(debts, 1):
        remaining = (d["amount"] or 0) - (d["paid_amount"] or 0)
        totals[d["currency"]] = totals.get(d["currency"], 0) + remaining

        overdue = (days_until(d["due_date"]) or 0) < 0
        marker = "⚠️" if overdue else "▫️"
        if _has(d, "share_status") and d["share_status"] == "accepted":
            marker = "🤝"  # confirmed by both sides
        when = due_phrase(d["due_date"], lang)
        if d["due_date"]:
            when = f"{fmt_date(d['due_date'])} — {when}"
        who = d["person_name"]
        if _has(d, "is_mine") and not d["is_mine"]:
            who = ("@" + d["owner_username"]) if _has(d, "owner_username") and d["owner_username"] else "?"
        text += (
            f"{marker} <b>{idx}. {who}</b>\n"
            f"     {fmt_amount(remaining)} {d['currency']}\n"
            f"     <i>{when}</i>\n"
        )
        if d["paid_amount"]:
            text += f"     <i>{i18n.get('card_paid', lang, paid=fmt_amount(d['paid_amount']), currency=d['currency'])}</i>\n"
        text += "\n"

    totals_str = " · ".join(f"{fmt_amount(v)} {c}" for c, v in totals.items())
    text += "━" * 18 + "\n" + i18n.get("list_total", lang, totals=totals_str)
    text += "\n\n" + i18n.get("list_pick", lang)
    return text


def _has(row, field):
    """Rows from the shared-view query carry extra columns; plain lookups don't."""
    try:
        return field in row.keys()
    except AttributeError:
        return field in row


def debt_card(debt, lang, owner_name=None):
    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    # For a shared debt the reader may be the counterparty, for whom the
    # direction is reversed; view_type carries that already-flipped value.
    dtype = debt["view_type"] if _has(debt, "view_type") else debt["debt_type"]
    is_mine = debt["is_mine"] if _has(debt, "is_mine") else 1
    direction = i18n.get("dir_lent" if dtype == "lent" else "dir_borrowed", lang)

    badge = ""
    if _has(debt, "share_status"):
        if debt["share_status"] == "accepted":
            badge = "  " + i18n.get("shared_badge", lang)
        elif debt["share_status"] == "pending":
            badge = "  " + i18n.get("share_pending", lang)

    # The owner wrote the OTHER party's name into person_name. Showing that
    # back to the other party reads as "Ali — you owe", which is nonsense from
    # their side: they need to see who recorded the debt.
    title = debt["person_name"]
    if not is_mine:
        title = owner_name or (
            "@" + debt["owner_username"]
            if _has(debt, "owner_username") and debt["owner_username"] else "?"
        )

    text = (
        f"👤 <b>{title}</b>{badge}\n"
        f"<i>{direction}</i>\n"
        + "━" * 18 + "\n\n"
        f"💵 {fmt_amount(debt['amount'])} {debt['currency']}\n"
        f"📅 {_when(debt['due_date'], lang)}\n"
    )
    if debt["paid_amount"]:
        text += "\n" + i18n.get("card_paid", lang,
                                paid=fmt_amount(debt["paid_amount"]),
                                currency=debt["currency"]) + "\n"
        text += i18n.get("card_remaining", lang,
                         remaining=fmt_amount(remaining),
                         currency=debt["currency"])
    if not is_mine and owner_name:
        text += "\n\n" + i18n.get("readonly_hint", lang, owner=owner_name)
    return text


def confirm_card(data, lang):
    direction = i18n.get("dir_lent" if data["debt_type"] == "lent" else "dir_borrowed", lang)
    return (
        f"{i18n.get('confirm_title', lang)}\n" + "━" * 18 + "\n\n"
        f"👤 <b>{data['person_name']}</b>\n"
        f"<i>{direction}</i>\n\n"
        f"💵 <b>{fmt_amount(data['amount'])} {data['currency']}</b>\n"
        f"📅 {_when(data['due_date'], lang)}"
    )


def history_card(debts, lang):
    if not debts:
        return f"{i18n.get('history_title', lang)}\n\n{i18n.get('history_empty', lang)}"
    text = i18n.get("history_title", lang) + "\n" + "━" * 18 + "\n\n"
    for d in debts[:20]:
        text += (
            f"✅ <b>{d['person_name']}</b> — {fmt_amount(d['amount'])} {d['currency']}\n"
            f"     <i>{fmt_date(d['due_date'])}</i>\n"
        )
    return text
