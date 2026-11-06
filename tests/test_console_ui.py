import threading

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is only needed for the browser path")

from playwright.sync_api import expect, sync_playwright

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


@pytest.fixture()
def page(console):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        tab = browser.new_page()
        tab.goto(console.url, wait_until="load")
        yield tab
        browser.close()


def test_console_is_clickable_and_proof_overlay_opens_only_on_demand(console, page):
    overlay = page.locator("#modalImg")
    expect(overlay).to_be_hidden()
    assert overlay.evaluate("node => node.hasAttribute('hidden')") is True
    assert page.evaluate(
        "() => document.elementFromPoint(innerWidth / 2, innerHeight / 2).closest('#modalImg') === null"
    )

    page.fill("#importText", "ui@example.com----pw----rec@example.com----JBSWY3DPEHPK3PXP")
    page.click("#btnImport")
    page.wait_for_function("() => document.getElementById('statusLine').textContent.includes('1 account(s) imported')")

    page.click("#accountsTbody button[data-login]")
    page.wait_for_function("() => document.querySelector('#accountsTbody .status-done') !== null", timeout=20000)
    page.wait_for_function("() => document.querySelector('#accountsTbody button[data-run]').disabled === false", timeout=20000)

    page.click("#btnBatchRun")
    page.wait_for_function("() => document.querySelector('#accountsTbody td.status-done') !== null", timeout=30000)

    page.click("#accountsTbody button[data-shot]")
    expect(overlay).to_be_visible()
    page.wait_for_function("() => document.getElementById('imgModal').naturalWidth > 0")

    page.keyboard.press("Escape")
    expect(overlay).to_be_hidden()
    assert overlay.evaluate("node => node.hasAttribute('hidden')") is True
