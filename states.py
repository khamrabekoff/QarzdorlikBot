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
    entering_note = State()
    # Typed "Ali 500000" from the home screen; waiting for which way round.
    choosing_type = State()


class Pay(StatesGroup):
    entering_amount = State()


class Edit(StatesGroup):
    # One state for every field; which field is kept in the FSM data.
    value = State()


class Admin(StatesGroup):
    broadcast = State()
