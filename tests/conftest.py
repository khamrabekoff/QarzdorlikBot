"""Test harness: a real Dispatcher with every router, driven by fake updates
against a fake Telegram that records what the bot sends.

Run:  .venv/Scripts/python -m pytest      (Windows also needs `tzdata`)
"""
import itertools
import os
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser

# Configuration is read at import time, so the environment comes first.
_DB = os.path.join(tempfile.mkdtemp(prefix="qarz_test_"), "test.db")
os.environ.update(BOT_TOKEN="123456:TEST-TOKEN", DB_PATH=_DB, ADMIN_ID="999",
                  CRON_SECRET="cron-secret", DEPLOY_SECRET="deploy-secret")
os.environ.pop("PA_API_TOKEN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402
import pytest  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update, User  # noqa: E402

import app as bot_app  # noqa: E402  (creates the schema in _DB)

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}


class _TagChecker(HTMLParser):
    """Telegram rejects a message whose HTML is not well-formed or uses tags
    it doesn't know. This catches unescaped user input before Telegram does."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"unknown tag <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unbalanced </{tag}>")
        else:
            self.stack.pop()


def assert_valid_html(text):
    checker = _TagChecker()
    checker.feed(text or "")
    checker.close()
    assert not checker.errors and not checker.stack, f"bad HTML {checker.errors} in: {text!r}"


class FakeTelegram(BaseSession):
    """Records every API call and returns plausible results."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self._ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        text = getattr(method, "text", None) or getattr(method, "caption", None)
        if text and getattr(method, "parse_mode", None) in (None, "HTML"):
            assert_valid_html(text)
        if name in ("SendMessage", "EditMessageText", "SendDocument"):
            chat_id = getattr(method, "chat_id", None) or 1
            return Message(message_id=next(self._ids), date=datetime.now(),
                           chat=Chat(id=chat_id, type="private"), text=text or "")
        if name == "GetMe":
            return User(id=42, is_bot=True, first_name="Qarz", username="qarz_test_bot")
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536,
                             raise_for_status=True):  # pragma: no cover
        yield b""

    async def close(self):
        pass

    # --- helpers for assertions ---
    def sent(self, kind=None, chat_id=None):
        out = []
        for m in self.calls:
            name = type(m).__name__
            if kind and name != kind:
                continue
            if chat_id is not None and getattr(m, "chat_id", None) not in (chat_id, None):
                continue
            out.append(m)
        return out

    def texts(self, chat_id=None):
        return [getattr(m, "text", None) or getattr(m, "caption", None) or ""
                for m in self.sent(chat_id=chat_id)
                if type(m).__name__ in ("SendMessage", "EditMessageText", "SendDocument")]

    def last_text(self, chat_id=None):
        texts = self.texts(chat_id)
        return texts[-1] if texts else ""

    def last_markup(self):
        for m in reversed(self.calls):
            if getattr(m, "reply_markup", None) is not None:
                return m.reply_markup
        return None

    def buttons(self):
        """callback_data of every inline button on the most recent keyboard."""
        markup = self.last_markup()
        rows = getattr(markup, "inline_keyboard", None) or []
        return [b.callback_data for row in rows for b in row]

    def alerts(self):
        return [m.text for m in self.sent("AnswerCallbackQuery") if m.text]

    def clear(self):
        self.calls.clear()


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def bot(tg):
    b = Bot(token="123456:TEST-TOKEN", session=tg, default=DefaultBotProperties(parse_mode="HTML"))
    b.session.middleware(bot_app.ResilientRequests())
    return b


@pytest.fixture(autouse=True)
async def clean_db():
    async with aiosqlite.connect(_DB) as db:
        for table in ("debts", "users", "fsm_state", "meta"):
            try:
                await db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        await db.commit()
    yield


_update_ids = itertools.count(1)


def _user(uid, first_name="Ali", username=None, lang_code="uz"):
    return User(id=uid, is_bot=False, first_name=first_name, username=username,
                language_code=lang_code)


class Chatter:
    """One person talking to the bot."""

    def __init__(self, bot, uid=1, first_name="Ali", username="ali", lang_code="uz"):
        self.bot, self.uid = bot, uid
        self.user = _user(uid, first_name, username, lang_code)

    async def say(self, text):
        msg = Message(message_id=next(_update_ids), date=datetime.now(),
                      chat=Chat(id=self.uid, type="private"), from_user=self.user, text=text)
        await bot_app.dp.feed_update(self.bot, Update(update_id=next(_update_ids), message=msg))

    async def tap(self, data):
        on = Message(message_id=next(_update_ids), date=datetime.now(),
                     chat=Chat(id=self.uid, type="private"),
                     from_user=User(id=42, is_bot=True, first_name="Qarz"), text="card")
        cq = CallbackQuery(id=str(next(_update_ids)), from_user=self.user, chat_instance="ci",
                           data=data, message=on)
        await bot_app.dp.feed_update(self.bot, Update(update_id=next(_update_ids), callback_query=cq))

    async def register(self, lang="uz"):
        """Get past onboarding."""
        await self.say("/start")
        await self.tap(f"lang:{lang}")


@pytest.fixture
async def ali(bot):
    c = Chatter(bot, uid=1, first_name="Ali", username="ali")
    await c.register()
    return c


@pytest.fixture
async def vali(bot):
    c = Chatter(bot, uid=2, first_name="Vali", username="vali")
    await c.register()
    return c
