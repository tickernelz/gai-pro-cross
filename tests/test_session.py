import sys
import threading
import time
import types

import pytest

import gai_pro.chrome as chrome_module
import gai_pro.gateway as gateway_module
from gai_pro.accounts import new_account_record, parse_line
from gai_pro.card import TAXFREE_PROFILES
from gai_pro.cli import main
from gai_pro.gateway import BrowserGateway, GatewayError, SandboxGateway, SessionMissing
from gai_pro.mockgoogle import SandboxServer
from gai_pro.paths import Layout
from gai_pro.pipeline import run_pipeline
from gai_pro.session import has_google_auth_cookie, has_session, read_session, write_session
from gai_pro.store import Store

SANDBOX_CARD = {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}
GOOGLE_SID = {"name": "SID", "value": "fake-sid", "domain": ".google.com", "path": "/", "httpOnly": True, "secure": True}


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
    def __init__(self, cookies=None):
        self.pages = [FakePage()]
        self._cookies = list(cookies or [])

    def new_page(self):
        return FakePage(self.pages[0].url)

    def add_cookies(self, cookies):
        self._cookies.extend(cookies)

    def cookies(self):
        return list(self._cookies)

    def storage_state(self):
        return {"cookies": list(self._cookies), "origins": []}

    def close(self):
        return None


class FakeClock:
    def __init__(self, step=30.0):
        self.now = 0.0
        self.step = step

    def time(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        return None


class FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]
        self.closed = False

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context):
        self.context = context
        self.calls = []
        self.endpoints = []
        self.browsers = []

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        return self.context

    def connect_over_cdp(self, endpoint):
        self.endpoints.append(endpoint)
        browser = FakeBrowser(self.context)
        self.browsers.append(browser)
        return browser


class FakePlaywright:
    def __init__(self, context):
        self.chromium = FakeChromium(context)

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


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class FakeChromeLauncher:
    def __init__(self, ready=True, listening=False):
        self.ready = ready
        self.listening = listening
        self.port = 9333
        self.commands = []
        self.processes = []
        self.stopped = []

    def free_port(self):
        return self.port

    def launch(self, profile_dir, port, proxy=""):
        self.commands.append(chrome_module.launch_args("/usr/bin/google-chrome", profile_dir, port, proxy))
        process = FakeProcess()
        self.processes.append(process)
        return process

    def wait_for_cdp(self, port, timeout=None):
        return self.ready

    def cdp_version(self, port, timeout=None):
        return {"Browser": "Chrome/140"} if self.listening else None

    def stop(self, process, timeout=5.0):
        self.stopped.append(process)
        if process is not None and process.poll() is None:
            process.terminate()


def install_fake_chrome(monkeypatch, ready=True, listening=False):
    launcher = FakeChromeLauncher(ready=ready, listening=listening)
    monkeypatch.setattr(gateway_module.chrome, "free_port", launcher.free_port)
    monkeypatch.setattr(gateway_module.chrome, "launch", launcher.launch)
    monkeypatch.setattr(gateway_module.chrome, "wait_for_cdp", launcher.wait_for_cdp)
    monkeypatch.setattr(gateway_module.chrome, "cdp_version", launcher.cdp_version)
    monkeypatch.setattr(gateway_module.chrome, "stop", launcher.stop)
    return launcher


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
    assert has_session(layout, account["id"], "mock") is True

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
    write_session(layout, account["id"], {"mode": "live", "email": account["email"], "cookies": [dict(GOOGLE_SID)], "origins": []})
    context = FakeContext()
    fake = FakePlaywright(context)
    install_fake_playwright(monkeypatch, fake)
    launcher = install_fake_chrome(monkeypatch)

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    try:
        assert gateway.verify_session() is True
        path = gateway.manual_login(account, timeout=30, interval=1)
    finally:
        gateway.close()

    assert path.name == "storage_state.json"
    assert read_session(layout, account["id"])["cookies"] == [GOOGLE_SID]
    assert context.pages[0].filled == []
    assert fake.chromium.calls == []
    assert fake.chromium.endpoints == ["http://127.0.0.1:9333"]
    command = launcher.commands[0]
    assert "--enable-automation" not in command
    assert f"--user-data-dir={layout.profile_dir(account['id'])}" in command
    assert "--remote-debugging-port=9333" in command
    assert launcher.stopped and launcher.stopped[0].poll() is not None
    assert fake.chromium.browsers[0].closed is True
    assert not layout.devtools_file(account["id"]).exists()


def test_google_login_cookie_detection_needs_the_right_name_and_domain():
    assert has_google_auth_cookie([]) is False
    assert has_google_auth_cookie([{"name": "NID", "domain": ".google.com"}]) is False
    assert has_google_auth_cookie([{"name": "SID", "domain": ".example.com"}]) is False
    assert has_google_auth_cookie([{"name": "SID", "domain": ".google.com"}]) is True
    assert has_google_auth_cookie([{"name": "__Secure-3PSID", "domain": "google.com"}]) is True


def test_a_new_context_has_no_google_login_cookie(monkeypatch, layout, store):
    account = build_account("newcontext@example.com")
    context = FakeContext()
    install_fake_playwright(monkeypatch, FakePlaywright(context))
    install_fake_chrome(monkeypatch)

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    try:
        assert gateway.context_authenticated() is False
        context.add_cookies([dict(GOOGLE_SID)])
        assert gateway.context_authenticated() is True
    finally:
        gateway.close()


def test_manual_login_times_out_instead_of_saving_an_anonymous_session(monkeypatch, layout, store):
    account = build_account("timeout@example.com")
    context = FakeContext()
    install_fake_playwright(monkeypatch, FakePlaywright(context))
    install_fake_chrome(monkeypatch)
    clock = FakeClock()
    monkeypatch.setattr(gateway_module, "time", clock)

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    try:
        with pytest.raises(SessionMissing):
            gateway.manual_login(account, timeout=120, interval=1)
    finally:
        gateway.close()

    assert read_session(layout, account["id"]) is None
    assert any("browser stays open until you finish signing in" in line for line in gateway.log)
    assert any("menunggu login selesai" in line for line in gateway.log)


class DivergentExportContext(FakeContext):
    def cookies(self):
        return [dict(GOOGLE_SID)]

    def storage_state(self):
        return {"cookies": [], "origins": []}


def test_manual_login_never_persists_a_state_without_the_login_cookie(monkeypatch, layout, store):
    account = build_account("divergent@example.com")
    context = DivergentExportContext()
    install_fake_playwright(monkeypatch, FakePlaywright(context))
    install_fake_chrome(monkeypatch)
    monkeypatch.setattr(gateway_module, "time", FakeClock())

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    try:
        with pytest.raises(SessionMissing):
            gateway.manual_login(account, timeout=120, interval=1)
    finally:
        gateway.close()

    assert read_session(layout, account["id"]) is None
    assert any("exported storage_state has no Google login cookie" in line for line in gateway.log)


def test_has_session_ignores_a_live_session_file_without_a_google_cookie(layout):
    account_id = "acc_anonymous"
    write_session(layout, account_id, {"mode": "live", "email": "anon@example.com", "cookies": [{"name": "NID", "domain": ".google.com"}], "origins": []})
    assert has_session(layout, account_id) is False
    assert has_session(layout, account_id, "live") is False

    write_session(layout, account_id, {"mode": "live", "email": "anon@example.com", "cookies": [dict(GOOGLE_SID)], "origins": []})
    assert has_session(layout, account_id) is True
    assert has_session(layout, account_id, "live") is True


def test_live_gateway_attaches_to_a_chrome_already_listening_on_its_devtools_port(monkeypatch, layout, store):
    account = build_account("attach@example.com")
    layout.devtools_file(account["id"]).write_text('{"port": 9444}', encoding="utf-8")
    fake = FakePlaywright(FakeContext())
    install_fake_playwright(monkeypatch, fake)
    launcher = install_fake_chrome(monkeypatch, listening=True)

    gateway = BrowserGateway("google", account, layout, store)
    gateway.start()
    gateway.close()

    assert launcher.commands == []
    assert fake.chromium.endpoints == ["http://127.0.0.1:9444"]
    assert fake.chromium.browsers[0].closed is False
    assert launcher.stopped == []
    assert layout.devtools_file(account["id"]).is_file()


def test_live_gateway_reports_a_devtools_port_that_never_opens(monkeypatch, layout, store):
    account = build_account("nodevtools@example.com")
    fake = FakePlaywright(FakeContext())
    install_fake_playwright(monkeypatch, fake)
    launcher = install_fake_chrome(monkeypatch, ready=False)

    gateway = BrowserGateway("google", account, layout, store)
    with pytest.raises(GatewayError) as excinfo:
        gateway.start()

    assert "DevTools port tidak terbuka" in str(excinfo.value)
    assert fake.chromium.calls == []
    assert fake.chromium.endpoints == []
    assert launcher.stopped and launcher.stopped[0].poll() is not None
    assert not layout.devtools_file(account["id"]).exists()


def test_live_gateway_names_a_missing_chrome(monkeypatch, layout, store):
    account = build_account("nochrome@example.com")
    install_fake_playwright(monkeypatch, FakePlaywright(FakeContext()))

    def missing(profile_dir, port, proxy=""):
        raise chrome_module.ChromeError("Chrome tidak ditemukan (Chrome not found): install Google Chrome")

    monkeypatch.setattr(gateway_module.chrome, "free_port", lambda: 9333)
    monkeypatch.setattr(gateway_module.chrome, "launch", missing)

    gateway = BrowserGateway("google", account, layout, store)
    with pytest.raises(GatewayError) as excinfo:
        gateway.start()

    assert "Chrome tidak ditemukan" in str(excinfo.value)


def test_login_command_names_a_missing_chrome_instead_of_raising(monkeypatch, tmp_path, capsys):
    data_dir = str(tmp_path / "data")
    layout = Layout(data_dir)
    store = Store(layout)
    account = build_account("cli-nochrome@example.com")
    store.replace_accounts([account])
    configure(store)
    install_fake_playwright(monkeypatch, FakePlaywright(FakeContext()))

    def missing(profile_dir, port, proxy=""):
        raise chrome_module.ChromeError("Chrome tidak ditemukan (Chrome not found)")

    monkeypatch.setattr(gateway_module.chrome, "free_port", lambda: 9333)
    monkeypatch.setattr(gateway_module.chrome, "launch", missing)

    code = main(["login", "--account", account["id"], "--data-dir", data_dir, "--mode", "live", "--accept-tos-risk"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Chrome tidak ditemukan" in captured.err
    assert "Traceback" not in captured.err


def test_the_chrome_launcher_never_adds_the_automation_flag(tmp_path):
    command = chrome_module.launch_args("/usr/bin/google-chrome", tmp_path / "profile", 9333)

    assert command[0] == "/usr/bin/google-chrome"
    assert "--enable-automation" not in command
    assert "--user-data-dir=" + str(tmp_path / "profile") in command
    assert "--remote-debugging-port=9333" in command
    assert "--no-first-run" in command
    assert "--no-default-browser-check" in command
    assert command[-1] == "about:blank"


def test_a_missing_chrome_install_is_named(monkeypatch):
    monkeypatch.setattr(chrome_module, "chrome_candidates", lambda: [])

    with pytest.raises(chrome_module.ChromeError) as excinfo:
        chrome_module.find_chrome()

    assert "Chrome tidak ditemukan" in str(excinfo.value)


def test_wait_for_cdp_gives_up_after_the_timeout(monkeypatch):
    monkeypatch.setattr(chrome_module, "cdp_version", lambda port, timeout=0.5: None)
    started = time.time()

    assert chrome_module.wait_for_cdp(9333, timeout=0.1, interval=0.01) is False
    assert time.time() - started < 5


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
