import threading

import pytest

from gai_pro.accounts import new_account_record, parse_line
from gai_pro.card import TAXFREE_PROFILES
from gai_pro.gateway import BrowserGateway, GatewayError
from gai_pro.mockgoogle import SandboxServer
from gai_pro.pipeline import run_pipeline

pytest.importorskip("playwright.sync_api", reason="playwright is only needed for the browser path")

SANDBOX_CARD = {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}


def build_fixture(store, layout, server):
    account = new_account_record(parse_line("browser@example.com----pw----rec@example.com----JBSWY3DPEHPK3PXP"))
    store.replace_accounts([account])
    store.update_config(
        {
            "mode": "mock",
            "card": dict(SANDBOX_CARD),
            "address": dict(TAXFREE_PROFILES["US-OR-97201"]),
            "auto_cancel": True,
        }
    )
    server.state.register(account["email"], account["password"], account["recovery_email"], account["totp_secret"])
    gateway = BrowserGateway("sandbox", account, layout, store, headless=True)
    gateway.start(base_url=server.base_url)
    return account, gateway


def test_browser_drives_the_sandbox_end_to_end(store, layout):
    server = SandboxServer().start()
    account, gateway = build_fixture(store, layout, server)
    try:
        gateway.login(account)
        result = run_pipeline(account, store.config, gateway, store, threading.Event())
    finally:
        gateway.close()
        server.stop()

    assert result["status"] == "done", result
    assert result["layers_done"] == 3
    assert result["last_screenshot"].endswith("final-proof.png")


def test_browser_failure_leaves_a_diagnostic_screenshot(store, layout):
    server = SandboxServer().start()
    account, gateway = build_fixture(store, layout, server)

    def explode(offer_id, card, address):
        raise GatewayError("checkout exploded")

    try:
        gateway.login(account)
        gateway.subscribe = explode
        result = run_pipeline(account, store.config, gateway, store, threading.Event())
    finally:
        gateway.close()
        server.stop()

    assert result["status"] == "error", result
    assert "checkout exploded" in result["last_error"]
    assert result["last_diagnostic"].endswith("failure.png")
    target = layout.root / result["last_diagnostic"]
    assert target.is_file()
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result["last_diagnostic"] in result["step_message"]
