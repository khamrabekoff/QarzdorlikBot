"""Reliability plumbing: retries, error handling, alerts, the webhook gate."""
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, SendMessage

import app as bot_app
import database as db
import ops
from utils import now_local


class _Flaky:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    async def __call__(self, bot, method):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(bot_app.asyncio, "sleep", no_sleep)


async def test_network_error_is_retried():
    err = TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message="proxy 503")
    flaky = _Flaky(err, err, "ok")
    assert await bot_app.ResilientRequests()(flaky, None, SendMessage(chat_id=1, text="x")) == "ok"
    assert flaky.calls == 3


async def test_pythonanywhere_proxy_503_is_retried():
    """The exact exception from the server log. v2.1 caught python_socks'
    ProxyError, a different class, so these were never retried."""
    from aiohttp_socks import ProxyError
    err = ProxyError("503 Service Unavailable", error_code=503)
    flaky = _Flaky(err, err, "ok")
    assert await bot_app.ResilientRequests()(flaky, None, SendMessage(chat_id=1, text="x")) == "ok"


async def test_gives_up_after_all_attempts():
    err = TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message="down")
    flaky = _Flaky(*[err] * bot_app.ResilientRequests.ATTEMPTS)
    with pytest.raises(TelegramNetworkError):
        await bot_app.ResilientRequests()(flaky, None, SendMessage(chat_id=1, text="x"))


async def test_stale_callback_answer_does_not_abort_the_handler():
    """2026-10-12: 'query is too old' on callback.answer() killed open_debt
    before it could show the card - and the 500 made Telegram replay it 8x."""
    method = AnswerCallbackQuery(callback_query_id="1")
    err = TelegramBadRequest(method=method, message="query is too old")
    assert await bot_app.ResilientRequests()(_Flaky(err), None, method) is True


async def test_other_bad_requests_are_not_swallowed():
    method = SendMessage(chat_id=1, text="x")
    with pytest.raises(TelegramBadRequest):
        await bot_app.ResilientRequests()(_Flaky(TelegramBadRequest(method=method, message="bad")),
                                          None, method)


async def test_handler_crash_tells_user_and_admin(ali, tg, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db is on fire")
    monkeypatch.setattr(db, "get_active_debts", boom)
    tg.clear()
    await ali.say("📥 Menga qarzdor")     # must not raise out of the dispatcher
    to_user = [m.text for m in tg.sent("SendMessage") if m.chat_id == 1]
    to_admin = [m.text for m in tg.sent("SendMessage") if m.chat_id == 999]
    assert to_user and "Xatolik" in to_user[-1]
    assert to_admin and "db is on fire" in to_admin[0]


async def test_admin_alerts_are_throttled(bot, tg):
    assert await ops.alert_admin(bot, "one", key="x", every_seconds=600)
    assert not await ops.alert_admin(bot, "two", key="x", every_seconds=600)
    assert len(tg.sent("SendMessage")) == 1


async def test_missed_cron_alerts_admin(bot, tg):
    await db.meta_set("last_cron_at", (now_local() - timedelta(hours=40)).isoformat())
    await ops.check_cron_alive(bot)
    assert "Extend" in tg.last_text()


async def test_recent_cron_is_quiet(bot, tg):
    await ops.record_cron_run()
    await ops.check_cron_alive(bot)
    assert tg.sent("SendMessage") == []


async def test_failed_backup_is_retried_later(bot, tg, monkeypatch):
    async def down(*a, **k):
        raise ConnectionError("proxy 503")
    monkeypatch.setattr(ops, "send_backup", down)
    await ops.daily_backup(bot)
    assert await db.meta_get("last_backup_date") is None   # not marked done
    monkeypatch.undo()
    await ops.daily_backup(bot)
    assert len(tg.sent("SendDocument")) == 1


async def test_daily_backup_runs_once_per_day(bot, tg):
    await ops.daily_backup(bot)
    await ops.daily_backup(bot)
    assert len(tg.sent("SendDocument")) == 1


async def test_task_is_recreated_before_expiry(bot, tg, monkeypatch):
    soon = (now_local().date() + timedelta(days=3)).isoformat()
    calls = []

    async def fake_pa(session, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if method == "GET" and path == "schedule/":
            return [{"id": 7, "command": "curl https://x/cron/reminders/s", "enabled": True,
                     "interval": "daily", "hour": 5, "minute": 0, "expiry": soon}]
        if method == "POST":
            return {"id": 8, "expiry": "2099-01-01"}
        if method == "GET":
            return {"expiry": "2099-01-01"}
        return None

    monkeypatch.setattr(ops, "PA_API_TOKEN", "t")
    monkeypatch.setattr(ops, "_pa", fake_pa)
    await ops.keep_host_alive(bot)
    assert ("DELETE", "schedule/7/", None) in calls
    post = next(c for c in calls if c[0] == "POST")
    assert post[2]["command"] == "curl https://x/cron/reminders/s" and post[2]["hour"] == 5


def test_webhook_rejects_requests_without_telegrams_secret():
    client = bot_app.app.test_client()
    assert client.post("/webhook", data="{}").status_code == 403
    assert client.post("/webhook", data="{}",
                       headers={"X-Telegram-Bot-Api-Secret-Token": "guess"}).status_code == 403


def test_webhook_acknowledges_even_a_broken_update(monkeypatch):
    """A replay would fail identically, so never answer 500."""
    monkeypatch.setattr(bot_app, "_last_sweep", float("inf"))  # no real Telegram calls
    client = bot_app.app.test_client()
    resp = client.post("/webhook", data="not json",
                       headers={"X-Telegram-Bot-Api-Secret-Token": bot_app.WEBHOOK_SECRET})
    assert resp.status_code == 200


def test_operator_endpoints_need_the_secret():
    client = bot_app.app.test_client()
    for path in ("/status/x", "/logs/x", "/deploy/x", "/setup/x", "/backup/x", "/cron/reminders/x"):
        assert client.get(path).status_code == 403, path


async def test_long_proxy_outage_on_button_answer_does_not_abort():
    """2026-10-13 12:02: the proxy was down longer than all retries and
    open_person died on callback.answer(). That call only clears the spinner."""
    from aiohttp_socks import ProxyError
    method = AnswerCallbackQuery(callback_query_id="1")
    err = ProxyError("503 Service Unavailable", error_code=503)
    flaky = _Flaky(*[err] * bot_app.ResilientRequests.ATTEMPTS)
    assert await bot_app.ResilientRequests()(flaky, None, method) is True


async def test_connection_outage_does_not_alert_admin(ali, tg, monkeypatch):
    from aiohttp_socks import ProxyError

    async def down(*a, **k):
        raise ProxyError("503 Service Unavailable", error_code=503)
    monkeypatch.setattr(db, "get_active_debts", down)
    tg.clear()
    await ali.say("📋 Qarzlarim")
    assert not [m for m in tg.sent("SendMessage") if m.chat_id == 999]
