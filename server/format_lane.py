#!/usr/bin/env python3
"""
Narrow valueFormat safe-auto detectors (ADR-0002 / ADR-0004).

Each kind fires only when column neighbors agree (2-of-3 consensus), avoiding the
naive format-consistency noise ADR-0004 rejected. Fixes copy neighbor valueFormat via
the shared fixer format-copy path (TEXT() preflight, readback/revert).
"""
from __future__ import annotations

from collections import Counter

import detectors
import junk_decimal as jd

_NUMERIC_TYPES = frozenset({"NUMBER", "AUTOMATIC", ""})
_ACCOUNTING = "ACCOUNTING"
_SKIP_PREFIX_VFT = frozenset({"PERCENT", "CURRENCY", "TEXT"})
_ZERO_EM_DASH = "EM DASH"


def _numeric_cell_guards(cell: dict) -> bool:
    """True when cell is eligible for a valueFormat safe-auto write."""
    if cell.get("type") == "richText":
        return False
    if cell.get("isLinked"):
        return False
    if cell.get("isMerged"):
        return False
    vf = cell.get("valueFormat") or {}
    vft = (vf.get("valueFormatType") or "").upper()
    if vft in ("TEXT", "PERIOD"):
        return False
    # The dedicated year detector owns AUTOMATIC 4-digit years and supplies the
    # canonical PERIOD target. Do not let neighboring amount formats compete with it.
    if detectors.detect_year_automatic_coercion(cell) is not None:
        return False
    return True


def _neighbor_formats(addr: str, cells_by_addr: dict[str, dict], *, predicate) -> list[dict]:
    parsed = jd._parse_addr(addr)
    if not parsed:
        return []
    col, row = parsed
    out: list[dict] = []
    for dr in jd._NEIGHBOR_DELTAS:
        naddr = f"{col}{row + dr}"
        cell = cells_by_addr.get(naddr)
        if not cell:
            continue
        vf = cell.get("valueFormat") or {}
        if not vf or not predicate(vf, cell):
            continue
        out.append(vf)
        if len(out) >= 3:
            break
    return out


def _consensus_from_neighbors(neighbors: list[dict]) -> dict | None:
    if len(neighbors) < 2:
        return None
    sigs = Counter(jd._vf_signature(vf) for vf in neighbors)
    best_sig, count = sigs.most_common(1)[0]
    if count < 2 or best_sig is None:
        return None
    for vf in neighbors:
        if jd._vf_signature(vf) == best_sig:
            return dict(vf)
    return None


def neighbor_thousands_consensus(addr: str, cells_by_addr: dict[str, dict]) -> dict | None:
    """2-of-3 column neighbors with showThousandsSeparator true and sane precision."""

    def ok(vf: dict, _cell: dict) -> bool:
        if jd.is_absurd_precision(vf):
            return False
        return vf.get("showThousandsSeparator") is True

    return _consensus_from_neighbors(_neighbor_formats(addr, cells_by_addr, predicate=ok))


def neighbor_accounting_consensus(addr: str, cells_by_addr: dict[str, dict]) -> dict | None:
    """2-of-3 column neighbors on ACCOUNTING with matching parens setting."""

    def ok(vf: dict, _cell: dict) -> bool:
        if (vf.get("valueFormatType") or "").upper() != _ACCOUNTING:
            return False
        if jd.is_absurd_precision(vf):
            return False
        return True

    return _consensus_from_neighbors(_neighbor_formats(addr, cells_by_addr, predicate=ok))


def _shows_literal_zero(display) -> bool:
    """True when a zero numeric value renders as literal 0 (not em-dash)."""
    s = str(display or "").strip()
    if s in ("—", "–", "-", ""):
        return False
    num = detectors._parse_numeric(display)
    if num != 0:
        return False
    return s in ("0", "0.0", "$0", "$0.00") or s.replace(",", "").replace("$", "").strip() in ("0", "0.0")


def neighbor_prefix_consensus(addr: str, cells_by_addr: dict[str, dict]) -> dict | None:
    """2-of-3 column neighbors sharing the same non-empty prefix (skip PERCENT/CURRENCY)."""

    def ok(vf: dict, _cell: dict) -> bool:
        vft = (vf.get("valueFormatType") or "").upper()
        if vft in _SKIP_PREFIX_VFT:
            return False
        if jd.is_absurd_precision(vf):
            return False
        return bool(vf.get("prefix"))

    neighbors = _neighbor_formats(addr, cells_by_addr, predicate=ok)
    if len(neighbors) < 2:
        return None
    prefixes = Counter(vf.get("prefix") for vf in neighbors)
    best_prefix, count = prefixes.most_common(1)[0]
    if count < 2 or not best_prefix:
        return None
    for vf in neighbors:
        if vf.get("prefix") == best_prefix:
            return dict(vf)
    return None


def neighbor_zero_display_consensus(addr: str, cells_by_addr: dict[str, dict]) -> dict | None:
    """2-of-3 ACCOUNTING neighbors with displayZeroAs EM DASH."""

    def ok(vf: dict, _cell: dict) -> bool:
        if (vf.get("valueFormatType") or "").upper() != _ACCOUNTING:
            return False
        if vf.get("displayZeroAs") != _ZERO_EM_DASH:
            return False
        if jd.is_absurd_precision(vf):
            return False
        return True

    return _consensus_from_neighbors(_neighbor_formats(addr, cells_by_addr, predicate=ok))


def neighbor_precision_zero_consensus(addr: str, cells_by_addr: dict[str, dict]) -> dict | None:
    """2-of-3 neighbors with integer precision (auto:false, value:0), non-junk."""

    def ok(vf: dict, _cell: dict) -> bool:
        if jd.is_absurd_precision(vf):
            return False
        prec = vf.get("precision") or {}
        return prec.get("auto") is False and prec.get("value") == 0

    return _consensus_from_neighbors(_neighbor_formats(addr, cells_by_addr, predicate=ok))


def _apply_lane_target(finding: dict, cells: list[dict], resolver) -> dict:
    cells_by_addr = {c["addr"]: c for c in (cells or []) if c.get("addr")}
    addr = finding.get("addr")
    target_vf = resolver(addr, cells_by_addr) if addr else None
    if not target_vf:
        g = dict(finding)
        g["fix_lane"] = "surfaced"
        g["fixable"] = False
        g["detail"] = g["detail"] + " (no neighbor format consensus)"
        g["target"] = None
        return g
    cell = cells_by_addr.get(addr) or {}
    before_vf = cell.get("valueFormat") or {}
    g = dict(finding)
    g["target"] = {
        "valueFormat": target_vf,
        "beforeFormat": detectors._format_desc(before_vf),
        "afterFormat": detectors._format_desc(target_vf),
    }
    return g


def detect_missing_thousands(cell: dict) -> dict | None:
    """Numeric cell >= 1000 without thousands separator when column neighbors use commas."""
    if not _numeric_cell_guards(cell):
        return None
    vf = cell.get("valueFormat") or {}
    if vf.get("showThousandsSeparator") is True:
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    num = detectors._parse_numeric(display)
    if num is None or abs(num) < 1000:
        return None
    s = str(display).strip()
    if "," in s:
        return None
    return {
        "kind": "missing-thousands-separator",
        "addr": cell["addr"],
        "severity": "medium",
        "detail": f"no thousands separator ({detectors._format_desc(vf)})",
        "fixable": True,
        "fix_lane": "safe-auto",
        "target": None,
    }


def detect_number_on_accounting_column(cell: dict) -> dict | None:
    """NUMBER/AUTOMATIC numeric cell in a column where neighbors use ACCOUNTING."""
    if not _numeric_cell_guards(cell):
        return None
    vf = cell.get("valueFormat") or {}
    vft = (vf.get("valueFormatType") or "AUTOMATIC").upper()
    if vft not in _NUMERIC_TYPES:
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    if detectors._parse_numeric(display) is None:
        return None
    return {
        "kind": "number-on-accounting-column",
        "addr": cell["addr"],
        "severity": "medium",
        "detail": f"NUMBER format in ACCOUNTING column ({detectors._format_desc(vf)})",
        "fixable": True,
        "fix_lane": "safe-auto",
        "target": None,
    }


def detect_prefix_mismatch(cell: dict) -> dict | None:
    """Numeric cell missing $ (or other) prefix when column neighbors share the same prefix."""
    if not _numeric_cell_guards(cell):
        return None
    vf = cell.get("valueFormat") or {}
    vft = (vf.get("valueFormatType") or "AUTOMATIC").upper()
    if vft in _SKIP_PREFIX_VFT:
        return None
    if vf.get("prefix"):
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    if detectors._parse_numeric(display) is None:
        return None
    return {
        "kind": "prefix-mismatch",
        "addr": cell["addr"],
        "severity": "medium",
        "detail": f"missing prefix ({detectors._format_desc(vf)})",
        "fixable": True,
        "fix_lane": "safe-auto",
        "target": None,
    }


def detect_zero_display_mismatch(cell: dict) -> dict | None:
    """Zero renders as literal 0 when ACCOUNTING column neighbors use em-dash zero display."""
    if not _numeric_cell_guards(cell):
        return None
    vf = cell.get("valueFormat") or {}
    vft = (vf.get("valueFormatType") or "AUTOMATIC").upper()
    if vft in _SKIP_PREFIX_VFT:
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    num = detectors._parse_numeric(display)
    if num != 0:
        return None
    if vf.get("displayZeroAs") == _ZERO_EM_DASH:
        return None  # sheetdata may still carry "0"; export/UI already uses em-dash
    if not _shows_literal_zero(display):
        return None
    return {
        "kind": "zero-display-mismatch",
        "addr": cell["addr"],
        "severity": "low",
        "detail": f"zero shows 0 not em-dash ({detectors._format_desc(vf)})",
        "fixable": True,
        "fix_lane": "safe-auto",
        "target": None,
    }


def detect_precision_mismatch(cell: dict) -> dict | None:
    """Non-junk fixed decimal precision when column neighbors use integer precision."""
    if not _numeric_cell_guards(cell):
        return None
    vf = cell.get("valueFormat") or {}
    prec = vf.get("precision") or {}
    if prec.get("auto") is not False:
        return None
    try:
        pval = int(prec.get("value"))
    except (TypeError, ValueError):
        return None
    if pval <= 0 or pval >= jd.JUNK_PRECISION_MIN:
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    if detectors._parse_numeric(display) is None:
        return None
    return {
        "kind": "precision-mismatch",
        "addr": cell["addr"],
        "severity": "low",
        "detail": f"decimal precision {pval} vs column integer ({detectors._format_desc(vf)})",
        "fixable": True,
        "fix_lane": "safe-auto",
        "target": None,
    }


# Surfaced format kinds eligible for Wave B gated column apply (user-confirmed valueFormat paste).
GATED_FORMAT_KINDS = frozenset({
    "junk-decimal",
    "missing-thousands-separator",
    "number-on-accounting-column",
    "precision-mismatch",
    "prefix-mismatch",
    "zero-display-mismatch",
    "negative-without-parens",
})

# Format-consistency kinds that FIRE BROADLY and only become a real defect when the column
# establishes a house style the cell violates. When neither per-cell neighbor consensus nor a
# column-majority (gated) format exists, the finding carries no evidence and no action — it is
# the "(no neighbor format consensus)" noise ADR-0004 deliberately designed out ("a scan that
# cries wolf is worse than a narrower one that is trusted"). negative-without-parens is excluded:
# its surfaced case ("mixed column") is itself informative evidence (ADR-0005), not no-evidence noise.
NO_EVIDENCE_FORMAT_KINDS = frozenset(GATED_FORMAT_KINDS - {"negative-without-parens"})


def is_no_evidence_format_group(group: dict) -> bool:
    """
    True when a grouped format finding is pure noise: a broadly-firing format-consistency kind
    that has no per-cell safe-auto fix AND no gated column target. Call AFTER
    attach_gated_format_targets so the gated check is meaningful.
    """
    if group.get("kind") not in NO_EVIDENCE_FORMAT_KINDS:
        return False
    if group.get("fixable") or group.get("fix_lane") == "safe-auto":
        return False  # has a per-cell consensus fix
    if group.get("gated_target") or group.get("gated_columns"):
        return False  # has a column-majority gated fix
    return True


def drop_no_evidence_format_groups(groups: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Partition grouped findings into (kept, dropped). Dropped = no-evidence format noise.
    Everything else — every other detector, every fixable or gated format finding — is kept.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for g in groups:
        (dropped if is_no_evidence_format_group(g) else kept).append(g)
    return kept, dropped


def addr_column(addr: str) -> str | None:
    """Column letters from A1-style address."""
    parsed = jd._parse_addr(addr or "")
    return parsed[0] if parsed else None


def column_majority_format(
    col: str,
    cells_by_addr: dict[str, dict],
    *,
    exclude_addrs: set[str] | None = None,
) -> dict | None:
    """
    Majority non-junk valueFormat among numeric cells in a column (excluding flagged addrs).
    Used for gated column format paste when per-cell neighbor consensus failed at scan.
    """
    exclude = exclude_addrs or set()
    sigs: Counter = Counter()
    vf_by_sig: dict = {}
    for addr, cell in cells_by_addr.items():
        parsed = jd._parse_addr(addr)
        if not parsed or parsed[0] != col:
            continue
        if addr in exclude:
            continue
        if not _numeric_cell_guards(cell):
            continue
        vf = cell.get("valueFormat") or {}
        if jd.is_absurd_precision(vf):
            continue
        display = cell.get("calculatedValue") or cell.get("value") or ""
        if detectors._parse_numeric(display) is None:
            continue
        sig = jd._vf_signature(vf)
        if sig is None:
            continue
        sigs[sig] += 1
        vf_by_sig[sig] = vf
    if not sigs:
        return None
    best_sig, count = sigs.most_common(1)[0]
    total = sum(sigs.values())
    if count < 2 and total < 2:
        return None
    if count < 2 and count <= total / 2:
        return None
    return dict(vf_by_sig[best_sig])


def attach_gated_format_targets(groups: list[dict], cells: list[dict]) -> None:
    """Attach gated_target / gated_columns on surfaced format groups for panel column apply."""
    cells_by_addr = {c["addr"]: c for c in (cells or []) if c.get("addr")}
    for g in groups:
        if g.get("fix_lane") != "surfaced":
            continue
        if g.get("kind") not in GATED_FORMAT_KINDS:
            continue
        addrs = list(g.get("addrs") or [])
        if not addrs:
            continue
        exclude = set(addrs)
        by_col: dict[str, list[str]] = {}
        for a in addrs:
            col = addr_column(a)
            if col:
                by_col.setdefault(col, []).append(a)
        gated_by_col: dict[str, dict] = {}
        for col, col_addrs in by_col.items():
            vf = column_majority_format(col, cells_by_addr, exclude_addrs=exclude)
            if not vf:
                continue
            gated_by_col[col] = {
                "valueFormat": vf,
                "afterFormat": detectors._format_desc(vf),
                "column": col,
                "addrCount": len(col_addrs),
            }
        if not gated_by_col:
            continue
        if len(gated_by_col) == 1:
            col = next(iter(gated_by_col))
            entry = gated_by_col[col]
            g["gated_target"] = {
                "valueFormat": entry["valueFormat"],
                "afterFormat": entry["afterFormat"],
                "column": col,
            }
        else:
            g["gated_columns"] = gated_by_col


def adjust_format_lane_findings(findings: list[dict], cells: list[dict]) -> list[dict]:
    resolvers = {
        "missing-thousands-separator": neighbor_thousands_consensus,
        "number-on-accounting-column": neighbor_accounting_consensus,
        "precision-mismatch": neighbor_precision_zero_consensus,
        "prefix-mismatch": neighbor_prefix_consensus,
        "zero-display-mismatch": neighbor_zero_display_consensus,
    }
    out: list[dict] = []
    for f in findings:
        resolver = resolvers.get(f.get("kind"))
        if not resolver:
            out.append(f)
            continue
        out.append(_apply_lane_target(f, cells, resolver))
    return out


def scan_format_lane(cells: list[dict]) -> list[dict]:
    raw: list[dict] = []
    for cell in cells:
        for det in (
            detect_missing_thousands,
            detect_number_on_accounting_column,
            detect_precision_mismatch,
            detect_prefix_mismatch,
            detect_zero_display_mismatch,
        ):
            hit = det(cell)
            if hit:
                raw.append(hit)
    return adjust_format_lane_findings(raw, cells)
