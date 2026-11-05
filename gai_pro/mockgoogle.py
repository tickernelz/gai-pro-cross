"""Local stand-in for the Google One and Play billing pages used in sandbox runs."""

from __future__ import annotations

import json
import threading
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .card import CardError, checkout_profile
from .png import proof_png
from .totp import verify as verify_totp

MOCK_ORIGIN = "http://127.0.0.1"


class SandboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class SandboxState:
    """In-memory Google-side state for sandbox runs."""

    def __init__(self, offers: list[dict] | None = None) -> None:
        self.lock = threading.RLock()
        self.offers = offers or [
            {"id": "pro_5tb_12m", "months": 12, "label": "Google AI Pro 12 months"},
            {"id": "pro_5tb_6m_samsung", "months": 6, "label": "Google AI Pro 6 months (Samsung)"},
            {"id": "pro_5tb_6m_pixel", "months": 6, "label": "Google AI Pro 6 months (Pixel)"},
        ]
        self.accounts: dict[str, dict] = {}
        self.sessions: dict[str, str] = {}

    def register(self, email: str, password: str, recovery_email: str = "", totp_secret: str = "") -> dict:
        with self.lock:
            record = self.accounts.get(email.lower())
            if record is None:
                record = {
                    "email": email,
                    "password": password,
                    "recovery_email": recovery_email,
                    "totp_secret": totp_secret,
                    "layers": [],
                    "renewing": False,
                    "expiry": None,
                }
                self.accounts[email.lower()] = record
            else:
                record["password"] = password
                record["recovery_email"] = recovery_email or record["recovery_email"]
                record["totp_secret"] = totp_secret or record["totp_secret"]
            return record

    def state_for(self, email: str) -> dict:
        record = self.accounts.get(email.lower())
        if record is None:
            raise SandboxError("account_not_found", f"no sandbox account for {email}")
        months = sum(layer["months"] for layer in record["layers"])
        return {
            "email": record["email"],
            "months_stacked": months,
            "layers_done": len(record["layers"]),
            "expiry": record["expiry"].isoformat() if record["expiry"] else None,
            "renewing": record["renewing"],
            "offers": [
                {
                    "id": offer["id"],
                    "months": offer["months"],
                    "label": offer["label"],
                    "available": offer["id"] not in {layer["id"] for layer in record["layers"]},
                }
                for offer in self.offers
            ],
        }

    def login(self, email: str, password: str, recovery_code: str = "", totp_code: str = "") -> str:
        record = self.accounts.get(email.lower())
        if record is None:
            raise SandboxError("account_not_found", "unknown account")
        if record["password"] != password:
            raise SandboxError("bad_password", "wrong password")
        if record["totp_secret"]:
            if not totp_code:
                raise SandboxError("totp_required", "2fa code required")
            if not verify_totp(record["totp_secret"], totp_code):
                raise SandboxError("bad_totp", "2fa code rejected")
        elif record["recovery_email"]:
            if not recovery_code:
                raise SandboxError("recovery_required", "recovery code required")
            expected = record.get("recovery_code_expected")
            if expected is not None and recovery_code.strip() != expected:
                raise SandboxError("bad_recovery", "recovery code rejected")
        token = f"sess_{email.lower()}_{len(self.sessions)}"
        with self.lock:
            self.sessions[token] = email.lower()
        return token

    def subscribe(self, email: str, offer_id: str, card: dict, address: dict) -> dict:
        record = self.accounts[email.lower()]
        offer = next((o for o in self.offers if o["id"] == offer_id), None)
        if offer is None:
            raise SandboxError("unknown_offer", offer_id)
        if offer_id in {layer["id"] for layer in record["layers"]}:
            raise SandboxError("offer_already_stacked", offer_id)
        try:
            profile = checkout_profile(card, address)
        except CardError as exc:
            raise SandboxError("payment_declined", str(exc)) from exc
        record["layers"].append({"id": offer["id"], "months": offer["months"]})
        months = sum(layer["months"] for layer in record["layers"])
        today = date.today()
        total_months = today.month - 1 + months
        record["expiry"] = date(today.year + total_months // 12, total_months % 12 + 1, min(today.day, 28))
        record["renewing"] = True
        return {
            "total": "0.00",
            "currency": "USD",
            "expiry": record["expiry"].isoformat(),
            "months_stacked": months,
            "brand": profile["brand"],
            "last4": profile["last4"],
            "sales_tax_exempt_state": profile["sales_tax_exempt_state"],
        }

    def cancel(self, email: str) -> dict:
        record = self.accounts[email.lower()]
        record["renewing"] = False
        return {"renewing": False, "expiry": record["expiry"].isoformat() if record["expiry"] else None}


def _render_plans(state: dict) -> str:
    rows = []
    for offer in state["offers"]:
        disabled = "" if offer["available"] else "disabled"
        rows.append(
            f'<li><span>{offer["label"]}</span>'
            f'<button data-offer="{offer["id"]}" {disabled}>Subscribe</button></li>'
        )
    return (
        "<main id=plans>"
        f"<p id=stacked>Months stacked: <b id=months>{state['months_stacked']}</b></p>"
        f"<p id=expiry>Expiry: <b id=expiry-value>{state['expiry'] or '-'}</b></p>"
        f"<ul id=offer-list>{''.join(rows)}</ul>"
        "<section id=checkout hidden>"
        "<p>Total due today: <b id=total-amount>$0.00</b></p>"
        "<label>Card number <input id=card-number autocomplete=off></label>"
        "<label>Expiry <input id=card-exp autocomplete=off></label>"
        "<label>CVC <input id=card-cvc autocomplete=off></label>"
        "<label>Name <input id=card-holder autocomplete=off></label>"
        "<label>Address <input id=addr1 autocomplete=off></label>"
        "<label>City <input id=locality autocomplete=off></label>"
        "<label>State <input id=province autocomplete=off></label>"
        "<label>ZIP <input id=postal autocomplete=off></label>"
        "<button id=confirm-subscribe>Confirm subscription</button>"
        "</section></main>"
    )


PLAN_SCRIPT = """
const params = new URLSearchParams(window.location.search);
document.querySelectorAll('[data-offer]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.getElementById('checkout').hidden = false;
    document.querySelectorAll('[data-offer]').forEach((b) => b.classList.remove('selected'));
    btn.classList.add('selected');
    window.__offer = btn.dataset.offer;
  });
});
document.getElementById('confirm-subscribe').addEventListener('click', async () => {
  const body = {
    offer_id: window.__offer,
    card: {
      number: document.getElementById('card-number').value,
      exp_month: document.getElementById('card-exp').value.split('/')[0],
      exp_year: document.getElementById('card-exp').value.split('/')[1],
      cvc: document.getElementById('card-cvc').value,
      holder: document.getElementById('card-holder').value
    },
    address: {
      address_line1: document.getElementById('addr1').value,
      locality: document.getElementById('locality').value,
      province: document.getElementById('province').value,
      postal_code: document.getElementById('postal').value,
      country: 'US'
    }
  };
  const res = await fetch('/mock/api/subscribe', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
  const data = await res.json();
  if (!res.ok) { document.getElementById('checkout').insertAdjacentHTML('beforeend', '<p id=error>' + data.message + '</p>'); return; }
  window.location.href = '/mock/one/plans';
});
"""


class MockHandler(BaseHTTPRequestHandler):
    server_version = "SandboxGoogle/1.0"
    state: SandboxState

    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, html: str) -> None:
        body = html.encode()
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError:
            return {}

    def _session_email(self) -> str:
        token = self.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token:
            cookie = self.headers.get("cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "sbx_session":
                    token = value
        email = self.state.sessions.get(token)
        if not email:
            raise SandboxError("unauthenticated", "no active session")
        return email

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/mock/accounts/signin":
                return self._html(200, SIGNIN_HTML)
            if parsed.path == "/mock/one/plans":
                email = self._session_email()
                return self._html(200, "<html><body><h1>Google One</h1>" + _render_plans(self.state.state_for(email)) + f"<script>{PLAN_SCRIPT}</script></body></html>")
            if parsed.path == "/mock/play/subscriptions":
                email = self._session_email()
                current = self.state.state_for(email)
                renewing = "true" if current["renewing"] else "false"
                return self._html(
                    200,
                    "<html><body><h1>Play subscriptions</h1>"
                    f"<p id=renewing>{renewing}</p>"
                    f"<p id=expiry-value>{current['expiry'] or '-'}</p>"
                    "<button id=cancel-renewal>Cancel subscription</button>"
                    "<script>"
                    "document.getElementById('cancel-renewal').addEventListener('click', async () => {"
                    "await fetch('/mock/api/cancel', {method:'POST'});"
                    "document.getElementById('renewing').textContent='false';"
                    "});"
                    "</script></body></html>",
                )
            if parsed.path == "/mock/api/proof":
                body = proof_png()
                self.send_response(200)
                self.send_header("content-type", "image/png")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/mock/api/plans":
                return self._json(200, self.state.state_for(self._session_email()))
            if parsed.path == "/mock/api/subscriptions":
                email = self._session_email()
                current = self.state.state_for(email)
                return self._json(200, {"renewing": current["renewing"], "expiry": current["expiry"], "months_stacked": current["months_stacked"]})
            return self._json(404, {"error": "not_found"})
        except SandboxError as exc:
            return self._json(401, {"error": exc.code, "message": str(exc)})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if parsed.path == "/mock/api/login":
                token = self.state.login(
                    body.get("email", ""),
                    body.get("password", ""),
                    body.get("recovery_code", ""),
                    body.get("totp_code", ""),
                )
                self.send_response(200)
                payload = json.dumps({"token": token}).encode()
                self.send_header("content-type", "application/json")
                self.send_header("set-cookie", f"sbx_session={token}; Path=/; HttpOnly")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if parsed.path == "/mock/api/form-login":
                token = self.state.login(
                    body.get("email", ""),
                    body.get("password", ""),
                    body.get("recovery_code", ""),
                    body.get("totp_code", ""),
                )
                return self._json(200, {"token": token})
            if parsed.path == "/mock/api/subscribe":
                email = self._session_email()
                return self._json(200, self.state.subscribe(email, body.get("offer_id", ""), body.get("card", {}), body.get("address", {})))
            if parsed.path == "/mock/api/cancel":
                email = self._session_email()
                return self._json(200, self.state.cancel(email))
            return self._json(404, {"error": "not_found"})
        except SandboxError as exc:
            status = 402 if exc.code == "payment_declined" else 400
            return self._json(status, {"error": exc.code, "message": str(exc)})


SIGNIN_HTML = """<html><body><h1>Sign in</h1>
<form id=login onsubmit="return false">
<label>Email <input id=identifierId autocomplete=off></label>
<button id=identifierNext>Next</button>
<label>Password <input id=password type=password autocomplete=off></label>
<button id=passwordNext>Next</button>
<label>2FA code <input id=totpPin autocomplete=off></label>
<label>Recovery code <input id=auxCode autocomplete=off></label>
<button id=submitNext>Verify</button>
</form>
<script>
document.getElementById('submitNext').addEventListener('click', async () => {
  const res = await fetch('/mock/api/login', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({
    email: document.getElementById('identifierId').value,
    password: document.getElementById('password').value,
    totp_code: document.getElementById('totpPin').value,
    recovery_code: document.getElementById('auxCode').value
  })});
  if (!res.ok) { document.body.insertAdjacentHTML('beforeend','<p id=error>sign in failed</p>'); return; }
  window.location.href = '/mock/one/plans';
});
</script></body></html>"""


class SandboxServer:
    """Threaded sandbox Google server bound to the loopback interface."""

    def __init__(self, port: int = 0, offers: list[dict] | None = None) -> None:
        self.state = SandboxState(offers)
        handler = type("BoundMockHandler", (MockHandler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "SandboxServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "SandboxServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
