from app.auth import EMAIL_PATTERN, PHONE_PATTERN


def test_registration_accepts_valid_email_addresses():
    assert EMAIL_PATTERN.fullmatch("owner@example.com")
    assert EMAIL_PATTERN.fullmatch("pharmacy.owner+demo@example.co.in")


def test_registration_rejects_malformed_email_addresses():
    assert not EMAIL_PATTERN.fullmatch("")
    assert not EMAIL_PATTERN.fullmatch("owner@localhost")
    assert not EMAIL_PATTERN.fullmatch("owner.example.com")


def test_registration_phone_requires_ten_digit_mobile_number():
    assert PHONE_PATTERN.fullmatch("9876543210")
    assert not PHONE_PATTERN.fullmatch("98765 43210")
    assert not PHONE_PATTERN.fullmatch("1234567890")
