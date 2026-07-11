"""Unit tests for the password strength policy.

Length + blocklist + email-similarity, NOT complexity classes.
"""

from app.domains.identity_access.password_policy import check_password_strength


def test_accepts_a_decent_password():
    assert check_password_strength("correct horse battery") is None
    assert check_password_strength("TestPass123!") is None


def test_rejects_common_password():
    reason = check_password_strength("password")
    assert reason is not None
    assert "common" in reason.lower()


def test_rejects_common_numeric():
    reason = check_password_strength("12345678")
    assert reason is not None
    assert "common" in reason.lower()


def test_blocklist_is_case_insensitive_and_stripped():
    assert check_password_strength("  PassWord  ") is not None
    assert check_password_strength("QWERTY") is not None


def test_rejects_too_short():
    reason = check_password_strength("short")
    assert reason is not None
    assert "at least 8" in reason.lower()


def test_rejects_too_long():
    reason = check_password_strength("a" * 73)
    assert reason is not None
    assert "72" in reason


def test_rejects_email_local_part_in_password():
    # local-part "samsmith" is contained in the password
    reason = check_password_strength("samsmith-secret-99", email="samsmith@company.com")
    assert reason is not None
    assert "email" in reason.lower()


def test_password_contained_in_local_part():
    # password lowercased is contained in the local-part
    reason = check_password_strength("jonathan", email="jonathanx@company.com")
    # "jonathan" is not blocklisted; it's contained in "jonathanx"
    assert reason is not None
    assert "email" in reason.lower()


def test_short_local_part_does_not_trigger_similarity():
    # local-part "ab" is < 3 chars — must not reject
    assert check_password_strength("ab-strong-passphrase", email="ab@company.com") is None


def test_email_none_skips_similarity():
    assert check_password_strength("a-decent-passphrase", email=None) is None
