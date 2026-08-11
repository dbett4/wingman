#!/usr/bin/env python3
"""Durable, redacted Wingman action receipts.

The telemetry log is deliberately aggregate-only. Action receipts are the local audit trail
for dry-run/apply outcomes: one JSON line per attempted fix with timestamp, pseudonymous
workbook/sheet identity, phase, status, Workiva account guard state, and redacted before /
planned / readback fields. They live outside the repo by default.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_NUM_OR_HEX = re.compile(r"(#[0-9A-Fa-f]{3,8}\b)|([-+(]?\$?\d[\d,]*(?:\.\d+)?%?\)?)")
_TEXT_KEYS = {"value", "before", "after", "expected", "readback"}
_SAFE_KEYS = {
    "status", "addr", "type", "reason", "warning", "kind", "phase",
    "beforeFormat", "afterFormat", "readbackFormat", "revertError", "readbackError",
    "error", "valueFormat",
}


def receipt_dir() -> Path:
    return Path(os.environ.get("WINGMAN_RECEIPT_DIR", str(Path.home() / ".wingman" / "receipts")))


def _hash(raw: str | None, n: int = 16) -> str:
    return hashlib.sha256(str(raw or "").encode()).hexdigest()[:n]


def _redact_number_runs(text: str | None) -> str | None:
    if text is None:
        return None
    return _NUM_OR_HEX.sub(lambda m: m.group(1) if m.group(1) is not None else "#", str(text))


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return "#"
    if isinstance(value, str):
        if key in _TEXT_KEYS and not value.startswith("#"):
            return {"redactedText": True, "length": len(value), "sha256": _hash(value, 12)}
        return _redact_number_runs(value)
    if isinstance(value, list):
        return [_redact_value(v, key=key) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            sk = str(k)
            if sk.lower() in {"spreadsheetid", "sheetid", "tableid", "token", "authorization"}:
                out[f"{sk}Hash"] = _hash(str(v))
            else:
                out[sk] = _redact_value(v, key=sk)
        return out
    return str(value)


def action_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    result = result or {}
    out: dict[str, Any] = {}
    for key, value in result.items():
        if key in _SAFE_KEYS or key in _TEXT_KEYS:
            out[key] = _redact_value(value, key=key)
    return out


def build_action_receipt(
    *,
    spreadsheet_id: str | None,
    sheet_id: str | None,
    addr: str | None,
    kind: str,
    confirm: bool,
    result: dict[str, Any] | None,
    arid_status: str | None = None,
    safety_status: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "artifact": "wingman-action-receipt",
        "schema": SCHEMA_VERSION,
        "ts": now.isoformat(),
        "workbookHash": _hash(spreadsheet_id),
        "sheetHash": _hash(f"{spreadsheet_id or ''}:{sheet_id or ''}"),
        "addr": addr,
        "kind": kind,
        "phase": "apply" if confirm else "dry-run",
        "status": (result or {}).get("status", "unknown"),
        "aridGuard": arid_status or ("required" if os.environ.get("WORKIVA_EXPECTED_ARID") else "missing"),
        "safetyGuard": safety_status or "unknown",
        "result": action_summary(result),
    }


def write_action_receipt(receipt: dict[str, Any], *, out_dir: str | os.PathLike | None = None) -> str:
    d = Path(out_dir) if out_dir else receipt_dir()
    d.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = d / f"wingman-action-receipts-{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return str(path)


def log_action_receipt(**kwargs: Any) -> str | None:
    """Best-effort write; returns path on success, None on receipt failure."""
    try:
        return write_action_receipt(build_action_receipt(**kwargs))
    except Exception:
        return None


if __name__ == "__main__":
    sample = build_action_receipt(
        spreadsheet_id="ss-secret", sheet_id="sh", addr="B7", kind="label-hygiene",
        confirm=True, result={"status": "applied", "before": "Revenue 100", "after": "Revenue 100", "readback": "Revenue 100"},
        arid_status="configured", safety_status="dummy-allowlisted",
    )
    print(json.dumps(sample, indent=2, ensure_ascii=False))
