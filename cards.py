"""Picture cards for the main screens (home, person, debt).

A Telegram message can only be text, emoji and grey buttons - there is no
way to make a list of debts look like an app. A rendered image can. The
buttons underneath stay exactly as they were; only the look changes.

Reminders stay text on purpose: a phone notification for a photo just says
"Photo", while text shows the name and the amount.

If Pillow isn't installed (e.g. right after a deploy, before /deploy has
run pip), every render_* returns None and the caller falls back to the
text card - the bot never breaks over a missing picture.
"""
import io
import logging
import os
import re
import zlib
from functools import lru_cache

from utils import days_until, fmt_amount, fmt_date, fmt_ts, i18n, now_local
from views import due_phrase

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a bare server
    AVAILABLE = False

_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
S = 2          # draw at 2x: Telegram scales down on phones, text stays crisp
W = 720        # logical width

BG = (244, 245, 247)
CARD = (255, 255, 255)
INK = (22, 24, 29)
MUTED = (120, 125, 135)
LINE = (232, 234, 238)
GREEN, GREEN_BG = (18, 140, 90), (226, 245, 236)
RED, RED_BG = (210, 60, 55), (252, 233, 231)
AMBER = (180, 110, 10)
AVATARS = [  # (background, initials) - picked by a stable hash of the name
    ((237, 231, 254), (91, 58, 200)),
    ((224, 242, 254), (14, 116, 144)),
    ((254, 243, 199), (161, 98, 7)),
    ((252, 231, 243), (157, 23, 77)),
    ((220, 252, 231), (21, 128, 61)),
    ((255, 237, 213), (194, 65, 12)),
]

# Open Sans has no emoji; a stray one would draw as an empty box.
_EMOJI = re.compile("[\U00010000-\U0010FFFF☀-➿️‍]")


def _plain(text) -> str:
    """Locale strings carry HTML and emoji for chat; strip both for drawing."""
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    return " ".join(_EMOJI.sub("", text).split())


@lru_cache(maxsize=32)
def _font(weight: str, size: int):
    return ImageFont.truetype(os.path.join(_FONTS, f"OpenSans-{weight}.ttf"), size * S)


class _Canvas:
    def __init__(self, height):
        self.img = Image.new("RGB", (W * S, height * S), BG)
        self.d = ImageDraw.Draw(self.img)

    def box(self, x0, y0, x1, y1, fill, r=22):
        self.d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=r * S, fill=fill)

    def text(self, x, y, s, weight, size, fill, anchor="la", max_w=None):
        f = _font(weight, size)
        s = _plain(s)
        if max_w:
            s = self._fit(s, f, max_w * S)
        self.d.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)

    def width(self, s, weight, size):
        return self.d.textlength(_plain(s), font=_font(weight, size)) / S

    @staticmethod
    def _fit(s, f, max_px):
        if f.getlength(s) <= max_px:
            return s
        while s and f.getlength(s + "…") > max_px:
            s = s[:-1]
        return s.rstrip() + "…"

    def line(self, x0, y, x1):
        self.d.line([(x0 * S, y * S), (x1 * S, y * S)], fill=LINE, width=S)

    def avatar(self, x, y, name, size=52):
        bg, fg = AVATARS[zlib.crc32(name.casefold().encode()) % len(AVATARS)]
        self.d.ellipse([x * S, y * S, (x + size) * S, (y + size) * S], fill=bg)
        self.text(x + size / 2, y + size / 2, initials(name), "Bold", int(size * 0.34), fg, "mm")

    def dot(self, x, y, color, r=6):
        self.d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S],
                       fill=color, outline=CARD, width=2 * S)

    def bar(self, x, y, w, ratio, h=6):
        self.box(x, y, x + w, y + h, LINE, r=3)
        if ratio > 0:
            self.box(x, y, x + max(h, w * min(ratio, 1)), y + h, GREEN, r=3)

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def initials(name) -> str:
    words = [w for w in re.split(r"[\s@_.\-]+", _plain(name)) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def _months(lang):
    return i18n.get("img_months", lang).split(",")


def _today(lang):
    t = now_local().date()
    return f"{t.day} {_months(lang)[t.month - 1].strip()} {t.year}"


def _remaining(d):
    return (d["amount"] or 0) - (d["paid_amount"] or 0)


def _due_line(due, lang):
    """('3 kun kechikdi', RED) / ('20.10 gacha', MUTED) / ('muddatsiz', MUTED)."""
    if not due:
        return i18n.get("no_deadline", lang), MUTED
    n = days_until(due)
    if n is None:
        return fmt_date(due), MUTED
    if n < 0:
        return i18n.get("img_late", lang, n=-n), RED
    if n == 0:
        return i18n.get("due_today", lang), AMBER
    if n == 1:
        return i18n.get("due_tomorrow", lang), AMBER
    return i18n.get("img_until", lang, date=fmt_date(due)[:5]), MUTED


def _money_lines(totals):
    """{'so'm': 2.8e6, '$': 1112} -> ('2 800 000', ["so'm", '+ 1 112 $'])
    The biggest-by-count currency goes large, the rest small underneath."""
    items = [(c, v) for c, v in totals.items() if abs(v) >= 0.005]
    if not items:
        return "0", []
    items.sort(key=lambda cv: -abs(cv[1]))
    (cur, val), rest = items[0], items[1:]
    small = [cur] + [f"+ {fmt_amount(abs(v))} {c}" for c, v in rest]
    return fmt_amount(abs(val)), small


# ---------- home ----------

def render_home(totals_rows, people, lang, flt="all", page=0, per_page=8):
    if not AVAILABLE:
        return None
    lent = {r["currency"]: r["remaining"] for r in totals_rows if r["debt_type"] == "lent" and r["remaining"]}
    owed = {r["currency"]: r["remaining"] for r in totals_rows if r["debt_type"] == "borrowed" and r["remaining"]}
    shown = people[page * per_page:(page + 1) * per_page]
    empty = not lent and not owed

    rows_h = max(1, len(shown)) * 92
    has_balance = bool(lent and owed)
    height = 130 + (0 if empty else 150) + (66 if has_balance else 0) + 56 + rows_h + 36
    c = _Canvas(height)

    c.text(40, 40, i18n.get("home_title", lang), "Bold", 34, INK)
    sub = _today(lang) + " · " + i18n.get("img_people_n", lang, n=len(people))
    if flt != "all":
        sub += " · " + i18n.get(f"img_filter_{flt}", lang)
    c.text(40, 90, sub, "Regular", 17, MUTED)
    y = 130

    if not empty:
        for x0, label, totals, fg, bg in (
            (40, "img_owe_me", lent, GREEN, GREEN_BG),
            (368, "img_i_owe", owed, RED, RED_BG),
        ):
            c.box(x0, y, x0 + 312, y + 134, bg)
            big, small = _money_lines(totals)
            c.text(x0 + 24, y + 20, i18n.get(label, lang), "Semibold", 17, fg)
            c.text(x0 + 24, y + 48, big, "Bold", 36 if len(big) < 12 else 28, fg, max_w=272)
            c.text(x0 + 24, y + 98, "  ".join(small), "Regular", 16, fg, max_w=272)
        y += 150

        if has_balance:
            net = {}
            for cur, v in lent.items():
                net[cur] = net.get(cur, 0) + v
            for cur, v in owed.items():
                net[cur] = net.get(cur, 0) - v
            parts = [(f"{'+' if v > 0 else '−'}{fmt_amount(abs(v))} {cur}", GREEN if v > 0 else RED)
                     for cur, v in net.items() if abs(v) >= 0.005]
            c.box(40, y, 680, y + 50, CARD, r=16)
            c.text(64, y + 25, i18n.get("img_balance", lang), "Regular", 18, MUTED, "lm")
            x = 656
            for text, color in reversed(parts):
                c.text(x, y + 25, text, "Bold", 19, color, "rm")
                x -= c.width(text, "Bold", 19) + 18
            y += 66

    c.box(40, y, 680, height - 30, CARD)
    c.text(64, y + 24, i18n.get("img_people", lang), "Semibold", 14, MUTED)
    y += 56
    if not shown:
        key = "home_empty" if empty else "home_filter_empty"
        c.text(360, y + 30, i18n.get(key, lang).split("\n")[0], "Regular", 18, MUTED, "mm", max_w=560)
    for i, p in enumerate(shown):
        if i:
            c.line(64, y - 12, 656)
        _person_row(c, y, p, lang)
        y += 92
    return c.png()


def _person_row(c, y, p, lang):
    c.avatar(64, y, p["name"])
    if p["overdue"]:
        c.dot(114, y + 46, RED)
    n = len(p["debts"])
    due = min((d["due_date"] for d in p["debts"] if d["due_date"]), default=None)
    sub, sub_color = _due_line(due, lang)
    if p["overdue"]:
        late = min(days_until(d["due_date"]) for d in p["debts"]
                   if d["due_date"] and (days_until(d["due_date"]) or 0) < 0)
        sub, sub_color = i18n.get("img_late", lang, n=-late), RED
    if n > 1:
        sub += " · " + i18n.get("img_debts_n", lang, n=n)

    amounts = [(f"{'+' if v > 0 else '−'}{fmt_amount(abs(v))}" + ("" if cur == "so'm" else f" {cur}"),
                GREEN if v > 0 else RED) for cur, v in p["net"].items() if abs(v) >= 0.005]
    right_w = max([c.width(a, "Bold", 21) for a, _ in amounts] + [0])
    name_w = 656 - 134 - right_w - 16
    c.text(134, y + 2, p["name"], "Semibold", 21, INK, max_w=name_w)

    paid = sum(d["paid_amount"] or 0 for d in p["debts"])
    total = sum(d["amount"] or 0 for d in p["debts"])
    if paid and total and sub_color != RED:
        sub += " · " + i18n.get("img_paid_pct", lang, p=round(100 * paid / total))
    c.text(134, y + 34, sub, "Regular", 16, sub_color, max_w=name_w)
    if paid and total:
        c.bar(134, y + 62, min(220, name_w), paid / total)

    for j, (text, color) in enumerate(amounts[:2]):
        c.text(656, y + 6 + j * 30, text, "Bold" if j == 0 else "Semibold", 21 if j == 0 else 16, color, "ra")


# ---------- one person ----------

def render_person(person, lang):
    if not AVAILABLE:
        return None
    debts = person["debts"][:30]
    height = 184 + len(debts) * 96 + 30
    c = _Canvas(height)

    c.avatar(40, 36, person["name"], size=72)
    c.text(130, 44, person["name"], "Bold", 30, INK, max_w=550)
    net_lent = {k: v for k, v in person["net"].items() if v > 0}
    net_owed = {k: -v for k, v in person["net"].items() if v < 0}
    parts = []
    if net_lent:
        parts.append((i18n.get("img_owes_you", lang) + ": " + " · ".join(
            f"{fmt_amount(v)} {k}" for k, v in net_lent.items()), GREEN))
    if net_owed:
        parts.append((i18n.get("img_you_owe", lang) + ": " + " · ".join(
            f"{fmt_amount(v)} {k}" for k, v in net_owed.items()), RED))
    for i, (text, color) in enumerate(parts):
        c.text(130, 92 + i * 26, text, "Semibold", 18, color, max_w=550)

    y = 160
    c.box(40, y, 680, height - 30, CARD)
    y += 24
    for i, d in enumerate(debts, 1):
        if i > 1:
            c.line(64, y - 14, 656)
        _debt_row(c, y, i, d, lang)
        y += 96
    return c.png()


def _debt_row(c, y, i, d, lang):
    lent = (d["view_type"] if "view_type" in d.keys() else d["debt_type"]) == "lent"
    color = GREEN if lent else RED
    c.d.ellipse([64 * S, y * S, 100 * S, (y + 36) * S], fill=GREEN_BG if lent else RED_BG)
    c.text(82, y + 18, str(i), "Bold", 17, color, "mm")
    rem = _remaining(d)
    c.text(118, y - 2, f"{fmt_amount(rem)} {d['currency']}", "Bold", 22, INK, max_w=360)
    c.text(656, y + 2, i18n.get("dir_lent" if lent else "dir_borrowed", lang), "Semibold", 15, color, "ra")
    sub, sub_color = _due_line(d["due_date"], lang)
    if d["due_date"]:
        sub = f"{fmt_date(d['due_date'])} · {due_phrase(d['due_date'], lang)}"
    note = d["description"] if "description" in d.keys() else None
    if note:
        sub += " · " + note
    c.text(118, y + 32, sub, "Regular", 16, sub_color, max_w=538)
    if d["paid_amount"]:
        c.bar(118, y + 62, 220, (d["paid_amount"] or 0) / (d["amount"] or 1))


# ---------- one debt ----------

def render_debt(debt, lang, title, is_mine=True):
    """`debt` is the reader's view (view_type already flipped for the other
    side); `title` is who it's with, as the reader should see it."""
    if not AVAILABLE:
        return None
    lent = debt["view_type"] == "lent"
    color, tint = (GREEN, GREEN_BG) if lent else (RED, RED_BG)
    rem = _remaining(debt)

    rows = []
    due_text, due_color = _due_line(debt["due_date"], lang)
    if debt["due_date"]:
        due_text = f"{fmt_date(debt['due_date'])} · {due_phrase(debt['due_date'], lang)}"
    rows.append((i18n.get("img_label_due", lang), due_text, due_color))
    if debt["paid_amount"]:
        rows.append((i18n.get("img_label_paid", lang),
                     f"{fmt_amount(debt['paid_amount'])} {debt['currency']}", GREEN))
    if debt["description"]:
        rows.append((i18n.get("img_label_note", lang), debt["description"], INK))
    if debt["share_status"] == "accepted":
        rows.append((i18n.get("img_label_shared", lang), i18n.get("img_shared_yes", lang), GREEN))
    elif debt["share_status"] == "pending" and is_mine:
        rows.append((i18n.get("img_label_shared", lang), i18n.get("img_shared_wait", lang), AMBER))
    rows.append((i18n.get("img_label_created", lang), fmt_ts(debt["created_at"]), MUTED))

    height = 330 + len(rows) * 56 + 40
    c = _Canvas(height)
    c.avatar(40, 36, title, size=72)
    c.text(130, 44, title, "Bold", 30, INK, max_w=550)
    label = i18n.get("dir_lent" if lent else "dir_borrowed", lang)
    lw = c.width(label, "Semibold", 16) + 28
    c.box(130, 92, 130 + lw, 122, tint, r=15)
    c.text(144, 107, label, "Semibold", 16, color, "lm")

    c.box(40, 150, 680, 290, tint)
    c.text(64, 172, i18n.get("img_left" if debt["paid_amount"] else "img_amount", lang), "Semibold", 17, color)
    c.text(64, 200, f"{fmt_amount(rem)} {debt['currency']}", "Bold", 40, color, max_w=600)
    if debt["paid_amount"]:
        c.text(656, 172, i18n.get("img_of_total", lang, total=fmt_amount(debt["amount"])),
               "Regular", 16, color, "ra")
        c.bar(64, 266, 592, (debt["paid_amount"] or 0) / (debt["amount"] or 1), h=8)

    y = 310
    c.box(40, y, 680, height - 30, CARD)
    y += 20
    for i, (label, value, vcolor) in enumerate(rows):
        if i:
            c.line(64, y - 8, 656)
        c.text(64, y + 14, label, "Regular", 17, MUTED, "lm")
        c.text(656, y + 14, value, "Semibold", 17, vcolor, "rm", max_w=400)
        y += 56
    return c.png()
