from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from states import AddDebt, Registration
from utils import i18n
import keyboards as kb
import database as db
import datetime

router = Router()

# Global Back Handler and Settings Handler
@router.message(F.text.in_([i18n.get("btn_back", "uz"), i18n.get("btn_back", "ru")]))
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    lang = user[2] if user else 'uz'
    await message.answer(i18n.get("main_menu", lang), reply_markup=kb.get_main_kb(lang))

@router.message(F.text.in_([i18n.get("menu_settings", "uz"), i18n.get("menu_settings", "ru")]))
async def settings(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(Registration.choosing_lang)
    msg = i18n.get("welcome", "uz") 
    await message.answer(msg, reply_markup=kb.get_lang_kb())

# --- ADD DEBT FLOW ---

@router.message(F.text.in_([i18n.get("menu_add_debt", "uz"), i18n.get("menu_add_debt", "ru")]))
async def start_add_debt(message: types.Message, state: FSMContext):
    await state.clear() 
    user = await db.get_user(message.from_user.id)
    lang = user[2]
    await state.update_data(lang=lang)
    
    if lang == 'ru':
        markup = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="Я дал в долг")],
            [types.KeyboardButton(text="Я взял в долг")],
            [types.KeyboardButton(text=i18n.get("btn_back", lang))]
        ], resize_keyboard=True, one_time_keyboard=True)
        await message.answer("Вы дали деньги или взяли?", reply_markup=markup)
    else:
        markup = types.ReplyKeyboardMarkup(keyboard=[
             [types.KeyboardButton(text="Men qarz berdim")],
             [types.KeyboardButton(text="Men qarz oldim")],
             [types.KeyboardButton(text=i18n.get("btn_back", lang))]
        ], resize_keyboard=True, one_time_keyboard=True)
        await message.answer("Siz qarz berdingizmi yoki oldingizmi?", reply_markup=markup)

    await state.set_state(AddDebt.choosing_type)

@router.message(AddDebt.choosing_type)
async def process_type(message: types.Message, state: FSMContext):
    text = message.text
    d_type = 'lent'
    if "oldim" in text.lower() or "взял" in text.lower():
        d_type = 'borrowed'
    
    await state.update_data(debt_type=d_type)
    data = await state.get_data()
    lang = data['lang']
    
    recent_names = await db.get_recent_names(message.from_user.id)
    
    q = i18n.get("debt_who", lang) if d_type == 'lent' else i18n.get("debt_who_borrowed", lang)
    await message.answer(q, reply_markup=kb.get_names_kb(recent_names, lang))
    await state.set_state(AddDebt.entering_name)

@router.message(AddDebt.entering_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(person_name=message.text)
    data = await state.get_data()
    lang = data['lang']
    await message.answer(i18n.get("debt_amount", lang), reply_markup=kb.get_back_kb(lang))
    await state.set_state(AddDebt.entering_amount)

@router.message(AddDebt.entering_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        # Improved parsing: remove spaces, comma to dot
        txt = message.text.replace(' ', '').replace(',', '.')
        amount = float(txt)
        if amount <= 0: raise ValueError
    except ValueError:
        data = await state.get_data()
        await message.answer(i18n.get("err_invalid_amount", data['lang']))
        return

    await state.update_data(amount=amount)
    data = await state.get_data()
    await message.answer(i18n.get("debt_currency", data['lang']), reply_markup=kb.get_currency_kb(data['lang']))
    await state.set_state(AddDebt.choosing_currency)

@router.message(AddDebt.choosing_currency)
async def process_currency(message: types.Message, state: FSMContext):
    await state.update_data(currency=message.text)
    data = await state.get_data()
    await message.answer(i18n.get("debt_date", data['lang']), reply_markup=kb.get_date_options_kb(data['lang']))
    await state.set_state(AddDebt.entering_date)

@router.message(AddDebt.entering_date)
async def process_date(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data['lang']
    text = message.text
    final_date_str = ""
    
    today = datetime.datetime.now()
    if text == i18n.get("days_3", lang):
        delta = datetime.timedelta(days=3)
        final_date_str = (today + delta).strftime("%d.%m.%Y")
    elif text == i18n.get("week_1", lang):
        delta = datetime.timedelta(weeks=1)
        final_date_str = (today + delta).strftime("%d.%m.%Y")
    elif text == i18n.get("days_10", lang):
        delta = datetime.timedelta(days=10)
        final_date_str = (today + delta).strftime("%d.%m.%Y")
    elif text == i18n.get("month_1", lang):
        delta = datetime.timedelta(days=30)
        final_date_str = (today + delta).strftime("%d.%m.%Y")
    elif text == i18n.get("btn_end_month", lang):
        import calendar
        last_day = calendar.monthrange(today.year, today.month)[1]
        if today.day < last_day:
            final_date_str = datetime.date(today.year, today.month, last_day).strftime("%d.%m.%Y")
        else:
            # Go to last day of next month
            year, next_month = today.year, today.month + 1
            if next_month > 12:
                year, next_month = year + 1, 1
            last_day_next = calendar.monthrange(year, next_month)[1]
            final_date_str = datetime.date(year, next_month, last_day_next).strftime("%d.%m.%Y")
    elif text == i18n.get("btn_end_week", lang):
        # Coming Sunday
        days_ahead = 6 - today.weekday()
        if days_ahead == 0: days_ahead = 7
        final_date_str = (today + datetime.timedelta(days=days_ahead)).strftime("%d.%m.%Y")
    else:
        # Flexible Date Parsing
        date_text = text.strip()
        # Normalize separators
        for char in ['-', '/', ' ']:
            date_text = date_text.replace(char, '.')
        
        parts = date_text.split('.')
        try:
             current_year = datetime.datetime.now().year
             d, m, y = 0, 0, 0
             
             if len(parts) == 2:
                 # DD.MM -> assume current year
                 d, m = int(parts[0]), int(parts[1])
                 y = current_year
             elif len(parts) == 3:
                 d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                 if y < 100: y += 2000 # 23 -> 2023
             else:
                 raise ValueError
                 
             # Validate date
             valid_date = datetime.date(y, m, d)
             final_date_str = valid_date.strftime("%d.%m.%Y")
             
        except ValueError:
            await message.answer(i18n.get("err_invalid_date", lang))
            return

    await state.update_data(due_date=final_date_str)
    data = await state.get_data() 
    
    try:
        await db.add_debt(
            user_id=message.from_user.id,
            debt_type=data['debt_type'],
            amount=data['amount'],
            currency=data['currency'],
            person_name=data['person_name'],
            due_date=final_date_str,
            description="" 
        )
    except Exception as e:
        await message.answer(f"Error saving debt: {e}")
        return
    
    await message.answer(i18n.get("debt_saved", lang), reply_markup=kb.get_main_kb(lang))
    await state.clear()

# --- LIST DEBTS ---

async def send_debt_list(message: types.Message, user_id: int, debt_type: str):
    user = await db.get_user(user_id)
    lang = user[2]
    debts = await db.get_active_debts(user_id, debt_type)
    
    if not debts:
        await message.answer(i18n.get("debt_list_empty", lang))
        return

    header_key = "owe_me_header" if debt_type == 'lent' else "i_owe_header"
    await message.answer(i18n.get(header_key, lang), parse_mode="HTML")
    
    totals = {}
    from utils import format_amount

    for d in debts:
        # Card View
        paid_amount = d['paid_amount'] or 0
        total_amount = d['amount']
        remaining = total_amount - paid_amount
        
        # Calculate totals
        curr = d['currency']
        totals[curr] = totals.get(curr, 0) + remaining

        if paid_amount > 0:
            # Bug fix: use remaining for the main amount, format both.
            txt = i18n.get("debt_item_partial", lang, 
                        total=format_amount(total_amount), 
                        remaining=format_amount(remaining),
                        paid=format_amount(paid_amount),
                        name=d['person_name'], 
                        currency=d['currency'], 
                        date=d['due_date'])
        else:
            txt = i18n.get("debt_item", lang, 
                        name=d['person_name'], 
                        amount=format_amount(total_amount), 
                        currency=d['currency'], 
                        date=d['due_date'])
        
        # Buttons: Partial | Full Close
        btn_close_full = i18n.get("btn_close_full", lang)
        if btn_close_full == "btn_close_full": 
            btn_close_full = "✅ Yopish" if lang=='uz' else "✅ Закрыть"

        btn_partial = i18n.get("partial_pay_btn", lang)

        markup = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text=btn_partial, callback_data=f"paypart_{d['id']}"),
                types.InlineKeyboardButton(text=btn_close_full, callback_data=f"payfull_{d['id']}")
            ]
        ])
        
        await message.answer(txt, reply_markup=markup, parse_mode="HTML")

    # Send Totals Summary
    if totals:
        total_strings = []
        for curr, val in totals.items():
            total_strings.append(f"<b>{format_amount(val)} {curr}</b>")
        
        div = " и " if lang == 'ru' else " va "
        totals_text = div.join(total_strings)
        summary = i18n.get("total_summary", lang, totals=totals_text)
        await message.answer(summary, parse_mode="HTML")

@router.message(F.text.in_([i18n.get("menu_owe_me", "uz"), i18n.get("menu_owe_me", "ru")]))
async def show_owe_me(message: types.Message, state: FSMContext):
    await state.clear()
    await send_debt_list(message, message.from_user.id, 'lent')

@router.message(F.text.in_([i18n.get("menu_i_owe", "uz"), i18n.get("menu_i_owe", "ru")]))
async def show_i_owe(message: types.Message, state: FSMContext):
    await state.clear()
    await send_debt_list(message, message.from_user.id, 'borrowed')

# --- CALLBACK HANDLER ---

@router.callback_query(F.data.startswith("payfull_"))
async def process_pay_full_callback(callback: types.CallbackQuery):
    debt_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    await db.mark_debt_paid(debt_id, user_id)
    
    user = await db.get_user(user_id)
    lang = user[2] if user else 'uz'
    
    await callback.answer(i18n.get("debt_closed", lang))
    await callback.message.delete()

@router.callback_query(F.data.startswith("paypart_"))
async def process_pay_partial_callback(callback: types.CallbackQuery, state: FSMContext):
    debt_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    # Save debt_id to state
    await state.set_state(AddDebt.paying_amount)
    await state.update_data(paying_debt_id=debt_id)
    
    user = await db.get_user(user_id)
    lang = user[2] if user else 'uz'
    await state.update_data(lang=lang)
    
    await callback.message.answer(i18n.get("enter_payment_amount", lang), reply_markup=kb.get_back_kb(lang))
    await callback.answer()

@router.message(AddDebt.paying_amount)
async def process_payment_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data['lang']
    debt_id = data['paying_debt_id']
    
    try:
        txt = message.text.replace(' ', '').replace(',', '.')
        amount = float(txt)
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer(i18n.get("err_invalid_amount", lang))
        return

    # Process Payment
    user_id = message.from_user.id
    
    # Check bounds
    debt = await db.get_debt(debt_id)
    if not debt:
        await message.answer("Error: Debt not found.")
        await state.clear()
        return

    current_paid = debt['paid_amount'] if debt['paid_amount'] else 0
    total = debt['amount']
    remaining = total - current_paid
    
    if amount > remaining:
         await message.answer(i18n.get("err_payment_too_large", lang, remaining=f"{remaining:g}", currency=debt['currency']))
         return
         
    success = await db.update_debt_payment(debt_id, user_id, amount)
    
    if success:
        new_remaining = remaining - amount
        if new_remaining <= 0:
            await message.answer(i18n.get("payment_full_success", lang), reply_markup=kb.get_main_kb(lang))
        else:
            await message.answer(i18n.get("payment_success", lang, remaining=f"{new_remaining:g}", currency=debt['currency']), reply_markup=kb.get_main_kb(lang))
            
    await state.clear()
