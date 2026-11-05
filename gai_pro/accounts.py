"""Batch account import for the four line formats the original console accepts."""

from __future__ import annotations

import hashlib
import re

from .totp import is_valid_secret

_PROXY = re.compile(r"^(?:https?|socks5)://|^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$", re.I)
_DELIMITERS = ("----", "\u2014\u2014", "\u2014", "|", "\t", ",", ";", "/")


def _split(line: str) -> list[str]:
    for delim in _DELIMITERS:
        if delim in line:
            parts = [p.strip() for p in line.split(delim)]
            return [p for p in parts if p]
    return [p for p in re.split(r"\s+", line.strip()) if p]


def account_id(email: str) -> str:
    return "acc_" + hashlib.sha1(email.strip().lower().encode()).hexdigest()[:10]


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        return cleaned
    local, _, domain = cleaned.partition("@")
    return f"{local.replace('.', '')}@{domain}"


def parse_line(line: str) -> dict | None:
    parts = _split(line)
    if len(parts) < 2:
        return None

    label = ""
    if "@" in parts[0]:
        email, password, rest = parts[0], parts[1], parts[2:]
    elif len(parts) >= 3 and "@" in parts[1]:
        label, email, password, rest = parts[0], parts[1], parts[2], parts[3:]
    else:
        return None

    if "@" not in email:
        return None

    recovery_email = ""
    totp_secret = ""
    proxy = ""

    for token in rest:
        clean = token.replace(" ", "")
        if _PROXY.match(token):
            proxy = token
        elif "@" in token and token.lower() != email.lower() and not recovery_email:
            recovery_email = token
        elif not totp_secret and is_valid_secret(clean):
            totp_secret = clean

    return {
        "id": account_id(email),
        "label": label,
        "email": email.strip(),
        "password": password,
        "recovery_email": recovery_email,
        "totp_secret": totp_secret,
        "proxy": proxy,
    }


def parse_batch(raw_text: str) -> tuple[list[dict], list[str]]:
    parsed: list[dict] = []
    rejected: list[str] = []
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        item = parse_line(line)
        if item is None:
            rejected.append(line)
        else:
            parsed.append(item)
    return parsed, rejected


def merge_accounts(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    by_key = {normalize_email(a["email"]): i for i, a in enumerate(existing)}
    added = 0
    for item in incoming:
        key = normalize_email(item["email"])
        if key in by_key:
            target = existing[by_key[key]]
            target["password"] = item["password"]
            if item["recovery_email"]:
                target["recovery_email"] = item["recovery_email"]
            if item["totp_secret"]:
                target["totp_secret"] = item["totp_secret"]
            if item["proxy"]:
                target["proxy"] = item["proxy"]
            continue
        existing.append(new_account_record(item))
        by_key[key] = len(existing) - 1
        added += 1
    return existing, added


def new_account_record(item: dict) -> dict:
    return {
        "id": item["id"],
        "label": item.get("label", ""),
        "email": item["email"],
        "password": item["password"],
        "recovery_email": item.get("recovery_email", ""),
        "totp_secret": item.get("totp_secret", ""),
        "proxy": item.get("proxy", ""),
        "status": "pending",
        "current_step": 0,
        "step_message": "imported, not started",
        "expiry_date": "-",
        "last_screenshot": "",
        "layers_done": 0,
        "last_error": "",
    }
