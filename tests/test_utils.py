"""Parsing and formatting - the parts people type into."""
from datetime import date, datetime, timedelta

import pytest

import views
from utils import (
    MAX_AMOUNT, clean_name, esc, fmt_amount, fmt_paid_csv, lang_from_code, now_local,
    parse_amount, parse_date, resolve_date_choice, split_name_amount, utc_to_local,
)


@pytest.mark.parametrize("text, expected", [
    ("500000", 500000),
    ("1 500 000", 1500000),
    ("500k", 500000),
    ("500 ming", 500000),
    ("1,5 mln", 1500000),
    ("2 млн", 2000000),
    ("1.5", 1.5),
    ("150 000 so'm", 150000),
])
def test_parse_amount_accepts_common_forms(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "-", str(MAX_AMOUNT * 10)])
def test_parse_amount_rejects_nonsense_and_typos(text):
    assert parse_amount(text) is None


@pytest.mark.parametrize("text, name, amount", [
    ("Ali 500000", "Ali", 500000),
    ("Vali aka 1,5 mln", "Vali aka", 1500000),       # two-word name survives
    ("500k Ali", "Ali", 500000),                     # amount first
    ("1,5 mln Sardor aka", "Sardor aka", 1500000),
    ("Ali", "Ali", None),
    ("Али 300к", "Али", 300000),
])
def test_split_name_amount(text, name, amount):
    assert split_name_amount(text) == (name, amount)


def test_clean_name_limits_length_and_whitespace():
    assert clean_name("  Ali   aka ") == "Ali aka"
    assert clean_name("x" * 41) is None
    assert clean_name("   ") is None


def test_parse_date_bare_day_month_in_the_past_means_next_year():
    today = now_local().date()
    past = today - timedelta(days=10)
    iso = parse_date(f"{past.day:02d}.{past.month:02d}")
    assert date.fromisoformat(iso) > today


@pytest.mark.parametrize("text", ["ertaga", "завтра", "tomorrow"])
def test_parse_date_words(text):
    assert parse_date(text) == (now_local().date() + timedelta(days=1)).isoformat()


def test_parse_date_rejects_garbage():
    assert parse_date("32.13") is None
    assert parse_date("hello") is None


def test_resolve_date_choice():
    assert resolve_date_choice("none") is None
    assert resolve_date_choice("t1") == (now_local().date() + timedelta(days=1)).isoformat()
    # Buttons from older versions or garbage fall back to a week, not a crash.
    assert resolve_date_choice("zzz") == (now_local().date() + timedelta(weeks=1)).isoformat()


def test_utc_to_local_is_tashkent():
    assert utc_to_local("2026-10-13 20:30:00") == datetime(2026, 10, 14, 1, 30)
    assert utc_to_local(None) is None


def test_formatting():
    assert fmt_amount(1234567) == "1 234 567"
    assert fmt_amount(12.5) == "12.50"
    assert fmt_paid_csv(1500000) == "1500000"
    assert fmt_paid_csv(12.5) == "12,50"


def test_lang_from_code():
    assert lang_from_code("ru") == "ru"
    assert lang_from_code("ru-RU") == "ru"
    assert lang_from_code("uz") == "uz"
    assert lang_from_code(None) == "uz"


def test_esc_neutralises_html():
    assert esc("<b>A&B</b>") == "&lt;b&gt;A&amp;B&lt;/b&gt;"


def test_debt_card_escapes_user_input():
    from tests.conftest import assert_valid_html
    debt = {"id": 1, "user_id": 1, "debt_type": "lent", "amount": 100, "paid_amount": 0,
            "currency": "so'm", "person_name": "<script>&", "due_date": None,
            "description": "</b> note", "share_status": None, "created_at": "2026-10-01 10:00:00"}
    text = views.debt_card(debt, "uz")
    assert "&lt;script&gt;&amp;" in text
    assert_valid_html(text)


def test_cards_stay_small():
    """Every card goes through PythonAnywhere's slow proxy; size is speed."""
    import cards
    debts = [{"id": i, "user_id": 1, "debt_type": "lent", "view_type": "lent", "is_mine": 1,
              "amount": 1000 * i, "paid_amount": 0, "currency": "so'm", "person_name": f"Odam {i}",
              "due_date": None, "description": "", "share_status": None, "created_at": None,
              "owner_username": None, "owner_first_name": None} for i in range(1, 9)]
    totals = [{"debt_type": "lent", "currency": "so'm", "remaining": 36000}]
    png = cards.render_home(totals, views.group_people(debts), "uz")
    assert len(png) < 60 * 1024
