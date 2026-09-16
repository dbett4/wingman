#!/usr/bin/env python3
"""
Wingman issue→pathway diagnosis (Slice 1 foundation).

Maps detector output (findings / grouped scan rows) to explain/judge metadata using
patterns from operational error/fix playbooks built up across live engagements.
Pure logic — no network — so it is unit-tested without Workiva creds.

Public API:
  diagnose_finding(finding) -> enriched finding dict
  diagnose_group(group, *, sheet_id=None, sheet_name=None) -> queue item dict
  build_sheet_queue(groups, *, sheet_id, sheet_name=None) -> prioritized queue
  build_workbook_queue(workbook_scan) -> prioritized queue across sheets
"""
from __future__ import annotations

import hashlib
from typing import Any

import export_proof

import diagnose_pathways
from diagnose_pathways import (
    BLANK_DL_GUIDED_STEPS,
    blank_dl_guided_steps,
    lookup_check_pathway,
    lookup_pathway,
)

# Severity weights for queue ordering (higher = more urgent).
_SEV = {"high": 300, "medium": 200, "low": 100}


def _stable_id(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return h[:12]


def _priority(severity: str, fix_lane: str, count: int, kind: str) -> int:
    # Severity dominates; safe-auto gets a modest boost within the same band.
    base = _SEV.get(severity, 50) * 10
    base += min(max(count, 1), 20) * 2
    if kind in ("blank-linked-cell", "broken-ref", "display-wrapper"):
        base += 50
    if fix_lane == "safe-auto":
        base += 30
    return base


def diagnose_check_fail(
    row: dict[str, Any],
    *,
    spreadsheet_id: str | None = None,
) -> dict[str, Any]:
    """Turn a run_checks FAIL row into a queue item."""
    check = str(row.get("check") or "unknown")
    detail = str(row.get("detail") or row.get("raw_check") or check)
    pathway = lookup_check_pathway(check, detail)
    kind = f"check-{check.replace('_', '-')}"
    fix_lane = "surfaced"
    severity = "high"
    item_id = _stable_id(spreadsheet_id or "", "check", check, detail)
    priority = _priority(severity, fix_lane, 1, kind) + 120  # checks rank above cosmetic scan items
    if check == "tieout" and row.get("source") in ("tieout_table", "tieout_scorecard"):
        priority += 80
    if check == "tieout" and row.get("jumpHint"):
        priority += 40
    if check.startswith("hardening_gate_"):
        priority += 60
    src = str(row.get("source") or "run_checks")
    jump = row.get("jumpHint") or {}
    sheet_name = jump.get("sheetName") or row.get("stmt") or row.get("sheet") or row.get("statement")
    addrs: list[str] = []
    if jump.get("addr"):
        addrs = [str(jump["addr"])]
    diagnosis: dict[str, Any] = {
        **pathway,
        "lane": fix_lane,
        "fixable": False,
    }
    if check == "tieout":
        export_proof.attach_export_proof_to_diagnosis(diagnosis, row)
    item: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "severity": severity,
        "priority": priority,
        "fixable": False,
        "fix_lane": fix_lane,
        "signature": detail,
        "count": 1,
        "addrs": addrs,
        "cellValues": {},
        "target": {"addr": addrs[0], "sheetName": sheet_name} if addrs else None,
        "spreadsheetId": spreadsheet_id,
        "sheetId": None,
        "sheetName": sheet_name,
        "source": src,
        "check": check,
        "diagnosis": diagnosis,
    }
    if row.get("wb_row") is not None:
        item["wb_row"] = row["wb_row"]
    if row.get("wb_col") is not None:
        item["wb_col"] = row["wb_col"]
    if row.get("jumpHint"):
        item["jumpHint"] = dict(row["jumpHint"])
    if row.get("tieout_status"):
        item["tieout_status"] = row["tieout_status"]
    if row.get("delta") is not None:
        item["delta"] = row["delta"]
    if check == "tieout" and (row.get("tieout_heuristic") or row.get("source") == "tieout_scorecard"):
        try:
            import tieout_pathways
            item = tieout_pathways.enrich_tieout_diagnosis(item, row)
        except ImportError:
            pass
    return item


def check_fails_to_queue_items(
    fail_rows: list[dict[str, Any]],
    *,
    spreadsheet_id: str | None = None,
    scorecard_generated_at: str | None = None,
) -> list[dict[str, Any]]:
    items = [diagnose_check_fail(r, spreadsheet_id=spreadsheet_id) for r in fail_rows]
    if scorecard_generated_at:
        for item in items:
            if item.get("kind") == "check-tieout":
                item["scorecard_generated_at"] = scorecard_generated_at
                dx = dict(item.get("diagnosis") or {})
                dx["scorecard_generated_at"] = scorecard_generated_at
                item["diagnosis"] = dx
    items.sort(key=lambda x: (-x["priority"], x["kind"], x["signature"]))
    return items


def merge_queue_with_checks(
    queue: dict[str, Any],
    checks_meta: dict[str, Any],
) -> dict[str, Any]:
    """Merge run_checks FAIL rows into an existing queue payload."""
    out = dict(queue)
    out["checks"] = {
        k: checks_meta.get(k)
        for k in (
            "requested", "suite", "source", "applied", "skipped", "live_skipped",
            "fail_count", "error", "exit_code", "report_path", "working_dir", "project",
            "elapsed_s", "planned_operations", "evaluation", "scorecard_generated_at",
            "tieout_enrich",
        )
        if k in checks_meta
    }
    if not checks_meta.get("applied") or checks_meta.get("skipped"):
        return out

    ss = out.get("spreadsheetId")
    check_items = check_fails_to_queue_items(
        checks_meta.get("fail_rows") or [],
        spreadsheet_id=ss,
        scorecard_generated_at=checks_meta.get("scorecard_generated_at"),
    )
    scan_items = list(out.get("items") or [])
    merged = scan_items + check_items
    merged.sort(key=lambda x: (-x["priority"], x.get("sheetName") or "", x["kind"], x["signature"]))
    out["items"] = merged
    out["groupCount"] = len(merged)
    out["issueCount"] = sum(i["count"] for i in merged)
    summary = dict(out.get("summary") or {})
    summary["groups"] = len(merged)
    summary["cells"] = sum(i["count"] for i in merged)
    summary["high"] = sum(1 for i in merged if i["severity"] == "high")
    summary["fixable"] = sum(1 for i in merged if i["fixable"])
    summary["review"] = len(merged) - summary["fixable"]
    summary["checks"] = len(check_items)
    out["summary"] = summary
    return out


def diagnose_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Attach diagnosis block to a raw detector finding."""
    pathway = lookup_pathway(
        finding["kind"],
        finding.get("fix_lane", "surfaced"),
        finding.get("detail", ""),
        target=finding.get("target"),
    )
    diagnosis: dict[str, Any] = {
        **pathway,
        "lane": finding.get("fix_lane"),
        "fixable": bool(finding.get("fixable")),
    }
    if finding["kind"] == "blank-linked-cell":
        diagnosis["guided_steps"] = blank_dl_guided_steps()
        diagnosis["guided_lane"] = "blank-dl"
    out = dict(finding)
    out["diagnosis"] = diagnosis
    return out


def diagnose_group(
    group: dict[str, Any],
    *,
    sheet_id: str | None = None,
    sheet_name: str | None = None,
    spreadsheet_id: str | None = None,
) -> dict[str, Any]:
    """Turn a grouped scan row into a prioritized queue item with explain/judge metadata."""
    kind = group["kind"]
    fix_lane = group.get("fix_lane", "surfaced")
    detail = group.get("signature") or ""
    pathway = lookup_pathway(
        kind,
        fix_lane,
        detail if kind == "broken-ref" else detail,
        target=group.get("target"),
    )
    count = int(group.get("count") or len(group.get("addrs") or []))
    item_id = _stable_id(spreadsheet_id or "", sheet_id or "", kind, fix_lane, detail, str(count))
    diagnosis: dict[str, Any] = {
        **pathway,
        "lane": fix_lane,
        "fixable": bool(group.get("fixable")),
    }
    if kind == "blank-linked-cell":
        diagnosis["guided_steps"] = blank_dl_guided_steps()
        diagnosis["guided_lane"] = "blank-dl"
    if kind == "display-wrapper":
        export_proof.attach_export_proof_to_diagnosis(
            diagnosis,
            {"label": detail, "detail": detail},
            replace_pathway=False,
        )
    return {
        "id": item_id,
        "kind": kind,
        "severity": group.get("severity", "medium"),
        "priority": _priority(group.get("severity", "medium"), fix_lane, count, kind),
        "fixable": bool(group.get("fixable")),
        "fix_lane": fix_lane,
        "signature": group.get("signature") or detail,
        "count": count,
        "addrs": list(group.get("addrs") or []),
        "cellValues": dict(group.get("cellValues") or {}),
        "target": group.get("target"),
        "gated_target": group.get("gated_target"),
        "gated_columns": group.get("gated_columns"),
        "spreadsheetId": spreadsheet_id,
        "sheetId": sheet_id,
        "sheetName": sheet_name,
        "diagnosis": diagnosis,
    }


def build_sheet_queue(
    groups: list[dict[str, Any]],
    *,
    spreadsheet_id: str,
    sheet_id: str,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    """Build a prioritized issue queue for one sheet's grouped findings."""
    items = [
        diagnose_group(g, sheet_id=sheet_id, sheet_name=sheet_name, spreadsheet_id=spreadsheet_id)
        for g in groups
    ]
    items.sort(key=lambda x: (-x["priority"], x["kind"], x["signature"]))
    high = sum(1 for i in items if i["severity"] == "high")
    fixable = sum(1 for i in items if i["fixable"])
    return {
        "scope": "sheet",
        "spreadsheetId": spreadsheet_id,
        "sheetId": sheet_id,
        "sheetName": sheet_name,
        "issueCount": sum(i["count"] for i in items),
        "groupCount": len(items),
        "summary": {
            "groups": len(items),
            "cells": sum(i["count"] for i in items),
            "high": high,
            "fixable": fixable,
            "review": len(items) - fixable,
        },
        "items": items,
    }


def scan_coverage(scan: dict[str, Any]) -> dict[str, Any]:
    """Coverage of automatic cell checks, not accounting accuracy or publication proof.

    Findings and coverage are independent: zero findings cannot supply missing evidence.
    Legacy payloads without an inventory or explicit read metadata remain unverified.
    """
    warnings: list[str] = []
    if scan.get("scope") == "workbook":
        sheets = scan.get("sheets")
        count, attempted = scan.get("sheetCount"), scan.get("scanned")
        if not isinstance(sheets, list) or count is None or attempted is None:
            warnings.append("Sheet inventory unavailable; workbook coverage is unknown")
        else:
            if count > attempted:
                warnings.append(f"{count - attempted} of {count} sheets were not scanned")
            elif scan.get("truncatedSheets"):
                warnings.append("Workbook sheet limit reached; some sheets were not scanned")
            if len(sheets) != attempted:
                warnings.append("Coverage records are missing for attempted sheets")
            for sheet in sheets:
                label = sheet.get("name") or sheet.get("sheetId") or "Unnamed sheet"
                warnings.extend(f"{label}: {w}" for w in scan_coverage(sheet)["warnings"])
    elif scan.get("error"):
        warnings.append("Sheet scan failed; no complete result is available")
    else:
        if scan.get("truncated") is True:
            warnings.append("sheet scan truncated; remaining pages were not checked")
        elif scan.get("truncated") is not False:
            warnings.append("Cell-page coverage unavailable")
        for key, label in (("formula_fetch", "Formula checks"), ("type_fetch", "Cell-type checks"),
                           ("link_fetch", "Link checks")):
            meta = scan.get(key)
            if not isinstance(meta, dict) or not meta:
                warnings.append(f"{label}: coverage unavailable")
                continue
            reason = meta.get("error") or meta.get("skipped") or meta.get("skip_reason")
            if (reason or meta.get("enabled") is not True or meta.get("partial") is not False
                    or meta.get("truncated") or meta.get("applied") is False):
                warnings.append(f"{label}: {reason or meta.get('reason') or 'incomplete or unavailable'}")
    checks = scan.get("checks") or {}
    if checks.get("error"):
        warnings.append(f"Checks error: {checks['error']}")
    return {"complete": not warnings, "warnings": warnings}


def build_workbook_queue(workbook_scan: dict[str, Any]) -> dict[str, Any]:
    """Flatten a /scan-workbook payload into one prioritized cross-sheet queue."""
    ss = workbook_scan.get("spreadsheetId")
    items: list[dict[str, Any]] = []
    for sheet in workbook_scan.get("sheets") or []:
        if sheet.get("error"):
            items.append({
                "id": _stable_id(ss or "", sheet.get("sheetId") or "", "scan-error"),
                "kind": "scan-error",
                "severity": "high",
                "priority": 900,
                "fixable": False,
                "fix_lane": "surfaced",
                "signature": sheet["error"],
                "count": 1,
                "addrs": [],
                "target": None,
                "spreadsheetId": ss,
                "sheetId": sheet.get("sheetId"),
                "sheetName": sheet.get("name"),
                "diagnosis": {
                    "pathway_id": "scan.error",
                    "title": "Sheet scan failed",
                    "explain": sheet["error"],
                    "judgment": "surfaced — API read failed for this sheet; other sheets may still be valid.",
                    "suggested_action": "Retry scan or inspect sheet access; fix auth/lock errors first.",
                    "trap_refs": [],
                    "fix_refs": [],
                    "lane": "surfaced",
                    "fixable": False,
                },
            })
            continue
        for g in sheet.get("groups") or []:
            items.append(
                diagnose_group(
                    g,
                    sheet_id=sheet.get("sheetId"),
                    sheet_name=sheet.get("name"),
                    spreadsheet_id=ss,
                )
            )
    items.sort(key=lambda x: (-x["priority"], x.get("sheetName") or "", x["kind"], x["signature"]))
    high = sum(1 for i in items if i["severity"] == "high")
    fixable = sum(1 for i in items if i["fixable"])
    sheets = [{**{k: v for k, v in sheet.items() if k != "groups"},
               "coverage": scan_coverage(sheet)} for sheet in workbook_scan.get("sheets") or []]
    return {
        "scope": "workbook",
        "spreadsheetId": ss,
        "sheetCount": workbook_scan.get("sheetCount"),
        "scanned": workbook_scan.get("scanned"),
        "truncatedSheets": workbook_scan.get("truncatedSheets"),
        "sheets": sheets,
        "coverage": scan_coverage({**workbook_scan, "scope": "workbook"}),
        "issueCount": workbook_scan.get("findingTotal", sum(i["count"] for i in items)),
        "groupCount": len(items),
        "summary": {
            "groups": len(items),
            "cells": sum(i["count"] for i in items),
            "high": high,
            "fixable": fixable,
            "review": len(items) - fixable,
        },
        "items": items,
    }


# ---- self-test ----

def _selftest() -> bool:
    cases: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        cases.append((name, bool(cond)))

    f = diagnose_finding({
        "kind": "low-contrast", "addr": "A1", "severity": "medium", "detail": "#FFFFFF on #7F7F7F",
        "fixable": True, "fix_lane": "safe-auto", "target": {"hex": "#000000"},
    })
    check("finding gets diagnosis", "diagnosis" in f and f["diagnosis"]["pathway_id"] == "format.contrast-plain-auto")

    g = diagnose_group({
        "kind": "low-contrast", "severity": "medium", "signature": "#FFFFFF on #7F7F7F",
        "fixable": True, "fix_lane": "safe-auto", "target": {"hex": "#000000"},
        "addrs": ["B3", "E3"], "count": 2,
    }, sheet_id="sh1", sheet_name="TB", spreadsheet_id="ss1")
    check("group queue item fields", g["count"] == 2 and g["sheetId"] == "sh1" and g["priority"] > 2000)
    check("stable id", len(g["id"]) == 12)

    br = diagnose_group({
        "kind": "broken-ref", "severity": "high", "signature": "formula result is #REF!",
        "fixable": False, "fix_lane": "surfaced", "addrs": ["E1"], "count": 1,
    })
    check("broken ref pathway", br["diagnosis"]["pathway_id"] == "formula.broken-ref")

    bdl = diagnose_group({
        "kind": "blank-linked-cell", "severity": "high",
        "signature": "cell carries a link but renders blank",
        "fixable": False, "fix_lane": "surfaced", "addrs": ["D12"], "count": 1,
    })
    check("blank-DL guided steps", bdl["diagnosis"].get("guided_steps") and len(bdl["diagnosis"]["guided_steps"]) == 4)
    check("blank-DL first step id", bdl["diagnosis"]["guided_steps"][0]["id"] == "verify-source")

    check("blank-DL guided lane", bdl["diagnosis"].get("guided_lane") == "blank-dl")

    tie = diagnose_check_fail({
        "check": "tieout",
        "detail": "Gov_Funds CY assets Δ=+100.00",
        "source": "tieout_table",
        "label": "Total assets",
        "calc": "1,000",
        "pub": "1,100",
    })
    check("tieout export-proof steps", tie["diagnosis"].get("guided_steps") and len(tie["diagnosis"]["guided_steps"]) == 5)
    check("tieout export-proof lane", tie["diagnosis"].get("guided_lane") == "export-proof")

    nm = diagnose_group({
        "kind": "broken-ref", "severity": "high", "signature": "formula result is #NAME?",
        "fixable": False, "fix_lane": "surfaced", "addrs": ["E2"], "count": 1,
    })
    check("name error pathway", nm["diagnosis"]["pathway_id"] == "formula.unsupported-name")

    q = build_sheet_queue([g, br], spreadsheet_id="ss1", sheet_id="sh1", sheet_name="TB")
    check("sheet queue sorted high first", q["items"][0]["kind"] == "broken-ref")
    check("sheet queue summary", q["summary"]["groups"] == 2 and q["summary"]["fixable"] == 1)

    wb = build_workbook_queue({
        "spreadsheetId": "ss1", "sheetCount": 2, "scanned": 2, "findingTotal": 3,
        "sheets": [
            {"sheetId": "a", "name": "Clean", "groups": [], "findingCount": 0, "error": None},
            {"sheetId": "b", "name": "Stmt", "groups": [g, br], "findingCount": 3, "error": None},
            {"sheetId": "c", "name": "Bad", "groups": [], "findingCount": 0, "error": "HTTP 403"},
        ],
    })
    check("workbook queue includes scan error", any(i["kind"] == "scan-error" for i in wb["items"]))
    check("workbook scope", wb["scope"] == "workbook")

    passed = sum(1 for _, ok in cases if ok)
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(cases)} passed")
    return passed == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
