from app.auth import EMAIL_PATTERN


def test_registration_accepts_valid_email_addresses():
    assert EMAIL_PATTERN.fullmatch("owner@example.com")
    assert EMAIL_PATTERN.fullmatch("pharmacy.owner+demo@example.co.in")


def test_registration_rejects_malformed_email_addresses():
    assert not EMAIL_PATTERN.fullmatch("")
    assert not EMAIL_PATTERN.fullmatch("owner@localhost")
    assert not EMAIL_PATTERN.fullmatch("owner.example.com")
