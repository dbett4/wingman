#!/usr/bin/env python3
"""
Wingman activity log — the data substrate for automatic tool improvement.

Every scan and every fix outcome is appended as one JSON line to a daily log under
WINGMAN_LOG_DIR (default ~/.wingman/logs). `wingman_log_digest.py` mines these lines
into an improvement digest: which detectors are noisy, which fixes get reverted/refused
(false-positive signal), drift, and vision's net contribution.

PII boundary: the log NEVER stores cell content —
no cell values, no formula text, no detail strings. Only counts by kind/lane/severity,
fix STATUS, A1 coordinates, timings, and a pseudonymous sheet hash (SHA-256 of
spreadsheetId:sheetId, truncated) so per-sheet trends are traceable without exposing
client identifiers. The log lives OUTSIDE the repo by default; never commit it.

Failsafe by contract: every public function swallows its own errors. Logging must never
break a scan or a fix.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# Content-bearing keys that must NEVER reach the log. log_event strips these recursively so the
# PII guarantee is structural — enforced at the write choke point, not just by the discipline of
# scan_summary/fix_summary. Case-insensitive. `addr` (a single A1 coordinate) is intentionally
# allowed; `addrs` (bulk coordinate lists) is not.
_FORBIDDEN_KEYS = frozenset({
    "value", "values", "cellvalues", "calculatedvalue", "displayvalue",
    "detail", "formula", "formulatext", "formulaexcerpt",
    "target", "before", "after", "expected", "readback",
    "groups", "cells", "addrs", "payload",
})


def _strip_forbidden(obj):
    """Recursively drop any content-bearing key (see _FORBIDDEN_KEYS). Preserves safe scalars,
    safe keys (incl. `addr`), and structure. The last line of PII defense before disk."""
    if isinstance(obj, dict):
        return {k: _strip_forbidden(v) for k, v in obj.items() if str(k).lower() not in _FORBIDDEN_KEYS}
    if isinstance(obj, list):
        return [_strip_forbidden(v) for v in obj]
    return obj


def log_dir() -> Path:
    return Path(os.environ.get("WINGMAN_LOG_DIR", str(Path.home() / ".wingman" / "logs")))


def _sheet_hash(spreadsheet_id, sheet_id) -> str:
    """Pseudonymous, stable per-sheet key — no raw client IDs leave the machine."""
    raw = f"{spreadsheet_id or ''}:{sheet_id or ''}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _counts_by(groups, key):
    out: dict[str, int] = {}
    for g in groups or []:
        v = str(g.get(key) or "unknown")
        # a group bundles N cells; count cells so volume reflects real defect count
        out[v] = out.get(v, 0) + int(g.get("count") or len(g.get("addrs") or []) or 1)
    return out


def scan_summary(payload: dict, *, spreadsheet_id=None, sheet_id=None) -> dict:
    """PII-safe aggregate of a scan payload. No cell values/details ever included."""
    groups = (payload or {}).get("groups") or []
    vision = (payload or {}).get("vision") or {}
    ff = (payload or {}).get("formula_fetch") or {}
    return {
        "event": "scan",
        "groupCount": len(groups),
        "findingCount": sum(int(g.get("count") or len(g.get("addrs") or []) or 1) for g in groups),
        "byKind": _counts_by(groups, "kind"),
        "byLane": _counts_by(groups, "fix_lane"),
        "bySeverity": _counts_by(groups, "severity"),
        "cellCount": (payload or {}).get("cellCount"),
        "truncated": bool((payload or {}).get("truncated")),
        "visionApplied": bool(vision.get("applied")),
        "visionNetNew": vision.get("netNew"),
        "formulaFetchEnabled": bool(ff.get("enabled")) if ff else None,
        "sheet": _sheet_hash(spreadsheet_id, sheet_id),
    }


def fix_summary(kind: str, confirm: bool, result: dict, *, addr=None) -> dict:
    """PII-safe fix outcome. STATUS is the signal; no cell values are stored."""
    return {
        "event": "fix",
        "kind": kind,
        "phase": "apply" if confirm else "dry-run",
        "status": (result or {}).get("status", "unknown"),
        "addr": addr or (result or {}).get("addr"),
    }


def log_event(record: dict) -> None:
    """Append one record as a JSON line to today's log. Never raises."""
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        record = _strip_forbidden({"ts": now.isoformat(), "schema": SCHEMA_VERSION, **record})
        path = d / f"wingman-events-{now.strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging is best-effort; never break the caller


def log_scan(spreadsheet_id, sheet_id, payload: dict) -> None:
    try:
        log_event(scan_summary(payload, spreadsheet_id=spreadsheet_id, sheet_id=sheet_id))
    except Exception:
        pass


def log_fix(kind: str, confirm: bool, result: dict, *, addr=None) -> None:
    try:
        log_event(fix_summary(kind, confirm, result, addr=addr))
    except Exception:
        pass


def _selftest():
    import tempfile

    cases = []

    def check(name, cond):
        cases.append((name, bool(cond)))

    payload = {
        "groups": [
            {"kind": "low-contrast", "fix_lane": "safe-auto", "severity": "high", "count": 3,
             "addrs": ["A1", "A2", "A3"], "cellValues": {"A1": "SECRET CLIENT TEXT"}},
            {"kind": "junk-decimal", "fix_lane": "surfaced", "severity": "medium", "addrs": ["B7"]},
        ],
        "cellCount": 500, "truncated": False,
        "vision": {"applied": True, "netNew": 2},
        "formula_fetch": {"enabled": False},
    }
    s = scan_summary(payload, spreadsheet_id="ss-123", sheet_id="sh-9")
    check("findingCount sums cells", s["findingCount"] == 4)
    check("byKind counts cells", s["byKind"] == {"low-contrast": 3, "junk-decimal": 1})
    check("byLane split", s["byLane"] == {"safe-auto": 3, "surfaced": 1})
    check("vision carried", s["visionApplied"] and s["visionNetNew"] == 2)
    check("sheet hashed (no raw id)", s["sheet"] != "ss-123:sh-9" and len(s["sheet"]) == 16)
    # PII guard: serialize the whole summary and assert no cell content leaked
    blob = json.dumps(s)
    check("no cell value in summary", "SECRET CLIENT TEXT" not in blob)
    check("no raw spreadsheet id", "ss-123" not in blob)

    f = fix_summary("junk-decimal", True, {"status": "mismatch-reverted", "addr": "B7", "before": "x"})
    check("fix status captured", f["status"] == "mismatch-reverted" and f["phase"] == "apply")
    check("fix no before-state leak", "before" not in f)

    # log_event writes a line to an isolated dir
    with tempfile.TemporaryDirectory() as td:
        os.environ["WINGMAN_LOG_DIR"] = td
        log_event({"event": "scan", "findingCount": 1})
        files = list(Path(td).glob("wingman-events-*.jsonl"))
        check("log file written", len(files) == 1)
        line = json.loads(files[0].read_text().strip())
        check("log line has ts + schema", "ts" in line and line["schema"] == SCHEMA_VERSION)
    os.environ.pop("WINGMAN_LOG_DIR", None)

    ok = sum(1 for _, c in cases if c)
    for name, c in cases:
        print(f"  {'PASS' if c else 'FAIL'}  {name}")
    print(f"\n{ok}/{len(cases)} passed")
    return ok == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
