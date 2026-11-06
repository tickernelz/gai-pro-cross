"""Per-OS application directories with owner-only permissions."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

APP_NAME = "gai-pro-cross"


def _home() -> pathlib.Path:
    return pathlib.Path.home()


def _posix_config_root() -> pathlib.Path:
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return pathlib.Path(xdg) if xdg else _home() / ".config"


def _posix_data_root() -> pathlib.Path:
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_DATA_HOME")
    return pathlib.Path(xdg) if xdg else _home() / ".local" / "share"


def config_root() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
        return pathlib.Path(base) / APP_NAME
    return _posix_config_root() / APP_NAME


def data_root() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(_home() / "AppData" / "Local")
        return pathlib.Path(base) / APP_NAME
    return _posix_data_root() / APP_NAME


def private_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    else:
        user = os.environ.get("USERNAME")
        if user:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                capture_output=True,
                check=False,
            )
    return path


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    if safe in ("", ".", ".."):
        return "unknown"
    return safe


class Layout:
    """Resolved directory layout for one console instance."""

    def __init__(self, data_dir: str | None = None) -> None:
        root = pathlib.Path(data_dir).expanduser() if data_dir else data_root()
        self.root = private_dir(root)
        self.config_file = self.root / "config.json"
        self.accounts_file = self.root / "accounts.json"
        self.log_file = self.root / "console.log"
        self.screenshots = private_dir(self.root / "screenshots")
        self.profiles = private_dir(self.root / "profiles")
        self.sessions = private_dir(self.root / "sessions")

    def screenshot_dir(self, tag: str) -> pathlib.Path:
        return private_dir(self.screenshots / _safe_name(tag))

    def profile_dir(self, account_id: str) -> pathlib.Path:
        return private_dir(self.profiles / _safe_name(account_id))

    def session_dir(self, account_id: str) -> pathlib.Path:
        return private_dir(self.sessions / _safe_name(account_id))

    def session_file(self, account_id: str) -> pathlib.Path:
        return self.session_dir(account_id) / "storage_state.json"


def secure_file(path: pathlib.Path) -> pathlib.Path:
    if os.name == "posix":
        os.chmod(path, 0o600)
    else:
        user = os.environ.get("USERNAME")
        if user:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True,
                check=False,
            )
    return path


def open_in_browser(url: str) -> bool:
    try:
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        return False
