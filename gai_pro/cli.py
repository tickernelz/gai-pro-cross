"""Console entry point and sandbox self-test command."""

from __future__ import annotations

import argparse
import json
import sys
import threading

from .paths import Layout, open_in_browser
from .store import Store
from .webui import Console, serve


def _selftest(layout: Layout, card_json: str, keep: bool) -> int:
    from .mockgoogle import SandboxServer
    from .pipeline import run_pipeline
    from .accounts import new_account_record, parse_line
    from .card import TAXFREE_PROFILES
    from .gateway import SandboxGateway

    store = Store(layout)
    card = json.loads(card_json) if card_json else {
        "number": "4242424242424242",
        "exp_month": "08",
        "exp_year": "29",
        "cvc": "123",
        "holder": "SANDBOX USER",
    }
    store.update_config({"mode": "mock", "card": card, "address": dict(TAXFREE_PROFILES["US-OR-97201"])})
    account = new_account_record(parse_line("sandbox.user@example.com----pw----aux@example.com----JBSWY3DPEHPK3PXP"))
    store.replace_accounts([account])
    server = SandboxServer(offers=store.config["offers"]).start()
    gateway = SandboxGateway(server.base_url, account, layout, store)
    server.state.register(account["email"], account["password"], account["recovery_email"], account["totp_secret"])
    gateway.login(account)
    result = run_pipeline(account, store.config, gateway, store, threading.Event())
    gateway.close()
    server.stop()
    print(json.dumps({
        "status": result["status"],
        "step": result["current_step"],
        "expiry": result["expiry_date"],
        "layers": result["layers_done"],
        "screenshot": result["last_screenshot"],
    }, indent=2))
    if not keep:
        print("sandbox selftest finished; artifacts stay under the data dir")
    return 0 if result["status"] == "done" else 1


def probe(layout: Layout) -> int:
    from .paths import config_root, data_root
    from .card import TAXFREE_PROFILES

    report = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "config_root": str(config_root()),
        "data_root": str(data_root()),
        "layout_root": str(layout.root),
        "taxfree_profile": TAXFREE_PROFILES["US-OR-97201"]["postal_code"],
    }
    print(json.dumps(report, indent=2))
    return 0


def _find_account(store: Store, needle: str) -> dict | None:
    for acc in store.accounts:
        if acc["id"] == needle:
            return acc
    target = needle.strip().lower()
    for acc in store.accounts:
        if str(acc.get("email", "")).lower() == target:
            return acc
    return None


def _login(layout: Layout, account_ref: str, mode: str, timeout: float, accept_tos_risk: bool) -> int:
    from .gateway import BrowserGateway, GatewayError, SandboxGateway
    from .mockgoogle import SandboxServer
    from .redact import mask_email

    store = Store(layout)
    account = _find_account(store, account_ref)
    if account is None:
        print(f"account not found: {account_ref} (import it in the console first)", file=sys.stderr)
        return 2
    chosen = mode or store.config.get("mode", "mock")
    if chosen == "live" and not accept_tos_risk:
        print("manual login opens the real Google sign-in page in Camoufox, the anti-detect browser;", file=sys.stderr)
        print("re-run with --accept-tos-risk if you have read RISKS.md and accept the outcome.", file=sys.stderr)
        return 2
    if chosen == "mock":
        server = SandboxServer(offers=store.config.get("offers")).start()
        try:
            server.state.register(
                account["email"],
                account["password"],
                account.get("recovery_email", ""),
                account.get("totp_secret", ""),
            )
            path = SandboxGateway(server.base_url, account, layout, store).login(account)
        finally:
            server.stop()
    else:
        gateway = BrowserGateway("google", account, layout, store, headless=False)
        try:
            gateway.start()
            path = gateway.manual_login(account, timeout=timeout)
        except GatewayError as exc:
            print(f"login failed: {exc}", file=sys.stderr)
            return 1
        finally:
            gateway.close()
    print(json.dumps({"account": mask_email(account["email"]), "mode": chosen, "session": str(path)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gai-pro-cross", description="Cross-platform Google AI Pro trial console")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("serve", help="start the local console")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--data-dir")
    run.add_argument("--mode", choices=["mock", "live"], default=None)
    run.add_argument("--no-browser", action="store_true")
    run.add_argument("--accept-tos-risk", action="store_true", help="required for live mode")

    test = sub.add_parser("selftest", help="run the sandbox pipeline without any network or real card")
    test.add_argument("--data-dir")
    test.add_argument("--card-json", default="")
    test.add_argument("--keep", action="store_true")

    info = sub.add_parser("probe", help="print per-OS paths and versions")
    info.add_argument("--data-dir")

    login = sub.add_parser("login", help="sign in by hand once and store the session for one account")
    login.add_argument("--account", required=True, help="account id or email")
    login.add_argument("--data-dir")
    login.add_argument("--mode", choices=["mock", "live"], default=None)
    login.add_argument("--timeout", type=float, default=600.0)
    login.add_argument("--accept-tos-risk", action="store_true")

    args = parser.parse_args(argv)
    command = args.command or "serve"
    layout = Layout(getattr(args, "data_dir", None))

    if command == "probe":
        return probe(layout)
    if command == "selftest":
        return _selftest(layout, args.card_json, args.keep)
    if command == "login":
        return _login(layout, args.account, args.mode, args.timeout, args.accept_tos_risk)

    mode = args.mode
    if mode == "live":
        if not args.accept_tos_risk:
            print("live mode drives the real Google console and violates the Google terms of service;", file=sys.stderr)
            print("re-run with --accept-tos-risk if you have read RISKS.md and accept the outcome.", file=sys.stderr)
            return 2
        store = Store(layout)
        store.update_config({"mode": "live"})
    elif mode == "mock":
        store = Store(layout)
        store.update_config({"mode": "mock"})

    server = serve(layout, host=args.host, port=args.port, open_browser=not args.no_browser)
    print(f"console ready at {server.url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.console.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
