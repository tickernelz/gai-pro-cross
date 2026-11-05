import datetime as dt

import pytest

from gai_pro.card import (
    CardError,
    TAXFREE_PROFILES,
    brand,
    checkout_profile,
    expiry_valid,
    luhn_ok,
    validate_address,
    validate_card,
)


def test_luhn_accepts_visa_test_number():
    assert luhn_ok("4242424242424242")
    assert luhn_ok("4111 1111 1111 1111")


def test_luhn_rejects_transposed_digits():
    assert not luhn_ok("4242424242424243")


def test_brand_detection():
    assert brand("4242424242424242") == "visa"
    assert brand("5555555555554444") == "mastercard"
    assert brand("2223003122003222") == "mastercard"
    assert brand("6011111111111117") == "unknown"


def test_expiry_window():
    today = dt.date(2026, 11, 5)
    assert expiry_valid("11", "26", today)
    assert expiry_valid("08", "29", today)
    assert not expiry_valid("10", "26", today)
    assert not expiry_valid("13", "29", today)


def test_validate_card_requires_known_brand_and_holder():
    with pytest.raises(CardError):
        validate_card({"number": "6011111111111117", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "X"})
    with pytest.raises(CardError):
        validate_card({"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": ""})


def test_taxfree_address_round_trip():
    profile = checkout_profile(
        {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"},
        TAXFREE_PROFILES["US-OR-97201"],
    )
    assert profile["postal_code"] == "97201"
    assert profile["province"] == "OR"
    assert profile["sales_tax_exempt_state"] is True
    assert profile["last4"] == "4242"


def test_address_state_zip_mismatch_is_rejected():
    with pytest.raises(CardError):
        validate_address({"address_line1": "1 Main", "locality": "Portland", "province": "OR", "postal_code": "10001", "country": "US"})


def test_address_rejects_non_us_country():
    with pytest.raises(CardError):
        validate_address({"address_line1": "1 Main", "locality": "Toronto", "province": "ON", "postal_code": "97201", "country": "CA"})
