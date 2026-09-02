"""Sharing a debt with the other party, and referral invites.

This is the growth engine. A debt recorded by one person is a private note;
a debt *confirmed by both* is an agreement - and getting that confirmation
requires the other person to open the bot. Every shared debt is therefore an
invitation that arrives with a reason to accept it.

Editing stays one-sided on purpose: only the owner can change or close a debt.
Two people editing the same row would need conflict rules nobody wants to
think about, and the counterparty already gets the value (visibility and
reminders) without write access.
"""
import logging

from aiogram import Bot, F, Router, types
from aiogram.filters import CommandObject, CommandStart

import database as db
import keyboards as kb
import views
from handlers.start import show_home
from utils import fmt_amount, fmt_date, i18n

router = Router()
logger = logging.getLogger(__name__)


async def _bot_username(bot: Bot):
    me = await bot.me()
    return me.username


# ---------- owner side: send the invite ----------

@router.callback_query(F.data.startswith("share:"))
async def share_debt(callback: types.CallbackQuery, bot: Bot):
    debt_id = int(callback.data.split(":", 1)[1])
    lang = await db.get_user_lang(callback.from_user.id)
    debt = await db.get_debt(debt_id)
    if not debt or debt["user_id"] != callback.from_user.id:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return

    token = await db.create_share_token(debt_id, callback.from_user.id)
    if not token:
        await callback.answer(i18n.get("err_not_found", lang), show_alert=True)
        return

    link = f"https://t.me/{await _bot_username(bot)}?start=d_{token}"
    await callback.answer()
    await callback.message.answer(
        i18n.get("share_ready", lang, name=debt["person_name"])
    )
    # Sent as its own plain message so it can be forwarded or copied whole.
    await callback.message.answer(
        i18n.get(
            "share_text", lang,
            amount=fmt_amount(debt["amount"]),
            currency=debt["currency"],
            date=fmt_date(debt["due_date"]) or i18n.get("no_deadline", lang),
            link=link,
        ),
        disable_web_page_preview=True,
    )


# ---------- counterparty side: confirm or decline ----------

@router.message(CommandStart(deep_link=True))
async def deep_link_start(message: types.Message, command: CommandObject, bot: Bot):
    """Handles both `d_<token>` (a shared debt) and `ref_<id>` (an invite)."""
    payload = (command.args or "").strip()
    user = await db.get_user(message.from_user.id)
    is_new = not (user and user["language"])
    lang = (user["language"] if user and user["language"] else "uz")

    await db.upsert_user(message.from_user.id, username=message.from_user.username)

    if payload.startswith("ref_"):
        try:
            await db.record_referral(message.from_user.id, int(payload[4:]), "ref_link")
        except ValueError:
            pass
        if is_new:
            await message.answer(i18n.get("choose_lang"), reply_markup=kb.lang_kb())
        else:
            await show_home(message, message.from_user.id, lang)
        return

    if not payload.startswith("d_"):
        if is_new:
            await message.answer(i18n.get("choose_lang"), reply_markup=kb.lang_kb())
        else:
            await show_home(message, message.from_user.id, lang)
        return

    token = payload[2:]
    debt = await db.get_debt_by_token(token)
    if not debt:
        await message.answer(i18n.get("share_err_gone", lang))
        return
    if debt["user_id"] == message.from_user.id:
        await message.answer(i18n.get("share_err_own", lang))
        return

    await db.record_referral(message.from_user.id, debt["user_id"], "debt_share")

    owner = await db.get_user(debt["user_id"])
    owner_name = (owner["username"] and "@" + owner["username"]) or debt["person_name"]
    # The owner wrote it from their side, so the direction flips for the reader.
    direction = i18n.get(
        "you_owe_them" if debt["debt_type"] == "lent" else "they_owe_you", lang
    )

    await message.answer(
        i18n.get("confirm_share_title", lang) + "\n\n" +
        i18n.get(
            "confirm_share_body", lang,
            owner=owner_name,
            direction_text=direction,
            amount=fmt_amount(debt["amount"]),
            currency=debt["currency"],
            date=fmt_date(debt["due_date"]) or i18n.get("no_deadline", lang),
        ),
        reply_markup=kb.share_confirm_kb(token, lang),
    )


@router.callback_query(F.data.startswith("shyes:"))
async def accept_share(callback: types.CallbackQuery, bot: Bot):
    token = callback.data.split(":", 1)[1]
    lang = await db.get_user_lang(callback.from_user.id)

    ok, result = await db.accept_share(token, callback.from_user.id)
    if not ok:
        await callback.answer(
            i18n.get({"own_debt": "share_err_own", "taken": "share_err_taken"}
                     .get(result, "share_err_gone"), lang),
            show_alert=True,
        )
        return

    debt = result
    await callback.answer()
    await callback.message.edit_text(i18n.get("share_ok", lang))

    who = callback.from_user.username and "@" + callback.from_user.username \
        or (callback.from_user.first_name or "?")
    owner_lang = await db.get_user_lang(debt["user_id"])
    try:
        await bot.send_message(
            debt["user_id"],
            i18n.get("notify_accepted", owner_lang, name=who,
                     amount=fmt_amount(debt["amount"]), currency=debt["currency"]),
        )
    except Exception as e:  # noqa: BLE001 - owner may have blocked the bot
        logger.info("Could not notify owner %s: %s", debt["user_id"], e)

    await show_home(callback.message, callback.from_user.id, lang)


@router.callback_query(F.data.startswith("shno:"))
async def decline_share(callback: types.CallbackQuery, bot: Bot):
    token = callback.data.split(":", 1)[1]
    lang = await db.get_user_lang(callback.from_user.id)
    debt = await db.get_debt_by_token(token)
    await db.decline_share(token)
    await callback.answer()
    await callback.message.edit_text(i18n.get("share_no", lang))

    if debt:
        who = callback.from_user.username and "@" + callback.from_user.username \
            or (callback.from_user.first_name or "?")
        owner_lang = await db.get_user_lang(debt["user_id"])
        try:
            await bot.send_message(
                debt["user_id"],
                i18n.get("notify_declined", owner_lang, name=who,
                         amount=fmt_amount(debt["amount"]), currency=debt["currency"]),
            )
        except Exception as e:  # noqa: BLE001
            logger.info("Could not notify owner %s: %s", debt["user_id"], e)


# ---------- referral link ----------

@router.callback_query(F.data == "invite")
async def show_invite(callback: types.CallbackQuery, bot: Bot):
    lang = await db.get_user_lang(callback.from_user.id)
    link = f"https://t.me/{await _bot_username(bot)}?start=ref_{callback.from_user.id}"
    count = await db.count_referrals(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(
        i18n.get("invite_text", lang, link=link, count=count),
        disable_web_page_preview=True,
    )
