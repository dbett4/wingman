#!/usr/bin/env python3
"""Prototype: merge cell.type from the content cells API (flag-gated in wk_client)."""
from __future__ import annotations

from wk_client import FORMULA_FETCH_CAP, a1, rc_from_a1


def type_from_content_value(val) -> str | None:
    """Return 'richText' | 'plainText' | 'formula' | None from a content API value object."""
    if val is None:
        return None
    if not isinstance(val, dict):
        return "plainText"
    if "richText" in val:
        return "richText"
    if val.get("type") == "formula" or "formula" in val:
        return "formula"
    return "plainText"


def _content_bbox(cells, max_cells=None):
    work = cells[:max_cells] if max_cells is not None and max_cells >= 0 else cells
    coords = []
    by_addr = {}
    for c in work:
        rc = rc_from_a1(c.get("addr"))
        if rc is None:
            continue
        by_addr[c["addr"]] = c
        coords.append(rc)
    if not coords:
        return work, by_addr, None
    min_r = min(r for r, _ in coords)
    max_r = max(r for r, _ in coords)
    min_c = min(c for _, c in coords)
    max_c = max(c for _, c in coords)
    return work, by_addr, (min_r, max_r, min_c, max_c)


def enrich_cells_with_types(
    cells,
    table_id,
    token,
    ctx,
    *,
    api_version="2026-01-01",
    max_cells=None,
    raw_cells=None,
):
    """
    Set cell['type'] from content API when absent. Returns (cells, enriched_count).
    When raw_cells is supplied (shared fetch with formula enrichment), skips the GET.
    """
    if not table_id or not cells:
        return cells, 0
    work, by_addr, bbox = _content_bbox(cells, max_cells)
    if not bbox:
        return cells, 0
    min_r, max_r, min_c, max_c = bbox
    if raw_cells is None:
        from wk_client import _get

        path = (
            f"/content/tables/{table_id}/cells"
            f"?startRow={min_r}&stopRow={max_r}&startColumn={min_c}&stopColumn={max_c}"
        )
        try:
            raw = _get(path, token, ctx, version=api_version)
        except Exception:
            return cells, 0
        rows = raw.get("data", [])
    else:
        rows = raw_cells
    if not isinstance(rows, list):
        return cells, 0
    enriched = 0
    for ri, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        rc = row.get("cells")
        if not isinstance(rc, list):
            continue
        for ci, cell in enumerate(rc):
            if not isinstance(cell, dict):
                continue
            addr = a1(min_r + ri, min_c + ci)
            norm = by_addr.get(addr)
            if not norm or norm.get("type"):
                continue
            t = type_from_content_value(cell.get("value"))
            if t:
                norm["type"] = t
                enriched += 1
    return cells, enriched


def build_type_fetch_meta(*, enabled, cell_count, enriched_count, table_id_present):
    cap = FORMULA_FETCH_CAP
    if not enabled:
        return {
            "enabled": False,
            "partial": True,
            "reason": (
                "WINGMAN_TYPE_FETCH off — sheetdata has no cell.type; "
                "contrast lane stays surfaced until types are known"
            ),
        }
    if not table_id_present:
        return {
            "enabled": True,
            "partial": True,
            "reason": "No content table id — type enrichment skipped",
            "cell_count": cell_count,
        }
    capped = cell_count > cap
    meta = {
        "enabled": True,
        "partial": capped,
        "cell_count": cell_count,
        "enriched_count": enriched_count,
        "cap": cap,
    }
    if capped:
        meta["reason"] = f"Type fetch capped at {cap} cells ({cell_count} scanned)"
    return meta
