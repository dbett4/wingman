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
    ("C8", "raw_value", None, 0),
    ("B1", "blank", None, ""),
    ("B2", "raw_value", None, 2025),
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
    if kind == "raw_value":
        assert data["content"]["value"] == str(calculated)
        assert data["nativeFormat"]["status"] == "observed"
    formula_source = data["source"]["formula"]
    if addr == "B7":
        assert formula_source == {"status": "text_only", "references": ["B3:B6"],
                                  "literalNumbers": ["12500"], "unresolved": []}
        assert data["source"]["rangeLinks"]["items"][0]["range"] == "B3:C7"
    else:
        assert data["source"]["rangeLinks"] == {"status": "observed", "items": []}
        assert formula_source == ({"status": "text_only", "references": [], "literalNumbers": [], "unresolved": ["#REF!"]}
                                  if addr == "B8" else {"status": "not_formula"})
    assert data["policy"] == {"status": "not_connected"}
    assert data["downstream"] == {"status": "not_inspected"}
    assert data["sourceValuesRequested"] is False and "values" not in data["source"]
    after = browser.state()
    assert after["requestCount"] - before["requestCount"] == 6  # metadata + links + two exact read pairs
    assert after["sheets"] == before["sheets"]
    assert after["events"] == before["events"] == []
    assert after["failureArmed"] == before["failureArmed"] is False


def test_foreign_sheet_has_no_cell_evidence(browser):
    data = browser.request("/api/inspect?spreadsheetId=de00&sheetId=foreign&addr=B7")
    assert data["status"] == "unavailable"
    assert "content" not in data and "calculated" not in data
    assert browser.state()["requestCount"] == 1
    assert browser.state()["events"] == []


def test_http_linked_constant_is_not_an_unlinked_hardcode(browser):
    before = browser.state()
    data = browser.request("/api/inspect?spreadsheetId=de00&sheetId=de02&addr=B2")
    assert data["content"]["value"] == "2025"
    assert data["source"]["formula"] == {"status": "not_formula"}
    links = data["source"]["rangeLinks"]
    assert links["status"] == "observed"
    assert links["items"] == [{
        "direction": "destination", "id": "demo-notes-destination", "resolution": "observed", "sourceRange": "C5:D8",
        "source": {"table": "demo-notes-table", "rangeLink": "demo-notes-source", "revision": "demo-published-3"},
    }]
    assert data["content"]["kind"] == "linked_value"
    assert data["source"]["cellLink"] == {
        "status": "observed", "resolution": "observed", "linkState": "connected",
        "id": "demo-year-cell-link", "revision": "demo-link-2", "sourceCell": "D6",
        "source": {"type": "table", "anchor": "demo-year-anchor", "table": "demo-notes-table", "revision": "demo-published-3"},
    }
    after = browser.state()
    assert after["requestCount"] - before["requestCount"] == 9
    assert after["sheets"] == before["sheets"] and after["events"] == []


@pytest.fixture
def transport(monkeypatch):
    """Fixed asymmetric C12 target; no live OAuth, scan, write, or network calls."""
    meta = {"data": [{"id": "sheet-b", "name": "Synthetic", "table": {"table": "table-c"}}]}
    native = {"data": {"range": {"startRow": 11, "startColumn": 2}, "cells": [[{
        "calculatedValue": 42,
        "effectiveFormats": {"valueFormat": {"valueFormatType": "NUMBER"}},
    }]]}}
    content = {"revision": "revision-9",
               "range": {"startRow": 11, "stopRow": 11, "startColumn": 2, "stopColumn": 2},
               "data": [{"cells": [{"value": {"type": "formula", "formula": "=6*7"}}]}]}
    responses = {"meta": [meta], "native": [native, copy.deepcopy(native)],
                 "content": [content, copy.deepcopy(content)], "links": [{"data": []}]}
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
        elif path == "/content/tables/table-c/rangeLinks":
            key = "links"
            assert version == "2026-01-01"
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
    assert data["source"] == {"formula": {"status": "not_inspected"}, "rangeLinks": {"status": "not_inspected"}}


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
    assert len(calls) == 7


def test_link_failure_preserves_cell_evidence_without_claiming_absence(transport):
    responses, _ = transport
    responses["links"] = [PermissionError("sensitive upstream body")]
    data = inspect()
    assert data["status"] == "observed"  # Cell evidence is independently available.
    assert data["content"]["formula"] == "=6*7"
    assert data["source"]["rangeLinks"] == {"status": "unavailable", "items": []}
    assert "sensitive" not in json.dumps(data)


@pytest.mark.parametrize("sheet,addr,revision,addresses,values", [
    ("de01", "B7", "demo-current-0", ["B3", "B4", "B5", "B6"], ["2450000", "875000", "315000", "94000"]),
    ("de02", "B2", "demo-published-3", ["C5", "D5", "C6", "D6", "C7", "D7", "C8", "D8"],
     ["REVIEW NOTES", "", "Fiscal year", "2024", "Reporting basis", "Accrual", "Scope", "Fictional demonstration only"]),
])
def test_opt_in_http_sources_at_reported_revision_without_writes(browser, sheet, addr, revision, addresses, values):
    before = browser.state()
    data = browser.request(f"/api/inspect?spreadsheetId=de00&sheetId={sheet}&addr={addr}&sources=true")
    assert data["sourceValuesRequested"] is True
    groups = data["source"]["values"]["groups"]
    group = groups[-1]
    if sheet == "de02":
        assert len(groups) == 2
        direct = groups[0]
        assert direct["basis"] == "cell_link_revision" and direct["revision"] == "demo-published-3"
        assert [(c["addr"], c["content"]["value"]) for c in direct["cells"]] == [("D6", "2024")]
    else:
        assert len(groups) == 1
    assert group["revision"] == revision and group["status"] == "observed"
    assert [cell["addr"] for cell in group["cells"]] == addresses
    assert [cell["content"]["value"] for cell in group["cells"]] == values
    after = browser.state()
    assert after["sheets"] == before["sheets"] and after["events"] == []
    assert after["requestCount"] == (8 if sheet == "de01" else 13)


@pytest.mark.parametrize("failure", ["changed", "unavailable"])
def test_origin_change_or_failed_recheck_discards_source_values(transport, monkeypatch, failure):
    responses, _ = transport
    returned = []

    def read_sources(*args, **kwargs):
        returned.append(args)
        return {"status": "observed", "groups": [{"status": "observed", "cells": [{"value": "must be discarded"}]}]}

    monkeypatch.setattr(inspector, "source_values", read_sources)
    if failure == "changed":
        responses["content"][1]["revision"] = "revision-10"
    else:
        responses["content"][1] = TimeoutError()
    data = inspector.inspect_cell("book-a", "sheet-b", "C12", "synthetic-token", None, include_sources=True)
    assert len(returned) == 1 and returned[0][2] == "revision-9"
    assert "values" not in data["source"]
    assert data["status"] == ("changed" if failure == "changed" else "partial")


def test_http_disconnected_cell_link_retains_nonblank_content(browser):
    before = browser.state()
    data = browser.request("/api/inspect?spreadsheetId=de00&sheetId=de02&addr=B3")
    assert data["content"]["value"] == data["calculated"]["value"] == "Accrual"
    link = data["source"]["cellLink"]
    assert link["status"] == "observed" and link["linkState"] == link["resolution"] == "disconnected"
    assert "sourceCell" not in link and "source" not in link
    assert data["source"]["rangeLinks"]["status"] == "observed"  # A covering range does not prove the cell is connected.
    assert browser.state()["requestCount"] == 8
    assert browser.state()["sheets"] == before["sheets"] and browser.state()["events"] == []


def test_changed_link_identity_discards_trace_even_when_content_is_unchanged(transport, monkeypatch):
    responses, _ = transport
    for i, response in enumerate(responses["content"]):
        response["data"][0]["cells"][0] = {"rawValue": "42", "value": {"type": "destinationLink",
            "destinationLink": {"destinationLink": {"destinationLink": "before" if i == 0 else "after", "revision": "link-7"}}}}
    monkeypatch.setattr(inspector, "cell_link", lambda *args: {"status": "observed", "sourceCell": "E11"})
    data = inspect()
    assert data["status"] == "changed" and "content" not in data
    assert "cellLink" not in data["source"]
