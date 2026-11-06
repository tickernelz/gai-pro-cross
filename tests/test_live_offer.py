import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is only needed for the browser path")

from playwright.sync_api import sync_playwright

from gai_pro.accounts import new_account_record, parse_line
from gai_pro.gateway import BrowserGateway, GatewayError

PLANS_HTML = """
<html><body>
  <div class="grid">
    <section class="card"><h2>Google AI Plus</h2>
      <button data-size="400GB">400 GB</button>
      <button onclick="window.cta='plus'">Mulai</button>
    </section>
    <section class="card"><h2>Google AI Pro</h2>
      <button data-size="5TB">5 TB</button>
      <button data-size="10TB">10 TB</button>
      <button onclick="window.cta='pro'">Dapatkan diskon</button>
    </section>
    <section class="card"><h2>Google AI Ultra</h2>
      <button onclick="window.cta='ultra'">Mulai</button>
    </section>
  </div>
  <section class="card" style="display: none"><h2>Google AI Pro</h2>
    <button onclick="window.cta='hidden'">Mulai</button>
  </section>
</body></html>
"""


def gateway_for(layout, page):
    account = new_account_record(parse_line("offer@example.com----pw"))
    gateway = BrowserGateway("google", account, layout, None)
    gateway._page = page
    return gateway


def test_live_offer_picks_the_visible_google_ai_pro_call_to_action(layout):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(PLANS_HTML)
        gateway = gateway_for(layout, page)

        button = gateway._offer_button()

        assert button is not None
        assert button.inner_text().strip() == "Dapatkan diskon"
        assert "Google AI Pro" in button.evaluate("(el) => el.closest('section').innerText")
        browser.close()


def test_live_offer_reports_candidates_when_no_google_ai_pro_card_exists(layout):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<html><body><button>Mulai</button><button>Beli penyimpanan</button></body></html>")
        gateway = gateway_for(layout, page)

        with pytest.raises(GatewayError) as excinfo:
            gateway._click_offer("pro_5tb_12m")

        message = str(excinfo.value)
        assert "Google AI Pro" in message
        assert "Mulai" in message
        browser.close()


def test_checkout_scope_returns_the_play_billing_frame(layout):
    body = b"<html><body>Google AI Pro (5 TB)<p>Rp 85.470/month</p></body></html>"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(
                f'<iframe src="http://127.0.0.1:{server.server_port}/eacquire/multiline"></iframe>'
            )
            gateway = gateway_for(layout, page)

            scope = gateway._checkout_scope()

            assert scope.url.endswith("/eacquire/multiline")
            text = scope.locator("body").inner_text()
            assert "Rp 85.470" in text
            assert gateway._amounts(text)[0] == 85470.0
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_checkout_scope_names_the_page_when_no_checkout_appears(layout):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<html><body><button>Batal</button></body></html>")
        gateway = gateway_for(layout, page)

        with pytest.raises(GatewayError) as excinfo:
            gateway._checkout_scope()

        assert "checkout surface did not appear" in str(excinfo.value)
        assert "Batal" in str(excinfo.value)
        browser.close()


def test_amounts_read_indonesian_and_plain_prices():
    account = new_account_record(parse_line("amounts@example.com----pw"))
    gateway = BrowserGateway("google", account, None, None)

    assert gateway._amounts("Dimulai hari ini Rp 85.470/month") == [85470.0]
    assert gateway._amounts("Rp 0") == [0.0]
    assert gateway._amounts("$0.00 today") == [0.0]
    assert gateway._amounts("no prices here") == []
