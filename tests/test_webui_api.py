import json
import threading
import time
import urllib.request

import pytest

from gai_pro.webui import serve


@pytest.fixture()
def console(layout):
    server = serve(layout, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.console.shutdown()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.loads(response.read().decode())


def post(base, path, payload):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def test_index_page_renders(console):
    with urllib.request.urlopen(console.url + "/", timeout=10) as response:
        body = response.read().decode()
    assert response.status == 200
    assert "accountsTbody" in body
    assert "logTerminal" in body


def test_state_never_leaks_secrets(console):
    base = console.url
    post(base, "/api/accounts/import", {"raw_text": "user@example.com----hunter2----rec@example.com----JBSWY3DPEHPK3PXP"})
    state = get(base, "/api/state")
    blob = json.dumps(state)
    assert "hunter2" not in blob
    assert "JBSWY3DPEHPK3PXP" not in blob
    assert state["accounts"][0]["email_masked"] == "u***@example.com"
    assert state["accounts"][0]["has_totp"] is True


def test_import_reports_rejected_lines(console):
    result = post(console.url, "/api/accounts/import", {"raw_text": "not-an-account\nok@example.com----pw"})
    assert result["added"] == 1
    assert result["rejected"] == ["not-an-account"]


def test_batch_run_completes_and_writes_proof(console, layout):
    base = console.url
    post(base, "/api/accounts/import", {"raw_text": "runner@example.com----pw----rec@example.com----JBSWY3DPEHPK3PXP"})
    post(base, "/api/config", {"mode": "mock", "card": {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}})
    queued = post(base, "/api/run/batch", {})["queued"]
    assert queued == 1
    deadline = time.time() + 30
    status = None
    while time.time() < deadline:
        state = get(base, "/api/state")
        status = state["accounts"][0]["status"]
        if status in ("done", "error"):
            break
        time.sleep(0.3)
    assert status == "done", state["accounts"][0]
    assert state["accounts"][0]["is_running"] is False


def test_stop_endpoint_acknowledges(console):
    assert post(console.url, "/api/stop", {})["ok"] is True


def test_proxy_test_rejects_malformed_value(console):
    assert post(console.url, "/api/proxy/test", {"proxy": "not-a-proxy"})["ok"] is False


def wait_for_finished(base, timeout=30):
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        state = get(base, "/api/state")
        if state["accounts"] and state["accounts"][0]["status"] in ("done", "error"):
            return state
        time.sleep(0.2)
    return state


def test_state_exposes_relative_proof_and_no_absolute_path(console, layout):
    base = console.url
    post(base, "/api/accounts/import", {"raw_text": "proof@example.com----pw----rec@example.com----JBSWY3DPEHPK3PXP"})
    post(base, "/api/config", {"mode": "mock"})
    assert post(base, "/api/run/batch", {})["queued"] == 1

    state = wait_for_finished(base)
    account = state["accounts"][0]
    assert account["status"] == "done", account
    assert account["current_step"] == 5
    assert "last_screenshot" not in account
    assert account["proof_rel"].endswith("/final-proof.png")
    assert str(layout.screenshots) not in json.dumps(state["accounts"])

    proof_url = base + "/screenshots/" + urllib.parse.quote(account["proof_rel"])
    with urllib.request.urlopen(proof_url, timeout=10) as response:
        assert response.status == 200
        assert response.read(8) == b"\x89PNG\r\n\x1a\n"


def test_config_round_trip_masks_stored_card(console):
    base = console.url
    post(base, "/api/config", {"mode": "mock", "card": {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}})
    state = get(base, "/api/state")
    assert state["card_last4"] == "4242"
    assert "number" not in state["card"]
    assert state["card"]["holder"] == "SANDBOX"
