#!/usr/bin/env python3
"""Vision crop candidate selection — addresses for pixel capture beyond queue-only flags."""
from __future__ import annotations

import detectors
from wk_client import rc_from_a1


def _addr_sort_key(addr: str):
    rc = rc_from_a1(addr)
    if rc is None:
        return (999999, 999999, addr or "")
    return (rc[0], rc[1], addr or "")


def _cell_text(cell: dict) -> str:
    cv = cell.get("calculatedValue")
    if cv is not None and str(cv).strip() != "":
        return str(cv).strip()
    v = cell.get("value")
    if v is not None and str(v).strip() != "":
        return str(v).strip()
    return ""


def _is_nonempty(cell: dict) -> bool:
    return bool(_cell_text(cell)) or bool(cell.get("formula"))


def _is_label_probe(cell: dict) -> bool:
    if cell.get("formula"):
        return False
    text = _cell_text(cell)
    if not text:
        return False
    if detectors.detect_low_contrast(cell) is not None:
        return False
    if detectors._parse_numeric(text) is not None:
        return False
    return True


def build_vision_candidates(
    cells: list[dict],
    flagged_addrs: set[str] | list[str],
    *,
    cap: int = 80,
) -> list[str]:
    """
    Return up to `cap` A1 addresses for vision capture.

    Tier 1: flagged (from scan queue)
    Tier 2: label probes — text cells where API contrast passes (clipped / run-level candidates)
    Tier 3: remaining non-empty cells
    """
    cap = max(0, int(cap))
    if cap == 0:
        return []

    flagged_set = {str(a).strip().upper() for a in (flagged_addrs or []) if a}
    flagged_ordered = sorted(flagged_set, key=_addr_sort_key)

    cells_by_addr: dict[str, dict] = {}
    for c in cells or []:
        addr = (c.get("addr") or "").strip().upper()
        if addr:
            cells_by_addr[addr] = c

    out: list[str] = []
    seen: set[str] = set()

    def push(addr: str | None) -> bool:
        if not addr or len(out) >= cap:
            return len(out) < cap
        a = addr.strip().upper()
        if not a or a in seen:
            return len(out) < cap
        seen.add(a)
        out.append(a)
        return len(out) < cap

    for addr in flagged_ordered:
        if not push(addr):
            return out

    label_probes = sorted(
        (addr for addr, cell in cells_by_addr.items()
         if addr not in seen and _is_label_probe(cell)),
        key=_addr_sort_key,
    )
    for addr in label_probes:
        if not push(addr):
            return out

    remainder = sorted(
        (addr for addr, cell in cells_by_addr.items()
         if addr not in seen and _is_nonempty(cell)),
        key=_addr_sort_key,
    )
    for addr in remainder:
        if not push(addr):
            break

    return out
