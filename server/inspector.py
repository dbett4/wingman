"""Read one selected cell without detectors, policy guesses, writes, or activity logs.

Content and calculated values come from different Workiva endpoints. Repeat both
reads and reject changed evidence; this is not an atomic or revision-pinned snapshot.
Unconnected policy and downstream evidence must never become a clean verdict.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

import wk_client as wk
from cell_sources import formula_references, range_links, read_cells, source_values, stored_content


def validate_target(spreadsheet_id, sheet_id, addr):
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v or "")
               for v in (spreadsheet_id, sheet_id)):
        raise ValueError("Valid spreadsheetId and sheetId required")
    if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", addr or ""):
        raise ValueError("Select one cell, such as B7; ranges are not supported")


def _sheet(spreadsheet_id, sheet_id, token, ctx):
    path = f"/spreadsheets/{spreadsheet_id}/sheets?$maxpagesize=500"
    for _ in range(50):
        raw = wk._get_url(path, token, ctx, version="2026-01-01")
        for sheet in raw.get("data", []):
            if sheet.get("id") == sheet_id:
                return sheet
        path = raw.get("@nextLink")
        if not path:
            raise ValueError("Sheet not found in this workbook's metadata")
        # Never forward the service's credentials to an unrelated pagination host.
        if urlsplit(path).netloc and urlsplit(path).netloc != urlsplit(wk._base()).netloc:
            raise ValueError("Untrusted metadata pagination URL")
        if urlsplit(path).scheme and urlsplit(path).scheme != urlsplit(wk._base()).scheme:
            raise ValueError("Untrusted metadata pagination scheme")
    raise ValueError("Sheet metadata is incomplete: pagination limit reached")


def _cell(spreadsheet_id, sheet_id, addr, token, ctx):
    raw = wk.get_sheetdata_cell(spreadsheet_id, sheet_id, addr, token, ctx)
    data = raw.get("data", {})
    row, col = wk.rc_from_a1(addr)
    bounds = data.get("range", {})
    cells = data.get("cells", [])
    if (raw.get("@nextLink") or bounds.get("startRow") != row
            or bounds.get("startColumn") != col or len(cells) != 1 or len(cells[0]) != 1
            or not isinstance(cells[0][0], dict)):
        raise ValueError("Cell read did not match the requested coordinates")
    return cells[0][0]


def _content(table_id, addr, token, ctx):
    if not table_id:
        raise ValueError("No content table identified")
    row, col = wk.rc_from_a1(addr)
    return read_cells(table_id, (row, row, col, col), token, ctx)


def _observe(read):
    try:
        return {"status": "observed", "value": read()}
    except Exception as exc:
        # Do not return upstream bodies/URLs/tokens as user-facing diagnostics.
        return {"status": "unavailable", "reason": type(exc).__name__}


def inspect_cell(spreadsheet_id, sheet_id, addr, token, ctx, *, include_sources=False):
    validate_target(spreadsheet_id, sheet_id, addr)
    target = {"spreadsheetId": spreadsheet_id, "sheetId": sheet_id, "addr": addr}
    result = {
        "target": target, "readOnly": True, "status": "unavailable",
        "sourceValuesRequested": include_sources,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "policy": {"status": "not_connected"},
        "downstream": {"status": "not_inspected"},
        "source": {"formula": {"status": "not_inspected"}, "rangeLinks": {"status": "not_inspected"}},
        "warnings": [],
    }
    meta = _observe(lambda: _sheet(spreadsheet_id, sheet_id, token, ctx))
    if meta["status"] != "observed":
        result["warnings"].append("Cannot resolve this sheet in the requested workbook. No cell evidence returned.")
        return result
    sheet = meta["value"]
    result["sheetName"] = sheet.get("name")
    table = sheet.get("table")
    result["revision"] = table.get("revision") if isinstance(table, dict) else None
    table_id = table.get("table") if isinstance(table, dict) else None
    read_cell = lambda: _cell(spreadsheet_id, sheet_id, addr, token, ctx)
    read_content = lambda: _content(table_id, addr, token, ctx)
    cell, content = _observe(read_cell), _observe(read_content)
    links = range_links(table_id, addr, token, ctx) if cell["status"] == "observed" else {"status": "not_inspected"}
    decoded = (stored_content(content["value"]["data"][0]["cells"][0])
               if content["status"] == "observed" else {"status": "unavailable"})
    formula = formula_references(decoded)
    values = None
    if include_sources and cell["status"] == content["status"] == "observed":
        revision = content["value"]["revision"]
        values = source_values(spreadsheet_id, table_id, revision, formula, links, token, ctx)
    cell_after, content_after = _observe(read_cell), _observe(read_content)
    # Python equality equates False with 0; native content types must remain distinct.
    if any(before["status"] == after["status"] == "observed"
           and json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True)
           for before, after in ((cell, cell_after), (content, content_after))):
        result["status"] = "changed"
        result["warnings"].append("The cell or its content revision changed during inspection. Inspect again; mixed evidence was discarded.")
        return result
    if cell["status"] != "observed" or cell_after["status"] != "observed":
        result["warnings"].append("Calculated value and format could not be read consistently. Inspect again.")
        return result
    native = cell["value"]
    result["calculated"] = ({"status": "observed", "value": native["calculatedValue"]}
                            if "calculatedValue" in native else {"status": "unavailable"})
    formats = native.get("effectiveFormats")
    vf = formats.get("valueFormat") if isinstance(formats, dict) else None
    result["nativeFormat"] = ({"status": "observed", "value": vf}
                              if isinstance(vf, dict) else {"status": "unavailable"})
    if content["status"] != "observed" or content_after["status"] != "observed":
        result["content"] = {"status": "unavailable"}
        result["warnings"].append("Stored content is unavailable. A calculated number does not prove a hardcode.")
    else:
        result["content"] = decoded
        result["contentRevision"] = content["value"]["revision"]
    result["source"] = {"formula": formula_references(result["content"]), "rangeLinks": links}
    if values is not None and content["status"] == content_after["status"] == "observed":
        result["source"]["values"] = values
    result["status"] = "observed" if all(
        result[key]["status"] == "observed" for key in ("content", "calculated", "nativeFormat")
    ) else "partial"
    result["observedAt"] = datetime.now(timezone.utc).isoformat()
    result["warnings"].append("Not an atomic snapshot of cells and links. Report correctness is not assessed.")
    return result
