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


def content_range():
    return {"revision": "revision-9",
            "range": {"startRow": 10, "stopRow": 11, "startColumn": 2, "stopColumn": 4},
            "data": [{"cells": [
                {"rawValue": "0", "value": {"type": "plainText", "plainText": {"effectiveValue": "—"}}},
                {"rawValue": "", "value": {"type": "plainText"}},
                {"rawValue": "=SUM(C1:C10)", "value": {"type": "formula", "formula": {
                    "calculatedValue": "-12340", "effectiveValue": "(12,340)"}}},
            ]}, {"cells": [{"value": False}, {"rawValue": "2025", "value": {"type": "destinationLink"}},
                            {"rawValue": "=literal", "value": {"type": "plainText"}}]}]}


def properties(table="table-c", revision="revision-9"):
    return {"id": table, "revision": revision, "name": "Synthetic source"}


def read_values(formula="=C11:E12", links=None):
    refs = sources.formula_references({"status": "observed", "formula": formula})
    return sources.source_values("book-a", "table-c", "revision-9", refs,
                                 links or {"status": "observed", "items": []}, "synthetic-token", None)


def test_raw_content_computed_result_and_link_type_remain_distinct(transport):
    responses, calls = transport
    responses.extend([properties(), content_range()])
    data = read_values()
    group, = data["groups"]
    assert data["status"] == group["status"] == "observed"
    cells = group["cells"]
    assert [cell["addr"] for cell in cells] == ["C11", "D11", "E11", "C12", "D12", "E12"]
    assert [cell["content"]["kind"] for cell in cells] == ["raw_value", "blank", "formula", "boolean", "linked_value", "raw_value"]
    assert [cell["content"]["value"] for cell in cells] == ["0", "", "=SUM(C1:C10)", False, "2025", "=literal"]
    assert cells[2]["calculated"] == {"status": "observed", "value": "-12340"}
    assert cells[0]["calculated"] == {"status": "unavailable"}
    assert parse_qs(urlsplit(calls[1]).query) == {
        "$revision": ["revision-9"], "startRow": ["10"], "stopRow": ["11"], "startColumn": ["2"], "stopColumn": ["4"],
    }
    assert len(calls) == 2  # No recursive read of C1:C10.


@pytest.mark.parametrize("fault", ["revision", "row", "column", "shape", "next-page", "missing-cell", "denied", "properties"])
def test_mismatched_or_incomplete_source_never_uses_latest(transport, fault):
    responses, calls = transport
    prop, raw = properties(), content_range()
    if fault == "revision": raw["revision"] = "revision-10"
    elif fault == "row": raw["range"]["startRow"] = 11
    elif fault == "column": raw["range"]["startColumn"] = 1
    elif fault == "shape": raw["data"][1]["cells"].pop()
    elif fault == "next-page": raw["@nextLink"] = "https://foreign.invalid/data"
    elif fault == "missing-cell": raw["data"][0]["cells"][0] = {}
    elif fault == "denied": raw = PermissionError("sensitive upstream body")
    else: prop["id"] = "wrong-table"
    responses.extend([prop, raw])
    data = read_values()
    assert data["status"] == "partial" and data["groups"][0]["status"] == "unavailable"
    assert "cells" not in data["groups"][0] and "sensitive" not in json.dumps(data)
    assert len(calls) == (1 if fault == "properties" else 2)
    assert all(parse_qs(urlsplit(path).query)["$revision"] == ["revision-9"] for path in calls)


def test_published_link_is_read_at_its_own_revision_not_origin(transport):
    responses, calls = transport
    raw = content_range()
    raw["revision"] = "published-3"
    responses.extend([properties("upstream-table", "published-3"), raw])
    data = read_values(None, {"status": "observed", "items": [{
        "direction": "destination", "sourceRange": "C11:E12", "source": {
            "table": "upstream-table", "revision": "published-3"}}]})
    assert data["groups"][0]["basis"] == "published_revision"
    assert data["groups"][0]["revision"] == "published-3"
    assert all("/upstream-table/" in path and parse_qs(urlsplit(path).query)["$revision"] == ["published-3"] for path in calls)


@pytest.mark.parametrize("fault", [None, "ambiguous", "revision", "foreign-page"])
def test_quoted_sheet_name_resolves_at_pinned_revision_or_stays_unavailable(transport, fault):
    responses, calls = transport
    sheet = {"name": "Director's Notes", "table": {"table": "table-c", "revision": "revision-9"}}
    page = {"data": [sheet]}
    if fault == "ambiguous": page["data"].append(copy.deepcopy(sheet))
    elif fault == "revision": sheet["table"]["revision"] = "revision-10"
    elif fault == "foreign-page": page["@nextLink"] = "https://foreign.invalid/sheets"
    responses.extend([page, properties(), content_range()])
    data = read_values("='Director''s Notes'!$C$11:$E$12")
    assert data["groups"][0]["status"] == ("unavailable" if fault else "observed")
    assert len(calls) == (1 if fault else 3)
    assert parse_qs(urlsplit(calls[0]).query) == {"$revision": ["revision-9"]}


@pytest.mark.parametrize("count", [100, 101])
def test_cell_budget_does_not_sample_large_ranges(transport, count):
    responses, calls = transport
    responses.extend([properties(), {"revision": "revision-9",
        "range": {"startRow": 0, "stopRow": 99, "startColumn": 0, "stopColumn": 0},
        "data": [{"cells": [{"value": i}]} for i in range(100)]}])
    data = read_values(f"=A1:A{count}")
    assert data["groups"][0]["status"] == ("observed" if count == 100 else "limited")
    assert len(calls) == (2 if count == 100 else 0)
    if count == 100:
        assert len(data["groups"][0]["cells"]) == 100
        assert data["groups"][0]["cells"][-1]["addr"] == "A100"


def test_total_budget_and_unbounded_ranges_are_explicit(transport):
    responses, calls = transport
    responses.extend([properties(), {"revision": "revision-9",
        "range": {"startRow": 0, "stopRow": 59, "startColumn": 0, "stopColumn": 0},
        "data": [{"cells": [{"value": i}]} for i in range(60)]}])
    data = read_values("=SUM(A1:A60,B1:B41,C:C)+TaxRate")
    assert [group["status"] for group in data["groups"]] == ["observed", "limited", "unavailable"]
    assert data["status"] == "partial" and len(calls) == 2


def test_range_budget_applies_to_failed_attempts_too(transport):
    responses, calls = transport
    responses.extend([PermissionError()] * 10)
    data = read_values("=" + "+".join(f"A{i}" for i in range(1, 12)))
    assert len(calls) == 10
    assert data["groups"][-1]["status"] == "limited"


def test_missing_formula_text_is_unknown_not_a_nonformula():
    content = sources.stored_content({"value": {"type": "formula", "formula": {"calculatedValue": "42"}}})
    assert content["kind"] == "unknown"
    assert sources.formula_references(content) == {"status": "unavailable"}


def linked_content():
    return {"revision": "origin-9", "data": [{"cells": [{"rawValue": "1234", "value": {
        "type": "destinationLink", "destinationLink": {"destinationLink": {
            "destinationLink": "cell-link", "revision": "link-4"}, "paragraphs": []}}}]}]}


def cell_destination():
    return {"id": "cell-link", "revision": "link-4", "status": "connected",
            "content": {"type": "table", "table": "table-c"}, "source": {"type": "anchor", "anchor": {
                "anchor": "source-anchor", "revision": "source-3", "content": {"type": "table", "table": "upstream-table"}}}}


def cell_anchor():
    return {"id": "source-anchor", "revision": "source-3", "content": {"type": "table", "table": "upstream-table"},
            "attachmentPoint": {"type": "tableRange", "range": {
                "startRow": 10, "stopRow": 10, "startColumn": 4, "stopColumn": 4}}}


def trace_cell():
    return sources.cell_link(linked_content(), "table-c", "synthetic-token", None)


def test_cell_link_follows_ids_and_each_recorded_revision_not_range_offsets(transport):
    responses, calls = transport
    responses.extend([cell_destination(), cell_anchor()])
    link = trace_cell()
    assert link == {"status": "observed", "resolution": "observed", "linkState": "connected",
                    "id": "cell-link", "revision": "link-4", "sourceCell": "E11",
                    "source": {"type": "table", "table": "upstream-table", "anchor": "source-anchor", "revision": "source-3"}}
    assert calls == ["/content/destinationLinks/cell-link?%24revision=link-4",
                     "/content/tables/upstream-table/anchors/source-anchor?%24revision=source-3"]
    # No covering range link is needed to follow this individual cell's source.
    responses.extend([properties("upstream-table", "source-3"), {"revision": "source-3",
        "range": {"startRow": 10, "stopRow": 10, "startColumn": 4, "stopColumn": 4},
        "data": [{"cells": [{"rawValue": "1200", "value": {"type": "plainText"}}]}]}])
    values = sources.source_values("book-a", "table-c", "origin-9", {"status": "not_formula"},
                                   {"status": "observed", "items": []}, "synthetic-token", None, linked_cell=link)
    group, = values["groups"]
    assert group["status"] == "observed" and group["basis"] == "cell_link_revision"
    assert group["cells"][0]["addr"] == "E11" and group["cells"][0]["content"]["value"] == "1200"
    assert parse_qs(urlsplit(calls[-1]).query) == {"$revision": ["source-3"],
        "startRow": ["10"], "stopRow": ["10"], "startColumn": ["4"], "stopColumn": ["4"]}


@pytest.mark.parametrize("range_count", [99, 100])
def test_cell_link_and_covering_range_share_one_cell_budget(transport, range_count):
    responses, calls = transport
    responses.extend([properties("upstream-table", "source-3"), {"revision": "source-3",
        "range": {"startRow": 10, "stopRow": 10, "startColumn": 4, "stopColumn": 4},
        "data": [{"cells": [{"value": 1200}]}]},
        properties("upstream-table", "published-5"), {"revision": "published-5",
        "range": {"startRow": 0, "stopRow": range_count - 1, "startColumn": 0, "stopColumn": 0},
        "data": [{"cells": [{"value": i}]} for i in range(range_count)]}])
    linked = {"status": "observed", "resolution": "observed", "sourceCell": "E11",
              "source": {"table": "upstream-table", "revision": "source-3"}}
    links = {"status": "observed", "items": [{"direction": "destination", "sourceRange": f"A1:A{range_count}",
             "source": {"table": "upstream-table", "revision": "published-5"}}]}
    data = sources.source_values("book-a", "table-c", "origin-9", {"status": "not_formula"},
                                 links, "synthetic-token", None, linked_cell=linked)
    direct, covering = data["groups"]
    assert direct["status"] == "observed" and direct["cells"][0]["content"]["value"] == 1200
    assert covering["status"] == ("observed" if range_count == 99 else "limited")
    assert sum(len(g.get("cells", [])) for g in data["groups"]) == (100 if range_count == 99 else 1)
    assert len(calls) == (4 if range_count == 99 else 2)


@pytest.mark.parametrize("fault", ["id", "revision", "table", "status", "denied"])
def test_cell_destination_mismatch_or_denial_is_not_disconnection(transport, fault):
    responses, calls = transport
    dest = cell_destination()
    if fault == "table": dest["content"]["table"] = "foreign-table"
    elif fault == "denied": dest = PermissionError("sensitive upstream detail")
    else: dest[fault] = "unexpected"
    responses.append(dest)
    data = trace_cell()
    assert data["status"] == "unavailable" and "linkState" not in data and "sourceCell" not in data
    assert data["revision"] == "link-4" and len(calls) == 1
    assert "sensitive" not in json.dumps(data)


@pytest.mark.parametrize("fault", ["id", "revision", "table", "type", "range", "denied"])
def test_cell_anchor_failure_preserves_connected_status_not_a_false_source(transport, fault):
    responses, calls = transport
    anchor = cell_anchor()
    if fault == "table": anchor["content"]["table"] = "foreign-table"
    elif fault == "type": anchor["attachmentPoint"]["type"] = "richTextSelection"
    elif fault == "range": anchor["attachmentPoint"]["range"]["stopColumn"] = 5
    elif fault == "denied": anchor = PermissionError("sensitive upstream detail")
    else: anchor[fault] = "unexpected"
    responses.extend([cell_destination(), anchor])
    data = trace_cell()
    assert data["status"] == "observed" and data["linkState"] == "connected"
    assert data["resolution"] == "unavailable" and "sourceCell" not in data
    assert len(calls) == 2 and "sensitive" not in json.dumps(data)


@pytest.mark.parametrize("state", ["disconnected", "richText", "unidentified"])
def test_disconnected_and_unsupported_links_do_not_trigger_source_reads(transport, state):
    responses, calls = transport
    dest = cell_destination()
    if state == "disconnected": dest["status"] = "disconnected"
    elif state == "richText": dest["source"]["anchor"]["content"] = {"type": "richText", "richText": "text-id"}
    else: dest["source"] = {"type": "unidentified", "unidentified": {}}
    responses.append(dest)
    link = trace_cell()
    assert link["resolution"] == ("disconnected" if state == "disconnected" else "unsupported")
    values = sources.source_values("book-a", "table-c", "origin-9", {"status": "not_formula"},
                                   {"status": "observed", "items": []}, "synthetic-token", None, linked_cell=link)
    assert values["status"] == "partial" and values["groups"][0]["status"] == "unavailable"
    assert len(calls) == 1  # No fallback to latest and no unrelated content reads.


def test_malformed_cell_link_ref_is_not_absence(transport):
    _, calls = transport
    content = linked_content()
    del content["data"][0]["cells"][0]["value"]["destinationLink"]["destinationLink"]["revision"]
    assert sources.cell_link(content, "table-c", "synthetic-token", None)["status"] == "unavailable"
    assert calls == []
