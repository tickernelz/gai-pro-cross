import pathlib
import threading

import pytest

from gai_pro.accounts import new_account_record, parse_line
from gai_pro.card import TAXFREE_PROFILES
from gai_pro.gateway import SandboxGateway, ZeroCostViolation
from gai_pro.mockgoogle import SandboxServer
from gai_pro.pipeline import Aborted, run_pipeline

SANDBOX_CARD = {"number": "4242424242424242", "exp_month": "08", "exp_year": "29", "cvc": "123", "holder": "SANDBOX"}


def account(email="user@example.com", totp_secret="JBSWY3DPEHPK3PXP"):
    line = f"{email}----pw----recovery@example.com----{totp_secret}" if totp_secret else f"{email}----pw"
    return new_account_record(parse_line(line))


@pytest.fixture()
def sandbox():
    server = SandboxServer().start()
    yield server
    server.stop()


def build_config(store):
    store.update_config({
        "mode": "mock",
        "card": dict(SANDBOX_CARD),
        "address": dict(TAXFREE_PROFILES["US-OR-97201"]),
        "auto_cancel": True,
    })
    return store.config


def gateway_for(store, layout, sandbox, acc):
    sandbox.state.register(acc["email"], acc["password"], acc["recovery_email"], acc["totp_secret"])
    return SandboxGateway(sandbox.base_url, acc, layout, store)


def test_full_pipeline_stacks_three_layers_and_cancels(store, layout, sandbox):
    acc = account()
    store.replace_accounts([acc])
    cfg = build_config(store)
    gateway = gateway_for(store, layout, sandbox, acc)
    result = run_pipeline(acc, cfg, gateway, store, threading.Event())

    assert result["status"] == "done"
    assert result["layers_done"] == 3
    assert result["expiry_date"] > "2028-01-01"
    assert result["last_screenshot"].endswith("final-proof.png")
    assert pathlib.Path(result["last_screenshot"]).exists()
    assert sandbox.state.accounts["user@example.com"]["renewing"] is False


def test_rerun_skips_completed_layers(store, layout, sandbox):
    acc = account()
    store.replace_accounts([acc])
    cfg = build_config(store)
    run_pipeline(acc, cfg, gateway_for(store, layout, sandbox, acc), store, threading.Event())

    second = store.get_account(acc["id"])
    result = run_pipeline(second, store.config, gateway_for(store, layout, sandbox, acc), store, threading.Event())
    assert result["status"] == "done"
    assert result["layers_done"] == 3
    log = "\n".join(store.logs())
    assert log.count("skip") >= 3


def test_wrong_password_is_reported_and_not_fatal_to_console(store, layout, sandbox):
    acc = account()
    acc["password"] = "wrong"
    store.replace_accounts([acc])
    cfg = build_config(store)
    sandbox.state.register(acc["email"], "right", acc["recovery_email"], acc["totp_secret"])
    gateway = SandboxGateway(sandbox.base_url, acc, layout, store)
    result = run_pipeline(acc, store.config, gateway, store, threading.Event())
    assert result["status"] == "error"
    assert "bad_password" in result["last_error"]


def test_zero_cost_guard_blocks_a_paid_checkout(store, layout, sandbox):
    acc = account()
    store.replace_accounts([acc])
    cfg = build_config(store)
    gateway = gateway_for(store, layout, sandbox, acc)
    gateway.subscribe = lambda offer_id, card, address: {"total": "9.99", "months_stacked": 12}
    result = run_pipeline(acc, cfg, gateway, store, threading.Event())
    assert result["status"] == "error"
    assert "0.00" in result["last_error"]


def test_mock_without_card_uses_the_sandbox_card(store, layout, sandbox):
    acc = account()
    store.replace_accounts([acc])
    store.update_config({
        "mode": "mock",
        "card": {"number": "", "exp_month": "", "exp_year": "", "cvc": "", "holder": ""},
        "address": dict(TAXFREE_PROFILES["US-OR-97201"]),
        "auto_cancel": True,
    })
    gateway = gateway_for(store, layout, sandbox, acc)
    result = run_pipeline(acc, store.config, gateway, store, threading.Event())

    assert result["status"] == "done", result
    assert result["current_step"] == 5
    assert result["layers_done"] == 3
    assert result["expiry_date"] > "2028-01-01"
    assert result["last_screenshot"].endswith("final-proof.png")


def test_stop_event_marks_account_stopped(store, layout, sandbox):
    acc = account()
    store.replace_accounts([acc])
    cfg = build_config(store)
    stop = threading.Event()
    stop.set()
    gateway = gateway_for(store, layout, sandbox, acc)
    with pytest.raises(Aborted):
        run_pipeline(acc, cfg, gateway, store, stop)
    assert store.get_account(acc["id"])["status"] == "stopped"


def test_declined_card_leaves_account_in_error(store, layout, sandbox):
    acc = account()
    store.replace_accounts([acc])
    cfg = build_config(store)
    cfg["card"]["number"] = "4242424242424243"
    gateway = gateway_for(store, layout, sandbox, acc)
    result = run_pipeline(acc, cfg, gateway, store, threading.Event())
    assert result["status"] == "error"
    assert "payment_declined" in result["last_error"]

