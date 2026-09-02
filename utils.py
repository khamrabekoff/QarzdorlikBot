"""Translations, formatting, and date parsing."""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import TIMEZONE

logger = logging.getLogger(__name__)

TZ = ZoneInfo(TIMEZONE)


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


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


def fmt_date(iso_str) -> str:
    """Stored ISO -> displayed DD.MM.YYYY."""
    try:
        return datetime.strptime(str(iso_str), "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(iso_str)


def days_until(iso_str):
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
    return value if value > 0 else None


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
        import calendar
        last = calendar.monthrange(today.year, today.month)[1]
        if today.day < last:
            return date(today.year, today.month, last).isoformat()
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    return (today + timedelta(days=days, weeks=weeks) + timedelta(days=30 * months)).isoformat()
