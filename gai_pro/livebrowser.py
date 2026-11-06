"""Camoufox-backed live browser: the only browser the live profile may use."""

from __future__ import annotations

import contextlib
import sys
from urllib.parse import unquote, urlsplit

CAMOUFOX_INSTALL = (
    "Camoufox belum terpasang (Camoufox is not installed). "
    "Jalankan: pip install -U camoufox lalu python -m camoufox fetch"
)


class LiveBrowserError(RuntimeError):
    """Raised when Camoufox cannot be imported or launched."""


def host_os():
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def proxy_settings(raw):
    value = (raw or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if not parts.hostname:
        return None
    server = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        server = f"{server}:{parts.port}"
    settings = {"server": server}
    if parts.username:
        settings["username"] = unquote(parts.username)
    if parts.password:
        settings["password"] = unquote(parts.password)
    return settings


class LiveBrowser:
    """One persistent Camoufox profile per account, headed, driven by Playwright."""

    def __init__(self, profile_dir, proxy="", locale="en-US"):
        self.profile_dir = str(profile_dir)
        self.proxy = proxy
        self.locale = locale
        self._stack = None
        self.context = None

    def options(self):
        options = {
            "persistent_context": True,
            "user_data_dir": self.profile_dir,
            "headless": False,
            "locale": self.locale,
            "os": host_os(),
        }
        proxy = proxy_settings(self.proxy)
        if proxy:
            options["proxy"] = proxy
        return options

    def open(self):
        try:
            from camoufox.sync_api import Camoufox
        except ImportError as exc:
            raise LiveBrowserError(CAMOUFOX_INSTALL) from exc
        stack = contextlib.ExitStack()
        try:
            context = stack.enter_context(Camoufox(**self.options()))
        except Exception as exc:
            stack.close()
            raise LiveBrowserError(self.explain(exc)) from exc
        self._stack = stack
        self.context = context
        return context

    def close(self):
        stack = self._stack
        self._stack = None
        self.context = None
        if stack is not None:
            stack.close()

    def explain(self, exc):
        message = str(exc)
        missing = isinstance(exc, FileNotFoundError) or "camoufox fetch" in message or "Executable doesn't exist" in message
        if missing:
            return f"{CAMOUFOX_INSTALL} ({message})"
        return f"Camoufox gagal dijalankan (Camoufox did not start): {message}"
