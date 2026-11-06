"""Local console: loopback HTTP API plus the browser control panel."""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .accounts import merge_accounts, parse_batch
from .gateway import BrowserGateway, SandboxGateway
from .mockgoogle import SandboxServer
from .paths import Layout
from .pipeline import Aborted, run_pipeline
from .session import has_session
from .store import Store
from .totp import seconds_remaining, totp

WEB_DIR = Path(__file__).parent / "web"


class Console:
    """Owns the store, the worker pool, and the sandbox Google server."""

    def __init__(self, layout: Layout, sandbox_offers: list[dict] | None = None) -> None:
        self.layout = layout
        self.store = Store(layout)
        self.stop_event = threading.Event()
        self.sandbox = None
        self.sandbox_offers = sandbox_offers
        self._active: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._batch = False

    @property
    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._active)

    @property
    def batch_running(self) -> bool:
        return self._batch

    def ensure_sandbox(self) -> SandboxServer:
        with self._lock:
            if self.sandbox is None:
                self.sandbox = SandboxServer(offers=self.sandbox_offers).start()
                self.store.log(f"sandbox google started on {self.sandbox.base_url}", "sandbox")
            return self.sandbox

    def shutdown(self) -> None:
        self.stop_event.set()
        with self._lock:
            if self.sandbox is not None:
                self.sandbox.stop()
                self.sandbox = None

    def _gateway(self, account: dict):
        mode = self.store.config.get("mode", "mock")
        if mode == "mock":
            server = self.ensure_sandbox()
            server.state.register(
                account["email"],
                account["password"],
                account.get("recovery_email", ""),
                account.get("totp_secret", ""),
            )
            return SandboxGateway(server.base_url, account, self.layout, self.store)
        gateway = BrowserGateway(
            "google",
            account,
            self.layout,
            self.store,
            headless=bool(self.store.config.get("headless", True)),
            proxy=account.get("proxy", ""),
        )
        if has_session(self.layout, account["id"]):
            gateway.start()
        return gateway

    def _run(self, account: dict) -> None:
        gateway = None
        try:
            gateway = self._gateway(account)
            run_pipeline(account, self.store.config, gateway, self.store, self.stop_event)
        except Aborted:
            self.store.log(f"{account['email']}: stopped by user", "pipeline")
        except Exception as exc:
            self.store.log(f"{account['email']}: aborted: {exc}", "pipeline")
            self.store.update_account(account["id"], {"status": "error", "last_error": str(exc)[:400]})
        finally:
            if gateway is not None:
                try:
                    gateway.close()
                except Exception:
                    pass
            with self._lock:
                self._active.pop(account["id"], None)

    def _login(self, account: dict) -> None:
        mode = self.store.config.get("mode", "mock")
        gateway = None
        try:
            if mode == "mock":
                server = self.ensure_sandbox()
                server.state.register(
                    account["email"],
                    account["password"],
                    account.get("recovery_email", ""),
                    account.get("totp_secret", ""),
                )
                gateway = SandboxGateway(server.base_url, account, self.layout, self.store)
                gateway.login(account)
            else:
                gateway = BrowserGateway(
                    "google",
                    account,
                    self.layout,
                    self.store,
                    headless=False,
                    proxy=account.get("proxy", ""),
                )
                gateway.start()
                gateway.manual_login(account)
            self.store.update_account(account["id"], {"last_error": ""})
        except Exception as exc:
            self.store.log(f"{account['email']}: manual login failed: {exc}", "session")
            self.store.update_account(account["id"], {"last_error": str(exc)[:400]})
        finally:
            if gateway is not None:
                try:
                    gateway.close()
                except Exception:
                    pass
            with self._lock:
                self._active.pop(account["id"], None)

    def start_login(self, acc_id: str) -> bool:
        account = self.store.get_account(acc_id)
        if account is None:
            return False
        with self._lock:
            if acc_id in self._active:
                return False
            thread = threading.Thread(target=self._login, args=(account,), daemon=True, name=f"login-{acc_id}")
            self._active[acc_id] = thread
        thread.start()
        return True

    def start_account(self, acc_id: str) -> bool:
        account = self.store.get_account(acc_id)
        if account is None:
            return False
        with self._lock:
            if acc_id in self._active:
                return False
            thread = threading.Thread(target=self._run, args=(account,), daemon=True, name=f"acc-{acc_id}")
            self._active[acc_id] = thread
        self.stop_event.clear()
        thread.start()
        return True

    def start_batch(self, start_step: int = 1) -> int:
        active = set(self.active_ids)
        pending = [a for a in self.store.accounts if a["status"] in ("pending", "error", "stopped") and a["id"] not in active]
        if not pending:
            return 0
        with self._lock:
            self._batch = True
        self.stop_event.clear()
        workers = max(1, min(4, int(self.store.config.get("max_concurrency", 2))))
        counter = {"done": 0}

        def worker(account: dict) -> None:
            self._run(account)
            with self._lock:
                counter["done"] += 1
                if counter["done"] >= len(pending):
                    self._batch = False

        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch")
        for account in pending:
            with self._lock:
                marker = threading.Thread(target=lambda: None)
                self._active[account["id"]] = marker
            pool.submit(worker, account)
        pool.shutdown(wait=False)
        return len(pending)

    def stop_all(self) -> None:
        self.stop_event.set()
        self.store.log("stop requested for all workers", "pipeline")
        with self._lock:
            self._batch = False

    def state(self) -> dict:
        cfg = self.store.config
        rem = seconds_remaining()
        accounts = []
        for acc in self.store.accounts:
            item = self.store.public_account(acc)
            item["is_running"] = acc["id"] in self.active_ids
            item["totp_now"] = totp(acc["totp_secret"]) if acc.get("totp_secret") else "------"
            item["totp_rem"] = rem
            accounts.append(item)
        return {
            "mode": cfg.get("mode", "mock"),
            "target_region": cfg.get("target_region", "US"),
            "target_plan": cfg.get("target_plan", "pro_2year_zero"),
            "auto_cancel": cfg.get("auto_cancel", True),
            "headless": cfg.get("headless", True),
            "proxy_template": cfg.get("proxy_template", ""),
            "card": {k: v for k, v in cfg.get("card", {}).items() if k in ("exp_month", "exp_year", "holder")},
            "card_last4": (cfg.get("card", {}).get("number", "") or "")[-4:],
            "address": cfg.get("address", {}),
            "offers": cfg.get("offers", []),
            "max_stack_months": cfg.get("max_stack_months", 24),
            "accounts": accounts,
            "logs": self.store.logs(),
            "batch_running": self.batch_running,
            "active_count": len(self.active_ids),
            "sandbox_url": self.sandbox.base_url if self.sandbox else "",
        }



class ConsoleHandler(BaseHTTPRequestHandler):
    """HTTP surface for the control panel."""

    server_version = "GaiProConsole/1.0"
    console: Console

    def log_message(self, fmt, *args):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        rel = urllib.parse.unquote(path.lstrip("/")) or "index.html"
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents and target != WEB_DIR.resolve():
            return self._json(403, {"error": "forbidden"})
        if not target.is_file():
            return self._json(404, {"error": "not_found"})
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proof(self, rel):
        parts = [p for p in urllib.parse.unquote(rel).split("/") if p not in ("", ".", "..")]
        if len(parts) != 2:
            return self._json(400, {"error": "bad_path"})
        target = (self.console.layout.screenshots / parts[0] / parts[1]).resolve()
        root = self.console.layout.screenshots.resolve()
        if root not in target.parents or not target.is_file():
            return self._json(404, {"error": "not_found"})
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("content-type", "image/png")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            return self._json(200, self.console.state())
        if path == "/" or path.startswith("/static/"):
            return self._static("index.html" if path == "/" else path[len("/static/") :])
        if path.startswith("/screenshots/"):
            return self._proof(path[len("/screenshots/") :])
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        store = self.console.store
        if path == "/api/config":
            store.update_config(body)
            store.log(f"config saved (mode={body.get('mode', store.config.get('mode'))})", "config")
            return self._json(200, {"ok": True, "config": store.config})
        if path == "/api/accounts/import":
            parsed, rejected = parse_batch(body.get("raw_text", ""))
            accounts, added = merge_accounts(store.accounts, parsed)
            store.replace_accounts(accounts)
            store.log(f"imported {added} new account(s), {len(rejected)} line(s) rejected", "import")
            return self._json(200, {"ok": True, "added": added, "total": len(accounts), "rejected": rejected})
        if path == "/api/accounts/delete":
            ok = store.delete_account(body.get("id", ""))
            return self._json(200, {"ok": ok})
        if path == "/api/accounts/reset":
            acc = store.reset_account(body.get("id", ""))
            return self._json(200, {"ok": acc is not None})
        if path == "/api/accounts/login":
            started = self.console.start_login(body.get("id", ""))
            return self._json(200, {"ok": started})
        if path == "/api/run/single":
            started = self.console.start_account(body.get("id", ""))
            return self._json(200, {"ok": started})
        if path == "/api/run/batch":
            count = self.console.start_batch(int(body.get("start_step", 1) or 1))
            store.log(f"batch started for {count} account(s)", "batch")
            return self._json(200, {"ok": True, "queued": count})
        if path == "/api/stop":
            self.console.stop_all()
            return self._json(200, {"ok": True})
        if path == "/api/proxy/test":
            proxy = (body.get("proxy") or "").strip()
            if not proxy:
                return self._json(200, {"ok": True, "detail": "no proxy configured, direct connection"})
            reachable = _probe_proxy(proxy)
            return self._json(200, {"ok": reachable, "detail": proxy if reachable else f"unreachable: {proxy}"})
        if path == "/api/open_browser":
            url = body.get("url") or ""
            if url:
                from .paths import open_in_browser

                opened = open_in_browser(url)
                return self._json(200, {"ok": opened})
            return self._json(400, {"error": "url_required"})
        return self._json(404, {"error": "not_found"})


def _probe_proxy(proxy: str) -> bool:
    import socket

    target = proxy.split("://")[-1]
    target = target.split("@")[-1]
    host, _, port = target.partition(":")
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=5):
            return True
    except (OSError, ValueError):
        return False


class ConsoleServer(ThreadingHTTPServer):
    """Threaded console server bound to the loopback interface."""

    daemon_threads = True

    def __init__(self, address, console: Console) -> None:
        handler = type("BoundConsoleHandler", (ConsoleHandler,), {"console": console})
        super().__init__(address, handler)
        self.console = console
        self.port = self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def serve(layout: Layout, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> ConsoleServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("console only binds to loopback")
    console = Console(layout)
    server = ConsoleServer((host, port), console)
    console.store.log(f"console listening on http://{host}:{server.port}", "console")
    if open_browser:
        from .paths import open_in_browser

        open_in_browser(f"http://127.0.0.1:{server.port}")
    return server
