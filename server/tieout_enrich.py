#!/usr/bin/env python3
"""
Tie-out scorecard enrichment for Wingman queue rows.

Loads validation/tieout_scorecard.json (wb_row, wb_col, critic heuristics)
and attaches coordinates + jump hints to run_checks tieout FAIL rows.

Configure:
  WINGMAN_TIEOUT_SCORECARD — override path to scorecard JSON
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Any

import client_preset

SHEET_ALIAS: dict[str, str] = {
    "Gov-Wide - Activities (General Revenues)": "Gov-Wide - Activities",
}

DEFAULT_REL = "validation/tieout_scorecard.json"

# Heuristic cause ids (aligned with the tieout scorecard generator's critic + Wingman extensions).
CAUSE_DOUBLE_AGG = "double_aggregation"
CAUSE_TB_SOURCE_GAP = "tb_source_gap"
CAUSE_RECLASS = "reclass"
CAUSE_SIGN_FLIP = "sign_flip"
CAUSE_SCALING = "scaling_rounding"
CAUSE_UNKNOWN = "unknown"


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("_", " ").strip().lower())


def col_index_to_letter(col_idx: int) -> str:
    """1-indexed column number → Excel letter (no openpyxl dep)."""
    if col_idx < 1:
        return ""
    letters = ""
    c = col_idx
    while c > 0:
        c, rem = divmod(c - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def cell_addr(row: int | None, col: int | None) -> str | None:
    if row is None or col is None:
        return None
    letter = col_index_to_letter(col)
    return f"{letter}{row}" if letter else None


def resolve_scorecard_path(
    working_dir: str | None = None,
    project: str | None = None,
) -> pathlib.Path | None:
    """Return the tie-out scorecard path for the ACFR preset, or None."""
    if project and project.lower() != "acfr":
        return None
    override = os.environ.get("WINGMAN_TIEOUT_SCORECARD")
    if override:
        p = pathlib.Path(override).expanduser()
        if p.is_file():
            return p.resolve()

    candidates: list[pathlib.Path] = []
    if working_dir:
        candidates.append(pathlib.Path(working_dir).expanduser() / DEFAULT_REL)
    candidates.append(client_preset.data_root() / "clients" / "riverton" / DEFAULT_REL)

    for p in candidates:
        if p.is_file():
            return p.resolve()
    return None


def load_scorecard(path: pathlib.Path | str) -> dict[str, Any]:
    p = pathlib.Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def scorecard_generated_at(scorecard: dict[str, Any]) -> str | None:
    """ISO timestamp from scorecard JSON (generated_at), or None."""
    raw = scorecard.get("generated_at") or scorecard.get("scorecard_generated_at")
    return str(raw).strip() if raw else None


def face_sheet(statement: str) -> str:
    return SHEET_ALIAS.get(statement, statement)


def _result_keys(entry: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Lookup keys for one scorecard results[] row."""
    stmt = entry.get("statement") or ""
    col = entry.get("column") or ""
    keys: list[tuple[str, str, str]] = []
    li = entry.get("line_item")
    if li:
        keys.append((_norm_text(stmt), _norm_text(li), _norm_text(col)))
    wb = entry.get("wb_label")
    if wb:
        keys.append((_norm_text(stmt), _norm_text(wb), _norm_text(col)))
    return keys


def build_scorecard_index(scorecard: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    idx: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in scorecard.get("results") or []:
        for key in _result_keys(entry):
            idx.setdefault(key, entry)
    return idx


def build_critic_index(scorecard: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    idx: dict[tuple[str, str, str], dict[str, Any]] = {}
    for c in scorecard.get("critic") or []:
        key = (
            _norm_text(c.get("statement") or ""),
            _norm_text(c.get("line_item") or ""),
            _norm_text(c.get("column") or ""),
        )
        idx[key] = c
    return idx


def detect_heuristic(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Classify a scorecard row using the scorecard critic rules + double-aggregation pattern.

    Returns {cause, confidence, notes}.
    """
    wb = float(entry.get("wb_value_thousands") or 0.0)
    pdf = float(entry.get("pdf_value_thousands") or 0.0)
    var = entry.get("variance_thousands")
    status = entry.get("status") or ""

    if var is None:
        return {"cause": CAUSE_UNKNOWN, "confidence": "low", "notes": "No variance computed."}

    abs_var = abs(float(var))

    # 2× aggregation: workbook Total ≈ 2× published (component counted twice).
    if pdf > 0 and abs(wb - 2 * pdf) <= max(1.0, abs(pdf) * 0.02):
        return {
            "cause": CAUSE_DOUBLE_AGG,
            "confidence": "high",
            "notes": (
                f"wb={wb:.0f}K ≈ 2×pdf={pdf:.0f}K — Total column may double-count "
                f"one activity column; compare Gov vs BTA components."
            ),
        }

    if status == "ZERO":
        return {
            "cause": CAUSE_TB_SOURCE_GAP,
            "confidence": "medium",
            "notes": (
                f"wb≈0 but PDF={pdf:.0f}K — GL likely absent from AcctMap or "
                f"tagged to wrong STMT_LINE."
            ),
        }

    if abs(wb) < 1.0 and abs(pdf) > 10.0:
        return {
            "cause": CAUSE_TB_SOURCE_GAP,
            "confidence": "medium",
            "notes": f"wb≈0 but PDF={pdf:.0f}K — check AcctMap routing.",
        }

    if abs(wb) > 1.0 and abs(abs_var - 2 * abs(wb)) < max(1.0, abs_var * 0.02):
        return {
            "cause": CAUSE_SIGN_FLIP,
            "confidence": "high",
            "notes": f"var({var:+.0f}K) ≈ 2×wb — check $A sign-column convention.",
        }

    if abs_var < 1.0:
        return {
            "cause": CAUSE_SCALING,
            "confidence": "high",
            "notes": f"|var|={abs_var:.3f}K — within THOUSANDS rounding tolerance.",
        }

    if abs_var > 50.0:
        return {
            "cause": CAUSE_RECLASS,
            "confidence": "low",
            "notes": (
                f"|var|={abs_var:.0f}K (>50K). Likely missing Adjustments entry — "
                f"check Adj sheet for STMT_LINE + fund."
            ),
        }

    return {
        "cause": CAUSE_UNKNOWN,
        "confidence": "low",
        "notes": f"wb={wb:.0f}K, pdf={pdf:.0f}K, var={var:+.0f}K — no heuristic matched.",
    }


def _match_row(
    row: dict[str, Any],
    index: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    stmt = _norm_text(row.get("stmt") or row.get("statement") or row.get("sheetName") or "")
    col = _norm_text(row.get("column") or "")
    label = _norm_text(row.get("label") or row.get("line_item") or "")

    if not stmt:
        return None

    # Direct keys from tieout_table or pre-enriched rows.
    for candidate in (label, _norm_text(row.get("line_item") or "")):
        if candidate:
            hit = index.get((stmt, candidate, col))
            if hit:
                return hit

    # Detail string fallback: "Stmt Col Label Δ=..."
    detail = row.get("detail") or ""
    if detail and not label:
        parts = detail.replace("Δ=", " ").split()
        if len(parts) >= 3:
            hit = index.get((_norm_text(parts[0]), _norm_text(parts[2]), _norm_text(parts[1])))
            if hit:
                return hit

    return None


def _attach_coords(row: dict[str, Any], entry: dict[str, Any], heuristic: dict[str, Any]) -> None:
    wb_row = entry.get("wb_row")
    wb_col = entry.get("wb_col")
    addr = cell_addr(wb_row, wb_col)
    stmt = entry.get("statement") or row.get("stmt") or ""
    sheet = face_sheet(stmt)

    row["wb_row"] = wb_row
    row["wb_col"] = wb_col
    row["statement"] = entry.get("statement") or stmt
    row["line_item"] = entry.get("line_item") or row.get("label")
    row["column"] = entry.get("column") or row.get("column")
    row["tieout_status"] = entry.get("status")
    row["calc"] = entry.get("wb_value_thousands")
    row["pub"] = entry.get("pdf_value_thousands")
    row["delta"] = entry.get("variance_thousands")

    if addr:
        row["addr"] = addr
        row["jumpHint"] = {
            "sheetName": sheet,
            "addr": addr,
            "wb_row": wb_row,
            "wb_col": wb_col,
        }

    row["tieout_heuristic"] = heuristic


def enrich_fail_row(
    row: dict[str, Any],
    *,
    index: dict[tuple[str, str, str], dict[str, Any]],
    critic_index: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Return enriched copy of a tieout FAIL row (no-op if no match)."""
    out = dict(row)
    if out.get("check") != "tieout":
        return out

    entry = _match_row(out, index)
    if entry is None:
        return out

    key = (
        _norm_text(entry.get("statement") or ""),
        _norm_text(entry.get("line_item") or ""),
        _norm_text(entry.get("column") or ""),
    )
    critic = critic_index.get(key)
    heuristic = detect_heuristic(entry)
    if critic:
        critic_h = {
            "cause": critic["cause"],
            "confidence": critic["confidence"],
            "notes": critic["notes"],
        }
        if heuristic["cause"] == CAUSE_DOUBLE_AGG and critic_h["cause"] in ("unknown", "reclass"):
            pass  # 2× aggregation is more actionable than generic reclass/unknown
        else:
            heuristic = critic_h
    _attach_coords(out, entry, heuristic)
    return out


def scorecard_fail_rows(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert scorecard FAIL + ZERO rows to checks_bridge fail_row shape."""
    rows: list[dict[str, Any]] = []
    critic_index = build_critic_index(scorecard)
    for entry in scorecard.get("results") or []:
        if entry.get("status") not in ("FAIL", "ZERO"):
            continue
        stmt = entry.get("statement") or ""
        col = entry.get("column") or ""
        label = entry.get("line_item") or entry.get("wb_label") or ""
        var = entry.get("variance_thousands")
        delta_s = f"{var:+,.0f}" if var is not None else "n/a"
        row: dict[str, Any] = {
            "check": "tieout",
            "raw_check": "tieout",
            "detail": f"{stmt} {col} {label} Δ={delta_s}",
            "source": "tieout_scorecard",
            "stmt": stmt,
            "statement": stmt,
            "column": col,
            "label": label,
            "line_item": entry.get("line_item"),
            "calc": entry.get("wb_value_thousands"),
            "pub": entry.get("pdf_value_thousands"),
            "delta": delta_s,
            "tieout_status": entry.get("status"),
        }
        key = (_norm_text(stmt), _norm_text(entry.get("line_item") or ""), _norm_text(col))
        critic = critic_index.get(key)
        heuristic = detect_heuristic(entry)
        if critic:
            critic_h = {
                "cause": critic["cause"],
                "confidence": critic["confidence"],
                "notes": critic["notes"],
            }
            if heuristic["cause"] == CAUSE_DOUBLE_AGG and critic_h["cause"] in ("unknown", "reclass"):
                pass
            else:
                heuristic = critic_h
        _attach_coords(row, entry, heuristic)
        rows.append(row)
    return rows


def _row_dedupe_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        _norm_text(row.get("statement") or row.get("stmt") or ""),
        _norm_text(row.get("line_item") or row.get("label") or ""),
        _norm_text(row.get("column") or ""),
    )


def merge_scorecard_fail_rows(
    fail_rows: list[dict[str, Any]],
    scorecard: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Enrich existing tieout rows from the scorecard index; append scorecard FAIL/ZERO not already present.
    """
    index = build_scorecard_index(scorecard)
    critic_index = build_critic_index(scorecard)

    seen = {_row_dedupe_key(r) for r in fail_rows if r.get("check") == "tieout"}
    out: list[dict[str, Any]] = []

    for row in fail_rows:
        if row.get("check") == "tieout":
            enriched = enrich_fail_row(row, index=index, critic_index=critic_index)
            seen.add(_row_dedupe_key(enriched))
            out.append(enriched)
        else:
            out.append(row)

    for sc_row in scorecard_fail_rows(scorecard):
        key = _row_dedupe_key(sc_row)
        if key in seen:
            continue
        seen.add(key)
        out.append(sc_row)

    return out


def enrich_fail_rows(
    fail_rows: list[dict[str, Any]],
    *,
    working_dir: str | None = None,
    project: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Enrich tieout fail rows from the tieout scorecard when project is acfr.

    Returns (rows, enrichment_meta) where meta is None if skipped.
    """
    path = resolve_scorecard_path(working_dir, project)
    if path is None:
        return fail_rows, None

    try:
        scorecard = load_scorecard(path)
    except (OSError, json.JSONDecodeError) as exc:
        return fail_rows, {"applied": False, "error": str(exc), "path": str(path)}

    merged = merge_scorecard_fail_rows(fail_rows, scorecard)
    fail_n = sum(1 for r in scorecard.get("results") or [] if r.get("status") == "FAIL")
    zero_n = sum(1 for r in scorecard.get("results") or [] if r.get("status") == "ZERO")
    meta = {
        "applied": True,
        "path": str(path),
        "scorecard_fail": fail_n,
        "scorecard_zero": zero_n,
        "enriched_count": sum(1 for r in merged if r.get("jumpHint")),
    }
    gen_at = scorecard_generated_at(scorecard)
    if gen_at:
        meta["scorecard_generated_at"] = gen_at
    return merged, meta
