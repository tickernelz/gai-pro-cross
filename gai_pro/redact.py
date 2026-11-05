"""Redaction of credentials before anything reaches a log."""

from __future__ import annotations

import re

_CARD = re.compile(r"(?<!\d)(\d[\d \-]{11,25}\d)(?!\d)")
_TOTP = re.compile(r"\b[A-Z2-7]{16,}={0,6}\b")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|totp|cvc|cvv|token)\b\s*[:=]\s*(\S+)"
)


def mask_card(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8:
        return "****"
    return f"**** **** **** {digits[-4:]}"


def mask_email(value: str) -> str:
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    head = local[0] if local else ""
    return f"{head}***@{domain}"


def _mask_card_match(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(1))
    if len(digits) < 13:
        return match.group(0)
    return mask_card(digits)


def redact(text: str) -> str:
    if not text:
        return ""
    out = _KV_SECRET.sub(lambda m: f"{m.group(1)}: ***", text)
    out = _CARD.sub(_mask_card_match, out)
    out = _TOTP.sub("***", out)
    out = _EMAIL.sub(lambda m: mask_email(m.group(0)), out)
    return out


def redact_obj(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    return value
