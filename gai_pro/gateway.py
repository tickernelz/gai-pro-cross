"""Gateways that drive the sandbox flow or the real Google flow."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .totp import totp


class GatewayError(RuntimeError):
    pass


class ZeroCostViolation(GatewayError):
    pass


class SandboxGateway:
    """HTTP driver for the local sandbox Google server."""

    def __init__(self, base_url, account, layout, store, timeout=20.0):
        self.base_url = base_url.rstrip("/")
        self.account = account
        self.layout = layout
        self.store = store
        self.timeout = timeout
        self.token = ""
        self.log = []

    def _note(self, message):
        self.log.append(message)

    def _request(self, method, path, payload=None):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("content-type", "application/json")
        if self.token:
            request.add_header("authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            try:
                detail = json.loads(body)
            except json.JSONDecodeError:
                detail = {"error": "http_error", "message": body}
            raise GatewayError(f"{exc.code} {detail.get('error', '')}: {detail.get('message', '')}") from exc
        except urllib.error.URLError as exc:
            raise GatewayError(f"sandbox unreachable: {exc.reason}") from exc

    def login(self, account):
        totp_code = totp(account["totp_secret"]) if account.get("totp_secret") else ""
        payload = {
            "email": account["email"],
            "password": account["password"],
            "totp_code": totp_code,
            "recovery_code": account.get("recovery_code", "") or "",
        }
        result = self._request("POST", "/mock/api/login", payload)
        self.token = result.get("token", "")
        if not self.token:
            raise GatewayError("sandbox login returned no session token")
        self._note(f"login ok for {account['email']}")

    def plan_state(self):
        return self._request("GET", "/mock/api/plans")

    def subscribe(self, offer_id, card, address):
        return self._request("POST", "/mock/api/subscribe", {"offer_id": offer_id, "card": card, "address": address})

    def subscriptions(self):
        return self._request("GET", "/mock/api/subscriptions")

    def cancel_renewal(self):
        self._request("POST", "/mock/api/cancel", {})

    def capture_proof(self, tag):
        out_dir = self.layout.screenshot_dir(self.account["email"])
        target = out_dir / f"{tag}.png"
        with urllib.request.urlopen(f"{self.base_url}/mock/api/proof", timeout=self.timeout) as response:
            target.write_bytes(response.read())
        return str(target)

    def close(self):
        return None


SANDBOX_SELECTORS = {
    "base_url": "",
    "signin_url": "/mock/accounts/signin",
    "plans_url": "/mock/one/plans",
    "subscriptions_url": "/mock/play/subscriptions",
    "email": "#identifierId",
    "email_next": "#identifierNext",
    "password": "#password",
    "password_next": "#passwordNext",
    "totp": "#totpPin",
    "recovery": "#auxCode",
    "submit": "#submitNext",
    "offer_button": '[data-offer="{offer}"]',
    "checkout": "#checkout",
    "total": "#total-amount",
    "card_number": "#card-number",
    "card_exp": "#card-exp",
    "card_cvc": "#card-cvc",
    "card_holder": "#card-holder",
    "addr1": "#addr1",
    "locality": "#locality",
    "province": "#province",
    "postal": "#postal",
    "confirm": "#confirm-subscribe",
    "renewing": "#renewing",
    "cancel_renewal": "#cancel-renewal",
    "stacked_months": "#months",
    "expiry": "#expiry-value",
}

GOOGLE_SELECTORS = {
    "base_url": "https://accounts.google.com",
    "signin_url": "https://accounts.google.com/ServiceLogin?hl=en&continue=https%3A%2F%2Fone.google.com%2Fplans",
    "plans_url": "https://one.google.com/plans",
    "subscriptions_url": "https://play.google.com/store/account/subscriptions",
    "email": 'input[type="email"], input#identifierId',
    "email_next": '#identifierNext, button:has-text("Next")',
    "password": 'input[type="password"], input[name="Passwd"]',
    "password_next": '#passwordNext, button:has-text("Next")',
    "totp": 'input#totpPin, input[name="totpPin"], input[type="tel"]',
    "recovery": 'input#knowledge-preregistered-email-response, input[name="knowledgePreregisteredEmailResponse"]',
    "submit": 'button:has-text("Next"), button:has-text("Verify")',
    "offer_button": 'button:has-text("Try"), button:has-text("Subscribe"), button:has-text("Get started")',
    "checkout": 'iframe[src*="payments"], [role="dialog"]',
    "total": "body",
    "card_number": 'input[autocomplete="cc-number"], input[name="cardNumber"]',
    "card_exp": 'input[autocomplete="cc-exp"], input[name="cardExpiry"]',
    "card_cvc": 'input[autocomplete="cc-csc"], input[name="cvc"]',
    "card_holder": 'input[autocomplete="cc-name"], input[name="cardholderName"]',
    "addr1": 'input[autocomplete="address-line1"]',
    "locality": 'input[autocomplete="address-level2"]',
    "province": 'input[autocomplete="address-level1"]',
    "postal": 'input[autocomplete="postal-code"]',
    "confirm": 'button:has-text("Subscribe"), button:has-text("Start trial"), button:has-text("Confirm")',
    "renewing": "body",
    "cancel_renewal": 'button:has-text("Cancel subscription"), button:has-text("Cancel")',
    "stacked_months": "",
    "expiry": "",
}


class BrowserGateway:
    """Playwright driver running the pipeline against the sandbox or Google profile."""

    def __init__(self, profile, account, layout, store, headless=True, proxy=""):
        if profile not in ("sandbox", "google"):
            raise GatewayError(f"unknown profile {profile}")
        self.profile = profile
        self.selectors = SANDBOX_SELECTORS if profile == "sandbox" else GOOGLE_SELECTORS
        self.account = account
        self.layout = layout
        self.store = store
        self.headless = headless
        self.proxy = proxy
        self._base = ""
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self.log = []

    def _note(self, message):
        self.log.append(message)

    def start(self, base_url=""):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise GatewayError("playwright missing; run: pip install -e .[live]") from exc
        self._base = (base_url or self.selectors["base_url"]).rstrip("/")
        self._playwright = sync_playwright().start()
        launch = {"headless": self.headless}
        if self.proxy:
            launch["proxy"] = {"server": self.proxy}
        self._browser = self._playwright.chromium.launch(**launch)
        self._context = self._browser.new_context(locale="en-US")
        self._page = self._context.new_page()

    def _url(self, path):
        return path if path.startswith("http") else f"{self._base}{path}"

    def _fill(self, key, value):
        if not value:
            return
        selector = self.selectors[key]
        self._page.wait_for_selector(selector, timeout=30000, state="visible")
        self._page.fill(selector, value)

    def _click(self, key, timeout=30000):
        selector = self.selectors[key]
        self._page.wait_for_selector(selector, timeout=timeout)
        self._page.click(selector)

    def login(self, account):
        page = self._page
        page.goto(self._url(self.selectors["signin_url"]), wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        self._fill("email", account["email"])
        self._click("email_next")
        page.wait_for_timeout(400)
        self._fill("password", account["password"])
        self._click("password_next")
        page.wait_for_timeout(700)
        if account.get("totp_secret"):
            self._fill("totp", totp(account["totp_secret"]))
        elif account.get("recovery_code"):
            self._fill("recovery", account["recovery_code"])
        try:
            self._click("submit", timeout=4000)
        except Exception:
            self._note("submit control absent, challenge already satisfied")
        page.wait_for_timeout(900)
        self._note(f"login finished at {page.url}")

    def plan_state(self):
        page = self._page
        page.goto(self._url(self.selectors["plans_url"]), wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        months = 0
        expiry = None
        if self.selectors["stacked_months"]:
            months_text = page.text_content(self.selectors["stacked_months"]) or "0"
            months = int("".join(ch for ch in months_text if ch.isdigit()) or 0)
            if self.selectors["expiry"]:
                expiry_text = (page.text_content(self.selectors["expiry"]) or "").strip()
                expiry = expiry_text if expiry_text and expiry_text != "-" else None
        return {"months_stacked": months, "expiry": expiry, "renewing": None, "offers": []}

    def subscriptions(self):
        page = self._page
        page.goto(self._url(self.selectors["subscriptions_url"]), wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        return {"renewing": (page.text_content(self.selectors["renewing"]) or "").strip()[:200]}

    def cancel_renewal(self):
        target = self.selectors["subscriptions_url"]
        if target not in self._page.url:
            self._page.goto(self._url(target), wait_until="domcontentloaded")
            self._page.wait_for_timeout(400)
        self._click("cancel_renewal")
        self._page.wait_for_timeout(400)

    def _click_offer(self, offer_id):
        template = self.selectors["offer_button"]
        selector = template.format(offer=offer_id) if "{offer}" in template else template
        self._page.wait_for_selector(selector, timeout=20000)
        self._page.click(selector)

    def _checkout_scope(self):
        selector = self.selectors["checkout"]
        try:
            self._page.wait_for_selector(selector, timeout=15000)
            return self._page
        except Exception:
            for frame in self._page.frames:
                try:
                    if frame.query_selector(selector):
                        return frame
                except Exception:
                    continue
        raise GatewayError("checkout surface did not appear")

    def subscribe(self, offer_id, card, address):
        self._click_offer(offer_id)
        scope = self._checkout_scope()
        total_text = (scope.text_content(self.selectors["total"]) or "").strip()
        amounts = re.findall(r"\$?\s?(\d+(?:\.\d{2})?)", total_text)
        if any(float(a) > 0 for a in amounts if a):
            raise ZeroCostViolation(f"checkout shows {total_text[:120]!r}, refusing to confirm")
        if "0.00" not in total_text and not re.search(r"free|trial", total_text, re.I):
            raise ZeroCostViolation(f"checkout total unclear ({total_text[:120]!r}), refusing to confirm")
        scope.fill(self.selectors["card_number"], card["number"])
        scope.fill(self.selectors["card_exp"], f"{card['exp_month']}/{str(card['exp_year'])[-2:]}")
        scope.fill(self.selectors["card_cvc"], card["cvc"])
        scope.fill(self.selectors["card_holder"], card["holder"])
        scope.fill(self.selectors["addr1"], address["address_line1"])
        scope.fill(self.selectors["locality"], address["locality"])
        scope.fill(self.selectors["province"], address["province"])
        scope.fill(self.selectors["postal"], address["postal_code"])
        scope.click(self.selectors["confirm"])
        self._page.wait_for_timeout(700)
        months = 0
        try:
            months_text = self._page.text_content(self.selectors["stacked_months"]) or "0"
            months = int("".join(ch for ch in months_text if ch.isdigit()) or 0)
        except Exception:
            months = 0
        return {"total": "0.00", "offer_id": offer_id, "months_stacked": months}

    def capture_proof(self, tag):
        out_dir = self.layout.screenshot_dir(self.account["email"])
        target = out_dir / f"{tag}.png"
        self._page.screenshot(path=str(target), full_page=True)
        return str(target)

    def close(self):
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass

