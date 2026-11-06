import sys
import threading
import time
import types

from gai_pro.accounts import new_account_record, parse_line
from gai_pro.card import TAXFREE_PROFILES
from gai_pro.cli import main
from gai_pro.gateway import BrowserGateway, GatewayError, SandboxGateway
from gai_pro.mockgoogle import SandboxServer
from gai_pro.paths import Layout
from gai_pro.pipeline import run_pipeline
from gai_pro.session import read_session, write_session
from gai_pro.store import Store

SANDBOX_CARD = {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}


def build_account(email="session@example.com", password="pw"):
    return new_account_record(parse_line(f"{email}----{password}----rec@example.com----JBSWY3DPEHPK3PXP"))


def configure(store):
    store.update_config(
        {
            "mode": "mock",
            "card": dict(SANDBOX_CARD),
            "address": dict(TAXFREE_PROFILES["US-OR-97201"]),
            "auto_cancel": True,
        }
    )


class PasswordGuard(dict):
    def _deny(self, key):
        if key == "password":
            raise AssertionError("the live gateway read the stored password")

    def __getitem__(self, key):
        self._deny(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._deny(key)
        return super().get(key, default)


class FakePage:
    def __init__(self, url="about:blank"):
        self.url = url
        self.filled = []

    def goto(self, url, **kwargs):
        self.url = url

    def wait_for_timeout(self, milliseconds):
        return None

    def fill(self, selector, value):
        self.filled.append((selector, value))

    def screenshot(self, **kwargs):
        return None

    def close(self):
        return None


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]
        self.cookies = []

    def new_page(self):
        return FakePage(self.pages[0].url)

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def storage_state(self):
        return {"cookies": [], "origins": []}

    def close(self):
        return None


class FakeChromium:
    def __init__(self, context, fail_chrome=False):
        self.context = context
        self.calls = []
        self.fail_chrome = fail_chrome

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_chrome and kwargs.get("channel") == "chrome":
            raise RuntimeError("chrome is not installed")
        return self.context


class FakePlaywright:
    def __init__(self, context, fail_chrome=False):
        self.chromium = FakeChromium(context, fail_chrome)

    def stop(self):
        return None


class FakeStarter:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


def install_fake_playwright(monkeypatch, playwright):
    module = types.SimpleNamespace(sync_playwright=lambda: FakeStarter(playwright))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)


class StubGateway:
    def __init__(self, layout):
        self.layout = layout

    def verify_session(self):
        return True

    def plan_state(self):
        raise GatewayError("plans page exploded")

    def capture_diagnostic(self, tag):
        target = self.layout.screenshot_dir("stub@example.com") / f"{tag}.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"screenshot": str(target), "url_file": "", "url": "https://one.google.com/plans"}

    def close(self):
        return None


def test_login_command_stores_a_session_the_pipeline_reuses_without_a_password(tmp_path):
    data_dir = str(tmp_path / "data")
    layout = Layout(data_dir)
    store = Store(layout)
    account = build_account("cli@example.com")
    store.replace_accounts([account])
    configure(store)

    assert main(["login", "--account", account["id"], "--data-dir", data_dir]) == 0
    state = read_session(layout, account["id"])
    assert state["mode"] == "mock"
    assert state["email"] == "cli@example.com"

    runtime = store.get_account(account["id"])
    runtime["password"] = ""
    server = SandboxServer(offers=store.config["offers"]).start()
    try:
        server.state.register(runtime["email"], "pw", runtime["recovery_email"], runtime["totp_secret"])
        result = run_pipeline(runtime, store.config, SandboxGateway(server.base_url, runtime, layout, store), store, threading.Event())
    finally:
        server.stop()

    assert result["status"] == "done", result
    assert result["layers_done"] == 3


def test_pipeline_without_a_session_fails_fast_with_a_clear_message(store, layout):
    account = build_account("nosession@example.com")
    store.replace_accounts([account])
    configure(store)
    server = SandboxServer(offers=store.config["offers"]).start()
    try:
        server.state.register(account["email"], account["password"], account["recovery_email"], account["totp_secret"])
        started = time.time()
        result = run_pipeline(account, store.config, SandboxGateway(server.base_url, account, layout, store), store, threading.Event())
    finally:
        server.stop()

    assert time.time() - started < 5
    assert result["status"] == "error", result
    assert result["current_step"] == 1
    assert "sesi belum ada" in result["last_error"]
    assert "Login manual" in result["step_message"]


def test_live_gateway_never_reads_the_password(monkeypatch, layout, store):
    account = PasswordGuard(build_account("live@example.com", "hunter2"))
    write_session(layout, account["id"], {"mode": "live", "email": account["email"], "cookies": [], "origins": []})
    context = FakeContext()
    fake = FakePlaywright(context)
    install_fake_playwright(monkeypatch, fake)

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    try:
        assert gateway.verify_session() is True
        path = gateway.manual_login(account, timeout=30, interval=1)
    finally:
        gateway.close()

    assert path.name == "storage_state.json"
    assert context.pages[0].filled == []
    launch = fake.chromium.calls[0]
    assert launch["channel"] == "chrome"
    assert launch["headless"] is False
    assert launch["user_data_dir"] == str(layout.profile_dir(account["id"]))


def test_live_gateway_falls_back_to_bundled_chromium(monkeypatch, layout, store):
    account = build_account("fallback@example.com")
    fake = FakePlaywright(FakeContext(), fail_chrome=True)
    install_fake_playwright(monkeypatch, fake)

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    gateway.close()

    assert [call.get("channel") for call in fake.chromium.calls] == ["chrome", None]


def test_failed_step_writes_a_diagnostic_and_names_it(store, layout):
    account = new_account_record(parse_line("diag@example.com----pw"))
    store.replace_accounts([account])
    configure(store)

    result = run_pipeline(account, store.config, StubGateway(layout), store, threading.Event())

    assert result["status"] == "error"
    assert result["last_diagnostic"].endswith("failure.png")
    assert (layout.root / result["last_diagnostic"]).is_file()
    assert result["last_diagnostic"] in result["step_message"]
    assert "plans page exploded" in result["last_error"]
