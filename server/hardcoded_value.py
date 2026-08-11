#!/usr/bin/env python3
"""
Hardcoded face-value detector (Wingman ACFR issue family 2 -- surfaced/RED only).

Flags numeric cells with no formula in a column where >=2 sampled numeric
neighbors ARE formula-driven. Common in ACFR statement faces when an amount
was hand-entered instead of pulled from the TB/AcctMap SUMIFS layer -- it will
drift when the trial balance updates.

Requires formula enrichment on the cell dict (formula key present and non-None
for formula cells). Without enrichment all cells look formula-free, so the column
gate (formula_count < 2) suppresses all findings. The detector is noise-free
by design: it refuses to fire when it cannot prove the column expects formulas.

Pure logic -- no network -- unit-tested without Workiva credentials.
"""
from __future__ import annotations

import re
from typing import Any

_ADDR = re.compile(r"^([A-Z]+)([0-9]+)$", re.I)
# Walk up and down 3 rows (6 neighbors max).
_NEIGHBOR_DELTAS = (-1, 1, -2, 2, -3, 3)
# Minimum formula-having neighbors before we consider the column formula-driven.
_MIN_FORMULA_NEIGHBORS = 2


def _parse_addr(addr: str):
    m = _ADDR.match((addr or "").strip().upper())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _is_numeric_value(val: Any) -> bool:
    """True when the display value looks like a formatted number (not a label or blank)."""
    s = str(val or "").strip()
    if not s or s in ("-", "—", ""):
        return False
    # Strip common numeric formatting before float parse.
    clean = s.replace(",", "").replace("$", "").lstrip("(").rstrip(")")
    if s.startswith("(") and s.endswith(")"):
        clean = "-" + clean.lstrip("-")
    try:
        float(clean)
        return True
    except ValueError:
        return False


def _has_formula(cell: dict) -> bool:
    """True when the normalized cell dict carries a formula string."""
    f = cell.get("formula")
    if f and isinstance(f, str) and f.strip().startswith("="):
        return True
    # Some enrichment paths store the formula in value.
    v = cell.get("value") or ""
    return isinstance(v, str) and v.strip().startswith("=")


def column_formula_density(addr: str, cells_by_addr: dict) -> dict:
    """
    Sample up to 6 numeric column neighbors.

    Returns dict with formula_count, total, density.
    Only numeric-valued cells count toward the denominator (skips blank/label rows).
    """
    parsed = _parse_addr(addr)
    if not parsed:
        return {"formula_count": 0, "total": 0, "density": 0.0}
    col, row = parsed

    formula_n = 0
    total = 0
    for dr in _NEIGHBOR_DELTAS:
        naddr = "{}{}".format(col, row + dr)
        cell = cells_by_addr.get(naddr)
        if not cell:
            continue
        cv = cell.get("calculatedValue") or cell.get("value") or ""
        if not _is_numeric_value(cv):
            continue  # skip labels, blank rows
        total += 1
        if _has_formula(cell):
            formula_n += 1
        if total >= 6:
            break

    density = (formula_n / total) if total > 0 else 0.0
    return {"formula_count": formula_n, "total": total, "density": density}


def detect_hardcoded_face_value(cell: dict, *, cells_by_addr: dict = None,
                                any_fetched: bool | None = None) -> dict | None:
    """
    Flag a numeric cell with no formula when column neighbors are formula-driven.

    Gate summary (all must be true to fire):
      1. Not richText (richText cells may carry partial run structure)
      2. Not linked (linked = source-controlled, not hardcoded)
      3. Not merged (merged header cells are layout, not data)
      4. Numeric calculatedValue or value
      5. No formula present on this cell
      6. valueFormatType is not TEXT (TEXT format = intentional string data-entry cell)
      7. Column gate: >=2 sampled numeric neighbors have formulas (proves column expects formulas)
      8. cells_by_addr context must be provided (no context -> skip, not noise)
      9. Cap-boundary guard: when the scan carries formula_fetched markers (partial formula
         enrichment past WINGMAN_FORMULA_FETCH_CAP), trust only a cell whose formula was
         actually fetched — a formula cell beyond the cap has formula=None and would otherwise
         look like a literal. Inert when no cell carries the marker (no false negatives off it).
    """
    if cell.get("type") == "richText":
        return None
    if cell.get("isLinked"):
        return None
    if cell.get("isMerged"):
        return None

    cv = cell.get("calculatedValue") or cell.get("value") or ""
    if not _is_numeric_value(cv):
        return None

    if _has_formula(cell):
        return None

    vf = cell.get("valueFormat") or {}
    if (vf.get("valueFormatType") or "").upper() == "TEXT":
        return None

    if not cells_by_addr:
        return None

    # cap-boundary guard (backward-compatible: inert unless markers are present)
    fetched_present = (
        any_fetched if any_fetched is not None
        else any(c.get("formula_fetched") for c in cells_by_addr.values())
    )
    if fetched_present and not cell.get("formula_fetched"):
        return None

    addr = cell.get("addr", "")
    density = column_formula_density(addr, cells_by_addr)
    if density["formula_count"] < _MIN_FORMULA_NEIGHBORS:
        return None

    return {
        "kind": "hardcoded-face-value",
        "addr": addr,
        "severity": "high",
        "detail": (
            "numeric value with no formula "
            "({}/{} neighbors formula-driven)".format(
                density["formula_count"], density["total"]
            )
        ),
        "fixable": False,
        "fix_lane": "surfaced",
        "target": {
            "density": round(density["density"], 2),
            "formula_count": density["formula_count"],
            "neighbor_total": density["total"],
        },
    }


def scan_hardcoded_values(cells: list) -> list:
    """
    Run the hardcoded-value detector over a full cell list.

    Builds cells_by_addr internally so callers (scan_cells) don't need to.
    Returns findings list (empty when formula enrichment is not active -- the column
    gate suppresses all findings when no neighbors have formulas).
    """
    cells_by_addr = {c["addr"]: c for c in cells if c.get("addr")}
    any_fetched = any(c.get("formula_fetched") for c in cells_by_addr.values())
    out = []
    for cell in cells:
        f = detect_hardcoded_face_value(cell, cells_by_addr=cells_by_addr, any_fetched=any_fetched)
        if f:
            out.append(f)
    return out
