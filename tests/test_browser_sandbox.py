import threading

import pytest

from gai_pro.accounts import new_account_record, parse_line
from gai_pro.card import TAXFREE_PROFILES
from gai_pro.gateway import BrowserGateway
from gai_pro.mockgoogle import SandboxServer
from gai_pro.pipeline import run_pipeline

pytest.importorskip("playwright.sync_api", reason="playwright is only needed for the browser path")

SANDBOX_CARD = {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}


def test_browser_drives_the_sandbox_end_to_end(store, layout):
    server = SandboxServer().start()
    try:
        account = new_account_record(parse_line("browser@example.com----pw----rec@example.com----JBSWY3DPEHPK3PXP"))
        store.replace_accounts([account])
        store.update_config({
            "mode": "mock",
            "card": dict(SANDBOX_CARD),
            "address": dict(TAXFREE_PROFILES["US-OR-97201"]),
            "auto_cancel": True,
        })
        server.state.register(account["email"], account["password"], account["recovery_email"], account["totp_secret"])
        gateway = BrowserGateway("sandbox", account, layout, store, headless=True)
        gateway.start(base_url=server.base_url)
        try:
            result = run_pipeline(account, store.config, gateway, store, threading.Event())
        finally:
            gateway.close()
    finally:
        server.stop()

    assert result["status"] == "done", result
    assert result["layers_done"] == 3
    assert result["last_screenshot"].endswith("final-proof.png")
