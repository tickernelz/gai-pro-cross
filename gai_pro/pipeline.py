"""Shared five-step pipeline: login, stack layers, cancel renewal, capture proof."""

from __future__ import annotations

import pathlib

from .card import SANDBOX_CARD
from .gateway import GatewayError, SessionMissing, ZeroCostViolation
from .session import SESSION_MISSING


class Aborted(RuntimeError):
    pass


def _emit(store, account, step, message, channel="pipeline"):
    store.update_account(account["id"], {"current_step": step, "step_message": message})
    store.log(f"{account['email']}: {message}", channel)


def _check_stop(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise Aborted("stop requested")


def _capture_diagnostic(gateway, store, account):
    try:
        info = gateway.capture_diagnostic("failure")
    except Exception as exc:
        store.log(f"{account['email']}: diagnostic capture failed: {exc}", "pipeline")
        return None
    if not info:
        return None
    try:
        relative = pathlib.Path(info["screenshot"]).resolve().relative_to(store.layout.root.resolve()).as_posix()
    except (KeyError, ValueError, OSError):
        relative = info.get("screenshot", "")
    store.log(f"{account['email']}: diagnostic saved at {relative} (url {info.get('url', '')})", "pipeline")
    return {"screenshot": info.get("screenshot", ""), "relative": relative, "url": info.get("url", "")}


def run_pipeline(account, config, gateway, store, stop_event=None, attempts=2):
    """Run every step for one account and return the final account state."""
    offers = config.get("offers", [])
    card = config.get("card", {})
    if config.get("mode") == "mock" and not str(card.get("number") or "").strip():
        card = dict(SANDBOX_CARD)
    address = config.get("address", {})
    max_months = int(config.get("max_stack_months", 24))

    try:
        store.update_account(account["id"], {"status": "running", "last_error": "", "last_diagnostic": ""})
        _emit(store, account, 1, "step 1/5: verifying the stored session")
        if not gateway.verify_session():
            raise SessionMissing(SESSION_MISSING)
        _check_stop(stop_event)

        state = gateway.plan_state()
        months_before = int(state.get("months_stacked") or 0)
        _emit(store, account, 2, f"plan state: {months_before} months already stacked")

        cumulative = 0
        for index, offer in enumerate(offers):
            _check_stop(stop_event)
            cumulative += int(offer["months"])
            threshold = min(cumulative, max_months)
            step = index + 2
            if months_before >= threshold:
                _emit(store, account, step, f"skip {offer['label']}: {months_before} months already stacked")
                continue
            last_error = None
            for attempt in range(1, attempts + 1):
                try:
                    _emit(store, account, step, f"step {step}/5: subscribing {offer['label']} (attempt {attempt})")
                    result = gateway.subscribe(offer["id"], card, address)
                    if str(result.get("total")) != "0.00":
                        raise ZeroCostViolation(f"checkout total {result.get('total')!r} is not 0.00")
                    months_before = int(result.get("months_stacked") or months_before + offer["months"])
                    store.update_account(
                        account["id"],
                        {
                            "layers_done": index + 1,
                            "expiry_date": result.get("expiry") or "-",
                        },
                    )
                    _emit(store, account, step, f"subscribed {offer['label']}, total due {result.get('total')}")
                    last_error = None
                    break
                except GatewayError as exc:
                    last_error = str(exc)
                    _emit(store, account, step, f"attempt {attempt} failed: {last_error}")
                    if isinstance(exc, ZeroCostViolation):
                        raise
            if last_error:
                raise GatewayError(f"{offer['label']} failed: {last_error}")

        _check_stop(stop_event)
        if config.get("auto_cancel", True):
            _emit(store, account, 5, "step 5/5: cancelling auto-renewal")
            gateway.cancel_renewal()
        subs = gateway.subscriptions()
        proof = gateway.capture_proof("final-proof")
        store.update_account(
            account["id"],
            {
                "status": "done",
                "current_step": 5,
                "step_message": f"completed, renewal active: {subs.get('renewing')}",
                "last_screenshot": proof,
                "expiry_date": subs.get("expiry") or store.get_account(account["id"]).get("expiry_date", "-"),
            },
        )
        return store.get_account(account["id"])
    except Aborted as exc:
        store.update_account(account["id"], {"status": "stopped", "step_message": str(exc)})
        raise
    except Exception as exc:
        diagnostic = _capture_diagnostic(gateway, store, account)
        message = f"failed: {str(exc)[:160]}"
        if diagnostic:
            message = f"failed: {str(exc)[:120]} | diagnostik: {diagnostic['relative']}"
        store.update_account(
            account["id"],
            {
                "status": "error",
                "step_message": message,
                "last_error": str(exc)[:400],
                "last_diagnostic": diagnostic["relative"] if diagnostic else "",
            },
        )
        store.log(f"{account['email']}: pipeline failed: {exc}", "pipeline")
        return store.get_account(account["id"])
