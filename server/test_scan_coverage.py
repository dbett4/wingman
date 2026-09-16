"""Coverage regressions through the real scanner, queue and packet; fictional transport only."""
import copy

import pytest

import diagnose
import review_packet
import wk_client as wk


def complete_sheet(sheet_id="complete", name="Review notes"):
    return {
        "sheetId": sheet_id, "name": name, "cellCount": 2, "findingCount": 0,
        "groups": [], "error": None, "truncated": False,
        **{key: {"enabled": True, "partial": False}
           for key in ("formula_fetch", "type_fetch", "link_fetch")},
    }


def mixed_workbook():
    """Asymmetric zero-finding states also used by the installed browser regression."""
    complete = complete_sheet()
    partial = {**complete_sheet("partial", "Statement detail"), "truncated": True}
    failed = {**complete_sheet("failed", "Restricted sheet"), "error": "Access denied"}
    unavailable = complete_sheet("unavailable", "Supporting calculations")
    unavailable["formula_fetch"] = {"enabled": True, "partial": True,
                                    "reason": "Content cell read failed or incomplete"}
    return {"spreadsheetId": "de00", "sheetCount": 5, "scanned": 4, "truncatedSheets": True,
            "findingTotal": 0, "sheets": [complete, partial, failed, unavailable]}


def test_queue_and_packet_keep_zero_finding_coverage():
    scan = mixed_workbook()
    original = copy.deepcopy(scan)
    queue = diagnose.build_workbook_queue(scan)
    assert scan == original
    assert len(queue["sheets"]) == 4
    assert [s["coverage"]["complete"] for s in queue["sheets"]] == [True, False, False, False]
    assert queue["sheets"][1]["truncated"] is True
    assert queue["sheets"][3]["formula_fetch"] == scan["sheets"][3]["formula_fetch"]
    assert all("groups" not in s for s in queue["sheets"])
    # Coverage gaps are not invented cell findings. Only the existing scan error is an item.
    assert [i["kind"] for i in queue["items"]] == ["scan-error"]
    assert queue["issueCount"] == 0
    assert queue["coverage"]["warnings"][0] == "1 of 5 sheets were not scanned"
    packet = review_packet.build_review_packet(queue)
    assert packet["overall"] == "BLOCKED"
    assert packet["coverage"] == queue["coverage"]
    assert "Statement detail: sheet scan truncated" in review_packet.render_packet_md(packet)


@pytest.mark.parametrize("gap", ["none", "cap", "pages", "metadata", "inventory", "legacy"])
def test_zero_findings_do_not_imply_complete(gap):
    scan = {"spreadsheetId": "ss", "sheetCount": 1, "scanned": 1,
            "truncatedSheets": False, "sheets": [complete_sheet()]}
    if gap == "cap":
        scan.update(sheetCount=2, truncatedSheets=True)
    elif gap == "pages":
        scan["sheets"][0]["truncated"] = True
    elif gap == "metadata":
        del scan["sheets"][0]["link_fetch"]
    elif gap == "inventory":
        scan["sheets"] = []
    queue = diagnose.build_workbook_queue(scan)
    if gap == "legacy":
        del queue["sheets"]
    packet = review_packet.build_review_packet(queue)
    assert packet["overall"] == ("CLEAN" if gap == "none" else "UNVERIFIED")
    assert packet["coverage"]["complete"] is (gap == "none")
    assert packet["summary"]["total"] == 0


@pytest.mark.parametrize("failure", ["none", "content-error", "content-short", "content-shape", "link-error", "link-shape"])
def test_scanner_records_enrichment_failure_before_queue(monkeypatch, failure):
    monkeypatch.setenv("WINGMAN_FORMULA_FETCH", "on")
    monkeypatch.setenv("WINGMAN_TYPE_FETCH", "on")
    monkeypatch.setenv("WINGMAN_LINK_FETCH", "on")

    def read(path, *_args, **_kwargs):
        if "/sheets?" in path:
            return {"data": [{"id": "s1", "name": "Notes", "table": {"table": "t1"}}]}
        if "/sheetdata" in path:
            return {"data": {"range": {"startRow": 2, "startColumn": 1},
                             "cells": [[{"value": "Alpha"}, {"value": "Beta"}]]}}
        if "/cells?" in path:
            if failure == "content-error":
                raise RuntimeError("fictional upstream failure")
            if failure == "content-shape":
                return {"data": "unavailable"}
            cells = [{"value": "Alpha"}, {"value": "Beta"}]
            return {"data": [{"cells": cells[:1] if failure == "content-short" else cells}]}
        if path.endswith("/rangeLinks"):
            if failure == "link-error":
                raise RuntimeError("fictional upstream failure")
            return {"data": {} if failure == "link-shape" else []}
        raise AssertionError(path)

    monkeypatch.setattr(wk, "_get", read)
    scan = wk.scan_workbook("ss", token="fictional", ctx=object())
    queue = diagnose.build_workbook_queue(scan)
    assert queue["sheets"][0]["error"] is None
    assert queue["sheets"][0]["cellCount"] == 2
    assert queue["items"] == []
    assert queue["coverage"]["complete"] is (failure == "none")
    # A literal-only sheet legitimately has zero formula strings and still has full coverage.
    assert queue["sheets"][0]["formula_fetch"]["enriched_count"] == 0
    assert review_packet.build_review_packet(queue)["overall"] == ("CLEAN" if failure == "none" else "UNVERIFIED")


def test_scanner_preserves_page_limit_and_omitted_sheet(monkeypatch):
    monkeypatch.setattr(wk, "list_sheets", lambda *_: [{"id": "s1"}, {"id": "s2"}])
    monkeypatch.setattr(wk, "_sheet_table_ids", lambda *_: {})
    monkeypatch.setattr(wk, "scan_sheet", lambda *_a, **_kw: ([], [], True, None, None, None, None))
    queue = diagnose.build_workbook_queue(wk.scan_workbook("ss", token="fictional", ctx=object(), max_sheets=1))
    assert queue["scanned"] == 1 and queue["sheetCount"] == 2
    assert queue["truncatedSheets"] and queue["sheets"][0]["truncated"]
    assert queue["sheets"][0]["coverage"]["complete"] is False


def test_link_page_cap_and_late_failure_keep_observations(monkeypatch):
    first = {"data": [{"id": "link1", "type": "source"}], "@nextLink": "/next"}
    for limit, fails in [(1, False), (2, True), (2, False)]:
        def read(path, *_args, **_kwargs):
            if path == "/next":
                if fails:
                    raise RuntimeError("fictional second page error")
                return {"data": []}
            return first
        monkeypatch.setattr(wk, "_get", read)
        meta = {}
        links = wk.fetch_range_links("t1", "fictional", None, max_pages=limit, meta=meta)
        assert [link["id"] for link in links] == ["link1"]
        assert meta["partial"] is (limit == 1 or fails)


def test_type_coverage_uses_actual_shared_cap(monkeypatch):
    monkeypatch.setenv("WINGMAN_FORMULA_FETCH", "scoped")
    monkeypatch.setenv("WINGMAN_TYPE_FETCH", "on")
    monkeypatch.setenv("WINGMAN_LINK_FETCH", "off")
    monkeypatch.setattr(wk, "FORMULA_FETCH_SCOPED_CAP", 1)
    monkeypatch.setattr(wk, "iter_sheetdata", lambda *_a, **_kw: iter([
        {"data": {"cells": [[{"value": "Alpha"}, {"value": "Beta"}]]}}]))
    monkeypatch.setattr(wk, "_get", lambda *_a, **_kw: {"data": [{"cells": [{"value": "Alpha"}]}]})
    cells, _, _, _, fm, tm, lm = wk.scan_sheet("ss", "sh", "fictional", object(), table_id="t1")
    assert cells[0]["type"] == "plainText" and cells[1]["type"] is None
    assert fm["partial"] and tm["partial"] and tm["cap"] == 1
    assert lm["enabled"] is False and lm["partial"] is True


@pytest.mark.parametrize("raw", [{}, {"data": {}}, {"data": [{}]},
                                 {"data": [], "@nextLink": "/more"}])
def test_incomplete_inventory_never_becomes_empty_workbook(monkeypatch, raw):
    monkeypatch.setattr(wk, "_get", lambda *_a, **_kw: raw)
    with pytest.raises(ValueError, match="Sheet inventory"):
        wk.scan_workbook("ss", token="fictional", ctx=object())


def test_malformed_cell_page_is_not_an_empty_sheet(monkeypatch):
    monkeypatch.setattr(wk, "iter_sheetdata", lambda *_a, **_kw: iter([{"data": {}}]))
    with pytest.raises(ValueError, match="Sheet cell response"):
        wk.scan_sheet("ss", "sh", token="fictional", ctx=object())
