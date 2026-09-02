from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from utils import i18n

def get_lang_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.get("choose_lang", "ru")), KeyboardButton(text=i18n.get("choose_lang_uz", "uz"))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_phone_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.get("share_phone", lang), request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_main_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.get("menu_add_debt", lang))],
            [KeyboardButton(text=i18n.get("menu_i_owe", lang)), KeyboardButton(text=i18n.get("menu_owe_me", lang))],
            [KeyboardButton(text=i18n.get("menu_settings", lang))]
        ],
        resize_keyboard=True
    )

def get_currency_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="USD"), KeyboardButton(text="UZS")],
            [KeyboardButton(text=i18n.get("btn_back", lang))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
def get_date_options_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.get("days_3", lang)), KeyboardButton(text=i18n.get("week_1", lang))],
            [KeyboardButton(text=i18n.get("days_10", lang)), KeyboardButton(text=i18n.get("month_1", lang))],
            [KeyboardButton(text=i18n.get("btn_end_week", lang)), KeyboardButton(text=i18n.get("btn_end_month", lang))],
             [KeyboardButton(text=i18n.get("btn_back", lang))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
def get_names_kb(names, lang):
    # names is a list of strings
    keyboard = []
    # Add names in rows of 2
    row = []
    for name in names:
        row.append(KeyboardButton(text=name))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([KeyboardButton(text=i18n.get("btn_back", lang))])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

def get_back_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.get("btn_back", lang))]
        ],
        resize_keyboard=True
    )
