"""Translations, formatting, and date parsing."""
import calendar
import html
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import TIMEZONE

logger = logging.getLogger(__name__)

TZ = ZoneInfo(TIMEZONE)

# Anything above this is a typo (an extra zero row), not a debt. It also keeps
# float precision and Telegram's message length out of trouble.
MAX_AMOUNT = 10 ** 13
MAX_NAME_LEN = 40
MAX_NOTE_LEN = 200


def esc(text) -> str:
    """Escape user-supplied text for HTML parse mode.

    Every message goes out with parse_mode=HTML, so a name like "<Ali>" or
    "A&B" used to make Telegram reject the whole message - the user tapped a
    button and got silence."""
    return html.escape(str(text if text is not None else ""), quote=False)


def clean_name(text):
    """Collapse whitespace; None when empty or too long to be a name."""
    name = " ".join((text or "").split())
    if not name or len(name) > MAX_NAME_LEN:
        return None
    return name


def lang_from_code(language_code) -> str:
    """Telegram's client language -> our locale. Russian-speaking clients get
    Russian; everyone else gets Uzbek, the bot's home language."""
    code = (language_code or "").lower()
    return "ru" if code.startswith(("ru", "be", "kk", "uk")) else "uz"


def display_name(username=None, first_name=None) -> str:
    if username:
        return "@" + username
    return first_name or "?"


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def utc_to_local(ts):
    """SQLite's CURRENT_TIMESTAMP is UTC; everything user-facing is Tashkent
    time. Comparing the two raw put 'created 30 minutes ago' five hours off."""
    try:
        dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).replace(tzinfo=None)


def fmt_ts(ts) -> str:
    """Stored UTC timestamp -> local DD.MM.YYYY."""
    local = utc_to_local(ts)
    return local.strftime("%d.%m.%Y") if local else ""


class I18n:
    def __init__(self):
        self.locales = {}
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
        for filename in os.listdir(folder):
            if filename.endswith(".json"):
                with open(os.path.join(folder, filename), encoding="utf-8") as f:
                    self.locales[filename[:-5]] = json.load(f)

    def get(self, key, lang="uz", **kwargs):
        text = self.locales.get(lang, {}).get(key)
        if text is None:
            # Falling back to Uzbek beats showing the raw key to a user, which
            # is what the previous version did.
            text = self.locales.get("uz", {}).get(key, key)
            if key not in self.locales.get("uz", {}):
                logger.warning("Missing translation for %r (%s)", key, lang)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning("Bad placeholder in %r (%s): %s", key, lang, e)
        return text

    def all_variants(self, key):
        """Every language's version of a label - for matching button presses."""
        return [loc[key] for loc in self.locales.values() if key in loc]


i18n = I18n()


def fmt_amount(val) -> str:
    """1234567.0 -> '1 234 567'; keeps decimals only when they matter."""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return str(val)
    if abs(val - round(val)) < 0.005:
        return f"{int(round(val)):,}".replace(",", " ")
    return f"{val:,.2f}".replace(",", " ")


def fmt_paid_csv(val) -> str:
    """Number for a spreadsheet cell: no thousands spaces (Excel would read
    text), comma decimals (the uz/ru Excel convention)."""
    val = float(val or 0)
    if abs(val - round(val)) < 0.005:
        return str(int(round(val)))
    return f"{val:.2f}".replace(".", ",")


def fmt_date(iso_str) -> str:
    """Stored ISO -> displayed DD.MM.YYYY. None means an open-ended debt."""
    if not iso_str:
        return ""
    try:
        return datetime.strptime(str(iso_str), "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(iso_str)


def days_until(iso_str):
    if not iso_str:
        return None
    try:
        due = datetime.strptime(str(iso_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (due - now_local().date()).days


def parse_amount(text):
    """Accepts '1 500 000', '1500000', '1.5', '1,5', '500k', '2 mln'."""
    if not text:
        return None
    t = text.strip().lower().replace(" ", " ")
    # Suffix multipliers attach straight to the digits ("500k"), so requiring
    # a word boundary before the letter is wrong - there isn't one after a digit.
    multiplier = 1
    if re.search(r"\d\s*(mln|млн|million|m|м)(\b|$)", t):
        multiplier = 1_000_000
    elif re.search(r"\d\s*(ming|тыс|k|к)(\b|$)", t):
        multiplier = 1_000
    t = re.sub(r"[^\d.,]", "", t)
    if not t:
        return None
    # A comma is a decimal separator here, not a thousands separator: people
    # type "1,5 mln" far more often than "1,500".
    t = t.replace(",", ".")
    if t.count(".") > 1:
        head, _, tail = t.rpartition(".")
        t = head.replace(".", "") + "." + tail
    try:
        value = float(t) * multiplier
    except ValueError:
        return None
    return value if 0 < value <= MAX_AMOUNT else None


_AMOUNT_WORD = re.compile(r"^(mln|млн|million|m|м|ming|тыс|k|к|so'?m|сум|\$|€|₽)$", re.I)


def split_name_amount(text):
    """'Ali aka 500k' -> ('Ali aka', 500000.0). Either part may be None.

    The name is everything before the first token with a digit in it, so
    two-word names survive; the old split took only the first word as the
    name and fed 'aka 500k' to the amount parser."""
    tokens = (text or "").split()
    first_digit = next((i for i, tok in enumerate(tokens) if re.search(r"\d", tok)), None)
    if first_digit is None:
        return clean_name(text), None

    if first_digit == 0:
        # "500000 Ali": amount first. The amount runs while tokens are digits
        # or multiplier words ("1,5 mln Ali").
        end = 1
        while end < len(tokens) and (re.search(r"\d", tokens[end]) or _AMOUNT_WORD.match(tokens[end])):
            end += 1
        return clean_name(" ".join(tokens[end:])), parse_amount(" ".join(tokens[:end]))

    return clean_name(" ".join(tokens[:first_digit])), parse_amount(" ".join(tokens[first_digit:]))


def parse_date(text):
    """Accepts DD.MM, DD.MM.YYYY, DD/MM/YY, and a few relative words.

    Returns an ISO string, or None. A bare DD.MM that has already passed this
    year is read as next year - people writing '05.01' in December mean the
    coming January, not eleven months ago.
    """
    if not text:
        return None
    t = text.strip().lower()
    today = now_local().date()

    words = {
        "bugun": 0, "сегодня": 0, "today": 0,
        "ertaga": 1, "завтра": 1, "tomorrow": 1,
    }
    if t in words:
        return (today + timedelta(days=words[t])).isoformat()

    normalized = re.sub(r"[\s/\-]", ".", t)
    parts = [p for p in normalized.split(".") if p]
    try:
        if len(parts) == 2:
            d, m = int(parts[0]), int(parts[1])
            candidate = date(today.year, m, d)
            if candidate < today:
                candidate = date(today.year + 1, m, d)
            return candidate.isoformat()
        if len(parts) == 3:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
            return date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None
    return None


def shift_date(days=0, weeks=0, months=0, end_of="") -> str:
    today = now_local().date()
    if end_of == "week":
        ahead = 6 - today.weekday() or 7
        return (today + timedelta(days=ahead)).isoformat()
    if end_of == "month":
        last = calendar.monthrange(today.year, today.month)[1]
        if today.day < last:
            return date(today.year, today.month, last).isoformat()
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    return (today + timedelta(days=days, weeks=weeks) + timedelta(days=30 * months)).isoformat()


# Codes carried in the date buttons' callback data -> due date. 'none' is an
# open-ended debt; anything unrecognised (a button from an older version)
# falls back to a week rather than failing.
_DATE_CHOICES = {
    "t1": lambda: shift_date(days=1),
    "d3": lambda: shift_date(days=3),
    "w1": lambda: shift_date(weeks=1),
    "d10": lambda: shift_date(days=10),
    "m1": lambda: shift_date(months=1),
    "ew": lambda: shift_date(end_of="week"),  # no longer offered; old buttons still send it
    "em": lambda: shift_date(end_of="month"),
}


def resolve_date_choice(code):
    if code == "none":
        return None
    return _DATE_CHOICES.get(code, _DATE_CHOICES["w1"])()
