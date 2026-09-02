from aiogram.fsm.state import State, StatesGroup

class Registration(StatesGroup):
    choosing_lang = State()
    sending_phone = State()

class AddDebt(StatesGroup):
    choosing_type = State() # Lent or Borrowed
    entering_name = State()
    entering_amount = State()
    choosing_currency = State()
    entering_date = State()
    entering_desc = State()
    paying_amount = State() # For partial payments

class AdminStates(StatesGroup):
    waiting_for_broadcast_msg = State()
