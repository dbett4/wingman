"""HTTP integration tests: real service, detectors, fixer, and simulated upstream.

No mocked detectors or fixer calls. A separate process keeps demo-only globals,
synthetic identities, and the outbound-network restriction out of the live tests.
"""
import copy
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo_url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WORKIVA_", "WINGMAN_"))}
    # Inherited credentials/region must not turn the demo into a live integration.
    env.update(WORKIVA_CLIENT_ID="not-real", WORKIVA_CLIENT_SECRET="not-real", WORKIVA_REGION="eu")
    with (tmp / "server.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "server/demo.py"), "--port", "0"],
                                   cwd=tmp, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                log.seek(0)
                output = log.read()
                match = re.search(r"ready on port (\d+)", output)
                if match:
                    yield "http://127.0.0.1:" + match[1]
                    return
                if process.poll() is not None:
                    pytest.fail("Demo exited during startup: " + output)
                time.sleep(.05)
            pytest.fail("Demo did not start within 15 seconds")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


class Browser:
    def __init__(self, url):
        self.url = url
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        with self.opener.open(url + "/", timeout=5) as response:
            assert "THE ACTUAL EXTENSION PANEL" in response.read().decode()

    def request(self, path, body=None, *, headers=None, method=None):
        request = urllib.request.Request(
            self.url + path, data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={"X-Wingman-Demo": "1", "Content-Type": "application/json", **(headers or {})},
        )
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def state(self):
        return self.request("/demo/state")


@pytest.fixture
def browser(demo_url):
    return Browser(demo_url)


def body(kind, addr, target=None):
    result = {"spreadsheetId": "de00", "sheetId": "de01", "kind": kind, "addr": addr}
    if target:
        result["target"] = target
    return result


FIXES = [
    body("label-hygiene", "A4"),
    body("year-automatic-coercion", "B2", {"valueFormat": {"valueFormatType": "PERIOD"}}),
    body("low-contrast", "A5"),
]


def test_real_scan_pages_and_does_not_propose_competing_year_fixes(browser):
    before = browser.state()
    data = browser.request("/api/queue?spreadsheetId=de00&sheetId=de01")
    actual = {(item["kind"], tuple(item["addrs"]), item["fix_lane"]) for item in data["items"]}
    assert actual == {
        ("broken-ref", ("B8",), "surfaced"),
        ("hardcoded-constant-in-formula", ("B7",), "surfaced"),
        ("dl-source-numeric-formula", ("B7",), "surfaced"),
        ("low-contrast", ("A5",), "safe-auto"),
        ("year-automatic-coercion", ("B2",), "safe-auto"),
        ("label-hygiene", ("A4",), "safe-auto"),
        ("zero-display-mismatch", ("C8",), "surfaced"),
    }
    assert data["cellCount"] == 22  # Includes findings on page 2, not just the first five rows.
    assert data["truncated"] is False
    assert browser.state()["sheets"] == before["sheets"]
    assert browser.state()["events"] == []
    clean = browser.request("/api/queue?spreadsheetId=de00&sheetId=de02")
    assert clean["items"] == []
    assert clean["cellCount"] == 7


@pytest.mark.parametrize("payload", FIXES, ids=["label", "year", "contrast"])
def test_preview_apply_readback_and_unchanged_neighbors(browser, payload):
    before = browser.state()["sheets"]
    plan = browser.request("/fix", payload)
    assert plan["status"] == "dry-run"
    assert browser.state()["sheets"] == before
    assert browser.state()["events"] == []
    result = browser.request("/apply", payload)
    assert result["status"] == "applied"
    after = browser.state()["sheets"]
    expected = copy.deepcopy(before)
    if payload["kind"] == "label-hygiene":
        expected[0]["cells"][3][0]["value"] = "Intergovernmental revenue"
        expected[0]["cells"][3][0]["calculatedValue"] = "Intergovernmental revenue"
    elif payload["kind"] == "year-automatic-coercion":
        expected[0]["cells"][1][1]["effectiveFormats"]["valueFormat"] = {"valueFormatType": "PERIOD"}
        assert after[0]["cells"][1][1]["value"] == 2025  # Formatting never rescales the stored year.
    else:
        # #767676 is the nearest AA-passing neutral gray on white (4.54:1);
        # #777777 is below 4.5. Expected independently of compute_aa_fontcolor.
        expected[0]["cells"][4][0]["effectiveFormats"]["textFormat"]["fontColor"] = "#767676"
    assert after == expected  # Every neighboring value, formula and format remains unchanged.
    assert len(browser.state()["events"]) == 1
    assert browser.request("/apply", payload)["status"] == "no-fix-needed"
    assert len(browser.state()["events"]) == 1


@pytest.mark.parametrize("payload", FIXES, ids=["label", "year", "contrast"])
def test_injected_mismatch_restores_exact_original_state(browser, payload):
    before = browser.state()["sheets"]
    browser.request("/demo/failure", {})
    assert browser.request("/fix", payload)["status"] == "dry-run"
    assert browser.state()["failureArmed"] is True  # Preview must not consume the injected failure.
    result = browser.request("/apply", payload)
    assert result["status"] == "mismatch-reverted"
    state = browser.state()
    assert state["sheets"] == before
    assert state["failureArmed"] is False
    assert len(state["events"]) == 2  # Wrong write followed by the actual fixer's restore.
    first, restore = state["events"]
    assert first["injectedMismatch"] is True
    assert restore["injectedMismatch"] is False
    assert restore["after"] == first["before"]


def test_sessions_and_reset_are_isolated(browser, demo_url):
    other = Browser(demo_url)
    baseline = other.state()["sheets"]
    browser.request("/apply", FIXES[0])
    other.request("/apply", FIXES[1])
    assert other.state()["sheets"][0]["cells"][3][0]["value"] == "Intergovernmental  revenue "
    assert browser.state()["sheets"][0]["cells"][1][1]["effectiveFormats"]["valueFormat"]["valueFormatType"] == "AUTOMATIC"
    other_before = other.state()["sheets"]
    browser.request("/demo/reset", {})
    assert browser.state()["sheets"] == baseline
    assert browser.state()["events"] == []
    assert other.state()["sheets"] == other_before


def test_packet_is_fresh_redacted_and_never_client_ready(browser):
    packet = browser.request("/api/review-packet?spreadsheetId=de00")
    assert packet["packet"]["clientReady"] is False
    assert packet["packet"]["redacted"] is True
    assert packet["packet"]["generatedAt"]
    assert packet["packet"]["sheetCount"] == 2
    assert "B8" in packet["markdown"]
    assert "12500" not in packet["markdown"]
    assert browser.state()["events"] == []


@pytest.mark.parametrize("path", ["/api/checks?spreadsheetId=de00", "/api/queue?spreadsheetId=de00&checks=tieout",
                                 "/api/review-packet?spreadsheetId=de00&write=true", "/../.env"])
def test_live_only_features_and_arbitrary_files_are_unavailable(browser, path):
    with pytest.raises(urllib.error.HTTPError) as caught:
        browser.request(path)
    assert caught.value.code in (400, 404)


def test_auth_and_cross_origin_boundary(browser, demo_url):
    with pytest.raises(urllib.error.HTTPError) as caught:
        browser.request("/apply", FIXES[0], headers={"X-Wingman-Demo": ""})
    assert caught.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as caught:
        browser.request("/apply", method="OPTIONS", headers={"Origin": "https://foreign.example"})
    assert caught.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(urllib.request.Request(demo_url + "/demo/state", headers={"X-Wingman-Demo": "1"}), timeout=5)
    assert caught.value.code == 403  # Header without session cookie is not sufficient.
    assert browser.state()["events"] == []


def test_unknown_workbook_write_is_refused(browser):
    payload = {**FIXES[0], "spreadsheetId": "not-demo"}
    with pytest.raises(urllib.error.HTTPError):
        browser.request("/apply", payload)
    assert browser.state()["events"] == []
