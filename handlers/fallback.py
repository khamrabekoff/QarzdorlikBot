"""Whatever no other handler claimed. Included LAST.

Before this existed, anything typed outside a flow got no answer at all - the
log shows those updates as "not handled". Silence reads as "the bot is
broken". Now free text is put to use:

  - "Ali 500000"  -> start a debt with name and amount already filled in
  - "Ali"         -> search the person's debts
  - anything else -> a one-line hint
"""
import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import views
from states import AddDebt
from utils import esc, fmt_amount, i18n, split_name_amount

router = Router()
logger = logging.getLogger(__name__)

# Never answer chatter in groups the bot was added to.
router.message.filter(F.chat.type == "private")


@router.message(AddDebt.choosing_type, F.text)
async def retyped_while_choosing(message: types.Message, state: FSMContext):
    """They typed again instead of tapping a direction - start over with the
    new text rather than holding on to the old one."""
    await state.clear()
    await free_text(message, state)


@router.message(F.text & ~F.text.startswith("/"))
async def free_text(message: types.Message, state: FSMContext):
    # Every live flow has its own text handler ahead of this one, so a state
    # seen here is a leftover (e.g. the removed phone-number step).
    await state.clear()
    uid = message.from_user.id
    lang = await db.get_user_lang(uid)
    text = message.text.strip()

    name, amount = split_name_amount(text)
    if name and amount:
        await state.set_state(AddDebt.choosing_type)
        await state.update_data(person_name=name, amount=amount, lang=lang)
        await message.answer(
            i18n.get("quick_add_ask", lang, name=esc(name), amount=fmt_amount(amount)),
            reply_markup=kb.quick_type_kb(name, lang),
        )
        return

    found = await db.search_debts(uid, text) if name else []
    if found:
        await message.answer(views.search_card(found, text, lang),
                             reply_markup=kb.debt_numbers_kb(found))
        return

    key = "search_empty" if name else "unknown_input"
    await message.answer(i18n.get(key, lang, query=esc(text[:40])), reply_markup=kb.main_kb(lang))


@router.message()
async def anything_else(message: types.Message, state: FSMContext):
    """Stickers, photos, voice, unknown /commands."""
    lang = await db.get_user_lang(message.from_user.id)
    await message.answer(i18n.get("unknown_input", lang), reply_markup=kb.main_kb(lang))


@router.callback_query()
async def stale_callback(callback: types.CallbackQuery, state: FSMContext):
    """A button from a message the current flow no longer matches (e.g. a
    date button after the conversation expired). Say so instead of leaving
    the button spinning."""
    lang = await db.get_user_lang(callback.from_user.id)
    await callback.answer(i18n.get("stale_button", lang), show_alert=True)
