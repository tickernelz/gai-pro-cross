"""Card and tax-region validation for the checkout profile."""

from __future__ import annotations

import datetime as _dt
import re

TAXFREE_PROFILES = {
    "US-OR-97201": {
        "address_line1": "700 SW 5th Ave",
        "locality": "Portland",
        "province": "OR",
        "postal_code": "97201",
        "country": "US",
    }
}

NO_SALES_TAX_STATES = {"OR", "MT", "NH", "DE", "AK"}

SANDBOX_CARD = {
    "number": "4242424242424242",
    "exp_month": "12",
    "exp_year": "2030",
    "cvc": "123",
    "holder": "SANDBOX",
}


class CardError(ValueError):
    pass


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def luhn_ok(number: str) -> bool:
    body = digits(number)
    if not 13 <= len(body) <= 19:
        return False
    total = 0
    for index, ch in enumerate(reversed(body)):
        value = int(ch)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def brand(number: str) -> str:
    body = digits(number)
    if not body:
        return "unknown"
    if body[0] == "4":
        return "visa"
    first_two = int(body[:2]) if len(body) >= 2 else 0
    first_four = int(body[:4]) if len(body) >= 4 else 0
    if 51 <= first_two <= 55 or 2221 <= first_four <= 2720:
        return "mastercard"
    return "unknown"


def expiry_valid(month: str, year: str, now: _dt.date | None = None) -> bool:
    today = now or _dt.date.today()
    try:
        month_i = int(str(month).strip())
        year_i = int(str(year).strip())
    except (TypeError, ValueError):
        return False
    if year_i < 100:
        year_i += 2000
    if not 1 <= month_i <= 12:
        return False
    return (year_i, month_i) >= (today.year, today.month)


def validate_card(card: dict) -> dict:
    number = digits(card.get("number", ""))
    if not luhn_ok(number):
        raise CardError("card number fails Luhn check")
    detected = brand(number)
    if detected == "unknown":
        raise CardError("card is not Visa or Mastercard")
    if not expiry_valid(card.get("exp_month", ""), card.get("exp_year", "")):
        raise CardError("card expiry is in the past")
    cvc = digits(card.get("cvc", ""))
    if len(cvc) not in (3, 4):
        raise CardError("cvc must be 3 or 4 digits")
    if not (card.get("holder", "") or "").strip():
        raise CardError("cardholder name is required")
    return {"brand": detected, "last4": number[-4:]}


def validate_address(address: dict) -> dict:
    province = (address.get("province", "") or "").strip().upper()
    postal = (address.get("postal_code", "") or "").strip()
    locality = (address.get("locality", "") or "").strip()
    line1 = (address.get("address_line1", "") or "").strip()
    country = (address.get("country", "US") or "US").strip().upper()
    if not line1 or not locality:
        raise CardError("street address and city are required")
    if country != "US":
        raise CardError("billing country must be US for this profile")
    if not re.fullmatch(r"\d{5}", postal):
        raise CardError("postal code must be 5 digits")
    if not re.fullmatch(r"[A-Z]{2}", province):
        raise CardError("state must be a two letter code")
    if province == "OR" and not postal.startswith("97"):
        raise CardError("postal code does not belong to the claimed state")
    return {
        "province": province,
        "postal_code": postal,
        "locality": locality,
        "address_line1": line1,
        "country": country,
        "sales_tax_exempt_state": province in NO_SALES_TAX_STATES,
    }


def checkout_profile(card: dict, address: dict) -> dict:
    card_info = validate_card(card)
    address_info = validate_address(address)
    return {
        "brand": card_info["brand"],
        "last4": card_info["last4"],
        "postal_code": address_info["postal_code"],
        "province": address_info["province"],
        "sales_tax_exempt_state": address_info["sales_tax_exempt_state"],
    }
