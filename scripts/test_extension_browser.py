"""Installed MV3 connection test: real worker + HTTP handler, fictional page/config only.

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
            super().do_GET()

    # A busy port fails without inspecting, stopping or replacing its owner.
    server = ThreadingHTTPServer(("127.0.0.1", 8770), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        extension = tmp_path / "extension"
        extension.mkdir()
        manifest = json.loads((ROOT / "extension/manifest.json").read_text())
        assets = {"manifest.json", manifest["background"]["service_worker"], "vision-capture.js"}
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
                page_url = "https://app.wdesk.com/a/fictional-workspace/spreadsheet/abc123/sheet/def456"

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
                page = context.pages[0]
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(page_url)
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
                server.shutdown()
                server.server_close()
                page.locator(".wc-check").click()
                expect(title).to_have_text("Service unreachable")
                capture("connection-offline")
                assert all(path in ("/version", "/api/connection") and paired for path, paired in requests)
                assert not upstream_calls
                assert not errors
                print("Screenshots:", tmp_path)
            finally:
                context.close()
    finally:
        released.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
