"""Read one selected cell without detectors, policy guesses, writes, or activity logs.

Content and calculated values come from different Workiva endpoints. Repeat both
reads and reject changed evidence; this is not an atomic or revision-pinned snapshot.
Unconnected policy and downstream evidence must never become a clean verdict.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import quote, urlencode, urlsplit

import wk_client as wk
from cell_sources import cell_link, formula_references, range_links, read_cells, source_values, stored_content, table_properties


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


def validate_source_target(table_id, revision, addr, workbook_hint=None):
    if any(not isinstance(v, str) or not v.strip() or len(v) > 2048
           or any(ord(c) < 32 for c in v) for v in (table_id, revision)):
        raise ValueError("A source tableId and recorded revision are required")
    if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", addr or ""):
        raise ValueError("Select one source cell, such as D6")
    if workbook_hint is not None and (not isinstance(workbook_hint, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workbook_hint)):
        raise ValueError("A valid workbookHint is required when supplied")


def document_tables(document_id, section_id, token, ctx, *, revision=None):
    """List body tables for one section, bound to one document revision.

    Refuse incomplete metadata instead of offering an ambiguous table choice.
    Header/footer tables and native document selection are not supported here.
    """
    validate_target(document_id, section_id, "A1")
    if revision is not None:
        validate_source_target("table", revision, "A1")
    result = {"target": {"documentId": document_id, "sectionId": section_id},
              "readOnly": True, "status": "unavailable", "tables": [],
              "observedAt": datetime.now(timezone.utc).isoformat()}
    try:
        query = "?" + urlencode({"$revision": revision}) if revision is not None else ""
        tables = wk._get(f"/documents/{document_id}/tables{query}", token, ctx, version="2026-01-01")
        pinned = tables["revision"]
        validate_source_target("table", pinned, "A1")
        if (revision is not None and pinned != revision) or tables.get("@nextLink"):
            raise ValueError("Incomplete or mismatched table metadata")
        section = wk._get(f"/documents/{document_id}/sections/{section_id}?{urlencode({'$revision': pinned})}",
                          token, ctx, version="2026-01-01")
        body = section["body"]
        if (section["id"] != section_id or section["revision"] != pinned
                or body["revision"] != pinned or not body["richText"]):
            raise ValueError("Mismatched section metadata")
        selected = []
        seen = set()
        for table in tables["data"]:
            if table["parent"] != {"type": "richText", "richText": body["richText"]}:
                continue
            table_id = table["id"]
            validate_source_target(table_id, pinned, "A1")
            if table_id in seen:
                raise ValueError("Duplicate table identity")
            seen.add(table_id)
            selected.append({"tableId": table_id, "name": table.get("name") or "Unnamed table"})
        result.update(status="observed", revision=pinned, sectionName=section.get("name"), tables=selected)
    except Exception:
        result["reason"] = "Could not verify a complete table list for this section at one revision. No tables offered."
    return result


def inspect_document(document_id, section_id, table_id, revision, addr, token, ctx):
    """Inspect an explicitly chosen document cell, never an inferred native selection."""
    validate_target(document_id, section_id, addr)
    validate_source_target(table_id, revision, addr)
    catalog = document_tables(document_id, section_id, token, ctx, revision=revision)
    target = {"documentId": document_id, "sectionId": section_id,
              "tableId": table_id, "revision": revision, "addr": addr}
    if catalog["status"] != "observed" or not any(t["tableId"] == table_id for t in catalog["tables"]):
        return {"target": target, "readOnly": True, "status": "unavailable", "sourceValuesRequested": True,
                "observedAt": catalog["observedAt"],
                "warnings": ["The chosen table could not be verified in this section at the recorded revision. No cell read."]}
    result = inspect_source(table_id, revision, addr, token, ctx)
    result.update(target=target, sectionName=catalog["sectionName"])
    return result


def inspect_source(table_id, revision, addr, token, ctx, *, workbook_hint=None):
    """Inspect one historical table cell and its direct sources, only on request.

    The caller's workbook is only a candidate. Prove sheet/table membership at the
    recorded revision before resolving named-sheet formulas within that workbook.
    Never use current sheetdata or mix in latest range-link lists.
    """
    validate_source_target(table_id, revision, addr, workbook_hint)
    result = {
        "target": {"tableId": table_id, "revision": revision, "addr": addr},
        "readOnly": True, "status": "unavailable", "sourceValuesRequested": True,
        "observedAt": datetime.now(timezone.utc).isoformat(), "warnings": [],
    }
    try:
        props = table_properties(table_id, revision, token, ctx)
        row, col = wk.rc_from_a1(addr)
        raw = read_cells(table_id, (row, row, col, col), token, ctx, revision)
        cell = raw["data"][0]["cells"][0]
        content = stored_content(cell)
    except Exception:
        result["warnings"].append("Source unavailable at its recorded revision. No latest-revision substitute or new evidence returned.")
        return result
    value = cell["value"]
    computed = value.get("formula") if isinstance(value, dict) and value.get("type") == "formula" else None
    calculated = ({"status": "observed", "value": computed["calculatedValue"]}
                  if isinstance(computed, dict) and "calculatedValue" in computed else {"status": "unavailable"})
    formula = formula_references(content)
    linked_cell = cell_link(raw, table_id, token, ctx)
    links = {"status": "not_inspected"}
    location = {"status": "not_inspected"}
    spreadsheet_id = None
    if workbook_hint is not None:
        location = {"status": "unavailable"}
        try:
            sheet_id = props["sheet"]
            validate_target(workbook_hint, sheet_id, addr)
            sheet = wk._get(f"/spreadsheets/{quote(workbook_hint, safe='')}/sheets/{quote(sheet_id, safe='')}"
                            f"?{urlencode({'$revision': revision})}", token, ctx, version="2026-01-01")
            if (sheet["id"] != sheet_id or sheet["revision"] != revision
                    or sheet["table"] != {"table": table_id, "revision": revision}):
                raise ValueError("Source workbook membership or revision mismatch")
            spreadsheet_id = workbook_hint
            location = {"status": "observed", "spreadsheetId": workbook_hint,
                        "sheetId": sheet_id, "revision": revision}
        except Exception:
            pass  # Retain table evidence; an unproven candidate grants no workbook context.
    result.update(status="partial", tableName=props.get("name"), tableId=table_id,
                  contentRevision=revision, content=content, calculated=calculated,
                  nativeFormat={"status": "not_inspected"}, location=location, source={
                      "formula": formula, "cellLink": linked_cell, "rangeLinks": links,
                      "values": source_values(spreadsheet_id, table_id, revision, formula, links, token, ctx,
                                              linked_cell=linked_cell),
                  })
    result["warnings"].append("Historical content only. Range-link lists and native format are not inspected.")
    if spreadsheet_id is None:
        result["warnings"].append("Source workbook identity is not established. Named-sheet references were not followed.")
    return result


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
    result["tableId"] = table_id
    read_cell = lambda: _cell(spreadsheet_id, sheet_id, addr, token, ctx)
    read_content = lambda: _content(table_id, addr, token, ctx)
    cell, content = _observe(read_cell), _observe(read_content)
    links = range_links(table_id, addr, token, ctx) if cell["status"] == "observed" else {"status": "not_inspected"}
    decoded = (stored_content(content["value"]["data"][0]["cells"][0])
               if content["status"] == "observed" else {"status": "unavailable"})
    linked_cell = (cell_link(content["value"], table_id, token, ctx)
                   if content["status"] == cell["status"] == "observed" else {"status": "unavailable"})
    formula = formula_references(decoded)
    values = None
    if include_sources and cell["status"] == content["status"] == "observed":
        revision = content["value"]["revision"]
        values = source_values(spreadsheet_id, table_id, revision, formula, links, token, ctx, linked_cell=linked_cell)
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
    result["source"]["cellLink"] = linked_cell if result["content"]["status"] == "observed" else {"status": "unavailable"}
    if values is not None and content["status"] == content_after["status"] == "observed":
        result["source"]["values"] = values
    result["status"] = "observed" if all(
        result[key]["status"] == "observed" for key in ("content", "calculated", "nativeFormat")
    ) else "partial"
    result["observedAt"] = datetime.now(timezone.utc).isoformat()
    result["warnings"].append("Not an atomic snapshot of cells and links. Report correctness is not assessed.")
    return result
