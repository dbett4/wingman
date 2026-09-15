"""Selected-cell evidence: real demo HTTP plus transport-level failure sequences."""
import copy
import json
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

import inspector
from test_demo import browser, demo_url  # shared disposable simulator fixtures


@pytest.mark.parametrize("addr,kind,formula,calculated", [
    ("B7", "formula", "=SUM(B3:B6)+12500", 3746500),
    ("B8", "formula", "=#REF!", "#REF!"),
    ("C8", "number", None, 0),
    ("B1", "blank", None, ""),
    ("B2", "number", None, 2025),
])
def test_http_exact_cell_without_mutation(browser, addr, kind, formula, calculated):
    before = browser.state()
    path = f"/api/inspect?spreadsheetId=de00&sheetId=de01&addr={addr}"
    request = urllib.request.Request(browser.url + path, headers={"X-Wingman-Demo": "1"})
    with browser.opener.open(request, timeout=10) as response:
        assert response.headers["Cache-Control"] == "no-store"
        data = json.load(response)
    assert data["readOnly"] is True
    assert data["target"] == {"spreadsheetId": "de00", "sheetId": "de01", "addr": addr}
    assert data["sheetName"] == "Statement of activities"
    assert data["content"]["kind"] == kind
    assert data["content"]["formula"] == formula
    assert data["calculated"] == {"status": "observed", "value": calculated}
    if kind == "number":
        assert data["content"]["value"] == calculated
        assert data["nativeFormat"]["status"] == "observed"
    assert data["source"] == {"status": "not_traced"}
    assert data["policy"] == {"status": "not_connected"}
    assert data["downstream"] == {"status": "not_inspected"}
    after = browser.state()
    assert after["requestCount"] - before["requestCount"] == 5  # metadata + two exact read pairs
    assert after["sheets"] == before["sheets"]
    assert after["events"] == before["events"] == []
    assert after["failureArmed"] == before["failureArmed"] is False


def test_foreign_sheet_has_no_cell_evidence(browser):
    data = browser.request("/api/inspect?spreadsheetId=de00&sheetId=foreign&addr=B7")
    assert data["status"] == "unavailable"
    assert "content" not in data and "calculated" not in data
    assert browser.state()["requestCount"] == 1
    assert browser.state()["events"] == []


@pytest.fixture
def transport(monkeypatch):
    """Fixed asymmetric C12 target; no live OAuth, scan, write, or network calls."""
    meta = {"data": [{"id": "sheet-b", "name": "Synthetic", "table": {"table": "table-c"}}]}
    native = {"data": {"range": {"startRow": 11, "startColumn": 2}, "cells": [[{
        "calculatedValue": 42,
        "effectiveFormats": {"valueFormat": {"valueFormatType": "NUMBER"}},
    }]]}}
    content = {"data": [{"cells": [{"value": {"type": "formula", "formula": "=6*7"}}]}]}
    responses = {"meta": [meta], "native": [native, copy.deepcopy(native)],
                 "content": [content, copy.deepcopy(content)]}
    calls = []

    def read(path, token, ctx, version=None):
        assert token == "synthetic-token" and ctx is None
        calls.append(path)
        if path.startswith("/spreadsheets/book-a/sheets"):
            key = "meta"
            assert version == "2026-01-01"
        elif path.startswith("/platform/v1/spreadsheets/book-a/sheets/sheet-b/sheetdata?"):
            key = "native"
            assert parse_qs(urlsplit(path).query) == {"$cellrange": ["C12:C12"], "$maxcellsperpage": ["1"]}
        else:
            assert path == "/content/tables/table-c/cells?startRow=11&stopRow=11&startColumn=2&stopColumn=2"
            key = "content"
            assert version == "2026-01-01"
        response = responses[key].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(inspector.wk, "_get", read)
    monkeypatch.setattr(inspector.wk, "_get_url", read)
    return responses, calls


def inspect():
    return inspector.inspect_cell("book-a", "sheet-b", "C12", "synthetic-token", None)


@pytest.mark.parametrize("failed_read", [0, 1])
def test_partial_content_is_not_a_hardcode(transport, failed_read):
    responses, _ = transport
    responses["content"][failed_read] = RuntimeError("sensitive upstream body must not escape")
    data = inspect()
    assert data["status"] == "partial"
    assert data["content"] == {"status": "unavailable"}
    assert data["calculated"]["value"] == 42
    assert "does not prove a hardcode" in " ".join(data["warnings"])
    assert "sensitive" not in json.dumps(data)


@pytest.mark.parametrize("endpoint", ["content", "native"])
def test_change_discards_all_evidence_including_same_result_formula(transport, endpoint):
    responses, _ = transport
    if endpoint == "content":
        # Same result (42), different stored formula: calculated-value-only checks miss this.
        responses[endpoint][1]["data"][0]["cells"][0]["value"]["formula"] = "=40+2"
    else:
        responses[endpoint][1]["data"]["cells"][0][0]["calculatedValue"] = 43
    data = inspect()
    assert data["status"] == "changed"
    assert not {"content", "calculated", "nativeFormat"} & data.keys()


def test_change_from_zero_to_false_is_not_equal_evidence(transport):
    responses, _ = transport
    responses["content"][0]["data"][0]["cells"][0]["value"] = 0
    responses["content"][1]["data"][0]["cells"][0]["value"] = False
    data = inspect()
    assert data["status"] == "changed"
    assert "content" not in data


@pytest.mark.parametrize("fault", ["row", "column", "extra-cell", "pagination", "second-read"])
def test_wrong_or_incomplete_sheetdata_is_not_evidence(transport, fault):
    responses, _ = transport
    raw = responses["native"][0]
    if fault == "row":
        raw["data"]["range"]["startRow"] = 12
    elif fault == "column":
        raw["data"]["range"]["startColumn"] = 1
    elif fault == "extra-cell":
        raw["data"]["cells"][0].append({"calculatedValue": 99})
    elif fault == "pagination":
        raw["@nextLink"] = "/more"
    else:
        responses["native"][1] = TimeoutError()
    data = inspect()
    assert data["status"] == "unavailable"
    assert "calculated" not in data


@pytest.mark.parametrize("value,kind", [(0, "number"), (False, "boolean"), (None, "blank"),
                                        ("=not-a-formula-object", "text"), ({"type": "future"}, "unknown")])
def test_content_type_is_not_inferred_from_calculated_value(transport, value, kind):
    responses, _ = transport
    for response in responses["content"]:
        response["data"][0]["cells"][0]["value"] = value
    data = inspect()
    assert data["content"]["kind"] == kind
    assert data["content"]["value"] == value
    assert data["calculated"]["value"] == 42


def test_malformed_native_format_is_explicitly_unavailable(transport):
    responses, _ = transport
    for response in responses["native"]:
        response["data"]["cells"][0][0]["effectiveFormats"] = ["unexpected"]
    data = inspect()
    assert data["status"] == "partial"
    assert data["nativeFormat"] == {"status": "unavailable"}
    assert data["content"]["kind"] == "formula"


@pytest.mark.parametrize("next_link", ["https://foreign.invalid/sheets", "//foreign.invalid/sheets",
                                     "http://api.app.wdesk.com/sheets"])
def test_pagination_never_forwards_credentials_to_untrusted_url(transport, monkeypatch, next_link):
    responses, calls = transport
    monkeypatch.setattr(inspector.wk, "_base", lambda: "https://api.app.wdesk.com")
    responses["meta"] = [{"data": [], "@nextLink": next_link}]
    assert inspect()["status"] == "unavailable"
    assert len(calls) == 1


def test_metadata_pagination_resolves_requested_sheet(transport):
    responses, calls = transport
    responses["meta"].insert(0, {"data": [{"id": "other-sheet"}],
                                 "@nextLink": "/spreadsheets/book-a/sheets?page=2"})
    assert inspect()["content"]["formula"] == "=6*7"
    assert len(calls) == 6
