#!/usr/bin/env python3
"""
Wingman safe-lane write path (ADR-0002, ADR-0003, ADR-0004).

Two v1 auto-writes behind the full safety harness (dry-run → confirm Apply → readback → revert):

1. **Low-contrast** on a PLAIN cell — set font color to an AA-passing value.
   Type-guard (refuse richText) — richText fontColor writes silently no-op (documented API behavior).

2. **Label-hygiene trim** on a PLAIN text label — strip leading/trailing whitespace and collapse
   internal double spaces via the values API PUT. Refuses richText, formula, linked, and merged cells.

3. **Negative-without-parens** on a numeric cell — apply ACCOUNTING valueFormat with
   useParensForNegatives via applyFormats (not TEXT or plain NUMBER). Refuses richText, linked,
   and merged cells; preserves prefix/precision from the cell when present. TEXT() formulas refused
   (W21 trap).

4. **Junk-decimal** on a numeric cell — copy column neighbor valueFormat when 2-of-3 neighbors
   agree on sane precision (production formatting-audit pattern). Same TEXT() preflight as (3).

5. **Missing-thousands-separator** — copy neighbor valueFormat when column uses comma grouping.

6. **Number-on-accounting-column** — copy neighbor ACCOUNTING format when cell is NUMBER.

7. **Precision-mismatch** — copy neighbor integer precision when cell drifts 1–4 decimals.

Both paths: pre-read before-state → apply → readback → revert on mismatch (no reliable Workiva undo).

Endpoints:
  WRITE font: POST /platform/v1/spreadsheets/{ss}/sheets/{sid}/update
              {"applyFormats":{"formats":[{"ranges":[{startRow,stopRow,startColumn,stopColumn}],
                                           "textFormat":{"fontColor":"#RRGGBB"}}]}}
  WRITE value: PUT /platform/v1/spreadsheets/{ss}/sheets/{sid}/values/{A1}
               {"values":[["trimmed text"]]}
  TYPE read:  GET /content/tables/{tableId}/cells?startRow&stopRow&startColumn&stopColumn
              (X-Version 2026-01-01; MUST pass numeric bounds - range query param is ignored, errors line 29)

CLI bounded harness test (proves write->readback->revert on a CONFIRMED-EMPTY cell, ADR-0003):
    python3 server/fixer.py <ss> <sheetId> <tableId> <A1>
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.request

import detectors
import wk_client as wk

API_VERSION = "2026-01-01"
_LETTERS = re.compile(r"^([A-Z]+)([0-9]+)$")
_TEXT_FORMULA = re.compile(r"\bTEXT\s*\(", re.I)


def _b64url_json(segment):
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def token_arid(token):
    """Decode Workiva JWT arid claim to a human Type/id string, e.g. Account/123456789."""
    parts = (token or "").split(".")
    if len(parts) < 2:
        raise RuntimeError("token is not a JWT; cannot verify WORKIVA_EXPECTED_ARID")
    claims = _b64url_json(parts[1])
    raw = claims.get("arid", "") or ""
    if not raw:
        raise RuntimeError("token has no arid claim; cannot verify WORKIVA_EXPECTED_ARID")
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
            "utf-8", "replace"
        ).replace("\x1f", "/")
    except Exception as e:
        raise RuntimeError(f"could not decode token arid claim: {e}")


def assert_expected_arid(token):
    """Fail-closed write gate: WORKIVA_EXPECTED_ARID must match the token's account/workspace."""
    expected = os.environ.get("WORKIVA_EXPECTED_ARID", "").strip()
    if not expected:
        raise RuntimeError("refusing Workiva write: WORKIVA_EXPECTED_ARID is required")
    actual = token_arid(token)
    if actual != expected:
        raise RuntimeError(
            f"refusing Workiva write: token arid {actual!r} != WORKIVA_EXPECTED_ARID {expected!r}"
        )
    return actual


def addr_to_rc(addr):
    """A1 -> (row, col), both 0-based."""
    m = _LETTERS.match(addr.strip().upper())
    if not m:
        raise ValueError(f"bad A1 address: {addr!r}")
    letters, digits = m.groups()
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return int(digits) - 1, col - 1


def _operation_url(body):
    if not isinstance(body, dict):
        return None
    loc = body.get("operationLocation") or body.get("operationUrl") or body.get("href")
    if not loc:
        return None
    loc = str(loc)
    return loc if loc.startswith("http") else wk._base() + loc


def _retry_after_seconds(resp, default=1.0):
    """Return a bounded Retry-After delay from a urllib response-like object.

    Workiva's public OpenAPI docs note that operation polling is rate-limited at 1/sec and
    429s may include Retry-After. Keep Wingman's safe-fix lane inside that contract instead
    of hammering `/operations/{id}` every 0.5s.
    """
    headers = getattr(resp, "headers", None)
    raw = None
    if headers is not None:
        getter = getattr(headers, "get", None)
        if getter:
            raw = getter("Retry-After") or getter("retry-after")
    if raw is None:
        getheader = getattr(resp, "getheader", None)
        if getheader:
            raw = getheader("Retry-After") or getheader("retry-after")
    if raw is None:
        delay = float(default)
    else:
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            delay = float(default)
    return max(0.1, min(delay, 10.0))


def _poll_operation(body, token, ctx, *, timeout_s=45):
    """Poll a Workiva async operation body and raise when the operation fails.

    Some Workiva writes return 202 before validation has completed. Without polling, a guarded
    write can immediately read back the unchanged value and falsely pass when before==after.
    Operation GETs are rate-limited; poll at most once per second unless Workiva supplies a
    Retry-After delay.
    """
    op_url = _operation_url(body)
    if not op_url:
        return None
    deadline = time.time() + timeout_s
    last = None
    delay = 1.0
    while time.time() < deadline:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        req = urllib.request.Request(op_url, headers=headers)
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        raw = resp.read()
        last = json.loads(raw) if raw else {}
        status = str((last or {}).get("status") or "").lower()
        if status in ("completed", "succeeded", "success"):
            return last
        if status in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError(f"Workiva async operation failed: {last!r}")
        delay = _retry_after_seconds(resp, default=delay)
        time.sleep(delay)
    raise TimeoutError(f"Workiva async operation did not complete within {timeout_s}s: {last!r}")


def _request_json(path, token, ctx, body, *, method, version=None, poll_async=True):
    if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        assert_expected_arid(token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "Content-Type": "application/json"}
    if version:
        headers["X-Version"] = version
    req = urllib.request.Request(wk._base() + path, data=json.dumps(body).encode(),
                                 headers=headers, method=method)
    resp = urllib.request.urlopen(req, context=ctx, timeout=60)
    raw = resp.read()
    payload = json.loads(raw) if raw else {}
    if poll_async and resp.status == 202:
        _poll_operation(payload, token, ctx)
    return payload, resp.status


def _post(path, token, ctx, body, version=None, poll_async=True):
    return _request_json(path, token, ctx, body, method="POST", version=version, poll_async=poll_async)


def _put(path, token, ctx, body, version=None, poll_async=True):
    return _request_json(path, token, ctx, body, method="PUT", version=version, poll_async=poll_async)


def read_cell_meta(table_id, row, col, token, ctx):
    """
    {'type': 'richText'|'plainText'|'formula'|None, 'merged': bool, 'linked': bool,
     'formula': bool, 'formulaText': str|None} from the content cells endpoint.
    Shape is data[rowIdx].cells[colIdx] (numeric bounds required).
    There is no top-level 'type'; richText is implied by value.richText. Merged cells carry
    a 'merges' key. Both richText and merged are unsafe for a fontColor safe-auto write.
    """
    path = (f"/content/tables/{table_id}/cells"
            f"?startRow={row}&stopRow={row}&startColumn={col}&stopColumn={col}")
    data = wk._get(path, token, ctx, version=API_VERSION)
    rows = data.get("data", [])
    cell = None
    if isinstance(rows, list) and rows:
        first = rows[0]
        rc = first.get("cells") if isinstance(first, dict) else first
        if isinstance(rc, list) and rc:
            cell = rc[0]
    if not isinstance(cell, dict):
        return {"type": None, "merged": False, "linked": False, "formula": False, "formulaText": None}
    val = cell.get("value")
    is_rich = isinstance(val, dict) and "richText" in val
    linked = isinstance(val, dict) and "destinationLink" in val
    is_formula = isinstance(val, dict) and (val.get("type") == "formula" or "formula" in val)
    formula_text = _extract_formula_text(val) if is_formula else None
    if is_rich:
        ctype = "richText"
    elif is_formula:
        ctype = "formula"
    else:
        ctype = "plainText"
    return {
        "type": ctype, "merged": bool(cell.get("merges")), "linked": linked,
        "formula": is_formula, "formulaText": formula_text,
    }


def _extract_formula_text(val):
    """Best-effort formula string from a content API cell value object."""
    if not isinstance(val, dict):
        return None
    for key in ("formula", "value", "rawValue"):
        raw = val.get(key)
        if isinstance(raw, str) and raw.strip():
            s = raw.strip()
            if s.startswith("=") or key == "formula":
                return s if s.startswith("=") else f"={s}"
    return None


def preflight_no_text_wrapper(formula_text):
    """
    W21-trap guard — refuse valueFormat writes when formula contains TEXT().
    Returns refusal reason string, or None when safe to proceed.
    """
    if not formula_text:
        return None
    if _TEXT_FORMULA.search(formula_text):
        return (
            "formula contains TEXT() — valueFormat write can rewrite scale (W21 trap); "
            "fix in Workiva UI"
        )
    return None


def read_cell_sheetdata(ss, sheet_id, row, col, token, ctx):
    """Effective formats + value from sheetdata. Returns (cell, found)."""
    raw = wk.get_sheetdata_cell(ss, sheet_id, wk.a1(row, col), token, ctx)
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return None, False
    rng = data.get("range", {}) or {}
    r0, c0 = rng.get("startRow", 0), rng.get("startColumn", 0)
    rows = data.get("cells", []) or []
    ri, ci = row - r0, col - c0
    if 0 <= ri < len(rows) and isinstance(rows[ri], list) and 0 <= ci < len(rows[ri]):
        return rows[ri][ci], True
    return None, False


def read_effective_fontcolor(ss, sheet_id, row, col, token, ctx):
    """Effective font color via sheetdata. Returns (fontColor, value, cell, found). found=False means
    the cell could not be resolved in sheetdata — kept distinct from an in-page cell with no explicit
    color, so callers never confuse a miss with a default-colored cell."""
    raw = wk.get_sheetdata_cell(ss, sheet_id, wk.a1(row, col), token, ctx)
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return None, None, None, False
    rng = data.get("range", {}) or {}
    r0, c0 = rng.get("startRow", 0), rng.get("startColumn", 0)
    rows = data.get("cells", []) or []
    ri, ci = row - r0, col - c0
    if 0 <= ri < len(rows) and isinstance(rows[ri], list) and 0 <= ci < len(rows[ri]):
        cell = rows[ri][ci]
        ef = (cell.get("effectiveFormats", {}) or {})
        val = cell.get("value", "")
        return (ef.get("textFormat", {}) or {}).get("fontColor"), ("" if val is None else str(val)), cell, True
    return None, None, None, False


def apply_fontcolor(ss, sheet_id, row, col, color_hex, token, ctx):
    body = {"applyFormats": {"formats": [{
        "ranges": [{"startRow": row, "stopRow": row, "startColumn": col, "stopColumn": col}],
        "textFormat": {"fontColor": color_hex},
    }]}}
    return _post(f"/platform/v1/spreadsheets/{ss}/sheets/{sheet_id}/update", token, ctx, body)


def apply_valueformat(ss, sheet_id, row, col, value_format, token, ctx):
    body = {"applyFormats": {"formats": [{
        "ranges": [{"startRow": row, "stopRow": row, "startColumn": col, "stopColumn": col}],
        "valueFormat": value_format,
    }]}}
    return _post(f"/platform/v1/spreadsheets/{ss}/sheets/{sheet_id}/update", token, ctx, body)


def _effective_valueformat(cell):
    ef = ((cell or {}).get("effectiveFormats", {}) or {})
    return ef.get("valueFormat") or {}


# Workiva silently + LOSSILY rescales every stored numeric value in a range when a valueFormat
# write changes enteredIn/shownIn: the displayed value is preserved, the raw value is not, and the
# rescale cannot be cleanly reversed (a documented valueFormat-flip incident that silently
# corrupted formulas across dozens of sheets). A neighbor-consensus format copy returns the neighbor's WHOLE
# valueFormat (format_lane.neighbor_*_consensus → dict(vf)), so a neighbor on a different scale would
# corrupt the cell. Refuse the write (surface only) whenever the planned format would change scale.
_SCALE_FIELDS = ("enteredIn", "shownIn")


def scale_change_block(before_vf, planned_vf):
    """Return a refusal reason if applying planned_vf would change enteredIn/shownIn from the
    cell's current scale; else None.

    Only a scale field PRESENT in planned_vf is compared — an omitted field is left untouched by
    applyFormats, so it is safe. An absent value on either side is normalized to the platform
    default (ONES), so ONES↔absent is not treated as a change."""
    before_vf = before_vf or {}
    planned_vf = planned_vf or {}
    for field in _SCALE_FIELDS:
        if field not in planned_vf:
            continue
        planned_scale = planned_vf.get(field) or "ONES"
        before_scale = before_vf.get(field) or "ONES"
        if planned_scale != before_scale:
            return (
                f"format copy would change {field} ({before_scale} → {planned_scale}); "
                "Workiva rescales stored values on a scale change (lossy, no clean revert) "
                "— fix in Workiva UI"
            )
    return None


def strip_unshown_currency_symbol(value_format):
    """Remove currencySymbol unless Workiva is explicitly asked to show it.

    Workiva treats a carried currencySymbol as a display request even when
    showCurrencySymbol is false. Neighbor-format copy paths can therefore paste
    a hidden symbol onto body rows. Keep restore/revert payloads faithful, but
    sanitize planned forward writes.
    """
    vf = dict(value_format or {})
    if vf.get("showCurrencySymbol") is not True:
        vf.pop("currencySymbol", None)
    return vf


def _vf_matches(target, actual):
    """True when readback valueFormat reflects the planned ACCOUNTING parens write."""
    if not actual:
        return False
    if (actual.get("valueFormatType") or "").upper() != "ACCOUNTING":
        return False
    if actual.get("useParensForNegatives") is not True:
        return False
    if target.get("prefix") and actual.get("prefix") != target.get("prefix"):
        return False
    return True


def _vf_matches_junk(target, actual):
    """True when readback valueFormat matches the planned neighbor-format copy."""
    if not actual or not target:
        return False
    if (actual.get("valueFormatType") or "").upper() != (target.get("valueFormatType") or "").upper():
        return False
    tp = (target.get("precision") or {})
    ap = (actual.get("precision") or {})
    if tp.get("auto") != ap.get("auto"):
        return False
    if tp.get("value") != ap.get("value"):
        return False
    if target.get("useParensForNegatives") is not None:
        if actual.get("useParensForNegatives") != target.get("useParensForNegatives"):
            return False
    if target.get("showThousandsSeparator") is not None:
        if actual.get("showThousandsSeparator") != target.get("showThousandsSeparator"):
            return False
    if target.get("prefix") and actual.get("prefix") != target.get("prefix"):
        return False
    if target.get("displayZeroAs") and actual.get("displayZeroAs") != target.get("displayZeroAs"):
        return False
    return True


def fix_format_copy(
    ss,
    sheet_id,
    table_id,
    addr,
    token,
    ctx,
    *,
    confirm=False,
    target_vf=None,
    no_fix_reason=None,
):
    """
    Generic safe-lane valueFormat copy (neighbor consensus at scan time).
    Preflight refuses TEXT()-wrapped formulas (W21 trap). dry-run → apply → readback → revert.
    """
    row, col = addr_to_rc(addr)
    meta = read_cell_meta(table_id, row, col, token, ctx)
    ctype = meta["type"]
    text_block = preflight_no_text_wrapper(meta.get("formulaText"))
    if text_block:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": text_block}
    if ctype == "richText":
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "richText cell — valueFormat write may not affect runs; fix in Workiva UI"}
    if meta["merged"]:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": "merged cell - skipped"}
    if meta.get("linked"):
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "linked cell — format write blocked; fix in Workiva UI"}
    if not target_vf:
        return {"status": "refused", "addr": addr,
                "reason": "no target valueFormat — re-scan or fix in Workiva UI"}
    cell, found = read_cell_sheetdata(ss, sheet_id, row, col, token, ctx)
    if not found:
        return {"status": "not-in-scan-page", "addr": addr,
                "reason": "cell is beyond the scanned page (large sheet); re-scan or open sheet"}
    before_vf = _effective_valueformat(cell)
    if no_fix_reason:
        reason = no_fix_reason(before_vf, cell)
        if reason:
            return {"status": "no-fix-needed", "reason": reason, "addr": addr}
    planned = strip_unshown_currency_symbol(target_vf)
    scale_block = scale_change_block(before_vf, planned)
    if scale_block:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": scale_block}
    plan = {
        "addr": addr, "type": ctype,
        "value": str((cell or {}).get("calculatedValue") or (cell or {}).get("value") or ""),
        "before": detectors._format_desc(before_vf),
        "after": detectors._format_desc(planned),
        "beforeFormat": detectors._format_desc(before_vf),
        "afterFormat": detectors._format_desc(planned),
        "valueFormat": planned,
    }
    if not confirm:
        return {"status": "dry-run", **plan}
    revert_vf = dict(before_vf) if before_vf else {"valueFormatType": "AUTOMATIC"}

    def _revert():
        try:
            apply_valueformat(ss, sheet_id, row, col, revert_vf, token, ctx)
            return None
        except Exception as re:
            return repr(re)

    apply_valueformat(ss, sheet_id, row, col, planned, token, ctx)
    time.sleep(0.3)
    try:
        after_cell, after_found = read_cell_sheetdata(ss, sheet_id, row, col, token, ctx)
    except Exception as e:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readbackError": repr(e), "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-failed-reverted", "error": repr(e), **plan}
    if not after_found:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "reason": "readback page-miss", "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-page-miss",
                "reason": "cell beyond the scanned page; write reverted, could not verify", **plan}
    after_vf = _effective_valueformat(after_cell)
    if not _vf_matches_junk(planned, after_vf):
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readback": detectors._format_desc(after_vf),
                    "revertError": rev, "warning": "cell may be left in the written state", **plan}
        return {"status": "mismatch-reverted", "expected": plan["after"],
                "readback": detectors._format_desc(after_vf), **plan}
    return {"status": "applied", "readback": detectors._format_desc(after_vf), **plan}


def fix_missing_thousands(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    def _no_fix(before_vf, cell):
        if before_vf.get("showThousandsSeparator") is True:
            return "already uses thousands separator"
        display = (cell or {}).get("calculatedValue") or (cell or {}).get("value") or ""
        num = detectors._parse_numeric(display)
        if num is None or abs(num) < 1000:
            return "value below thousands threshold"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm, target_vf=target_vf, no_fix_reason=_no_fix,
    )


def fix_number_on_accounting(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    def _no_fix(before_vf, _cell):
        if (before_vf.get("valueFormatType") or "").upper() == "ACCOUNTING":
            return "already ACCOUNTING format"
        if (before_vf.get("valueFormatType") or "AUTOMATIC").upper() not in ("NUMBER", "AUTOMATIC", ""):
            return "not a NUMBER/AUTOMATIC cell"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm, target_vf=target_vf, no_fix_reason=_no_fix,
    )


def fix_precision_mismatch(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    def _no_fix(before_vf, _cell):
        try:
            import junk_decimal as jd
        except ImportError:
            return None
        prec = (before_vf.get("precision") or {})
        if prec.get("auto") is not False:
            return "precision is auto"
        try:
            pval = int(prec.get("value"))
        except (TypeError, ValueError):
            return "precision not fixed"
        if pval <= 0 or pval >= jd.JUNK_PRECISION_MIN:
            return "precision outside mismatch band"
        if target_vf:
            tp = (target_vf.get("precision") or {})
            if tp.get("auto") is False and tp.get("value") == pval:
                return "already matches column precision"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm, target_vf=target_vf, no_fix_reason=_no_fix,
    )


def fix_prefix_mismatch(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    def _no_fix(before_vf, cell):
        vft = (before_vf.get("valueFormatType") or "AUTOMATIC").upper()
        if vft in ("PERCENT", "CURRENCY"):
            return "PERCENT/CURRENCY rows skip prefix copy"
        if before_vf.get("prefix"):
            return "already has prefix"
        display = (cell or {}).get("calculatedValue") or (cell or {}).get("value") or ""
        if detectors._parse_numeric(display) is None:
            return "not a numeric cell"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm, target_vf=target_vf, no_fix_reason=_no_fix,
    )


def fix_zero_display_mismatch(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    def _no_fix(before_vf, cell):
        display = (cell or {}).get("calculatedValue") or (cell or {}).get("value") or ""
        num = detectors._parse_numeric(display)
        if num != 0:
            return "cell is not zero"
        vft = (before_vf.get("valueFormatType") or "AUTOMATIC").upper()
        if vft in ("PERCENT", "CURRENCY"):
            return "PERCENT/CURRENCY rows skip zero-display copy"
        if before_vf.get("displayZeroAs") == "EM DASH":
            return "already uses em-dash zero display"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm, target_vf=target_vf, no_fix_reason=_no_fix,
    )


def fix_year_coercion(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    """Safe-lane fix: set valueFormatType=PERIOD on a 4-digit year coerced by AUTOMATIC format.
    Reuses the format-copy harness (TEXT preflight, scale guard, readback/revert). The PERIOD
    write carries no enteredIn/shownIn change, so it never trips the scale guard. Reversible."""
    def _no_fix(before_vf, cell):
        if (before_vf.get("valueFormatType") or "").upper() == "PERIOD":
            return "already PERIOD format"
        s = str((cell or {}).get("calculatedValue") or (cell or {}).get("value") or "").strip()
        if not re.fullmatch(r"\d{4}", s) or not (1900 <= int(s) <= 2100):
            return "cell is not a 4-digit year"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm,
        target_vf=(target_vf or {"valueFormatType": "PERIOD"}),
        no_fix_reason=_no_fix,
    )


def fix_negative_parens(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    """
    Safe-lane ACCOUNTING format fix: parentheses for negatives via applyFormats valueFormat.
    dry-run returns before/after format descriptions; confirm writes with readback/revert.
    """
    row, col = addr_to_rc(addr)
    meta = read_cell_meta(table_id, row, col, token, ctx)
    ctype = meta["type"]
    text_block = preflight_no_text_wrapper(meta.get("formulaText"))
    if text_block:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": text_block}
    if ctype == "richText":
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "richText cell — valueFormat write may not affect runs; fix in Workiva UI"}
    if meta["merged"]:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": "merged cell - skipped"}
    if meta.get("linked"):
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "linked cell — format write blocked; fix in Workiva UI"}
    cell, found = read_cell_sheetdata(ss, sheet_id, row, col, token, ctx)
    if not found:
        return {"status": "not-in-scan-page", "addr": addr,
                "reason": "cell is beyond the scanned page (large sheet); re-scan or open sheet"}
    before_vf = _effective_valueformat(cell)
    display = (cell or {}).get("calculatedValue") or (cell or {}).get("value") or ""
    num = detectors._parse_numeric(display)
    if num is None or num >= 0:
        return {"status": "no-fix-needed", "reason": "cell is not a negative numeric value", "addr": addr}
    if before_vf.get("useParensForNegatives") is True:
        return {"status": "no-fix-needed", "reason": "already uses parentheses for negatives", "addr": addr}
    planned = strip_unshown_currency_symbol(target_vf or detectors.build_accounting_parens_format(before_vf))
    scale_block = scale_change_block(before_vf, planned)
    if scale_block:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": scale_block}
    plan = {
        "addr": addr, "type": ctype, "value": str(display),
        "before": detectors._format_desc(before_vf),
        "after": detectors._format_desc(planned),
        "beforeFormat": detectors._format_desc(before_vf),
        "afterFormat": detectors._format_desc(planned),
        "valueFormat": planned,
    }
    if not confirm:
        return {"status": "dry-run", **plan}
    revert_vf = dict(before_vf) if before_vf else {"valueFormatType": "AUTOMATIC"}

    def _revert():
        try:
            apply_valueformat(ss, sheet_id, row, col, revert_vf, token, ctx)
            return None
        except Exception as re:
            return repr(re)

    apply_valueformat(ss, sheet_id, row, col, planned, token, ctx)
    time.sleep(0.3)
    try:
        after_cell, after_found = read_cell_sheetdata(ss, sheet_id, row, col, token, ctx)
    except Exception as e:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readbackError": repr(e), "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-failed-reverted", "error": repr(e), **plan}
    if not after_found:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "reason": "readback page-miss", "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-page-miss",
                "reason": "cell beyond the scanned page; write reverted, could not verify", **plan}
    after_vf = _effective_valueformat(after_cell)
    if not _vf_matches(planned, after_vf):
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readback": detectors._format_desc(after_vf),
                    "revertError": rev, "warning": "cell may be left in the written state", **plan}
        return {"status": "mismatch-reverted", "expected": plan["after"], "readback": detectors._format_desc(after_vf), **plan}
    return {"status": "applied", "readback": detectors._format_desc(after_vf), **plan}


def fix_junk_decimal(ss, sheet_id, table_id, addr, token, ctx, confirm=False, target_vf=None):
    """
    Safe-lane junk-decimal fix: copy column neighbor valueFormat (2-of-3 consensus at scan time).
    Preflight refuses TEXT()-wrapped formulas (W21 trap). dry-run → apply → readback → revert.
    """
    row, col = addr_to_rc(addr)
    meta = read_cell_meta(table_id, row, col, token, ctx)
    text_block = preflight_no_text_wrapper(meta.get("formulaText"))
    if text_block:
        return {"status": "refused", "addr": addr, "type": meta.get("type"), "reason": text_block}
    if not target_vf:
        try:
            import junk_decimal as jd
        except ImportError:
            return {"status": "refused", "addr": addr, "reason": "junk_decimal module unavailable"}
        cell, found = read_cell_sheetdata(ss, sheet_id, row, col, token, ctx)
        if not found:
            return {"status": "not-in-scan-page", "addr": addr,
                    "reason": "cell is beyond the scanned page (large sheet); re-scan or open sheet"}
        before_vf = _effective_valueformat(cell)
        if not jd.is_absurd_precision(before_vf):
            return {"status": "no-fix-needed", "reason": "precision is not junk-decimal", "addr": addr}
        raw = wk.get_sheetdata(ss, sheet_id, token, ctx)
        page_cells = wk.normalize_sheetdata(raw)
        cells_by_addr = {c["addr"]: c for c in page_cells}
        target_vf = jd.neighbor_consensus_format(addr, cells_by_addr)
        if not target_vf:
            return {"status": "refused", "addr": addr,
                    "reason": "no column neighbor format consensus — fix in Workiva UI"}

    def _no_fix(before_vf, _cell):
        try:
            import junk_decimal as jd
        except ImportError:
            return None
        if not jd.is_absurd_precision(before_vf):
            return "precision is not junk-decimal"
        return None

    return fix_format_copy(
        ss, sheet_id, table_id, addr, token, ctx,
        confirm=confirm, target_vf=target_vf, no_fix_reason=_no_fix,
    )


def apply_cell_value(ss, sheet_id, addr, value, token, ctx):
    """Write a single cell value via platform values PUT (plain text, not formula)."""
    body = {"values": [[value]]}
    return _put(f"/platform/v1/spreadsheets/{ss}/sheets/{sheet_id}/values/{addr}", token, ctx, body)


def fix_contrast(ss, sheet_id, table_id, addr, target_hex, token, ctx, confirm=False):
    """
    Safe-lane contrast fix. Returns a structured result; with confirm=False it is a
    dry-run (no write). richText cells are refused (surfaced, never written).
    """
    row, col = addr_to_rc(addr)
    meta = read_cell_meta(table_id, row, col, token, ctx)
    ctype = meta["type"]
    if ctype != "plainText":  # ADR-0002: only confirmed plain cells are safe-auto
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": f"{ctype or 'unknown'} cell - fontColor write would no-op; fix in Workiva UI"}
    if meta["merged"]:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": "merged cell - skipped"}
    before, value, cell, found = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    if not found:  # large sheet: cell beyond the scanned page — never write blind
        return {"status": "not-in-scan-page", "addr": addr,
                "reason": "cell is beyond the scanned page (large sheet); re-scan or supply targetHex"}
    bg = (((cell or {}).get("effectiveFormats", {}) or {}).get("cellFormat", {}) or {}).get("backgroundColor")
    ratio_before = target_ratio = None
    if target_hex is None:  # service computes the minimal AA-passing color from the cell's fg/bg
        if not before or not bg:
            return {"status": "no-fix-needed", "reason": "missing fg/bg colors", "addr": addr, "type": ctype}
        try:
            ratio_before = round(detectors.wcag_ratio(detectors._hex_to_rgb(before), detectors._hex_to_rgb(bg)), 2)
        except ValueError:
            ratio_before = None
        if ratio_before is not None and ratio_before >= detectors.AA:
            return {"status": "no-fix-needed", "reason": f"already passes AA ({ratio_before}:1)", "addr": addr}
        t = detectors.compute_aa_fontcolor(before, bg)
        if not t:
            return {"status": "no-fix-needed", "reason": "cannot reach AA", "addr": addr}
        target_hex, target_ratio = t["hex"], t["ratio"]
    else:  # caller-supplied target must itself clear AA against the bg (ADR-0002), never write a failing color
        if not bg:
            return {"status": "refused", "addr": addr, "type": ctype, "reason": "no background to validate target against"}
        try:
            target_ratio = round(detectors.wcag_ratio(detectors._hex_to_rgb(target_hex), detectors._hex_to_rgb(bg)), 2)
        except ValueError:
            return {"status": "refused", "addr": addr, "type": ctype, "reason": f"invalid targetHex {target_hex!r}"}
        if target_ratio < detectors.AA:
            return {"status": "refused", "addr": addr, "type": ctype,
                    "reason": f"targetHex {target_hex} fails WCAG AA ({target_ratio}:1)"}
    plan = {"addr": addr, "type": ctype, "value": value, "bg": bg, "before": before,
            "after": target_hex, "ratioBefore": ratio_before, "ratioAfter": target_ratio}
    if not confirm:
        return {"status": "dry-run", **plan}
    # write -> readback -> revert. The revert MUST run even if the readback raises (ADR-0003: no
    # reliable Workiva undo). before=None means "system default" -> revert to #000000, never leave stuck.
    revert_to = before or "#000000"

    def _revert():
        """Best-effort revert. Returns None on success, the error repr on failure (so a revert
        failure surfaces as 'cell may be left in written state' rather than a bare 502)."""
        try:
            apply_fontcolor(ss, sheet_id, row, col, revert_to, token, ctx)
            return None
        except Exception as re:
            return repr(re)

    apply_fontcolor(ss, sheet_id, row, col, target_hex, token, ctx)
    time.sleep(0.3)
    try:
        after, _, after_cell, after_found = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    except Exception as e:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readbackError": repr(e), "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-failed-reverted", "error": repr(e), **plan}
    if not after_found:  # write landed but it is beyond the readback page -> cannot verify, revert
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "reason": "readback page-miss", "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-page-miss",
                "reason": "cell beyond the scanned page; write reverted, could not verify", **plan}
    if (after or "").upper() != target_hex.upper():
        # The write landed in the cell's OWN format but the EFFECTIVE color didn't change ->
        # a higher-precedence rule (a conditional format or named style) is overriding it. A
        # direct fontColor write can never win against that, so this cell is not safe-auto
        # fixable; say so plainly instead of a confusing "reverted (no change)" (the write +
        # revert still dirtied the cell once, which is unavoidable once we've probed it).
        direct_after = (((after_cell or {}).get("formats", {}) or {}).get("textFormat", {}) or {}).get("fontColor")
        style_locked = (direct_after or "").upper() == target_hex.upper()
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readback": after, "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        if style_locked:
            return {"status": "style-locked", "expected": target_hex, "readback": after,
                    "reason": "font color is set by a conditional format or named style — "
                              "change the rule in Workiva, not the cell", **plan}
        return {"status": "mismatch-reverted", "expected": target_hex, "readback": after, **plan}
    return {"status": "applied", "readback": after, **plan}


def fix_label_trim(ss, sheet_id, table_id, addr, token, ctx, confirm=False):
    """
    Safe-lane label trim. Strips leading/trailing whitespace and collapses internal double spaces.
    dry-run (confirm=False) returns the plan; confirm=True writes via values PUT with readback/revert.
    """
    row, col = addr_to_rc(addr)
    meta = read_cell_meta(table_id, row, col, token, ctx)
    ctype = meta["type"]
    if ctype == "richText":
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "richText cell — value write would destroy formatting; fix in Workiva UI"}
    if ctype == "formula" or meta.get("formula"):
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "formula cell — cannot trim stored formula text"}
    if meta["merged"]:
        return {"status": "refused", "addr": addr, "type": ctype, "reason": "merged cell - skipped"}
    if meta.get("linked"):
        return {"status": "refused", "addr": addr, "type": ctype,
                "reason": "linked cell — value write blocked; fix in Workiva UI"}
    before, value, cell, found = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    if not found:
        return {"status": "not-in-scan-page", "addr": addr,
                "reason": "cell is beyond the scanned page (large sheet); re-scan or open sheet"}
    trim_target = detectors.compute_trim_label(value)
    if not trim_target:
        return {"status": "no-fix-needed", "reason": "no trimmable whitespace on label", "addr": addr, "type": ctype}
    plan = {"addr": addr, "type": ctype, "before": value, "after": trim_target}
    if not confirm:
        return {"status": "dry-run", **plan}
    revert_to = value

    def _revert():
        try:
            apply_cell_value(ss, sheet_id, addr, revert_to, token, ctx)
            return None
        except Exception as re:
            return repr(re)

    apply_cell_value(ss, sheet_id, addr, trim_target, token, ctx)
    time.sleep(0.3)
    try:
        # readback the cell VALUE (2nd return), not fontColor — this is a value write, so the
        # trim is verified against the cell text, not the font color (would always mismatch -> revert).
        _, after, _, after_found = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    except Exception as e:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readbackError": repr(e), "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-failed-reverted", "error": repr(e), **plan}
    if not after_found:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "reason": "readback page-miss", "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "readback-page-miss",
                "reason": "cell beyond the scanned page; write reverted, could not verify", **plan}
    if after != trim_target:
        rev = _revert()
        if rev:
            return {"status": "revert-failed", "readback": after, "revertError": rev,
                    "warning": "cell may be left in the written state", **plan}
        return {"status": "mismatch-reverted", "expected": trim_target, "readback": after, **plan}
    return {"status": "applied", "readback": after, **plan}


def harness_test(ss, sheet_id, table_id, addr, token, ctx):
    """
    Bounded write->readback->revert proof on a CONFIRMED-EMPTY, unlinked cell (ADR-0003).
    Writes a test font color, reads it back via sheetdata, then re-writes the captured
    before-state. Aborts before any write if the cell is not empty.
    """
    row, col = addr_to_rc(addr)
    before, value, cell, found = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    trace = {"addr": addr, "before_fontColor": before, "value": repr(value)}
    if not found:  # LSP-001: cell outside the fetched page is unresolvable -> never write (would mis-revert to #000000)
        trace["status"] = "ABORT-out-of-page"
        return trace
    if value not in ("", None):
        trace["status"] = "ABORT-not-empty"
        return trace
    if read_cell_meta(table_id, row, col, token, ctx).get("linked"):
        trace["status"] = "ABORT-linked-cell"  # never test-write to a linked cell (ADR-0003 bound)
        return trace
    test_color = "#2E62E8" if (before or "").upper() != "#2E62E8" else "#E0A106"
    _, st1 = apply_fontcolor(ss, sheet_id, row, col, test_color, token, ctx)
    time.sleep(0.4)
    after, _, _, _ = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    trace.update({"wrote": test_color, "write_status": st1, "readback": after,
                  "write_ok": (after or "").upper() == test_color.upper()})
    # revert
    revert_to = before or "#000000"
    _, st2 = apply_fontcolor(ss, sheet_id, row, col, revert_to, token, ctx)
    time.sleep(0.4)
    final, _, _, _ = read_effective_fontcolor(ss, sheet_id, row, col, token, ctx)
    trace.update({"reverted_to": revert_to, "revert_status": st2, "final": final,
                  "revert_ok": (final or "").upper() == (revert_to or "").upper()})
    trace["status"] = "PASS" if trace["write_ok"] and trace["revert_ok"] else "INVESTIGATE"
    return trace


if __name__ == "__main__":
    if len(sys.argv) < 5:
        raise SystemExit("usage: fixer.py <spreadsheetId> <sheetId> <tableId> <A1cell>")
    ss, sheet_id, table_id, addr = sys.argv[1:5]
    ctx = wk._ssl_context()
    try:
        tok = wk.get_token(ctx)
    except wk.AuthError as e:
        raise SystemExit(str(e))  # clean one-line exit for CLI use; the service catches AuthError itself
    print(json.dumps(harness_test(ss, sheet_id, table_id, addr, tok, ctx), indent=2))
