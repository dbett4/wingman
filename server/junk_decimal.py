#!/usr/bin/env python3
"""
Junk-decimal Wingman detector (Lane E).

Absurd valueFormat.precision (formatting_audit pattern —
`0.#{6,}` Excel formats → high precision.value in Workiva). Safe-auto copies a
column neighbor format when 2-of-3 neighbors agree on a non-junk format.
"""
from __future__ import annotations

import re
from collections import Counter

import detectors

# Junk when Excel format has 6+ `#` after decimal; API maps to high value.
JUNK_PRECISION_MIN = 5  # precision.value > 4 (auto:false)

_ADDR = re.compile(r"^([A-Z]+)([0-9]+)$", re.I)
_NEIGHBOR_DELTAS = (-1, 1, -2, 2, -3, 3)


def _parse_addr(addr: str) -> tuple[str, int] | None:
    m = _ADDR.match((addr or "").strip().upper())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def is_absurd_precision(vf: dict | None) -> bool:
    """True when valueFormat.precision is the junk-decimal pattern."""
    if not vf:
        return False
    prec = vf.get("precision")
    if not isinstance(prec, dict):
        return False
    if prec.get("auto") is True:
        return False
    val = prec.get("value")
    if val is None:
        return False
    try:
        return int(val) >= JUNK_PRECISION_MIN
    except (TypeError, ValueError):
        return False


def _vf_signature(vf: dict | None) -> tuple | None:
    if not vf:
        return None
    prec = vf.get("precision") or {}
    return (
        (vf.get("valueFormatType") or "").upper(),
        prec.get("auto"),
        prec.get("value"),
        vf.get("useParensForNegatives"),
        vf.get("showThousandsSeparator"),
        vf.get("prefix"),
    )


def neighbor_consensus_format(addr: str, cells_by_addr: dict[str, dict]) -> dict | None:
    """
    Style-lock pattern: copy valueFormat from column neighbors with sane precision.
    Requires 2-of-3 sampled neighbors to share the same non-junk format signature.
    """
    parsed = _parse_addr(addr)
    if not parsed:
        return None
    col, row = parsed
    neighbors: list[dict] = []
    for dr in _NEIGHBOR_DELTAS:
        naddr = f"{col}{row + dr}"
        cell = cells_by_addr.get(naddr)
        if not cell:
            continue
        vf = cell.get("valueFormat") or {}
        if is_absurd_precision(vf):
            continue
        if vf:
            neighbors.append(vf)
        if len(neighbors) >= 3:
            break
    if len(neighbors) < 2:
        return None
    sigs = Counter(_vf_signature(vf) for vf in neighbors)
    best_sig, count = sigs.most_common(1)[0]
    if count < 2 or best_sig is None:
        return None
    for vf in neighbors:
        if _vf_signature(vf) == best_sig:
            return dict(vf)
    return None


def detect_junk_decimal(cell: dict) -> dict | None:
    """
    Flag numeric cells with absurd precision.value. Skips richText, linked, merged, TEXT format.
    fix_lane safe-auto when neighbor consensus exists; otherwise surfaced.
    """
    vf = cell.get("valueFormat") or {}
    if not is_absurd_precision(vf):
        return None
    if cell.get("type") == "richText":
        return None
    if cell.get("isLinked"):
        return None
    if cell.get("isMerged"):
        return None
    if (vf.get("valueFormatType") or "").upper() == "TEXT":
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    # Junk DECIMAL precision only matters on a number; skip blanks and non-numeric text
    # (a non-empty non-numeric cell used to slip through the old compound guard).
    if detectors._parse_numeric(display) is None:
        return None
    prec = vf.get("precision") or {}
    detail = f"junk precision ({prec.get('value', '?')} decimals, {detectors._format_desc(vf)})"
    return {
        "kind": "junk-decimal",
        "addr": cell["addr"],
        "severity": "medium",
        "detail": detail,
        "fixable": True,
        "fix_lane": "safe-auto",
        "target": None,  # filled by adjust_junk_decimal_lanes
    }


def adjust_junk_decimal_lanes(findings: list[dict], cells: list[dict]) -> list[dict]:
    """Downgrade to surfaced when column neighbors lack 2-of-3 format consensus."""
    cells_by_addr = {c["addr"]: c for c in (cells or []) if c.get("addr")}
    out: list[dict] = []
    for f in findings:
        if f.get("kind") != "junk-decimal":
            out.append(f)
            continue
        addr = f.get("addr")
        target_vf = neighbor_consensus_format(addr, cells_by_addr) if addr else None
        if not target_vf:
            g = dict(f)
            g["fix_lane"] = "surfaced"
            g["fixable"] = False
            g["detail"] = g["detail"] + " (no neighbor format consensus)"
            g["target"] = None
            out.append(g)
            continue
        cell = cells_by_addr.get(addr) or {}
        before_vf = cell.get("valueFormat") or {}
        g = dict(f)
        g["target"] = {
            "valueFormat": target_vf,
            "beforeFormat": detectors._format_desc(before_vf),
            "afterFormat": detectors._format_desc(target_vf),
        }
        out.append(g)
    return out


def scan_junk_decimal(cells: list[dict]) -> list[dict]:
    """Run junk-decimal detector + neighbor gate over normalized cells."""
    raw = []
    for cell in cells:
        f = detect_junk_decimal(cell)
        if f:
            raw.append(f)
    return adjust_junk_decimal_lanes(raw, cells)
