"""Local-only persistence: config, accounts, and redacted log ring buffer."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from .card import TAXFREE_PROFILES
from .paths import Layout, secure_file
from .redact import mask_email, redact
from .session import clear_session, has_session

DEFAULT_OFFERS = [
    {"id": "pro_5tb_12m", "months": 12, "label": "Google AI Pro 12 months"},
    {"id": "pro_5tb_6m_samsung", "months": 6, "label": "Google AI Pro 6 months (Samsung)"},
    {"id": "pro_5tb_6m_pixel", "months": 6, "label": "Google AI Pro 6 months (Pixel)"},
]
MAX_STACK_MONTHS = 24


def default_config() -> dict:
    return {
        "mode": "mock",
        "target_region": "US",
        "target_plan": "pro_2year_zero",
        "auto_cancel": True,
        "proxy_template": "",
        "headless": True,
        "card": {
            "number": "",
            "exp_month": "",
            "exp_year": "",
            "cvc": "",
            "holder": "",
        },
        "address": dict(TAXFREE_PROFILES["US-OR-97201"]),
        "offers": [dict(o) for o in DEFAULT_OFFERS],
        "max_stack_months": MAX_STACK_MONTHS,
    }


class Store:
    """Thread-safe console state backed by two owner-only files."""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self._lock = threading.RLock()
        self._logs: list[str] = []
        self._config = self._load_json(layout.config_file, default_config())
        accounts = self._load_json(layout.accounts_file, {"accounts": []})
        self._accounts: list[dict] = accounts.get("accounts", [])

    @staticmethod
    def _load_json(path: Path, fallback: dict) -> dict:
        if not path.exists():
            return json.loads(json.dumps(fallback))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(fallback))
        if not isinstance(data, dict):
            return json.loads(json.dumps(fallback))
        return data

    def _atomic_write(self, path: Path, payload: dict) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        secure_file(tmp)
        os.replace(tmp, path)
        secure_file(path)

    @property
    def config(self) -> dict:
        with self._lock:
            return self._config

    def update_config(self, patch: dict) -> dict:
        with self._lock:
            for key in ("mode", "target_region", "target_plan", "auto_cancel", "proxy_template", "headless"):
                if key in patch and patch[key] is not None:
                    self._config[key] = patch[key]
            if isinstance(patch.get("card"), dict):
                self._config["card"].update(patch["card"])
            if isinstance(patch.get("address"), dict):
                self._config["address"].update(patch["address"])
            self._atomic_write(self.layout.config_file, self._config)
            return self._config

    @property
    def accounts(self) -> list[dict]:
        with self._lock:
            return list(self._accounts)

    def replace_accounts(self, accounts: list[dict]) -> None:
        with self._lock:
            self._accounts = accounts
            self._atomic_write(self.layout.accounts_file, {"accounts": self._accounts})

    def get_account(self, acc_id: str) -> dict | None:
        with self._lock:
            for acc in self._accounts:
                if acc["id"] == acc_id:
                    return acc
        return None

    def update_account(self, acc_id: str, patch: dict) -> dict | None:
        with self._lock:
            for acc in self._accounts:
                if acc["id"] == acc_id:
                    acc.update(patch)
                    self._atomic_write(self.layout.accounts_file, {"accounts": self._accounts})
                    return acc
        return None

    def delete_account(self, acc_id: str) -> bool:
        with self._lock:
            remaining = [a for a in self._accounts if a["id"] != acc_id]
            if len(remaining) == len(self._accounts):
                return False
            self._accounts = remaining
            self._atomic_write(self.layout.accounts_file, {"accounts": self._accounts})
            clear_session(self.layout, acc_id)
            for path in (self.layout.profile_dir(acc_id), self.layout.session_dir(acc_id)):
                try:
                    shutil.rmtree(path)
                except OSError:
                    self.log(f"could not remove {path.name}", "store")
            return True

    def reset_account(self, acc_id: str) -> dict | None:
        return self.update_account(
            acc_id,
            {
                "status": "pending",
                "current_step": 0,
                "step_message": "reset, not started",
                "expiry_date": "-",
                "last_screenshot": "",
                "layers_done": 0,
                "last_error": "",
            },
        )

    def log(self, message: str, channel: str = "core") -> str:
        stamp = time.strftime("%H:%M:%S")
        line = redact(f"[{stamp}] [{channel}] {message}")
        with self._lock:
            self._logs.append(line)
            if len(self._logs) > 2000:
                del self._logs[:-2000]
            try:
                if not self.layout.log_file.exists():
                    os.close(os.open(self.layout.log_file, os.O_CREAT | os.O_WRONLY, 0o600))
                with self.layout.log_file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                secure_file(self.layout.log_file)
            except OSError:
                pass
        return line

    def logs(self, limit: int = 250) -> list[str]:
        with self._lock:
            return self._logs[-limit:]

    def public_account(self, acc: dict) -> dict:
        out = dict(acc)
        out.pop("password", None)
        out.pop("totp_secret", None)
        out.pop("last_screenshot", None)
        out["email_masked"] = mask_email(acc.get("email", ""))
        out["has_totp"] = bool(acc.get("totp_secret"))
        out["has_recovery"] = bool(acc.get("recovery_email"))
        out["has_session"] = has_session(self.layout, acc.get("id", ""))
        out["proof_rel"] = self._proof_rel(acc.get("last_screenshot"))
        return out

    def _proof_rel(self, path: str | None) -> str:
        if not path:
            return ""
        try:
            return Path(path).resolve().relative_to(self.layout.screenshots.resolve()).as_posix()
        except (ValueError, OSError):
            return ""
