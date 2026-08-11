#!/usr/bin/env python3
"""
Document range-link awareness (ADR-0008).

`GET /content/tables/{tid}/rangeLinks` returns BLOCK-level links: a rectangular range is one link
(verified live on a production ACFR workbook — 123/224 sheets carry `type:"source"` links shaped
`{id, type, revision, table, source:{range:{startRow,stopRow,startColumn,stopColumn}}}`), NOT per-cell
metadata.

That granularity is why the per-cell `detectors.detect_blank_linked` is the wrong tool — naively
flagging every blank cell inside a 24-row linked statement block would fire on every spacer row
(ADR-0002/0004 cry-wolf bar). This lane works at the LINK level instead:

- `analyze_links` marks which scanned cells fall inside a link range (`linkRole`/`linkId`) for the
  link map + future DL-source detectors, WITHOUT setting the per-cell `isLinked` flag (so the
  per-cell detector stays inert by design).
- It surfaces one `empty-linked-range` finding per link whose entire block has **zero populated
  cells** — a guaranteed-blank destination document table — and only when the sheet scan was
  complete (not truncated), so an off-page block is never mis-reported as empty.
"""
from __future__ import annotations

from typing import Optional

import wk_client


def _link_range(lk: dict) -> dict:
    return lk.get("range") or {}


def _range_a1(rng: dict) -> str:
    r0, r1 = sorted((int(rng["startRow"]), int(rng["stopRow"])))
    c0, c1 = sorted((int(rng["startColumn"]), int(rng["stopColumn"])))
    return f"{_rc_to_a1(r0, c0)}:{_rc_to_a1(r1, c1)}"


def classify_link_health(links: list[dict], destinations_by_link_id: Optional[dict[str, list[dict]]] = None, anchors: Optional[list[dict]] = None):
    """Classify link health using the proven source/destination/orphan vocabulary.

    This is deliberately pure: callers provide `links`, optional destination lists, and optional
    anchors. Fetching remains in `wk_client`/routes so tests can validate semantics without Workiva.

    Statuses:
    - `healthy`: source link has one or more destinations, or anchor is linked.
    - `no_dest`: source link has zero known destinations.
    - `destination`: destination-side link marker.
    - `orphaned`: unknown link type or unlinked anchor.
    """
    destinations_by_link_id = destinations_by_link_id or {}
    anchors = anchors or []
    rows: list[dict] = []
    summary = {"total": 0, "healthy": 0, "orphaned": 0, "no_dest": 0, "destination": 0}

    for lk in links:
        lid = lk.get("id", "")
        ltype = lk.get("type", "unknown")
        summary["total"] += 1
        if ltype == "source":
            destinations = destinations_by_link_id.get(lid, [])
            if destinations:
                status = "healthy"
                summary["healthy"] += 1
            else:
                status = "no_dest"
                summary["no_dest"] += 1
            rows.append({
                "id": lid,
                "type": "source",
                "status": status,
                "dest_count": len(destinations),
                "destinations": [{"id": d.get("id", ""), "table_id": d.get("table", "") or d.get("table_id", "")} for d in destinations[:10]],
            })
        elif ltype == "destination":
            summary["destination"] += 1
            rows.append({"id": lid, "type": "destination", "status": "destination", "dest_count": 0, "destinations": []})
        else:
            summary["orphaned"] += 1
            rows.append({"id": lid, "type": ltype, "status": "orphaned", "dest_count": 0, "destinations": []})

    for anchor in anchors:
        aid = anchor.get("id", "")
        summary["total"] += 1
        linked = bool(anchor.get("linked"))
        if linked:
            status = "healthy"
            summary["healthy"] += 1
        else:
            status = "orphaned"
            summary["orphaned"] += 1
        rows.append({"id": aid, "type": f"anchor ({anchor.get('type', 'unknown')})", "status": status, "dest_count": 0, "destinations": []})

    return {"links": rows, "summary": summary}


def classify_publish_state(links: list[dict]):
    """Classify source/destination revision agreement for linked ranges.

    Workiva rangeLinks expose a `revision` per link row. When source and destination rows for the
    same link id are both present, matching revisions mean the destination has current source
    content; differing revisions mean the document-side linked range is stale/unpublished. Source- or
    destination-only rows are surfaced as proof gaps, not as safe-auto actions.
    """
    by_id: dict[str, dict[str, list[dict]]] = {}
    for lk in links or []:
        lid = lk.get("id")
        if not lid:
            continue
        typ = lk.get("type") or "unknown"
        by_id.setdefault(str(lid), {}).setdefault(str(typ), []).append(lk)

    rows: list[dict] = []
    summary = {"total": 0, "published": 0, "unpublished": 0, "source_only": 0, "destination_only": 0, "unknown": 0}
    for lid in sorted(by_id):
        bucket = by_id[lid]
        sources = bucket.get("source", [])
        destinations = bucket.get("destination", [])
        source_revs = {str(x.get("revision")) for x in sources if x.get("revision")}
        dest_revs = {str(x.get("revision")) for x in destinations if x.get("revision")}
        if sources and destinations and source_revs and dest_revs:
            if source_revs & dest_revs:
                status = "published"
            else:
                status = "unpublished"
        elif sources and not destinations:
            status = "source_only"
        elif destinations and not sources:
            status = "destination_only"
        else:
            status = "unknown"
        summary["total"] += 1
        summary[status] += 1
        anchor_link = (sources or destinations or bucket.get("unknown", []) or [{}])[0]
        rows.append({
            "id": lid,
            "status": status,
            "source_revisions": sorted(source_revs),
            "destination_revisions": sorted(dest_revs),
            "source_count": len(sources),
            "destination_count": len(destinations),
            "range": _range_a1(_link_range(anchor_link)) if _link_range(anchor_link) else None,
        })
    return {"links": rows, "summary": summary}


def _rc_to_a1(row: int, col: int) -> str:
    """0-based (row, col) -> A1 (e.g. (4, 2) -> 'C5')."""
    letters = ""
    c = col + 1
    while c > 0:
        c, rem = divmod(c - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row + 1}"


def _is_blank(cell: dict) -> bool:
    cv = cell.get("calculatedValue")
    if cv is not None and str(cv).strip() != "":
        return False
    v = cell.get("value")
    if v is not None and str(v).strip() != "":
        return False
    return True


_DL_NUMERIC_FORMAT_TYPES = frozenset({"ACCOUNTING", "CURRENCY", "NUMBER"})


def detect_dl_source_numeric_formula(cells: list[dict]) -> list[dict]:
    """D20: Source-link cells with numeric formulas render raw digits in the document.

    Workiva's document-link (DL) cache copies `calculatedValue` (the raw number), not
    `effectiveValue` (the ACCOUNTING/NUMBER formatted string). A cell formatted as ACCOUNTING
    that shows "$1,234,567" in the spreadsheet will render as "1234567" in the linked document.
    This fires after `analyze_links` stamps `linkRole`; it must be called on the same cell list.

    Groups by link ID so the finding reports once per source-link range, not once per cell.
    TEXT() formula cells are explicitly exempt (they already return a string to DL).
    """
    by_link: dict[str, list[dict]] = {}
    for cell in cells:
        if cell.get("linkRole") != "source":
            continue
        formula = (cell.get("formula") or "").strip()
        if not formula.startswith("="):
            continue
        if formula.upper().startswith("=TEXT("):
            continue
        cv = str(cell.get("calculatedValue") or "").strip()
        if not cv:
            continue
        try:
            float(cv)
        except (ValueError, TypeError):
            continue
        vft = (cell.get("valueFormatType") or "").upper()
        if vft not in _DL_NUMERIC_FORMAT_TYPES:
            continue
        lid = cell.get("linkId") or cell.get("addr", "")
        by_link.setdefault(str(lid), []).append(cell)

    findings: list[dict] = []
    for lid, affected in sorted(by_link.items()):
        first = affected[0]
        count = len(affected)
        vft = (first.get("valueFormatType") or "").upper()
        sample_cv = str(first.get("calculatedValue") or "")
        cell_word = "cell" if count == 1 else f"{count} cells"
        findings.append({
            "kind": "dl-source-numeric-formula",
            "addr": first.get("addr"),
            "severity": "high",
            "detail": (
                f"DL source link {lid!r}: {cell_word} "
                f"({first.get('addr')}) have {vft.lower()} numeric formulas — "
                f"document links copy calculatedValue ({sample_cv!r}), not the formatted text; "
                f"the linked document will render raw digits. "
                f"Wrap each source cell with TEXT() before linking."
            ),
            "fixable": False,
            "fix_lane": "surfaced",
            "target": None,
            "linkId": lid,
            "affectedCellCount": count,
            "sampleCalculatedValue": sample_cv,
            "valueFormatType": vft,
        })
    return findings


def analyze_links(cells: list[dict], links: list[dict], *, truncated: bool = False):
    """Mark link cells and surface fully-empty linked blocks.

    Returns (findings, meta). Mutates `cells` in place to stamp `linkRole`/`linkId` on every cell
    that falls inside a link range (used for the link map; does NOT set `isLinked`)."""
    by_rc: dict[tuple[int, int], dict] = {}
    for c in cells:
        rc = wk_client.rc_from_a1(c.get("addr"))
        if rc is not None:
            by_rc[rc] = c

    findings: list[dict] = []
    summaries: list[dict] = []
    for lk in links:
        rng = lk.get("range") or {}
        r0, r1 = rng.get("startRow"), rng.get("stopRow")
        c0, c1 = rng.get("startColumn"), rng.get("stopColumn")
        if None in (r0, r1, c0, c1):
            continue
        # Normalize in case stop < start.
        r0, r1 = sorted((int(r0), int(r1)))
        c0, c1 = sorted((int(c0), int(c1)))
        in_scan: list[dict] = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                cell = by_rc.get((r, c))
                if cell is not None:
                    in_scan.append(cell)
                    cell["linkRole"] = lk.get("type")
                    cell["linkId"] = lk.get("id")
        populated = [c for c in in_scan if not _is_blank(c)]
        range_a1 = _range_a1(rng)
        anchor = range_a1.split(":", 1)[0]
        # Empty only when we can prove it: no populated cell AND the sheet scan was complete
        # (a truncated scan may simply not have reached the block's cells).
        empty = (not populated) and (not truncated)
        summaries.append({
            "id": lk.get("id"), "type": lk.get("type"), "range": range_a1,
            "cellsInScan": len(in_scan), "populated": len(populated), "empty": empty,
        })
        if empty:
            findings.append({
                "kind": "empty-linked-range",
                "addr": anchor,
                "severity": "high",
                "detail": f"linked block {range_a1} is entirely blank — the destination "
                          f"document table will render empty",
                "fixable": False,
                "fix_lane": "surfaced",
                "target": None,
                "rangeA1": range_a1,
                "linkId": lk.get("id"),
                "linkType": lk.get("type"),
            })

    dl_numeric_findings = detect_dl_source_numeric_formula(cells)
    findings.extend(dl_numeric_findings)

    health = classify_link_health(links)
    publish_state = classify_publish_state(links)
    for row in publish_state["links"]:
        if row.get("status") != "unpublished":
            continue
        anchor = (row.get("range") or "A1:A1").split(":", 1)[0]
        findings.append({
            "kind": "unpublished-linked-range",
            "addr": anchor,
            "severity": "high",
            "detail": (
                f"linked range {row.get('range') or '(unknown range)'} has source revision "
                f"{','.join(row.get('source_revisions') or ['?'])} but destination revision "
                f"{','.join(row.get('destination_revisions') or ['?'])} — publish links before export"
            ),
            "fixable": False,
            "fix_lane": "surfaced",
            "target": None,
            "rangeA1": row.get("range"),
            "linkId": row.get("id"),
            "linkPublishStatus": "unpublished",
        })
    meta = {
        "enabled": True,
        "linkCount": len(links),
        "sourceLinks": sum(1 for l in links if l.get("type") == "source"),
        "destinationLinks": sum(1 for l in links if l.get("type") == "destination"),
        "emptyLinkedBlocks": len([f for f in findings if f.get("kind") == "empty-linked-range"]),
        "unpublishedLinkedRanges": publish_state["summary"].get("unpublished", 0),
        "dlSourceNumericFormulas": len(dl_numeric_findings),
        "links": summaries[:200],
        # Shared link-health vocabulary for UI/review-packet guidance. Destination fetching is not
        # wired yet, so source links without supplied destination evidence classify as `no_dest`
        # rather than being asserted publish-ready.
        "linkHealth": health,
        "linkHealthVocabulary": ["healthy", "no_dest", "destination", "orphaned"],
        "publishState": publish_state,
        "publishStateVocabulary": ["published", "unpublished", "source_only", "destination_only", "unknown"],
    }
    return findings, meta


if __name__ == "__main__":  # tiny self-test
    def chk(name, cond):
        print(("PASS " if cond else "FAIL ") + name)

    chk("rc_to_a1 C5", _rc_to_a1(4, 2) == "C5")
    chk("rc_to_a1 AA1", _rc_to_a1(0, 26) == "AA1")
    cells = [
        {"addr": "B2", "calculatedValue": ""},
        {"addr": "C2", "value": ""},
    ]
    links = [{"id": "L1", "type": "source",
              "range": {"startRow": 1, "stopRow": 1, "startColumn": 1, "stopColumn": 2}}]
    f, m = analyze_links(cells, links, truncated=False)
    chk("empty block fires once", len(f) == 1 and f[0]["kind"] == "empty-linked-range")
    chk("cells marked linkRole", cells[0]["linkRole"] == "source")
    cells2 = [{"addr": "B2", "calculatedValue": "100"}, {"addr": "C2", "value": ""}]
    f2, _ = analyze_links(cells2, links, truncated=False)
    chk("populated block does not fire", len(f2) == 0)
    f3, _ = analyze_links([{"addr": "B2", "calculatedValue": ""}], links, truncated=True)
    chk("truncated scan never asserts empty", len(f3) == 0)
