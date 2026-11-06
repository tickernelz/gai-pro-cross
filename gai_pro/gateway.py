"""Gateways that drive the sandbox flow or the real Google flow."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import chrome
from .paths import secure_file
from .redact import mask_email
from .session import SESSION_MISSING, has_google_auth_cookie, has_session, read_session, write_session
from .totp import totp


class GatewayError(RuntimeError):
    pass


class SessionMissing(GatewayError):
    """Raised when the pipeline runs before a login stored a session."""


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

    def _session_note(self, message):
        self._note(message)
        if self.store is not None:
            self.store.log(f"{self.account.get('email', '')}: {message}", "session")

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
        path = write_session(self.layout, account["id"], {"mode": "mock", "email": account["email"]})
        self._session_note(f"mock session stored at {path}")
        return path

    def verify_session(self):
        state = read_session(self.layout, self.account["id"])
        if not state or state.get("mode") != "mock":
            return False
        if str(state.get("email", "")).lower() != str(self.account.get("email", "")).lower():
            return False
        try:
            result = self._request("POST", "/mock/api/session", {"email": state["email"]})
        except GatewayError:
            return False
        self.token = result.get("token", "")
        if not self.token:
            return False
        self._session_note("stored session resumed")
        return True

    def capture_diagnostic(self, tag):
        return None

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
        self._chrome_process = None
        self._attached = False
        self.log = []

    def _note(self, message):
        self.log.append(message)

    def _session_note(self, message):
        self._note(message)
        if self.store is not None:
            self.store.log(f"{self.account.get('email', '')}: {message}", "session")

    def _headless(self):
        return False if self.profile == "google" else bool(self.headless)

    def start(self, base_url=""):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise GatewayError("playwright missing; run: pip install -e .[live]") from exc
        self._base = (base_url or self.selectors["base_url"]).rstrip("/")
        self._playwright = sync_playwright().start()
        try:
            if self.profile == "google":
                self._open_live_browser()
            else:
                self._open_sandbox_browser()
        except Exception:
            self._stop_playwright()
            raise

    def _open_sandbox_browser(self):
        profile_dir = self.layout.profile_dir(self.account["id"])
        options = {
            "user_data_dir": str(profile_dir),
            "headless": self._headless(),
            "locale": "en-US",
        }
        if self.proxy:
            options["proxy"] = {"server": self.proxy}
        try:
            self._context = self._playwright.chromium.launch_persistent_context(**options)
        except Exception as exc:
            raise GatewayError(f"browser launch failed: {exc}") from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._note(f"browser ready with bundled chromium at {profile_dir}")

    def _devtools_marker(self):
        return self.layout.devtools_file(self.account["id"])

    def _recorded_port(self):
        try:
            payload = json.loads(self._devtools_marker().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        port = payload.get("port") if isinstance(payload, dict) else None
        return port if isinstance(port, int) and port > 0 else None

    def _remember_port(self, port):
        marker = self._devtools_marker()
        marker.write_text(json.dumps({"port": int(port)}), encoding="utf-8")
        secure_file(marker)

    def _forget_port(self):
        try:
            self._devtools_marker().unlink()
        except OSError:
            pass

    def _open_live_browser(self):
        self._attached = False
        profile_dir = self.layout.profile_dir(self.account["id"])
        recorded = self._recorded_port()
        if recorded is not None and chrome.cdp_version(recorded):
            endpoint = f"http://127.0.0.1:{recorded}"
            self._attached = True
            self._note(f"attached to the Chrome already listening on {endpoint}")
        else:
            port = chrome.free_port()
            try:
                self._chrome_process = chrome.launch(profile_dir, port, self.proxy)
            except chrome.ChromeError as exc:
                raise GatewayError(str(exc)) from exc
            if not chrome.wait_for_cdp(port):
                chrome.stop(self._chrome_process)
                self._chrome_process = None
                raise GatewayError(
                    f"DevTools port tidak terbuka di http://127.0.0.1:{port} (DevTools port did not open); "
                    "tutup Chrome yang memakai profil ini lalu coba lagi"
                )
            endpoint = f"http://127.0.0.1:{port}"
            self._remember_port(port)
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(endpoint)
        except Exception as exc:
            if not self._attached:
                chrome.stop(self._chrome_process)
                self._chrome_process = None
                self._forget_port()
            raise GatewayError(f"gagal connect ke Chrome lewat DevTools di {endpoint}: {exc}") from exc
        contexts = getattr(self._browser, "contexts", None) or []
        if not contexts:
            self.close()
            raise GatewayError("Chrome tidak menyediakan context lewat DevTools (no browser context over CDP)")
        self._context = contexts[0]
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._note(f"Chrome langsung tanpa --enable-automation di {endpoint}, profile {profile_dir}")

    def _stop_playwright(self):
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None

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
        if self.profile == "google":
            return self.manual_login(account)
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
        self._session_note(f"mock sign-in finished at {page.url}")
        return self._persist_session(account)

    def manual_login(self, account, timeout=600.0, interval=3.0):
        page = self._page
        page.goto(self._url(self.selectors["signin_url"]), wait_until="domcontentloaded")
        hello = "browser stays open until you finish signing in; Ctrl+C to abort"
        self._announce(f"{hello} ({mask_email(account.get('email', ''))})")
        started = time.time()
        deadline = started + max(30.0, float(timeout))
        pause = max(1.0, float(interval))
        while time.time() < deadline:
            if self.context_authenticated():
                if not has_google_auth_cookie(self._context.storage_state().get("cookies")):
                    self._session_note("exported storage_state has no Google login cookie yet; still waiting")
                    time.sleep(pause)
                    continue
                path = self._persist_session(account)
                self._announce(f"session saved (SID present) at {path}")
                self.close()
                return path
            elapsed = int(time.time() - started)
            remaining = int(deadline - time.time())
            self._announce(f"menunggu login selesai... {elapsed}s elapsed, {remaining}s left")
            time.sleep(pause)
        raise SessionMissing(f"sesi belum valid — manual login timed out after {int(timeout)}s")

    def context_authenticated(self):
        if self._context is None:
            return False
        try:
            return has_google_auth_cookie(self._context.cookies())
        except Exception:
            return False

    def _announce(self, message):
        self._session_note(message)
        print(message, flush=True)

    def verify_session(self):
        mode = "live" if self.profile == "google" else "sandbox"
        if not has_session(self.layout, self.account["id"], mode):
            return False
        self._restore_session()
        if self.profile == "google":
            if not self.context_authenticated():
                self._session_note("stored session has no Google login cookie")
                return False
            self._session_note("stored session is valid (Google login cookie present)")
            return True
        self._session_note("stored session is valid")
        return True

    def _persist_session(self, account):
        state = self._context.storage_state()
        payload = {
            "mode": "live" if self.profile == "google" else "sandbox",
            "email": account.get("email", ""),
            "cookies": state.get("cookies", []),
            "origins": state.get("origins", []),
        }
        return write_session(self.layout, account["id"], payload)

    def _restore_session(self):
        state = read_session(self.layout, self.account["id"])
        if not state:
            return
        cookies = state.get("cookies")
        if not isinstance(cookies, list) or not cookies:
            return
        try:
            self._context.add_cookies(cookies)
            self._note("stored cookies loaded into the browser profile")
        except Exception as exc:
            self._note(f"stored cookies rejected: {exc}")

    def capture_diagnostic(self, tag):
        if self._page is None:
            return None
        out_dir = self.layout.screenshot_dir(self.account["email"])
        image = out_dir / f"{tag}.png"
        report = out_dir / f"{tag}.url.txt"
        url = self._page.url
        self._page.screenshot(path=str(image), full_page=True)
        report.write_text(url + "\n", encoding="utf-8")
        secure_file(image)
        secure_file(report)
        return {"screenshot": str(image), "url_file": str(report), "url": url}

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
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        self._page = None
        if self._attached:
            self._browser = None
            self._context = None
            self._stop_playwright()
            self._attached = False
            return
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        if self._chrome_process is not None:
            chrome.stop(self._chrome_process)
            self._chrome_process = None
            self._forget_port()
        self._stop_playwright()

