import decimal

import pytest

from app.views import normalize_phone, parse_money_field, valid_contact_name, valid_person_name


@pytest.mark.parametrize("value,expected", [
    ("0", decimal.Decimal("0.00")),
    ("12", decimal.Decimal("12.00")),
    ("12.3", decimal.Decimal("12.30")),
    ("9999999999.99", decimal.Decimal("9999999999.99")),
])
def test_money_field_accepts_valid_amounts(value, expected):
    assert parse_money_field(value) == expected


@pytest.mark.parametrize("value", ["", "-1", "1.001", "NaN", "Infinity", "1e2", "1_0", "99999999999.00"])
def test_money_field_rejects_invalid_amounts(value):
    with pytest.raises(ValueError):
        parse_money_field(value)


def test_payment_amount_must_be_greater_than_zero():
    with pytest.raises(ValueError):
        parse_money_field("0.00", allow_zero=False, field_name="Payment amount")


def test_phone_normalization_accepts_only_ten_digit_mobile_numbers():
    assert normalize_phone("9876543210") == "9876543210"


@pytest.mark.parametrize("phone", [
    "12345", "1234567890123456", "abc9876543", "+---()", "98765 43210",
    "+919876543210", "1234567890", "0000000000", " 9876543210",
])
def test_phone_normalization_rejects_invalid_numbers(phone):
    with pytest.raises(ValueError):
        normalize_phone(phone)


def test_person_name_supports_unicode_and_common_punctuation():
    assert valid_person_name("Amlan Sekhar Das")
    assert valid_person_name("Jean-Luc O'Neil")
    assert valid_person_name("श्याम कुमार")
    assert not valid_person_name("Amlan123")
    assert not valid_person_name("  ")


def test_supplier_contact_name_allows_only_letters_and_spaces():
    assert valid_contact_name("Amlan Sekhar Das")
    assert valid_contact_name("শ্যাম কুমার")
    assert valid_contact_name("")
    assert not valid_contact_name("Amlan Das2")
    assert not valid_contact_name("Amlan-Das")
    assert not valid_contact_name("Amlan Das.")
