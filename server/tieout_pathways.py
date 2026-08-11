#!/usr/bin/env python3
"""
Tieout diagnosis pathways for Wingman check-tieout queue items.

Keeps generic pathways in diagnose.py; this module adds scorecard-aware next actions
when tieout_enrich has attached coordinates and heuristics.
"""
from __future__ import annotations

from typing import Any

from tieout_enrich import (
    CAUSE_DOUBLE_AGG,
    CAUSE_RECLASS,
    CAUSE_SCALING,
    CAUSE_SIGN_FLIP,
    CAUSE_TB_SOURCE_GAP,
    CAUSE_UNKNOWN,
    cell_addr,
)

_PATHWAY_BY_CAUSE: dict[str, dict[str, str]] = {
    CAUSE_DOUBLE_AGG: {
        "pathway_id": "tieout.acfr-double-agg",
        "title": "Total column double-count",
        "explain": (
            "Workbook Total is approximately twice the published PDF target while "
            "individual activity columns tie. The Total formula likely sums a "
            "component twice instead of Gov + BTA."
        ),
        "suggested_action": (
            "Jump to the flagged cell; trace the Total-column formula against Gov "
            "and BTA columns on the same row. Fix the SUM range before AcctMap changes."
        ),
    },
    CAUSE_TB_SOURCE_GAP: {
        "pathway_id": "tieout.acfr-acctmap-gap",
        "title": "AcctMap / TB source gap (ZERO)",
        "explain": (
            "Workbook shows ~$0 while the published PDF carries material amount. "
            "Trial balance GL is not flowing through AcctMap to this statement line."
        ),
        "suggested_action": (
            "Open AcctMap; search STMT_LINE for this row; add or retag GL codes for "
            "the fund column. Re-run the tieout scorecard after mapping — do not patch the face formula first."
        ),
    },
    CAUSE_RECLASS: {
        "pathway_id": "tieout.acfr-reclass",
        "title": "Large variance — Adjustments / reclass",
        "explain": (
            "Variance exceeds the 50K critic threshold with no scaling or double-count pattern. "
            "Often a missing Adjustments entry or GASB reclass not yet seated."
        ),
        "suggested_action": (
            "Check Adjustments sheet for this STMT_LINE + fund column; confirm reclass "
            "code from workpapers before editing face formulas."
        ),
    },
    CAUSE_SIGN_FLIP: {
        "pathway_id": "tieout.acfr-sign-flip",
        "title": "Sign convention mismatch",
        "explain": (
            "Variance pattern matches 2× workbook magnitude — typical of $A sign-column "
            "or AcctMap SIGN_CONVENTION drift on expense/liability rows."
        ),
        "suggested_action": (
            "Inspect AcctMap col N (SIGN_CONVENTION) for the SLC feeding this row; "
            "compare formula $A multiplier to peer rows in the same section."
        ),
    },
    CAUSE_SCALING: {
        "pathway_id": "tieout.acfr-scaling",
        "title": "Rounding / display scale",
        "explain": "Variance is within THOUSANDS display rounding tolerance (< $1K).",
        "suggested_action": "Confirm publish state; accept or document as immaterial rounding.",
    },
    CAUSE_UNKNOWN: {
        "pathway_id": "tieout.acfr-unknown",
        "title": "Tieout break — investigate",
        "explain": "The scorecard critic heuristics did not classify this break.",
        "suggested_action": "Compare calc vs pub at the jumped cell; trace formula inputs fund-by-fund.",
    },
}


def _coord_hint(row: dict[str, Any]) -> str:
    jump = row.get("jumpHint") or {}
    addr = jump.get("addr") or row.get("addr")
    sheet = jump.get("sheetName") or row.get("sheetName") or row.get("stmt")
    if addr and sheet:
        return f"{sheet} · {addr}"
    if addr:
        return str(addr)
    wb_row, wb_col = row.get("wb_row"), row.get("wb_col")
    a = cell_addr(wb_row, wb_col)
    return f"{sheet} · {a}" if a and sheet else (a or "")


def enrich_tieout_diagnosis(item: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Apply scorecard tieout pathway overrides to a check-tieout queue item."""
    if item.get("kind") != "check-tieout" and row.get("check") != "tieout":
        return item

    heuristic = row.get("tieout_heuristic") or {}
    cause = heuristic.get("cause") or CAUSE_UNKNOWN
    pathway = dict(_PATHWAY_BY_CAUSE.get(cause, _PATHWAY_BY_CAUSE[CAUSE_UNKNOWN]))

    coord = _coord_hint(row)
    if coord:
        pathway["suggested_action"] = f"{pathway['suggested_action']} Cell: {coord}."

    notes = heuristic.get("notes")
    if notes:
        pathway["explain"] = f"{pathway['explain']} Critic: {notes}"

    diagnosis = dict(item.get("diagnosis") or {})
    diagnosis.update(pathway)
    diagnosis["tieout_cause"] = cause
    diagnosis["tieout_confidence"] = heuristic.get("confidence")
    if coord:
        diagnosis["tieout_coord"] = coord

    out = dict(item)
    out["diagnosis"] = diagnosis

    # Surface jump target on the queue item for the extension.
    jump = row.get("jumpHint")
    if jump:
        out["jumpHint"] = dict(jump)
        out["sheetName"] = jump.get("sheetName") or out.get("sheetName")
        addr = jump.get("addr")
        if addr:
            out["addrs"] = [addr]
            out["target"] = {"addr": addr, "sheetName": out.get("sheetName")}

    if row.get("wb_row") is not None:
        out["wb_row"] = row["wb_row"]
    if row.get("wb_col") is not None:
        out["wb_col"] = row["wb_col"]
    if row.get("tieout_status"):
        out["tieout_status"] = row["tieout_status"]

    return out
