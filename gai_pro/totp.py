"""RFC 6238 TOTP generation for 2FA codes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def normalize_secret(secret: str) -> str:
    cleaned = "".join(ch for ch in (secret or "").upper() if not ch.isspace())
    cleaned = cleaned.replace("-", "").replace("=", "")
    return cleaned


def is_valid_secret(secret: str) -> bool:
    cleaned = normalize_secret(secret)
    if len(cleaned) < 16:
        return False
    return all(ch in ALPHABET for ch in cleaned)


def _b32decode(secret: str) -> bytes:
    cleaned = normalize_secret(secret)
    pad = (-len(cleaned)) % 8
    return base64.b32decode(cleaned + "=" * pad, casefold=True)


def totp(secret: str, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    if not is_valid_secret(secret):
        raise ValueError("invalid totp secret")
    counter = int((at if at is not None else time.time()) // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(_b32decode(secret), msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def seconds_remaining(at: float | None = None, period: int = 30) -> int:
    return period - int((at if at is not None else time.time()) % period)


def verify(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    now = at if at is not None else time.time()
    code = (code or "").strip()
    for step in range(-window, window + 1):
        try:
            if hmac.compare_digest(totp(secret, now + step * 30), code):
                return True
        except ValueError:
            return False
    return False
