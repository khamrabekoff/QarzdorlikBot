"""Card renderers - every user-facing block of text is built here."""
from utils import days_until, fmt_amount, fmt_date, i18n


def due_phrase(iso_date, lang):
    """'осталось 5 дн.' / 'просрочено на 3 дн.' / 'сегодня'."""
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
        text += (
            f"{marker} <b>{idx}. {d['person_name']}</b>\n"
            f"     {fmt_amount(remaining)} {d['currency']}\n"
            f"     <i>{fmt_date(d['due_date'])} — {due_phrase(d['due_date'], lang)}</i>\n"
        )
        if d["paid_amount"]:
            text += f"     <i>{i18n.get('card_paid', lang, paid=fmt_amount(d['paid_amount']), currency=d['currency'])}</i>\n"
        text += "\n"

    totals_str = " · ".join(f"{fmt_amount(v)} {c}" for c, v in totals.items())
    text += "━" * 18 + "\n" + i18n.get("list_total", lang, totals=totals_str)
    text += "\n\n" + i18n.get("list_pick", lang)
    return text


def debt_card(debt, lang):
    remaining = (debt["amount"] or 0) - (debt["paid_amount"] or 0)
    direction = i18n.get("dir_lent" if debt["debt_type"] == "lent" else "dir_borrowed", lang)

    text = (
        f"👤 <b>{debt['person_name']}</b>\n"
        f"<i>{direction}</i>\n"
        + "━" * 18 + "\n\n"
        f"💵 {fmt_amount(debt['amount'])} {debt['currency']}\n"
        f"📅 {fmt_date(debt['due_date'])} — <i>{due_phrase(debt['due_date'], lang)}</i>\n"
    )
    if debt["paid_amount"]:
        text += "\n" + i18n.get("card_paid", lang,
                                paid=fmt_amount(debt["paid_amount"]),
                                currency=debt["currency"]) + "\n"
        text += i18n.get("card_remaining", lang,
                         remaining=fmt_amount(remaining),
                         currency=debt["currency"])
    return text


def confirm_card(data, lang):
    direction = i18n.get("dir_lent" if data["debt_type"] == "lent" else "dir_borrowed", lang)
    return (
        f"{i18n.get('confirm_title', lang)}\n" + "━" * 18 + "\n\n"
        f"👤 <b>{data['person_name']}</b>\n"
        f"<i>{direction}</i>\n\n"
        f"💵 <b>{fmt_amount(data['amount'])} {data['currency']}</b>\n"
        f"📅 {fmt_date(data['due_date'])} — <i>{due_phrase(data['due_date'], lang)}</i>"
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
