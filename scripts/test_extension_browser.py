"""Installed MV3 connection/source tests: real worker + HTTP handler, fictional data only.

Requires Python Playwright and its Chromium, and a FREE loopback port 8770.
Run separately from a running Wingman service. Never reads .env or local-config.js.
"""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import app


@pytest.mark.parametrize("token", ["", "fictional-extension-pair"])
def test_installed_connection(tmp_path, monkeypatch, token):
    monkeypatch.setattr(app, "WINGMAN_TOKEN", "fictional-extension-pair")
    monkeypatch.setenv("WINGMAN_READ_ONLY", "1")
    monkeypatch.setenv("WORKIVA_CLIENT_ID", "fictional-id")
    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "fictional-secret")
    upstream_calls = []

    def refuse_workiva():
        upstream_calls.append("unexpected OAuth request")
        raise AssertionError("A connection check must never request Workiva access")

    monkeypatch.setattr(app, "_token", refuse_workiva)
    requests = []
    delay = threading.Event()
    released = threading.Event()
    document_delay = threading.Event()
    document_released = threading.Event()
    document_sent = threading.Event()

    class Handler(app.Handler):
        # No keep-alive threads may outlive shutdown during the offline assertion.
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("X-Wingman-Token") == "fictional-extension-pair"))
            if self.path == "/api/connection" and delay.is_set():
                released.wait(15)
                self.close_connection = True
                return
            if self.path.startswith(("/api/inspect-document?", "/api/document-tables?")) and document_delay.is_set():
                document_released.wait(45)
                super().do_GET()
                document_sent.set()
                return
            super().do_GET()

    # A busy port fails without inspecting, stopping or replacing its owner.
    server = ThreadingHTTPServer(("127.0.0.1", 8770), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        extension = tmp_path / "extension"
        extension.mkdir()
        manifest = json.loads((ROOT / "extension/manifest.json").read_text())
        assets = {"manifest.json", manifest["background"]["service_worker"], "vision-capture.js",
                  manifest["options_ui"]["page"], "setup.js", "setup.css"}
        assets.update(manifest["icons"].values())
        for script in manifest["content_scripts"]:
            assets.update(script["js"])
        for asset in assets:
            target = extension / asset
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "extension" / asset, target)
        (extension / "local-config.js").write_text("globalThis.WINGMAN_LOCAL_CONFIG = " + json.dumps({"token": token}) + ";")
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(tmp_path / "profile"), channel="chromium", headless=True, chromium_sandbox=True,
                executable_path=os.environ.get("WINGMAN_TEST_CHROMIUM") or playwright.chromium.executable_path,
                viewport={"width": 1280, "height": 900}, device_scale_factor=2,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"],
            )
            try:
                page_url = "https://app.wdesk.com/a/fictional-workspace/spreadsheet/abc123/-1/sheet/def456"

                def route_request(route):
                    if route.request.url == page_url:
                        route.fulfill(content_type="text/html", body="""<!doctype html><html lang="en">
                            <title>Fictional extension test</title><body style="font:16px system-ui;background:#f3f4f6;padding:32px">
                            <h1>Fictional workbook</h1><p>Installed-extension test. No Workiva session or live data.</p>
                            <p>Selected cell:</p><div class="dt-formula-cell-indicator">B2</div>
                            <table border="1" cellpadding="16"><tr><th>Label</th><th>Fiscal year</th></tr>
                            <tr><td>Report period</td><td>2025</td></tr></table></body></html>""")
                    elif route.request.url.startswith(("http://127.0.0.1:8770/", "chrome-extension://")):
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", route_request)
                errors = []
                worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                setup_url = worker.url.rsplit("/", 1)[0] + "/" + manifest["options_ui"]["page"]
                # Chrome reuses its options tab. A manual goto can race the
                # onInstalled openOptionsPage navigation to that same tab.
                worker.evaluate("() => chrome.runtime.openOptionsPage()")
                # The API acknowledges tab creation before Playwright sees its URL.
                deadline = time.monotonic() + 10
                setup = None
                while setup is None and time.monotonic() < deadline:
                    setup = next((page for page in context.pages if page.url == setup_url), None)
                    if setup is None:
                        context.pages[0].wait_for_timeout(50)
                assert setup is not None, "Chrome did not open the extension options page"
                setup.on("pageerror", lambda error: errors.append(str(error)))
                setup_title = setup.locator(".wc-status h3")
                expect(setup_title).to_have_text("Not checked")
                assert not [r for r in requests if r[0] != "/version"]
                setup.emulate_media(color_scheme="dark")
                setup.screenshot(path=str(tmp_path / "setup-unchecked.png"))
                setup.locator(".wc-check").focus()
                setup.keyboard.press("Enter")
                expect(setup_title).to_have_text("Service connected" if token else "Extension setup needed")
                expect(setup.locator(".wc-check")).to_be_focused()
                setup.screenshot(path=str(tmp_path / "setup-result.png"))
                if token:
                    assert [r for r in requests if r[0] != "/version"] == [("/api/connection", True)]
                    expect(setup.locator(".wc-facts")).to_contain_text("Read-only — repairs disabled")
                    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "")
                    setup.locator(".wc-check").click()
                    expect(setup_title).to_have_text("Workiva setup incomplete")
                    setup.screenshot(path=str(tmp_path / "setup-credentials.png"))
                    setup.emulate_media(color_scheme="light")
                    setup.screenshot(path=str(tmp_path / "setup-light.png"))
                    monkeypatch.setattr(app, "WINGMAN_TOKEN", "different-fictional-pair")
                    setup.locator(".wc-check").click()
                    expect(setup_title).to_have_text("Service access rejected")
                    setup.screenshot(path=str(tmp_path / "setup-denied.png"))
                    monkeypatch.setattr(app, "WINGMAN_TOKEN", "fictional-extension-pair")
                    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "fictional-secret")
                    setup.locator(".wc-check").click()
                    expect(setup_title).to_have_text("Service connected")
                    setup.locator(".wc-copy").click()
                    expect(setup.locator("#copy-status")).to_have_text("Redacted diagnostics copied.")
                assert "fictional-extension-pair" not in setup.content()
                assert "fictional-secret" not in setup.content()
                setup.set_viewport_size({"width": 390, "height": 844})
                setup.locator(".wc-route summary").click()
                setup.locator("aside summary").click()
                setup.screenshot(path=str(tmp_path / "setup-narrow.png"), full_page=True)
                assert setup.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert setup.locator("button,summary").evaluate_all(
                    "els=>els.every(e=>e.getBoundingClientRect().height>=40)")
                assert not upstream_calls
                requests.clear()

                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(page_url)
                expect(page.locator(".wm-pill")).to_be_visible()
                page.bring_to_front()
                for opened in (True, False):
                    reply = setup.evaluate("""async () => {
                        const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
                        return chrome.tabs.sendMessage(tab.id, {type:'WM_TOGGLE'}, {frameId:0});
                    }""")
                    assert reply == {"ok": True}
                    expect(page.locator(".wm-panel" if opened else ".wm-pill")).to_be_visible()
                page.locator(".wm-pill").click()
                page.locator(".wc-toggle").click()
                title = page.locator(".wc-status h3")
                expect(title).to_have_text("Not checked")
                assert not [r for r in requests if r[0] != "/version"]

                def capture(name):
                    expect(page.locator(".wm-logo")).to_have_js_property("complete", True)
                    assert page.locator(".wm-logo").evaluate("e=>e.naturalWidth>0")
                    page.screenshot(path=str(tmp_path / (name + ".png")))

                capture("connection-unchecked")
                page.locator(".wc-check").focus()
                page.keyboard.press("Enter")
                expect(title).to_have_text("Service connected" if token else "Extension setup needed")
                capture("connection-connected" if token else "connection-setup")
                if not token:
                    assert not requests
                    assert not errors
                    print("Screenshots:", tmp_path)
                    return
                assert [r for r in requests if r[0] != "/version"] == [("/api/connection", True)]
                expect(page.locator(".wc-facts")).to_contain_text("Read-only — repairs disabled")
                expect(page.locator(".wc-facts")).to_contain_text("Configured, not validated")
                expect(page.locator(".wc-facts")).to_contain_text("Workbook accessNot tested")
                monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "")
                page.locator(".wc-check").click()
                expect(title).to_have_text("Workiva setup incomplete")
                capture("connection-credentials")
                monkeypatch.setattr(app, "WINGMAN_TOKEN", "different-fictional-pair")
                page.locator(".wc-check").click()
                expect(title).to_have_text("Service access rejected")
                capture("connection-denied")

                page.set_viewport_size({"width": 390, "height": 844})
                page.locator(".wc-route summary").click()
                capture("connection-narrow")
                page.locator(".wc-copy").scroll_into_view_if_needed()
                capture("connection-narrow-setup")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator(".wc-toggle,.wc-check,.wc-copy,.wc-route summary").evaluate_all(
                    "els=>els.every(e=>e.getBoundingClientRect().height>=40)")
                page.set_viewport_size({"width": 1280, "height": 900})
                page.locator(".wc-route summary").click()
                page.locator(".wm-body").evaluate("e=>e.scrollTop=0")
                page.locator('[title^="Theme:"]').click()
                capture("connection-light")

                monkeypatch.setattr(app, "WINGMAN_TOKEN", "fictional-extension-pair")
                delay.set()
                page.locator(".wc-check").click()
                expect(title).to_have_text("Checking connection…")
                capture("connection-checking")
                expect(title).to_have_text("Connection timed out", timeout=12000)
                capture("connection-timeout")
                released.set()
                delay.clear()
                # Same running service recovers after loss; no extension reload needed.
                page.locator(".wc-check").click()
                expect(title).to_have_text("Workiva setup incomplete")
                assert all(path in ("/version", "/api/connection") and paired for path, paired in requests)
                assert not upstream_calls
                requests.clear()

                # Real installed content script, worker and handler. Only the Workiva
                # transport is replaced with the same fictional fixture as the demo.
                # In particular, this does not use the demo's Chrome messaging adapter.
                from demo import Workbook
                book = Workbook()

                def fictional_read(path, _token, _ctx, version=None):
                    return book.request("GET", path, None)

                with monkeypatch.context() as source_patch:
                    source_patch.setattr(app, "_token", lambda: "fictional-inspection-token")
                    source_patch.setattr(app.inspector.wk, "_get", fictional_read)
                    source_patch.setattr(app.inspector.wk, "_get_url", fictional_read)
                    page_url = page_url.replace("abc123", "de00").replace("def456", "de02")
                    page.goto(page_url)
                    page.locator(".wm-pill").click()
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-evidence dd").first).to_have_text("2025")
                    page.locator(".wi-sources > summary").click()
                    page.locator(".wi-read-sources").click()
                    page.locator(".wi-follow").first.click()
                    expect(page.locator(".wi-address")).to_have_text("D6")
                    expect(page.locator(".wi-evidence dd").first).to_have_text("2024")
                    page.locator(".wi-follow").first.click()
                    expect(page.locator(".wi-address")).to_have_text("E11")
                    expect(page.locator(".wi-evidence dd").first).to_have_text("=E12+1")
                    expect(page.locator(".wi-origin")).to_contain_text("B2: 2025")
                    expect(page.locator(".wi-trail")).to_have_text("Selected B2→D6→E11")
                    expect(page.locator(".dt-formula-cell-indicator")).to_have_text("B2")
                    page.locator(".wm-body").evaluate("e=>e.scrollTop=0")
                    capture("installed-source-trail")
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "source-trail-panel.png"))
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.locator(".wi-follow").scroll_into_view_if_needed()
                    capture("installed-source-narrow-evidence")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert page.locator(".wi-inspector").evaluate("e=>e.scrollWidth<=e.clientWidth")
                    assert page.locator(".wi-follow,button.wi-trail-step,.wi-inspect").evaluate_all(
                        "els=>els.every(e=>e.getBoundingClientRect().height>=40)")
                    before_return = book.request_count
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-address")).to_have_text("B2")
                    expect(page.locator(".wi-evidence dd").first).to_have_text("2025")
                    assert book.request_count == before_return == 32
                    assert book.events == []
                    assert all(paired and (path == "/version" or path.startswith(("/api/inspect?", "/api/inspect-source?")))
                               for path, paired in requests)
                    assert sum(path.startswith("/api/inspect-source?") for path, _ in requests) == 2
                    assert all("workbookHint=de00" in path for path, _ in requests if path.startswith("/api/inspect-source?"))

                    # A source in the opened workbook gains context only after the
                    # real handler checks revision-bound sheet/table membership.
                    page_url = page_url.replace("de02", "de01")
                    page.goto(page_url)
                    page.locator(".dt-formula-cell-indicator").evaluate("e=>e.textContent='B7'")
                    page.locator(".wm-pill").click()
                    expect(page.locator(".wi-address")).to_have_text("B7")
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-evidence dd").first).to_have_text("=SUM(B3:B6)+12500")
                    page.locator(".wi-sources > summary").click()
                    page.locator(".wi-read-sources").click()
                    page.locator(".wi-follow").first.click()
                    expect(page.locator(".wi-address")).to_have_text("B3")
                    page.get_by_text("Context & raw format", exact=True).click()
                    location = page.get_by_text("Matched at the recorded revision. Workiva selection has not moved.", exact=True)
                    expect(location).to_be_visible()
                    location.scroll_into_view_if_needed()
                    capture("source-location-narrow")
                    assert page.locator(".wi-inspector").evaluate("e=>e.scrollWidth<=e.clientWidth")
                    page.set_viewport_size({"width": 1280, "height": 900})
                    location.scroll_into_view_if_needed()
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "source-location-panel.png"))
                    before_return = book.request_count
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-address")).to_have_text("B7")
                    assert book.request_count == before_return and book.events == []

                    # Document choice uses native-shaped URLs but fictional data.
                    # No cell address is supplied by the host page in this flow.
                    requests.clear()
                    page_url = "https://app.wdesk.com/a/fictional-workspace/doc/de10/r/-1/v/1/sec/de11"
                    page.goto(page_url)
                    page.locator(".dt-formula-cell-indicator").evaluate("e=>e.remove()")
                    page.locator(".wm-pill").click()
                    expect(page.locator(".wi-doc-heading")).to_have_text("Inspect a report table")
                    assert all(path == "/version" for path, _ in requests)
                    page.locator(".wi-doc-load").click()
                    expect(page.get_by_role("combobox", name="Table", exact=True)).to_be_visible()
                    assert page.locator(".wi-doc-table option").all_text_contents() == ["Choose a table", "1. Reporting period"]
                    page.get_by_role("combobox", name="Table", exact=True).select_option("demo-report-table")
                    page.get_by_label("Cell address", exact=True).fill("b2")
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-choice-light.png"))
                    page.locator('[title^="Theme:"]').click()
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-choice-dark.png"))
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.get_by_role("button", name="Inspect cell & direct sources", exact=True).scroll_into_view_if_needed()
                    capture("document-choice-narrow")
                    assert page.locator(".wi-inspector").evaluate("e=>e.scrollWidth<=e.clientWidth")
                    assert page.locator(".wi-doc-form input,.wi-doc-form select,.wi-doc-form button,.wi-doc-load").evaluate_all(
                        "els=>els.every(e=>e.getBoundingClientRect().height>=40)")
                    page.get_by_text("Context & revision", exact=True).click()
                    expect(page.locator(".wi-evidence")).to_contain_text("demo-report-5")
                    page.locator(".wi-evidence").scroll_into_view_if_needed()
                    capture("document-context-narrow")
                    page.get_by_text("Context & revision", exact=True).click()
                    page.get_by_label("Cell address", exact=True).press("Enter")
                    expect(page.locator(".wi-evidence dd").first).to_have_text("2025")
                    expect(page.locator(".wi-inspector")).to_contain_text("Explicit table-cell choice")
                    page.locator(".wi-follow").first.click()
                    expect(page.locator(".wi-address")).to_have_text("D6")
                    page.locator(".wi-follow").first.click()
                    expect(page.locator(".wi-address")).to_have_text("E11")
                    expect(page.locator(".wi-trail")).to_have_text("Chosen B2→D6→E11")
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.locator(".wm-body").evaluate("e=>e.scrollTop=0")
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-source-trail.png"))
                    result_row = page.locator(".wi-row").filter(has=page.get_by_text("Calculated result", exact=True))
                    result_row.scroll_into_view_if_needed()
                    expect(result_row.locator("dd")).to_be_in_viewport(ratio=1)
                    expect(result_row.locator("dd")).to_have_text("2023")  # E12 (2022) + 1.
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-source-result.png"))
                    before_return = book.request_count
                    page.get_by_role("button", name="Return to chosen cell", exact=True).click()
                    expect(page.locator(".wi-evidence dd").first).to_have_text("2025")
                    assert book.request_count == before_return
                    assert all(paired and (path == "/version" or path.startswith((
                        "/api/document-tables?", "/api/inspect-document?", "/api/inspect-source?"))) for path, paired in requests)

                    # Exercise the real content-script timer, not a substituted clock.
                    document_delay.set()
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-inspector")).to_contain_text("Cell read timed out", timeout=35000)
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-cell-timeout.png"))
                    assert not document_sent.is_set()
                    document_released.set()
                    assert document_sent.wait(10)
                    document_delay.clear()
                    page.wait_for_timeout(300)
                    expect(page.locator(".wi-inspector")).to_contain_text("Cell read timed out")
                    expect(page.locator(".wi-evidence")).to_have_count(0)
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-evidence dd").first).to_have_text("2025")
                    document_released.clear()
                    document_sent.clear()

                    # A cancelled reply cannot restore the old cell or cross a section change.
                    document_delay.set()
                    page.locator(".wi-inspect").click()
                    expect(page.locator(".wi-inspect")).to_have_text("Reading cell…")
                    page.locator(".wi-doc-change").click()
                    expect(page.locator(".wi-doc-form")).to_be_visible()
                    document_released.set()
                    assert document_sent.wait(10)
                    document_delay.clear()
                    page.wait_for_timeout(300)
                    expect(page.locator(".wi-address")).to_have_count(0)
                    expect(page.locator(".wi-doc-form")).to_be_visible()
                    document_released.clear()
                    document_sent.clear()
                    document_delay.set()
                    page.get_by_role("combobox", name="Table", exact=True).select_option("demo-report-table")
                    page.get_by_label("Cell address", exact=True).fill("B2")
                    page.get_by_label("Cell address", exact=True).press("Enter")
                    expect(page.locator(".wi-inspect")).to_have_text("Reading cell…")
                    page.evaluate("() => {history.pushState({}, '', location.href.replace('de11','de13')); document.body.append('Section changed');}")
                    expect(page.locator(".wi-doc-heading")).to_have_text("Inspect a report table")
                    document_released.set()
                    assert document_sent.wait(10)
                    document_delay.clear()
                    page.locator(".wi-doc-load").click()
                    expect(page.locator(".wi-inspector")).to_contain_text("No body tables in this section")
                    expect(page.locator(".wi-address")).to_have_count(0)
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-empty.png"))
                    page.evaluate("() => {history.pushState({}, '', location.href.replace('de13','unknown')); document.body.append('Section changed');}")
                    page.locator(".wi-doc-load").click()
                    expect(page.locator(".wi-inspector")).to_contain_text("Could not verify the tables")
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-unavailable.png"))

                    # Metadata timeout must also reject a late successful catalog.
                    page.evaluate("() => {history.pushState({}, '', location.href.replace('unknown','de11')); document.body.append('Section changed');}")
                    document_released.clear()
                    document_sent.clear()
                    document_delay.set()
                    page.locator(".wi-doc-load").click()
                    expect(page.locator(".wi-inspector")).to_contain_text("Table read timed out", timeout=35000)
                    page.locator(".wm-panel").screenshot(path=str(tmp_path / "document-table-timeout.png"))
                    assert not document_sent.is_set()
                    document_released.set()
                    assert document_sent.wait(10)
                    document_delay.clear()
                    page.wait_for_timeout(300)
                    expect(page.locator(".wi-inspector")).to_contain_text("Table read timed out")
                    expect(page.locator(".wi-doc-form")).to_have_count(0)
                    page.locator(".wi-doc-load").click()
                    expect(page.get_by_role("combobox", name="Table", exact=True)).to_be_visible()
                    assert book.events == []
                requests.clear()
                page.set_viewport_size({"width": 1280, "height": 900})
                page.locator(".wc-toggle").click()
                server.shutdown()
                server.server_close()
                page.locator(".wc-check").click()
                expect(title).to_have_text("Service unreachable")
                capture("connection-offline")
                setup.bring_to_front()
                setup.locator(".wc-check").click()
                expect(setup_title).to_have_text("Service unreachable")
                setup.screenshot(path=str(tmp_path / "setup-offline.png"), full_page=True)
                assert all(path in ("/version", "/api/connection") and paired for path, paired in requests)
                assert not upstream_calls
                assert not errors
                print("Screenshots:", tmp_path)
            finally:
                context.close()
    finally:
        released.set()
        document_released.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
