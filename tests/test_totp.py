import pytest

from gai_pro.totp import is_valid_secret, normalize_secret, seconds_remaining, totp, verify

SECRET = "JBSWY3DPEHPK3PXP"


def test_rfc6238_sha1_vector():
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59, digits=8, period=30) == "94287082"
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=1111111109, digits=8, period=30) == "07081804"


def test_normalizes_case_and_padding():
    assert normalize_secret("jbswy3dpehpk3pxp==") == SECRET


def test_rejects_short_or_non_base32_secret():
    assert not is_valid_secret("SHORT")
    assert not is_valid_secret("!!!!!!!!!!!!!!!!!")


def test_errors_on_invalid_secret():
    with pytest.raises(ValueError):
        totp("nope")


def test_verify_accepts_adjacent_window_only():
    now = 1_800_000_000
    code = totp(SECRET, at=now)
    assert verify(SECRET, code, at=now)
    assert verify(SECRET, code, at=now + 30)
    assert not verify(SECRET, code, at=now + 300)


def test_seconds_remaining_is_in_range():
    assert 1 <= seconds_remaining(1_800_000_000) <= 30
