"""Direct source evidence for Inspect, not a dependency graph or accounting verdict.

Range-link contract: developers.workiva.com/2026-01-01/content-rangelinks-guide.html
Destinations occupy their entire table. source.range is zero-based and inclusive.
Source revisions identify publication, not proof of current downstream correctness.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlsplit

import wk_client as wk


_CELL = r"\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}"
_QUALIFIER = r"(?:'(?:[^']|'')+'|[A-Z_][A-Z0-9_.]*)!"
_REFERENCE = re.compile(
    rf"(?:{_QUALIFIER})?(?:{_CELL}(?::{_CELL})?|\$?[A-Z]{{1,3}}:\$?[A-Z]{{1,3}}|\$?[1-9][0-9]*:\$?[1-9][0-9]*)"
    r"(?![A-Z0-9_.!\[:#])", re.I,
)
_TOKENS = re.compile(
    r'(?P<string>"(?:[^"]|"")*")'
    r"|(?P<external>\[[^\]]*\][^+*/^&=<>(),;\s]*)"
    r"|(?P<structured>[A-Z_][A-Z0-9_.]*\[(?:[^\[\]]|\[[^\]]*\])*\])"
    r"|(?P<function>[A-Z_][A-Z0-9_.]*\s*(?=\())"
    r"|(?P<number>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:E[+-]?[0-9]+)?)(?![0-9:])",
    re.I,
)


def formula_references(content):
    """Tokenize supported static A1 notation; never evaluate or fetch precedents.

    Strings/function names are not references. Preserve unresolved notation rather
    than extracting a misleading A1 suffix from an external/structured/3-D reference.
    This deliberately reports text_only even if every token is recognized.
    """
    if content.get("status") != "observed" or content.get("kind") == "unknown":
        return {"status": "unavailable"}
    formula = content.get("formula")
    if not formula:
        return {"status": "not_formula"}
    references, numbers, unresolved = [], [], []
    pos = 1 if formula.startswith("=") else 0
    while pos < len(formula):
        token = _TOKENS.match(formula, pos)
        if token:
            text, kind = token.group(), token.lastgroup
            if kind == "number":
                numbers.append(text)
            elif kind in ("external", "structured"):
                unresolved.append(text)
            elif kind == "function" and text.strip().upper() in ("INDIRECT", "OFFSET", "INDEX"):
                unresolved.append(text.strip() + "(…) — computed reference not resolved")
            pos = token.end()
            continue
        reference = _REFERENCE.match(formula, pos)
        if reference:
            text = reference.group()
            # Quoted external and 3-D qualifiers are not local sheet references.
            qualifier = text.rsplit("!", 1)[0] if "!" in text else ""
            if ":" in qualifier or "[" in qualifier:
                unresolved.append(text)
            else:
                references.append(text)
            pos = reference.end()
            continue
        if formula[pos].isspace() or formula[pos] in "+-*/^&=<>(),;%{}":
            pos += 1
            continue
        unknown = re.match(r"[^+*/^&=<>(),;%{}\s]+", formula[pos:])
        text = unknown.group() if unknown else formula[pos]
        if text.upper() not in ("TRUE", "FALSE"):
            unresolved.append(text)
        pos += len(text)
    return {"status": "text_only", "references": list(dict.fromkeys(references)),
            "literalNumbers": list(dict.fromkeys(numbers)),
            "unresolved": list(dict.fromkeys(unresolved))}


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Missing range-link identity")
    return value


def _bounds(source):
    bounds = source["range"]
    r0, r1, c0, c1 = [bounds[k] for k in ("startRow", "stopRow", "startColumn", "stopColumn")]
    if (any(type(v) is not int or not 0 <= v < 10_000_000 for v in (r0, r1, c0, c1))
            or r0 > r1 or c0 > c1):
        raise ValueError("Invalid source range")
    return r0, r1, c0, c1


def _range_text(bounds):
    r0, r1, c0, c1 = bounds
    start, stop = wk.a1(r0, c0), wk.a1(r1, c1)
    return start if start == stop else start + ":" + stop


def range_links(table_id, addr, token, ctx):
    """Read all table link pages or mark incomplete; only return links covering addr.

    Never use the legacy scan reader here: it returns [] on a failed page. Resolve
    destination ranges at their reported source revision, never at latest revision.
    No destination enumeration, source cell values, or writes are performed.
    """
    items = []
    try:
        table_id = _identity(table_id)
        row, col = wk.rc_from_a1(addr)
        path = f"/content/tables/{quote(table_id, safe='')}/rangeLinks"
        seen_paths, seen_ids = set(), set()
        for _ in range(50):
            if not isinstance(path, str):
                raise ValueError("Invalid pagination URL")
            url, base = urlsplit(path), urlsplit(wk._base())
            if ((url.netloc and url.netloc != base.netloc)
                    or (url.scheme and url.scheme != base.scheme) or path in seen_paths):
                raise ValueError("Untrusted or repeated pagination URL")
            seen_paths.add(path)
            page = wk._get_url(path, token, ctx, version="2026-01-01")
            if not isinstance(page.get("data"), list):
                raise ValueError("Incomplete range-link list")
            for link in page["data"]:
                identity = _identity(link["id"])
                if link["table"] != table_id or identity in seen_ids:
                    raise ValueError("Wrong table or repeated range link")
                seen_ids.add(identity)
                if link["type"] == "source":
                    bounds = _bounds(link["source"])
                    r0, r1, c0, c1 = bounds
                    if r0 <= row <= r1 and c0 <= col <= c1:
                        items.append({"direction": "source", "id": identity,
                                      "range": _range_text(bounds), "revision": link.get("revision")})
                elif link["type"] == "destination":
                    source = link["destination"]["source"]
                    items.append({"direction": "destination", "id": identity, "source": {
                        key: _identity(source[key]) for key in ("table", "rangeLink", "revision")},
                        "resolution": "not_inspected"})
                else:
                    raise ValueError("Unknown range-link direction")
            path = page.get("@nextLink")
            if path is None or path == "":
                break
        else:
            raise ValueError("Range-link pagination limit reached")
    except Exception:
        # Do not expose upstream diagnostics or convert failed reads into absence.
        return {"status": "partial" if items else "unavailable", "items": items}

    for item in items:
        if item["direction"] != "destination":
            continue
        source = item["source"]
        try:
            path = (f"/content/tables/{quote(source['table'], safe='')}/rangeLinks/"
                    f"{quote(source['rangeLink'], safe='')}?{urlencode({'$revision': source['revision']})}")
            raw = wk._get(path, token, ctx, version="2026-01-01")
            if (raw["type"] != "source" or raw["id"] != source["rangeLink"]
                    or raw["table"] != source["table"] or raw["revision"] != source["revision"]):
                raise ValueError("Source identity or revision mismatch")
            item.update(resolution="observed", sourceRange=_range_text(_bounds(raw["source"])))
        except Exception:
            item["resolution"] = "unavailable"
    return {"status": "observed", "items": items}


def cell_link(content, table_id, token, ctx):
    """Follow a CellDestinationLinkValue to its recorded source, never range offsets.

    Contract: content-links-guide.html, getdestinationlinkbyid.html and
    gettableanchorbyid.html in Workiva's 2026-01-01 documentation.
    This does not enumerate inline rich-text links or source-link destinations.
    """
    result = {"status": "unavailable", "resolution": "unavailable"}
    try:
        value = content["data"][0]["cells"][0]["value"]
        if not isinstance(value, dict) or value.get("type") != "destinationLink":
            return {"status": "not_present"}
        ref = value["destinationLink"]["destinationLink"]
        identity, revision = _identity(ref["destinationLink"]), _identity(ref["revision"])
        result.update(id=identity, revision=revision)
        raw = wk._get(f"/content/destinationLinks/{quote(identity, safe='')}?{urlencode({'$revision': revision})}",
                      token, ctx, version="2026-01-01")
        if (raw["id"] != identity or raw["revision"] != revision
                or raw["content"]["type"] != "table" or raw["content"]["table"] != table_id
                or raw["status"] not in ("connected", "disconnected")):
            raise ValueError("Destination identity, revision or status mismatch")
        result.update(status="observed", linkState=raw["status"])
        if raw["status"] == "disconnected":
            result.update(resolution="disconnected", reason="A disconnected link can retain its last published value. No source was followed.")
            return result
        source = raw["source"]
        if source["type"] != "anchor":
            result.update(resolution="unsupported", reason="Workiva did not identify a supported source anchor.")
            return result
        anchor = source["anchor"]
        source_type = anchor["content"]["type"]
        result["source"] = {"type": source_type, "anchor": _identity(anchor["anchor"]),
                            "revision": _identity(anchor["revision"])}
        if source_type != "table":
            result.update(resolution="unsupported", reason="Non-table source content is not read by this inspector.")
            return result
        source_table = _identity(anchor["content"]["table"])
        result["source"]["table"] = source_table
        raw_anchor = wk._get(
            f"/content/tables/{quote(source_table, safe='')}/anchors/{quote(anchor['anchor'], safe='')}"
            f"?{urlencode({'$revision': anchor['revision']})}", token, ctx, version="2026-01-01")
        if (raw_anchor["id"] != anchor["anchor"] or raw_anchor["revision"] != anchor["revision"]
                or raw_anchor["content"]["type"] != "table" or raw_anchor["content"]["table"] != source_table
                or raw_anchor["attachmentPoint"]["type"] != "tableRange"):
            raise ValueError("Source anchor identity or revision mismatch")
        bounds = _bounds(raw_anchor["attachmentPoint"])
        if bounds[0] != bounds[1] or bounds[2] != bounds[3]:
            raise ValueError("Cell source anchor is not a single cell")
        result.update(resolution="observed", sourceCell=_range_text(bounds))
    except Exception:
        result["reason"] = "Cell link or source anchor unavailable at its recorded revision; no latest-revision substitute."
    return result


def stored_content(cell):
    """Keep raw content separate from formatted/computed strings and numeric types."""
    value = cell["value"]
    formula = wk._formula_from_content_value(value, cell.get("rawValue"))
    if isinstance(value, dict) and "rawValue" in cell:
        native_type = value.get("type")
        value = cell["rawValue"]
        kind = ("formula" if formula else "linked_value" if native_type == "destinationLink" else
                "unknown" if native_type not in ("plainText", "richText") else
                "blank" if value == "" else "raw_value")
    else:
        kind = ("formula" if formula else "blank" if value in (None, "") else
                "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else
                "text" if isinstance(value, str) else "unknown")
    return {"status": "observed", "value": value, "kind": kind, "formula": formula}


def read_cells(table_id, bounds, token, ctx, revision=None):
    """One complete bounded content read. Never sample or substitute a newer revision."""
    r0, r1, c0, c1 = bounds
    count = (r1 - r0 + 1) * (c1 - c0 + 1)
    if not 0 < count <= 100:
        raise ValueError("Range exceeds the 100-cell read limit")
    query = dict(zip(("startRow", "stopRow", "startColumn", "stopColumn"), bounds))
    if revision is not None:
        query["$revision"] = _identity(revision)
    raw = wk._get(f"/content/tables/{quote(_identity(table_id), safe='')}/cells?{urlencode(query)}",
                  token, ctx, version="2026-01-01")
    observed_revision = _identity(raw["revision"])
    rows = raw["data"]
    if (raw.get("@nextLink") or _bounds(raw) != bounds or len(rows) != r1 - r0 + 1
            or (revision is not None and observed_revision != revision)
            or any(len(row["cells"]) != c1 - c0 + 1 for row in rows)):
        raise ValueError("Incomplete or mismatched content range/revision")
    for row in rows:
        for cell in row["cells"]:
            if not isinstance(cell, dict) or "value" not in cell:
                raise ValueError("Missing cell content")
    return {"revision": observed_revision, "data": rows}


def _a1_bounds(address):
    if not re.fullmatch(rf"{_CELL}(?::{_CELL})?", address, re.I):
        raise ValueError("Only bounded A1 ranges can be read")
    ends = address.replace("$", "").upper().split(":")
    r0, c0 = wk.rc_from_a1(ends[0])
    r1, c1 = wk.rc_from_a1(ends[-1])
    return _bounds({"range": dict(zip(("startRow", "stopRow", "startColumn", "stopColumn"), (r0, r1, c0, c1)))})


def _named_table(spreadsheet_id, name, revision, token, ctx):
    """Resolve a unique same-workbook sheet at the selected content revision."""
    path = f"/spreadsheets/{quote(spreadsheet_id, safe='')}/sheets?{urlencode({'$revision': revision})}"
    seen, matches = set(), []
    for _ in range(50):
        url, base = urlsplit(path), urlsplit(wk._base())
        if ((url.netloc and url.netloc != base.netloc) or (url.scheme and url.scheme != base.scheme)
                or path in seen):
            raise ValueError("Untrusted or repeated sheet pagination")
        seen.add(path)
        raw = wk._get_url(path, token, ctx, version="2026-01-01")
        matches.extend(sheet for sheet in raw["data"] if sheet["name"].casefold() == name.casefold())
        path = raw.get("@nextLink")
        if path is None or path == "":
            if len(matches) != 1:
                raise ValueError("Sheet name missing or ambiguous")
            if matches[0]["table"]["revision"] != revision:
                raise ValueError("Sheet metadata revision mismatch")
            return _identity(matches[0]["table"]["table"])
    raise ValueError("Sheet metadata incomplete")


def source_values(spreadsheet_id, table_id, revision, formula, links, token, ctx, *, linked_cell=None):
    """Opt-in direct evidence only: 100 cells across at most 10 range reads."""
    groups, used_cells, used_ranges = [], 0, 0
    candidates = [{"reference": ref, "basis": "selected_revision", "revision": revision,
                   "tableId": table_id} for ref in formula.get("references", [])]
    if linked_cell and linked_cell.get("status") != "not_present":
        source = linked_cell.get("source", {})
        candidates.insert(0, {"reference": linked_cell.get("sourceCell"), "basis": "cell_link_revision",
                              "revision": source.get("revision"), "tableId": source.get("table"),
                              "reason": linked_cell.get("reason", "Cell-link source is unresolved; no values read.")})
    for link in links.get("items", []):
        if link["direction"] == "destination":
            candidates.append({"reference": link.get("sourceRange"), "basis": "published_revision",
                               "revision": link["source"]["revision"], "tableId": link["source"]["table"]})
    for candidate in candidates:
        group = dict(candidate, status="unavailable")
        groups.append(group)
        reference = candidate["reference"]
        if not reference:
            group["reason"] = candidate.get("reason", "Source range is unresolved; no values read.")
            continue
        address = reference.rsplit("!", 1)[-1]
        try:
            bounds = _a1_bounds(address)
        except ValueError:
            group["reason"] = "Unbounded or unsupported range; no values read."
            continue
        r0, r1, c0, c1 = bounds
        count = (r1 - r0 + 1) * (c1 - c0 + 1)
        if used_cells + count > 100 or used_ranges >= 10:
            group.update(status="limited", reason="100-cell / 10-range limit; this range was not sampled.")
            continue
        used_cells += count
        used_ranges += 1
        try:
            rev = _identity(candidate["revision"])
            if "!" in reference and candidate["basis"] == "selected_revision":
                name = reference.rsplit("!", 1)[0]
                if name.startswith("'"):
                    name = name[1:-1].replace("''", "'")
                group["tableId"] = _named_table(spreadsheet_id, name, rev, token, ctx)
            table = quote(_identity(group["tableId"]), safe="")
            props = wk._get(f"/content/tables/{table}/properties?{urlencode({'$revision': rev})}",
                            token, ctx, version="2026-01-01")
            if props["id"] != group["tableId"] or props["revision"] != rev:
                raise ValueError("Table identity or revision mismatch")
            raw = read_cells(group["tableId"], bounds, token, ctx, rev)
            cells = []
            for ri, row in enumerate(raw["data"]):
                for ci, cell in enumerate(row["cells"]):
                    content = stored_content(cell)
                    value = cell["value"]
                    computed = value.get("formula") if isinstance(value, dict) and value.get("type") == "formula" else None
                    calculated = ({"status": "observed", "value": computed["calculatedValue"]}
                                  if isinstance(computed, dict) and "calculatedValue" in computed else
                                  {"status": "unavailable"})
                    cells.append({"addr": wk.a1(r0 + ri, c0 + ci), "content": content, "calculated": calculated})
            group.update(status="observed", name=props.get("name"), range=_range_text(bounds), cells=cells)
            group.pop("reason", None)
        except Exception:
            group["reason"] = "Source could not be read at this revision; no latest-revision substitute."
    incomplete = (formula.get("status") == "unavailable" or bool(formula.get("unresolved"))
                  or links.get("status") != "observed" or any(group["status"] != "observed" for group in groups))
    return {"status": "partial" if incomplete else "observed", "groups": groups,
            "cellLimit": 100, "rangeLimit": 10}
