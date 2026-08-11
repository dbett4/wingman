#!/usr/bin/env python3
"""
Wingman local service (stdlib http.server — zero pip deps, self-contained per ADR-0002).

The extension side panel calls this on localhost. It holds Workiva creds (never the
extension), reads + scans via wk_client/detectors, and brokers the safe-lane fix via
fixer (confirm-before-write, readback, revert).

Endpoints (JSON):
  GET  /health
  GET  /config                                     -> presets + safe_fix_kinds contract
  GET  /api/status, /status                         -> wingman feature flags + extension build id (read-only)
  GET  /api/queue?spreadsheetId=..&sheetId=..&checks=tieout  -> scan queue + run_checks FAILs
  GET  /api/checks?spreadsheetId=..&suite=tieout|hardening_gate|scorecard -> checks FAILs only
       (suite=scorecard surfaces an existing tieout scorecard JSON read-only; no subprocess)
  GET/POST /api/review-packet {spreadsheetId,sheetId?,checks?,label?,write?} -> redacted ACCEPT/BLOCKED/UNVERIFIED packet
  POST /api/queue {spreadsheetId,sheetId,vision?,cellImages?} -> sheet queue with vision merge
  GET  /scan, POST /scan, GET /scan-workbook       -> legacy aliases of /api/queue (deprecated)
  POST /fix   {spreadsheetId,sheetId,addr,kind?,targetHex?} -> dry-run plan (no write)
  POST /apply {spreadsheetId,sheetId,addr,kind?,targetHex?} -> writes (readback+revert)
      kind: "low-contrast" (default) | "label-hygiene" | "negative-without-parens" | "junk-decimal"

CORS: only the Wingman extension origin (chrome-extension://<id>) + localhost are allowed.
Run:  python3 server/app.py            # binds 127.0.0.1:8770
      WINGMAN_PORT=9000 python3 server/app.py
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import error as SocketError

import checks_bridge
import diagnose
import format_lane
import hardening_gate_bridge
import detectors
import fixer
import review_packet
import vision_candidates
import wingman_config
import wingman_log
import wingman_receipts
import wk_client as wk

PORT = int(os.environ.get("WINGMAN_PORT", "8770"))
WINGMAN_TOKEN = os.environ.get("WINGMAN_TOKEN", "wm-local-1665dd6a")  # shared with the extension background worker
# Allowlisted extension id(s). Set WINGMAN_EXT_ID to your unpacked/store extension ID
# (chrome://extensions with Developer mode on shows the ID of a loaded unpacked build).
ALLOWED_EXT_IDS = {
    x.strip() for x in os.environ.get("WINGMAN_EXT_ID", "").split(",") if x.strip()
}
REPO_ROOT = Path(__file__).resolve().parents[1]
# Operator-provided write-safety configs (see _assert_apply_safety). /apply fails closed
# until safety.json exists and the target workbook is explicitly allowlisted.
DEFAULT_SAFETY_JSON = REPO_ROOT / "config" / "safety.json"
DEFAULT_DUMMY_IDS_JSON = REPO_ROOT / "config" / "dummy_ids.json"

_ctx = wk._ssl_context()
_tok = {"value": None, "ts": 0.0}
_tok_lock = threading.Lock()  # ThreadingHTTPServer: serialize refresh so concurrent requests don't double-mint
_tables = {}  # (ss, sheetId) -> tableId, cached per process


def _token():
    with _tok_lock:
        if not _tok["value"] or (time.time() - _tok["ts"]) > 480:  # token lives 600s; refresh at 480
            _tok["value"] = wk.get_token(_ctx)
            _tok["ts"] = time.time()
        return _tok["value"]


def _table_id(ss, sheet_id):
    # tableId = sheet.table.table (opaque content-table token), from the CONTENT sheets
    # list (X-Version header). The platform endpoints do not expose it.
    key = (ss, sheet_id)
    if key not in _tables:
        raw = wk._get(f"/spreadsheets/{ss}/sheets?$maxperpage=500", _token(), _ctx, version="2026-01-01")
        lst = raw.get("data", raw if isinstance(raw, list) else [])
        for s in lst:
            tbl = (s.get("table") or {}).get("table")
            if tbl:  # only cache resolved tokens; a sheet with no table re-fetches next call (not None forever)
                _tables[(ss, s.get("id"))] = tbl
    return _tables.get(key)


def _ext_build():
    # Build id = hash of the extension folder's file names + mtimes + sizes. Changes whenever any
    # extension source file changes, so the background worker can detect "code changed -> reload".
    import hashlib
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
    h = hashlib.sha256()
    for dirpath, _dirs, files in sorted(os.walk(root)):
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
                h.update(f"{os.path.relpath(p, root)}:{st.st_mtime_ns}:{st.st_size}".encode())
            except OSError:
                pass
    return h.hexdigest()[:16]


def _origin_ok(origin):
    # Write endpoints hold Workiva creds, so require a known origin. The extension always sends its
    # chrome-extension:// origin; localhost is allowed only on the EXACT service port. Missing or foreign
    # origins are rejected. (A determined local process can still forge the header — native messaging or a
    # user-approved token is the gate before any non-internal use; tracked as a follow-up.)
    if not origin:
        return False
    try:
        p = urllib.parse.urlparse(origin)
    except ValueError:
        return False
    if p.scheme == "chrome-extension":
        return p.netloc in ALLOWED_EXT_IDS
    return p.hostname in ("127.0.0.1", "localhost", "::1") and p.port == PORT


def _truthy(s):
    return str(s or "").strip().lower() in ("1", "true", "yes", "on")


def _route_path(raw_path):
    """Normalize path for routing (trailing slash tolerant). Query string untouched."""
    path = (raw_path or "/").split("?", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path


def _api_error_payload(exc):
    """Map upstream Workiva failures to panel-friendly messages (502 envelope)."""
    msg = repr(exc)
    if "NO_CREDENTIALS" in msg:
        return {
            "error": (
                "Workiva credentials missing — set WORKIVA_CLIENT_ID + WORKIVA_CLIENT_SECRET "
                "in the service environment (or a .env in the working directory)"
            ),
            "detail": msg,
        }
    if "HTTPError 404" in msg or "404: 'Not Found'" in msg:
        return {
            "error": (
                "Workiva returned 404 — spreadsheet or sheet not found "
                "(check service credentials and that the open workbook is accessible)"
            ),
            "detail": msg,
        }
    if "HTTPError 403" in msg or "403: 'Forbidden'" in msg:
        return {
            "error": "Workiva returned 403 — service credentials cannot access this spreadsheet",
            "detail": msg,
        }
    return {"error": msg}


def _load_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _configured_safety_json_path():
    return Path(os.environ.get("WINGMAN_SAFETY_JSON", str(DEFAULT_SAFETY_JSON)))


def _configured_dummy_ids_json_path():
    return Path(os.environ.get("WINGMAN_DUMMY_IDS_JSON", str(DEFAULT_DUMMY_IDS_JSON)))


def _apply_allowlist_ids():
    ids = {x.strip() for x in os.environ.get("WINGMAN_APPLY_ALLOWLIST", "").split(",") if x.strip()}
    dummy_cfg = _load_json_file(_configured_dummy_ids_json_path()) or {}
    for item in dummy_cfg.get("dummy_ids") or []:
        if isinstance(item, dict) and item.get("id"):
            ids.add(str(item["id"]))
    for key in ("primary_spreadsheet_id", "dummy_document_companion_spreadsheet_id"):
        if dummy_cfg.get(key):
            ids.add(str(dummy_cfg[key]))
    return ids


def _protected_source_ids():
    safety = _load_json_file(_configured_safety_json_path()) or {}
    protected = safety.get("protected_source_ids") or {}
    return {str(v) for v in protected.values() if v}


def _assert_apply_safety(spreadsheet_id):
    """Fail-closed runtime guard for Workiva writes.

    Dry-runs may inspect any authorized workbook. `/apply` is local-only until a workbook is
    explicitly allowlisted as a dummy/sandbox workbook; known source ids are denied even if a
    caller supplies a broad allowlist. This makes the sandbox safety config executable instead
    of just documentation.
    """
    if not spreadsheet_id:
        raise RuntimeError("refusing Workiva write: spreadsheetId missing")
    safety_path = _configured_safety_json_path()
    dummy_path = _configured_dummy_ids_json_path()
    if _load_json_file(safety_path) is None:
        raise RuntimeError(f"refusing Workiva write: safety config missing ({safety_path})")
    if _load_json_file(dummy_path) is None and not os.environ.get("WINGMAN_APPLY_ALLOWLIST"):
        raise RuntimeError(f"refusing Workiva write: dummy allowlist missing ({dummy_path})")
    if spreadsheet_id in _protected_source_ids():
        raise RuntimeError("refusing Workiva write: target is a protected source workbook")
    if spreadsheet_id not in _apply_allowlist_ids():
        raise RuntimeError("refusing Workiva write: target is not in the dummy/apply allowlist")
    return "dummy-allowlisted"


def _run_scan(ss, sh, *, vision=False, cell_images=None, checks_suite=None):
    """Legacy alias — canonical scan response is the /api/queue sheet payload."""
    return _run_sheet_queue(ss, sh, vision=vision, cell_images=cell_images, checks_suite=checks_suite)


def _run_workbook_queue(ss, checks_suite=None):
    wb = wk.scan_workbook(ss, _token(), _ctx)
    payload = diagnose.build_workbook_queue(wb)
    # Log each non-error sheet so workbook-wide scans show up in the digest, not just /scan.
    # Each sheet entry already carries noise-dropped groups + cellCount (wk.scan_workbook).
    for sheet in wb.get("sheets") or []:
        if not sheet.get("error"):
            wingman_log.log_scan(ss, sheet.get("sheetId"), sheet)  # PII-safe; failsafe
    return _maybe_merge_checks(payload, ss, checks_suite)


def _attach_formula_fetch(payload, formula_meta):
    if formula_meta:
        payload["formula_fetch"] = formula_meta
    return payload


def _attach_type_fetch(payload, type_meta):
    if type_meta:
        payload["type_fetch"] = type_meta


def _attach_link_fetch(payload, link_meta):
    if link_meta:
        payload["link_fetch"] = link_meta
    return payload


def _run_checks_bridge(ss, suite):
    """Route suite to the external checks CLI or the hardening-gate bridge."""
    key = (suite or "tieout").strip().lower()
    if key == "hardening_gate":
        return hardening_gate_bridge.run_hardening_gate_suite(ss)
    return checks_bridge.run_checks_suite(ss, suite=key)


def _maybe_merge_checks(payload, ss, checks_suite):
    """When checks=tieout|hardening_gate|all|…, run read-only checks and merge FAIL rows."""
    if not checks_suite:
        return payload
    meta = _run_checks_bridge(ss, checks_suite)
    return diagnose.merge_queue_with_checks(payload, meta)


def _run_checks_only(ss, suite):
    meta = _run_checks_bridge(ss, suite)
    items = diagnose.check_fails_to_queue_items(meta.get("fail_rows") or [], spreadsheet_id=ss)
    return {
        "scope": "checks",
        "spreadsheetId": ss,
        "suite": meta.get("suite") or suite,
        "issueCount": sum(i["count"] for i in items),
        "groupCount": len(items),
        "summary": {
            "groups": len(items),
            "cells": sum(i["count"] for i in items),
            "high": sum(1 for i in items if i["severity"] == "high"),
            "fixable": 0,
            "review": len(items),
        },
        "items": items,
        "checks": {
            k: meta.get(k)
            for k in (
                "requested", "suite", "source", "applied", "skipped", "live_skipped",
                "fail_count", "error", "exit_code", "report_path", "working_dir", "project",
                "elapsed_s", "planned_operations", "evaluation", "scorecard_generated_at",
                "tieout_enrich",
            )
            if k in meta
        },
    }


def _run_sheet_queue(ss, sh, *, vision=False, cell_images=None, checks_suite=None):
    tid = _table_id(ss, sh)
    cells, findings, truncated, vision_meta, formula_meta, type_meta, link_meta = wk.scan_sheet(
        ss, sh, _token(), _ctx, vision=vision, cell_images=cell_images, table_id=tid)
    cells_by_addr = {c["addr"]: c for c in cells}
    groups = detectors.group_findings(findings, cells_by_addr)
    format_lane.attach_gated_format_targets(groups, cells)
    # Drop no-evidence format-consistency noise (no per-cell fix + no gated column target).
    # Keeps every fixable/gated finding and every other detector. See ADR-0004.
    groups, _dropped_noise = format_lane.drop_no_evidence_format_groups(groups)
    sheet_name = None
    for s in wk.list_sheets(ss, _token(), _ctx):
        if s["id"] == sh:
            sheet_name = s.get("name")
            break
    payload = diagnose.build_sheet_queue(
        groups, spreadsheet_id=ss, sheet_id=sh, sheet_name=sheet_name,
    )
    flagged_addrs = set()
    for g in groups:
        flagged_addrs.update(g.get("addrs") or [])
    payload["visionCandidates"] = vision_candidates.build_vision_candidates(
        cells, flagged_addrs, cap=80,
    )
    payload["cellCount"] = len(cells)
    payload["truncated"] = truncated
    if vision or vision_meta:
        payload["vision"] = vision_meta or {"requested": vision, "applied": False,
                                            "skipped": "no cellImages supplied"}
    _attach_formula_fetch(payload, formula_meta)
    _attach_type_fetch(payload, type_meta)
    _attach_link_fetch(payload, link_meta)
    wingman_log.log_scan(ss, sh, payload)  # PII-safe aggregate; failsafe
    return _maybe_merge_checks(payload, ss, checks_suite)


def _run_review_packet(ss, sh, *, checks_suite=None, label=None, vision=False,
                       cell_images=None, write=False):
    """Run the normal read-only scan path, then build a durable review packet (F1).

    No Workiva writes: the underlying scan/checks are the same read-only path as /api/queue.
    The packet is redacted (client values stripped, workbook id pseudonymized) and may be
    persisted to a local dir outside the repo when write=True."""
    if sh:
        queue = _run_sheet_queue(ss, sh, vision=vision, cell_images=cell_images, checks_suite=checks_suite)
    else:
        queue = _run_workbook_queue(ss, checks_suite)
    packet = review_packet.build_review_packet(
        queue, label=label, generated_at=datetime.now(timezone.utc).isoformat(),
    )
    md = review_packet.render_packet_md(packet)
    out = {"packet": packet, "markdown": md}
    if write:
        out["written"] = review_packet.write_packet(packet, markdown=md)
    return out


def _operator_config_status():
    """Redacted operator config facts for setup/status surfaces.

    This intentionally reports only present/missing/custom/default facts. It never
    returns tokens, extension IDs, or Workiva credential values.
    """
    token_custom = bool(os.environ.get("WINGMAN_TOKEN"))
    ext_custom = bool(os.environ.get("WINGMAN_EXT_ID"))
    warnings = []
    if not token_custom:
        warnings.append("WINGMAN_TOKEN is using the local default; set a custom token before sharing this service beyond local loopback.")
    if not ext_custom:
        warnings.append("WINGMAN_EXT_ID is using the packaged default; verify it matches the installed extension before relying on origin-gated writes.")
    return {
        "wingman_token": "custom" if token_custom else "default-local",
        "extension_origin": "custom" if ext_custom else "default-packaged",
        "allowed_extension_ids_count": len([x for x in ALLOWED_EXT_IDS if x]),
        "workiva_client_id": "present" if os.environ.get("WORKIVA_CLIENT_ID") else "missing",
        "workiva_client_secret": "present" if os.environ.get("WORKIVA_CLIENT_SECRET") else "missing",
        "workiva_expected_arid": "present" if os.environ.get("WORKIVA_EXPECTED_ARID") else "missing",
        "apply_safety_config": "present" if _load_json_file(_configured_safety_json_path()) is not None else "missing",
        "apply_allowlist_count": len(_apply_allowlist_ids()),
        "warnings": warnings,
    }


def _wingman_status():
    return {
        "ok": True,
        "service": "wingman",
        "port": PORT,
        "version": {"extension_build": _ext_build()},
        "operator_config": _operator_config_status(),
        "features": {
            "formula_fetch": wk.formula_fetch_enabled(),
            "formula_fetch_mode": wk.formula_fetch_mode(),
            "formula_fetch_cap": wk.formula_fetch_cap(),
            "vision": True,
            "checks": True,
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # quiet; the panel surfaces status

    def _cors(self, origin):
        if origin and _origin_ok(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Wingman-Token")

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors(self.headers.get("Origin"))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if not _origin_ok(origin):
            self._send(403, {"error": "origin not allowed"})
            return
        self.send_response(204)
        self._cors(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self):
        # The extension's background worker fetches with no Origin header, so origin-based CORS
        # can't gate it — the shared token does. A web page cannot read the token, and a non-simple
        # web request is preflight-rejected by do_OPTIONS (origin check). Token first, origin fallback.
        if self.headers.get("X-Wingman-Token") == WINGMAN_TOKEN:
            return True
        return _origin_ok(self.headers.get("Origin"))

    def _guard(self):
        if not self._authorized():
            self._send(403, {"error": "not authorized (missing token / bad origin)"})
            return False
        return True

    def _loopback_readonly_ok(self, path):
        """Allow local ops probes to read non-sensitive status surfaces without the extension token."""
        if path not in ("/", "/config", "/api/status", "/status", "/version", "/digest"):
            return False
        host = self.client_address[0] if self.client_address else ""
        return host in ("127.0.0.1", "::1", "localhost")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = _route_path(u.path)
        if path == "/health":
            self._send(200, {"ok": True})
            return
        if not self._loopback_readonly_ok(path) and not self._guard():
            return
        q = urllib.parse.parse_qs(u.query)
        try:
            if path == "/":
                self._send(200, {
                    "ok": True,
                    "service": "wingman",
                    "message": "Wingman local service is running. Use the Chrome extension for Workiva actions.",
                    "status": "/api/status",
                    "digest": "/digest",
                    "operator_config": _operator_config_status(),
                    "guarded_endpoints": ["/api/queue", "/api/checks", "/api/review-packet", "/fix", "/apply"],
                })
            elif path == "/config":
                self._send(200, wingman_config.service_config())
            elif path in ("/api/status", "/status"):
                self._send(200, _wingman_status())
            elif path == "/version":
                self._send(200, {"build": _ext_build()})  # dev auto-reload signal
            elif path == "/digest":
                import wingman_log_digest as wd  # improvement digest mined from the activity log
                self._send(200, wd.build_digest(wd.load_events(wingman_log.log_dir())))
            elif path == "/scan":
                ss = q.get("spreadsheetId", [""])[0]
                sh = q.get("sheetId", [""])[0]
                if not ss or not sh:
                    self._send(400, {"error": "spreadsheetId and sheetId required"})
                    return
                vision = _truthy(q.get("vision", [""])[0])
                checks_suite = q.get("checks", [""])[0] or None
                self._send(200, _run_scan(ss, sh, vision=vision, checks_suite=checks_suite))
            elif path == "/scan-workbook":
                ss = q.get("spreadsheetId", [""])[0]
                if not ss:
                    self._send(400, {"error": "spreadsheetId required"})
                    return
                checks_suite = q.get("checks", [""])[0] or None
                self._send(200, _run_workbook_queue(ss, checks_suite))
            elif path == "/api/checks":
                ss = q.get("spreadsheetId", [""])[0]
                suite = q.get("suite", q.get("checks", ["tieout"]))[0] or "tieout"
                if not ss:
                    self._send(400, {"error": "spreadsheetId required"})
                    return
                self._send(200, _run_checks_only(ss, suite))
            elif path == "/api/queue":
                ss = q.get("spreadsheetId", [""])[0]
                sh = q.get("sheetId", [""])[0]
                checks_suite = q.get("checks", [""])[0] or None
                if not ss:
                    self._send(400, {"error": "spreadsheetId required"})
                    return
                if sh:
                    self._send(200, _run_sheet_queue(ss, sh, checks_suite=checks_suite))
                else:
                    self._send(200, _run_workbook_queue(ss, checks_suite))
            elif path == "/api/review-packet":
                ss = q.get("spreadsheetId", [""])[0]
                sh = q.get("sheetId", [""])[0] or None
                if not ss:
                    self._send(400, {"error": "spreadsheetId required"})
                    return
                checks_suite = q.get("checks", [""])[0] or None
                label = q.get("label", [""])[0] or None
                write = _truthy(q.get("write", [""])[0])
                self._send(200, _run_review_packet(
                    ss, sh, checks_suite=checks_suite, label=label, write=write))
            else:
                self._send(404, {"error": "not found", "path": path})
        except Exception as e:
            self._send(502, _api_error_payload(e))

    def do_POST(self):
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        path = _route_path(u.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/scan":
                ss, sh = body.get("spreadsheetId"), body.get("sheetId")
                if not ss or not sh:
                    self._send(400, {"error": "spreadsheetId and sheetId required"})
                    return
                vision = _truthy(body.get("vision"))
                checks_suite = (body.get("checks") or "").strip() or None
                cell_images = body.get("cellImages") if isinstance(body.get("cellImages"), dict) else None
                self._send(200, _run_scan(ss, sh, vision=vision or bool(cell_images),
                                          cell_images=cell_images, checks_suite=checks_suite))
                return
            if path == "/api/queue":
                ss, sh = body.get("spreadsheetId"), body.get("sheetId")
                if not ss or not sh:
                    self._send(400, {"error": "spreadsheetId and sheetId required"})
                    return
                vision = _truthy(body.get("vision"))
                checks_suite = (body.get("checks") or "").strip() or None
                cell_images = body.get("cellImages") if isinstance(body.get("cellImages"), dict) else None
                self._send(200, _run_sheet_queue(ss, sh, vision=vision or bool(cell_images),
                                                 cell_images=cell_images, checks_suite=checks_suite))
                return
            if path == "/api/review-packet":
                ss, sh = body.get("spreadsheetId"), body.get("sheetId")
                if not ss:
                    self._send(400, {"error": "spreadsheetId required"})
                    return
                checks_suite = (body.get("checks") or "").strip() or None
                label = body.get("label") or None
                vision = _truthy(body.get("vision"))
                cell_images = body.get("cellImages") if isinstance(body.get("cellImages"), dict) else None
                write = _truthy(body.get("write"))
                self._send(200, _run_review_packet(
                    ss, sh or None, checks_suite=checks_suite, label=label,
                    vision=vision or bool(cell_images), cell_images=cell_images, write=write))
                return
            ss, sh, addr = body.get("spreadsheetId"), body.get("sheetId"), body.get("addr")
            tgt = body.get("targetHex")  # optional; service computes the AA-passing color if absent
            kind = body.get("kind", "low-contrast")
            if not all([ss, sh, addr]):
                self._send(400, {"error": "spreadsheetId, sheetId, addr required"})
                return
            if kind not in wingman_config.SAFE_FIX_KINDS:
                self._send(400, {"error": f"unknown fix kind: {kind!r}"})
                return
            tid = _table_id(ss, sh)
            if not tid:
                self._send(404, {"error": "sheet/table not found"})
                return
            confirm = path == "/apply"
            if path not in ("/fix", "/apply"):
                self._send(404, {"error": "not found"})
                return
            safety_status = "dry-run"
            if confirm:
                safety_status = _assert_apply_safety(ss)
            tok, ctx = _token(), _ctx
            arid_status = fixer.assert_expected_arid(tok) if confirm else ("not-required-dry-run")
            if kind == "label-hygiene":
                result = fixer.fix_label_trim(ss, sh, tid, addr, tok, ctx, confirm=confirm)
            elif kind == "negative-without-parens":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_negative_parens(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "junk-decimal":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_junk_decimal(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "missing-thousands-separator":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_missing_thousands(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "number-on-accounting-column":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_number_on_accounting(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "precision-mismatch":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_precision_mismatch(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "prefix-mismatch":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_prefix_mismatch(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "zero-display-mismatch":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_zero_display_mismatch(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            elif kind == "year-automatic-coercion":
                tgt_vf = (body.get("target") or {}).get("valueFormat") if isinstance(body.get("target"), dict) else None
                result = fixer.fix_year_coercion(ss, sh, tid, addr, tok, ctx, confirm=confirm, target_vf=tgt_vf)
            else:
                result = fixer.fix_contrast(ss, sh, tid, addr, tgt, tok, ctx, confirm=confirm)
            wingman_log.log_fix(kind, confirm, result, addr=addr)  # PII-safe outcome; failsafe
            receipt_path = wingman_receipts.log_action_receipt(
                spreadsheet_id=ss, sheet_id=sh, addr=addr, kind=kind, confirm=confirm,
                result=result, arid_status=arid_status, safety_status=safety_status,
            )
            if receipt_path:
                result = {**result, "receiptPath": receipt_path}
            self._send(200, result)
        except Exception as e:
            self._send(502, _api_error_payload(e))


class WingmanHTTPServer(ThreadingHTTPServer):
    """Threaded local server with quiet handling for probe disconnects.

    Browser/devtools/curl probes sometimes close the socket after reading enough
    bytes. Python's stdlib server treats that as an exception and prints a full
    traceback from worker threads, even though the service is healthy. Keep real
    server errors visible, but suppress expected local disconnect noise.
    """

    def handle_error(self, request, client_address):
        exc = None
        try:
            import sys
            exc = sys.exc_info()[1]
        except Exception:
            exc = None
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, SocketError)):
            return
        super().handle_error(request, client_address)


def main():
    srv = WingmanHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Wingman service on http://127.0.0.1:{PORT} (allowed ext: {', '.join(ALLOWED_EXT_IDS)})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
