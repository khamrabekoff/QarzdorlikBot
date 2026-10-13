"""Small helpers shared by the handler modules."""
import logging

from aiogram import Bot, types
from aiogram.types import BufferedInputFile, InputMediaPhoto

import database as db
from utils import display_name, esc, fmt_amount, fmt_date, i18n

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024


def _is_photo(message) -> bool:
    return bool(getattr(message, "photo", None))


async def delete_message(message):
    try:
        await message.delete()
    except Exception as e:  # noqa: BLE001 - too old to delete, already gone...
        logger.debug("delete failed: %s", e)


async def edit_or_send(callback: types.CallbackQuery, text, markup=None, keep_photo=False):
    """Show `text` in place of the message the button was on.

    On a picture card: with keep_photo the picture stays and the text
    becomes its caption (small steps like 'what to edit?'); otherwise the
    picture is replaced by a fresh text message, so the chat doesn't keep
    an outdated card above it."""
    msg = callback.message
    try:
        if _is_photo(msg):
            if keep_photo and len(text) <= CAPTION_LIMIT:
                await msg.edit_caption(caption=text, reply_markup=markup)
                return
            await delete_message(msg)
        else:
            await msg.edit_text(text, reply_markup=markup)
            return
    except Exception as e:  # noqa: BLE001
        if "not modified" in str(e).lower():
            return
        logger.debug("edit failed, sending instead: %s", e)
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


async def show_card(bot: Bot, user_id: int, png, caption, markup, fallback_text,
                    callback: types.CallbackQuery = None):
    """Put a picture card on screen - in place when the button was on a
    picture, as a new message otherwise. Text messages are never deleted
    for it: a reminder you tapped 'open' on should stay in the chat.
    Without Pillow (png is None) the text card is used instead."""
    if png is None:
        if callback:
            await edit_or_send(callback, fallback_text, markup)
        else:
            await bot.send_message(user_id, fallback_text, reply_markup=markup)
        return

    photo = BufferedInputFile(png, filename="card.png")
    caption = caption if caption and len(caption) <= CAPTION_LIMIT else None
    if callback and _is_photo(callback.message):
        try:
            await callback.message.edit_media(
                InputMediaPhoto(media=photo, caption=caption, parse_mode="HTML"), reply_markup=markup)
            return
        except Exception as e:  # noqa: BLE001
            if "not modified" in str(e).lower():
                return
            logger.debug("edit_media failed, sending instead: %s", e)
            await delete_message(callback.message)
            photo = BufferedInputFile(png, filename="card.png")
    await bot.send_photo(user_id, photo, caption=caption, reply_markup=markup)


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
