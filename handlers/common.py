"""Small helpers shared by the handler modules."""
import logging

from aiogram import Bot, types

import database as db
from utils import display_name, esc, fmt_amount, fmt_date, i18n

logger = logging.getLogger(__name__)


async def edit_or_send(callback: types.CallbackQuery, text, markup=None):
    """Edit the card in place; if that's impossible (message too old, deleted,
    or not text), send a fresh one rather than leaving the tap unanswered."""
    try:
        await callback.message.edit_text(text, reply_markup=markup)
        return
    except Exception as e:  # noqa: BLE001
        if "not modified" in str(e).lower():
            return
        logger.debug("edit_text failed, sending instead: %s", e)
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


async def user_display(user_id: int) -> str:
    u = await db.get_user(user_id)
    return display_name(u and u["username"], u and u["first_name"])


async def notify_counterparty(bot: Bot, debt, key: str, **fields):
    """Tell the other side of a confirmed shared debt that the owner changed
    it. Without this the two copies of 'the same agreement' silently drift:
    one person closes it, the other keeps getting reminders about it."""
    if not (debt and debt["counterparty_id"] and debt["share_status"] == "accepted"):
        return
    lang = await db.get_user_lang(debt["counterparty_id"])
    params = {
        "owner": esc(await user_display(debt["user_id"])),
        "amount": fmt_amount(debt["amount"]),
        "currency": esc(debt["currency"]),
        "date": fmt_date(debt["due_date"]) or i18n.get("no_deadline", lang),
    }
    params.update(fields)
    try:
        await bot.send_message(debt["counterparty_id"], i18n.get(key, lang, **params))
    except Exception as e:  # noqa: BLE001 - they may have blocked the bot
        logger.info("Could not notify counterparty %s: %s", debt["counterparty_id"], e)
