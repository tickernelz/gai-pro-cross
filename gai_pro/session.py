"""Stored sessions: one storage_state.json per account, written only by a login."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .paths import Layout, secure_file

SESSION_MISSING = "sesi belum ada, jalankan Login manual (no session yet, run Login manual)"

GOOGLE_AUTH_COOKIE_NAMES = frozenset({"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "HSID", "SSID"})


def has_google_auth_cookie(cookies) -> bool:
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        if cookie.get("name") not in GOOGLE_AUTH_COOKIE_NAMES:
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain == "google.com" or domain.endswith(".google.com"):
            return True
    return False


def session_authenticated(state: dict | None) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("mode", "live") != "live":
        return True
    return has_google_auth_cookie(state.get("cookies"))


def session_path(layout: Layout, account_id: str) -> Path:
    return layout.session_file(account_id)


def read_session(layout: Layout, account_id: str) -> dict | None:
    path = layout.session_file(account_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def has_session(layout: Layout, account_id: str, mode: str | None = None) -> bool:
    state = read_session(layout, account_id)
    if state is None:
        return False
    if mode is not None and state.get("mode", "live") != mode:
        return False
    return session_authenticated(state)


def write_session(layout: Layout, account_id: str, state: dict) -> Path:
    path = layout.session_file(account_id)
    payload = dict(state)
    payload.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    secure_file(tmp)
    os.replace(tmp, path)
    secure_file(path)
    return path


def clear_session(layout: Layout, account_id: str) -> bool:
    path = layout.session_file(account_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
