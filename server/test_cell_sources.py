"""Source text and metadata must not become claims of verified dependencies."""
import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

import cell_sources as sources


@pytest.mark.parametrize("formula,refs,numbers,unresolved", [
    ("=SUM(B3:B6)+12500", ["B3:B6"], ["12500"], []),
    ("=LOG10(A1)+1E10+IF(B2=\"C3\",D4,0)", ["A1", "B2", "D4"], ["1E10", "0"], []),
    ("='Director''s Notes'!$C$12+Sheet2!B7:$D9+$a12", ["'Director''s Notes'!$C$12", "Sheet2!B7:$D9", "$a12"], [], []),
    ("=SUM(B:D,12:14)+B3+B3", ["B:D", "12:14", "B3"], [], []),
    ('=IF(A1="say ""B2""",-1.25,TRUE)', ["A1"], ["1.25"], []),
    ("=SUM(Table1[[#All],[Cost]],B2)+TaxRate+A1_name", ["B2"], [], ["Table1[[#All],[Cost]]", "TaxRate", "A1_name"]),
    ("='[Book]Sheet'!A1+[Book]Sheet!B2+C3", ["C3"], [], ["'[Book]Sheet'!A1", "[Book]Sheet!B2"]),
    ("=Sheet1:Sheet3!B2+'Sheet 1:Sheet 3'!C7+D4", ["D4"], [], ["Sheet1:Sheet3!B2", "'Sheet 1:Sheet 3'!C7"]),
    ("=#REF!+A1#", [], [], ["#REF!", "A1#"]),
    ('=INDIRECT("B7")+OFFSET(C2,1,0)', ["C2"], ["1", "0"],
     ["INDIRECT(…) — computed reference not resolved", "OFFSET(…) — computed reference not resolved"]),
])
def test_reference_text_not_strings_functions_or_names(formula, refs, numbers, unresolved):
    assert sources.formula_references({"status": "observed", "formula": formula}) == {
        "status": "text_only", "references": refs, "literalNumbers": numbers, "unresolved": unresolved,
    }


def test_no_formula_and_failed_content_are_different():
    assert sources.formula_references({"status": "observed", "value": 0}) == {"status": "not_formula"}
    assert sources.formula_references({"status": "unavailable"}) == {"status": "unavailable"}


def source_link():
    return {"id": "source-id", "table": "table-c", "type": "source", "revision": "published-7",
            "source": {"range": {"startRow": 10, "stopRow": 11, "startColumn": 2, "stopColumn": 4}}}


def destination_link():
    return {"id": "destination-id", "table": "table-c", "type": "destination", "revision": "local-9",
            "destination": {"source": {"table": "upstream-table", "rangeLink": "upstream-link", "revision": "published-3"}}}


@pytest.fixture
def transport(monkeypatch):
    responses, calls = [], []

    def read(path, token, ctx, version=None):
        assert token == "synthetic-token" and ctx is None and version == "2026-01-01"
        calls.append(path)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)

    monkeypatch.setattr(sources.wk, "_base", lambda: "https://api.app.wdesk.com")
    monkeypatch.setattr(sources.wk, "_get_url", read)
    monkeypatch.setattr(sources.wk, "_get", read)
    return responses, calls


def read(addr="C12"):
    return sources.range_links("table-c", addr, "synthetic-token", None)


@pytest.mark.parametrize("addr,covered", [("C11", True), ("E12", True), ("D11", True),
                                        ("B11", False), ("C10", False), ("E13", False), ("F12", False)])
def test_source_range_inclusive_and_not_transposed(transport, addr, covered):
    responses, calls = transport
    responses.append({"data": [source_link()]})
    data = read(addr)
    assert data["status"] == "observed"
    assert data["items"] == ([{"direction": "source", "id": "source-id", "range": "C11:E12",
                               "revision": "published-7"}] if covered else [])
    assert calls == ["/content/tables/table-c/rangeLinks"]


def test_later_page_is_not_skipped(transport):
    responses, calls = transport
    next_link = "https://api.app.wdesk.com/content/tables/table-c/rangeLinks?$next=opaque"
    responses.extend([{"data": [], "@nextLink": next_link}, {"data": [source_link()]}])
    assert read()["items"][0]["range"] == "C11:E12"
    assert calls[-1] == next_link


@pytest.mark.parametrize("first", [[], [source_link()]])
def test_failed_second_page_never_proves_absence_or_completeness(transport, first):
    responses, _ = transport
    responses.extend([{"data": first, "@nextLink": "/page-two"}, RuntimeError("sensitive upstream body")])
    data = read()
    assert data["status"] == ("partial" if first else "unavailable")
    assert len(data["items"]) == len(first)
    assert "sensitive" not in json.dumps(data)


@pytest.mark.parametrize("next_link", ["https://foreign.invalid/links", "//foreign.invalid/links",
                                     "http://api.app.wdesk.com/links", "/content/tables/table-c/rangeLinks", False])
def test_bad_pagination_does_not_forward_credentials(transport, next_link):
    responses, calls = transport
    responses.append({"data": [], "@nextLink": next_link})
    assert read()["status"] == "unavailable"
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["missing-data", "wrong-table", "unknown-type", "bool-bound", "backwards", "duplicate"])
def test_malformed_or_ambiguous_metadata_is_not_complete(transport, fault):
    responses, _ = transport
    link = source_link()
    page = {"data": [link]}
    if fault == "missing-data":
        page = {}
    elif fault == "wrong-table":
        link["table"] = "wrong-table"
    elif fault == "unknown-type":
        link["type"] = "future-direction"
    elif fault == "bool-bound":
        link["source"]["range"]["startColumn"] = False
    elif fault == "backwards":
        link["source"]["range"]["stopRow"] = 9
    else:
        page["data"].append(copy.deepcopy(link))
    responses.append(page)
    assert read()["status"] in ("unavailable", "partial")


@pytest.mark.parametrize("fault", [None, "denied", "table", "id", "revision", "type"])
def test_destination_occupies_table_and_resolves_only_reported_revision(transport, fault):
    responses, calls = transport
    upstream = source_link()
    upstream.update(table="upstream-table", id="upstream-link", revision="published-3")
    if fault == "denied":
        upstream = PermissionError("sensitive body")
    elif fault:
        upstream[fault] = "wrong-value"
    responses.extend([{"data": [destination_link()]}, upstream])
    # Destination has NO range. Never fall back to its local revision or its own ID.
    data = read("H27")
    assert data["status"] == "observed"
    item = data["items"][0]
    assert item["source"] == {"table": "upstream-table", "rangeLink": "upstream-link", "revision": "published-3"}
    assert item["resolution"] == ("unavailable" if fault else "observed")
    assert item.get("sourceRange") == (None if fault else "C11:E12")
    assert len(calls) == 2  # Never retry at latest revision.
    assert urlsplit(calls[1]).path == "/content/tables/upstream-table/rangeLinks/upstream-link"
    assert parse_qs(urlsplit(calls[1]).query) == {"$revision": ["published-3"]}
    assert "sensitive" not in json.dumps(data)
