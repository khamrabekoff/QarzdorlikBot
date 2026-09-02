from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    sending_phone = State()


class AddDebt(StatesGroup):
    # "Ali 500000" in one message covers name+amount for most people; the
    # amount-only step is the fallback when the name came from a button or
    # the single line couldn't be split confidently.
    entering_who_amount = State()
    entering_amount = State()
    entering_date = State()


class Pay(StatesGroup):
    entering_amount = State()


class Admin(StatesGroup):
    broadcast = State()
